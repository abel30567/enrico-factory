"""Animation: foot slide, pops, interpenetration and lip sync within one frame (brief sections 6 and 16).

Reads, of the files git would commit, only each shot's `production/<ep>/<shot>/animation/state.json`, in
sorted order. Of a state it reads `shot`, `stage`, `motion` and `lipsync`. Every other key is ignored.

Rule ids: state (1), motion (2), lipsync (3). Findings are on line 1. An entry with a `state` finding is
not checked for its values; every other entry is. Limits are inclusive and compared on the value as read.
Penetration, float and joint-limit violations are from `docs/research/04-3d-pipeline.md` section 5
("penetration and float at or under 5 mm, zero joint-limit violations"); the lip sync offset from brief
section 6 ("within one frame"). `MAX_FOOT_SLIDE_MM` and `MAX_STEP_DEG` are placeholders until #118
measures them on the golden levels.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from ..lint import Finding, files, read_json

MAX_LIMIT_VIOLATIONS = 0
MAX_PENETRATION_MM = 5
MAX_FLOAT_MM = 5
MAX_FOOT_SLIDE_MM = 5
MAX_STEP_DEG = 30
MAX_OFFSET_FRAMES = 1
STATE = re.compile(r"^production/([^/]+)/([^/]+)/animation/state\.json$")
MEASURES = ("foot_slide_mm", "penetration_mm", "float_mm", "max_step_deg")
MOTION_SHAPE = "is not {actor, foot_slide_mm, penetration_mm, float_mm, limit_violations, max_step_deg}"
LIPSYNC_SHAPE = "is not {line, actor, offset_frames}"
LIMITS = (("limit_violations", MAX_LIMIT_VIOLATIONS), ("penetration_mm", MAX_PENETRATION_MM),
          ("float_mm", MAX_FLOAT_MM), ("foot_slide_mm", MAX_FOOT_SLIDE_MM), ("max_step_deg", MAX_STEP_DEG))


def _integer(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _measure(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def _motion(value) -> bool:
    return (isinstance(value, dict) and isinstance(value.get("actor"), str)
            and all(_measure(value.get(k)) for k in MEASURES)
            and _integer(value.get("limit_violations")) and value["limit_violations"] >= 0)


def _lipsync(value) -> bool:
    return (isinstance(value, dict) and isinstance(value.get("line"), str)
            and isinstance(value.get("actor"), str) and _integer(value.get("offset_frames")))


def _state(rel: str, shot: str, data) -> list[Finding]:
    def finding(rule, message):
        return Finding(rel, 1, rule, message)

    if not isinstance(data, dict):
        return [finding("state", "not a JSON object")]
    found = []
    if data.get("shot") != shot:
        found.append(finding("state", f"shot is not '{shot}'"))
    if data.get("stage") != "animation":
        found.append(finding("state", "stage is not 'animation'"))
    motion, lipsync = data.get("motion"), data.get("lipsync")
    if not isinstance(motion, list):
        found.append(finding("state", "motion is not a list"))
    else:
        actors = set()
        for i, entry in enumerate(motion):
            if not _motion(entry):
                found.append(finding("state", f"motion[{i}] {MOTION_SHAPE}"))
                continue
            if entry["actor"] in actors:
                found.append(finding("state", f"actor {entry['actor']} is listed twice in motion"))
                continue
            actors.add(entry["actor"])
            for key, limit in LIMITS:
                if entry[key] > limit:
                    found.append(finding("motion",
                                         f"actor {entry['actor']}: {key} {entry[key]} (at most {limit})"))
    if not isinstance(lipsync, list):
        found.append(finding("state", "lipsync is not a list"))
    else:
        lines = set()
        for i, entry in enumerate(lipsync):
            if not _lipsync(entry):
                found.append(finding("state", f"lipsync[{i}] {LIPSYNC_SHAPE}"))
                continue
            if entry["line"] in lines:
                found.append(finding("state", f"line {entry['line']} is listed twice in lipsync"))
                continue
            lines.add(entry["line"])
            if abs(entry["offset_frames"]) > MAX_OFFSET_FRAMES:
                found.append(finding("lipsync", f"line {entry['line']} (actor {entry['actor']}):"
                                                f" offset_frames {entry['offset_frames']}"
                                                f" (from -{MAX_OFFSET_FRAMES} to {MAX_OFFSET_FRAMES})"))
    return found


def check(root: Path) -> list[Finding]:
    found = []
    for rel in (p.as_posix() for p in files(root)):
        if m := STATE.match(rel):
            _, data = read_json(root, rel)
            found += _state(rel, m.group(2), data)
    return found
