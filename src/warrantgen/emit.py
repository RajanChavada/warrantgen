"""Write inferred rules as a tenuo-cli warrant template (version 2), for one run's values.

The template format has no variables, no all/not and no subset. A step that needs one of those is
refused with the reasons, never written looser than inferred.
"""
import re

import yaml

_RANGE_LIKE = re.compile(r"-?[\d.]*\.\.-?[\d.]*")


class Unsupported(Exception):
    def __init__(self, gaps):
        super().__init__("; ".join(gaps))
        self.gaps = gaps


def template(step_rules, *, name, ttl="5m", bindings=None):
    """Return the template as a dict. Raises Unsupported listing every argument YAML cannot express."""
    bindings = bindings or {}
    capabilities, gaps = {}, []
    for tool, args in step_rules.items():
        capabilities[tool] = {}
        for arg, rule in args.items():
            try:
                capabilities[tool][arg] = _value(rule, bindings.get(arg))
            except ValueError as e:
                gaps.append(f"{tool}.{arg}: {e}")
    if gaps:
        raise Unsupported(gaps)
    return {"version": 2, "name": name, "ttl": ttl, "capabilities": capabilities}


def dump(document):
    return yaml.safe_dump(document, sort_keys=False, default_flow_style=False)


def _value(rule, binding):
    kind, value = rule.kind, rule.value
    if kind == "var":
        if binding is None:
            raise ValueError("differs per run and templates have no variables; pass this run's value")
        if value == "under":
            raise ValueError("needs all/not (folders minus secrets), which templates cannot express")
        kind, value = ("exact", binding) if value == "exact" else ("oneof", binding)
    if kind == "exact":
        if isinstance(value, (int, float)):
            return f"{value}..{value}"
        return {"exact": value} if value == "any" or _RANGE_LIKE.fullmatch(value) else value
    if kind == "oneof":
        if not all(isinstance(v, str) for v in value):
            raise ValueError("one_of takes strings only")
        return sorted(value)
    if kind == "range":
        low, high = value
        return f"{low}..{high}"
    if kind in ("pattern", "subpath"):
        return {kind: value}
    if kind == "wildcard":
        return "any"
    raise ValueError(f"needs {kind}, which templates cannot express")
