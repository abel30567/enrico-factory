"""The judge calibration set: one row per beat of a live post, from its `retention.json` (brief section 7).

`calibration/retention.tsv` is tab-separated with the columns of `HEADER`. `rows` reads
`distribution/<ep>/<platform>/retention.json`; `merge` replaces a post's rows in the old text.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

PATH = "calibration/retention.tsv"
HEADER = ("post", "post_id", "beat", "start", "end", "retention", "snapshot_sha256")
HEADER_MESSAGE = "header is not " + ", ".join(HEADER)
POST = re.compile(r"^[^/]+/[^/]+$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
NUMBER = re.compile(r"^[0-9]+(\.[0-9]+)?$")
SHAPE = "{beats: [{beat, start, end, retention}], post, post_id, snapshot_sha256}"


class CalibrationError(Exception):
    """A refusal; `problems` is a list of one message."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def _number(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0
            and NUMBER.fullmatch(json.dumps(v)) is not None)


def _name(v) -> bool:
    return isinstance(v, str) and v != "" and not any(c in v for c in "\t\r\n")


def _shaped(data) -> bool:
    if not isinstance(data, dict) or not {"beats", "post", "post_id", "snapshot_sha256"} <= set(data):
        return False
    beats = data["beats"]
    if not isinstance(beats, list) or not beats:
        return False
    for b in beats:
        if not isinstance(b, dict) or not {"beat", "start", "end", "retention"} <= set(b):
            return False
        if not (_name(b["beat"]) and _number(b["start"]) and _number(b["end"])
                and (b["retention"] is None or _number(b["retention"]))):
            return False
    sha = data["snapshot_sha256"]
    return _name(data["post_id"]) and isinstance(sha, str) and SHA256.fullmatch(sha) is not None


def rows(root, post: str) -> list[tuple[str, ...]]:
    """The rows of `post`, one per beat in the order of `beats`; raises CalibrationError."""
    if not POST.fullmatch(post):
        raise CalibrationError([f"post {post} is not <ep>/<platform>"])
    rel = f"distribution/{post}/retention.json"
    path = Path(root) / rel
    if not path.is_file():
        raise CalibrationError([f"{rel} is missing"])
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise CalibrationError([f"{rel} is not JSON"]) from None
    if not _shaped(data):
        raise CalibrationError([f"{rel} is not {SHAPE}"])
    if data["post"] != post:
        raise CalibrationError([f"{rel} is for post {json.dumps(data['post'])}, not {post}"])
    out = []
    for b in data["beats"]:
        retention = "-" if b["retention"] is None else json.dumps(b["retention"])
        out.append((post, data["post_id"], b["beat"], json.dumps(b["start"]), json.dumps(b["end"]), retention,
                    data["snapshot_sha256"]))
    return out


def merge(old: str | None, post: str, new: list[tuple[str, ...]]) -> str:
    """The text of `PATH` with the rows of `post` replaced by `new`, sorted by post; raises CalibrationError."""
    header = "\t".join(HEADER)
    kept = []
    if old is not None:
        lines = [line for line in old.splitlines() if line.strip()]
        if not lines or lines[0] != header:
            raise CalibrationError([f"{PATH}: {HEADER_MESSAGE}"])
        kept = [line for line in lines[1:] if line.split("\t")[0] != post]
    body = sorted(kept + ["\t".join(r) for r in new], key=lambda line: line.split("\t")[0])
    return "\n".join([header] + body) + "\n"
