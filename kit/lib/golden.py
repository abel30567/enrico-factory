"""The golden test levels in `evals/golden/<level>/`, checked without the engine.

A unit is one directory that one job records: its `scene.py` and, once recorded, the files of `RECORDED`
(brief section 16, "Production files"). `UNITS` holds the `LEVEL` each `scene.py` must hold. The script
rules are read with `ast` only: nothing here imports or runs a `scene.py`.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from pathlib import Path

from kit.lib import reproduce

FLOOR = {"id": "floor", "shape": "plane", "kind": "set", "location": [0, 0, 0], "rotation": [0, 0, 0],
         "scale": [10, 10, 1], "physics": False}
KEY = {"id": "key", "kind": "directional", "intensity": 10, "unit": "lux", "color": [1, 1, 1],
       "temperature_k": 6500, "location": [0, 0, 5], "rotation": [0, -45, 30]}
FILL = {"id": "fill", "kind": "point", "intensity": 50, "unit": "cd", "color": [1, 1, 1],
        "temperature_k": 4000, "location": [-2, -2, 2], "rotation": [0, 0, 0]}
SENSOR = [36, 20.25]


def _primitive(id_, shape, kind, location, scale, physics=False) -> dict:
    return {"id": id_, "shape": shape, "kind": kind, "location": location, "rotation": [0, 0, 0],
            "scale": scale, "physics": physics}


def _level(stage, first, last, lit, primitives, lights, focal, location, rotation) -> dict:
    return {"stage": stage, "fps": 24, "first": first, "last": last, "width": 640, "height": 360,
            "lumen": lit, "tsr": lit, "primitives": [FLOOR] + primitives, "lights": lights,
            "camera": {"focal_mm": focal, "sensor_mm": SENSOR, "location": location, "rotation": rotation}}


ACTORS = [_primitive("actor-a", "cube", "character", [0, -0.4, 0.9], [0.4, 0.4, 1.8]),
          _primitive("actor-b", "cube", "character", [0, 0.4, 0.8], [0.4, 0.4, 1.6])]
UNITS = {
    "lit-render": _level("render", 0, 23, True,
                         [_primitive("ball", "sphere", "prop", [0, 0, 0.5], [1, 1, 1]),
                          _primitive("block", "cube", "prop", [0.8, 0.9, 0.25], [0.5, 0.5, 0.5])],
                         [KEY, FILL], 35, [-5, 0, 1.5], [0, -10, 0]),
    "prop-drop": _level("simulation", 0, 47, False,
                        [_primitive("prop", "cube", "prop", [0, 0, 3], [0.5, 0.5, 0.5], True)],
                        [KEY], 35, [-6, 0, 1.5], [0, -10, 0]),
    "two-shot/Q1-1": _level("layout", 0, 23, False, ACTORS, [KEY], 35, [-5, 0, 1.2], [0, 0, 0]),
    "two-shot/Q1-2": _level("layout", 24, 47, False, ACTORS, [KEY], 50, [-6, 0, 1.2], [0, 0, 0]),
}

RECORDED = ("manifest.json", "render.json", "state.json")
RERUN = "prop-drop"
STATE_KEYS = ("shot", "stage", "fps", "frames", "actors", "camera", "framing", "line_flips", "motion",
              "lipsync", "caches", "lights", "sun", "render")
RENDER_KEYS = ("fps", "frames", "count", "width", "height", "frames_sha256", "sequence_sha256", "black",
               "nan", "text", "samples")
NETWORK = {"asyncio", "ftplib", "http", "imaplib", "poplib", "smtplib", "socket", "socketserver", "ssl",
           "telnetlib", "urllib", "webbrowser", "xmlrpc"}
CLOCK = {"time", "datetime"}
CLOCK_CALLS = {"now", "today", "utcnow"}
UNSEEDED = {"secrets", "uuid"}
HIDDEN = {"__import__", "exec", "eval", "compile"}
ENGINE_SHAPES = {"/Engine/BasicShapes/Cube.Cube", "/Engine/BasicShapes/Sphere.Sphere",
                 "/Engine/BasicShapes/Plane.Plane"}
STATE_FILES = {"state.json", "rerun.json"}
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_STDLIB = frozenset(getattr(sys, "stdlib_module_names", ()))


def _segments(text: str) -> list[str]:
    return re.split(r"[/\\]", text)


def _module_problems(name: str) -> list[str]:
    top = name.split(".")[0]
    if top != "unreal" and top not in _STDLIB:
        return [f"imports {name}, which is not unreal or the standard library"]
    if top in NETWORK:
        return [f"imports {name}, a network module"]
    if top in CLOCK:
        return [f"reads the clock: {name}"]
    if top in UNSEEDED:
        return [f"uses a random number without a seed: {name}"]
    if top == "importlib":
        return [f"runs code it does not show: {name}"]
    return []


def _literal_problems(text: str) -> list[str]:
    found = []
    parts = _segments(text)
    if ((text.startswith("/") and text not in ENGINE_SHAPES) or text.startswith("~") or _DRIVE.match(text)
            or ".." in parts):
        found.append(f"names a path outside the job directory: '{text}'")
    if parts[-1] in STATE_FILES:
        found.append(f"names the state file {parts[-1]}, which only the job writes")
    if text.startswith("asset:"):
        found.append(f"names a production asset: '{text}'")
    return found


def _seeded(node: ast.Call) -> bool:
    """True for `random.Random(<one integer literal>)`."""
    return (len(node.args) == 1 and not node.keywords and isinstance(node.args[0], ast.Constant)
            and type(node.args[0].value) is int)


def _same(a, b) -> bool:
    return not reproduce.differences(a, b)


def _level_problems(tree: ast.Module, level: dict) -> list[tuple[int, str]]:
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "LEVEL"):
            try:
                found = ast.literal_eval(node.value)
            except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                continue
            if not isinstance(found, dict):
                continue
            for key in sorted(set(found) | set(level), key=str):
                if key not in found or key not in level or not _same(found[key], level[key]):
                    return [(node.lineno, f"LEVEL differs from golden.UNITS at {key}")]
            return []
    return [(1, "has no LEVEL literal")]


def script_problems(text: str, shown: str, level: dict | None = None) -> list[str]:
    """The findings of the script rules on the text of a `scene.py`, each `<shown>:<line> <message>`."""
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        return [f"{shown}:{error.lineno or 1} is not Python 3"]
    found: list[tuple[int, str]] = []
    allowed = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "Random"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "random" and _seeded(node)):
            allowed.add(id(node.func))
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 1)
        if isinstance(node, ast.Import):
            for alias in node.names:
                found += [(line, message) for message in _module_problems(alias.name)]
        elif isinstance(node, ast.ImportFrom):
            name = "." * node.level + (node.module or "")
            if node.level:
                found.append((line, f"imports {name}, which is not unreal or the standard library"))
                continue
            found += [(line, message) for message in _module_problems(name)]
            for alias in node.names:
                if name == "random":
                    found.append((line, f"uses a random number without a seed: random.{alias.name}"))
                elif name == "os" and alias.name == "urandom":
                    found.append((line, "uses a random number without a seed: os.urandom"))
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr in CLOCK_CALLS:
                found.append((line, f"reads the clock: {node.func.attr}"))
            if isinstance(node.func, ast.Name) and node.func.id in HIDDEN:
                found.append((line, f"runs code it does not show: {node.func.id}"))
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and id(node) not in allowed:
                if node.value.id == "random":
                    found.append((line, f"uses a random number without a seed: random.{node.attr}"))
                elif node.value.id == "os" and node.attr == "urandom":
                    found.append((line, "uses a random number without a seed: os.urandom"))
            if "Blueprint" in node.attr:
                found.append((line, f"uses a Blueprint: {node.attr}"))
        elif isinstance(node, ast.Name):
            if "Blueprint" in node.id:
                found.append((line, f"uses a Blueprint: {node.id}"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found += [(line, message) for message in _literal_problems(node.value)]
    if level is not None:
        found += _level_problems(tree, level)
    return [f"{shown}:{line} {message}" for line, message in sorted(set(found))]


def _files(unit: str) -> tuple[str, ...]:
    return tuple(sorted(RECORDED + (("rerun.json",) if unit == RERUN else ())))


def recorded(unit: Path) -> bool:
    """True when any recorded file of the unit is in its directory."""
    name = unit.name if unit.parent.name != "two-shot" else f"two-shot/{unit.name}"
    return any((unit / file).is_file() for file in _files(name))


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_hex(value) -> bool:
    return isinstance(value, str) and bool(_HEX64.fullmatch(value))


def _manifest_ok(manifest: dict) -> bool:
    inputs, seeds, outputs = manifest.get("inputs"), manifest.get("seeds"), manifest.get("outputs")
    flags, engine = manifest.get("flags"), manifest.get("engine")
    return (_is_hex(manifest.get("job")) and isinstance(engine, str) and bool(engine)
            and isinstance(flags, list) and all(isinstance(f, str) for f in flags)
            and isinstance(inputs, list)
            and all(isinstance(i, dict) and isinstance(i.get("path"), str) and _is_hex(i.get("sha256"))
                    for i in inputs)
            and isinstance(seeds, dict) and all(_is_int(v) for v in seeds.values())
            and isinstance(outputs, list)
            and all(isinstance(o, dict) and isinstance(o.get("name"), str) and _is_hex(o.get("sha256"))
                    and _is_int(o.get("bytes")) for o in outputs))


def _hash_problems(directory: Path, unit: str, manifest: dict) -> list[str]:
    found = []
    script = (directory / "scene.py").read_bytes() if (directory / "scene.py").is_file() else None
    digest = hashlib.sha256(script).hexdigest() if script is not None else None
    if not any(row["path"] == "scene.py" and row["sha256"] == digest for row in manifest["inputs"]):
        found.append(f"{unit}/manifest.json does not record scene.py at its sha256")
    for name in _files(unit):
        if name == "manifest.json":
            continue
        data = (directory / name).read_bytes()
        row = {"name": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        if not any(all(out[k] == v for k, v in row.items()) for out in manifest["outputs"]):
            found.append(f"{unit}/manifest.json does not record {name} at its sha256 and size")
    return found


_ABSENT = object()


def _get(value, path: str):
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return _ABSENT
        value = value[part]
    return value


def _ids(value):
    if not isinstance(value, list) or not all(isinstance(item, dict) and "id" in item for item in value):
        return _ABSENT
    ids = [item["id"] for item in value]
    try:
        return sorted(ids)
    except TypeError:
        return ids


def _dump(value) -> str:
    return "absent" if value is _ABSENT else json.dumps(value, sort_keys=True)


def _value_problems(shown: str, found: dict, expected: dict) -> list[str]:
    lines = []
    for key, want in expected.items():
        have = found[key]
        if have is _ABSENT or not _same(have, want):
            lines.append(f"{shown} {key} is {_dump(have)}, not {_dump(want)}")
    return lines


def _state_problems(unit: str, name: str, state: dict) -> list[str]:
    level, shown = UNITS[unit], f"{unit}/{name}"
    found = [f"{shown} lacks {key}" for key in STATE_KEYS if key not in state]
    expected = {"shot": "Q1-2" if unit == "two-shot/Q1-2" else "Q1-1", "stage": level["stage"],
                "fps": level["fps"], "frames": {"first": level["first"], "last": level["last"]},
                "render.width": level["width"], "render.height": level["height"],
                "render.lumen": level["lumen"], "render.tsr": level["tsr"],
                "actors[].id": sorted(p["id"] for p in level["primitives"]),
                "lights[].id": sorted(light["id"] for light in level["lights"]),
                "camera.focal_mm": {"max": level["camera"]["focal_mm"], "min": level["camera"]["focal_mm"]}}
    have = {key: _get(state, key) for key in expected}
    have["actors[].id"] = _ids(state.get("actors"))
    have["lights[].id"] = _ids(state.get("lights"))
    return found + _value_problems(shown, have, expected)


def _render_problems(unit: str, render: dict) -> list[str]:
    level, shown = UNITS[unit], f"{unit}/render.json"
    found = [f"{shown} lacks {key}" for key in RENDER_KEYS if key not in render]
    expected = {"fps": level["fps"], "frames": {"first": level["first"], "last": level["last"]},
                "count": level["last"] - level["first"] + 1, "width": level["width"], "height": level["height"]}
    found += _value_problems(shown, {key: _get(render, key) for key in expected}, expected)
    if unit == "lit-render" and "rerender" not in render:
        found.append(f"{shown} lacks rerender")
    elif unit != "lit-render" and "rerender" in render:
        found.append(f"{shown} holds rerender, which only lit-render records")
    return found


def _load(path: Path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def unit_problems(golden: Path, unit: str) -> list[str]:
    """The findings on one unit of `UNITS`, its paths relative to `golden`."""
    directory = golden / unit
    script = directory / "scene.py"
    if script.is_file():
        found = script_problems(script.read_text(encoding="utf-8"), f"{unit}/scene.py", UNITS[unit])
    else:
        found = [f"{unit}/scene.py missing"]
    files = _files(unit)
    if directory.is_dir():
        for path in sorted(directory.iterdir()):
            if not (path.is_file() and path.name in ("scene.py",) + files):
                found.append(f"{unit}/{path.name} is not a file of a golden level")
    missing = [name for name in files if not (directory / name).is_file()]
    if len(missing) == len(files):
        return found
    if missing:
        return found + [f"{unit} is partly recorded: {', '.join(missing)} missing"]
    loaded = {}
    for name in files:
        loaded[name] = _load(directory / name)
        if loaded[name] is None:
            found.append(f"{unit}/{name} is not a JSON object")
    manifest = loaded["manifest.json"]
    if manifest is not None:
        if _manifest_ok(manifest):
            found += _hash_problems(directory, unit, manifest)
        else:
            found.append(f"{unit}/manifest.json is not {{job, engine, flags, inputs, seeds, outputs}}")
    for name in ("state.json", "rerun.json"):
        if loaded.get(name) is not None:
            found += _state_problems(unit, name, loaded[name])
    if loaded["render.json"] is not None:
        found += _render_problems(unit, loaded["render.json"])
    return found


def check(golden: Path) -> list[str]:
    """The findings on every unit of `UNITS`, in sorted order."""
    found = []
    for unit in sorted(UNITS):
        found += unit_problems(golden, unit)
    return found
