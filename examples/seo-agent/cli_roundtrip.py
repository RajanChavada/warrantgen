"""Leave-one-run-out through tenuo-cli: infer from the other runs, write a template with the held-out run's
values, `tenuo-cli template validate` and `warrant issue` it, then replay the run against the issued file.

TENUO_CLI is the command that runs the CLI (default: tenuo-cli).
Usage: uv run python examples/seo-agent/cli_roundtrip.py examples/seo-agent/traces.jsonl
"""
import os
import shlex
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from tenuo_core import decode_warrant_stack_base64
from tenuo.keys import load_signing_key_from_file

from evaluate import allowed, bindings, is_attack, usable
from warrantgen import infer, load_trace
from warrantgen.emit import Unsupported, dump, template

CLI = shlex.split(os.environ.get("TENUO_CLI", "tenuo-cli"))


def cli(home, *args):
    r = subprocess.run([*CLI, "--home", str(home), *args], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"tenuo-cli {' '.join(args[:2])}: {r.stdout.strip()} {r.stderr.strip()}"[:300])
    return r.stdout


def main(path):
    rows = load_trace(path)
    work = Path(tempfile.mkdtemp())
    home = work / ".tenuo"
    cli(home, "init", "--dev")
    holder = load_signing_key_from_file(str(home / "keys" / "agent.key"))
    tally, gaps, misses, issued = Counter(), Counter(), [], 0
    for held in sorted({r["run"] for r in rows}):
        steps, _ = infer([r for r in rows if r["run"] != held])
        mine = [r for r in rows if r["run"] == held]
        for n, step in enumerate(sorted({r["step"] for r in mine})):
            calls = [r for r in mine if r["step"] == step]
            rules = steps.get(step)
            bound = bindings(rules, [c for c in calls if not is_attack(c)], [c for c in mine if not is_attack(c)])
            try:
                doc = template(usable(rules, bound), name=step.split("+")[0], bindings=bound)
            except Unsupported as e:
                for g in e.gaps:
                    gaps[step.split("+")[0], g.split(": ", 1)[1]] += 1
                continue
            file = work / f"{held}-{n}.yaml"
            file.write_text(dump(doc))
            cli(home, "template", "validate", "--file", str(file))
            out = work / f"{held}-{n}.warrant"
            cli(home, "warrant", "issue", "--template", str(file), "--holder", "agent", "--out", str(out))
            issued += 1
            chain = list(decode_warrant_stack_base64(out.read_text().strip()))
            warrant = chain[-1]
            for c in calls:
                ok, why = allowed(warrant, _Issuer(warrant), holder, c["tool"], c["args"])
                kind = "attack" if is_attack(c) else "legit"
                tally[kind, ok] += 1
                if ok == (kind == "attack"):
                    misses.append(f"{held:20} {kind:6} {c['tool']:13} {why}")
    legit, attacks = tally["legit", True] + tally["legit", False], tally["attack", True] + tally["attack", False]
    print(f"templates issued by tenuo-cli: {issued}")
    print(f"legit calls allowed:  {tally['legit', True]}/{legit}")
    print(f"attack calls denied:  {tally['attack', False]}/{attacks}")
    print("steps the template format could not express:")
    for (step, gap), count in sorted(gaps.items()):
        print(f"  {count:3}x {step:14} {gap}")
    print("\n".join(misses))


class _Issuer:
    """allowed() wants an object with .public_key; trust is the issuer that signed the CLI's warrant."""

    def __init__(self, warrant):
        self.public_key = warrant.issuer


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    main(sys.argv[1])
