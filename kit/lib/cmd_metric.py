"""factory metric <loop> <ref> [--root DIR]: refuse a change of a lab loop's metric without a better held-out comparison.

Compares the loop file `lab/loops/<loop>.json` at `<ref>` with the base's. No change of `metric`, `better` or
`command`: one line, exit 0. A change: the held-out list and the real retention are read in an export of the
base head, the comparison `lab/metric-amendments/<loop>.tsv` at `<ref>`; three lines on standard output, then
a fourth and exit 0 when the new metric orders strictly more pairs as real retention, else one line on standard
error and exit 2. Every other refusal is one line on standard error, exit 2. Writes nothing; never exits 1.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kit.lib import gitstate, metric, production  # noqa: E402

ERRORS = (*metric.ERRORS, production.ProductionError, gitstate.GitError)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="factory metric", description=__doc__.split(": ", 1)[1].split("\n")[0])
    parser.add_argument("loop", help="the loop id: lab/loops/<loop>.json on the base and at the ref")
    parser.add_argument("ref", help="the commit that changes the loop file")
    parser.add_argument("--root", default=".", metavar="DIR", help="production tree (default: current directory)")
    args = parser.parse_args(argv)
    loop = args.loop
    try:
        p = production.load(production.find_root(args.root))
        result = metric.check(p.root, production.base(p), loop, args.ref)
    except ERRORS as e:
        sys.stderr.write(f"metric: {e}\n")
        return 2
    old = result["old"]
    if not result["changed"]:
        sys.stdout.write(f"metric {loop}: {old} is unchanged\n")
        return 0
    new, counted, a, b = result["new"], result["counted"], result["a"], result["b"]
    sys.stdout.write(f"metric {loop}: {result['episodes']} held-out episodes, {counted} pairs of different real "
                     f"retention, {result['equal']} of equal retention not counted\n")
    for name, agree in ((old, a), (new, b)):
        sys.stdout.write(f"metric {loop} {name}: {agree} of {counted} pairs ordered as real retention, "
                         f"{metric.share(agree, counted)}\n")
    if b > a:
        sys.stdout.write(f"metric {loop}: {new} replaces {old}: it predicts real retention better on held-out "
                         f"episodes\n")
        return 0
    sys.stderr.write(f"metric: {new} does not predict real retention better than {old} on held-out episodes "
                     f"({b} against {a} of {counted} pairs)\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
