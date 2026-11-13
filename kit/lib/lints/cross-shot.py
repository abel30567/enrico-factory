"""Cross-shot: the world state at the end of shot N equals the start of shot N+1 (brief sections 6 and 16).

Reads, of the files git would commit, only `production/<ep>/shots.json` and each shot's
`production/<ep>/<shot>/lighting/state.json`, episode by episode in sorted order. Of `shots.json` it
reads the `shot` of each row; of a state, `shot`, `stage` and `actors`, and of each actor `id`,
`sha256`, `start` and `end`. Every other key is ignored.

Rule ids: shots (1), state (2), cut (3). Findings are on line 1. A cut is two rows next to each other
in `shots.json` whose ids `<sequence>-<n>` have the same sequence and whose states both have no
finding; the last shot of one sequence and the first of the next are not compared, and a shot with no
state is not compared. At a cut, each actor in both shots keeps its `sha256`, and its `start` in shot
N+1 is within `MAX_MM` millimetres and `MAX_DEG` degrees of its `end` in shot N. Both limits are
placeholders until #118 measures them on the golden levels.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from ..lint import Finding, files, read_json

MAX_MM = 10
MAX_DEG = 1
SHOTS = re.compile(r"^production/([^/]+)/shots\.json$")
STATE = re.compile(r"^production/([^/]+)/([^/]+)/lighting/state\.json$")
SHOT_ID = re.compile(r"^(.+)-[0-9]+$")
ACTOR_SHAPE = "is not {id, sha256, start, end} with transforms {location, rotation}"


def _vector(value) -> bool:
    return (isinstance(value, list) and len(value) == 3
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                    for v in value))


def _transform(value) -> bool:
    return isinstance(value, dict) and _vector(value.get("location")) and _vector(value.get("rotation"))


def _actor(value) -> bool:
    return (isinstance(value, dict) and isinstance(value.get("id"), str)
            and isinstance(value.get("sha256"), str)
            and _transform(value.get("start")) and _transform(value.get("end")))


def _state(rel: str, shot: str, data) -> tuple[list[Finding], dict | None]:
    """The `state` findings of one state, and its actors by id when it has none."""
    def finding(message):
        return Finding(rel, 1, "state", message)

    if not isinstance(data, dict):
        return [finding("not a JSON object")], None
    found = []
    if data.get("shot") != shot:
        found.append(finding(f"shot is not '{shot}'"))
    if data.get("stage") != "lighting":
        found.append(finding("stage is not 'lighting'"))
    actors = data.get("actors")
    by_id = {}
    if not isinstance(actors, list):
        found.append(finding("actors is not a list"))
    else:
        for i, actor in enumerate(actors):
            if not _actor(actor):
                found.append(finding(f"actors[{i}] {ACTOR_SHAPE}"))
            elif actor["id"] in by_id:
                found.append(finding(f"actor {actor['id']} is listed twice"))
            else:
                by_id[actor["id"]] = actor
    return found, None if found else by_id


def _cut(rel: str, before: str, a: dict, b: dict) -> list[Finding]:
    """The `cut` findings of shot N+1 (actors `b`, state `rel`) against shot N (`before`, actors `a`)."""
    found = []
    for aid in sorted(a.keys() & b.keys()):
        end, start = a[aid]["end"], b[aid]["start"]
        if a[aid]["sha256"] != b[aid]["sha256"]:
            found.append(Finding(rel, 1, "cut", f"actor {aid}: sha256 differs from shot {before}"))
        mm = math.dist(end["location"], start["location"]) * 1000
        if round(mm, 3) > MAX_MM:
            found.append(Finding(rel, 1, "cut",
                                 f"actor {aid}: starts {mm:.1f} mm from its end in {before} (at most {MAX_MM})"))
        deg = max(abs((y - x + 180) % 360 - 180) for x, y in zip(end["rotation"], start["rotation"]))
        if round(deg, 3) > MAX_DEG:
            found.append(Finding(rel, 1, "cut", f"actor {aid}: starts turned {deg:.1f} degrees from its end"
                                                f" in {before} (at most {MAX_DEG})"))
    return found


def _episode(root: Path, ep: str, has_shots: bool, states: dict[str, str]) -> list[Finding]:
    shots_rel = f"production/{ep}/shots.json"
    found, rows = [], []
    if has_shots:
        _, data = read_json(root, shots_rel)
        if not (isinstance(data, list)
                and all(isinstance(r, dict) and isinstance(r.get("shot"), str) for r in data)):
            return [Finding(shots_rel, 1, "shots", "not a list of {shot, first, last}")]
        rows = [r["shot"] for r in data]
        for shot in rows:
            if not SHOT_ID.match(shot):
                found.append(Finding(shots_rel, 1, "shots", f"shot '{shot}' is not <sequence>-<n>"))
    actors = {}
    for shot, rel in sorted(states.items()):
        if shot not in rows:
            found.append(Finding(rel, 1, "shots", f"shot {shot} is not a row of {shots_rel}"))
        _, data = read_json(root, rel)
        state_found, by_id = _state(rel, shot, data)
        found += state_found
        if by_id is not None:
            actors[shot] = by_id
    for before, after in zip(rows, rows[1:]):
        m, n = SHOT_ID.match(before), SHOT_ID.match(after)
        if m and n and m.group(1) == n.group(1) and before in actors and after in actors:
            found += _cut(states[after], before, actors[before], actors[after])
    return found


def check(root: Path) -> list[Finding]:
    shots, states = set(), {}
    for rel in (p.as_posix() for p in files(root)):
        if m := SHOTS.match(rel):
            shots.add(m.group(1))
        elif m := STATE.match(rel):
            states.setdefault(m.group(1), {})[m.group(2)] = rel
    found = []
    for ep in sorted(shots | states.keys()):
        found += _episode(root, ep, ep in shots, states.get(ep, {}))
    return found
