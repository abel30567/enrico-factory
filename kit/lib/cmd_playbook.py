"""factory playbook [--root DIR] [--apply]: list the playbook lessons whose expiry date has passed.

The lessons `playbook/PB-<n>.md` are read on the base. A lesson is expired when its `expires` date is before
today, the date of `FACTORY_NOW` or the UTC clock; a misnamed lesson or one with no valid date is never
dropped. Prints `playbook PB-<n>: expired <date>` per expired lesson. Without `--apply` it writes nothing.
With it, it first brings the local base up to GitHub's, commits the deletions on a new branch
`playbook/expired-<today>`, pushes it and opens a pull request with no ticket. Exit 0 when listed; 2 on
refusal or error.
"""

import argparse
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from kit.lib import gitstate, github, playbook, production, tickets  # noqa: E402

AUTHOR = "factory"


class PlaybookError(Exception):
    """A refusal."""


ERRORS = (PlaybookError, production.ProductionError, github.GitHubError, gitstate.GitError)


def _expired(p, base, today) -> list:
    """``(path, expires)`` of every expired lesson on the base, by number."""
    out = []
    for path in playbook.lesson_paths(gitstate.files(p.root, base, playbook.DIR)):
        if playbook.number(path) is None:
            continue
        text = gitstate.show(p.root, base, path)
        if text is not None and playbook.expired(text, today):
            out.append((path, playbook.expires(text)))
    return out


def _body(today, lessons) -> str:
    lines = [f"Lessons of the playbook whose expiry date is before {today}, dropped by `factory playbook`:", ""]
    lines += [f"- `{path}`: expired {last}" for path, last in lessons]
    return "\n".join(lines) + "\n"


def _drop(p, gh, base, branch, today, lessons) -> int:
    paths = [path for path, _ in lessons]
    with tempfile.TemporaryDirectory(prefix="factory-playbook-") as tmp:
        tree = Path(tmp) / "tree"
        gitstate.add_worktree(p.root, tree, base, branch=branch)
        try:
            for path in paths:
                (tree / path).unlink()
            gitstate.commit(tree, paths, f"playbook: drop {len(paths)} expired lesson(s)", AUTHOR)
        finally:
            gitstate.remove_worktree(p.root, tree)
    gh.push(p.root, branch)
    return gh.open_pull(branch, base, f"Drop the expired playbook lessons of {today}", _body(today, lessons))


def _playbook(p, gh, apply) -> int:
    base = production.base(p)
    today = date.fromisoformat(production.now()[:10])
    lessons = _expired(p, base, today)
    if not lessons:
        print(f"playbook: no lesson has expired on {today}")
        return 0
    branch = f"playbook/expired-{today}"
    if apply and gitstate.git(p.root, "branch", "--list", branch).strip():
        raise PlaybookError(f"branch {branch} already exists")
    for path, last in lessons:
        print(f"playbook PB-{playbook.number(path)}: expired {last}")
    if apply:
        number = _drop(p, gh, base, branch, today, lessons)
        print(f"playbook: PR #{number} drops {len(lessons)} lesson(s) on branch {branch}")
    return 0


def main(argv):
    parser = argparse.ArgumentParser(prog="factory playbook", description=__doc__.split(": ", 1)[1].split("\n")[0])
    parser.add_argument("--root", default=".", help="a directory inside the production (default: .)")
    parser.add_argument("--apply", action="store_true", help="drop the expired lessons by a pull request")
    args = parser.parse_args(argv)
    try:
        p = production.load(production.find_root(args.root))
        gh = github.connect(p.settings)
        if args.apply:
            gh.sync_base(p.root, production.base(p))
        if tickets.frozen(gh):
            print("playbook: frozen", file=sys.stderr)
            return 2
        return _playbook(p, gh, args.apply)
    except ERRORS as error:
        print(f"playbook: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
