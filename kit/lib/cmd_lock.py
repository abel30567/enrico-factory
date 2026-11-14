"""factory lock [--room R] [--apply] [--root DIR] [--json]: report which pull requests may be locked, and lock one.

The rules are ``kit.lib.lockrules`` (system brief section 9). Without ``--apply`` it only reports and
writes nothing. With it, it locks the first lockable candidate, by pull number, and stops: it posts
``[<ORCHESTRATOR>] LOCK`` on the pull as ``program:lock``, merges at the head it evaluated and closes the
ticket. Before it reads, ``--apply`` brings the local base up to GitHub's (``sync_base``), and again after
the merge. Exit 0 report printed, 2 frozen, usage, or the production or GitHub cannot be read (a sync that
fails before the read included), 3 merge failed (the LOCK is void), 4 merged and the ticket closed but the
local base not synced, 5 merged and the ticket closed, but the asset registry row or a stale shot's ticket not
written, or, beside it, the post registry row of a ``distribution/post`` lock (``registry/posts.tsv``,
``kit/lib/posts.py``) not written. Each ``--apply``, before its report, first writes that row for every merged
pull of a ``distribution/post`` ticket that no row names, by pull number; one that fails also exits 5.
"""

import argparse
import json
import os
import re
import sys

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from kit.lib import github, gitstate, ledger, lockrules, markers, posts, production, registry, tickets  # noqa: E402
from kit.lib.gitstate import GitError  # noqa: E402
from kit.lib.github import GitHubError  # noqa: E402
from kit.lib.production import ProductionError  # noqa: E402
from kit.lib.tickets import TicketError  # noqa: E402

ROLE = "program:lock"
VOID = "lock: merge failed, the LOCK above is void"
ERRORS = (ProductionError, GitHubError, GitError, TicketError)
POST_ERRORS = (posts.PostsError, GitError, GitHubError)
_LOCKED_HEAD = re.compile(r"locked head ([0-9a-f]{40}) manifest \S+")


class Unsynced(Exception):
    """GitHub merged the pull and the ticket is closed, but the local base could not be brought up to it."""


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
    unsynced = None
    try:
        gh.sync_base(p.root, pull["base_ref"])
    except GitHubError as error:
        unsynced = error
    gh.close_issue(ticket["number"])
    print(f"locked PR #{number} at {merged[:12]}")
    if unsynced is not None:
        raise Unsynced(f"PR #{number} is merged at {merged[:12]}, base not synced: {unsynced}")
    try:
        lines = registry.record(p, gh, p.root, state, ticket, pull)
    except (registry.RegistryError, GitError, GitHubError) as error:
        print("lock: registry: " + " ".join(str(error).split()), file=sys.stderr)
        return 5
    for line in lines:
        print(line)
    if posts.is_post(ticket):
        try:
            print(posts.append(p, gh, p.root, ticket["unit"], number, pull["git"]["head"]))
        except POST_ERRORS as error:
            print(_unwritten(number, merged, error), file=sys.stderr)
            return 5
    return 0


def _unwritten(number, merged, error) -> str:
    return f"lock: PR #{number} is merged at {merged[:12]}, {posts.PATH} not written: " + " ".join(str(error).split())


def _locked_head(gh, number):
    """The head the newest ``LOCK`` by ``program:lock`` on the pull names, or None."""
    found = None
    for c in sorted(gh.comments(number), key=lambda c: (c["created_at"], c["id"])):
        m = markers.parse(c["body"])
        heads = [h.group(1) for h in map(_LOCKED_HEAD.fullmatch, c["body"].splitlines()) if h]
        if c.get("role") == ROLE and m is not None and m.verb == "LOCK" and heads:
            found = heads[0]
    return found


def _merge_of(root, base, head) -> str:
    """The commit of ``base``'s first-parent line that brought ``head`` in; ``head`` when none is found."""
    try:
        found = gitstate.git(root, "rev-list", "--first-parent", "--ancestry-path", f"{head}..{base}").split()
    except GitError:
        found = []
    return found[-1] if found else head


def backfill(p, gh) -> int:
    """Write the post registry row of every merged pull of a ``distribution/post`` ticket that no row names,
    by pull number. 0, or 5 when a row is not written."""
    base = production.base(p)
    units = {}
    for issue in gh.issues("all"):
        try:
            ticket = tickets.parse(issue)
        except TicketError:
            continue
        if ticket is not None and posts.is_post(ticket):
            units[ticket["number"]] = ticket["unit"]
    named = {row["pull"] for row in posts.rows(p.root, base)}
    for pull in sorted(gh.pulls("all"), key=lambda pull: pull["number"]):
        number = pull["number"]
        if pull["state"] != "merged" or pull.get("ticket") not in units or str(number) in named:
            continue
        head = _locked_head(gh, number)
        try:
            if head is None:
                raise posts.PostsError(f"no LOCK on PR #{number} names its locked head")
            print(posts.append(p, gh, p.root, units[pull["ticket"]], number, head))
        except POST_ERRORS as error:
            print(_unwritten(number, _merge_of(p.root, base, head) if head else "-", error), file=sys.stderr)
            return 5
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
        if args.apply:
            gh.sync_base(p.root, production.base(p))
            if backfill(p, gh):
                return 5
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
    except Unsynced as error:
        print(f"lock: {error}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
