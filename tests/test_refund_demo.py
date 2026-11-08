"""The refund demo end to end, so examples/refund-agent/README.md stays true."""
import sys
from pathlib import Path

DEMO = Path(__file__).parents[1] / "examples" / "refund-agent"
sys.path.insert(0, str(DEMO))
import support_agent  # noqa: E402


def test_record_enforce_then_scenarios(tmp_path, capsys):
    support_agent.record(tmp_path)
    rules = support_agent.learned(tmp_path)
    assert rules["issue_refund"]["amount_cents"].value == (599, 3450)  # observed bounds: money gets no headroom
    assert rules["send_email"]["to"].kind == "var" and "close_account" not in rules
    capsys.readouterr()

    support_agent.enforce(tmp_path)
    out = capsys.readouterr().out
    denied = [line.split(" {")[0].strip() for line in out.splitlines() if "DENIED" in line]
    assert denied == ["DENIED  issue_refund", "DENIED  send_email"]  # only the poisoned note's two calls
    assert "500000" in out and support_agent.ATTACKER in out
    # The lead is asked about the two ordinary refunds only: the $5,000 one is denied before it reaches them.
    assert [line for line in out.splitlines() if "lead    approves" in line and "500000" in line] == []
    assert out.count("lead    approves") == 2

    rows = support_agent.scenarios(tmp_path)
    assert len(rows) == 16
    for scenario, attempt, expected, outcome, ran in rows:
        assert expected in outcome, (attempt, outcome)
        assert ran == (expected == "allowed"), attempt
