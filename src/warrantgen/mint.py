"""Build a real signed Tenuo warrant from inferred rules."""
from tenuo import Exact, OneOf, Pattern, Range, Subpath, Warrant, Wildcard


def constraint(rule, binding=None):
    if rule.kind == "exact":
        return Exact(rule.value)
    if rule.kind == "var":
        if binding is None:
            raise ValueError("a per-run argument needs a value at mint time")
        return Exact(binding) if rule.value == "exact" else OneOf(list(binding))
    if rule.kind == "oneof":
        return OneOf(list(rule.value))
    if rule.kind == "range":
        low, high = rule.value
        return Range(min=low, max=high)
    if rule.kind == "pattern":
        return Pattern(rule.value)
    if rule.kind == "subpath":
        return Subpath(rule.value)
    if rule.kind == "wildcard":
        return Wildcard()
    raise ValueError(f"unknown rule kind {rule.kind!r}")


def mint(step_rules, issuer, holder_public_key, *, ttl, bindings=None):
    """Mint one step's warrant. `bindings` gives each per-run argument its value for this run."""
    bindings = bindings or {}
    builder = Warrant.mint_builder()
    for tool, args in step_rules.items():
        builder = builder.capability(tool, **{a: constraint(r, bindings.get(a)) for a, r in args.items()})
    return builder.holder(holder_public_key).ttl(ttl).mint(issuer)
