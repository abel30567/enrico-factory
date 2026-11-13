"""factory calibration <ep>/<platform> [--root DIR]: write the calibration rows of one live post."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kit.lib import calibration  # noqa: E402


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="factory calibration", description=__doc__.split(": ", 1)[1].strip())
    parser.add_argument("post", help="the live post, <ep>/<platform>")
    parser.add_argument("--root", default=".", metavar="DIR", help="production tree (default: current directory)")
    args = parser.parse_args(argv)
    root = Path(args.root)
    target = root / calibration.PATH
    try:
        rows = calibration.rows(root, args.post)
        old = target.read_text(encoding="utf-8") if target.is_file() else None
        text = calibration.merge(old, args.post, rows)
    except calibration.CalibrationError as e:
        sys.stderr.write(f"calibration: {e.problems[0]}\n")
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(text.encode("utf-8"))
    sys.stdout.write(f"calibration {args.post}: {len(rows)} rows written to {calibration.PATH}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
