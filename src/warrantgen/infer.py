"""Turn recorded tool calls into per-step, per-argument constraint rules.

A trace is JSON lines, one tool call each: {"run": ..., "step": ..., "tool": ..., "args": {...}}.
`step` is whatever groups calls under one warrant (a Temporal phase, a LangGraph node).
"""
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

# Values that should never be learned as normal behavior: secrets, parent paths, private hosts.
# Modeled on seo-agent's SECRET/PARENT rules in phases.py.
SUSPICIOUS = re.compile(
    r"""(?i)(^|/)(\.env[^/\s"']*|\.ssh|\.git/config|id_(rsa|ed25519)[^/\s"']*|[^/\s"']*\.(pem|key))($|/|\s|["'])"""
    r"|(^|/)\.\.(/|$)"
    r"|\b(127\.0\.0\.1|localhost|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|169\.254\.169\.254)\b"
)
FREE_TEXT_LEN = 80  # longer strings, or any with whitespace, are free text: identifiers rarely have spaces
MIN_PREFIX = 3
READ_VERBS = ("read", "list", "get", "fetch", "search", "view")
PATH_ARGS = ("path", "dir", "file", "folder")


@dataclass(frozen=True)
class Rule:
    """How one argument is constrained. kind: exact, var, oneof, subset, range, pattern, subpath, under, wildcard.

    var: the value is fixed within a run but differs between runs, so it becomes a mint-time
    parameter. `value` then says whether that parameter is one value ("exact"), a list ("oneof"),
    or the folders a path must stay under ("under").
    """
    kind: str
    value: object = None
    note: str = ""


def load_trace(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def tainted_runs(records):
    """Runs that touched a suspicious value, with the offending values. They are left out of inference."""
    found = {}
    for r in records:
        for v in r["args"].values():
            if isinstance(v, str) and SUSPICIOUS.search(v):
                hit = SUSPICIOUS.search(v)
                found.setdefault(r["run"], []).append(f'{r["tool"]}: ...{v[max(0, hit.start() - 30):hit.end() + 30]}...')
    return found


def infer(records, *, variables=(), exact=()):
    """Return ({step: {tool: {arg: Rule}}}, {excluded_run: [reasons]}).

    `variables` forces an argument to be a mint-time parameter; `exact` forces a fixed value.
    """
    excluded = tainted_runs(records)
    calls = {}  # (step, tool, arg) -> {run: [values]}
    for r in records:
        if r["run"] in excluded:
            continue
        for arg, value in r["args"].items():
            calls.setdefault((r["step"], r["tool"], arg), {}).setdefault(r["run"], []).append(value)
    steps = {}
    for (step, tool, arg), by_run in calls.items():
        steps.setdefault(step, {}).setdefault(tool, {})[arg] = _rule(tool, arg, by_run, arg in variables, arg in exact)
    return steps, excluded


def _rule(tool, arg, by_run, force_var, force_exact):
    values = [v for vs in by_run.values() for v in vs]
    distinct = set(map(_key, values))
    per_run = [set(map(_key, vs)) for vs in by_run.values()]

    numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values)
    one_per_run = all(len(s) == 1 for s in per_run)

    if any(isinstance(v, str) and (len(v) > FREE_TEXT_LEN or re.search(r"\s", v)) for v in values):
        return Rule("wildcard", note="free text: confirm it does not change what the tool does")
    if all(isinstance(v, list) and all(isinstance(i, (str, int)) for i in v) for v in values):
        return Rule("subset", sorted({i for v in values for i in v}, key=str))
    # Reads generalize to the folders they touched; writes keep their exact files.
    if not force_exact and tool.startswith(READ_VERBS) and _relative_paths(arg, values):
        dirs = [{v.split("/")[0] for v in vs} for vs in by_run.values()]
        note = "under these folders, minus secrets and '..'"
        if len(by_run) > 1 and any(d != dirs[0] for d in dirs):
            return Rule("var", "under", note="differs per run; " + note)
        return Rule("under", sorted(dirs[0]), note=note)
    if force_exact or (len(distinct) == 1 and not force_var):
        if len(distinct) > 1:
            return Rule("oneof", sorted(distinct, key=str), note="forced exact but several values seen")
        return Rule("exact", values[0], note=_single_run_note(by_run))
    # One value per run (repo, base branch, PR number), or a list that changes per run: a per-run parameter.
    per_run_value = len(by_run) > 1 and (one_per_run or (not numeric and len(set(map(frozenset, per_run))) > 1))
    if force_var or per_run_value:
        prefix = _prefix(values)
        note = f"differs per run; or Pattern({prefix}*) if unknown at mint time" if prefix else "differs per run"
        return Rule("var", "exact" if one_per_run else "oneof", note=note)
    if numeric:
        return Rule("range", (min(values), max(values)), note="observed bounds; choose headroom")
    if all(isinstance(v, str) for v in values):
        if all(v.startswith("/") for v in values):
            return Rule("subpath", os.path.commonpath(values))
        prefix = _prefix(values)
        if prefix:
            return Rule("pattern", prefix + "*")
    return Rule("oneof", sorted(distinct, key=str))


def _prefix(values):
    """Shared prefix cut back to its last '/', so 'fixagent/agent-2026...' generalizes to 'fixagent/'."""
    if not all(isinstance(v, str) for v in values):
        return ""
    prefix = os.path.commonprefix(values)
    prefix = prefix[: prefix.rfind("/") + 1]
    return prefix if len(prefix) >= MIN_PREFIX else ""


def _relative_paths(arg, values):
    return all(isinstance(v, str) and v and not v.startswith("/") for v in values) and (
        any(word in arg.lower() for word in PATH_ARGS) or all("/" in v for v in values))


def _key(v):
    return json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v




def _single_run_note(by_run):
    return "seen in one run only: may be a per-run value (use --var)" if len(by_run) == 1 else ""
