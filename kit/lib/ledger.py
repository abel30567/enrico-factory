"""The spend ledger and the producer's decision (system brief sections 7, 9 and 11).

``ledger/spend.tsv`` is append-only and written only by ``factory gate`` and ``factory run``, straight on
the base branch: the gate appends a ``job`` or ``lock`` row with its decision, the run a ``run`` row with
a job's real cost.
Money and compute are ``decimal.Decimal``. Nothing here prints or returns a setting's value other than
a cap's name.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from kit.lib import gitstate, markers, production, tickets

PATH = "ledger/spend.tsv"
COLUMNS = ("time", "kind", "hash", "ticket", "episode", "tool", "usd", "compute_min", "caller", "verdict",
           "cap")
HEADER = "\t".join(COLUMNS)
KINDS = ("job", "lock", "run")
VERDICTS = ("PASS", "STOP")
RUN_VERDICTS = ("DONE", "FAILED")
GATE_ROLE = "program:gate"
GATE_ACTOR = "PRODUCER"

# Which cost each money or compute cap limits, and whether it is the episode's (else the season's).
SPEND_CAPS = (("CAP_EPISODE_USD", "usd", True), ("CAP_SEASON_USD", "usd", False),
              ("CAP_EPISODE_COMPUTE_MIN", "compute_min", True), ("CAP_SEASON_COMPUTE_MIN", "compute_min", False))

_AMOUNT = re.compile(r"\d+(\.\d{1,4})?")


class LedgerError(Exception):
    pass


def _problem(row: dict) -> str | None:
    """What breaks Behaviour 1 in ``row`` (a dict of strings), or None."""
    if sorted(row) != sorted(COLUMNS):
        return "columns are not " + ", ".join(COLUMNS)
    for name in COLUMNS:
        value = row[name]
        if not isinstance(value, str) or not value:
            return f"{name} is empty"
        if any(c in value for c in "\t\n\r"):
            return f"{name} holds a tab or a newline"
    if row["kind"] not in KINDS:
        return "kind is not job, lock or run"
    if row["kind"] == "run":
        if row["verdict"] not in RUN_VERDICTS:
            return "verdict of a run is not DONE or FAILED"
        if row["cap"] != "-":
            return "cap of a run is not -"
    elif row["verdict"] not in VERDICTS:
        return "verdict is not PASS or STOP"
    if row["cap"] != "-" and row["cap"] not in production.CAPS:
        return "cap is not a cap name or -"
    if row["kind"] == "lock" and row["tool"] != "-":
        return "tool of a lock is not -"
    for name in ("usd", "compute_min"):
        if not _AMOUNT.fullmatch(row[name]):
            return f"{name} is not digits with at most 4 decimal places"
    return None


def read(text: str) -> list[dict]:
    """The rows of the ledger ``text``; ``LedgerError("ledger line <n>: ...")`` on the first bad line."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines or lines[0] != HEADER:
        raise LedgerError("ledger line 1: the header is not " + " ".join(COLUMNS))
    rows = []
    for n, line in enumerate(lines[1:], 2):
        fields = line.split("\t")
        if len(fields) != len(COLUMNS):
            raise LedgerError(f"ledger line {n}: {len(fields)} columns, not {len(COLUMNS)}")
        row = dict(zip(COLUMNS, fields))
        problem = _problem(row)
        if problem:
            raise LedgerError(f"ledger line {n}: {problem}")
        rows.append(row)
    return rows


def _text(value) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return value


def line(row: dict) -> str:
    """The ledger line for ``row``, without a newline. Amounts may be ``Decimal``, the ticket an int."""
    row = {name: _text(value) for name, value in row.items()}
    problem = _problem(row)
    if problem:
        raise LedgerError(f"ledger row: {problem}")
    return "\t".join(row[name] for name in COLUMNS)


def spent(rows: list, episode: str | None = None) -> dict:
    """What was spent, of one episode or of the season (the whole file), counting each hash once.

    A hash with ``run`` rows counts their amounts, the real cost; any other counts its PASS rows.
    """
    total = {"usd": Decimal(0), "compute_min": Decimal(0)}
    ran = {row["hash"] for row in rows if row["kind"] == "run"}
    for row in rows:
        counts = row["kind"] == "run" or (row["verdict"] == "PASS" and row["hash"] not in ran)
        if counts and (episode is None or row["episode"] == episode):
            total["usd"] += Decimal(row["usd"])
            total["compute_min"] += Decimal(row["compute_min"])
    return total


def append(p, gh, root, row: dict) -> str:
    """Append ``row`` to the ledger, commit it on the base as ``factory`` and push. Returns the line."""
    base = production.base(p)
    try:
        branch = gitstate.git(root, "symbolic-ref", "--short", "HEAD").strip()
    except gitstate.GitError:
        branch = None
    if branch != base or gitstate.git(root, "diff", "--cached", "--name-only").strip():
        raise LedgerError(f"the checkout is not on {base} with a clean index")
    new = line(row)
    path = Path(root) / PATH
    current = path.read_text(encoding="utf-8") if path.is_file() else HEADER + "\n"
    content = current + new + "\n"
    committed = gitstate.show(root, "HEAD", PATH) or ""
    if not current.startswith(committed):
        raise LedgerError(f"{PATH} differs from the base")
    read(content)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    gitstate.commit(root, [PATH], f"ledger: {row['kind']} {row['hash'][:12]} {row['verdict']}", "factory")
    gh.push(root, base)
    return new


def _hours(created, now: str) -> Decimal | None:
    if not created:
        return None
    seconds = (datetime.strptime(now, production.TIME_FORMAT)
               - datetime.strptime(min(created), production.TIME_FORMAT)).total_seconds()
    return Decimal(int(seconds)) / 3600


def decide(p, rows, state, ticket, kind, usd, compute_min, now) -> tuple:
    """``("PASS", "-", "within caps")`` or ``("STOP", <cap>, <reason>)`` for the first cap that applies."""
    caps = {name: None if value is None else Decimal(str(value)) for name, value in production.caps(p).items()}
    episode = tickets.episode(ticket["unit"])
    cost = {"usd": usd, "compute_min": compute_min}
    if kind == "job":
        for name, what, _ in SPEND_CAPS:
            if caps[name] is None and cost[what] > 0:
                return "STOP", name, "cap not set"
    so_far = {True: spent(rows, episode), False: spent(rows)}
    for name, what, of_episode in SPEND_CAPS:
        if caps[name] is not None and so_far[of_episode][what] + cost[what] > caps[name]:
            return "STOP", name, "over cap"
    everyone = list(state.tickets.values())
    for name, group in (("CAP_EPISODE_HOURS", [t for t in everyone if tickets.episode(t["unit"]) == episode]),
                        ("CAP_SEASON_HOURS", everyone)):
        hours = _hours([t["created_at"] for t in group], now)
        if caps[name] is not None and hours is not None and hours > caps[name]:
            return "STOP", name, "over cap"
    if kind == "job" and caps["CAP_TICKET_ITERATIONS"] is not None:
        entry = production.stage(p, ticket["room"], ticket["stage"])
        launched = sum(tickets.launches(state, ticket["number"], role)
                       for role in production.stage_roles(entry, "maker"))
        if launched > caps["CAP_TICKET_ITERATIONS"]:
            return "STOP", "CAP_TICKET_ITERATIONS", "over cap"
    return "PASS", "-", "within caps"


def _order(comment):
    return (comment["created_at"], comment["id"])


def gate_marker(comment):
    """The comment's marker when it is a gate's: role ``program:gate`` and actor ``PRODUCER``; else None."""
    if comment.get("role") != GATE_ROLE:
        return None
    m = markers.parse(comment["body"])
    return m if m is not None and m.actor == GATE_ACTOR else None


def covers(pull_git: dict, comments: list, stage: dict) -> bool:
    """Whether the newest gate comment on the pull is a ``GATE PASS`` that still counts for its head."""
    ordered = sorted(comments, key=_order)
    gates = [c for c in ordered if gate_marker(c) is not None]
    if not gates:
        return False
    gate = gates[-1]
    if gate_marker(gate).verb != "GATE PASS":
        return False
    seen = markers.reviewed(gate["body"])
    if seen is None:
        return False
    sha, manifest = seen
    if manifest != pull_git["manifest"]:
        return False
    if not markers.only_base_merges(pull_git["commits"], sha, pull_git["head"], pull_git["base_shas"]):
        return False
    makers = production.stage_roles(stage, "maker")
    for c in ordered:
        m = markers.parse(c["body"])
        if m and m.verb in ("READY", "ADDRESSED") and m.actor.lower() in makers and _order(c) >= _order(gate):
            return False
    return True
