"""The tickets ``factory tick`` files next: a locked ticket's next stage in the pipeline (system brief section 12).

A pure reading of a ``tickets.State``: nothing here writes to GitHub or git. ``due`` gives one entry per
ticket to file, ``{"from", "pull", "room", "stage", "unit", "title", "body", "labels"}``, sorted by the
locked ticket's number, the pipeline order of the stage, then the unit id.

For every ticket with a merged pull, every stage of its room whose ``after`` is the ticket's stage is
filed; a stage the format of the unit's episode skips is replaced by the stages after it. A stage with
the same unit type is filed for the same unit. A ``sequence`` stage after an ``episode`` stage is filed
once per ``episodes/<unit>/sequences/<Q>/sequence.json`` in the tree of the base, as unit ``<unit>/<Q>``.
Any other pair files nothing. A room, stage and unit that some ticket already has, open or closed, is
never filed again. While a tracked issue does not parse, nothing is filed.
"""

from __future__ import annotations

import re

from kit.lib import gitstate, production, tickets

_KEY = re.compile(r"[^A-Za-z0-9]+")


def key(stage: str, unit: str) -> str:
    """``<stage>-<unit>``, every run of characters outside letters and digits one ``-``, upper-cased."""
    return _KEY.sub("-", f"{stage}-{unit}").upper()


def _next(p, room: str, stage: str, skips: list) -> list:
    """The entries of ``room`` after ``stage`` in pipeline order, a skipped one replaced by those after it."""
    found = []
    for entry in production.stages(p, room):
        if entry["after"] != stage:
            continue
        if entry["stage"] in skips:
            found += _next(p, room, entry["stage"], skips)
        else:
            found.append(entry)
    return found


def _sequences(p, root, unit: str) -> list:
    """The directory names ``<Q>`` of ``episodes/<unit>/sequences/<Q>/sequence.json`` on the base."""
    prefix = f"episodes/{unit}/sequences/"
    sha = gitstate.head(root, production.base(p))
    out = gitstate.git(root, "--literal-pathspecs", "ls-tree", "-r", "-z", "--name-only", sha, "--", prefix)
    found = []
    for path in out.split("\0"):
        parts = path[len(prefix):].split("/") if path.startswith(prefix) else []
        if len(parts) == 2 and parts[0] and parts[1] == "sequence.json":
            found.append(parts[0])
    return found


def _units(p, root, ticket: dict, upstream_unit: str, entry: dict) -> list:
    if entry["unit"] == upstream_unit:
        return [ticket["unit"]]
    if upstream_unit == "episode" and entry["unit"] == "sequence":
        return [f"{ticket['unit']}/{q}" for q in _sequences(p, root, ticket["unit"])]
    return []


def _filing(ticket: dict, pull: dict, room: str, stage: str, unit: str) -> dict:
    body = (f"Do the stage {stage} for unit {unit}.\n\n"
            f"Upstream: #{ticket['number']} {ticket['stage']} {ticket['unit']}, locked by PR #{pull['number']}.\n"
            "Filed by factory tick.\n")
    return {"from": ticket["number"], "pull": pull["number"], "room": room, "stage": stage, "unit": unit,
            "title": f"[{key(stage, unit)}] {stage} for {unit}", "body": body,
            "labels": [tickets.TRACKED, f"room:{room}", f"stage:{stage}", f"unit:{unit}"]}


def due(p, state: tickets.State, root) -> list:
    """The tickets to file, in the order they are filed."""
    if state.errors:
        return []
    taken = {(t["room"], t["stage"], t["unit"]) for t in state.tickets.values()}
    order = {(entry["room"], entry["stage"]): i for i, entry in enumerate(p.pipeline)}
    found = []
    for number in sorted(state.tickets):
        ticket = state.tickets[number]
        pull = tickets.locked_pull(state, number)
        if pull is None:
            continue
        room, stage = ticket["room"], ticket["stage"]
        if not any(entry["after"] == stage for entry in production.stages(p, room)):
            continue
        upstream_unit = production.stage(p, room, stage)["unit"]
        candidates = []
        for entry in _next(p, room, stage, tickets.skips(p, root, ticket["unit"])):
            for unit in _units(p, root, ticket, upstream_unit, entry):
                if (room, entry["stage"], unit) not in taken:
                    taken.add((room, entry["stage"], unit))
                    candidates.append(_filing(ticket, pull, room, entry["stage"], unit))
        found += sorted(candidates, key=lambda f: (order[(f["room"], f["stage"])], f["unit"]))
    return found
