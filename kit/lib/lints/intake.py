"""Intake: an episode's shot list equals its locked edit list frame for frame.

A shot list is `production/<ep>/shots.json`, a list of `{shot, first, last}`, one row per event of
`episodes/<ep>/reel/edit-list.json`, in order: `first` is `start × fps` and `last` is
`first + duration × fps - 1`. Rules, every finding on line 1 of its JSON file:

1. depends: a shot list's edit list is among the files.
2. edit-list: the edit list is `{fps, events: [{shot, start, duration}]}`, every start and duration
   a whole number of frames at its fps.
3. shots: the shot list is a list of `{shot, first, last}`, one row per event, each row the event's shot.
4. frames: each row's `first` and `last` are the event's.

A tree without shot lists passes.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from ..lint import Finding, files, read_json

SHOTS = re.compile(r"^production/([^/]+)/shots\.json$")
RESERVED = {"assets"}
TOLERANCE = 1e-6


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _positive(value) -> bool:
    return _number(value) and value > 0


def _integer(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _whole(x: float) -> int | None:
    """round(x) when x is a whole number within the tolerance, else None."""
    if not math.isfinite(x) or abs(x - round(x)) > TOLERANCE:
        return None
    return round(x)


def _events(rel: str, data) -> tuple[list[tuple[str, int, int] | None] | None, list[Finding]]:
    """The edit list's events as (shot, first, last), None for an event with a finding."""
    if not isinstance(data, dict) or not _positive(data.get("fps")) or not isinstance(data.get("events"), list):
        return None, [Finding(rel, 1, "edit-list", "not {fps, events: [...]} with fps above zero")]
    fps, events, found = data["fps"], [], []
    for i, e in enumerate(data["events"], 1):
        if not isinstance(e, dict) or not isinstance(e.get("shot"), str) or not _number(e.get("start")) \
                or not _positive(e.get("duration")):
            found.append(Finding(rel, 1, "edit-list",
                                 f"event {i} is not {{shot, start, duration}} with a duration above zero"))
            events.append(None)
            continue
        first, frames = _whole(e["start"] * fps), _whole(e["duration"] * fps)
        if first is None:
            found.append(Finding(rel, 1, "edit-list",
                                 f"event {i}: start {e['start']:g} s at {fps:g} fps is not a whole frame"))
        if frames is None:
            found.append(Finding(rel, 1, "edit-list", f"event {i}: duration {e['duration']:g} s at {fps:g} fps "
                                                      "is not a whole number of frames"))
        events.append(None if first is None or frames is None else (e["shot"], first, first + frames - 1))
    return events, found


def _compare(rel: str, edit_rel: str, rows, events) -> list[Finding]:
    if not isinstance(rows, list):
        return [Finding(rel, 1, "shots", "not a list of {shot, first, last}")]
    found = []
    if len(rows) != len(events):
        found.append(Finding(rel, 1, "shots", f"{len(rows)} rows for {len(events)} events of {edit_rel}"))
    for i, (row, event) in enumerate(zip(rows, events), 1):
        if not isinstance(row, dict) or not isinstance(row.get("shot"), str) \
                or not _integer(row.get("first")) or not _integer(row.get("last")):
            found.append(Finding(rel, 1, "shots",
                                 f"row {i} is not {{shot, first, last}} with a string and two whole numbers"))
            continue
        if event is None:
            continue
        shot, first, last = event
        if row["shot"] != shot:
            found.append(Finding(rel, 1, "shots",
                                 f"row {i}: shot {row['shot']}, event {i} of the edit list is shot {shot}"))
        if (row["first"], row["last"]) != (first, last):
            found.append(Finding(rel, 1, "frames", f"row {i} ({row['shot']}): frames {row['first']}-{row['last']}, "
                                                   f"the edit list gives {first}-{last}"))
    return found


def check(root: Path) -> list[Finding]:
    rels = {p.as_posix() for p in files(root)}
    found = []
    for rel in sorted(rels):
        m = SHOTS.match(rel)
        if not m or m.group(1) in RESERVED:
            continue
        edit_rel = f"episodes/{m.group(1)}/reel/edit-list.json"
        if edit_rel not in rels:
            found.append(Finding(rel, 1, "depends", f"{edit_rel} is missing"))
            continue
        events, edit_found = _events(edit_rel, read_json(root, edit_rel)[1])
        found += edit_found
        rows = read_json(root, rel)[1]
        if events is None:
            if not isinstance(rows, list):
                found += _compare(rel, edit_rel, rows, [])
            continue
        found += _compare(rel, edit_rel, rows, events)
    return found
