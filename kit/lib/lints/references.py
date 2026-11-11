"""References: every relative link, JSON path and media id points at something that exists."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from ..lint import (Finding, LintError, files, json_strings, link_targets, markdown_lines,
                    read_json, read_text, read_tsv)

MANIFEST = "media/manifest.tsv"
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
MEDIA = re.compile(r"^media:(.+)$")
JSON_KEYS = ("path", "source", "ref")


def _exists(root: Path, rel: str, target: str) -> bool:
    """The target exists inside root, resolved against the file's directory, then the root."""
    target = unquote(target.split("#", 1)[0].split("?", 1)[0])
    if not target:
        return True
    top = root.resolve()
    bases = [top] if target.startswith("/") else [top / PurePosixPath(rel).parent, top]
    for base in bases:
        path = (base / target.lstrip("/")).resolve()
        if path.is_relative_to(top) and path.exists():
            return True
    return False


def _manifest_ids(root: Path) -> set[str] | None:
    if not (root / MANIFEST).is_file():
        return None
    rows = read_tsv(root, MANIFEST)
    if not rows or "id" not in rows[0][1]:
        raise LintError(f"{MANIFEST} has no id column")
    col = rows[0][1].index("id")
    return {fields[col].strip() for _, fields in rows[1:] if col < len(fields)}


def check(root: Path) -> list[Finding]:
    found = []
    cited = []  # (path, line, media id)
    for rel in (p.as_posix() for p in files(root)):
        if rel.endswith(".md"):
            for n, line in markdown_lines(read_text(root, rel)):
                for target in link_targets(line):
                    m = MEDIA.match(target)
                    if m:
                        cited.append((rel, n, m.group(1)))
                    elif not target.startswith("#") and not SCHEME.match(target) \
                            and not _exists(root, rel, target):
                        found.append(Finding(rel, n, "link-target", f"link target {target} does not exist"))
        elif rel.endswith(".json"):
            raw, _ = read_json(root, rel)
            for n, key, value in json_strings(raw)[0]:
                m = MEDIA.match(value)
                if m:
                    cited.append((rel, n, m.group(1)))
                elif key in JSON_KEYS and value and not SCHEME.match(value) \
                        and not _exists(root, rel, value):
                    found.append(Finding(rel, n, "json-path", f"{key} {value} does not exist"))
    if cited:
        ids = _manifest_ids(root)
        if ids is None:
            raise LintError(f"media ids are cited but {MANIFEST} does not exist")
        found += [Finding(rel, n, "media-id", f"media:{mid} is not in {MANIFEST}")
                  for rel, n, mid in cited if mid not in ids]
    return found
