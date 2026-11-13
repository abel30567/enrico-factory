"""Calibration: every row of the judge calibration set is well formed and has a post and a beat ID behind it
(brief section 7, "Judge calibration set").

Rule ids: calibration-header, calibration-row, calibration-post, calibration-beat. `calibration/retention.tsv`
has the columns `post`, `post_id`, `beat`, `start`, `end`, `retention`, `snapshot_sha256`. A row's `post`
needs a row of `registry/posts.tsv` with its `post_id` (a post has every `post_id` of its rows); its `beat`
needs an event of `episodes/<ep>/reel/edit-list.json` that names it. A tree with no calibration file is not
checked.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from ..calibration import HEADER, HEADER_MESSAGE, NUMBER, PATH, POST, SHA256
from ..lint import Finding, files, read_tsv

REGISTRY = "registry/posts.tsv"


def _posts(root: Path, have: set[str]) -> dict[str, set[str]]:
    """Every `post_id` of each post of the registry; a line whose cell count is not the header's is skipped."""
    if REGISTRY not in have:
        return {}
    rows = read_tsv(root, REGISTRY)
    if not rows:
        return {}
    names = rows[0][1]
    if "post" not in names or "post_id" not in names:
        return {}
    p, i = names.index("post"), names.index("post_id")
    out: dict[str, set[str]] = {}
    for _, fields in rows[1:]:
        if len(fields) == len(names):
            out.setdefault(fields[p], set()).add(fields[i])
    return out


def _beats(root: Path, have: set[str], ep: str) -> set[str]:
    """The beat IDs named by the events of the episode's edit list; none when it is not such an object."""
    rel = f"episodes/{ep}/reel/edit-list.json"
    if rel not in have:
        return set()
    try:
        data = json.loads((root / rel).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return set()
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        return set()
    return {e["beat"] for e in data["events"] if isinstance(e, dict) and isinstance(e.get("beat"), str)}


def check(root: Path) -> list[Finding]:
    root = Path(root)
    have = {p.as_posix() for p in files(root)}
    if PATH not in have:
        return []
    rows = read_tsv(root, PATH)
    if not rows or tuple(rows[0][1]) != HEADER:
        return [Finding(PATH, rows[0][0] if rows else 1, "calibration-header", HEADER_MESSAGE)]
    posts, beats, first, found = _posts(root, have), {}, {}, []
    for n, fields in rows[1:]:
        if len(fields) != len(HEADER):
            found.append(Finding(PATH, n, "calibration-row",
                                 f"row does not have the {len(HEADER)} cells of the header"))
            continue
        post, post_id, beat, start, end, retention, sha = fields
        if not POST.fullmatch(post):
            found.append(Finding(PATH, n, "calibration-row", f"post {post} is not <ep>/<platform>"))
            continue
        bad = []
        if not NUMBER.fullmatch(start):
            bad.append(f"start {start} is not a number of seconds")
        if not NUMBER.fullmatch(end):
            bad.append(f"end {end} is not a number of seconds")
        if NUMBER.fullmatch(start) and NUMBER.fullmatch(end) and Decimal(end) <= Decimal(start):
            bad.append(f"end {end} is not after start {start}")
        if retention != "-" and not NUMBER.fullmatch(retention):
            bad.append(f"retention {retention} is not a number or -")
        if not SHA256.fullmatch(sha):
            bad.append(f"snapshot_sha256 {sha} is not 64 lower-case hex characters")
        if (post, beat) in first:
            bad.append(f"beat {beat} of post {post} is repeated (first on line {first[post, beat]})")
        else:
            first[post, beat] = n
        found += [Finding(PATH, n, "calibration-row", m) for m in bad]
        if post not in posts:
            found.append(Finding(PATH, n, "calibration-post", f"post {post} has no row in {REGISTRY}"))
        elif post_id not in posts[post]:
            found.append(Finding(PATH, n, "calibration-post",
                                 f"post_id {post_id} is not a post_id of post {post} in {REGISTRY}"))
        ep = post.split("/")[0]
        if ep not in beats:
            beats[ep] = _beats(root, have, ep)
        if beat not in beats[ep]:
            found.append(Finding(PATH, n, "calibration-beat",
                                 f"beat {beat} is not a beat ID of episodes/{ep}/reel/edit-list.json"))
    return found
