"""Rights: every source is listed with a basis, every canon sheet's origin is covered, no listed
third-party name appears in the production's work, and every speaker has a canon sheet (brief rule 8,
sections 10 and 16).

Rule ids: rights-row, origin, not-ours, speakers. `sources/rights.tsv` has the columns `path`, `work`,
`holder`, `basis`, `note`; a row's `path` is one glob in the syntax of a role's `writes`, a path under
`sources/` or a media id such as `media:lighthouse-*`. A canon sheet's `origin: source <file or media id>`
needs a row that covers it; `origin: house A-<n>` needs `bible/amendments/A-<n>.json`. The names of
`research/not-ours.tsv` are matched as whole words, case ignored, in every file under `CHECKED`;
`bible/charter.md`, `research/**` and `sources/**` are not read for names. A file that does not exist
is not checked.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..lint import Finding, files, read_text, read_tsv
from ..production import matches
from .structure import AMENDMENT_ID

RIGHTS = "sources/rights.tsv"
NOT_OURS = "research/not-ours.tsv"
BASES = ("own", "licence", "permission", "public-domain")
CHECKED = ("episodes/", "bible/canon/", "bible/style/", "post/", "distribution/")
RIGHTS_COLUMNS = ("path", "work", "holder", "basis", "note")
ORIGIN_SHEET = re.compile(r"^bible/canon/(characters|places)/[^/]+\.md$")
LINES = re.compile(r"^episodes/[^/]+/lines\.json$")


def _never_read(path: str) -> bool:
    return path == "bible/charter.md" or path.startswith(("research/", "sources/"))


def _header(rows, wanted):
    """``(index of each wanted column, the missing ones)`` for the first row, fields trimmed."""
    names = [f.strip() for f in rows[0][1]]
    return {c: names.index(c) for c in wanted if c in names}, [c for c in wanted if c not in names]


def _field(fields, i):
    return fields[i].strip() if i < len(fields) else ""


def _rights(root: Path, have: set[str]):
    """``(the path globs of the rows, findings)``; no globs when there is no rights file."""
    if RIGHTS not in have:
        return [], []
    rows = read_tsv(root, RIGHTS)
    if not rows:
        return [], []
    cols, missing = _header(rows, RIGHTS_COLUMNS)
    if missing:
        return [], [Finding(RIGHTS, rows[0][0], "rights-row", f"header has no {' or '.join(missing)} column")]
    globs, found = [], []
    for n, fields in rows[1:]:
        globs.append(_field(fields, cols["path"]))
        if not _field(fields, cols["holder"]):
            found.append(Finding(RIGHTS, n, "rights-row", "holder is empty"))
        basis = _field(fields, cols["basis"])
        if basis not in BASES:
            found.append(Finding(RIGHTS, n, "rights-row",
                                 f"basis '{basis}' is not own, licence, permission or public-domain"))
    return globs, found


def _origin(rel: str, text: str, globs, have: set[str]) -> list[Finding]:
    for n, line in enumerate(text.splitlines(), 1):
        if not line.startswith("origin:"):
            continue
        words = line[len("origin:"):].split()
        if not words or words[0] not in ("source", "house"):
            return []
        kind, cited = words[0], words[1] if len(words) > 1 else None
        if kind == "source":
            if cited is None:
                return [Finding(rel, n, "origin", "origin: source cites no file or media id")]
            if not matches(cited, globs):
                return [Finding(rel, n, "origin", f"{cited} is not covered by a row of {RIGHTS}")]
        elif cited is None:
            return [Finding(rel, n, "origin", "origin: house names no amendment")]
        elif not AMENDMENT_ID.fullmatch(cited):
            return [Finding(rel, n, "origin", f"{cited} is not the id A-<n> of an amendment")]
        elif f"bible/amendments/{cited}.json" not in have:
            return [Finding(rel, n, "origin", f"amendment {cited}: bible/amendments/{cited}.json is missing")]
        return []
    return []


def _names(rows):
    """``(the names of the not-ours list as (name, line, pattern), the line of a header with no name column)``."""
    if not rows:
        return [], None
    cols, missing = _header(rows, ("name",))
    if missing:
        return [], rows[0][0]
    out = []
    for n, fields in rows[1:]:
        name = _field(fields, cols["name"])
        if name:
            out.append((name, n, re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)", re.IGNORECASE)))
    return out, None


def _speakers(rel: str, text: str, have: set[str]) -> list[Finding]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    speakers = sorted({e["speaker"] for e in data if isinstance(e, dict) and isinstance(e.get("speaker"), str)})
    return [Finding(rel, 1, "speakers", f"speaker {s} has no bible/canon/characters/{s.strip().lower()}.md")
            for s in speakers if f"bible/canon/characters/{s.strip().lower()}.md" not in have]


def check(root: Path) -> list[Finding]:
    root = Path(root)
    have = {p.as_posix() for p in files(root)}
    globs, found = _rights(root, have)
    for rel in sorted(have):
        if ORIGIN_SHEET.match(rel):
            found += _origin(rel, read_text(root, rel), globs, have)
        elif LINES.match(rel):
            found += _speakers(rel, read_text(root, rel), have)
    listed = []
    if NOT_OURS in have:
        listed, no_name = _names(read_tsv(root, NOT_OURS))
        if no_name is not None:
            found.append(Finding(NOT_OURS, no_name, "not-ours", "header has no name column"))
    for rel in sorted(have) if listed else ():
        if not rel.startswith(CHECKED) or _never_read(rel):
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            found += [Finding(rel, n, "not-ours", f"'{name}' is on {NOT_OURS}:{at}")
                      for name, at, rx in listed if rx.search(line)]
    return found


def refusal(text_of, list_text, inputs):
    """The reason a job whose ``inputs`` name a listed name is refused, or None.

    ``text_of(path)`` is the text of an input at the job's ref, None when there is none; ``list_text`` is
    the text of the not-ours list on the base, None when there is none.
    """
    if list_text is None:
        return None
    rows = [(n, row.split("\t")) for n, row in enumerate(list_text.splitlines(), 1) if row.strip()]
    listed, no_name = _names(rows)
    if no_name is not None:
        return f"{NOT_OURS} has no name column"
    for item in inputs:
        path = item["path"]
        if not listed or _never_read(path):
            continue
        text = text_of(path)
        if text is None:
            continue
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            continue
        for name, at, rx in listed:
            if rx.search(text):
                return f"input {path} names '{name}' ({NOT_OURS}:{at})"
    return None
