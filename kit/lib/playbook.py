"""Playbook lessons: `playbook/PB-<n>.md`, a lesson with evidence, a confidence and an expiry date (brief
section 7, "Playbook").

Front matter (`frontmatter.parse`) holds exactly `KEYS`: `confidence` one of `CONFIDENCE`; `evidence` a
comma-separated list of `<path>:<line>` or `#<n>` items, with no space before a `#` (a `#` after white space
starts a comment); `expires` the last day the lesson holds, `YYYY-MM-DD`. Then the lesson text. Only files
directly under `DIR` ending in `.md` are lessons. No clock, git or GitHub is read here: the lint and
`factory playbook` share it.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import PurePosixPath

from . import frontmatter

DIR = "playbook"
KEYS = ("confidence", "evidence", "expires")
CONFIDENCE = ("low", "medium", "high")
NAME_MESSAGE = "is not named PB-<n>.md"
_NAME = re.compile(r"PB-([1-9][0-9]*)\.md")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_LINE = re.compile(r"([^\s:]+):([1-9][0-9]*)")
_REF = re.compile(r"#[1-9][0-9]*")


def number(path) -> int | None:
    """The `<n>` of a path named `PB-<n>.md`, else None."""
    m = _NAME.fullmatch(PurePosixPath(str(path)).name)
    return int(m.group(1)) if m else None


def lesson_paths(paths) -> list[str]:
    """The paths directly under `DIR` ending in `.md`, as posix strings: by number, misnamed ones last by name."""
    out = []
    for path in paths:
        p = PurePosixPath(str(path))
        if len(p.parts) == 2 and p.parts[0] == DIR and p.name.endswith(".md"):
            out.append(p.as_posix())
    return sorted(out, key=lambda p: (number(p) is None, number(p) or 0, p))


def _date(value) -> date | None:
    if not value or not _DATE.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def expires(text) -> date | None:
    """The lesson's valid `expires` date, or None when the front matter or the date is not well formed."""
    try:
        meta, _ = frontmatter.parse(text)
    except frontmatter.FrontMatterError:
        return None
    return _date(meta.get("expires"))


def expired(text, today) -> bool:
    """True when the lesson's expiry date is before ``today``; a lesson holds on its expiry date."""
    last = expires(text)
    return last is not None and last < today


def _evidence(item, line_count) -> list[str]:
    if _REF.fullmatch(item):
        return []
    m = _LINE.fullmatch(item)
    if not m:
        return [f"evidence '{item}' is not <path>:<line> or #<n>"]
    path, line = m.group(1), int(m.group(2))
    count = line_count(path)
    if count is None:
        return [f"evidence {item}: {path} is not in the tree"]
    if line > count:
        return [f"evidence {item}: {path} has {count} line(s)"]
    return []


def problems(path, text, line_count) -> list[str]:
    """Every problem of the lesson ``text`` at ``path``; ``line_count(rel)`` is a file's line count, or None
    for a path not in the tree."""
    name = PurePosixPath(str(path)).name
    if number(path) is None:
        return [f"{name} {NAME_MESSAGE}"]
    try:
        meta, body = frontmatter.parse(text)
    except frontmatter.FrontMatterError as e:
        return [f"front matter: {str(e).split(': ', 1)[1]}"]
    items = frontmatter.as_list(meta.get("evidence"))
    out = [f"no {key}" for key in KEYS if not (items if key == "evidence" else meta.get(key))]
    out += [f"unknown key {key}" for key in sorted(meta) if key not in KEYS]
    if meta.get("confidence") and meta["confidence"] not in CONFIDENCE:
        out.append("confidence is not low, medium or high")
    if meta.get("expires") and _date(meta["expires"]) is None:
        out.append("expires is not a date YYYY-MM-DD")
    for item in items:
        out += _evidence(item, line_count)
    if not body.strip():
        out.append("no lesson text after the front matter")
    return out
