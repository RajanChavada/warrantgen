"""The demo agent end to end, so the story in examples/demo/README.md stays true."""
import sys
from pathlib import Path

from warrantgen.cli import main

DEMO = Path(__file__).parents[1] / "examples" / "demo"
sys.path.insert(0, str(DEMO))
import agent  # noqa: E402


def test_record_reconcile_then_enforce(tmp_path, capsys):
    agent.record(tmp_path)
    traces = sorted(str(p) for p in tmp_path.glob("*.jsonl"))
    assert len(traces) == 5
    capsys.readouterr()

    assert main(["reconcile", *traces, "--draft", str(DEMO / "draft.yaml"), "--step", "team/researcher"]) == 1
    out = capsys.readouterr().out
    for line in ("exceeds   search.limit", "tighten   fetch.url", "untested  delete_file", "quarantined run"):
        assert line in out

    agent.enforce(tmp_path)
    out = capsys.readouterr().out
    denied = [line for line in out.splitlines() if "DENIED" in line]
    assert denied == [f"    DENIED  fetch {{'url': '{agent.METADATA}'}}"]  # only the poisoned page's fetch
    assert out.count("    ok      ") == 6  # the new task's 3 calls, and the poisoned task's other 3
