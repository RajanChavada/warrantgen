"""TemporalRecorder against the plugin's event shape; examples/seo-agent re-records a real workflow with it."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from warrantgen import load_trace
from warrantgen.record_temporal import TemporalRecorder


def event(run, tool, caps, **args):
    return SimpleNamespace(workflow_id="fixrun-acme", workflow_run_id=run, tool=tool, arguments=args,
                           decision="ALLOW", warrant_capabilities=caps)


def test_one_line_per_decision_grouped_by_warrant(tmp_path):
    rec = TemporalRecorder(tmp_path / "t.jsonl")
    rec(event("run-1", "write_file", ["write_file", "open_pr"], path="public/robots.txt"))
    rec(event("run-2", "get_pr_state", ["get_pr_state"], number=7))
    rows = load_trace(tmp_path / "t.jsonl")
    assert [(r["run"], r["step"], r["tool"]) for r in rows] == [
        ("run-1", "open_pr+write_file", "write_file"), ("run-2", "get_pr_state", "get_pr_state")]
    assert rows[0]["args"] == {"path": "public/robots.txt"} and rows[0]["workflow_id"] == "fixrun-acme"


def test_concurrent_activities_never_interleave_lines(tmp_path):
    rec = TemporalRecorder(tmp_path / "t.jsonl", step=lambda e: e.tool)
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda i: rec(event(f"run-{i}", "write_file", [], text="x" * 5000)), range(200)))
    rows = load_trace(tmp_path / "t.jsonl")
    assert len(rows) == 200 and {r["step"] for r in rows} == {"write_file"}
