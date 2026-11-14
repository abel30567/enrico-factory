"""The render reproducibility harness, exact half: two runs of one render job compared key by key.

A run is a directory holding `state.json` and `render.json` (brief section 16, "Production files"). The whole
`state.json` is compared; of `render.json` only `RENDER_KEYS`, because frames are not bit-exact between two
good runs (`frames_sha256`, `sequence_sha256`, `samples` and `rerender` may differ). The frame hashes are
counted for a note only. Two values are equal when they have the same JSON type and the same value.
"""

from __future__ import annotations

import json
from pathlib import Path

RENDER_KEYS = ("black", "count", "fps", "frames", "height", "nan", "text", "width")
RENDER_INTEGERS = ("count", "fps", "height", "width")
STATE_RENDER_BOOLEANS = ("lumen", "tsr")
STATE_RENDER_INTEGERS = ("spatial_samples", "temporal_samples", "warmup_frames", "width", "height")


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


def _note(a: list[str], b: list[str]) -> str:
    m = max(len(a), len(b))
    k = sum(1 for i in range(m) if i >= len(a) or i >= len(b) or a[i] != b[i])
    return f"note render.json frames_sha256: {k} of {m} frame hashes differ; frames are not compared exactly"


def compare(first: str, second: str) -> tuple[list[str], str]:
    """The difference lines of two runs, `render.json` lines first, and the note on the frame hashes."""
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
    lines += [f"state.json {line}" for line in differences(state_a, state_b)]
    return lines, _note(render_a["frames_sha256"], render_b["frames_sha256"])
