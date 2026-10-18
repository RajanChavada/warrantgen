"""Generated warrant tests pass against the inferred warrant and fail against a loosened one."""
import subprocess
import sys

from warrantgen import infer
from warrantgen.testgen import render_tests as generate

TRACE = [
    {"run": run, "step": "write", "tool": "write_file",
     "args": {"repo": repo, "path": path, "size": size, "text": "body " * 40}}
    for run, repo, path, size in (("r1", "acme/site", "public/robots.txt", 812), ("r1", "acme/site", "public/llms.txt", 90),
                                  ("r2", "acme/web", "public/llms.txt", 5100))
] + [
    {"run": run, "step": "read", "tool": "read_file", "args": {"path": path}}
    for run, path in (("r1", "public/index.html"), ("r1", "web/app.tsx"), ("r2", "public/robots.txt"))
] + [
    {"run": run, "step": "fetch", "tool": "fetch", "args": {"url": url}}
    for run, url in (("r1", "https://docs.python.org/3/a.html"), ("r2", "https://docs.python.org/3/b.html"))
]


def run_pytest(path):
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(path)],
                       capture_output=True, text=True)
    return r.returncode, r.stdout


def test_generated_tests_pass_and_cover_each_rule(tmp_path):
    path = tmp_path / "test_warrants.py"
    path.write_text(generate(infer(TRACE)[0], TRACE, source="t.jsonl"))
    code, out = run_pytest(path)
    assert code == 0, out
    text = path.read_text()
    for why in ("size: above the range", "repo: another value", "path: a secret file", "url: another host",
                "url: '..' out of the prefix", "a tool this step never used"):
        assert why in text, why
    assert "body body" not in text  # long free text is shortened


def test_generated_tests_catch_a_loosened_warrant(tmp_path):
    path = tmp_path / "test_warrants.py"
    path.write_text(generate(infer(TRACE)[0], TRACE).replace(
        "def warrant_for(step, values):\n",
        "def warrant_for(step, values):\n"
        "    STEPS['write']['rules']['write_file']['size'] = Rule('range', (0, 10**9))\n"))
    code, out = run_pytest(path)
    assert code == 1 and "1 failed" in out and "size: above the range" in out
