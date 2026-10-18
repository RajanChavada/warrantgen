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
    r"(?i)(^|/)(\.env[^/]*|\.ssh|\.git/config|id_(rsa|ed25519)[^/]*|[^/]*\.(pem|key))($|/)"
    r"|(^|/)\.\.(/|$)"
    r"|\b(127\.0\.0\.1|localhost|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|169\.254\.169\.254)\b"
)
FREE_TEXT_LEN = 80  # longer strings, or any with whitespace, are free text: identifiers rarely have spaces
MIN_PREFIX = 3


@dataclass(frozen=True)
class Rule:
    """How one argument is constrained. kind: exact, var, oneof, range, pattern, subpath, wildcard.

    var: the value is fixed within a run but differs between runs, so it becomes a mint-time
    parameter. `value` then says whether that parameter is one value ("exact") or a list ("oneof").
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
                found.setdefault(r["run"], []).append(f'{r["tool"]}: {v}')
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
        steps.setdefault(step, {}).setdefault(tool, {})[arg] = _rule(arg, by_run, arg in variables, arg in exact)
    return steps, excluded


def _rule(arg, by_run, force_var, force_exact):
    values = [v for vs in by_run.values() for v in vs]
    distinct = set(map(_key, values))
    per_run = [set(map(_key, vs)) for vs in by_run.values()]

    numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values)
    one_per_run = all(len(s) == 1 for s in per_run)

    if any(isinstance(v, str) and (len(v) > FREE_TEXT_LEN or re.search(r"\s", v)) for v in values):
        return Rule("wildcard", note="free text: confirm it does not change what the tool does")
    if force_exact or (len(distinct) == 1 and not force_var):
        if len(distinct) > 1:
            return Rule("oneof", sorted(distinct, key=str), note="forced exact but several values seen")
        return Rule("exact", values[0], note=_single_run_note(by_run))
    # No value repeats across runs: a per-run parameter (repo, PR number, this run's file list).
    per_run_value = len(by_run) > 1 and _disjoint(per_run) and (one_per_run or not numeric)
    if force_var or per_run_value:
        prefix = os.path.commonprefix(values) if all(isinstance(v, str) for v in values) else ""
        note = f"differs per run; or Pattern({prefix}*) if unknown at mint time" if len(prefix) >= MIN_PREFIX else "differs per run"
        return Rule("var", "exact" if one_per_run else "oneof", note=note)
    if numeric:
        return Rule("range", (min(values), max(values)), note="observed bounds; choose headroom")
    if all(isinstance(v, str) for v in values):
        if all(v.startswith("/") for v in values):
            return Rule("subpath", os.path.commonpath(values))
        prefix = os.path.commonprefix(values)
        if len(prefix) >= MIN_PREFIX:
            return Rule("pattern", prefix + "*")
    return Rule("oneof", sorted(distinct, key=str))


def _key(v):
    return json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v


def _disjoint(sets):
    seen = set()
    for s in sets:
        if seen & s:
            return False
        seen |= s
    return True


def _single_run_note(by_run):
    return "seen in one run only: may be a per-run value (use --var)" if len(by_run) == 1 else ""
