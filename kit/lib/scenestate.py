"""The scene state `state.json` of a render job, made from the raw `scene.json` the build step wrote.

Assembled on the machine that runs `factory run` (docs/design/render-job.md, "The engine side of a job"),
so its rules are tested offline. `scene.json` is in centimetres and degrees; the state is in metres and
degrees, rounded so that two builds of one static scene give equal states: metres to 4 decimals, degrees
into (-180, 180] to 3, `scale`, `color` and `box` to 4, `focal_mm`, `sensor_mm` and `intensity` to 3,
`temperature_k` to 1; a rounded integral value is an integer. `sun` is the direction toward the one
directional light, `elevation_deg` above the horizon and `azimuth_deg` from +X toward +Y in [0, 360), or
null when the scene has none. A light with no temperature gets the correlated colour temperature of its
`color` (McCamy, on CIE xy). Nothing moves in this version, so each
transform's `start` equals its `end` and `motion`, `lipsync`, `caches` and `line_flips` are empty.
`framing` projects the 8 corners of each actor's bounds through the camera on every frame; an actor with a
corner behind the camera, or whose box is empty on the frame, has no row. An actor whose label is the id of
a job asset takes its `kind`, `asset` and `sha256` from that asset's locked state.
"""

from __future__ import annotations

import json
import math

from kit.lib.lints.assets import KINDS
from kit.lib.lints.lighting import RGB_TO_XYZ

LIGHT_KINDS = {"DirectionalLight": "directional", "PointLight": "point", "SpotLight": "spot",
               "RectLight": "rect"}
CVAR_GI = "r.DynamicGlobalIlluminationMethod"
CVAR_AA = "r.AntiAliasingMethod"


class SceneError(Exception):
    """The scene cannot become a state; the message says why."""


def _num(value, places: int):
    rounded = round(float(value), places)
    if rounded == int(rounded):
        return int(rounded)
    return rounded


def _degrees(value):
    angle = math.fmod(float(value), 360.0)
    if angle > 180:
        angle -= 360
    elif angle <= -180:
        angle += 360
    rounded = _num(angle, 3)
    return 180 if rounded <= -180 else rounded


def _metres(values) -> list:
    return [_num(v / 100, 4) for v in values]


def _transform(row: dict) -> dict:
    return {"location": _metres(row["location"]), "rotation": [_degrees(v) for v in row["rotation"]]}


def _axes(rotation) -> tuple:
    """The camera's forward, right and up axes by Unreal's rotation matrix, from `[roll, pitch, yaw]` degrees."""
    roll, pitch, yaw = (math.radians(v) for v in rotation)
    sr, cr, sp, cp, sy, cy = math.sin(roll), math.cos(roll), math.sin(pitch), math.cos(pitch), math.sin(yaw), math.cos(yaw)
    forward = (cp * cy, cp * sy, sp)
    right = (sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp)
    up = (-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp)
    return forward, right, up


def _box(bounds: dict, camera: dict):
    """`[left, top, right, bottom]` of the bounds as fractions of the frame, or None when not seen."""
    forward, right, up = _axes(camera["rotation"])
    focal = float(camera["focal_mm"])
    width, height = (float(v) for v in camera["sensor_mm"])
    xs, ys = [], []
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz in (-1, 1):
                corner = [o + s * e - c for o, s, e, c in
                          zip(bounds["origin"], (sx, sy, sz), bounds["extent"], camera["location"])]
                d = sum(a * b for a, b in zip(corner, forward))
                if d <= 0:
                    return None
                r = sum(a * b for a, b in zip(corner, right))
                u = sum(a * b for a, b in zip(corner, up))
                xs.append(min(1.0, max(0.0, 0.5 + focal * r / (width * d))))
                ys.append(min(1.0, max(0.0, 0.5 - focal * u / (height * d))))
    box = [min(xs), min(ys), max(xs), max(ys)]
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return [_num(v, 4) for v in box]


def _kinds(level) -> dict:
    if not level:
        return {}
    return {p["id"]: p["kind"] for p in level.get("primitives", [])}


def _light(row: dict) -> dict:
    label = row["label"]
    kind = LIGHT_KINDS.get(row["class"])
    if kind is None:
        raise SceneError(f"light {label} is a {row['class']}, not one of {', '.join(sorted(LIGHT_KINDS))}")
    if kind == "directional":
        unit = "lux"
    elif row.get("units") == "CANDELAS":
        unit = "cd"
    else:
        raise SceneError(f"light {label} intensity is in {row.get('units')}, not lux or cd")
    temperature = row.get("temperature_k")
    if not isinstance(temperature, (int, float)) or isinstance(temperature, bool):
        temperature = _temperature(label, row["color"])
    return {"id": label, "kind": kind, "intensity": _num(row["intensity"], 3), "unit": unit,
            "color": [_num(v, 4) for v in row["color"]], "temperature_k": _num(temperature, 1)}


def _temperature(label: str, color) -> float:
    """The correlated colour temperature of a linear `[r, g, b]` by McCamy's formula on CIE xy."""
    big_x, big_y, big_z = (sum(m * float(c) for m, c in zip(row, color)) for row in RGB_TO_XYZ)
    total = big_x + big_y + big_z
    kelvin = 0.0
    if total != 0:
        x, y = big_x / total, big_y / total
        if y != 0.1858:
            n = (x - 0.3320) / (0.1858 - y)
            kelvin = 449 * n ** 3 + 3525 * n ** 2 + 6823.3 * n + 5520.33
    if not kelvin > 0:
        raise SceneError(f"light {label} has no temperature and its color {[_num(v, 4) for v in color]} has none")
    return kelvin


def _sun(lights: list):
    """`{elevation_deg, azimuth_deg}` toward the one directional light of the sorted rows, or None."""
    suns = [row for row in lights if row.get("class") == "DirectionalLight"]
    if not suns:
        return None
    if len(suns) > 1:
        raise SceneError(f"lights {suns[0]['label']} and {suns[1]['label']} are both directional; a scene has one sun")
    x, y, z = (-v for v in _axes(suns[0]["rotation"])[0])
    azimuth = _num(math.degrees(math.atan2(y, x)) % 360, 3)
    return {"elevation_deg": _num(math.degrees(math.asin(min(1.0, max(-1.0, z)))), 3),
            "azimuth_deg": 0 if azimuth == 360 else azimuth}


def state(scene: dict, job: dict, level: dict | None = None, assets: dict | None = None) -> dict:
    """The 14 keys of the scene state from `scene.json`, the job's `job.json`, the unit's `LEVEL` and
    `assets`, the locked state of each asset by id."""
    camera = scene.get("camera")
    if not camera or camera.get("label") != "camera":
        raise SceneError("no actor labelled camera")
    rows = sorted(scene.get("actors", []), key=lambda r: r["label"])
    lights = sorted(scene.get("lights", []), key=lambda r: r["label"])
    labels = [r["label"] for r in rows + lights] + ["camera"]
    for label in sorted(labels):
        if labels.count(label) > 1:
            raise SceneError(f"two actors labelled {label}")
    kinds = _kinds(level)
    named = {}
    for name, digest in sorted((job.get("assets") or {}).items()):
        found = (assets or {}).get(name)
        if not isinstance(found, dict) or found.get("sha256") != digest or found.get("kind") not in KINDS:
            raise SceneError(f"scene names asset:{name}; it has no locked state")
        if not any(row["label"] == name for row in rows):
            raise SceneError(f"scene names asset:{name}; no actor is labelled {name}")
        named[name] = found
    actors = []
    for row in rows:
        found = named.get(row["label"])
        if found is None and row["label"] not in kinds:
            raise SceneError(f"actor {row['label']} has no kind: no level names it")
        kind = kinds[row["label"]] if found is None else found["kind"]
        asset = None if found is None else f"asset:{row['label']}"
        place = _transform(row)
        actors.append({"id": row["label"], "kind": kind, "asset": asset,
                       "sha256": None if found is None else found["sha256"],
                       "scale": [_num(v, 4) for v in row["scale"]],
                       "height_m": _num(2 * row["bounds"]["extent"][2] / 100, 4),
                       "start": place, "end": dict(place)})
    view = _transform(camera)
    focal = _num(camera["focal_mm"], 3)
    framing = []
    for frame in range(int(job["first"]), int(job["last"]) + 1):
        for row in rows:
            box = _box(row["bounds"], camera)
            if box is not None:
                framing.append({"frame": frame, "actor": row["label"], "box": box})
    cvars, render = scene.get("cvars", {}), scene.get("render", {})
    return {
        "shot": job["shot"], "stage": job["stage"], "fps": job["fps"],
        "frames": {"first": job["first"], "last": job["last"]},
        "actors": actors,
        "camera": {"focal_mm": {"min": focal, "max": focal}, "sensor_mm": [_num(v, 3) for v in camera["sensor_mm"]],
                   "start": view, "end": dict(view)},
        "framing": framing, "line_flips": [], "motion": [], "lipsync": [], "caches": [],
        "lights": [_light(row) for row in lights], "sun": _sun(lights),
        "render": {"lumen": cvars.get(CVAR_GI) == 1, "tsr": cvars.get(CVAR_AA) == 4,
                   "spatial_samples": render["spatial_samples"], "temporal_samples": render["temporal_samples"],
                   "warmup_frames": render["warmup_frames"], "width": job["width"], "height": job["height"]},
    }


def dumps(value: dict) -> str:
    return json.dumps(value, sort_keys=True, indent=2) + "\n"
