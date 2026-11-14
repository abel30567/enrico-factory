"""The render reproducibility harness: two runs of one render job compared key by key, and frame by frame.

A run is a directory holding `state.json` and `render.json` (brief section 16, "Production files"). The whole
`state.json` is compared; of `render.json` only `RENDER_KEYS`, because frames are not bit-exact between two
good runs (`frames_sha256`, `sequence_sha256`, `samples` and `rerender` may differ). Two values are equal when
they have the same JSON type and the same value. Without a frames directory the frame hashes are counted for a
note only. With one, every pair of frames whose hashes differ is scored by the setting `PERCEPTUAL_METRIC`, a
command run on the two frame files, and a pair scoring under the setting `RERENDER_MIN` is a difference.
"""

from __future__ import annotations

import decimal
import hashlib
import json
import os
import re
import shlex
import subprocess
from pathlib import Path

RENDER_KEYS = ("black", "count", "fps", "frames", "height", "nan", "text", "width")
RENDER_INTEGERS = ("count", "fps", "height", "width")
STATE_RENDER_BOOLEANS = ("lumen", "tsr")
STATE_RENDER_INTEGERS = ("spatial_samples", "temporal_samples", "warmup_frames", "width", "height")
TOLERANCE = "RERENDER_MIN"
METRIC = "PERCEPTUAL_METRIC"
_NUMBER = re.compile(r"-?\d+(\.\d+)?")


class ReproduceError(Exception):
    """The comparison cannot run; `problems` holds one line per problem."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def _kind(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _dump(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _absent(path: str, a, b, a_present: bool) -> str:
    if a_present:
        return f"{path}: {_dump(a)} in first, absent in second"
    return f"{path}: absent in first, {_dump(b)} in second"


def differences(a, b, path: str = "") -> list[str]:
    """The `<path>: <a> in first, <b> in second` lines for two JSON values, down to where they first differ."""
    if _kind(a) != _kind(b):
        return [f"{path}: {_dump(a)} in first, {_dump(b)} in second"]
    lines = []
    if isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            sub = f"{path}.{key}" if path else key
            if key in a and key in b:
                lines.extend(differences(a[key], b[key], sub))
            else:
                lines.append(_absent(sub, a.get(key), b.get(key), key in a))
    elif isinstance(a, list):
        for i in range(max(len(a), len(b))):
            sub = f"{path}[{i}]"
            if i < len(a) and i < len(b):
                lines.extend(differences(a[i], b[i], sub))
            else:
                lines.append(_absent(sub, a[i] if i < len(a) else None, b[i] if i < len(b) else None, i < len(a)))
    elif a != b:
        lines.append(f"{path}: {_dump(a)} in first, {_dump(b)} in second")
    return lines


def _no_constant(name):
    raise ValueError(f"{name} is not JSON")


def _load(label: str, problems: list[str]):
    path = Path(label)
    try:
        raw = path.read_bytes() if path.is_file() else None
    except OSError:
        raw = None
    if raw is None:
        problems.append(f"{label} is missing")
        return None
    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=_no_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        problems.append(f"{label} is not JSON")
        return None
    if not isinstance(value, dict):
        problems.append(f"{label} is not a JSON object")
        return None
    return value


def _frame_list(value) -> bool:
    return isinstance(value, list) and all(_is_int(v) for v in value)


def _check_render(label: str, render: dict, problems: list[str]) -> None:
    for key in RENDER_INTEGERS:
        if not _is_int(render.get(key)):
            problems.append(f"{label}: {key} is missing or not an integer")
    frames = render.get("frames")
    if not (isinstance(frames, dict) and _is_int(frames.get("first")) and _is_int(frames.get("last"))):
        problems.append(f"{label}: frames is missing or not {{first, last}} with two integers")
    for key in ("black", "nan"):
        if not _frame_list(render.get(key)):
            problems.append(f"{label}: {key} is missing or not a list of frames")
    text = render.get("text")
    if not (isinstance(text, list) and all(isinstance(t, dict) and _is_int(t.get("frame"))
                                           and isinstance(t.get("text"), str) for t in text)):
        problems.append(f"{label}: text is missing or not a list of {{frame, text}}")
    hashes = render.get("frames_sha256")
    if not (isinstance(hashes, list) and all(isinstance(h, str) for h in hashes)):
        problems.append(f"{label}: frames_sha256 is missing or not a list of strings")


def _check_state(label: str, state: dict, problems: list[str]) -> None:
    for key in ("shot", "stage"):
        if not isinstance(state.get(key), str):
            problems.append(f"{label}: {key} is missing or not a string")
    render = state.get("render")
    if not (isinstance(render, dict)
            and all(isinstance(render.get(k), bool) for k in STATE_RENDER_BOOLEANS)
            and all(_is_int(render.get(k)) for k in STATE_RENDER_INTEGERS)):
        problems.append(f"{label}: render is not {{lumen, tsr, spatial_samples, temporal_samples, warmup_frames, "
                        "width, height} with two booleans, then integers")


def _differing(a: list[str], b: list[str]) -> int:
    return sum(1 for i in range(max(len(a), len(b))) if i >= len(a) or i >= len(b) or a[i] != b[i])


def _note(a: list[str], b: list[str]) -> str:
    return (f"note render.json frames_sha256: {_differing(a, b)} of {max(len(a), len(b))} frame hashes differ; "
            "frames are not compared exactly")


def _settings(settings: dict) -> tuple[str, list[str]]:
    """The tolerance as written and the metric's argument list; `ReproduceError` when either is unusable."""
    problems = []
    tolerance = settings.get(TOLERANCE, "").strip()
    if not tolerance:
        problems.append(f"setting {TOLERANCE} is empty")
    elif not _NUMBER.fullmatch(tolerance):
        problems.append(f"setting {TOLERANCE} is not a number")
    command: list[str] = []
    value = settings.get(METRIC, "").strip()
    if not value:
        problems.append(f"setting {METRIC} is empty")
    else:
        try:
            command = shlex.split(value)
        except ValueError:
            pass
        if not command:
            problems.append(f"setting {METRIC} is not a command")
    if problems:
        raise ReproduceError(problems)
    return tolerance, command


def _check_frames(directory: str, hashes: list[str]) -> list[str]:
    problems = []
    for sha in hashes:
        label = f"{directory}/{sha}"
        path = Path(label)
        try:
            raw = path.read_bytes() if path.is_file() else None
        except OSError:
            raw = None
        if raw is None:
            problems.append(f"{label} is missing")
        elif hashlib.sha256(raw).hexdigest() != sha:
            problems.append(f"{label} does not hold the bytes of its sha256")
    return problems


def _score(command: list[str], first: str, second: str, cwd: str, frame: int) -> tuple[str | None, str | None]:
    """The score as the metric printed it, or the problem."""
    try:
        done = subprocess.run([*command, os.path.abspath(first), os.path.abspath(second)], cwd=cwd,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace")
    except OSError:
        # What a shell reports for a command that cannot be started.
        return None, f"the metric exited 127 on frame {frame}"
    if done.returncode != 0:
        return None, f"the metric exited {done.returncode} on frame {frame}"
    printed = [text.strip() for text in done.stdout.splitlines() if text.strip()]
    if not printed or not _NUMBER.fullmatch(printed[-1]):
        return None, f"the metric printed no number on its last line on frame {frame}"
    return printed[-1], None


def _frames(render_a: dict, render_b: dict, frames: str, settings: dict, cwd: str) -> tuple[list[str], str]:
    """The frame lines, the length line first, and the note; `ReproduceError` on a setting, a file or a run."""
    tolerance, command = _settings(settings)
    a, b = render_a["frames_sha256"], render_b["frames_sha256"]
    pairs = [i for i in range(min(len(a), len(b))) if a[i] != b[i]]
    directory = frames.rstrip("/")
    problems = _check_frames(directory, [a[i] for i in pairs]) + _check_frames(directory, [b[i] for i in pairs])
    if problems:
        raise ReproduceError(problems)
    start = render_a["frames"]["first"]
    scores = {}
    for i in pairs:
        scores[i] = _score(command, f"{directory}/{a[i]}", f"{directory}/{b[i]}", cwd, start + i)
    problems = [problem for _, problem in scores.values() if problem]
    if problems:
        raise ReproduceError(problems)
    lines = []
    if len(a) != len(b):
        lines.append(f"render.json frames_sha256: {len(a)} frames in first, {len(b)} in second")
    under = [i for i in pairs if decimal.Decimal(scores[i][0]) < decimal.Decimal(tolerance)]
    lines += [f"render.json frames_sha256[{i}]: frame {start + i} scores {scores[i][0]}, under {tolerance}"
              for i in under]
    note = (f"note render.json frames_sha256: {_differing(a, b)} of {max(len(a), len(b))} frame hashes differ; "
            f"{len(pairs) - len(under)} of {len(pairs)} scored frames at least {tolerance}")
    return lines, note


def compare(first: str, second: str, frames: str | None = None, settings: dict | None = None,
            cwd: str | None = None) -> tuple[list[str], str]:
    """The difference lines of two runs, `render.json` lines first, and the note on the frame hashes.

    With `frames`, a directory of frame files named by their sha256, the frames whose hashes differ are scored
    too: `settings` is the production's settings (`None` is none), `cwd` the directory the metric runs in
    (`None` is the current one).
    """
    runs = [first.rstrip("/"), second.rstrip("/")]
    problems: list[str] = []
    loaded = {}
    for name, check in (("render.json", _check_render), ("state.json", _check_state)):
        for run in runs:
            label = f"{run}/{name}"
            value = _load(label, problems)
            if value is not None:
                check(label, value, problems)
            loaded[run, name] = value
    if problems:
        raise ReproduceError(problems)
    render_a, render_b = (loaded[run, "render.json"] for run in runs)
    state_a, state_b = (loaded[run, "state.json"] for run in runs)
    lines = [f"render.json {line}" for line in differences({k: render_a[k] for k in RENDER_KEYS},
                                                             {k: render_b[k] for k in RENDER_KEYS})]
    if frames is None:
        note = _note(render_a["frames_sha256"], render_b["frames_sha256"])
    else:
        frame_lines, note = _frames(render_a, render_b, frames, settings or {}, cwd if cwd is not None else ".")
        lines += frame_lines
    lines += [f"state.json {line}" for line in differences(state_a, state_b)]
    return lines, note
