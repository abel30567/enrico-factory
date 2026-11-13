"""factory trends <topic> <platform> [--root DIR] [--view-floor N] [--write TREE]: rank the stored reads of a topic on one platform, with no model."""

import argparse
import os
import re
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from kit.lib import production, trends  # noqa: E402


def main(argv):
    parser = argparse.ArgumentParser(prog="factory trends", description=__doc__.split(": ", 1)[1])
    parser.add_argument("topic", help="the topic, as in research/topics/<topic>.json")
    parser.add_argument("platform", choices=trends.PLATFORMS, help="the platform whose reads are ranked")
    parser.add_argument("--root", default=".", help="a directory inside the production (default: .)")
    parser.add_argument("--view-floor", default=str(trends.VIEW_FLOOR),
                        help=f"leave out videos with fewer views (default: {trends.VIEW_FLOOR})")
    parser.add_argument("--write", metavar="TREE", help="write the trend card under TREE (the root or a worktree)")
    args = parser.parse_args(argv)

    if not trends.TOPIC.fullmatch(args.topic):
        print("trends: topic must match [a-z0-9][a-z0-9-]*", file=sys.stderr)
        return 2
    if not re.fullmatch(r"\d+", args.view_floor) or int(args.view_floor) < 1:
        print("trends: --view-floor must be a whole number >= 1", file=sys.stderr)
        return 2
    floor = int(args.view_floor)
    card = trends.card_file(args.topic, args.platform)

    try:
        p = production.load(production.find_root(args.root))
        trends.check_topic(p.root, args.topic, args.platform)
        reads = trends.load_reads(trends.reads_dir(p, args.topic, args.platform), args.topic, args.platform)
        read_at, rows, skips, under = trends.rank(reads, floor)
        lost = []
        if args.write is not None:
            path = Path(args.write) / card
            notes = {}
            if path.is_file():
                try:
                    notes = trends.read_card(path.read_text(encoding="utf-8"), card)
                except UnicodeDecodeError:
                    raise trends.TrendError(f"{card} is not a trend card (line 1)") from None
            lost = trends.dropped(rows, notes)
            text = trends.card_text(rows, read_at, notes)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(text.encode("utf-8"))
            except OSError:
                print(f"trends: {card}: cannot write", file=sys.stderr)
                return 2
    except trends.TrendError as error:
        print(f"trends: {error}", file=sys.stderr)
        return 1
    except production.ProductionError as error:
        print(f"trends: {error}", file=sys.stderr)
        return 2

    out = [f"trends {args.topic} {args.platform}: read {read_at}, {len(reads[-1]['videos'])} videos, "
           f"{under} under the view floor {floor}"]
    out += [f"{row['ranking']} {row['rank']} {row['id']} {trends.score_text(row)}" for row in rows]
    out += [f"skip {ranking}: {lacking} of {of} videos lack {what}" for ranking, lacking, of, what in skips]
    if args.write is not None:
        out += [f"dropped note {ranking} {vid}" for ranking, vid in lost]
        out.append(f"wrote {card}: {len(rows)} rows")
    sys.stdout.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
