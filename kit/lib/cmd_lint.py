"""factory lint [--root DIR] [--only a,b] [--json] [--post PR]: run the lint checks on a tree."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kit.lib import github, gitstate, lint, lintpost, markers, production, tickets  # noqa: E402


def post(root: str, number: int) -> int:
    """Run the stage's lints on pull request `number`'s head and post the `[LINT]` marker."""
    try:
        p = production.load(production.find_root(root))
        gh = github.connect(p.settings)
        if tickets.frozen(gh):
            sys.stderr.write("lint: frozen\n")
            return 2
        existing = lintpost.posted(p, gh, p.root, number)
        if existing is not None:
            head = markers.reviewed(existing["body"])[0]
            sys.stderr.write(f"lint: already posted for {head[:12]}\n")
            return lintpost.EXIT[markers.parse(existing["body"]).verb]
        body, results = lintpost.run(p, gh, p.root, number)
        gh.post(number, body, lintpost.ROLE)
    except (lintpost.PostError, production.ProductionError, github.GitHubError, gitstate.GitError,
            tickets.TicketError) as e:
        sys.stderr.write(f"lint: {e}\n")
        return 2
    sys.stdout.write(body)
    return lint.exit_code(results)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="factory lint", description=__doc__.split(":")[1].strip())
    parser.add_argument("--root", default=".", help="tree to check (default: current directory)")
    parser.add_argument("--only", help="comma-separated check ids")
    parser.add_argument("--json", action="store_true", help="print the results as JSON")
    parser.add_argument("--post", type=int, metavar="PR",
                        help="lint the pull request's head for its stage and post the [LINT] marker")
    args = parser.parse_args(argv)
    if args.post is not None:
        if args.only is not None or args.json:
            sys.stderr.write("lint: --post cannot be combined with --only or --json\n")
            return 2
        return post(args.root, args.post)
    only = [c.strip() for c in args.only.split(",") if c.strip()] if args.only else None
    results = lint.run(Path(args.root), only)
    sys.stdout.write(lint.to_json(results) if args.json else lint.text(results))
    return lint.exit_code(results)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
