"""factory tick [--apply] [--root DIR] [--room R]: take the next mechanical step of every open ticket.

What comes next is ``kit.lib.steps``; this command only carries it out (system brief section 12). Every
comment it causes is written by the module that owns it: ``LAUNCH`` by ``cmd_launch``, ``[LINT]`` by
``lintpost``, the gate's by ``cmd_gate``, ``LOCK`` by ``cmd_lock`` and a timed-out run's ``BLOCKED`` by
``finish``. A judgment is a ``NEED`` line and the label ``needs:orchestrator`` on the ticket.

Without ``--apply`` it prints the lines and writes nothing. Exit 0, 1 when a step failed, 2 when the
production or GitHub cannot be read.
"""

import argparse
import contextlib
import io
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from kit.lib import (blind, cmd_gate, cmd_launch, cmd_lock, finish, github, gitstate, harness, launch,  # noqa: E402
                     ledger, lintpost, lockrules, markers, production, steps, tickets)

LABEL = "needs:orchestrator"
LOCK_DIR = "tick.lock"
STALE_SECONDS = 30 * 60
GATE_RULE, BASE_RULE = 9, 10
ERRORS = (github.GitHubError, gitstate.GitError, blind.BlindError, lintpost.PostError, tickets.TicketError,
          production.ProductionError, OSError)
READ_ERRORS = (production.ProductionError, github.GitHubError, tickets.TicketError, gitstate.GitError, OSError)


class Failed(Exception):
    """A step that was not taken; its text follows ``FAIL #<n> <step>: ``."""


def _message(error) -> str:
    if isinstance(error, (blind.BlindError, lintpost.PostError)):
        return " ".join(str(error).split())
    return finish.describe(error)


@contextlib.contextmanager
def _captured():
    """What a called command prints is kept from this command's output; yields its standard error."""
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        yield err


def _main(name, main, argv, ok):
    """Run ``factory <name>``; ``Failed`` with its last standard error line when the exit is not in ``ok``."""
    with _captured() as err:
        code = main(argv)
    if code in ok:
        return
    lines = [line for line in err.getvalue().splitlines() if line.strip()]
    if not lines:
        raise Failed(f"exit {code}")
    last = lines[-1]
    raise Failed(last[len(name) + 2:] if last.startswith(name + ": ") else last)


def _order(comment):
    return (comment["created_at"], comment["id"])


def _launch_id(comments, role):
    """The launch id of the newest ``LAUNCH <role>`` comment."""
    found = None
    for comment in sorted(comments, key=_order):
        m = markers.parse(comment["body"])
        if m and m.verb == "LAUNCH" and m.args[0] == role:
            found = m.args[1]
    return found


def _void(pull):
    """A ``LOCK`` for the pull's current head and manifest was followed by ``cmd_lock.VOID``, both by
    ``cmd_lock.ROLE``: the merge at this head failed, so the lock is not tried again by a tick."""
    locked = f"locked head {pull['git']['head']} manifest {pull['git']['manifest']}"
    posted = [c for c in sorted(pull["comments"], key=_order) if c["role"] == cmd_lock.ROLE]
    for before, after in zip(posted, posted[1:]):
        m = markers.parse(before["body"])
        if (after["body"].rstrip() == cmd_lock.VOID and m is not None and m.verb == "LOCK"
                and locked in (line.strip() for line in before["body"].splitlines())):
            return True
    return False


def _take_lock(state_dir):
    """The lock directory this run made, or None when another tick holds it."""
    path = state_dir / LOCK_DIR
    for _ in range(2):
        try:
            os.mkdir(path)
            return path
        except FileExistsError:
            pass
        try:
            if time.time() - path.stat().st_mtime < STALE_SECONDS:
                return None
            shutil.rmtree(path)
        except FileNotFoundError:
            pass
    return None


class Tick:
    def __init__(self, p, gh, apply, now):
        self.p, self.gh, self.apply, self.now = p, gh, apply, now
        self.counts = {"STEP": 0, "NEED": 0, "WAIT": 0, "FAIL": 0}
        self.locked_rooms = set()
        self.state = tickets.load(p, gh, p.root)

    def say(self, line):
        self.counts[line.split(" ", 1)[0]] += 1
        print(line, flush=True)
        return line

    def has_key(self, pull):
        return blind.has_key(self.p, pull["number"], pull["git"]["head"])

    def take(self, n, word, line, action):
        """``line`` when the step is taken (or only printed), else its ``FAIL`` line; an action may return
        the line to print in place of ``line``."""
        if not self.apply:
            return line
        try:
            instead = action()
        except Failed as failed:
            return f"FAIL #{n} {word}: {failed}"
        except ERRORS as error:
            return f"FAIL #{n} {word}: {_message(error)}"
        return instead or line

    # -- one ticket

    def ticket(self, number):
        ticket = self.state.tickets[number]
        n = ticket["number"]
        args = (self.p, self.state, ticket, self.p.root, self.now, self.has_key)
        try:
            entries = steps.all_steps(*args)
            first = steps.next_step(*args)
        except ERRORS as error:
            self.say(f"FAIL #{n} read: {_message(error)}")
            return
        state = self.state
        lines = [self.say(self.entry(state, ticket, entry, first)) for entry in entries]
        if first["step"] == "wake" and first not in entries:
            lines.append(self.say(f"NEED #{n} {first['state']} {first['reason']}"))
        if self.apply:
            self.label(ticket, lines)
            if any(line.startswith(f"STEP #{n} lock ") for line in lines):
                self.state = tickets.load(self.p, self.gh, self.p.root)

    def label(self, ticket, lines):
        n = ticket["number"]
        need = any(line.startswith("NEED ") for line in lines)
        has = LABEL in ticket["labels"]
        try:
            if need and not has:
                self.gh.add_label(n, LABEL)
            elif has and not need:
                self.gh.remove_label(n, LABEL)
        except ERRORS as error:
            self.say(f"FAIL #{n} label: {_message(error)}")

    def entry(self, state, ticket, entry, first):
        n, m, step, role = ticket["number"], entry["pull"], entry["step"], entry["role"]
        if step == "none":
            return f"WAIT #{n} {entry['state']} {entry['reason']}"
        if step == "wake":
            return f"NEED #{n} {entry['state']} {entry['reason']}"
        if step == "lock":
            return self.lock(state, ticket, entry, first)
        if step == "launch":
            target, on = (m, f"PR #{m}") if m is not None else (n, f"#{n}")
            argv = [role, str(target), "--root", str(self.p.root)]
            return self.take(n, "launch", f"STEP #{n} launch {role} on {on}",
                             lambda: _main("launch", cmd_launch.main, argv, (0, 3)))
        if step == "lint":
            return self.take(n, "lint", f"STEP #{n} lint PR #{m}", lambda: self.lint(m))
        if step == "blind":
            return self.take(n, "blind", f"STEP #{n} blind PR #{m}", lambda: self.blind(state, m))
        # step == "timeout"
        comments = state.pulls[m]["comments"] if m is not None else state.ticket_comments.get(n, [])
        launch_id = _launch_id(comments, role)
        return self.take(n, "timeout", f"STEP #{n} timeout {role} {launch_id}",
                         lambda: self.timeout(role, launch_id))

    # -- the actions

    def lint(self, m):
        with _captured():
            body, _ = lintpost.run(self.p, self.gh, self.p.root, m)
        self.gh.post(m, body, lintpost.ROLE)

    def blind(self, state, m):
        blind.prepare(self.p, state, self.p.root, state.pulls[m])

    def timeout(self, role, launch_id):
        name = f"{role}-{launch_id}"
        try:
            run = launch.load_run(production.state_dir(self.p) / "runs" / name)
        except (OSError, ValueError, KeyError):
            raise Failed(f"no run {name}") from None
        with _captured():
            finish.post(self.p, self.gh, self.p.root, run, harness.Outcome("failed", None, "timed out"))

    def lock(self, state, ticket, entry, first):
        n, m, room = ticket["number"], entry["pull"], ticket["room"]
        if entry != first:
            return f"WAIT #{n} lockable PR #{m} is not the ticket's next step"
        pull = state.pulls[m]
        root = self.p.root
        try:
            rules = lockrules.evaluate(self.p, state, ticket, pull, root)
            contains = gitstate.contains(root, pull["head_ref"], pull["base_ref"])
        except ERRORS as error:
            return f"FAIL #{n} read: {_message(error)}"
        failed = [rule for rule in rules if not rule.ok]
        other = [rule for rule in failed if rule.number not in (GATE_RULE, BASE_RULE)]
        if other:
            return f"NEED #{n} lock refused: rule {other[0].number} {other[0].name}"
        numbers = {rule.number for rule in failed}
        if GATE_RULE in numbers:
            seen = (pull["git"]["head"], pull["git"]["manifest"])
            if any(ledger.gate_marker(c) is not None and markers.reviewed(c["body"]) == seen
                   for c in pull["comments"]):
                return f"NEED #{n} lock refused: rule {GATE_RULE} gate"
            argv = [str(m), "--root", str(root), "--apply"]
            return self.take(n, "gate", f"STEP #{n} gate PR #{m}",
                             lambda: _main("gate", cmd_gate.main, argv, (0, 1)))
        if BASE_RULE in numbers:
            if not contains and tickets.HOLD not in pull["labels"]:
                return self.take(n, "sync", f"STEP #{n} sync PR #{m}", lambda: self.sync(n, pull))
            return f"NEED #{n} lock refused: rule {BASE_RULE} base"
        if _void(pull):
            return f"NEED #{n} lock refused: merge failed at {pull['git']['head'][:12]}"
        if room in self.locked_rooms:
            return f"WAIT #{n} lockable (one lock per room per tick)"
        line = self.take(n, "lock", f"STEP #{n} lock PR #{m}", lambda: self.merge(state, ticket, pull))
        if line.startswith("STEP "):
            self.locked_rooms.add(room)
        return line

    def merge(self, state, ticket, pull):
        with _captured():
            code = cmd_lock.lock(self.p, self.gh, state, ticket, pull)
        if code != 0:
            raise Failed("merge failed")

    def sync(self, n, pull):
        """Merge the base into the pull's branch with a merge commit, then push the branch."""
        root = self.p.root
        old = pull["git"]["head"]
        base = gitstate.head(root, pull["base_ref"])
        with tempfile.TemporaryDirectory(prefix="factory-sync-") as tmp:
            tree = Path(tmp) / "tree"
            gitstate.add_worktree(root, tree, old)
            try:
                try:
                    gitstate.git(tree, *github.GIT_IDENTITY, "merge", "--no-ff", "--no-edit", base)
                except gitstate.GitError:
                    if _merging(tree):
                        return f"NEED #{n} conflict with base"
                    raise
                new = gitstate.head(tree)
            finally:
                gitstate.remove_worktree(root, tree)
        ref = "refs/heads/" + pull["head_ref"]
        gitstate.git(root, "update-ref", ref, new, old)
        try:
            self.gh.push(root, pull["head_ref"])
        except github.GitHubError:
            gitstate.git(root, "update-ref", ref, old, new)
            raise
        return None


def _merging(tree) -> bool:
    """A merge stopped on a conflict in ``tree``."""
    try:
        gitstate.git(tree, "rev-parse", "-q", "--verify", "MERGE_HEAD")
    except gitstate.GitError:
        return False
    return True


def _tick(p, gh, rooms, apply) -> int:
    if tickets.frozen(gh):
        print("FROZEN")
        return 0
    now = production.now()
    tick = Tick(p, gh, apply, now)
    for error in tick.state.errors:
        tick.say(f"NEED - {error}")
    for room in rooms:
        for number in sorted(tick.state.tickets):
            ticket = tick.state.tickets[number]
            if ticket["room"] == room and ticket["state"] == "open":
                tick.ticket(number)
    c = tick.counts
    print(f"tick: {c['STEP']} steps, {c['NEED']} need, {c['WAIT']} wait, {c['FAIL']} failed")
    return 1 if c["FAIL"] else 0


def main(argv):
    parser = argparse.ArgumentParser(prog="factory tick", description=__doc__.split(": ", 1)[1].split("\n")[0])
    parser.add_argument("--apply", action="store_true", help="take the steps (default: only print them)")
    parser.add_argument("--root", default=".", help="a directory inside the production (default: .)")
    parser.add_argument("--room", help="only this room's tickets (default: every room)")
    args = parser.parse_args(argv)
    try:
        p = production.load(production.find_root(args.root))
        rooms = production.rooms(p)
        if args.room is not None and args.room not in rooms:
            print(f"tick: unknown room {args.room}", file=sys.stderr)
            return 2
        gh = github.connect(p.settings)
        state_dir = production.state_dir(p)
        state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        held = _take_lock(state_dir)
    except READ_ERRORS as error:
        print(f"tick: {_message(error)}", file=sys.stderr)
        return 2
    if held is None:
        print("tick: another tick is running")
        return 0
    try:
        return _tick(p, gh, rooms if args.room is None else [args.room], args.apply)
    except READ_ERRORS as error:
        print(f"tick: {_message(error)}", file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(held, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
