"""factory tick [--apply] [--root DIR] [--room R]: take the next mechanical step of every open ticket.

What comes next is ``kit.lib.steps``; this command only carries it out (system brief section 12). Every
comment it causes is written by the module that owns it: ``LAUNCH`` by ``cmd_launch``, ``[LINT]`` by
``lintpost``, the gate's by ``cmd_gate``, ``LOCK`` by ``cmd_lock`` and a timed-out run's ``BLOCKED`` by
``finish``. A judgment is a ``NEED`` line and the label ``needs:orchestrator`` on the ticket.

Before any ticket's step, the tick files the next stage's ticket of every locked ticket (``kit.lib.filing``):
one line ``STEP #<n> file <stage> <unit>`` per filing, ``<n>`` being the locked ticket, and with ``--apply``
a new issue with the labels ``tracked``, ``room:``, ``stage:`` and ``unit:``. A filed ticket takes its first
step on the next tick, and a pull locked by this tick files on the next one. A filing is not a lock.

Before the filings, the tick refreshes each grant that is due (``kit.lib.grants``): one line
``STEP - refresh <id>``, and with ``--apply`` the refresh, which stores the rotated token in the key file. A
refresh that fails is ``FAIL - refresh <id>: <reason>``.

Then, still before the filings, the producer's monitors (``kit.lib.monitors``, system brief section 11)
read every ticket whatever ``--room`` says: one ``NEED - monitor <name>: ...`` line per trip, and with
``--apply`` one issue per trip labelled ``needs:orchestrator`` unless an open issue has its title. Each step is the one
``monitors.instead`` gives: a budget trip stops the run (``WAIT #<n> budget ...``), a canon contradiction and a
re-break are ``NEED`` lines, charter drift holds the lock of the summary stage (``NEED #<n> charter-drift ...``),
and after twice ``monitors.NO_WIN_ROUNDS`` lost blind rounds the step is ``STEP #<n> close #<n>``, which with
``--apply`` closes the ticket. A monitor file ``kit/lib/monitor_<name>.py`` runs after them; its trip's ``labels``
go on its issue, and each of its ``tickets`` is filed once by title (``STEP - monitor <name> file <title>``). A
monitor that cannot be read prints ``FAIL - monitor <name>: <message>`` and the tick goes on.

With the setting ``MAX_WORKERS`` (a whole number >= 1; empty or absent: no cap), a launch step is taken only
while fewer workers run: one per ``LAUNCH`` on an open ticket or its open pulls that no marker answers
(``steps.running``), counted when the tick starts, plus one per launch step this tick takes. Over the cap the
step is ``WAIT #<n> <state> <k> running, MAX_WORKERS is <cap>``.

With ``--apply`` it first brings the local base up to GitHub's (``sync_base``), so its gate, lock rules
and launches read the base GitHub has. A lock whose merge succeeded but whose sync failed ends the tick:
one ``FAIL -`` line, no step for any further ticket. Without ``--apply`` it prints the lines, writes
nothing and fetches nothing. Exit 0, 1 when a step failed, 2 when the production or GitHub cannot be read
(a first sync that fails included).
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

from kit.lib import (blind, cmd_gate, cmd_launch, cmd_lock, cmd_run, down, filing, finish, github,  # noqa: E402
                     gitstate, grants, harness, launch, ledger, lintpost, lockrules, markers, production,
                     resources, steps, tickets)

from kit.lib import monitors  # noqa: E402

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


def _max_workers(p):
    """The setting ``MAX_WORKERS`` as a number, or None when it is empty or absent."""
    value = p.settings.get("MAX_WORKERS", "")
    if not value:
        return None
    if not (value.isascii() and value.isdigit() and value[0] != "0"):  # [1-9][0-9]*
        raise production.ProductionError("setting MAX_WORKERS is not a whole number >= 1")
    return int(value)


class Tick:
    def __init__(self, p, gh, apply, now, cap=None):
        self.p, self.gh, self.apply, self.now, self.cap = p, gh, apply, now, cap
        self.counts = {"STEP": 0, "NEED": 0, "WAIT": 0, "FAIL": 0}
        self.locked_rooms = set()
        self.open_issues = None
        self.trips = []
        self.state = tickets.load(p, gh, p.root)
        self.running = 0
        if cap is not None:
            self.running = sum(steps.running(p, self.state, ticket)
                               for ticket in self.state.tickets.values() if ticket["state"] == "open")

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

    def file(self, rooms):
        """One ``STEP #<n> file <stage> <unit>`` line per ticket due in ``rooms``; with ``--apply``, the issue."""
        try:
            due = filing.due(self.p, self.state, self.p.root)
        except ERRORS as error:
            self.say(f"FAIL - file: {_message(error)}")
            return
        for found in due:
            if found["room"] not in rooms:
                continue
            n = found["from"]

            def create(found=found):
                self.gh.create_issue(found["title"], found["body"], found["labels"])
            self.say(self.take(n, "file", f"STEP #{n} file {found['stage']} {found['unit']}", create))

    def refresh(self):
        """One ``STEP - refresh <id>`` line per grant that is due; with ``--apply``, the refresh."""
        secrets_dir = resources.expand(production.setting(self.p, "SECRETS_DIR", ""))
        for rid in grants.due(secrets_dir, self.now):
            line = f"STEP - refresh {rid}"
            if self.apply:
                try:
                    grants.refresh(secrets_dir, rid, self.now)
                except grants.Failed as failed:
                    line = f"FAIL - refresh {rid}: {failed}"
            self.say(line)

    # -- one ticket

    def ticket(self, number):
        ticket = self.state.tickets[number]
        n = ticket["number"]
        args = (self.p, self.state, ticket, self.p.root, self.now, self.has_key)
        try:
            entries = steps.all_steps(*args)
            first = steps.next_step(*args)
            first = monitors.instead(self.p, self.trips, ticket, first)
            entries = [monitors.instead(self.p, self.trips, ticket, entry) for entry in entries]
        except ERRORS as error:
            self.say(f"FAIL #{n} read: {_message(error)}")
            return
        if first["state"] == "no-convergence":
            entries = [first]
        held = monitors.holds(self.trips)
        entries = [e for i, e in enumerate(entries) if e["state"] not in held or e not in entries[:i]]
        state = self.state
        lines = [self.say(self.entry(state, ticket, entry, first)) for entry in entries]
        if first not in entries:
            lines.append(self.say(self.entry(state, ticket, first, first)))
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
            try:
                found = self.down(role)
            except ERRORS as error:
                return f"FAIL #{n} read: {_message(error)}"
            if found is not None:
                return f"WAIT #{n} {entry['state']} resource {found[0]} is down (#{found[1]})"
            found = down.waiting(self.p, self.open_issues, role, self.now)
            if found is not None:
                return f"WAIT #{n} {entry['state']} slot {found[0]} is out of usage until {found[1]}"
            if self.cap is not None and self.running >= self.cap:
                return f"WAIT #{n} {entry['state']} {self.running} running, MAX_WORKERS is {self.cap}"
            target, on = (m, f"PR #{m}") if m is not None else (n, f"#{n}")
            argv = [role, str(target), "--root", str(self.p.root)]
            line = self.take(n, "launch", f"STEP #{n} launch {role} on {on}",
                             lambda: _main("launch", cmd_launch.main, argv, (0, 3)))
            if line.startswith("STEP "):
                self.running += 1
            return line
        if step == "close":
            return self.take(n, "close", f"STEP #{n} close #{n}", lambda: self.gh.close_issue(n))
        if step == "lint":
            return self.take(n, "lint", f"STEP #{n} lint PR #{m}", lambda: self.lint(m))
        if step == "blind":
            return self.take(n, "blind", f"STEP #{n} blind PR #{m}", lambda: self.blind(state, m))
        if step in ("gate", "run"):
            main = cmd_gate.main if step == "gate" else cmd_run.main
            argv = [entry["job"], "--pull", str(m), "--root", str(self.p.root), "--apply"]
            return self.take(n, step, f"STEP #{n} {step} {entry['job']} PR #{m}",
                             lambda: _main(step, main, argv, (0, 1)))
        # step == "timeout"
        comments = state.pulls[m]["comments"] if m is not None else state.ticket_comments.get(n, [])
        launch_id = _launch_id(comments, role)
        return self.take(n, "timeout", f"STEP #{n} timeout {role} {launch_id}",
                         lambda: self.timeout(role, launch_id))

    def monitor(self):
        """One line per trip of ``monitors.trips``; with ``--apply``, its ``[MONITOR]`` issue unless one is open."""
        try:
            text = gitstate.show(self.p.root, production.base(self.p), ledger.PATH)
            rows = ledger.read(text) if text is not None else []
        except (ledger.LedgerError, *ERRORS) as error:
            self.say(f"FAIL - monitor budget: {error if isinstance(error, ledger.LedgerError) else _message(error)}")
            rows = None
        self.trips = monitors.trips(self.p, self.state, rows, self.now,
                                    lambda path: gitstate.show(self.p.root, production.base(self.p), path))
        for trip in self.trips:
            name = trip["monitor"]
            if "error" in trip:
                self.say(f"FAIL - monitor {name}: {_message(trip['error'])}")
                continue
            self.say(f"NEED - {trip['line']}")
            if not self.apply:
                continue
            try:
                if self.open_issues is None:
                    self.open_issues = self.gh.issues("open")
                if not any(issue["title"] == trip["title"] for issue in self.open_issues):
                    self.gh.create_issue(trip["title"], trip["body"], [LABEL, *trip.get("labels", [])])
            except ERRORS as error:
                self.say(f"FAIL - monitor {name}: {_message(error)}")
        self.file_tickets()

    def file_tickets(self):
        """One ``STEP - monitor <name> file <title>`` line per ticket of a trip whose title no issue has; with
        ``--apply``, the issue with the ticket's labels. Nothing while a tracked issue does not parse."""
        if self.state.errors:
            return
        taken = {f"[{t['key']}] {t['summary']}" for t in self.state.tickets.values()}
        taken |= {issue["title"] for issue in self.state.others}
        for trip in self.trips:
            if "error" in trip:
                continue
            name = trip["monitor"]
            for found in trip.get("tickets", []):
                if found["title"] in taken:
                    continue
                line = f"STEP - monitor {name} file {found['title']}"
                if self.apply:
                    try:
                        self.gh.create_issue(found["title"], found["body"], found["labels"])
                    except ERRORS as error:
                        self.say(f"FAIL - monitor {name}: {_message(error)}")
                        continue
                taken.add(found["title"])
                self.say(line)

    def down(self, role):
        """``down.blocking`` for ``role``; the open issues are read once per tick, at the first launch."""
        if self.open_issues is None:
            self.open_issues = self.gh.issues("open")
        return down.blocking(self.p, self.open_issues, role)

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
        finish.cleanup(self.p.root, run)

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
    cap = _max_workers(p)
    if tickets.frozen(gh):
        print("FROZEN")
        return 0
    if apply:
        gh.sync_base(p.root, production.base(p))
    now = production.now()
    tick = Tick(p, gh, apply, now, cap)
    for error in tick.state.errors:
        tick.say(f"NEED - {error}")
    tick.refresh()
    tick.monitor()
    tick.file(rooms)
    try:
        for room in rooms:
            for number in sorted(tick.state.tickets):
                ticket = tick.state.tickets[number]
                if ticket["room"] == room and ticket["state"] == "open":
                    tick.ticket(number)
    except cmd_lock.Unsynced as error:
        tick.say(f"FAIL - {error}")
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
