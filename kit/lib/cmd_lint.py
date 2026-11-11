"""factory lint [--root DIR] [--only a,b] [--json]: run the lint checks on a tree."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kit.lib import lint  # noqa: E402


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="factory lint", description=__doc__.split(":")[1].strip())
    parser.add_argument("--root", default=".", help="tree to check (default: current directory)")
    parser.add_argument("--only", help="comma-separated check ids")
    parser.add_argument("--json", action="store_true", help="print the results as JSON")
    args = parser.parse_args(argv)
    only = [c.strip() for c in args.only.split(",") if c.strip()] if args.only else None
    results = lint.run(Path(args.root), only)
    sys.stdout.write(lint.to_json(results) if args.json else lint.text(results))
    return lint.exit_code(results)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
