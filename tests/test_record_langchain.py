"""Record a LangGraph agent, infer one warrant per node, then run it again under real TenuoTool enforcement."""
from typing import TypedDict

import pytest
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from tenuo import SigningKey
from tenuo.langchain import TenuoTool

from warrantgen import infer, load_trace, mint
from warrantgen.record_langchain import Recorder


@tool
def search(query: str) -> str:
    """Search the docs."""
    return "results"


@tool
def fetch(url: str) -> str:
    """Fetch a page."""
    return "page"


@tool
def write_file(path: str, text: str) -> str:
    """Write the report."""
    return "written"


class State(TypedDict):
    query: str
    urls: list
    path: str


def build(tools):
    def researcher(state, config):
        tools["search"].invoke({"query": state["query"]}, config)
        for url in state["urls"]:
            tools["fetch"].invoke({"url": url}, config)
        return state

    def writer(state, config):
        tools["write_file"].invoke({"path": state["path"], "text": "# Report\n..."}, config)
        return state

    team = StateGraph(State)
    team.add_node("researcher", researcher)
    team.add_edge(START, "researcher")
    team.add_edge("researcher", END)
    graph = StateGraph(State)
    graph.add_node("team", team.compile())
    graph.add_node("writer", writer)
    graph.add_edge(START, "team")
    graph.add_edge("team", "writer")
    graph.add_edge("writer", END)
    return graph.compile()


RAW = {"search": search, "fetch": fetch, "write_file": write_file}
RUNS = [
    {"query": "json module", "urls": ["https://docs.python.org/3/library/json.html"], "path": "out/json.md"},
    {"query": "asyncio tasks", "urls": ["https://docs.python.org/3/library/asyncio-task.html",
                                        "https://docs.python.org/3/library/asyncio.html"], "path": "out/asyncio.md"},
    {"query": "pathlib", "urls": ["https://docs.python.org/3/library/pathlib.html"], "path": "out/pathlib.md"},
]
INJECTED = {"query": "json", "urls": ["http://169.254.169.254/latest/meta-data/"], "path": "out/creds.md"}


def test_records_node_paths_and_skips_wrapped_duplicates(tmp_path):
    trace = tmp_path / "t.jsonl"
    build(RAW).invoke(RUNS[1], {"callbacks": [Recorder(trace)]})
    rows = load_trace(trace)
    assert [(r["step"], r["tool"]) for r in rows] == [
        ("team/researcher", "search"), ("team/researcher", "fetch"), ("team/researcher", "fetch"), ("writer", "write_file")]
    assert rows[0]["args"] == {"query": "asyncio tasks"} and len({r["run"] for r in rows}) == 1


def test_recorded_runs_become_per_node_warrants_that_tenuo_enforces(tmp_path):
    trace = tmp_path / "t.jsonl"
    for state in RUNS + [INJECTED]:
        build(RAW).invoke(state, {"callbacks": [Recorder(trace)]})
    steps, excluded = infer(load_trace(trace))
    assert len(excluded) == 1  # the metadata-endpoint run is never learned
    url = steps["team/researcher"]["fetch"]["url"]
    assert (url.kind, url.value) == ("url_pattern", "https://docs.python.org/3/library/*")

    issuer, holder = SigningKey.generate(), SigningKey.generate()

    def enforced(state):
        warrants = {step: mint(rules, issuer, holder.public_key, ttl=300, bindings={"path": state["path"]})
                    for step, rules in steps.items()}
        node_of = {"search": "team/researcher", "fetch": "team/researcher", "write_file": "writer"}
        return {name: TenuoTool(t, bound_warrant=warrants[node_of[name]].bind(holder),
                                trusted_roots=[issuer.public_key]) for name, t in RAW.items()}

    for state in RUNS:  # every recorded run passes under its own node's warrant
        build(enforced(state)).invoke(state, {"callbacks": [Recorder(tmp_path / "enforced.jsonl")]})
    for url in ("http://169.254.169.254/latest/meta-data/", "https://docs.python.org/3/library/../../internal",
                "https://docs.python.org@evil.com/3/library/x"):
        attack = {**INJECTED, "urls": [url]}
        with pytest.raises(Exception, match="url"):
            build(enforced(attack)).invoke(attack)
    calls = sum(2 + len(state["urls"]) for state in RUNS)  # search, fetches, write_file
    assert len(load_trace(tmp_path / "enforced.jsonl")) == calls  # one line per call, not two per wrapped tool
