"""Reconcile a draft warrant (a tenuo-cli template) with recorded runs.

Each draft argument comes out as one of: tighten (the runs fit a narrower rule), exceeds (the runs needed more than
the draft, never widened), untested (no recorded call, kept as written), per-run (narrow it when issuing) or keep.
Tenuo decides every comparison: recorded values are checked by the draft's own constraints, and a narrower rule is
proposed only if Tenuo accepts it as a delegation of the draft.
"""
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from tenuo import Exact, OneOf, Pattern, Range, Regex, SigningKey, Subpath, UrlPattern, Warrant, Wildcard

from warrantgen.chain import _grant
from warrantgen.emit import _value

# The template syntax tenuo-cli reads (warrant_template.compile_constraint on tenuo's feat/local-cli-review).
_RANGE = re.compile(r"(-?\d+(?:\.\d+)?)?\.\.(-?\d+(?:\.\d+)?)?")
_TYPED = {"exact": Exact, "pattern": Pattern, "subpath": Subpath, "regex": Regex, "url_pattern": UrlPattern}


@dataclass(frozen=True)
class Finding:
    kind: str  # tighten, exceeds, untested, per-run, keep
    tool: str
    arg: str = ""
    draft: object = None
    proposed: object = None
    note: str = ""


def load_draft(path):
    doc = yaml.safe_load(Path(path).read_text())
    if not isinstance(doc, dict) or doc.get("version") != 2 or not isinstance(doc.get("capabilities"), dict):
        raise ValueError(f"{path}: not a tenuo-cli template (version 2 with capabilities)")
    return doc


def compile_value(value):
    """One template value as a Tenuo constraint."""
    if value == "any":
        return Wildcard()
    if isinstance(value, str):
        match = _RANGE.fullmatch(value)
        if match and any(match.groups()):
            return Range(*(None if g is None else float(g) if "." in g else int(g) for g in match.groups()))
        return Exact(value)
    if isinstance(value, list):
        return OneOf(value)
    if isinstance(value, dict) and len(value) == 1:
        (kind, arg), = value.items()
        if kind == "wildcard" and arg is True:
            return Wildcard()
        if kind == "range" and isinstance(arg, dict):
            return Range(arg.get("min"), arg.get("max"))
        if kind == "one_of":
            return OneOf(arg)
        if kind in _TYPED:
            return _TYPED[kind](arg)
    raise ValueError(f"unsupported constraint {value!r}")


def reconcile(draft, rules, calls):
    """Return (proposal, findings). `rules` is the step's inferred {tool: {arg: Rule}}; `calls` its clean records.

    The proposal is the draft with each tightened argument replaced, and nothing else changed.
    """
    caps = {tool: dict(args or {}) for tool, args in draft["capabilities"].items()}
    compiled = {tool: {arg: compile_value(v) for arg, v in args.items()} for tool, args in caps.items()}
    key = SigningKey.generate()  # throwaway: the draft is minted only so Tenuo can judge each narrowing
    builder = Warrant.mint_builder()
    for tool, args in compiled.items():
        builder = builder.capability(tool, **args)
    parent = builder.holder(key.public_key).ttl(60).mint(key)

    findings = []
    for tool in sorted({c["tool"] for c in calls} - set(caps)):
        n = sum(c["tool"] == tool for c in calls)
        findings.append(Finding("exceeds", tool, note=f"not in the draft; {n} recorded calls would be denied"))
    proposal = {tool: dict(args) for tool, args in caps.items()}
    for tool, args in caps.items():
        tool_calls = [c["args"] for c in calls if c["tool"] == tool]
        if not tool_calls:
            findings.append(Finding("untested", tool, note="no recorded call: kept, no allow test"))
            continue
        for arg in sorted({a for c in tool_calls for a in c} - set(args)):
            findings.append(Finding("exceeds", tool, arg, note="not in the draft, and Tenuo denies unlisted arguments"))
        for arg, value in args.items():
            seen = [c[arg] for c in tool_calls if arg in c]
            outside = [v for v in seen if not compiled[tool][arg].satisfies(v)]
            if not seen:
                findings.append(Finding("untested", tool, arg, value, note="never passed in a recorded call: kept"))
            elif outside:
                findings.append(Finding("exceeds", tool, arg, value, note=f"{len(outside)} of {len(seen)} recorded "
                                        f"values outside the draft, e.g. {outside[0]!r}; never widened"))
            else:
                runs = len({c["run"] for c in calls if c["tool"] == tool and arg in c["args"]})
                findings.append(_tighten(parent, key, compiled, tool, arg, value, rules[tool][arg], runs, proposal))
    return {**draft, "capabilities": proposal}, findings


def _tighten(parent, key, compiled, tool, arg, value, rule, runs, proposal):
    if rule.kind == "var":
        return Finding("per-run", tool, arg, value, note="differs per run: kept; narrow it when issuing")
    if rule.kind == "exact" and runs < 2:
        # One run can't tell a fixed value from a per-run one; pinning it would deny the next run.
        return Finding("keep", tool, arg, value, rule.value, note="seen in one run only: record more runs to pin it")
    try:
        tighter = _value(rule, None)
    except ValueError as e:
        return Finding("keep", tool, arg, value, note=f"a narrower {rule.kind} fits, but it {e}")
    if tighter == value:
        return Finding("keep", tool, arg, value, note=rule.note or "already as tight as the runs show")
    caps = {t: dict(a) for t, a in compiled.items()}
    caps[tool][arg] = compile_value(tighter)
    try:
        _grant(parent, key, key, caps, 60)
    except Exception:
        return Finding("keep", tool, arg, value, tighter, note="Tenuo does not accept the inferred rule as narrower")
    proposal[tool][arg] = tighter
    return Finding("tighten", tool, arg, value, tighter, note=rule.note)
