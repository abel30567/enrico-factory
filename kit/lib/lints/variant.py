"""Variant: the platform gate. A variant is cut for one platform and kind and is checked against that
kind's entry of `rulebook/<platform>.json` only (brief section 16, "Rulebook").

Rule ids: variant, duration, aspect, safe-zone, captions, disclosure, policy, format, text. A record is
`post/<ep>/<platform>/<kind>/variant.json`, written by the variant's gated job: its packaging, `aspect`,
`file` (the rendered file as `ffprobe` measured it), `captions` (the burned-in cues, in seconds of the
file, each `box` as `corner.box`) and `upload` (each upload field, named as the rulebook names it, to
its value). Findings are on line 1. A rulebook value that is null or absent is ERROR, never PASS.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..lint import Finding, LintError, files, read_json
from .brand import ASPECT, RULEBOOK, SIDES, VARIANT, _box, _number

KEYS = ("container", "codec", "aspects", "resolution", "min_fps", "max_fps", "min_seconds", "max_seconds",
        "max_bytes", "caption_zone", "title_max_chars", "caption_max_chars")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
TEXT_LIMITS = (("title", "title_max_chars"), ("caption", "caption_max_chars"))


def _integer(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _same(a, b) -> bool:
    """Equal values of the same JSON type: `1` is not `true`."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if _number(a) or _number(b):
        return _number(a) and _number(b) and a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    return type(a) is type(b) and a == b


def _rulebook(root: Path, platform: str, kind: str) -> tuple[dict, dict, list]:
    """(the kind's values by key, disclosure, policy), or LintError on the first that fails."""
    rel = RULEBOOK.format(platform)
    if not (root / rel).is_file():
        raise LintError(f"{rel} does not exist")
    _, data = read_json(root, rel)
    kinds = data.get("kinds") if isinstance(data, dict) else None
    if not isinstance(kinds, dict) or not isinstance(kinds.get(kind), dict):
        raise LintError(f"{rel} has no kind {kind!r}")
    values = {}
    for key in KEYS:
        entry = kinds[kind].get(key)
        values[key] = entry.get("value") if isinstance(entry, dict) else None
        if values[key] is None:
            raise LintError(f"{rel}: kinds.{kind}.{key} has no value")
    disclosure = data.get("disclosure")
    if (not isinstance(disclosure, dict) or not isinstance(disclosure.get("field"), str)
            or disclosure.get("value") is None):
        raise LintError(f"{rel}: disclosure has no value")
    policy = data.get("policy")
    if not isinstance(policy, list) or not all(
            isinstance(p, dict) and isinstance(p.get("field"), str) and p.get("value") is not None for p in policy):
        raise LintError(f"{rel}: policy is not a list of values with a field")
    return values, disclosure, policy


def _for_aspect(platform: str, kind: str, key: str, value, aspect: str, valid):
    got = value.get(aspect) if isinstance(value, dict) else None
    if not valid(got):
        raise LintError(f"{RULEBOOK.format(platform)}: kinds.{kind}.{key} has no value for {aspect}")
    return got


def _file(value) -> bool:
    return (isinstance(value, dict) and isinstance(value.get("sha256"), str) and bool(SHA256.match(value["sha256"]))
            and isinstance(value.get("container"), str) and isinstance(value.get("codec"), str)
            and all(_integer(value.get(k)) for k in ("bytes", "width", "height"))
            and all(_number(value.get(k)) for k in ("fps", "seconds")))


def _cue(value) -> bool:
    return (isinstance(value, dict) and isinstance(value.get("text"), str) and _number(value.get("start"))
            and _number(value.get("end")) and _box(value.get("box")))


def _variant(root: Path, rel: str, platform: str, kind: str) -> list[Finding]:
    _, data = read_json(root, rel)
    if not isinstance(data, dict):
        return [Finding(rel, 1, "variant", "the variant is not a JSON object")]
    values, disclosure, policy = _rulebook(root, platform, kind)
    book = RULEBOOK.format(platform)
    where = f"({book} {kind})"
    found = []

    def at_line_1(rule, message):
        found.append(Finding(rel, 1, rule, message))

    file = data.get("file")
    if not _file(file):
        at_line_1("variant", "file is not {sha256, bytes, container, codec, width, height, fps, seconds}")
        file = None
    aspect = data.get("aspect")
    if not isinstance(aspect, str) or not ASPECT.match(aspect):
        at_line_1("variant", "aspect is not a string such as '9:16'")
        aspect = None
    captions = data.get("captions")
    if not isinstance(captions, list) or not all(_cue(c) for c in captions):
        at_line_1("variant", "captions is not a list of {text, start, end, box}")
        captions = None
    upload = data.get("upload")
    if not isinstance(upload, dict):
        at_line_1("variant", "upload is not an object")
        upload = None

    if file is not None:
        low, high = values["min_seconds"], values["max_seconds"]
        if not low <= file["seconds"] <= high:
            at_line_1("duration", f"file runs {file['seconds']} s, outside [{low}, {high}] {where}")
        if file["container"] != values["container"]:
            at_line_1("format", f"container is {file['container']!r}, not {values['container']!r} {where}")
        if file["codec"] != values["codec"]:
            at_line_1("format", f"codec is {file['codec']!r}, not {values['codec']!r} {where}")
        low, high = values["min_fps"], values["max_fps"]
        if not low <= file["fps"] <= high:
            at_line_1("format", f"frame rate {file['fps']} is outside [{low}, {high}] {where}")
        if file["bytes"] > values["max_bytes"]:
            at_line_1("format", f"file has {file['bytes']} bytes, more than {values['max_bytes']} {where}")

    if aspect is not None:
        if aspect not in values["aspects"]:
            at_line_1("aspect", f"aspect is {aspect}, not one of {', '.join(values['aspects'])} {where}")
        else:
            w, h = _for_aspect(platform, kind, "resolution", values["resolution"], aspect,
                               lambda v: isinstance(v, list) and len(v) == 2 and all(_integer(n) for n in v))
            z = _for_aspect(platform, kind, "caption_zone", values["caption_zone"], aspect,
                            lambda v: isinstance(v, dict) and all(_number(v.get(side)) for side in SIDES))
            if file is not None and (file["width"], file["height"]) != (w, h):
                at_line_1("aspect", f"file is {file['width']}x{file['height']}, not {w}x{h} for {aspect} {where}")
            for n, cue in enumerate(captions or [], 1):
                left, top, right, bottom = cue["box"]
                if not (left >= z["left"] and top >= z["top"] and right <= 1 - z["right"]
                        and bottom <= 1 - z["bottom"]):
                    at_line_1("safe-zone", f"caption {n} {cue['box']} is outside the caption zone of "
                                           f"{book} ({kind}, {aspect})")

    if captions is not None:
        if not captions:
            at_line_1("captions", "the variant has no captions")
        for n, cue in enumerate(captions, 1):
            if not cue["text"].strip():
                at_line_1("captions", f"caption {n} has no text")
            if file is not None and not 0 <= cue["start"] < cue["end"] <= file["seconds"]:
                at_line_1("captions", f"caption {n} runs from {cue['start']} to {cue['end']} s, "
                                      f"outside the file's {file['seconds']} s")

    if upload is not None:
        for rule, entry in [("disclosure", disclosure)] + [("policy", p) for p in policy]:
            field, want = entry["field"], entry["value"]
            if field not in upload:
                at_line_1(rule, f"upload does not set {field} ({book} {rule})")
            elif not _same(upload[field], want):
                at_line_1(rule, f"upload sets {field} to {json.dumps(upload[field])}, not {json.dumps(want)} "
                                f"({book} {rule})")

    for key, limit in TEXT_LIMITS:
        text = data.get(key)
        if isinstance(text, str) and len(text) > values[limit]:
            at_line_1("text", f"{key} has {len(text)} characters, more than {values[limit]} {where}")
    return sorted(set(found))


def check(root: Path) -> list[Finding]:
    found = []
    for path in files(root):
        rel = path.as_posix()
        m = VARIANT.match(rel)
        if m:
            found += _variant(root, rel, m.group(1), m.group(2))
    return found
