"""Reconcile a hand-written draft with recorded runs: tighten, flag what exceeds it, keep what was never exercised."""
import json

import yaml
from tenuo import SigningKey, Warrant

from warrantgen.cli import main
from warrantgen.infer import infer
from warrantgen.reconcile import compile_value, reconcile


def call(run, tool, **args):
    return {"run": run, "step": "write", "tool": tool, "args": args}


DRAFT = {
    "version": 2, "name": "write", "ttl": "10m",
    "capabilities": {
        "write_file": {"repo": {"pattern": "acme/*"}, "env": ["dev", "staging", "prod"], "size": "0..100000",
                       "path": {"subpath": "/srv"}, "text": "any"},
        "open_pr": {"repo": {"pattern": "acme/*"}, "title": "any"},
        "delete_branch": {"repo": {"pattern": "acme/*"}},
    },
}
CALLS = [
    call("r1", "write_file", repo="acme/bakery", env="dev", size=812, path="/srv/site/robots.txt", text="User-agent: *"),
    call("r1", "write_file", repo="acme/bakery", env="dev", size=2040, path="/srv/site/llms.txt", text="# Bakery"),
    call("r2", "write_file", repo="acme/dentist", env="dev", size=503, path="/etc/hosts", text="x"),
    call("r2", "open_pr", repo="acme/dentist", title="add schema", draft=True),
    call("r2", "merge_pr", repo="acme/dentist"),
]


def run(draft=DRAFT, calls=CALLS):
    rules = infer(calls)[0]["write"]
    proposal, findings = reconcile(draft, rules, calls)
    return proposal, {(f.tool, f.arg): f for f in findings}


def test_each_argument_is_classified():
    proposal, found = run()
    assert {k: f.kind for k, f in found.items()} == {
        ("merge_pr", ""): "exceeds",                   # tool the draft never granted
        ("open_pr", "draft"): "exceeds",               # argument the draft never listed
        ("write_file", "path"): "exceeds",             # /etc/hosts is outside /srv
        ("write_file", "repo"): "per-run",
        ("write_file", "env"): "tighten",
        ("write_file", "size"): "tighten",
        ("write_file", "text"): "keep",                # free text stays any
        ("open_pr", "repo"): "keep",                   # one run only: may be a per-run value
        ("open_pr", "title"): "keep",
        ("delete_branch", ""): "untested",
    }
    caps = proposal["capabilities"]
    assert caps["write_file"]["env"] == "dev"
    assert caps["write_file"]["size"] == "0..5000"     # observed 503..2040, rounded up
    assert caps["write_file"]["path"] == {"subpath": "/srv"}  # exceeds: never widened, never dropped
    assert caps["delete_branch"] == DRAFT["capabilities"]["delete_branch"]
    assert "merge_pr" not in caps and proposal["ttl"] == "10m"


def test_proposal_admits_every_call_the_draft_admitted():
    proposal, _ = run()
    key = SigningKey.generate()
    for doc in (DRAFT, proposal):
        builder = Warrant.mint_builder()
        for tool, args in doc["capabilities"].items():
            builder = builder.capability(tool, **{a: compile_value(v) for a, v in args.items()})
        warrant = builder.holder(key.public_key).ttl(60).mint(key)
        allowed = [c for c in CALLS if warrant.check_constraints(c["tool"], c["args"]) is None]
        if doc is DRAFT:
            expected = allowed
    assert allowed == expected and len(allowed) == 2


def test_cli_writes_the_proposal_and_fails_on_exceeds(tmp_path, capsys):
    draft, trace, out = tmp_path / "draft.yaml", tmp_path / "t.jsonl", tmp_path / "proposal.yaml"
    draft.write_text(yaml.safe_dump(DRAFT))
    trace.write_text("".join(json.dumps(c) + "\n" for c in CALLS))
    assert main(["reconcile", str(trace), "--draft", str(draft), "--step", "write", "--out", str(out)]) == 1
    text = capsys.readouterr().out
    assert "tighten   write_file.env" in text and "exceeds   merge_pr" in text
    assert yaml.safe_load(out.read_text())["capabilities"]["write_file"]["env"] == "dev"
