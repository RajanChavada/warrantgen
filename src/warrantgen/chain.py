"""Per-hop delegation chains: one root warrant for the whole graph, then a narrower child for each sub-agent.

Hops come from recorded node paths: "team/researcher" sits under "team", which sits under the root (""). Each hop's
rules are inferred from its own calls and all its descendants', so a parent always covers its children. Children are
minted through Tenuo's delegation API, so Tenuo itself checks that every hop only narrows.
"""
from warrantgen.infer import infer, run_values
from tenuo import AnyOf, UrlPattern

from warrantgen.minting import constraint, mint, under


def within(step, hop):
    return hop == "" or step == hop or step.startswith(hop + "/")


def hop_rules(records, **infer_kwargs):
    """{hop: {tool: {arg: Rule}}} for every recorded node path and its ancestors."""
    hops = {""}
    for r in records:
        parts = r["step"].split("/")
        hops |= {"/".join(parts[:i]) for i in range(1, len(parts) + 1)}
    out = {}
    for hop in hops:
        rules = infer([dict(r, step=hop) for r in records if within(r["step"], hop)], **infer_kwargs)[0].get(hop)
        if rules:
            out[hop] = rules
    return out


def hop_values(hops, run):
    """Per-run values for each hop, from one recorded run's calls."""
    return {hop: run_values(rules, [r for r in run if within(r["step"], hop)], run) for hop, rules in hops.items()}


def mint_chains(hops, issuer, keys, *, ttl, values=None):
    """Return ({hop: [root, ..., hop]}, notes). `keys` maps every hop to its holder's SigningKey.

    An argument Tenuo would not narrow keeps its parent's constraint; `notes` lists each one.
    """
    values = values or {}
    chains, notes = {}, []
    for hop in sorted(hops, key=lambda h: (h != "", h.count("/"), h)):
        if hop == "":
            chains[hop] = [mint(hops[hop], issuer, keys[hop].public_key, ttl=ttl, bindings=values.get(hop))]
            continue
        parent = hop.rpartition("/")[0]
        warrant, kept = _narrow(chains[parent][-1], keys[parent], keys[hop], hops[parent], hops[hop],
                                values.get(parent, {}), values.get(hop, {}), ttl)
        chains[hop] = chains[parent] + [warrant]
        notes += [f"{hop}: {tool}.{arg} keeps its parent's constraint" for tool, arg in kept]
    return chains, notes


def _narrow(parent, parent_key, child_key, parent_rules, child_rules, parent_values, child_values, ttl):
    # Start from the parent's constraints for the child's tools (a child may not drop an argument), then
    # try each narrower argument alone so one Tenuo refusal doesn't cost the others.
    inherited = {t: {a: constraint(r, parent_values.get(a)) for a, r in parent_rules[t].items()} for t in child_rules}
    accepted, kept = {}, []
    for tool, args in child_rules.items():
        for arg, rule in args.items():
            try:
                candidate = _candidate(parent_rules[tool][arg], parent_values.get(arg), rule, child_values.get(arg))
                _grant(parent, parent_key, child_key, _with(inherited, {(tool, arg): candidate}), ttl)
                accepted[tool, arg] = candidate
            except Exception:
                kept.append((tool, arg))
    return _grant(parent, parent_key, child_key, _with(inherited, accepted), ttl), kept


def _candidate(parent_rule, parent_value, rule, value):
    parent_folders, folders = _folders(parent_rule, parent_value), _folders(rule, value)
    if parent_folders and folders:
        return under(folders, within=parent_folders)  # Tenuo narrows an All only by adding parts
    if parent_rule.kind == "url_patterns" and rule.kind in ("url_pattern", "url_patterns"):
        # Tenuo narrows an AnyOf only into a smaller AnyOf, never into a bare UrlPattern.
        return AnyOf([UrlPattern(p) for p in ([rule.value] if rule.kind == "url_pattern" else rule.value)])
    return constraint(rule, value)


def _folders(rule, value):
    if rule.kind == "under":
        return rule.value
    return value if rule.kind == "var" and rule.value == "under" else None


def _with(inherited, changes):
    caps = {tool: dict(args) for tool, args in inherited.items()}
    for (tool, arg), c in changes.items():
        caps[tool][arg] = c
    return caps


def _grant(parent, parent_key, child_key, caps, ttl):
    builder = parent.grant_builder()
    for tool, args in caps.items():
        builder = builder.capability(tool, **args)
    return builder.holder(child_key.public_key).ttl(ttl).grant(parent_key)
