"""factory freeze [--root DIR] [--reason TEXT]: the kill switch, stop every launch and gate in the production.

The freeze is an open issue with the label ``factory:freeze`` (system brief section 11); nothing local
records it. Exit 0 when frozen or already frozen; 2 when the production or GitHub cannot be reached.
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

TITLE = "[FREEZE] production frozen"


def main(argv):
    parser = argparse.ArgumentParser(prog="factory freeze", description=__doc__.split(": ", 1)[1].split("\n")[0])
    parser.add_argument("--root", default=".", help="a directory in the production (default: .)")
    parser.add_argument("--reason", default="frozen by factory freeze", help="the freeze issue's body")
    args = parser.parse_args(argv)
    try:
        p = production.load(production.find_root(args.root))
        gh = github.connect(p.settings)
        open_freezes = sorted(i["number"] for i in gh.issues("open") if tickets.FREEZE in i["labels"])
        if open_freezes:
            print(f"already frozen (#{open_freezes[0]})")
            return 0
        number = gh.create_issue(TITLE, args.reason, [tickets.FREEZE])
    except (ProductionError, GitHubError, GitError, TicketError) as error:
        print("freeze: " + " ".join(str(error).split()), file=sys.stderr)
        return 2
    print(f"frozen (#{number})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
