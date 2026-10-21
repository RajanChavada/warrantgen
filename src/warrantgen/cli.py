"""warrantgen: infer Tenuo warrants from recorded tool calls, then write templates, delegation chains or tests."""
import argparse
import json
import sys
from pathlib import Path

from warrantgen.chain import hop_rules, hop_values, mint_chains
from warrantgen.emit import Unsupported, dump, template
from warrantgen.infer import bound_only, infer, load_trace, run_values
from warrantgen.testgen import render_tests


def main(argv=None):
    parser = argparse.ArgumentParser(prog="warrantgen", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("infer", "show the rules inferred for each step"),
                            ("template", "write one step as a tenuo-cli template (version 2)"),
                            ("chain", "show the per-hop delegation tree and check each hop narrows"),
                            ("test", "write pytest tests: recorded calls allowed, near-misses denied")):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("traces", nargs="+", help="trace files (JSON lines)")
        sub.add_argument("--var", action="append", default=[], help="treat this argument as a per-run value")
        sub.add_argument("--exact", action="append", default=[], help="pin this argument to its observed value")
        sub.add_argument("--headroom", default="1", help="scale range maxima before rounding up, or 'none'")
    commands.choices["infer"].add_argument("--json", action="store_true", help="print rules as JSON")
    commands.choices["chain"].add_argument("--from-run", help="mint every hop with this run's values")
    commands.choices["test"].add_argument("--out", help="write here instead of stdout")
    commands.choices["test"].add_argument("--warrant-for", metavar="MODULE:FUNCTION",
                                          help="test your own warrants: (step, values, issuer, holder_public_key)")
    commands.choices["test"].add_argument("--known-looser", action="append", default=[], metavar="ARG",
                                          help="an argument your warrants loosen on purpose (strict xfail)")
    tpl = commands.choices["template"]
    tpl.add_argument("--step", required=True, help="step name, or a unique prefix of it")
    tpl.add_argument("--from-run", help="fill per-run values from this recorded run")
    tpl.add_argument("--set", action="append", default=[], metavar="[TOOL.]NAME=VALUE",
                     help="per-run value for NAME in every tool, or in TOOL only; commas make a list (repeatable)")
    tpl.add_argument("--ttl", default="5m")
    tpl.add_argument("--out", help="write here instead of stdout")
    args = parser.parse_args(argv)

    records = [r for path in args.traces for r in load_trace(path)]
    headroom = None if args.headroom == "none" else float(args.headroom)
    steps, excluded = infer(records, variables=args.var, exact=args.exact, headroom=headroom)
    if args.command == "infer":
        return _show(steps, excluded, args.json)
    if args.command == "chain":
        return _chain(records, args, excluded)
    if args.command == "test":
        text = render_tests(steps, [r for r in records if r["run"] not in excluded], source=", ".join(args.traces),
                            warrant_for=args.warrant_for, known_looser=args.known_looser)
        Path(args.out).write_text(text) if args.out else sys.stdout.write(text)
        return 0
    return _template(steps, records, args)


def _show(steps, excluded, as_json):
    if as_json:
        print(json.dumps({"steps": {s: {t: {a: r.__dict__ for a, r in args.items()} for t, args in tools.items()}
                                    for s, tools in steps.items()}, "quarantined": excluded}, indent=2, default=str))
        return 0
    for step, tools in steps.items():
        print(f"step {step}")
        for tool, args in tools.items():
            for arg, rule in args.items():
                print(f"  {tool + '.' + arg:28} {_describe(rule):44} {rule.note}")
    for run, reasons in excluded.items():
        print(f"quarantined run {run}: {'; '.join(reasons)}")
    return 0


def _describe(rule):
    v = rule.value
    if rule.kind == "var":
        return {"exact": "per-run value", "oneof": "per-run list", "under": "per-run folders"}[v]
    if rule.kind == "range":
        return f"range {v[0]}..{v[1]}"
    if rule.kind == "wildcard":
        return "any"
    if rule.kind == "under":
        return f"under {', '.join(v)}"
    text = f"{rule.kind} {v!r}"
    return text if len(text) <= 44 else text[:41] + "..."


def _chain(records, args, excluded):
    hops = hop_rules([r for r in records if r["run"] not in excluded], variables=args.var, exact=args.exact,
                     headroom=None if args.headroom == "none" else float(args.headroom))
    for hop in sorted(hops, key=lambda h: (h.split("/"), h)):
        depth = hop.count("/") + 1 if hop else 0
        print(f"{'  ' * depth}{hop or '(root)'}: {', '.join(sorted(hops[hop]))}")
    if not args.from_run:
        print("pass --from-run RUN to mint every hop and let Tenuo check that each one narrows")
        return 0
    run = [r for r in records if r["run"] == args.from_run]
    if not run:
        print(f"--from-run {args.from_run!r} is not in the traces", file=sys.stderr)
        return 2
    from tenuo import SigningKey  # throwaway keys: this checks the chain, it doesn't hand one out

    keys = {hop: SigningKey.generate() for hop in hops}
    chains, notes = mint_chains(hops, SigningKey.generate(), keys, ttl=300, values=hop_values(hops, run))
    print(f"minted {len(chains)} hops; Tenuo accepted every delegation")
    for note in notes:
        print(f"  {note}")
    return 0


def _template(steps, records, args):
    matches = [s for s in steps if s == args.step] or [s for s in steps if s.startswith(args.step)]
    if len(matches) != 1:
        print(f"--step {args.step!r} matches {len(matches)} steps: {', '.join(steps)}", file=sys.stderr)
        return 2
    step = matches[0]
    rules = steps[step]
    values = {}
    if args.from_run:
        run = [r for r in records if r["run"] == args.from_run]
        if not run:
            print(f"--from-run {args.from_run!r} is not in the traces", file=sys.stderr)
            return 2
        values = run_values(rules, [r for r in run if r["step"] == step], run)
    for item in args.set:
        name, _, value = item.partition("=")
        values[name] = _parse_value(value, rules, name)
    try:
        doc = template(bound_only(rules, values) if args.from_run else rules, name=step.split("+")[0],
                       ttl=args.ttl, bindings=values)
    except Unsupported as e:
        print(f"step {step} cannot be written as a template:", file=sys.stderr)
        for gap in e.gaps:
            print(f"  {gap}", file=sys.stderr)
        return 2
    text = dump(doc)
    if args.out:
        Path(args.out).write_text(text)
    else:
        sys.stdout.write(text)
    return 0


def _parse_value(value, rules, name):
    """NAME applies to every tool with that argument; TOOL.NAME to one tool."""
    tool, _, arg = name.rpartition(".")
    kinds = {r.value for t, args in rules.items() for a, r in args.items()
             if a == arg and (not tool or t == tool) and r.kind == "var"}
    if kinds & {"oneof", "under"}:
        return value.split(",")
    return int(value) if value.isdigit() else value


if __name__ == "__main__":
    sys.exit(main())
