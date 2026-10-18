import json

import yaml

from warrantgen.cli import main

TRACE = [
    {"run": "r1", "step": "poll", "tool": "get_pr_state", "args": {"repo": "acme/site", "number": 7}},
    {"run": "r2", "step": "poll", "tool": "get_pr_state", "args": {"repo": "acme/web", "number": 9}},
    {"run": "r1", "step": "read", "tool": "read_file", "args": {"path": "public/robots.txt"}},
    {"run": "r2", "step": "read", "tool": "read_file", "args": {"path": "web/app.tsx"}},
]


def trace_file(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in TRACE))
    return str(path)


def test_infer_prints_rules_and_json(tmp_path, capsys):
    assert main(["infer", trace_file(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "step poll" in out and "get_pr_state.number" in out and "per-run value" in out
    assert main(["infer", trace_file(tmp_path), "--json"]) == 0
    rules = json.loads(capsys.readouterr().out)["steps"]["poll"]["get_pr_state"]
    assert rules["repo"]["kind"] == "var"


def test_template_from_set_and_from_run(tmp_path, capsys):
    assert main(["template", trace_file(tmp_path), "--step", "poll", "--set", "repo=acme/site", "--set", "number=7"]) == 0
    doc = yaml.safe_load(capsys.readouterr().out)
    assert doc["capabilities"]["get_pr_state"] == {"repo": "acme/site", "number": "7..7"}
    out = tmp_path / "poll.yaml"
    assert main(["template", trace_file(tmp_path), "--step", "po", "--from-run", "r2", "--out", str(out)]) == 0
    assert yaml.safe_load(out.read_text())["capabilities"]["get_pr_state"]["number"] == "9..9"


def test_template_refuses_with_reasons(tmp_path, capsys):
    assert main(["template", trace_file(tmp_path), "--step", "read", "--from-run", "r1"]) == 2
    assert "read_file.path: needs all/not" in capsys.readouterr().err
    assert main(["template", trace_file(tmp_path), "--step", "poll"]) == 2  # per-run values not given
    assert "get_pr_state.repo" in capsys.readouterr().err
    assert main(["template", trace_file(tmp_path), "--step", "nope"]) == 2


def test_chain_prints_hops_and_mints_them(tmp_path, capsys):
    rows = [{"run": run, "step": step, "tool": tool, "args": args}
            for run, path in (("r1", "out/a.md"), ("r2", "out/b.md"))
            for step, tool, args in (("team/researcher", "search", {"query": "a b"}),
                                     ("writer", "write_file", {"path": path}))]
    path = tmp_path / "t.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert main(["chain", str(path)]) == 0
    out = capsys.readouterr().out
    assert "(root): search, write_file\n  team: search\n    team/researcher: search\n  writer: write_file" in out
    assert main(["chain", str(path), "--from-run", "r1"]) == 0
    assert "minted 4 hops" in capsys.readouterr().out
