"""factory reproduce <first> <second>: compares two runs of one render job exactly.

A run is a directory holding `state.json` and `render.json`. Prints `reproduce EQUAL` or
`reproduce DIFFERENT <n> differences`, one line per difference, then a note on the frame hashes; exit 0 when
equal, 1 when different. A file that cannot be compared is one line per problem on standard error, exit 2.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kit.lib import reproduce  # noqa: E402


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="factory reproduce", description=__doc__.split(": ", 1)[1].split("\n")[0])
    parser.add_argument("first", help="the first run: a directory holding state.json and render.json")
    parser.add_argument("second", help="the second run, the same way")
    args = parser.parse_args(argv)
    try:
        lines, note = reproduce.compare(args.first, args.second)
    except reproduce.ReproduceError as e:
        for problem in e.problems:
            sys.stderr.write(f"reproduce: {problem}\n")
        return 2
    if lines:
        n = len(lines)
        head = f"reproduce DIFFERENT {n} difference{'' if n == 1 else 's'}"
    else:
        head = "reproduce EQUAL"
    sys.stdout.write("".join(f"{line}\n" for line in [head, *lines, note]))
    return 1 if lines else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
