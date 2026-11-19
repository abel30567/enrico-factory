"""Rigging: range of motion, limits, interpenetration and rig hash of an asset's rig (brief sections 6 and 16).

Reads, of the files git would commit, only each `production/assets/<asset>/rigging/state.json`,
`production/assets/<asset>/rigging/manifest.json` and `production/assets/<asset>/rigging/requests/<name>.json`
(one level, `.json` only), in sorted order, for each state with no `shape` finding
its manifest, and for each manifest the asset's `production/assets/<asset>/assets/state.json` where there is
one. Of a rigging state it reads `asset`, `rig_sha256`, `joints` (each `{name, min, max}`, `[x, y, z]`
degrees) and `rom` (`{poses, limit_violations, penetration_mm}`); of a manifest only `outputs`, of each
output only `sha256`, and `skeleton`; of an asset state only `kind`; of a request `for`, `prompt` and `refs`.
Every other key is ignored.

Rule ids: shape (1), range-of-motion (2), limits (3), interpenetration (4), rig-hash (5), skeleton (6),
depends (7), request (8).
Findings are on line 1 of the state, except a manifest whose `outputs` is not a list of outputs and every
`skeleton` finding, which are on line 1 of the manifest. The manifest of a character (an asset state that is
an object whose `kind` is `character`) has a `skeleton`, `{bones, weights_normalised}` with one root, unique
bone names and normalised weights; it needs no rigging state. A state with a `shape` finding gets none of
rules 2 to 5. At least one pose is tested, no joint-limit violation is found, no joint's `min` is above its
`max` on an axis, and the worst penetration is at most `MAX_PENETRATION_MM`. The rig hash is the `sha256` of
an output the rig job's manifest records. Rule 7 holds each `rigging/scene.py` to `JOB_FILES`
(`lint.job_files`), on line 1 of the script: the state only, since rule 5 already names a missing manifest.
Rule 8 holds each request to what the rig vendor takes (`vendor.request`, `vendor.model`), on line 1 of the
request: a JSON object of exactly `for`, `prompt` and `refs` with `refs` a list (else that is its only
finding), `for` the asset of its path, `prompt` a non-empty string and `refs` exactly `["asset:<asset>"]`.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from .. import vendor
from ..lint import Finding, files, job_files, read_json
from . import assets

MAX_PENETRATION_MM = 5
STATE = re.compile(r"^production/assets/([^/]+)/rigging/state\.json$")
MANIFEST = re.compile(r"^production/assets/([^/]+)/rigging/manifest\.json$")
SCENE = re.compile(r"^production/assets/[^/]+/rigging/scene\.py$")
REQUEST = re.compile(r"^production/assets/([^/]+)/rigging/requests/[^/]+\.json$")
JOB_FILES = ("state.json",)
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


def _skeleton(root: Path, rel: str, asset_state: str, present: set[str]) -> list[Finding]:
    """The `skeleton` findings of one rigging manifest: none unless the asset is a character."""
    if asset_state not in present:
        return []
    _, state = read_json(root, asset_state)
    if not (isinstance(state, dict) and state.get("kind") == "character"):
        return []
    _, data = read_json(root, rel)
    skeleton = data.get("skeleton") if isinstance(data, dict) else None
    if skeleton is None:
        messages = ["a character has no skeleton"]
    elif not assets._skeleton(skeleton):
        messages = ["skeleton is not null or {bones, weights_normalised}"]
    else:
        messages = assets.skeleton_problems(skeleton)
    return [Finding(rel, 1, "skeleton", message) for message in messages]


def _request(root: Path, rel: str, asset: str) -> list[Finding]:
    """The `request` findings of one rigging request of ``asset``."""
    _, data = read_json(root, rel)
    if not isinstance(data, dict) or sorted(data) != vendor.REQUEST_KEYS or not isinstance(data["refs"], list):
        return [Finding(rel, 1, "request", vendor.NOT_A_REQUEST)]
    found = []
    if data["for"] != asset:
        named = data["for"] if isinstance(data["for"], str) else json.dumps(data["for"])
        found.append(f"request for {named} is not for asset {asset}")
    if not (isinstance(data["prompt"], str) and data["prompt"]):
        found.append("request has no prompt")
    if data["refs"] != [vendor.ASSET_PREFIX + asset]:
        found.append(f'refs is not ["{vendor.ASSET_PREFIX}{asset}"]')
    return [Finding(rel, 1, "request", message) for message in found]


def check(root: Path) -> list[Finding]:
    rels = [p.as_posix() for p in files(root)]
    present = set(rels)
    found = []
    for rel in rels:
        if SCENE.match(rel):
            found += job_files(present, rel, JOB_FILES)
        if m := REQUEST.match(rel):
            found += _request(root, rel, m.group(1))
        if m := MANIFEST.match(rel):
            found += _skeleton(root, rel, f"production/assets/{m.group(1)}/assets/state.json", present)
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
