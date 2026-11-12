"""The tickets ``factory tick`` files next: a locked ticket's next stage in the pipeline (system brief section 12).

A pure reading of a ``tickets.State``: nothing here writes to GitHub or git. ``due`` gives one entry per
ticket to file, ``{"from", "pull", "room", "stage", "unit", "title", "body", "labels"}``, sorted by the
locked ticket's number, the pipeline order of the stage, then the unit id.

For every ticket with a merged pull, every stage of its room whose ``after`` is the ticket's stage is
filed; a stage the format of the unit's episode skips is replaced by the stages after it. A stage with
the same unit type is filed for the same unit. A ``sequence`` stage after an ``episode`` stage is filed
once per ``episodes/<unit>/sequences/<Q>/sequence.json`` in the tree of the base, as unit ``<unit>/<Q>``.
An ``episode`` stage after a ``sequence`` stage is filed once for the episode, only when every ``<Q>`` of
the episode on the base has a locked ticket of the sequence stage with unit ``<episode>/<Q>``, and only
from the lowest numbered of those tickets (the lowest numbered locked ticket of each ``<Q>``); its body
has one ``Upstream:`` line per sequence. Any other pair files nothing. A room, stage and unit that some
ticket already has, open or closed, is never filed again. While a tracked issue does not parse, nothing
is filed.
"""

from __future__ import annotations

import re

from kit.lib import production, tickets

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


def _boards(p, root, state: tickets.State, ticket: dict) -> list:
    """``(ticket, pull)`` of the lowest numbered locked ticket of the ticket's stage for each sequence of its
    episode on the base, by unit id; empty when one is missing or ``ticket`` is not the lowest numbered."""
    room, stage, episode = ticket["room"], ticket["stage"], tickets.episode(ticket["unit"])
    found = []
    for q in tickets.sequences(p, root, episode):
        unit = f"{episode}/{q}"
        number = next((n for n in sorted(state.tickets) if state.tickets[n]["room"] == room
                       and state.tickets[n]["stage"] == stage and state.tickets[n]["unit"] == unit
                       and tickets.locked_pull(state, n) is not None), None)
        if number is None:
            return []
        found.append((state.tickets[number], tickets.locked_pull(state, number)))
    if not found or min(t["number"] for t, _ in found) != ticket["number"]:
        return []
    return found


def _units(p, root, state: tickets.State, ticket: dict, pull: dict, upstream_unit: str, entry: dict) -> list:
    """``(unit, upstream (ticket, pull) pairs)`` of each ticket of ``entry`` to file from ``ticket``."""
    if entry["unit"] == upstream_unit:
        return [(ticket["unit"], [(ticket, pull)])]
    if upstream_unit == "episode" and entry["unit"] == "sequence":
        return [(f"{ticket['unit']}/{q}", [(ticket, pull)]) for q in tickets.sequences(p, root, ticket["unit"])]
    if upstream_unit == "sequence" and entry["unit"] == "episode":
        upstreams = _boards(p, root, state, ticket)
        return [(tickets.episode(ticket["unit"]), upstreams)] if upstreams else []
    return []


def _filing(ticket: dict, pull: dict, upstreams: list, room: str, stage: str, unit: str) -> dict:
    lines = "".join(f"Upstream: #{t['number']} {t['stage']} {t['unit']}, locked by PR #{u['number']}.\n"
                    for t, u in upstreams)
    body = f"Do the stage {stage} for unit {unit}.\n\n{lines}Filed by factory tick.\n"
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
            for unit, upstreams in _units(p, root, state, ticket, pull, upstream_unit, entry):
                if (room, entry["stage"], unit) not in taken:
                    taken.add((room, entry["stage"], unit))
                    candidates.append(_filing(ticket, pull, upstreams, room, entry["stage"], unit))
        found += sorted(candidates, key=lambda f: (order[(f["room"], f["stage"])], f["unit"]))
    return found
