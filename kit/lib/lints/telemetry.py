"""Telemetry: a window's snapshot holds the platform's answers as received and the metrics they give, and a
value the answers do not hold is null, never 0: empty is not zero (brief sections 6 and 12).

Rule ids: snapshot, answers, metrics, zero. A snapshot is `distribution/<ep>/<platform>/snapshot.json`,
written by the window's pull: one object with exactly `platform`, `video`, `start`, `end`, `answers` (the
platform's answers, parsed) and `metrics` (what `metrics` derives from them, names as the platform names
them, no unit converted). Only `youtube` has a schema: `answers` is `[totals, retention]`, two result tables
of the Analytics API reports.query, and `metrics` holds `views`, `averageViewDuration`,
`averageViewPercentage` and `audienceWatchRatio` (100 points, point k for the ratio k/100). Findings are on
line 1 of the snapshot. The pull tool writes its snapshot with `snapshot`, so the derivation is this one.
"""

from __future__ import annotations

import datetime
import json
import math
import re
from pathlib import Path

from ..lint import Finding, files, read_json

SNAPSHOT = re.compile(r"^distribution/([^/]+)/([^/]+)/snapshot\.json$")
DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
KEYS = ["answers", "end", "metrics", "platform", "start", "video"]
TOTALS = ("views", "averageViewDuration", "averageViewPercentage")
RATIO, WATCH = "elapsedVideoTimeRatio", "audienceWatchRatio"
POINTS = 100
METRICS = sorted(TOTALS + (WATCH,))
SCHEMAS = ("youtube",)


def _value(value) -> bool:
    """A finite number of at least 0, not a boolean."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            and value >= 0)


def _date(value) -> bool:
    if not isinstance(value, str) or not DATE.match(value):
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def table(value) -> bool:
    """A result table: `columnHeaders` a list, `rows` (absent counting as `[]`) a list of lists."""
    if not isinstance(value, dict) or not isinstance(value.get("columnHeaders"), list):
        return False
    rows = value.get("rows", [])
    return isinstance(rows, list) and all(isinstance(row, list) for row in rows)


def _column(result, name):
    """The index of the column whose header `name` is ``name``, or None."""
    for i, header in enumerate(result["columnHeaders"]):
        if isinstance(header, dict) and header.get("name") == name:
            return i
    return None


def _cell(row, index):
    return row[index] if index is not None and index < len(row) else None


def metrics(platform, answers):
    """The metrics the answers give, each a number or None; None when the answers are not two result tables."""
    if platform not in SCHEMAS or not isinstance(answers, list) or len(answers) != 2 or not all(
            table(a) for a in answers):
        return None
    totals, retention = answers
    out = {}
    rows = totals.get("rows", [])
    for name in TOTALS:
        value = _cell(rows[0], _column(totals, name)) if len(rows) == 1 else None
        out[name] = value if _value(value) else None
    points = [None] * POINTS
    ratio, watch = _column(retention, RATIO), _column(retention, WATCH)
    for row in retention.get("rows", []):
        at, value = _cell(row, ratio), _cell(row, watch)
        if not _value(at) or not _value(value):
            continue
        k = round(at * 100)
        if abs(at * 100 - k) <= 1e-6 and 1 <= k <= POINTS and points[k - 1] is None:
            points[k - 1] = value
    out[WATCH] = points
    return out


def snapshot(platform, video, start, end, answers) -> str:
    """The text of the snapshot of ``answers``."""
    data = {"answers": answers, "end": end, "metrics": metrics(platform, answers), "platform": platform,
            "start": start, "video": video}
    return json.dumps(data, indent=1, sort_keys=True) + "\n"


def _zero(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0


def _same(written, derived) -> bool:
    if isinstance(written, bool) or derived is None:
        return written is None and derived is None
    return isinstance(written, (int, float)) and written == derived


def _compare(add, name, written, derived):
    if derived is None and _zero(written):
        add("zero", f"{name} is 0, but the answers hold no value for it: empty is not zero")
    elif not _same(written, derived):
        add("metrics", f"{name} is {json.dumps(written)}, the answers give {json.dumps(derived)}")


def _check(root: Path, rel: str, platform: str) -> list[Finding]:
    out = []

    def add(rule, message):
        out.append(Finding(rel, 1, rule, message))

    _, data = read_json(root, rel)
    if not isinstance(data, dict) or sorted(data) != KEYS:
        return [Finding(rel, 1, "snapshot", "not an object {platform, video, start, end, answers, metrics}")]
    if platform not in SCHEMAS:
        return [Finding(rel, 1, "snapshot", f"no telemetry schema for platform '{platform}'")]
    if data["platform"] != platform:
        add("snapshot", f"platform is {json.dumps(data['platform'])}, the directory says '{platform}'")
    if not isinstance(data["video"], str) or not data["video"]:
        add("snapshot", "video is not a video id")
    for key in ("start", "end"):
        if not _date(data[key]):
            add("snapshot", f"{key} is not a date YYYY-MM-DD")

    derived = metrics(platform, data["answers"])
    if derived is None:
        add("answers", "answers is not a list of two result tables {columnHeaders, rows}")
        return out
    written = data["metrics"]
    if not isinstance(written, dict) or sorted(written) != METRICS:
        add("metrics", "metrics is not an object {" + ", ".join(METRICS) + "}")
        return out
    for name in TOTALS:
        _compare(add, name, written[name], derived[name])
    points = written[WATCH]
    if not isinstance(points, list) or len(points) != POINTS:
        add("metrics", f"{WATCH} is not a list of {POINTS} points")
    else:
        for k, (value, want) in enumerate(zip(points, derived[WATCH]), 1):
            _compare(add, f"{WATCH} point {k}", value, want)
    return out


def check(root: Path) -> list[Finding]:
    findings = []
    for path in files(root):
        m = SNAPSHOT.match(path.as_posix())
        if m:
            findings += _check(root, path.as_posix(), m.group(2))
    return findings
