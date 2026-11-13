"""Simulation: a shot's simulation ran twice from the same inputs gives the same caches, and the locked
run's caches stay in bounds (brief sections 6 and 16).

Rule ids: depends (1), state (2), hashes (3), bounds (4). Findings in JSON files are on line 1. A run
file is `production/<ep>/<shot>/simulation/state.json` (the locked run) or `rerun.json` (a second run of
the same job); `production/assets/` holds assets, not shots, and is not read. Caches are matched by
`id`; only `state.json` is held to the bounds.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from ..lint import Finding, files, read_json

RUN_FILE = re.compile(r"^production/([^/]+)/([^/]+)/simulation/(state|rerun)\.json$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_SPEED_M_S = 30
ENERGY_GAIN = 1.1
GRAVITY_M_S2 = 9.81
GRAVITY_TOLERANCE = 0.1


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _cache_problem(n: int, cache, seen: set[str]) -> str | None:
    """The first `state` message that applies to entry `n` of `caches`, else None."""
    if not isinstance(cache, dict):
        return f"cache {n} is not an object"
    cid = cache.get("id")
    if not isinstance(cid, str) or not cid.strip():
        return f"cache {n} id is not a string"
    sha = cache.get("sha256")
    if not isinstance(sha, str) or not SHA256.match(sha):
        return f"cache {n} sha256 is not 64 lowercase hex digits"
    if not _number(cache.get("max_speed_m_s")):
        return f"cache {n} max_speed_m_s is not a number"
    energy = cache.get("energy_j")
    if not (isinstance(energy, dict) and _number(energy.get("first")) and _number(energy.get("last"))):
        return f"cache {n} energy_j is not {{first, last}} with two numbers"
    gravity = cache.get("gravity_m_s2")
    if "gravity_m_s2" not in cache or not (gravity is None or _number(gravity)):
        return f"cache {n} gravity_m_s2 is not a number or null"
    if cid in seen:
        return f"cache {n} id '{cid}' is repeated"
    seen.add(cid)
    return None


def _state(rel: str, shot: str, data) -> tuple[list[Finding], dict[str, dict]]:
    """Rule `state` on one run file: its findings, and its caches by id."""
    if not isinstance(data, dict):
        return [Finding(rel, 1, "state", "not a scene state object")], {}
    messages = []
    if data.get("shot") != shot:
        messages.append(f"shot is not '{shot}'")
    if data.get("stage") != "simulation":
        messages.append("stage is not 'simulation'")
    caches = data.get("caches")
    if not isinstance(caches, list):
        messages.append("caches is not a list")
        caches = []
    seen: set[str] = set()
    for n, cache in enumerate(caches, 1):
        problem = _cache_problem(n, cache, seen)
        if problem:
            messages.append(problem)
    found = [Finding(rel, 1, "state", m) for m in messages]
    return found, {} if found else {c["id"]: c for c in caches}


def _hashes(state_rel: str, state: dict, rerun_rel: str, rerun: dict) -> list[Finding]:
    found = []
    for cid in sorted(state):
        if cid not in rerun:
            found.append(Finding(state_rel, 1, "hashes", f"cache '{cid}' is not in {rerun_rel}"))
        elif state[cid]["sha256"] != rerun[cid]["sha256"]:
            found.append(Finding(state_rel, 1, "hashes", f"cache '{cid}': sha256 differs from {rerun_rel}"))
    for cid in sorted(set(rerun) - set(state)):
        found.append(Finding(rerun_rel, 1, "hashes", f"cache '{cid}' is not in {state_rel}"))
    return found


def _bounds(rel: str, caches: dict[str, dict]) -> list[Finding]:
    found = []
    for cid in sorted(caches):
        c = caches[cid]
        speed, g = c["max_speed_m_s"], c["gravity_m_s2"]
        first, last = c["energy_j"]["first"], c["energy_j"]["last"]
        if speed > MAX_SPEED_M_S:
            found.append(Finding(rel, 1, "bounds", f"cache '{cid}': max_speed_m_s {speed} is above 30"))
        if last > ENERGY_GAIN * first:
            found.append(Finding(rel, 1, "bounds",
                                 f"cache '{cid}': energy_j.last {last} is above 1.1 times first {first}"))
        if g is not None and abs(g - GRAVITY_M_S2) > GRAVITY_TOLERANCE * GRAVITY_M_S2:
            found.append(Finding(rel, 1, "bounds", f"cache '{cid}': gravity_m_s2 {g} is not within 10% of 9.81"))
    return found


def check(root: Path) -> list[Finding]:
    units: dict[tuple[str, str], set[str]] = {}
    for rel in files(root):
        m = RUN_FILE.match(rel.as_posix())
        if m and m.group(1) != "assets":
            units.setdefault((m.group(1), m.group(2)), set()).add(m.group(3))
    found = []
    for ep, shot in sorted(units):
        unit, have = f"production/{ep}/{shot}", units[(ep, shot)]
        rels = {run: f"{unit}/simulation/{run}.json" for run in ("state", "rerun")}
        caches = {}
        for run, other in (("state", "rerun"), ("rerun", "state")):
            if run not in have:
                continue
            if other not in have:
                found.append(Finding(rels[run], 1, "depends", f"{rels[other]} is missing"))
            findings, caches[run] = _state(rels[run], shot, read_json(root, rels[run])[1])
            found += findings
            if findings:
                caches[run] = None
        if caches.get("state") is not None and caches.get("rerun") is not None:
            found += _hashes(rels["state"], caches["state"], rels["rerun"], caches["rerun"])
        if caches.get("state") is not None:
            found += _bounds(rels["state"], caches["state"])
    return found
