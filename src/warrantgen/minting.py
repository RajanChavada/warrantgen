"""Build a real signed Tenuo warrant from inferred rules."""
import re

from tenuo import All, Exact, Not, OneOf, Pattern, Range, Regex, Subpath, Subset, UrlPattern, Warrant, Wildcard

# Same shape as seo-agent's readable(): under the folders, never a secret file, never `..`.
SECRET = (r"(?i)(^|/)(\.env[^/]*|\.git|\.ssh|secrets?|credentials?|[^/]*(secret|credential|private)[^/]*|"
          r"id_(rsa|dsa|ecdsa|ed25519)[^/]*|\.npmrc|\.netrc|\.pypirc|[^/]*\.(pem|key|p12|pfx))(/|$)")
PARENT = r"(^|/)\.\.(/|$)"
SAFE_CHARS = r"[A-Za-z0-9._@+()\[\]/-]"  # no %, so an encoded `..` or `/` cannot slip past


def exact(value):
    # Tenuo's Exact takes strings only; a pinned number is a one-point range.
    return Range(min=value, max=value) if isinstance(value, (int, float)) else Exact(value)


def under(paths, within=None):
    """Paths under these folders, never a secret, never `..`. `within` is a parent hop's folders: Tenuo only
    narrows an All by adding parts, so the child keeps the parent's parts and adds its own folder rule."""
    base = [Regex(_folders(within or paths)), Not(Regex(SECRET)), Not(Regex(PARENT))]
    return All(base + [Regex(_folders(paths))] if within else base)


def _folders(paths):
    dirs = sorted({p.split("/")[0] for p in paths})
    return r"^(" + "|".join(re.escape(d) for d in dirs) + rf")(/{SAFE_CHARS}*)?$"


def constraint(rule, binding=None):
    if rule.kind == "exact":
        return exact(rule.value)
    if rule.kind == "var":
        if binding is None:
            raise ValueError("a per-run argument needs a value at mint time")
        if rule.value == "under":
            return under(binding)
        return exact(binding) if rule.value == "exact" else OneOf(list(binding))
    if rule.kind == "oneof":
        return OneOf(list(rule.value))
    if rule.kind == "under":
        return under(rule.value)
    if rule.kind == "subset":
        return Subset(list(rule.value))
    if rule.kind == "range":
        low, high = rule.value
        return Range(min=low, max=high)
    if rule.kind == "pattern":
        return Pattern(rule.value)
    if rule.kind == "url_pattern":
        # Not Pattern: a plain glob lets ".../library/../../internal" climb out of the prefix.
        return UrlPattern(rule.value)
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
