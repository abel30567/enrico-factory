"""Provenance: every factual line in research, bible and episode files carries a source tag."""

from __future__ import annotations

import re
from pathlib import Path

from ..lint import Finding, json_strings, markdown_lines, read_json, read_text, read_tsv, walk

DIRS = ("research/", "bible/", "episodes/")
TAGS = ("text", "art", "inferred", "invented")
ANY_TAG = re.compile(r"\[(text|art|inferred|invented)\]")
SOURCED_TAG = re.compile(r"\[(text|art)\](\(([^)]*)\))?")
FORM = {"text": "sources/", "art": "media:"}
LIST_ITEM = re.compile(r"^\s*([-*+]|\d+[.)])\s+\S")
TABLE_SEP = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
EPISODE_LEDGER = re.compile(r"^episodes/[^/]+/ledger\.json$")
CANON_LEDGER = "bible/canon/ledger.tsv"


def _markdown(root: Path, rel: str) -> list[Finding]:
    found = []
    header = in_body = False
    for n, line in markdown_lines(read_text(root, rel)):
        is_table = line.lstrip().startswith("|")
        if is_table and header and TABLE_SEP.match(line):
            header, in_body = False, True
            continue
        in_body = in_body and is_table
        header = is_table and not in_body
        if in_body or LIST_ITEM.match(line):
            if not ANY_TAG.search(line):
                kind = "table row" if is_table else "list item"
                found.append(Finding(rel, n, "tag-missing",
                                     f"{kind} has none of [text], [art], [inferred], [invented]"))
        for m in SOURCED_TAG.finditer(line):
            tag, target = m.group(1), m.group(3)
            if target is None or not target.startswith(FORM[tag]) or target == FORM[tag]:
                found.append(Finding(rel, n, "tag-source",
                                     f"[{tag}] must be a link: [{tag}]({FORM[tag]}...)"))
    return found


def _entry(rel: str, n: int, tag, source) -> list[Finding]:
    if tag not in TAGS:
        return [Finding(rel, n, "ledger-tag", f"tag {tag!r} is not one of {', '.join(TAGS)}")]
    if tag in FORM and not (isinstance(source, str) and source.strip()):
        return [Finding(rel, n, "ledger-source", f"tag {tag!r} needs a non-empty source")]
    return []


def _episode_ledger(root: Path, rel: str) -> list[Finding]:
    raw, data = read_json(root, rel)
    if not isinstance(data, list):
        return [Finding(rel, 1, "ledger-shape", "ledger must be a list of objects")]
    entry_lines = [n for n, depth in json_strings(raw)[1] if depth == 1]
    found, objects = [], iter(entry_lines)
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            found.append(Finding(rel, 1, "ledger-shape", f"entry {i} is not an object"))
            continue
        found += _entry(rel, next(objects), entry.get("tag"), entry.get("source"))
    return found


def _canon_ledger(root: Path, rel: str) -> list[Finding]:
    rows = read_tsv(root, rel)
    if not rows:
        return [Finding(rel, 1, "ledger-shape", "ledger has no header row")]
    (hn, header), body = rows[0], rows[1:]
    missing = [c for c in ("tag", "source") if c not in header]
    if missing:
        return [Finding(rel, hn, "ledger-shape", f"header has no {' or '.join(missing)} column")]
    t, s = header.index("tag"), header.index("source")
    found = []
    for n, fields in body:
        cell = lambda i: fields[i].strip() if i < len(fields) else ""
        found += _entry(rel, n, cell(t), cell(s))
    return found


def check(root: Path) -> list[Finding]:
    found = []
    for rel in walk(root):
        if rel.endswith(".md") and rel.startswith(DIRS):
            found += _markdown(root, rel)
        elif EPISODE_LEDGER.match(rel):
            found += _episode_ledger(root, rel)
        elif rel == CANON_LEDGER:
            found += _canon_ledger(root, rel)
    return found
