"""The live packaging loop: one change per post, measured on the post's frozen window (system brief sections 12
and 13).

A packaging loop is ``lab/packaging/<loop>.json`` on the base. A post is ``<ep>/<platform>``; its change is
``-`` (a no-op control, the house packaging) or a change id whose ``lab/experiments/<change>.md`` is on the
base. ``lab/assignments.tsv`` is append-only and written only by ``factory packaging assign``, straight on
the base branch, before the post's window: one row per post. A post's value is read from its snapshot
``distribution/<ep>/<platform>/snapshot.json`` at the base, never from disk; a null metric is no value,
never 0. The rows of ``lab/results.tsv`` are the lab's, written through ``lab``.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from kit.lib import evals, gitstate, lab, production

PATH = "lab/assignments.tsv"
COLUMNS = ("time", "loop", "post", "change")
HEADER = "\t".join(COLUMNS)
LOOP_KEYS = ("better", "controls", "drift_every", "metric", "platform", "ticket")
PLATFORMS = ("youtube",)
DURATION = "averageViewDuration"
WATCH = "audienceWatchRatio"
POINTS = 100
POSTS = 2
WAITS = "waits"
SKIPPED = "skipped"

_ID = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_HOLD = re.compile(WATCH + r":([1-9][0-9]?|100)")


class PackagingError(Exception):
    pass


def loop_path(loop: str) -> str:
    return f"lab/packaging/{loop}.json"


def experiment_path(change: str) -> str:
    return f"lab/experiments/{change}.md"


def snapshot_path(ep: str, platform: str) -> str:
    return f"distribution/{ep}/{platform}/snapshot.json"


def _integer(value, low, high) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _shaped(spec) -> bool:
    if not isinstance(spec, dict) or sorted(spec) != list(LOOP_KEYS):
        return False
    metric, ticket = spec["metric"], spec["ticket"]
    return (isinstance(metric, str) and (metric == DURATION or bool(_HOLD.fullmatch(metric)))
            and spec["better"] in lab.BETTER
            and spec["platform"] in PLATFORMS
            and _integer(spec["controls"], *lab.CONTROLS)
            and _integer(spec["drift_every"], *lab.DRIFT_EVERY)
            and isinstance(ticket, int) and not isinstance(ticket, bool) and ticket > 0)


def load_loop(root, base: str, loop: str) -> dict:
    """The packaging loop file of ``loop`` at the base; ``PackagingError`` when the id, the file or its shape is
    wrong, or when ``loop`` is also a lab loop."""
    if not _ID.fullmatch(loop):
        raise PackagingError(f"'{loop}' is not a loop id")
    text = gitstate.show(root, base, loop_path(loop))
    if text is None:
        raise PackagingError(f"{loop_path(loop)} is not on {base}")
    try:
        spec = json.loads(text)
    except ValueError:
        spec = None
    if not _shaped(spec):
        raise PackagingError(f"{loop_path(loop)} is not {{" + ", ".join(LOOP_KEYS) + "}")
    if gitstate.show(root, base, lab.loop_path(loop)) is not None:
        raise PackagingError(f"{loop} is also a lab loop: {lab.loop_path(loop)} is on {base}")
    return spec


def episode(ep: str) -> str:
    if not _ID.fullmatch(ep):
        raise PackagingError(f"'{ep}' is not an episode id")
    return ep


def change_id(change: str) -> str:
    if change != lab.NONE and not _ID.fullmatch(change):
        raise PackagingError(f"'{change}' is not a change id")
    return change


def read(text: str) -> list[dict]:
    """The rows of the assignment log ``text``, each with its ``line``; ``PackagingError`` on the first bad
    line."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines or lines[0] != HEADER:
        raise PackagingError("assignments line 1: the header is not " + " ".join(COLUMNS))
    rows = []
    for n, row_line in enumerate(lines[1:], 2):
        fields = row_line.split("\t")
        if len(fields) != len(COLUMNS):
            raise PackagingError(f"assignments line {n}: {len(fields)} columns, not {len(COLUMNS)}")
        rows.append({**dict(zip(COLUMNS, fields)), "line": n})
    return rows


def line(row: dict) -> str:
    """The assignment line for ``row``, without a newline."""
    return "\t".join(row[name] for name in COLUMNS)


def assignments(root, base: str) -> list[dict]:
    """The rows of the assignment log at the base, each with its ``line`` and the ``commit`` that wrote it."""
    text = gitstate.show(root, base, PATH)
    if text is None:
        return []
    rows = read(text)
    blame = gitstate.git(root, "blame", "-l", "-s", "--root", gitstate.head(root, base), "--", PATH).splitlines()
    for row in rows:
        row["commit"] = blame[row["line"] - 1].split()[0]
    return rows


def results(root, base: str) -> list[dict]:
    text = gitstate.show(root, base, lab.PATH)
    return lab.read(text) if text is not None else []


def _number(value) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def value(root, base: str, spec: dict, ep: str):
    """``(kind, what)`` for the post ``<ep>/<platform>``: ``("value", number)``, ``(WAITS, reason)`` with no
    frozen window, or ``(SKIPPED, reason)`` when the window holds no value or is not a snapshot."""
    platform, metric = spec["platform"], spec["metric"]
    rel = snapshot_path(ep, platform)
    text = gitstate.show(root, base, rel)
    if text is None:
        return WAITS, "no frozen window"
    not_snapshot = (SKIPPED, f"{rel} is not a snapshot of {platform}")
    try:
        data = json.loads(text)
    except ValueError:
        return not_snapshot
    if not isinstance(data, dict) or data.get("platform") != platform or not isinstance(data.get("metrics"), dict):
        return not_snapshot
    metrics = data["metrics"]
    if metric == DURATION:
        if DURATION not in metrics:
            return not_snapshot
        found = metrics[DURATION]
    else:
        points = metrics.get(WATCH)
        if not isinstance(points, list) or len(points) != POINTS:
            return not_snapshot
        found = points[int(metric.split(":", 1)[1]) - 1]
    if found is None:
        return SKIPPED, f"the window holds no {metric}"
    if not _number(found):
        return not_snapshot
    return "value", round(float(found), evals.PLACES)


def ready(p, root, path: str) -> None:
    """``PackagingError`` unless the checkout is on the base with a clean index and ``path`` on disk starts with
    the base's: what ``append`` and ``lab.append`` need before anything is written."""
    base = production.base(p)
    try:
        branch = gitstate.git(root, "symbolic-ref", "--short", "HEAD").strip()
    except gitstate.GitError:
        branch = None
    if branch != base or gitstate.git(root, "diff", "--cached", "--name-only").strip():
        raise PackagingError(f"the checkout is not on {base} with a clean index")
    disk = Path(root) / path
    current = disk.read_text(encoding="utf-8") if disk.is_file() else ""
    if not current.startswith(gitstate.show(root, "HEAD", path) or ""):
        raise PackagingError(f"{path} differs from the base")


def append(p, gh, root, row: dict) -> str:
    """Append ``row`` to the assignment log, commit it on the base as ``factory`` and push. Returns the line."""
    ready(p, root, PATH)
    new = line(row)
    path = Path(root) / PATH
    current = path.read_text(encoding="utf-8") if path.is_file() else HEADER + "\n"
    content = current + new + "\n"
    read(content)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    gitstate.commit(root, [PATH], f"lab: {row['loop']} assign {row['post']} {row['change']}", "factory")
    gh.push(root, production.base(p))
    return new
