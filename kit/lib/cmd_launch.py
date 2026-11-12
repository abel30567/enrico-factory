"""factory launch <role> <ticket-or-pr> [task-file] [--root DIR] [--wait]: launch one role and post its marker.

``launch.prepare`` runs the checks, makes the worktree and posts ``LAUNCH``; a supervisor then runs the
worker and posts its marker (``finish.supervise``). The supervisor runs detached unless ``--wait`` is given
or the environment has ``FACTORY_LAUNCH_WAIT=1``. ``factory launch --supervise <run-dir> [--root DIR]`` is
that supervisor.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from kit.lib import blind, finish, github, launch, production, tickets  # noqa: E402
from kit.lib.gitstate import GitError  # noqa: E402

KIT = Path(__file__).resolve().parents[1]
READ_ERRORS = (production.ProductionError, github.GitHubError, tickets.TicketError, GitError)


def _parser():
    parser = argparse.ArgumentParser(prog="factory launch", description=__doc__.split(": ", 1)[1].split("\n")[0])
    parser.add_argument("role", nargs="?", help="the role id")
    parser.add_argument("number", nargs="?", type=int, help="the ticket or pull request number")
    parser.add_argument("task_file", nargs="?", help="a file holding the task (default: the role's default)")
    parser.add_argument("--root", default=".", help="a directory inside the production (default: .)")
    parser.add_argument("--wait", action="store_true", help="run the supervisor in this process")
    parser.add_argument("--supervise", metavar="RUN_DIR", help=argparse.SUPPRESS)
    return parser


def _pair_dir(p, gh, role, number):
    """``(pair directory or None, refused)``: a judge of a pull with a locked old version needs its pair."""
    if p.kinds.get(role) != "judge":
        return None, False
    state = tickets.load(p, gh, p.root)
    pull = state.pulls.get(number)
    if pull is None or "git" not in pull or not blind.old_exists(p, state, pull):
        return None, False
    pair = blind.pair_dir(p, pull["number"], pull["git"]["head"])
    if pair is None:
        print(f"launch: no blinded pair for #{number}; run factory blind {number}", file=sys.stderr)
        return None, True
    return pair, False


def _supervise(root, run_dir):
    try:
        p = production.load(production.find_root(root))
        gh = github.connect(p.settings)
        run = launch.load_run(run_dir)
    except (*READ_ERRORS, OSError, ValueError, KeyError) as error:
        print(f"launch: {type(error).__name__}: {finish.describe(error)}", file=sys.stderr)
        return 2
    return finish.supervise(p, gh, p.root, run)


def _detach(p, run):
    """Start the supervisor in its own session, its output appended to ``supervisor.log``."""
    argv = [sys.executable, str(KIT / "factory"), "launch", "--supervise", str(run.dir), "--root", str(p.root)]
    with open(run.dir / "supervisor.log", "ab") as log:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                         start_new_session=True)


def main(argv):
    parser = _parser()
    args = parser.parse_args(argv)
    if args.supervise is not None:
        if args.role is not None:
            parser.error("--supervise takes no role or number")
        return _supervise(args.root, args.supervise)
    if args.role is None or args.number is None:
        parser.error("give a role and a ticket or pull request number")

    try:
        p = production.load(production.find_root(args.root))
        gh = github.connect(p.settings)
        pair, refused = _pair_dir(p, gh, args.role, args.number)
        if refused:
            return 1
        run = launch.prepare(p, gh, p.root, args.role, args.number, task_file=args.task_file, pair_dir=pair)
    except launch.LaunchError as error:
        print(f"launch: {error}", file=sys.stderr)
        return 1
    except READ_ERRORS as error:
        print(f"launch: {error}", file=sys.stderr)
        return 2
    print(f"launched {args.role} {run.meta['id']}", flush=True)
    if args.wait or os.environ.get("FACTORY_LAUNCH_WAIT") == "1":
        return finish.supervise(p, gh, p.root, run)
    _detach(p, run)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
