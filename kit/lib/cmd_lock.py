"""factory lock [--room R] [--apply] [--root DIR] [--json]: report which pull requests may be locked, and lock one.

The rules are ``kit.lib.lockrules`` (system brief section 9). Without ``--apply`` it only reports and
writes nothing. With it, it locks the first lockable candidate, by pull number, and stops: it posts
``[<ORCHESTRATOR>] LOCK`` on the pull as ``program:lock``, merges at the head it evaluated and closes the
ticket. Exit 0 report printed, 2 frozen, usage, or the production or GitHub cannot be read, 3 merge failed.
"""

import argparse
import json
import os
import sys

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from kit.lib import github, ledger, lockrules, production, tickets  # noqa: E402
from kit.lib.gitstate import GitError  # noqa: E402
from kit.lib.github import GitHubError  # noqa: E402
from kit.lib.production import ProductionError  # noqa: E402
from kit.lib.tickets import TicketError  # noqa: E402

ROLE = "program:lock"
VOID = "lock: merge failed, the LOCK above is void"
ERRORS = (ProductionError, GitHubError, GitError, TicketError)


def candidates(p, state, rooms) -> list:
    """``(pull, ticket, rules)`` for every open pull of an open ticket of ``rooms``, by pull number."""
    found = []
    for number in sorted(state.pulls):
        pull = state.pulls[number]
        ticket = state.tickets[pull["ticket"]]
        if pull["state"] != "open" or ticket["state"] != "open" or ticket["room"] not in rooms:
            continue
        found.append((pull, ticket, lockrules.evaluate(p, state, ticket, pull, p.root)))
    return found


def text(found) -> str:
    lines = []
    for pull, ticket, rules in found:
        failed = [rule for rule in rules if not rule.ok]
        verdict = f"NO {len(failed)} rule(s)" if failed else "LOCKABLE"
        lines.append(f"PR #{pull['number']} #{ticket['number']} {ticket['room']}/{ticket['stage']} "
                     f"{ticket['unit']}: {verdict}")
        lines += [f"  rule {rule.number} {rule.name}: {rule.reason}" for rule in failed]
    return "\n".join(lines)


def to_json(found) -> str:
    return json.dumps([{"pull": pull["number"], "ticket": ticket["number"], "lockable": lockrules.lockable(rules),
                        "rules": [rule._asdict() for rule in rules]}
                       for pull, ticket, rules in found], indent=2, sort_keys=True)


def relied_on(p, state, ticket, pull) -> str:
    """The verdicts the lock relies on: lint, checkers, judges (with their side when blinded), gate."""
    entry = production.stage(p, ticket["room"], ticket["stage"])
    summary = pull["summary"]
    parts = []
    if summary["lint"] is not None:
        parts.append(f"lint #{summary['lint']['id']}")
    for role, found in summary["checkers"].items():
        parts.append(f"{role} {found['verb']} #{found['id']}")
    sides = lockrules.judge_sides(p, state, pull)
    for role, found in summary["judges"].items():
        side = f" ({sides[role]})" if sides is not None else ""
        parts.append(f"{role} {found['verb']} #{found['id']}{side}")
    if lockrules.gate_needed(p, state, ticket, entry, lockrules.maker_role(entry, pull)):
        gates = [c for c in sorted(pull["comments"], key=lambda c: (c["created_at"], c["id"]))
                 if ledger.gate_marker(c) is not None]
        parts.append(f"gate #{gates[-1]['id']}")
    return "; ".join(parts) or "none"


def lock_body(p, state, ticket, pull) -> str:
    git = pull["git"]
    return (f"[{production.orchestrator(p, ticket['room']).upper()}] LOCK\n\n"
            f"locked head {git['head']} manifest {git['manifest']}\n"
            f"relied on: {relied_on(p, state, ticket, pull)}\n")


def lock(p, gh, state, ticket, pull) -> int:
    number = pull["number"]
    gh.post(number, lock_body(p, state, ticket, pull), ROLE)
    try:
        merged = gh.merge(p.root, number, pull["git"]["head"])
    except GitHubError:
        gh.post(number, VOID, ROLE)
        print("lock: merge failed", file=sys.stderr)
        return 3
    gh.close_issue(ticket["number"])
    print(f"locked PR #{number} at {merged[:12]}")
    return 0


def main(argv):
    parser = argparse.ArgumentParser(prog="factory lock", description=__doc__.split(": ", 1)[1].split("\n")[0])
    parser.add_argument("--room", help="only this room's pull requests (default: every room)")
    parser.add_argument("--apply", action="store_true", help="lock the first lockable pull request")
    parser.add_argument("--root", default=".", help="a directory in the production (default: .)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    try:
        p = production.load(production.find_root(args.root))
        rooms = production.rooms(p)
        if args.room is not None and args.room not in rooms:
            print(f"lock: no room {args.room}", file=sys.stderr)
            return 2
        gh = github.connect(p.settings)
        state = tickets.load(p, gh, p.root)
        if args.apply and state.frozen:
            print("lock: frozen", file=sys.stderr)
            return 2
        found = candidates(p, state, [args.room] if args.room is not None else rooms)
        report = to_json(found) if args.json else text(found)
        if report:
            print(report)
        if not args.apply:
            return 0
        chosen = next(((pull, ticket) for pull, ticket, rules in found if lockrules.lockable(rules)), None)
        if chosen is None:
            print("nothing to lock")
            return 0
        pull, ticket = chosen
        return lock(p, gh, state, ticket, pull)
    except ERRORS as error:
        print("lock: " + " ".join(str(error).split()), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
