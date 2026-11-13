"""Brand: variant packaging spells the brand's terms and handle, carries its hashtags, avoids its never
list, shows its end card and corner mark, and burns captions in its palette."""

from __future__ import annotations

import re
from pathlib import Path

from ..lint import Finding, LintError, files, read_json
from .spoiler import _packaging

BRAND = "brand/brand.json"
VARIANT = re.compile(r"^post/[^/]+/([^/]+)/([^/]+)/variant\.json$")
RULEBOOK = "rulebook/{}.json"
CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
ASPECT = re.compile(r"^[0-9]+:[0-9]+$")
HANDLE = re.compile(r"@\w+(?:\.\w+)*")
HASHTAG = re.compile(r"#\w+")
WORD = re.compile(r"\w+(?:-\w+)*")
SPELLED = frozenset({"title", "caption", "cta"})
TAGGED = frozenset({"title", "caption", "cta", "description"})
SIDES = ("left", "top", "right", "bottom")


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _strings(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _brand(root: Path) -> dict:
    _, data = read_json(root, BRAND)

    def fail(key):
        raise LintError(f'{BRAND}: {key} is not as brief section 16, "Brand kit", gives it')

    if not isinstance(data, dict):
        fail("the file")
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        fail("name")
    marks = data.get("marks")
    if not isinstance(marks, dict) or not all(isinstance(v, str) for v in marks.values()):
        fail("marks")
    palette = data.get("palette")
    if not isinstance(palette, dict) or not all(isinstance(v, str) and COLOR.match(v) for v in palette.values()):
        fail("palette")
    corner = data.get("corner")
    if not isinstance(corner, dict) or corner.get("mark") not in marks or corner.get("corner") not in CORNERS:
        fail("corner")
    end_card = data.get("end_card")
    duration = end_card.get("duration") if isinstance(end_card, dict) else None
    if (not isinstance(end_card, dict) or end_card.get("mark") not in marks or not isinstance(duration, list)
            or len(duration) != 2 or not all(_number(d) for d in duration) or duration[0] > duration[1]):
        fail("end_card")
    words = data.get("words")
    if not isinstance(words, dict) or not _strings(words.get("exact")) or not _strings(words.get("never")):
        fail("words")
    platforms = data.get("platforms")
    if not isinstance(platforms, dict) or not all(
            isinstance(p, dict) and isinstance(p.get("handle"), str) and _strings(p.get("hashtags"))
            for p in platforms.values()):
        fail("platforms")
    return data


def _distance(a: str, b: str) -> int:
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1]


def _tolerance(term: str) -> int:
    return 0 if len(term) <= 4 else 1 if len(term) <= 7 else 2


def _tokens(text: str) -> tuple[list[str], list[str], list[str]]:
    """(handles, hashtags, words) of a packaging string."""
    handles = HANDLE.findall(text)
    rest = HANDLE.sub(" ", text)
    hashtags = HASHTAG.findall(rest)
    return handles, hashtags, WORD.findall(HASHTAG.sub(" ", rest))


def _runs(words: list[str], n: int) -> list[list[str]]:
    return [words[i:i + n] for i in range(len(words) - n + 1)]


def _spelling(key, handles, words, terms, handle) -> list[tuple[str, str]]:
    out = []
    for term in terms:
        parts = WORD.findall(term)
        allowed = {term, term[:1].upper() + term[1:]}
        for run in _runs(words, len(parts)):
            found = " ".join(run)
            if len(parts) == 1:
                close = _distance(found.lower(), term.lower()) <= _tolerance(term)
            else:
                close = [w.lower() for w in run] == [p.lower() for p in parts]
            if close and found not in allowed:
                out.append(("spelling", f"{key} spells {term!r} as {found!r}"))
    if handle is not None:
        out += [("spelling", f"{key} spells {handle!r} as {h!r}") for h in handles
                if h != handle and _distance(h.lower(), handle.lower()) <= _tolerance(handle)]
    return out


def _never(key, hashtags, words, never) -> list[tuple[str, str]]:
    out = []
    for entry in never:
        parts = [p.lower() for p in WORD.findall(entry)]
        in_words = key in SPELLED and parts and any(
            [w.lower() for w in run] == parts for run in _runs(words, len(parts)))
        in_tags = any(tag[1:].lower() == entry.lower() for tag in hashtags)
        if in_words or in_tags:
            out.append(("never", f"{key} uses {entry!r} ({BRAND} words.never)"))
    return out


def _box(value) -> bool:
    return (isinstance(value, list) and len(value) == 4 and all(_number(v) and 0 <= v <= 1 for v in value)
            and value[0] < value[2] and value[1] < value[3])


def _zone(root: Path, platform: str, kind: str, aspect: str) -> dict:
    rel = RULEBOOK.format(platform)
    if not (root / rel).is_file():
        raise LintError(f"{rel} does not exist")
    _, data = read_json(root, rel)
    kinds = data.get("kinds") if isinstance(data, dict) else None
    if not isinstance(kinds, dict) or not isinstance(kinds.get(kind), dict):
        raise LintError(f"{rel} has no kind {kind!r}")
    mark_zone = kinds[kind].get("mark_zone")
    value = mark_zone.get("value") if isinstance(mark_zone, dict) else None
    if value is None:
        raise LintError(f"{rel}: kinds.{kind}.mark_zone has no value")
    zone = value.get(aspect) if isinstance(value, dict) else None
    if not isinstance(zone, dict) or not all(_number(zone.get(side)) for side in SIDES):
        raise LintError(f"{rel}: kinds.{kind}.mark_zone has no value for {aspect}")
    return zone


def _variant(root: Path, rel: str, platform: str, kind: str, brand: dict) -> list[Finding]:
    _, data = read_json(root, rel)
    if not isinstance(data, dict):
        return [Finding(rel, 1, "variant", "the variant is not a JSON object")]
    found = []

    def at_line_1(rule, message):
        found.append(Finding(rel, 1, rule, message))

    target = brand["platforms"].get(platform)
    if target is None:
        at_line_1("platform", f"{BRAND} has no platform {platform!r}")
    handle = target["handle"] if target else None
    terms = list(dict.fromkeys([brand["name"], *brand["words"]["exact"]]))
    tags = set()
    for line, key, text in _packaging(root, rel):
        if key not in TAGGED:
            continue
        handles, hashtags, words = _tokens(text)
        tags.update(tag.lower() for tag in hashtags)
        messages = _spelling(key, handles, words, terms, handle) if key in SPELLED else []
        messages += _never(key, hashtags, words, brand["words"]["never"])
        found += [Finding(rel, line, rule, message) for rule, message in messages]
    if target:
        for tag in target["hashtags"]:
            if tag.lower() not in tags:
                at_line_1("hashtag", f"hashtag {tag!r} is missing ({BRAND} platforms.{platform}.hashtags)")

    end_card, mark = data.get("end_card"), brand["end_card"]["mark"]
    if not isinstance(end_card, dict) or end_card.get("mark") != mark:
        at_line_1("end-card", f"end card {mark!r} is missing")
    elif not _number(end_card.get("seconds")):
        at_line_1("variant", "end_card.seconds is not a number")
    else:
        low, high = brand["end_card"]["duration"]
        if not low <= end_card["seconds"] <= high:
            at_line_1("end-card", f"end card lasts {end_card['seconds']} s, outside [{low}, {high}] "
                                  f"({BRAND} end_card.duration)")

    corner, mark, where = data.get("corner"), brand["corner"]["mark"], brand["corner"]["corner"]
    if not isinstance(corner, dict) or corner.get("mark") != mark:
        at_line_1("corner", f"corner mark {mark!r} is missing")
    elif not _box(corner.get("box")):
        at_line_1("variant", "corner.box is not [left, top, right, bottom] in fractions of the frame")
    else:
        box = corner["box"]
        left, top, right, bottom = box
        at = ("top" if (top + bottom) / 2 < 0.5 else "bottom") + "-" + ("left" if (left + right) / 2 < 0.5 else "right")
        if at != where:
            at_line_1("corner", f"corner mark is in the {at} corner, not {where}")
        aspect = data.get("aspect")
        if not isinstance(aspect, str) or not ASPECT.match(aspect):
            at_line_1("variant", "aspect is not a string such as '9:16'")
        else:
            z = _zone(root, platform, kind, aspect)
            if not (left >= z["left"] and top >= z["top"] and right <= 1 - z["right"]
                    and bottom <= 1 - z["bottom"]):
                at_line_1("corner", f"corner mark {box} is outside the mark zone of "
                                    f"{RULEBOOK.format(platform)} ({kind}, {aspect})")

    if "caption_colors" in data:
        colors = data["caption_colors"]
        if not isinstance(colors, list) or not all(isinstance(c, str) and COLOR.match(c) for c in colors):
            at_line_1("variant", "caption_colors is not a list of #rrggbb colors")
        else:
            palette = {v.lower() for v in brand["palette"].values()}
            for color in colors:
                if color.lower() not in palette:
                    at_line_1("palette", f"caption color {color!r} is not in the palette ({BRAND} palette)")
    return sorted(set(found))


def check(root: Path) -> list[Finding]:
    if not (root / BRAND).is_file():
        return []
    brand = _brand(root)
    found = []
    for path in files(root):
        rel = path.as_posix()
        m = VARIANT.match(rel)
        if m:
            found += _variant(root, rel, m.group(1), m.group(2), brand)
    return found
