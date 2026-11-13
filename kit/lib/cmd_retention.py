"""factory retention <ep>/<platform> [--root DIR]: write retention per beat ID for one live post."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kit.lib import retention  # noqa: E402


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="factory retention", description=__doc__.split(": ", 1)[1].strip())
    parser.add_argument("post", help="the live post, <ep>/<platform>")
    parser.add_argument("--root", default=".", help="tree to read and write (default: current directory)")
    args = parser.parse_args(argv)
    try:
        text = retention.build(args.root, args.post)
    except retention.RetentionError as e:
        for problem in e.problems:
            sys.stderr.write(f"retention: {problem}\n")
        return 1
    rel = retention.output(args.post)
    (Path(args.root) / rel).write_bytes(text.encode("utf-8"))
    n = len(json.loads(text)["beats"])
    sys.stdout.write(f"retention {args.post}: {n} beats written to {rel}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
