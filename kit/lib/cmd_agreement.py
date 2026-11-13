"""factory agreement <loop> [--root DIR] [--tree DIR]: the rank agreement of a tuned judge with real retention on held-out episodes.

The held-out list `lab/held-out/<loop>.json` and the real retention are read under the root; the guard and the
verdicts `lab/verdicts/<loop>.tsv` in the tree measured: `--tree`, else `FACTORY_LAB_TREE`, else the root.
Prints one line and the value, exit 0; a refusal is one line on standard error, exit 2. Never exits 1, which
the lab reads as a broken floor.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kit.lib import agreement  # noqa: E402


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="factory agreement", description=__doc__.split(": ", 1)[1].split("\n")[0])
    parser.add_argument("loop", help="the loop id: lab/held-out/<loop>.json under the root")
    parser.add_argument("--root", default=".", metavar="DIR", help="production tree (default: current directory)")
    parser.add_argument("--tree", metavar="DIR", help="the tree measured (default: FACTORY_LAB_TREE, else the root)")
    args = parser.parse_args(argv)
    tree = args.tree if args.tree is not None else (os.environ.get("FACTORY_LAB_TREE") or args.root)
    try:
        agreement.check_loop(args.loop)
        if not Path(tree).is_dir():
            raise agreement.AgreementError("the tree measured is not a directory")
        result = agreement.measure(args.root, tree, args.loop)
    except agreement.AgreementError as e:
        sys.stderr.write(f"agreement: {e}\n")
        return 2
    sys.stdout.write(f"agreement {args.loop} {result['judge']}: {result['agree']} of {result['counted']} pairs agree "
                     f"with real retention, {result['equal']} of equal retention not counted\n")
    sys.stdout.write(json.dumps(result["value"]) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
