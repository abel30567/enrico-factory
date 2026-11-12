"""factory unfreeze [--root DIR]: lift the kill switch, close every open issue labelled factory:freeze.

Exit 0 when unfrozen or not frozen; 2 when the production or GitHub cannot be reached.
"""

import argparse
import os
import sys

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from kit.lib import github, production, tickets  # noqa: E402
from kit.lib.gitstate import GitError  # noqa: E402
from kit.lib.github import GitHubError  # noqa: E402
from kit.lib.production import ProductionError  # noqa: E402
from kit.lib.tickets import TicketError  # noqa: E402


def main(argv):
    parser = argparse.ArgumentParser(prog="factory unfreeze", description=__doc__.split(": ", 1)[1].split("\n")[0])
    parser.add_argument("--root", default=".", help="a directory in the production (default: .)")
    args = parser.parse_args(argv)
    try:
        p = production.load(production.find_root(args.root))
        gh = github.connect(p.settings)
        open_freezes = sorted(i["number"] for i in gh.issues("open") if tickets.FREEZE in i["labels"])
        if not open_freezes:
            print("not frozen")
            return 0
        for number in open_freezes:
            gh.close_issue(number)
            print(f"unfrozen (#{number})")
    except (ProductionError, GitHubError, GitError, TicketError) as error:
        print("unfreeze: " + " ".join(str(error).split()), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
