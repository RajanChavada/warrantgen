"""Tenuo's own audit log (what `tenuo discover` reads) works as a trace, one run per file."""
from tenuo.audit import AuditEvent, AuditEventType

from warrantgen import infer, load_trace


def audit_log(path, *events):
    lines = ["app starting"] + [AuditEvent(event_type=t, tool=tool, constraints=args, warrant_id="w1").to_json()
                                for t, tool, args in events] + ["app done"]
    path.write_text("\n".join(lines) + "\n")
    return path


def test_audit_events_become_trace_rows(tmp_path):
    log = audit_log(tmp_path / "run1.log",
                    (AuditEventType.AUTHORIZATION_SUCCESS, "read_file", {"path": "docs/a.md"}),
                    (AuditEventType.AUTHORIZATION_FAILURE, "read_file", {"path": "docs/b.md"}),
                    (AuditEventType.WARRANT_CREATED, None, None))
    rows = load_trace(log)
    assert [(r["run"], r["step"], r["tool"], r["args"], r["decision"]) for r in rows] == [
        (str(log), "audit", "read_file", {"path": "docs/a.md"}, "ALLOW"),
        (str(log), "audit", "read_file", {"path": "docs/b.md"}, "DENY")]


def test_one_file_per_run_gives_per_run_values(tmp_path):
    rows = [r for i, repo in enumerate(["acme/site", "acme/web"]) for r in load_trace(audit_log(
        tmp_path / f"run{i}.log", (AuditEventType.AUTHORIZATION_SUCCESS, "open_pr", {"repo": repo, "base": "main"})))]
    rules = infer(rows)[0]["audit"]["open_pr"]
    assert rules["repo"].kind == "var" and rules["base"].kind == "exact"
