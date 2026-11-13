"""Rigging: range of motion, limits, interpenetration and rig hash of an asset's rig (brief sections 6 and 16).

Reads, of the files git would commit, only each `production/assets/<asset>/rigging/state.json`, in
sorted order, and for each state with no `shape` finding its `production/assets/<asset>/rigging/manifest.json`.
Of a state it reads `asset`, `rig_sha256`, `joints` (each `{name, min, max}`, `[x, y, z]` degrees) and
`rom` (`{poses, limit_violations, penetration_mm}`); of a manifest only `outputs`, and of each output
only `sha256`. Every other key is ignored.

Rule ids: shape (1), range-of-motion (2), limits (3), interpenetration (4), rig-hash (5). Findings are
on line 1 of the state, except a manifest whose `outputs` is not a list of outputs, which is on line 1
of the manifest. A state with a `shape` finding gets none of rules 2 to 5. At least one pose is tested,
no joint-limit violation is found, no joint's `min` is above its `max` on an axis, and the worst
penetration is at most `MAX_PENETRATION_MM`. The rig hash is the `sha256` of an output the rig job's
manifest records.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from ..lint import Finding, files, read_json

MAX_PENETRATION_MM = 5
STATE = re.compile(r"^production/assets/([^/]+)/rigging/state\.json$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
KEYS = ("asset", "rig_sha256", "joints", "rom")
ROM_KEYS = ("poses", "limit_violations", "penetration_mm")
AXES = ("x", "y", "z")


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _vector(value) -> bool:
    return isinstance(value, list) and len(value) == 3 and all(_number(v) for v in value)


def _joint(value) -> bool:
    return (isinstance(value, dict) and isinstance(value.get("name"), str)
            and _vector(value.get("min")) and _vector(value.get("max")))


def _shape(asset: str, data) -> list[str]:
    """The `shape` messages of one state."""
    if not isinstance(data, dict):
        return ["not {asset, rig_sha256, joints, rom}"]
    found = [f"{key} is missing" for key in KEYS if key not in data]
    if "asset" in data:
        if not isinstance(data["asset"], str):
            found.append("asset is not a string")
        elif data["asset"] != asset:
            found.append(f"asset is not '{asset}'")
    if "rig_sha256" in data and not (isinstance(data["rig_sha256"], str) and SHA256.match(data["rig_sha256"])):
        found.append("rig_sha256 is not 64 lower-case hex characters")
    if "joints" in data and not (isinstance(data["joints"], list) and all(_joint(j) for j in data["joints"])):
        found.append("joints is not a list of {name, min, max}, each [x, y, z] degrees")
    if "rom" in data:
        rom = data["rom"]
        if not isinstance(rom, dict):
            found.append("rom is not {poses, limit_violations, penetration_mm}")
        else:
            found += [f"rom.{key} is missing" for key in ROM_KEYS if key not in rom]
            for key in ("poses", "limit_violations"):
                if key in rom and not _whole(rom[key]):
                    found.append(f"rom.{key} is not a whole number")
            pen = rom.get("penetration_mm")
            if "penetration_mm" in rom and not (_number(pen) and pen >= 0):
                found.append("rom.penetration_mm is not a number at or above 0")
    return found


def _values(rel: str, data: dict) -> list[Finding]:
    """The `range-of-motion`, `limits` and `interpenetration` findings of a state of the right shape."""
    rom, joints = data["rom"], data["joints"]
    found = []
    if rom["poses"] == 0:
        found.append(Finding(rel, 1, "range-of-motion", "rom.poses is 0: no pose was tested"))
    if rom["limit_violations"] > 0:
        found.append(Finding(rel, 1, "limits", f"rom.limit_violations is {rom['limit_violations']}, not 0"))
    if not joints:
        found.append(Finding(rel, 1, "limits", "joints is empty"))
    names = [j["name"] for j in joints]
    for name in sorted({n for n in names if names.count(n) > 1}):
        found.append(Finding(rel, 1, "limits", f"joint name {name} repeats"))
    for j in joints:
        axes = [axis for axis, lo, hi in zip(AXES, j["min"], j["max"]) if lo > hi]
        if axes:
            found.append(Finding(rel, 1, "limits", f"joint {j['name']}: min is above max on {', '.join(axes)}"))
    if rom["penetration_mm"] > MAX_PENETRATION_MM:
        found.append(Finding(rel, 1, "interpenetration",
                             f"penetration {format(rom['penetration_mm'], 'g')} mm is over {MAX_PENETRATION_MM} mm"))
    return found


def _rig_hash(root: Path, rel: str, manifest: str, present: set[str], rig_sha256: str) -> list[Finding]:
    if manifest not in present:
        return [Finding(rel, 1, "rig-hash", f"{manifest} is missing")]
    _, data = read_json(root, manifest)
    outputs = data.get("outputs") if isinstance(data, dict) else None
    if not (isinstance(outputs, list)
            and all(isinstance(o, dict) and isinstance(o.get("sha256"), str) for o in outputs)):
        return [Finding(manifest, 1, "rig-hash", "outputs is not a list of {name, sha256, bytes}")]
    if rig_sha256 not in {o["sha256"] for o in outputs}:
        return [Finding(rel, 1, "rig-hash", f"rig_sha256 is not the sha256 of an output of {manifest}")]
    return []


def check(root: Path) -> list[Finding]:
    rels = [p.as_posix() for p in files(root)]
    present = set(rels)
    found = []
    for rel in rels:
        if not (m := STATE.match(rel)):
            continue
        asset = m.group(1)
        _, data = read_json(root, rel)
        shape = _shape(asset, data)
        if shape:
            found += [Finding(rel, 1, "shape", message) for message in shape]
            continue
        found += _values(rel, data)
        found += _rig_hash(root, rel, f"production/assets/{asset}/rigging/manifest.json", present,
                           data["rig_sha256"])
    return found
