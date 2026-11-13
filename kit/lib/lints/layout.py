"""Layout: a shot's layout state against its shot file and its row of shots.json.

Reads every `production/<ep>/<shot>/layout/state.json`, `production/<ep>/shots.json` and the shot file
`episodes/<ep>/sequences/<Q>/shots/<shot>.json`. Every finding is on line 1 of the state file.

Rules:
1. `lens`: the camera's `focal_mm` is within FOCAL_MM.
2. `framing`: every character the shot file names has a `framing` box.
3. `safe-zone`: every character's box is inside SAFE_ZONE, the centred 9:16 crop of a 16:9 frame.
4. `line`: `line_flips` is empty.
5. `duration`: `frames` equals the shot's row of `shots.json`.
6. `state`: the state holds the keys these rules read, in their shapes.
7. `depends`: `shots.json`, its row for the shot and the shot file exist.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..lint import Finding, files, read_json

STATE = re.compile(r"^production/([^/]+)/([^/]+)/layout/state\.json$")
FOCAL_MM = (18, 135)  # placeholder until measured on the golden levels (#118)
SAFE_ZONE = (0.342, 0.658)  # the centred 9:16 crop of a 16:9 frame; placeholder until the platform rulebook holds one
KEYS = ("shot", "stage", "fps", "frames", "actors", "camera", "framing", "line_flips")
KINDS = ("character", "set", "prop")


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _frames(v) -> bool:
    return isinstance(v, dict) and _int(v.get("first")) and _int(v.get("last"))


def _actors(v) -> bool:
    return isinstance(v, list) and all(isinstance(a, dict) and isinstance(a.get("id"), str)
                                       and a.get("kind") in KINDS for a in v)


def _camera(v) -> bool:
    focal = v.get("focal_mm") if isinstance(v, dict) else None
    return isinstance(focal, dict) and _number(focal.get("min")) and _number(focal.get("max"))


def _framing(v) -> bool:
    return isinstance(v, list) and all(
        isinstance(e, dict) and _int(e.get("frame")) and isinstance(e.get("actor"), str)
        and isinstance(e.get("box"), list) and len(e["box"]) == 4 and all(_number(x) for x in e["box"])
        for e in v)


def _shape(state: dict, shot: str) -> tuple[set[str], list[str]]:
    """The keys that are valid, and one message per key that is missing or of another shape."""
    shapes = {
        "shot": (lambda v: v == shot, lambda v: f"shot is {v!r}, not the directory's {shot!r}"),
        "stage": (lambda v: v == "layout", lambda v: "stage is not 'layout'"),
        "fps": (lambda v: _int(v) and v > 0, lambda v: "fps is not a whole number above zero"),
        "frames": (_frames, lambda v: "frames is not {first, last} with whole numbers"),
        "actors": (_actors, lambda v: "actors is not a list of {id, kind, ...}"),
        "camera": (_camera, lambda v: "camera is not {focal_mm: {min, max}, ...} with numbers"),
        "framing": (_framing, lambda v: "framing is not a list of {frame, actor, box: [left, top, right, bottom]}"),
        "line_flips": (lambda v: isinstance(v, list) and all(_int(f) for f in v),
                       lambda v: "line_flips is not a list of frame numbers"),
    }
    valid, messages = set(), []
    for key in KEYS:
        if key not in state:
            messages.append(f"missing key {key!r}")
            continue
        ok, message = shapes[key]
        if ok(state[key]):
            valid.add(key)
        else:
            messages.append(message(state[key]))
    return valid, messages


def _characters(root: Path, ep: str, shot: str, paths: list[str]) -> tuple[list[str] | None, list[str]]:
    """The shot file's characters, unique and sorted (None if not a list of ids), and its depends message."""
    pattern = re.compile(rf"^episodes/{re.escape(ep)}/sequences/[^/]+/shots/{re.escape(shot)}\.json$")
    found = [p for p in paths if pattern.match(p)]
    if not found:
        return None, [f"episodes/{ep}/sequences/*/shots/{shot}.json is missing"]
    _, data = read_json(root, found[0])
    characters = data.get("characters") if isinstance(data, dict) else None
    if not isinstance(characters, list) or not all(isinstance(c, str) for c in characters):
        return None, []  # structure's finding
    return sorted(set(characters)), []


def _row(root: Path, ep: str, shot: str, paths: list[str]) -> tuple[dict | None, list[str]]:
    """The first row of shots.json for the shot, and its depends message."""
    rel = f"production/{ep}/shots.json"
    if rel not in paths:
        return None, [f"{rel} is missing"]
    _, rows = read_json(root, rel)
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("shot") == shot:
            return row, []
    return None, [f"{rel} has no row for {shot}"]


def _check_state(root: Path, rel: str, ep: str, shot: str, paths: list[str]) -> list[Finding]:
    _, state = read_json(root, rel)
    if not isinstance(state, dict):
        return [Finding(rel, 1, "state", "not a JSON object")]
    valid, messages = _shape(state, shot)
    found = [Finding(rel, 1, "state", m) for m in messages]
    characters, missing_shot = _characters(root, ep, shot, paths)
    row, missing_row = _row(root, ep, shot, paths)
    found += [Finding(rel, 1, "depends", m) for m in missing_row + missing_shot]

    if "camera" in valid:
        lo, hi = state["camera"]["focal_mm"]["min"], state["camera"]["focal_mm"]["max"]
        if lo < FOCAL_MM[0] or hi > FOCAL_MM[1]:
            found.append(Finding(rel, 1, "lens", f"focal length {lo:g}-{hi:g} mm is outside "
                                                 f"{FOCAL_MM[0]}-{FOCAL_MM[1]} mm"))

    if "framing" in valid and characters is not None:
        left, right = SAFE_ZONE
        for c in characters:
            boxes = [e for e in state["framing"] if e["actor"] == c]
            if not boxes:
                found.append(Finding(rel, 1, "framing", f"character {c} has no framing box"))
                continue
            outside = [e for e in boxes if e["box"][0] < left or e["box"][2] > right]
            if outside:
                first = min(e["frame"] for e in outside)
                found.append(Finding(rel, 1, "safe-zone",
                                     f"character {c} leaves the 9:16 safe zone (left {left}, right {right}) "
                                     f"on {len(outside)} of its {len(boxes)} boxes, first on frame {first}"))

    if "line_flips" in valid and state["line_flips"]:
        flips = ", ".join(str(f) for f in sorted(set(state["line_flips"])))
        found.append(Finding(rel, 1, "line", f"camera crosses the 180-degree line on frames: {flips}"))

    if "frames" in valid and row is not None:
        first, last = state["frames"]["first"], state["frames"]["last"]
        if (first, last) != (row.get("first"), row.get("last")):
            found.append(Finding(rel, 1, "duration", f"frames {first}-{last} do not match "
                                                     f"{row.get('first')}-{row.get('last')} "
                                                     f"in production/{ep}/shots.json"))
    return found


def check(root: Path) -> list[Finding]:
    paths = [p.as_posix() for p in files(root)]
    found = []
    for rel in paths:
        m = STATE.match(rel)
        if m:
            found += _check_state(root, rel, m.group(1), m.group(2), paths)
    return found
