"""Playbook: every lesson `playbook/PB-<n>.md` has a confidence, evidence that exists and an expiry date
(brief section 7, "Playbook").

Rule id: lesson, on line 1 of the lesson. The shape is `kit/lib/playbook.py`. An evidence item
`<path>:<line>` needs a file of the tree with that line; `#<n>` is checked for its form only. The clock is
never read: a well-formed expired lesson passes (`factory playbook` drops it).
"""

from __future__ import annotations

from pathlib import Path

from .. import playbook
from ..lint import Finding, files, read_text


def check(root: Path) -> list[Finding]:
    root = Path(root)
    have = {p.as_posix() for p in files(root)}
    counts: dict[str, int] = {}

    def line_count(rel):
        if rel not in have:
            return None
        if rel not in counts:
            counts[rel] = len(read_text(root, rel).splitlines())
        return counts[rel]

    return [Finding(rel, 1, "lesson", message)
            for rel in playbook.lesson_paths(have)
            for message in playbook.problems(rel, read_text(root, rel), line_count)]
