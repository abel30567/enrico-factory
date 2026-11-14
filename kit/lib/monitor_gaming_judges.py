"""The gaming-the-judges monitor of the producer gate (system brief section 11).

It reads ``registry/verdicts.tsv`` on the base, one row per judge of a blind lock. An episode's round is its rows
of its latest lock at a stage whose judges include ``held-out-judge``. Over the last ``EPISODES`` rounds by lock
time it trips when each rose (more than half of the other judges ``new``) and stayed flat (``held-out-judge`` not
``new`` and the ``retell`` of ``audience-sim`` ``old`` or ``none``). The trip's issue freezes the run and the tick
files one retro ticket naming the judges to rotate. A window whose issue exists, open or closed, does not trip
again. A pure reading: it writes nothing and changes no step.
"""

from __future__ import annotations

from kit.lib import filing, monitors, production, tickets

NAME = "gaming-judges"
PATH = "registry/verdicts.tsv"
COLUMNS = ("locked_at", "episode", "stage", "pull", "role", "side", "retell")
HELD_OUT = "held-out-judge"
RETELL = "audience-sim"
EPISODES = 3
RETRO = "retro"
SIDES = ("new", "old", "none")
RETELLS = SIDES + ("-",)


def _read(text) -> list:
    """One dict per row of ``text``; raises MonitorError at the first line that does not parse."""
    rows = text.split("\n")
    if rows and rows[-1] == "":
        rows = rows[:-1]
    if not rows or rows[0] != "\t".join(COLUMNS):
        raise monitors.MonitorError(f"{PATH} line 1: the header is not {' '.join(COLUMNS)}")
    found = []
    for n, line in enumerate(rows[1:], start=2):
        cells = line.split("\t")
        if len(cells) != len(COLUMNS):
            raise monitors.MonitorError(f"{PATH} line {n}: {len(cells)} columns, not {len(COLUMNS)}")
        row = dict(zip(COLUMNS, cells))
        if row["side"] not in SIDES:
            raise monitors.MonitorError(f"{PATH} line {n}: side {row['side']} is not new, old or none")
        if row["retell"] not in RETELLS:
            raise monitors.MonitorError(f"{PATH} line {n}: retell {row['retell']} is not new, old, none or -")
        found.append(row)
    return found


def _rounds(p, rows) -> list:
    """``(episode, rows)`` of the last ``EPISODES`` rounds, ordered by ``(locked_at, pull, episode)``."""
    stages = {entry["stage"] for entry in p.pipeline if HELD_OUT in production.stage_roles(entry, "judge")}
    locks = {}  # episode -> (locked_at, pull) -> rows
    for row in rows:
        if row["stage"] in stages:
            locks.setdefault(row["episode"], {}).setdefault((row["locked_at"], row["pull"]), []).append(row)
    found = sorted((*max(by), episode, by[max(by)]) for episode, by in locks.items())
    return [(episode, kept) for _, _, episode, kept in found][-EPISODES:]


def _others(rows) -> list:
    return [row for row in rows if row["role"] != HELD_OUT]


def _rises(rows) -> bool:
    others = _others(rows)
    return sum(1 for row in others if row["side"] == "new") * 2 > len(others)


def _flat(rows) -> bool:
    if any(row["role"] == HELD_OUT and row["side"] == "new" for row in rows):
        return False
    retells = [row["retell"] for row in rows if row["role"] == RETELL]
    return bool(retells) and all(retell in ("old", "none") for retell in retells)


def trips(p, state, rows, now, read) -> list:
    text = read(PATH)
    if text is None:
        return []
    rounds = _rounds(p, _read(text))
    if len(rounds) < EPISODES or not all(_rises(kept) and _flat(kept) for _, kept in rounds):
        return []
    episodes = [episode for episode, _ in rounds]
    third = episodes[-1]
    title = f"[MONITOR] {NAME} {third}"
    if any(issue["title"] == title for issue in state.others):
        return []
    new = [{row["role"] for row in _others(kept) if row["side"] == "new"} for _, kept in rounds]
    judges = sorted(set.intersection(*new))
    named = ", ".join(judges) or "none"
    listed = ", ".join(episodes)
    entry = next((entry for entry in p.pipeline if entry["stage"] == RETRO), None)
    body = ["Monitor: gaming the judges", f"Episodes: {listed}", f"Judges to rotate: {named}",
            "Action: freeze the run, file a retro ticket (system brief section 11)."]
    if entry is None:
        body.append("No retro ticket: the pipeline has no retro stage.")
    body.append("To clear: run factory unfreeze. This window does not trip again.")
    filed = []
    if entry is not None:
        filed.append({"title": f"[{filing.key(RETRO, third)}] {RETRO} for {third}",
                      "labels": [tickets.TRACKED, f"room:{entry['room']}", f"stage:{RETRO}", f"unit:{third}"],
                      "body": monitors._body([
                          f"Do the stage {RETRO} for unit {third}.", "",
                          "Monitor: gaming the judges (system brief section 11)", f"Episodes: {listed}",
                          f"Judges to rotate: {named}",
                          "Propose a change to the rubric and prompt of each judge to rotate; the lab replays it "
                          "(system brief section 13).", "", "Filed by factory tick."])})
    return [{"line": f"monitor {NAME}: {listed} won without {HELD_OUT} or the retell; judges to rotate: {named}",
             "title": title, "body": monitors._body(body), "labels": [tickets.FREEZE], "tickets": filed,
             "episodes": episodes, "judges": judges}]


def instead(p, trip, ticket, entry):
    return entry
