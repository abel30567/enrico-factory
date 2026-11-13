"""Render: each shot's `render/render.json` has the shot's frame count, no black, NaN or text frame, and
re-renders inside tolerance (brief sections 6 and 16).

Rule ids: shots (1), render (2), count (3), black (4), nan (5), text (6), rerender (7). Findings are on
line 1 of the render file. A render file is `production/<ep>/<shot>/render/render.json` (not under
`production/assets/`); its shot's row is the first `{shot, first, last}` of `production/<ep>/shots.json`
whose `shot` is the shot id. The lint reads only those two files: the job measures the frames.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from ..lint import Finding, files, read_json

RENDER = re.compile(r"^production/([^/]+)/([^/]+)/render/render\.json$")
# ColorVideoVDP, JOD; brief section 16 values table, until #130 measures the spread
RERENDER_MIN = 9.9


def _int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _frames(value) -> bool:
    return isinstance(value, list) and all(_int(v) for v in value)


def _score(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _text(value) -> bool:
    return isinstance(value, list) and all(
        isinstance(e, dict) and _int(e.get("frame")) and isinstance(e.get("text"), str) for e in value)


def _rerender(value) -> bool:
    return (isinstance(value, dict) and _frames(value.get("frames")) and bool(value["frames"])
            and isinstance(value.get("metric"), str) and bool(value["metric"].strip())
            and isinstance(value.get("scores"), list) and all(_score(s) for s in value["scores"])
            and len(value["scores"]) == len(value["frames"]))


def _row(shots, shot):
    """The shot's (first, last), or None when shots.json has no usable row for it."""
    if not isinstance(shots, list):
        return None
    for row in shots:
        if isinstance(row, dict) and row.get("shot") == shot:
            first, last = row.get("first"), row.get("last")
            return (first, last) if _int(first) and _int(last) and last >= first else None
    return None


def _check(root: Path, rel: str, ep: str, shot: str, present: set[str]) -> list[Finding]:
    out = []

    def add(rule, message):
        out.append(Finding(rel, 1, rule, message))

    _, data = read_json(root, rel)
    if not isinstance(data, dict):
        return [Finding(rel, 1, "render", "not an object {fps, frames, count, ...}")]

    shots_rel = f"production/{ep}/shots.json"
    row = None
    if shots_rel not in present:
        add("shots", f"{shots_rel} is missing")
    else:
        row = _row(read_json(root, shots_rel)[1], shot)
        if row is None:
            add("shots", f"shot {shot} has no row {{shot, first, last}} in {shots_rel}")

    count = data.get("count")
    if not _int(count):
        add("render", "count is missing or not an integer")
    elif row is not None and count != row[1] - row[0] + 1:
        add("count", f"count {count}, the shot has {row[1] - row[0] + 1} frames ({row[0]} to {row[1]})")

    for key, label in (("black", "black frames"), ("nan", "NaN or infinite frames")):
        value = data.get(key)
        if not _frames(value):
            add("render", f"{key} is missing or not a list of frames")
        elif value:
            add(key, f"{label}: " + ", ".join(str(f) for f in sorted(set(value))))

    text = data.get("text")
    if not _text(text):
        add("render", "text is missing or not a list of {frame, text}")
    else:
        for entry in text:
            add("text", f"engine text on frame {entry['frame']}: '{' '.join(entry['text'].split())}'")

    rerender = data.get("rerender")
    if not _rerender(rerender):
        add("render", "rerender is missing or not {frames, metric, scores}")
    else:
        for frame, score in zip(rerender["frames"], rerender["scores"]):
            if score < RERENDER_MIN:
                add("rerender", f"re-render of frame {frame} scores {score}, under {RERENDER_MIN}")
    return out


def check(root: Path) -> list[Finding]:
    rels = [p.as_posix() for p in files(root)]
    present = set(rels)
    findings = []
    for rel in rels:
        m = RENDER.match(rel)
        if m and m.group(1) != "assets":
            findings += _check(root, rel, m.group(1), m.group(2), present)
    return findings
