"""Per-hop chains from a recorded LangGraph agent, enforced by real TenuoTool with full delegation chains."""
import pytest
from tenuo import SigningKey
from tenuo.exceptions import ConstraintViolation, ToolNotAuthorized
from tenuo.langchain import TenuoTool

from langchain_core.tools import tool

from test_record_langchain import RAW, RUNS, build
from warrantgen import load_trace
from warrantgen.chain import hop_rules, hop_values, mint_chains
from warrantgen.record_langchain import Recorder

@tool
def read_file(path: str) -> str:
    """Read a file."""
    return "contents"


NODE_OF = {"search": "team/researcher", "fetch": "team/researcher", "write_file": "writer"}


def record(tmp_path):
    trace = tmp_path / "t.jsonl"
    for state in RUNS:
        build(RAW).invoke(state, {"callbacks": [Recorder(trace)]})
    rows = load_trace(trace)
    runs = list(dict.fromkeys(r["run"] for r in rows))  # recording order = RUNS order
    return rows, runs


def tool_for(name, chains, keys, issuer, hop=None):
    chain = chains[hop or NODE_OF[name]]
    return TenuoTool(RAW[name], bound_warrant=chain[-1].bind(keys[hop or NODE_OF[name]]), warrant_chain=chain[:-1],
                     trusted_roots=[issuer.public_key])


def test_hops_follow_node_paths_and_parents_cover_children(tmp_path):
    rows, _ = record(tmp_path)
    hops = hop_rules(rows)
    assert set(hops) == {"", "team", "team/researcher", "writer"}
    assert set(hops[""]) == {"search", "fetch", "write_file"}
    assert set(hops["team/researcher"]) == {"search", "fetch"} and set(hops["writer"]) == {"write_file"}


def test_each_sub_agent_runs_under_its_own_narrower_chain(tmp_path):
    rows, runs = record(tmp_path)
    hops = hop_rules(rows)
    issuer = SigningKey.generate()
    keys = {hop: SigningKey.generate() for hop in hops}
    for state, run in zip(RUNS, runs):
        values = hop_values(hops, [r for r in rows if r["run"] == run])
        chains, notes = mint_chains(hops, issuer, keys, ttl=300, values=values)
        assert notes == []
        assert [len(chains[h]) for h in ("", "team", "team/researcher", "writer")] == [1, 2, 3, 2]
        for hop, chain in chains.items():  # a child never outlives its parent
            assert all(c.expires_at() <= p.expires_at() for p, c in zip(chain, chain[1:])), hop
        build({name: tool_for(name, chains, keys, issuer) for name in RAW}).invoke(state)

        # The root holds write_file, but the researcher's chain does not.
        with pytest.raises(ToolNotAuthorized):
            tool_for("write_file", chains, keys, issuer, hop="team/researcher").invoke(
                {"path": state["path"], "text": "x"})
        with pytest.raises(ToolNotAuthorized):
            tool_for("fetch", chains, keys, issuer, hop="writer").invoke({"url": state["urls"][0]})
        with pytest.raises(ConstraintViolation):  # the writer may only write this run's file
            tool_for("write_file", chains, keys, issuer).invoke({"path": "out/other.md", "text": "x"})


def test_folder_reads_narrow_per_hop_inside_the_parent(tmp_path):
    rows = [{"run": run, "step": step, "tool": "read_file", "args": {"path": path}}
            for run in ("r1", "r2") for step, path in (("team/site", "public/index.html"), ("team/code", "web/app.tsx"))]
    hops = hop_rules(rows)
    assert hops["team"]["read_file"]["path"].value == ["public", "web"]
    issuer = SigningKey.generate()
    keys = {hop: SigningKey.generate() for hop in hops}
    chains, notes = mint_chains(hops, issuer, keys, ttl=300)
    assert notes == []
    site = TenuoTool(read_file, bound_warrant=chains["team/site"][-1].bind(keys["team/site"]),
                     warrant_chain=chains["team/site"][:-1], trusted_roots=[issuer.public_key])
    site.invoke({"path": "public/never-seen.html"})
    for path in ("web/app.tsx", "public/.env", "public/../web/app.tsx"):  # sibling's folder, secrets, climbing out
        with pytest.raises(ConstraintViolation):
            site.invoke({"path": path})


def test_tenuo_refusal_keeps_the_parent_constraint(tmp_path):
    # Different hosts give the root a per-run URL list; a child can't narrow a one-of into a URL pattern.
    rows = [{"run": "r1", "step": "a/x", "tool": "fetch", "args": {"url": "https://docs.python.org/3/a.html"}},
            {"run": "r1", "step": "a/y", "tool": "fetch", "args": {"url": "https://pypi.org/project/x/"}},
            {"run": "r2", "step": "a/x", "tool": "fetch", "args": {"url": "https://docs.python.org/3/b.html"}},
            {"run": "r2", "step": "a/y", "tool": "fetch", "args": {"url": "https://pypi.org/project/y/"}}]
    hops = hop_rules(rows)
    issuer = SigningKey.generate()
    keys = {hop: SigningKey.generate() for hop in hops}
    values = hop_values(hops, [r for r in rows if r["run"] == "r1"])
    _, notes = mint_chains(hops, issuer, keys, ttl=300, values=values)
    assert notes == ["a/x: fetch.url keeps its parent's constraint", "a/y: fetch.url keeps its parent's constraint"]
