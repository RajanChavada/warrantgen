"""Leave-one-run-out replay: infer from every other run, then replay the held-out run through Tenuo.

Per-run parameters are bound from the held-out run, as a minter would from its run context.
Usage: uv run python examples/seo-agent/evaluate.py examples/seo-agent/traces.jsonl
"""
import sys
import time
from collections import Counter

from tenuo import Authorizer, SigningKey

from warrantgen import infer, load_trace, mint


def is_attack(r):
    """Ground truth: what seo-agent's hand-written warrants denied when the trace was recorded."""
    return r["decision"] == "DENY"


def bindings(rules, calls, run_calls):
    """Values for per-run arguments: from this step's calls, else anywhere in the run (the minter's context)."""
    out = {}
    for args in rules.values():
        for arg, rule in args.items():
            if rule.kind == "var":
                seen = [c["args"][arg] for c in calls if arg in c["args"]] or [
                    c["args"][arg] for c in run_calls if arg in c["args"]]
                if seen:
                    out[arg] = seen[0] if rule.value == "exact" else sorted(set(seen))
    return out


def usable(rules, bound):
    """Drop tools whose per-run arguments this run never revealed: the run does not call them."""
    return {t: a for t, a in rules.items() if all(r.kind != "var" or n in bound for n, r in a.items())}


def allowed(warrant, issuer, holder, tool, args):
    try:
        sig = bytes(warrant.sign(holder, tool, args, int(time.time())))
        Authorizer(trusted_roots=[issuer.public_key]).authorize_one(warrant, tool, args, signature=sig)
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:70]}"


def main(path):
    rows = load_trace(path)
    issuer, holder = SigningKey.generate(), SigningKey.generate()
    tally, misses = Counter(), []
    for held in sorted({r["run"] for r in rows}):
        steps, _ = infer([r for r in rows if r["run"] != held])
        mine = [r for r in rows if r["run"] == held]
        for step in sorted({r["step"] for r in mine}):
            calls = [r for r in mine if r["step"] == step]
            rules = steps.get(step)
            if rules:
                legit = [c for c in calls if not is_attack(c)]
                bound = bindings(rules, legit, [c for c in mine if not is_attack(c)])
                w = mint(usable(rules, bound), issuer, holder.public_key, ttl=300, bindings=bound)
            for c in calls:
                ok, why = allowed(w, issuer, holder, c["tool"], c["args"]) if rules else (False, "step never seen")
                kind = "attack" if is_attack(c) else "legit"
                tally[kind, ok] += 1
                if ok == (kind == "attack"):
                    misses.append(f"{held:20} {kind:6} {c['tool']:13} {str(c['args'].get('path', ''))[:28]:28} {why}")
    legit, attacks = tally["legit", True] + tally["legit", False], tally["attack", True] + tally["attack", False]
    print(f"legit calls allowed:  {tally['legit', True]}/{legit}")
    print(f"attack calls denied:  {tally['attack', False]}/{attacks}")
    print("\n".join(misses))


if __name__ == "__main__":
    main(sys.argv[1])
