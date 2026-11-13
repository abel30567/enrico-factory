"""Lighting: exposure and color against the sequence key, clipping, time of day (brief sections 6 and 16).

Reads, of the files git would commit, each `production/<ep>/<shot>/lighting/render.json` outside
`production/assets/`, in sorted order, and for its shot `production/<ep>/<shot>/lighting/state.json`
and the sequence key `bible/style/keys/<ep>/<Q>.json`, `<Q>` the sequence of the id `<Q>-<n>`. Of a
render it reads `samples`, and of each sample `frame`, `luma`, `clipped` and `color`; of a state,
`shot`, `stage`, `lights` (each `id`, `kind`, `intensity`, `unit`, `color`, `temperature_k`) and `sun`
(`elevation_deg`, `azimuth_deg`); of a key, `luma`, `color` and `sun` (`min_elevation_deg`,
`max_elevation_deg`). Every other key is ignored; a state with no render is not read.

Rule ids: render (1), state (2), key (3), exposure (4), color (5), clipping (6), time-of-day (7).
Findings are on line 1. Each sample's `luma` is within `LUMA_SHARE` of the key's, its `color` within
`DELTA_E_MAX` delta-E 2000 of the key's, and its `clipped` at most `CLIPPED_MAX`; the state's sun
elevation is inside the key's band, or both are null. The three limits are placeholders until #130
measures them. A missing or bad key stops rules 4 to 7 of its shots, a missing key every other rule
too; a render finding stops rules 4 to 6 of its file and a state finding stops rule 7.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from ..lint import Finding, files, read_json

LUMA_SHARE = 0.10
DELTA_E_MAX = 1
CLIPPED_MAX = 0.001
RENDER = re.compile(r"^production/([^/]+)/([^/]+)/lighting/render\.json$")
SHOT_ID = re.compile(r"^(.+)-[0-9]+$")
SAMPLE_SHAPE = "is not {frame, luma, clipped, color} with values from 0 to 1"
LIGHT_SHAPE = "is not {id, kind, intensity, unit, color, temperature_k}"
RGB_TO_XYZ = ((0.4124564, 0.3575761, 0.1804375),
              (0.2126729, 0.7151522, 0.0721750),
              (0.0193339, 0.1191920, 0.9503041))
WHITE = (0.95047, 1.0, 1.08883)


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _unit(value) -> bool:
    return _number(value) and 0 <= value <= 1


def _rgb(value) -> bool:
    return isinstance(value, list) and len(value) == 3 and all(_unit(v) for v in value)


def lab(rgb) -> tuple[float, float, float]:
    """CIELAB (D65) of a linear sRGB `[r, g, b]`."""
    def f(t):
        return t ** (1 / 3) if t > (6 / 29) ** 3 else t / (3 * (6 / 29) ** 2) + 4 / 29

    x, y, z = (sum(m * c for m, c in zip(row, rgb)) / w for row, w in zip(RGB_TO_XYZ, WHITE))
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e_2000(lab1, lab2) -> float:
    """CIEDE2000 (CIE 142-2001) between two CIELAB colors, kL = kC = kH = 1."""
    l1, a1, b1 = lab1
    l2, a2, b2 = lab2
    c_bar = (math.hypot(a1, b1) + math.hypot(a2, b2)) / 2
    g = 0.5 * (1 - math.sqrt(c_bar ** 7 / (c_bar ** 7 + 25 ** 7)))
    a1p, a2p = a1 * (1 + g), a2 * (1 + g)
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if c1p else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if c2p else 0.0
    dlp, dcp = l2 - l1, c2p - c1p
    if c1p * c2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    elif h2p - h1p > 180:
        dhp = h2p - h1p - 360
    else:
        dhp = h2p - h1p + 360
    dHp = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dhp / 2))
    lp_bar, cp_bar = (l1 + l2) / 2, (c1p + c2p) / 2
    if c1p * c2p == 0:
        hp_bar = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hp_bar = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        hp_bar = (h1p + h2p + 360) / 2
    else:
        hp_bar = (h1p + h2p - 360) / 2
    t = (1 - 0.17 * math.cos(math.radians(hp_bar - 30)) + 0.24 * math.cos(math.radians(2 * hp_bar))
         + 0.32 * math.cos(math.radians(3 * hp_bar + 6)) - 0.20 * math.cos(math.radians(4 * hp_bar - 63)))
    d_theta = 30 * math.exp(-(((hp_bar - 275) / 25) ** 2))
    rc = 2 * math.sqrt(cp_bar ** 7 / (cp_bar ** 7 + 25 ** 7))
    sl = 1 + 0.015 * (lp_bar - 50) ** 2 / math.sqrt(20 + (lp_bar - 50) ** 2)
    sc = 1 + 0.045 * cp_bar
    sh = 1 + 0.015 * cp_bar * t
    rt = -math.sin(math.radians(2 * d_theta)) * rc
    return math.sqrt((dlp / sl) ** 2 + (dcp / sc) ** 2 + (dHp / sh) ** 2 + rt * (dcp / sc) * (dHp / sh))


def _key(data) -> bool:
    if not (isinstance(data, dict) and _number(data.get("luma")) and 0 < data["luma"] <= 1
            and _rgb(data.get("color")) and "sun" in data):
        return False
    sun = data["sun"]
    if sun is None:
        return True
    return (isinstance(sun, dict) and _number(lo := sun.get("min_elevation_deg"))
            and _number(hi := sun.get("max_elevation_deg")) and -90 <= lo <= hi <= 90)


def _sample(value) -> bool:
    return (isinstance(value, dict) and isinstance(value.get("frame"), int)
            and not isinstance(value["frame"], bool) and _unit(value.get("luma"))
            and _unit(value.get("clipped")) and _rgb(value.get("color")))


def _render(rel: str, data) -> list[Finding]:
    def finding(message):
        return Finding(rel, 1, "render", message)

    if not isinstance(data, dict):
        return [finding("not a JSON object")]
    samples = data.get("samples")
    if not isinstance(samples, list):
        return [finding("samples is not a list")]
    if not samples:
        return [finding("samples is empty")]
    return [finding(f"samples[{i}] {SAMPLE_SHAPE}") for i, s in enumerate(samples) if not _sample(s)]


def _light(value) -> bool:
    return (isinstance(value, dict) and isinstance(value.get("id"), str)
            and isinstance(value.get("kind"), str) and _number(value.get("intensity"))
            and value["intensity"] >= 0 and value.get("unit") in ("lux", "cd") and _rgb(value.get("color"))
            and _number(value.get("temperature_k")) and value["temperature_k"] > 0)


def _sun(value) -> bool:
    return value is None or (isinstance(value, dict) and _number(value.get("elevation_deg"))
                             and _number(value.get("azimuth_deg")))


def _state(rel: str, shot: str, data) -> list[Finding]:
    def finding(message):
        return Finding(rel, 1, "state", message)

    if not isinstance(data, dict):
        return [finding("not a JSON object")]
    found = []
    if data.get("shot") != shot:
        found.append(finding(f"shot is not '{shot}'"))
    if data.get("stage") != "lighting":
        found.append(finding("stage is not 'lighting'"))
    lights = data.get("lights")
    if not isinstance(lights, list):
        found.append(finding("lights is not a list"))
    else:
        found += [finding(f"lights[{i}] {LIGHT_SHAPE}") for i, light in enumerate(lights) if not _light(light)]
    if "sun" not in data or not _sun(data["sun"]):
        found.append(finding("sun is not null or {elevation_deg, azimuth_deg}"))
    return found


def _samples(rel: str, samples: list, key: dict) -> list[Finding]:
    found = []
    key_lab = lab(key["color"])
    for s in samples:
        frame, luma, clipped = s["frame"], s["luma"], s["clipped"]
        share = abs(luma - key["luma"]) / key["luma"]
        if round(share, 6) > LUMA_SHARE:
            found.append(Finding(rel, 1, "exposure", f"frame {frame}: luma {luma:g} is {share * 100:.1f}% from"
                                                     f" the key's {key['luma']:g} (at most {LUMA_SHARE * 100:g}%)"))
        delta_e = delta_e_2000(key_lab, lab(s["color"]))
        if round(delta_e, 4) > DELTA_E_MAX:
            found.append(Finding(rel, 1, "color", f"frame {frame}: color is {delta_e:.2f} delta-E 2000 from"
                                                  f" the key's (at most {DELTA_E_MAX:g})"))
        if round(clipped, 6) > CLIPPED_MAX:
            found.append(Finding(rel, 1, "clipping",
                                 f"frame {frame}: {clipped:g} of the pixels clipped (at most {CLIPPED_MAX:g})"))
    return found


def _time_of_day(rel: str, seq: str, sun, band) -> list[Finding]:
    def finding(message):
        return [Finding(rel, 1, "time-of-day", message)]

    if band is None:
        return finding(f"sun is set but the key of {seq} has no sun") if sun is not None else []
    lo, hi = band["min_elevation_deg"], band["max_elevation_deg"]
    if sun is None:
        return finding(f"sun is null but the key of {seq} wants one from {lo:g} to {hi:g} degrees")
    elevation = sun["elevation_deg"]
    if elevation < lo or elevation > hi:
        return finding(f"sun at {elevation:g} degrees is outside the key of {seq}, {lo:g} to {hi:g} degrees")
    return []


def check(root: Path) -> list[Finding]:
    rels = {p.as_posix() for p in files(root)}
    renders = sorted((m.group(1), m.group(2), rel) for rel in rels
                     if (m := RENDER.match(rel)) and m.group(1) != "assets")
    found, keys = [], {}
    for ep, shot, rel in renders:
        if not (m := SHOT_ID.match(shot)):
            found.append(Finding(rel, 1, "render", f"shot '{shot}' is not <sequence>-<n>"))
            continue
        seq = m.group(1)
        key_rel = f"bible/style/keys/{ep}/{seq}.json"
        if key_rel not in rels:
            found.append(Finding(rel, 1, "key", f"no key {key_rel} for sequence {seq}"))
            continue
        if key_rel not in keys:
            _, data = read_json(root, key_rel)
            keys[key_rel] = data if _key(data) else None
            if keys[key_rel] is None:
                found.append(Finding(key_rel, 1, "key", "not {luma, color, sun}"))
        key = keys[key_rel]
        _, render = read_json(root, rel)
        render_found = _render(rel, render)
        found += render_found
        state_rel = f"production/{ep}/{shot}/lighting/state.json"
        state, state_found = None, []
        if state_rel not in rels:
            state_found = [Finding(rel, 1, "state", f"{state_rel} is missing")]
        else:
            _, state = read_json(root, state_rel)
            state_found = _state(state_rel, shot, state)
        found += state_found
        if key is None:
            continue
        if not render_found:
            found += _samples(rel, render["samples"], key)
        if not state_found:
            found += _time_of_day(state_rel, seq, state["sun"], key["sun"])
    return found
