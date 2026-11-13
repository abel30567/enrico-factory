"""Rank other people's videos from stored reads, with no model (brief section 16).

A stored read is one file `<STATE_DIR>/reads/<topic>/<platform>/<stamp>.json`, `<stamp>` its `read_at` with
`-` and `:` removed: `{"topic", "platform", "read_at", "videos": [...]}`. A video holds `id` and `link` and
may hold `order`, `published_at`, `duration_s`, `views`, `likes`, `comments`, `shares`, `saves`,
`followers` and `author_views`. An absent key and `null` both mean missing; missing is never zero. Other
keys are ignored. Rankings are made inside one platform only and never merged.

A `TrendError` message names a read by its path under the state directory, never by its absolute path.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path

from kit.lib import production

PLATFORMS = ("youtube", "tiktok", "instagram")
VIEW_FLOOR = 1000
SHORT_UNDER_S = 240
BANDS = ("short", "long", "unknown")
HEADER = ("ranking", "rank", "id", "link", "read_at", "note")
COUNTS = ("views", "likes", "comments", "shares", "saves", "followers")
AUTHOR_MIN = 10
AUTHOR_MAX = 20
TOPIC = re.compile(r"[a-z0-9][a-z0-9-]*")

_READ_NAME = re.compile(r"\d{8}T\d{6}Z\.json")
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_TOKEN = re.compile(r"\S+")

# (name, what a skip line says the videos lack), in the order of the output.
RANKINGS = (
    ("engagement-rate", "views, likes or comments"),
    ("views-per-hour", "views or a publish time"),
    ("outlier-score", "views or 10 views of the author"),
    ("comment-rate", "views or comments"),
    ("reach", "views or followers"),
    ("velocity", "views in this read or the one before"),
    ("platform-order", "a platform order"),
)


class TrendError(Exception):
    pass


def reads_dir(p: production.Production, topic: str, platform: str) -> Path:
    """Where the reads of `topic` on `platform` are stored. Not created."""
    return production.state_dir(p) / "reads" / topic / platform


def topic_file(topic: str) -> str:
    return f"research/topics/{topic}.json"


def card_file(topic: str, platform: str) -> str:
    return f"research/trends/{topic}.{platform}.tsv"


def check_topic(root, topic: str, platform: str) -> None:
    """Refuse unless `research/topics/<topic>.json` under `root` is an object with a key `platform`."""
    rel = topic_file(topic)
    path = Path(root) / rel
    if not path.is_file():
        raise TrendError(f"no {rel}")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError):
        doc = None
    if not isinstance(doc, dict) or platform not in doc:
        raise TrendError(f"{rel} does not watch {platform}")


def _time(value):
    if not isinstance(value, str) or not _TIME.fullmatch(value):
        return None
    try:
        return datetime.strptime(value, production.TIME_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _check_video(rel, video, index, seen):
    if not isinstance(video, dict):
        raise TrendError(f"{rel}: video {index} has no id or link")
    vid, link = video.get("id"), video.get("link")
    if not all(isinstance(v, str) and _TOKEN.fullmatch(v) for v in (vid, link)):
        raise TrendError(f"{rel}: video {index} has no id or link")
    if vid in seen:
        raise TrendError(f"{rel}: video {vid} is repeated")
    seen.add(vid)
    for key in ("order", *COUNTS):
        value = video.get(key)
        if value is not None and not _whole(value):
            raise TrendError(f"{rel}: video {vid}: {key} is not a whole number >= 0 or null")
    published = video.get("published_at")
    if published is not None and _time(published) is None:
        raise TrendError(f"{rel}: video {vid}: published_at is not valid")
    duration = video.get("duration_s")
    if duration is not None and not (isinstance(duration, (int, float)) and not isinstance(duration, bool)
                                     and math.isfinite(duration) and duration >= 0):
        raise TrendError(f"{rel}: video {vid}: duration_s is not valid")
    author = video.get("author_views")
    if author is not None and not (isinstance(author, list) and all(_whole(v) for v in author)):
        raise TrendError(f"{rel}: video {vid}: author_views is not valid")


def _check_read(rel, name, doc, topic, platform):
    if not isinstance(doc, dict):
        raise TrendError(f"{rel}: not a JSON object")
    if doc.get("topic") != topic or doc.get("platform") != platform:
        raise TrendError(f"{rel}: topic or platform is not {topic} {platform}")
    read_at = doc.get("read_at")
    if _time(read_at) is None or read_at.replace("-", "").replace(":", "") + ".json" != name:
        raise TrendError(f"{rel}: read_at does not match the file name")
    if not isinstance(doc.get("videos"), list):
        raise TrendError(f"{rel}: videos is not a list")
    seen = set()
    for index, video in enumerate(doc["videos"]):
        _check_video(rel, video, index, seen)


def load_reads(directory, topic: str, platform: str) -> list[dict]:
    """Every read in `directory`, checked, oldest first. A file whose name is not a stamp is ignored."""
    directory = Path(directory)
    names = sorted(path.name for path in directory.iterdir()
                   if _READ_NAME.fullmatch(path.name) and path.is_file()) if directory.is_dir() else []
    if not names:
        raise TrendError(f"no reads of {topic} on {platform}")
    reads = []
    for name in names:
        rel = f"reads/{topic}/{platform}/{name}"
        try:
            doc = json.loads((directory / name).read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            doc = None
        _check_read(rel, name, doc, topic, platform)
        reads.append(doc)
    return reads


def band(video) -> str:
    duration = video.get("duration_s")
    if duration is None:
        return "unknown"
    return "short" if duration < SHORT_UNDER_S else "long"


def _hours(start, end) -> float:
    return (end - start).total_seconds() / 3600


def _scores(video, read_time, earlier):
    """{ranking name: score} for every count ranking the video has the fields for."""
    views = video.get("views")
    if views is None:
        return {}
    likes, comments, followers = video.get("likes"), video.get("comments"), video.get("followers")
    scores = {}
    if likes is not None and comments is not None:
        extra = sum(video[key] for key in ("shares", "saves") if video.get(key) is not None)
        scores["engagement-rate"] = (likes + comments + extra) / views
    published = _time(video.get("published_at"))
    if published is not None:
        scores["views-per-hour"] = views / max(1.0, _hours(published, read_time))
    author = video.get("author_views")
    if author is not None and len(author) >= AUTHOR_MIN:
        median = statistics.median(author[:AUTHOR_MAX])
        if median > 0:
            scores["outlier-score"] = views / median
    if comments is not None:
        scores["comment-rate"] = comments / views
    if followers is not None and followers > 0:
        scores["reach"] = views / followers
    if earlier is not None:
        before = earlier[0].get(video["id"], {}).get("views")
        if before is not None:
            scores["velocity"] = (views - before) / earlier[1]
    return scores


def _order_key(video):
    order = video.get("order")
    return (order is None, order if order is not None else 0, video["id"])


def rank(reads: list[dict], floor: int):
    """Rank the last of `reads` (oldest first) against the one before it.

    Returns `(read_at, rows, skips, under)`: `rows` is a list of `{ranking, rank, id, link, score}` in the
    output order (`score` an int for `platform-order`), `skips` a list of `(ranking, lacking, of, what)`,
    `under` the number of videos whose known views are below `floor`.
    """
    current = reads[-1]
    read_time = _time(current["read_at"])
    earlier = None
    if len(reads) > 1:
        before = reads[-2]
        earlier = ({v["id"]: v for v in before["videos"]}, _hours(_time(before["read_at"]), read_time))
    videos, under = [], 0
    for video in current["videos"]:
        if video.get("views") is not None and video["views"] < floor:
            under += 1
        else:
            videos.append(video)
    scored = [(video, _scores(video, read_time, earlier)) for video in videos]
    rows, skips = [], []
    for name, what in RANKINGS:
        if name == "platform-order":
            groups = [(name, [(v, None) for v, _ in scored])]
        else:
            groups = [(f"{name}:{b}", [(v, s) for v, s in scored if band(v) == b]) for b in BANDS]
        for ranking, members in groups:
            if name == "platform-order":
                ranked = sorted(((v, v["order"]) for v, _ in members if v.get("order") is not None),
                                key=lambda item: _order_key(item[0]))
            else:
                ranked = sorted(((v, s[name]) for v, s in members if name in s),
                                key=lambda item: (-item[1], *_order_key(item[0])))
            for position, (video, score) in enumerate(ranked, 1):
                rows.append({"ranking": ranking, "rank": position, "id": video["id"], "link": video["link"],
                             "score": score})
            if len(ranked) < len(members):
                skips.append((ranking, len(members) - len(ranked), len(members), what))
    return current["read_at"], rows, skips, under


def score_text(row) -> str:
    return str(row["score"]) if row["ranking"] == "platform-order" else format(row["score"], ".4f")


def read_card(text: str, rel: str) -> dict:
    """{(ranking, id): note} of a trend card's rows; refuses a file that is not a trend card."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines or tuple(lines[0].split("\t")) != HEADER:
        raise TrendError(f"{rel} is not a trend card (line 1)")
    notes = {}
    for number, line in enumerate(lines[1:], 2):
        fields = line.split("\t")
        if len(fields) != len(HEADER):
            raise TrendError(f"{rel} is not a trend card (line {number})")
        notes.setdefault((fields[0], fields[2]), fields[5])
    return notes


def card_text(rows: list[dict], read_at: str, notes: dict) -> str:
    lines = ["\t".join(HEADER)]
    for row in rows:
        note = notes.get((row["ranking"], row["id"]), "")
        lines.append("\t".join((row["ranking"], str(row["rank"]), row["id"], row["link"], read_at, note)))
    return "\n".join(lines) + "\n"


def dropped(rows: list[dict], notes: dict) -> list[tuple[str, str]]:
    """The non-empty notes of the old card whose video is no longer ranked, sorted."""
    kept = {(row["ranking"], row["id"]) for row in rows}
    return sorted(key for key, note in notes.items() if note and key not in kept)
