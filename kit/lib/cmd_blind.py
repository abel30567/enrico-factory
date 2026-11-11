"""factory blind <pr> [--root DIR]: prepare a blinded old-against-new pair for a judge, key kept outside the worktree."""

import argparse
import os
import sys

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from kit.lib import blind, github, production, tickets  # noqa: E402
from kit.lib.gitstate import GitError  # noqa: E402


def main(argv):
    parser = argparse.ArgumentParser(prog="factory blind", description=__doc__.split(": ", 1)[1])
    parser.add_argument("pr", type=int, help="the pull request number")
    parser.add_argument("--root", default=".", help="a directory inside the production (default: .)")
    args = parser.parse_args(argv)

    try:
        p = production.load(production.find_root(args.root))
        gh = github.connect(p.settings)
        state = tickets.load(p, gh, p.root)
        if state.frozen:
            print("blind: frozen", file=sys.stderr)
            return 2
        pull = state.pulls.get(args.pr)
        if pull is None:
            print(f"blind: #{args.pr} is not a pull request of a tracked ticket", file=sys.stderr)
            return 1
        key = blind.prepare(p, state, p.root, pull)
        pair = blind.pair_dir(p, args.pr, key["head"])
    except blind.BlindError as error:
        print(f"blind: {error}", file=sys.stderr)
        return 1
    except (production.ProductionError, github.GitHubError, GitError, tickets.TicketError) as error:
        print(f"blind: {error}", file=sys.stderr)
        return 2
    print(f"blind {args.pr}: id {key['id']} pair {pair}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
