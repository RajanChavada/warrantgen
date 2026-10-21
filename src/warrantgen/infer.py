"""Turn recorded tool calls into per-step, per-argument constraint rules.

A trace is JSON lines, one tool call each: {"run": ..., "step": ..., "tool": ..., "args": {...}}.
`step` is whatever groups calls under one warrant (a Temporal phase, a LangGraph node).
"""
import json
import math
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
FREE_TEXT_LEN = 80  # longer strings without a "/", or any with whitespace, are free text
MIN_PREFIX = 3
READ_VERBS = ("read", "list", "get", "fetch", "search", "view")
PATH_ARGS = ("path", "dir", "file", "folder")
URL = re.compile(r"https?://[^/\s]+/")  # scheme, host and the slash after it
# Money-like arguments get no headroom: extra room on an amount is real exposure.
MONEY = re.compile(r"(?i)amount|price|cost|cents|fee|total|balance|payment|charge|refund|budget")


@dataclass(frozen=True)
class Rule:
    """How one argument is constrained. kind: exact, var, oneof, subset, range, pattern, url_pattern, url_patterns, subpath, under,
    wildcard.

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


def infer(records, *, variables=(), exact=(), headroom=1.0):
    """Return ({step: {tool: {arg: Rule}}}, {excluded_run: [reasons]}).

    `variables` forces an argument to be a mint-time parameter; `exact` forces a fixed value.
    `headroom` scales a numeric range's observed max before rounding it up to 1, 2 or 5 x 10^n;
    None keeps the observed bounds. Money-like arguments always keep them.
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
        steps.setdefault(step, {}).setdefault(tool, {})[arg] = _rule(tool, arg, by_run, arg in variables, arg in exact, headroom)
    return steps, excluded


def _rule(tool, arg, by_run, force_var, force_exact, headroom):
    values = [v for vs in by_run.values() for v in vs]
    distinct = set(map(_key, values))
    per_run = [set(map(_key, vs)) for vs in by_run.values()]

    numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values)
    one_per_run = all(len(s) == 1 for s in per_run)

    if any(_free_text(v) for v in values):
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
    # Pages a read tool fetches are chosen while it runs, so they generalize to a URL prefix per host.
    if not force_exact and tool.startswith(READ_VERBS) and all(isinstance(v, str) and URL.match(v) for v in values):
        by_host = {}
        for v in values:
            by_host.setdefault(URL.match(v).group(0), []).append(v)
        prefixes = sorted(_prefix(vs) + "*" for vs in by_host.values())
        if len(prefixes) == 1:
            return Rule("url_pattern", prefixes[0], note="pages under this URL prefix; resolves '..' and encodings")
        return Rule("url_patterns", prefixes, note="pages under one URL prefix per host")
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
        return _range(arg, min(values), max(values), headroom)
    if all(isinstance(v, str) for v in values):
        if all(v.startswith("/") for v in values):
            return Rule("subpath", os.path.commonpath(values))
        prefix = _prefix(values)
        if prefix:
            return Rule("pattern", prefix + "*")
    return Rule("oneof", sorted(distinct, key=str))


def _free_text(v):
    """Prose, not an identifier: any whitespace, or long with no '/'. Long paths stay paths."""
    return isinstance(v, str) and (re.search(r"\s", v) is not None or (len(v) > FREE_TEXT_LEN and "/" not in v))


def _range(arg, low, high, headroom):
    observed = f"observed {low}..{high}"
    if headroom is None:
        return Rule("range", (low, high), note=observed)
    if MONEY.search(arg):
        return Rule("range", (low, high), note=observed + "; money-like, so no headroom")
    if high <= 0:
        return Rule("range", (low, high), note=observed + "; no headroom below zero")
    ceiling = _round_up(high * headroom)
    floor = 0 if low >= 0 else low
    return Rule("range", (floor, ceiling), note=f"{observed}; proposed {floor}..{ceiling} with headroom")


def _round_up(x):
    """Smallest of 1, 2, 5 x 10^n that is at least x: 503 -> 1000, 5100 -> 10000."""
    scale = 10 ** math.floor(math.log10(x))
    step = next(m for m in (1, 2, 5, 10) if m * scale >= x)
    value = step * scale
    return int(value) if value == int(value) else value


def _prefix(values):
    """Shared prefix cut back to its last '/', so 'fixagent/agent-2026...' generalizes to 'fixagent/'."""
    if not all(isinstance(v, str) for v in values):
        return ""
    prefix = os.path.commonprefix(values)
    prefix = prefix[: prefix.rfind("/") + 1]
    return prefix if len(prefix) >= MIN_PREFIX else ""


def _relative_paths(arg, values):
    # By name only: "acme/site" has a slash but a repo is not a folder to widen into.
    return any(word in arg.lower() for word in PATH_ARGS) and all(
        isinstance(v, str) and v and not v.startswith("/") for v in values)


def _key(v):
    return json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v




def _single_run_note(by_run):
    return "seen in one run only: may be a per-run value (use --var)" if len(by_run) == 1 else ""


def run_values(step_rules, step_calls, run_calls):
    """Values for per-run arguments: from this step's calls, else anywhere in the run (the minter's context)."""
    out = {}
    for args in step_rules.values():
        for arg, rule in args.items():
            if rule.kind == "var":
                seen = [c["args"][arg] for c in step_calls if arg in c["args"]] or [
                    c["args"][arg] for c in run_calls if arg in c["args"]]
                if seen:
                    out[arg] = seen[0] if rule.value == "exact" else sorted(set(seen))
    return out


def bound_only(step_rules, values):
    """Drop tools whose per-run arguments have no value: the run does not call them."""
    return {t: a for t, a in step_rules.items() if all(r.kind != "var" or n in values for n, r in a.items())}
