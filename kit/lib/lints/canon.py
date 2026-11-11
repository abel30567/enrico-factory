"""Canon: the canon ledger does not contradict itself, and no episode ledger contradicts it."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..lint import Finding, LintError, json_strings, read_json, read_tsv, walk

CANON_LEDGER = "bible/canon/ledger.tsv"
EPISODE_LEDGER = re.compile(r"^episodes/[^/]+/ledger\.json$")
KEY_COLUMNS = ("subject", "attribute", "value")


def _norm(value) -> str | None:
    """A field compared after trimming and lower-casing; JSON numbers and booleans as written in JSON."""
    if isinstance(value, str):
        return value.strip().lower()
    if isinstance(value, (bool, int, float)):
        return json.dumps(value)
    return None


def _canon(root: Path) -> tuple[dict, list[Finding]]:
    """{(subject, attribute): [(line, normalised value, value)]} and the ledger's own contradictions."""
    rows = read_tsv(root, CANON_LEDGER)
    if not rows:
        return {}, []
    (hn, header), body = rows[0], rows[1:]
    header = [c.strip() for c in header]
    missing = [c for c in KEY_COLUMNS if c not in header]
    if missing:
        return {}, [Finding(CANON_LEDGER, hn, "canon-header", f"header has no {' or '.join(missing)} column")]
    cols = [header.index(c) for c in KEY_COLUMNS]
    canon, found = {}, []
    for n, fields in body:
        subject, attribute, value = (fields[i].strip() if i < len(fields) else "" for i in cols)
        key, norm = (subject.lower(), attribute.lower()), value.lower()
        for ln, other, raw in canon.get(key, []):
            if other != norm:
                found.append(Finding(CANON_LEDGER, n, "canon-self",
                                     f"{subject} / {attribute}: {value!r} contradicts {raw!r} "
                                     f"on line {ln}"))
                break
        canon.setdefault(key, []).append((n, norm, value))
    return canon, found


def _episode(root: Path, rel: str, canon: dict) -> list[Finding]:
    raw, data = read_json(root, rel)
    if not isinstance(data, list):
        return []  # the shape is provenance's finding
    entry_lines = iter(n for n, depth in json_strings(raw)[1] if depth == 1)
    found = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        line = next(entry_lines)
        subject, attribute, value = (_norm(entry.get(c)) for c in KEY_COLUMNS)
        if None in (subject, attribute, value):
            continue
        shown_subject, shown_attribute, shown_value = (str(entry[c]).strip() for c in KEY_COLUMNS)
        for ln, norm, canon_value in canon.get((subject, attribute), []):
            if norm != value:
                found.append(Finding(rel, line, "canon-contradiction",
                                     f"{shown_subject} / {shown_attribute}: episode says {shown_value!r}, "
                                     f"canon says {canon_value!r} ({CANON_LEDGER}:{ln})"))
    return found


def check(root: Path) -> list[Finding]:
    files = walk(root)
    episodes = [rel for rel in files if EPISODE_LEDGER.match(rel)]
    has_canon = CANON_LEDGER in files
    if episodes and not has_canon:
        raise LintError(f"episode ledgers present and no {CANON_LEDGER}")
    if not has_canon:
        return []
    canon, found = _canon(root)
    for rel in episodes:
        found += _episode(root, rel, canon)
    return found
