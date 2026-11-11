"""Spoiler and CTA: packaging text gives away no listed spoiler and matches no bait pattern."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..lint import Finding, LintError, json_strings, read_json, read_text, walk

PACKAGING = re.compile(r"^post/([^/]+)/.+\.json$")
PACKAGING_KEYS = frozenset({"title", "caption", "description", "cta", "hook"})
INTENT_PACKET = "episodes/{}/intent-packet.json"
BAIT_RULES = "rulebook/bait.json"
DEFAULT_BAIT = (
    # engagement bait
    r"\blike if\b",
    r"\bcomment below to\b",
    r"\btag a friend\b",
    r"\bshare this with\b",
    # false urgency
    r"\blast chance\b",
    r"\bbefore it['\u2019]?s deleted\b",
    # withheld-payoff bait
    r"\byou won['\u2019]?t believe\b",
    r"\bwait for it\b",
)


class _Pairs(list):
    """A JSON object as its (key, value) pairs in document order, duplicates kept."""


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _packaging(root: Path, rel: str) -> list[tuple[int, str, str]]:
    """(line, key, text) for every string under a packaging key, at any depth."""
    raw = read_text(root, rel)
    try:
        data = json.loads(raw, object_pairs_hook=_Pairs)
    except json.JSONDecodeError as e:
        raise LintError(f"cannot parse {rel}:{e.lineno}: {e.msg}") from None
    keys = []

    def visit(value, key):
        if isinstance(value, _Pairs):
            for k, v in value:
                visit(v, key or (k if k in PACKAGING_KEYS else None))
        elif isinstance(value, list):
            for v in value:
                visit(v, key)
        elif isinstance(value, str):
            keys.append(key)

    visit(data, None)
    return [(n, k, s) for (n, _, s), k in zip(json_strings(raw)[0], keys) if k is not None]


def _bait(root: Path) -> list[tuple[str, re.Pattern]]:
    if not (root / BAIT_RULES).is_file():
        sources = DEFAULT_BAIT
    else:
        _, data = read_json(root, BAIT_RULES)
        sources = data.get("patterns") if isinstance(data, dict) else None
        if not isinstance(sources, list) or not all(isinstance(p, str) for p in sources):
            raise LintError(f"{BAIT_RULES}: patterns must be a list of strings")
    try:
        return [(p, re.compile(p, re.IGNORECASE)) for p in sources]
    except re.error as e:
        raise LintError(f"{BAIT_RULES}: bad pattern {e.pattern!r}: {e}") from None


def _spoilers(root: Path, ep: str) -> list[tuple[str, re.Pattern]]:
    rel = INTENT_PACKET.format(ep)
    if not (root / rel).is_file():
        raise LintError(f"packaging text in post/{ep}/ and no {rel}")
    _, data = read_json(root, rel)
    phrases = data.get("spoilers", []) if isinstance(data, dict) else None
    if not isinstance(phrases, list) or not all(isinstance(p, str) for p in phrases):
        raise LintError(f"{rel}: spoilers must be a list of strings")
    out = []
    for phrase in phrases:
        words = _collapse(phrase)
        if words:
            out.append((words, re.compile(r"(?<!\w)" + re.escape(words) + r"(?!\w)", re.IGNORECASE)))
    return out


def check(root: Path) -> list[Finding]:
    texts = {}
    for rel in walk(root):
        m = PACKAGING.match(rel)
        if m:
            texts.setdefault(m.group(1), []).extend((rel, *t) for t in _packaging(root, rel))
    texts = {ep: rows for ep, rows in texts.items() if rows}
    if not texts:
        return []
    bait = _bait(root)
    found = []
    for ep, rows in sorted(texts.items()):
        spoilers = _spoilers(root, ep)
        for rel, line, key, text in rows:
            words = _collapse(text)
            found += [Finding(rel, line, "spoiler", f"{key} gives away {phrase!r} "
                                                    f"({INTENT_PACKET.format(ep)})")
                      for phrase, rx in spoilers if rx.search(words)]
            found += [Finding(rel, line, "bait", f"{key} matches bait pattern /{source}/")
                      for source, rx in bait if rx.search(words)]
    return found
