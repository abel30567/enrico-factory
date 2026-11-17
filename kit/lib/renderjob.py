"""The render job of the driver `ec2` on the machine that runs `factory run` (docs/design/render-job.md).

``describe(job, settings)`` is the job's `job.json`: the scene scripts are the inputs named `scene.py`, in input order,
the stage is the directory of the last one and the shot the directory above it, the frames the shot's row of the first
input ending `shots.json`, the size `RENDER_RESOLUTION`, `fps` that of the input `episodes/<ep>/reel/edit-list.json`
(`<ep>` the directory above the shot's), else of the unit's `LEVEL` (the literal of the last script); both given, they
must agree. `assets` is `{<id>: <sha256>}` of the inputs `asset:<id>`, whose `text` must be the asset's locked state
with that `sha256` (``asset_states``). ``in_tar`` packs `job.json`, the scripts as `scripts/<nn>.py`, `job.sh` and
`build.py` of `kit/engine/`. ``unpack`` writes the members of `out.tar` into a directory, refusing any member that is
not a regular file or a directory, whose path is absolute or holds `..`, or whose name is not one `job.sh` writes.
``finish`` checks the node's answer and writes the outputs into `out`, with `manifest.json` last. At `simulation` it
also writes `rerun.json`, the state of the second build. ``failed_step`` reads the step of a failed node's
`status.json`; ``tail`` keeps the end of its log without a line that may hold a secret.

A refusal or a failed job is ``JobError``; its text never holds a value of a setting or a secret.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import tarfile
from pathlib import Path

from kit.lib import frames, scenestate
from kit.lib.lints.assets import ASSET_ID, KINDS, SHA256

ENGINE = Path(__file__).resolve().parent.parent / "engine"
ENGINE_FILES = ("job.sh", "build.py")
SCRIPT = "scene.py"
SHOTS = "shots.json"
JOB_FAILED = "job failed on the node"
BUILD_OK = "FACTORY build ok"
RERUN_STAGE = "simulation"
RERUN_SCENE = "rerun-scene.json"
NO_SCRIPT = "no input named scene.py"
NO_SHOTS = "no input ending shots.json"
BAD_RESOLUTION = "RENDER_RESOLUTION is not <width>x<height>"
EDIT_LIST = "episodes/{}/reel/edit-list.json"
NO_FPS = "no input {} and the unit's LEVEL has no fps"
BAD_FPS = "{} has no fps that is a whole number above zero"
TWO_FPS = "the unit's LEVEL has fps {}, {} has fps {}"
ASSET = "asset:"
NOT_LOCKED = "input asset:{} does not match its locked state"
# What job.sh packs into out.tar.
NAMES = re.compile(r"scene\.json|rerun-scene\.json|node\.json|engine-build\.log|engine-render\.log"
                   r"|frame-[0-9]{6}\.(png|exr)|rerender-[0-9]{6}\.png")
FIXED_SEED = {"FixedSeed": 0}
# The steps a node names in status.json when it fails: start.sh's, then job.sh's.
STEPS = ("pull", "fetch", "build", "rebuild", "render", "rerender", "pack", "upload")
TAIL_LINES = 40
LINE_CHARS = 240
WITHHELD = "[line withheld: it may hold a secret]"
SIGNED = "X-Amz-"
_RESOLUTION = re.compile(r"([1-9][0-9]*)x([1-9][0-9]*)")


class JobError(Exception):
    """The job cannot be sent, or failed on the node; ``str(error)`` says which."""


def _int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def level(text: str):
    """The `LEVEL` dict literal assigned at the top of a script, or None."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "LEVEL"):
            try:
                found = ast.literal_eval(node.value)
            except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                return None
            return found if isinstance(found, dict) else None
    return None


def _scripts(job) -> list:
    return [item for item in job["inputs"] if posixpath.basename(item["path"]) == SCRIPT]


def stage_dir(job) -> str:
    """The directory of the last input named `scene.py`."""
    scripts = _scripts(job)
    if not scripts:
        raise JobError(NO_SCRIPT)
    return posixpath.dirname(scripts[-1]["path"])


def _row(job, shot):
    shots = [item for item in job["inputs"] if item["path"].endswith(SHOTS)]
    if not shots:
        raise JobError(NO_SHOTS)
    try:
        rows = json.loads(shots[0]["text"])
    except (TypeError, ValueError):
        rows = None
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("shot") == shot:
            first, last = row.get("first"), row.get("last")
            if _int(first) and _int(last) and 0 <= first <= last:
                return first, last
            break
    raise JobError(f"{SHOTS} has no row {{shot, first, last}} for shot {shot}")


def sampled(stage: str, first: int, last: int) -> list:
    """The frames rendered again at `render`: the first, the middle and the last (#130 rules on more)."""
    if stage != "render":
        return []
    return sorted({first, (first + last) // 2, last})


def edit_list(directory: str) -> str:
    """The path of the edit list of the episode of a stage directory `<ep>/<shot>/<stage>`."""
    return EDIT_LIST.format(posixpath.basename(posixpath.dirname(posixpath.dirname(directory))))


def frame_rate(job, directory) -> int:
    """The job's `fps`: the edit list's when it is an input, else the `LEVEL`'s; both given, they must agree."""
    edit = edit_list(directory)
    level_fps = (level(_scripts(job)[-1]["text"]) or {}).get("fps")
    if not (_int(level_fps) and level_fps > 0):
        level_fps = None
    edits = [item for item in job["inputs"] if item["path"] == edit]
    if not edits:
        if level_fps is None:
            raise JobError(NO_FPS.format(edit))
        return level_fps
    try:
        listed = json.loads(edits[0]["text"])
    except (TypeError, ValueError):
        listed = None
    fps = listed.get("fps") if isinstance(listed, dict) else None
    if not (_int(fps) and fps > 0):
        raise JobError(BAD_FPS.format(edit))
    if level_fps is not None and level_fps != fps:
        raise JobError(TWO_FPS.format(level_fps, edit, fps))
    return fps


def asset_states(job) -> dict:
    """``{<id>: <state>}`` of the inputs `asset:<id>` in input order, each ``text`` the asset's locked state with
    the input's ``sha256``; ``JobError`` on the first that is not."""
    found = {}
    for item in job["inputs"]:
        if not item["path"].startswith(ASSET):
            continue
        name = item["path"][len(ASSET):]
        try:
            state = json.loads(item["text"])
        except (TypeError, ValueError):
            state = None
        if not (ASSET_ID.fullmatch(name) and isinstance(state, dict) and state.get("asset") == name
                and state.get("kind") in KINDS and isinstance(state.get("sha256"), str)
                and SHA256.fullmatch(state["sha256"]) and state["sha256"] == item["sha256"]):
            raise JobError(NOT_LOCKED.format(name))
        found[name] = state
    return found


def describe(job, settings) -> dict:
    """The job's `job.json`; ``JobError`` when an input or a setting it needs is missing."""
    scripts = _scripts(job)
    directory = stage_dir(job)
    stage, shot = posixpath.basename(directory), posixpath.basename(posixpath.dirname(directory))
    first, last = _row(job, shot)
    size = _RESOLUTION.fullmatch(str(settings.get("RENDER_RESOLUTION", "")))
    if size is None:
        raise JobError(BAD_RESOLUTION)
    fps = frame_rate(job, directory)
    assets = asset_states(job)
    return {"hash": job["hash"], "shot": shot, "stage": stage, "fps": fps, "first": first, "last": last,
            "width": int(size.group(1)), "height": int(size.group(2)),
            "scripts": [f"scripts/{n:02d}.py" for n in range(len(scripts))],
            "assets": {name: assets[name]["sha256"] for name in sorted(assets)},
            "sampled": sampled(stage, first, last)}


def _add(tar, name, data: bytes, mode=0o644):
    info = tarfile.TarInfo(name)
    info.size, info.mode, info.mtime = len(data), mode, 0
    tar.addfile(info, io.BytesIO(data))


def in_tar(job, described) -> bytes:
    """`in.tar`: `job.json`, the scripts in input order, `job.sh` and `build.py`."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        _add(tar, "job.json", (json.dumps(described, sort_keys=True, indent=2) + "\n").encode("utf-8"))
        for name, item in zip(described["scripts"], _scripts(job)):
            _add(tar, name, item["text"].encode("utf-8"))
        for name in ENGINE_FILES:
            _add(tar, name, (ENGINE / name).read_bytes(), 0o755 if name.endswith(".sh") else 0o644)
    return buffer.getvalue()


def _refused(member) -> bool:
    name = member.name
    return (not (member.isfile() or member.isdir()) or name.startswith("/") or ".." in re.split(r"[/\\]", name)
            or not NAMES.fullmatch(name))


def unpack(tar_path, directory) -> list:
    """The names of `out.tar`, each written into ``directory``; ``JobError`` before any write on a refused
    member or a name given twice."""
    try:
        with tarfile.open(tar_path, mode="r:") as tar:
            members = tar.getmembers()
            names = [member.name for member in members]
            if any(_refused(member) for member in members) or len(set(names)) != len(names):
                raise JobError(JOB_FAILED)
            for member in members:
                source = tar.extractfile(member)
                with open(os.path.join(directory, member.name), "xb") as handle:
                    shutil.copyfileobj(source, handle)
    except (tarfile.TarError, OSError):
        raise JobError(JOB_FAILED) from None
    return sorted(names)


def failed_step(status):
    """The step of ``STEPS`` a failed node's `status.json` names, else None."""
    if isinstance(status, dict) and status.get("result") == "failed" and status.get("step") in STEPS:
        return status["step"]
    return None


def tail(text: str, hidden) -> list:
    """The last ``TAIL_LINES`` lines of ``text``, a line holding ``SIGNED`` or a value of ``hidden`` withheld and
    any other longer than ``LINE_CHARS`` cut."""
    found = []
    for line in text.splitlines()[-TAIL_LINES:]:
        if SIGNED in line or any(value in line for value in hidden):
            line = WITHHELD
        elif len(line) > LINE_CHARS:
            line = line[:LINE_CHARS - 1] + "…"
        found.append(line)
    return found


def _json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def built(node, step_name: str = "build") -> bool:
    """True when `node.json`'s step ``step_name`` logged the ok marker."""
    steps = node.get("steps") if isinstance(node, dict) else None
    return isinstance(steps, list) and any(
        isinstance(step, dict) and step.get("step") == step_name and BUILD_OK in (step.get("markers") or [])
        for step in steps)


def flags(node) -> list:
    """The render command line of `node.json`."""
    for step in node["steps"]:
        if isinstance(step, dict) and step.get("step") == "render" and isinstance(step.get("command"), list):
            return [str(part) for part in step["command"]]
    raise JobError(JOB_FAILED)


def _shown(path: str, directory: str) -> str:
    """A path inside the stage directory relative to it, any other as it is."""
    prefix = directory + "/"
    return path[len(prefix):] if path.startswith(prefix) else path


def seeds(job, directory: str) -> dict:
    """`{"FixedSeed": 0}` and each `random.Random(<int>)` of the scripts as `<path>:<line>`."""
    found = dict(FIXED_SEED)
    for item in _scripts(job):
        try:
            tree = ast.parse(item["text"])
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "Random"
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "random"
                    and len(node.args) == 1 and not node.keywords and isinstance(node.args[0], ast.Constant)
                    and _int(node.args[0].value)):
                found[f"{_shown(item['path'], directory)}:{node.lineno}"] = node.args[0].value
    return found


def _sha256(path) -> tuple:
    digest, size = hashlib.sha256(), 0
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def finish(job, described, status, unpacked, settings, run=subprocess.run):
    """Check the node's answer in ``unpacked`` and write the outputs into ``job["out"]``, `manifest.json` last."""
    node = _json(os.path.join(unpacked, "node.json"))
    if status != {"result": "ok"} or not built(node):
        raise JobError(JOB_FAILED)
    scene = _json(os.path.join(unpacked, "scene.json"))
    if not isinstance(scene, dict):
        raise JobError(JOB_FAILED)
    rerun = _json(os.path.join(unpacked, RERUN_SCENE))
    if described["stage"] == RERUN_STAGE:
        if not built(node, "rebuild") or not isinstance(rerun, dict):
            raise JobError(JOB_FAILED)
    elif os.path.exists(os.path.join(unpacked, RERUN_SCENE)):
        raise JobError(JOB_FAILED)
    directory = stage_dir(job)
    assets = asset_states(job)
    command = flags(node)
    out = job["out"]
    for name in sorted(os.listdir(unpacked)):
        if NAMES.fullmatch(name):
            shutil.copyfile(os.path.join(unpacked, name), os.path.join(out, name))
    try:
        state = scenestate.state(scene, described, level(_scripts(job)[-1]["text"]), assets)
        rerun_state = None if described["stage"] != RERUN_STAGE else scenestate.state(
            rerun, described, level(_scripts(job)[-1]["text"]), assets)
        measured = frames.measure(out, described["fps"], described["first"], described["last"],
                                  described["sampled"], run=run)
        metric = str(settings.get("PERCEPTUAL_METRIC", "")).strip()
        if described["stage"] == "render" and metric:
            measured["rerender"] = frames.rerender(
                [(f, os.path.join(out, f"frame-{f:06d}.png"), os.path.join(out, f"rerender-{f:06d}.png"))
                 for f in described["sampled"]], shlex.split(metric), run=run)
    except (scenestate.SceneError, frames.FramesError, KeyError, TypeError, ValueError):
        raise JobError(JOB_FAILED) from None
    Path(out, "state.json").write_text(scenestate.dumps(state), encoding="utf-8")
    if rerun_state is not None:
        Path(out, "rerun.json").write_text(scenestate.dumps(rerun_state), encoding="utf-8")
    Path(out, "render.json").write_text(json.dumps(measured, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    outputs = []
    for name in sorted(os.listdir(out)):
        sha256, size = _sha256(os.path.join(out, name))
        outputs.append({"name": name, "sha256": sha256, "bytes": size})
    manifest = {"job": job["hash"], "engine": str(settings.get("UNREAL_IMAGE", "")), "flags": command,
                "inputs": [{"path": _shown(item["path"], directory), "sha256": item["sha256"]}
                           for item in job["inputs"]],
                "seeds": seeds(job, directory), "outputs": outputs}
    Path(out, "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
