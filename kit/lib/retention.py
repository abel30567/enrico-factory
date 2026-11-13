"""Retention per beat ID for one live post: the snapshot's curve mapped onto the beat map of its registry row.

`build(root, post)` reads `registry/posts.tsv` (the last row of `post`: its `post_id` and `beats`),
`distribution/<ep>/<platform>/snapshot.json` (`video` and `metrics.audienceWatchRatio`) and
`episodes/<ep>/reel/edit-list.json` (the beat IDs, in order of first event), and returns the text of
`distribution/<ep>/<platform>/retention.json`. It runs no `git` and writes nothing.

Point k (1 to 100) of the curve is at time k x L / 100, L the greatest end of the beat map, and belongs to a
beat `start-end` when 100 x start < k x L <= 100 x end, in `Decimal`. A beat with no point, or only null
points, has a null retention: an empty telemetry field is missing data, never zero.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from decimal import Decimal
from pathlib import Path

REGISTRY = "registry/posts.tsv"
COLUMNS = ("post", "post_id", "beats")
POINTS = 100
POST = re.compile(r"^[^/]+/[^/]+$")
RANGE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)-([0-9]+(?:\.[0-9]+)?)$")


class RetentionError(Exception):
    """A refusal: `problems` is the list of messages, in the order they are printed."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def output(post: str) -> str:
    """The path of the file written for `post`, relative to the root."""
    return f"distribution/{post}/retention.json"


def row(root: Path, post: str) -> dict[str, str]:
    """The last row of `registry/posts.tsv` whose `post` is `post`, by column name."""
    path = root / REGISTRY
    if not path.is_file():
        raise RetentionError([f"{REGISTRY} is missing"])
    lines = path.read_text(encoding="utf-8").splitlines()
    header = lines[0].split("\t") if lines else []
    for name in COLUMNS:
        if name not in header:
            raise RetentionError([f"{REGISTRY} has no column {name}"])
    found = None
    for line in lines[1:]:
        cells = line.split("\t")
        if len(cells) == len(header) and cells[header.index("post")] == post:
            found = dict(zip(header, cells))
    if found is None:
        raise RetentionError([f"{REGISTRY} has no row for post {post}"])
    return found


def _json(root: Path, rel: str):
    path = root / rel
    if not path.is_file():
        raise RetentionError([f"{rel} is missing"])
    try:
        return path.read_bytes(), json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise RetentionError([f"{rel} is not JSON"]) from None


def _point(v) -> bool:
    return v is None or (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0)


def curve(root: Path, post: str, post_id: str) -> tuple[list, str]:
    """The 100 points of the snapshot's `metrics.audienceWatchRatio`, and the sha256 of the file's bytes."""
    rel = f"distribution/{post}/snapshot.json"
    raw, data = _json(root, rel)
    metrics = data.get("metrics") if isinstance(data, dict) else None
    points = metrics.get("audienceWatchRatio") if isinstance(metrics, dict) else None
    if not isinstance(points, list) or len(points) != POINTS or not all(_point(v) for v in points):
        raise RetentionError([f"{rel} has no metrics.audienceWatchRatio of {POINTS} points"])
    if data.get("video") != post_id:
        raise RetentionError([f"snapshot video {json.dumps(data.get('video'))} is not post_id {post_id} of {REGISTRY}"])
    return points, hashlib.sha256(raw).hexdigest()


def beat_ids(root: Path, ep: str) -> list[str]:
    """The `beat` of each event of the episode's edit list, in order of first event, each once."""
    rel = f"episodes/{ep}/reel/edit-list.json"
    data = _json(root, rel)[1]
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list) or not events \
            or not all(isinstance(e, dict) and isinstance(e.get("beat"), str) for e in events):
        raise RetentionError([f"{rel} is not {{events: [{{beat, ...}}]}}"])
    return list(dict.fromkeys(e["beat"] for e in events))


def _range(text: str | None) -> tuple[Decimal, Decimal] | None:
    m = RANGE.match(text) if text is not None else None
    if m is None or Decimal(m.group(2)) <= Decimal(m.group(1)):
        return None
    return Decimal(m.group(1)), Decimal(m.group(2))


def beat_map(beats: str, ids: list[str], post: str, ep: str) -> dict[str, tuple[Decimal, Decimal]]:
    """The row's `beats` as {beat: (start, end)}, one valid range for each beat ID and no other beat."""
    entries = []
    if beats != "-":
        for entry in beats.split(","):
            beat, sep, text = entry.partition("=")
            entries.append((beat, _range(text) if sep else None))
    ranges: dict[str, tuple[Decimal, Decimal] | None] = {}
    later = []
    for beat, rng in entries:
        if beat in ranges:
            later.append(f"beat {beat} is named twice in the beat map of post {post}")
            continue
        ranges[beat] = rng
        if beat not in ids:
            later.append(f"beat {beat} of the beat map of post {post} is not in episodes/{ep}/reel/edit-list.json")
    problems = [f"beat {b} has no time range in the beat map of post {post}" for b in ids if ranges.get(b) is None]
    if problems or later:
        raise RetentionError(problems + later)
    return ranges


def _number(d: Decimal) -> int | float:
    return int(d) if d == d.to_integral_value() else float(d)


def build(root, post: str) -> str:
    """The text of `distribution/<ep>/<platform>/retention.json` for `post`; RetentionError on a refusal."""
    if not POST.match(post):
        raise RetentionError([f"post {post} is not <ep>/<platform>"])
    root = Path(root)
    ep = post.split("/")[0]
    found = row(root, post)
    points, sha = curve(root, post, found["post_id"])
    ids = beat_ids(root, ep)
    ranges = beat_map(found["beats"], ids, post, ep)
    length = max(end for _, end in ranges.values())
    beats = []
    for beat in ids:
        start, end = ranges[beat]
        ks = [k for k in range(1, POINTS + 1) if POINTS * start < k * length <= POINTS * end]
        values = [points[k - 1] for k in ks if points[k - 1] is not None]
        beats.append({"beat": beat, "start": _number(start), "end": _number(end),
                      "first": ks[0] if ks else None, "last": ks[-1] if ks else None,
                      "retention": round(math.fsum(values) / len(values), 4) if values else None})
    data = {"beats": beats, "length": _number(length), "post": post, "post_id": found["post_id"],
            "snapshot_sha256": sha}
    return json.dumps(data, indent=1, sort_keys=True) + "\n"
