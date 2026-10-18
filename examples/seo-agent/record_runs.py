"""Record every Tenuo decision from varied FixRunWorkflow runs as warrantgen trace lines.

Real local Temporal server and real Tenuo plugin, as in test_agent_workflow.py; GitHub and the model are scripted.
Usage: uv run python tests/record_runs.py OUT.jsonl
"""
import asyncio
import itertools
import json
import sys
import uuid
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from temporalio.testing import WorkflowEnvironment
from tenuo import SigningKey

sys.path.insert(0, str(Path(__file__).parent))
from agent_fakes import BLOCKED, LAYOUT, FakeGitHub  # noqa: E402
from fixagent.agent import activities as act  # noqa: E402
from fixagent.agent.llm import ScriptedLLM, Step  # noqa: E402
from fixagent.agent.workers import set_holder_key, worker  # noqa: E402
from fixagent.agent.workflow import WORKFLOW_QUEUE, FixRunWorkflow, RunInput  # noqa: E402

SITES = [
    dict(slug="acme", repo="acme/site", base="main", domain="acme.com", root="public", code_root="web"),
    dict(slug="bakery", repo="northside/bakery-web", base="main", domain="northsidebakery.com", root="static",
         code_root="frontend"),
    dict(slug="dental", repo="smile-dental/www", base="trunk", domain="smiledental.io", root="public", code_root="app"),
]
PR_NUMBERS = itertools.count(101)


def call(name, **args):
    return {"id": uuid.uuid4().hex[:8], "name": name, "args": args}


def routes(s):
    robots, layout = f"{s['root']}/robots.txt", f"{s['code_root']}/app/layout.tsx"
    finish = call("finish", summary="done")
    return {
        "site-files": [Step("look", [call("run_gates"), call("list_files", dir=s["root"])]),
                       Step("read", [call("read_file", path=robots)]),
                       Step("fix", [call("propose_fix", type="site-files", facts={}), finish])],
        "org-schema": [Step("look", [call("list_files", dir=s["code_root"]), call("read_file", path=layout)]),
                       Step("fix", [call("propose_fix", type="org-schema", facts={"logo": None, "same_as": []}),
                                    finish])],
        "both": [Step("look", [call("homepage_links"), call("read_file", path=robots), call("read_file", path=layout)]),
                 Step("fix", [call("propose_fix", type="org-schema", facts={"logo": None, "same_as": []}),
                              call("propose_fix", type="site-files", facts={}), finish])],
        "injected": [Step("look", [call("run_gates"), call("read_file", path=robots)]),
                     Step("obey", [call("read_file", path=".env"), call("read_file", path="../payments-api/.env")]),
                     Step("fix", [call("propose_fix", type="site-files", facts={}), finish])],
        "explorer": [Step("look", [call("list_files", dir=s["root"]), call("list_files", dir=s["code_root"])]),
                     Step("read", [call("read_file", path=f"{s['root']}/index.html"),
                                   call("read_file", path=f"{s['code_root']}/package.json"),
                                   call("read_file", path=robots)]),
                     Step("fix", [call("propose_fix", type="site-files", facts={}), finish])],
    }


class NumberedGitHub(FakeGitHub):
    """A new pull request number per run, so the number varies the way it would on a real repo."""

    def open_pr(self, repo, base, head, title, body):
        self.prs.append(body)
        return f"https://github.com/{repo}/pull/{next(PR_NUMBERS)}"


def brand(s):
    return (f"slug: {s['slug']}\nname: {s['slug'].title()}\ndomain: {s['domain']}\naliases: []\none_liner: x\n"
            f"category: y\ncompetitors: []\nsite: {{repo: {s['repo']}, base: {s['base']}, root: {s['root']}, "
            f"code_root: {s['code_root']}}}\n")


def fetch_for(domain):
    pages = {f"https://{domain}/robots.txt": "User-agent: *\nAllow: /\n",
             f"https://{domain}/": f"<html><head><title>{domain}</title></head><body></body></html>"}
    return lambda url: (200, pages[url]) if url in pages else (404, "")


async def main(out):
    issuer, holder = SigningKey.generate(), SigningKey.generate()
    rows = []

    def audit(e):
        rows.append({"run": e.workflow_id, "step": "+".join(sorted(e.warrant_capabilities)), "tool": e.tool,
                     "args": e.arguments, "decision": e.decision, "warrant_id": e.warrant_id})

    env = await WorkflowEnvironment.start_local()
    set_holder_key(holder)
    workers = [worker(role, env.client, issuer.public_key, audit=audit if role in ("executor", "llm") else None)
               for role in ("minter", "executor", "llm", "workflow")]
    tasks = [asyncio.create_task(w.run()) for w in workers]
    with TemporaryDirectory() as brands:
        for s in SITES:
            Path(brands, f"{s['slug']}.yaml").write_text(brand(s))
            for route, steps in routes(s).items():
                states = ["open", "closed"] if (s["slug"], route) == ("bakery", "site-files") else ["merged"]
                gh = NumberedGitHub({f"{s['root']}/robots.txt": BLOCKED, f"{s['code_root']}/app/layout.tsx": LAYOUT,
                                     f"{s['root']}/index.html": "<html></html>", f"{s['code_root']}/package.json": "{}"},
                                    states=states)
                act.configure(act.Deps(github=lambda repo, gh=gh: gh, fetch=fetch_for(s["domain"]),
                                       llm=ScriptedLLM(steps), brands_root=Path(brands), issuer=issuer,
                                       holder_public_key=holder.public_key, is_onboarded=lambda repo: True))
                run_id = f"{s['slug']}-{route}"
                result = await env.client.execute_workflow(
                    FixRunWorkflow.run, RunInput(**s, poll_every=1, poll_for=3, verify_every=1, verify_for=2),
                    id=run_id, task_queue=WORKFLOW_QUEUE, execution_timeout=timedelta(minutes=2))
                print(f"{run_id:22} {result.status:28} denied={len(result.denied)}", flush=True)
    for w in workers:
        await w.shutdown()
    await asyncio.gather(*tasks, return_exceptions=True)
    await env.shutdown()
    Path(out).write_text("".join(json.dumps(r, default=str) + "\n" for r in rows))
    print(f"{len(rows)} decisions -> {out}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
