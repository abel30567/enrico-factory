"""The answer a post's upload records and the post registry (system brief sections 7, 10 and 12).

A post ticket of stage ``distribution/post`` has the unit ``<ep>/<platform>``. Its upload job records the
platform's answer as ``distribution/<ep>/<platform>/post.json`` (`factory run`, the ``record`` of
`kit/lib/tools/__init__.py`): ``answer_problems`` is its shape. ``registry/posts.tsv`` is append-only and
written only by ``factory lock``, straight on the base branch: one row per locked post, with the post id,
the uploaded file's hash and the beat map of the episode's edit list.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from kit.lib import github, gitstate, production, tickets

ROOM, STAGE = "distribution", "post"
ANSWER = "distribution/{}/post.json"
EDIT_LIST = "episodes/{}/reel/edit-list.json"
PATH = "registry/posts.tsv"
COLUMNS = ("time", "post", "episode", "platform", "post_id", "visibility", "sha256", "job", "pull", "head",
           "beats")
HEADER = "\t".join(COLUMNS)
VISIBILITIES = ("private", "unlisted", "public")

_SHA256 = re.compile(r"[0-9a-f]{64}")
_PLATFORM = re.compile(r"[a-z][a-z0-9-]*")
_POST_ID = re.compile(r"[A-Za-z0-9_.~-]{1,64}")
_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_FIELD = re.compile(r"[A-Za-z0-9_.]{1,64}")
_SHORT = re.compile(r"[A-Za-z0-9_.:-]{0,64}")


class PostsError(Exception):
    pass


def _boolean(value) -> bool:
    return isinstance(value, bool)


def _short(value) -> bool:
    """A boolean, an integer, or a string of at most 64 of ``[A-Za-z0-9_.:-]``."""
    return isinstance(value, (bool, int)) or (isinstance(value, str) and _SHORT.fullmatch(value) is not None)


def _fields(value) -> bool:
    return isinstance(value, dict) and all(_FIELD.fullmatch(k) and _short(v) for k, v in value.items())


def _matching(pattern):
    return lambda value: isinstance(value, str) and pattern.fullmatch(value) is not None


def _time(value) -> bool:
    if not (isinstance(value, str) and _TIME.fullmatch(value)):
        return False
    try:
        datetime.strptime(value, production.TIME_FORMAT)
    except ValueError:
        return False
    return True


KEYS = {
    "captions": (_boolean, "true or false"),
    "fields": (_fields, "an object of field names and short values"),
    "job": (_matching(_SHA256), "a sha256"),
    "platform": (_matching(_PLATFORM), "a platform id"),
    "post_id": (_matching(_POST_ID), "a post id"),
    "processed": (_boolean, "true or false"),
    "read_at": (_time, "a time YYYY-MM-DDTHH:MM:SSZ"),
    "sha256": (_matching(_SHA256), "a sha256"),
    "visibility": (lambda value: value in VISIBILITIES and isinstance(value, str), "private, unlisted or public"),
}


def answer_problems(doc) -> list:
    """What makes ``doc`` not the answer of an upload, in order: missing keys, unknown keys, then the keys
    whose value has the wrong shape, each kind sorted by key. ``[]`` for a sound answer."""
    if not isinstance(doc, dict):
        return ["post.json is not a JSON object"]
    problems = [f"post.json: key {k} is missing" for k in sorted(KEYS) if k not in doc]
    problems += [f"post.json: unknown key {k}" for k in sorted(doc) if k not in KEYS]
    problems += [f"post.json: {k} is not {words}" for k, (ok, words) in sorted(KEYS.items())
                 if k in doc and not ok(doc[k])]
    return problems


def parse_answer(text):
    """The answer in ``text`` when it is sound, else None."""
    if text is None:
        return None
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    return doc if not answer_problems(doc) else None


def _number(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _text(d: Decimal) -> str:
    return format(d.normalize(), "f")


def beats(text) -> str:
    """The beat map of an edit list's text: ``<beat>=<least start>-<greatest end>`` per beat in the order of
    its first event, joined by ``,``; ``-`` when there is no such list."""
    try:
        doc = json.loads(text) if text is not None else None
    except ValueError:
        doc = None
    events = doc.get("events") if isinstance(doc, dict) else None
    if (not isinstance(events, list) or not events
            or not all(isinstance(e, dict) and isinstance(e.get("beat"), str)
                       and _number(e.get("start")) and _number(e.get("duration")) for e in events)):
        return "-"
    spans = {}
    for event in events:
        start = Decimal(str(event["start"]))
        end = start + Decimal(str(event["duration"]))
        least, greatest = spans.get(event["beat"], (start, end))
        spans[event["beat"]] = (min(least, start), max(greatest, end))
    return ",".join(f"{beat}={_text(least)}-{_text(greatest)}" for beat, (least, greatest) in spans.items())


def is_post(ticket) -> bool:
    return ticket["room"] == ROOM and ticket["stage"] == STAGE


def rows(root, ref) -> list:
    """The rows of the registry at ``ref`` as dicts by column; a line whose cell count is not the header's
    is skipped."""
    text = gitstate.show(root, ref, PATH)
    lines = text.splitlines() if text is not None else []
    header = lines[0].split("\t") if lines else []
    return [dict(zip(header, cells)) for cells in (line.split("\t") for line in lines[1:])
            if len(cells) == len(header)]


def _commit(p, gh, root, line: str, message: str) -> None:
    """Append ``line`` to the registry on the checked-out base, commit it as ``factory`` and push. A push
    that fails takes the commit back, so that the next lock writes the row again."""
    base = production.base(p)
    try:
        branch = gitstate.git(root, "symbolic-ref", "--short", "HEAD").strip()
    except gitstate.GitError:
        branch = None
    if branch != base or gitstate.git(root, "diff", "--cached", "--name-only").strip():
        raise PostsError(f"the checkout is not on {base} with a clean index")
    path = Path(root) / PATH
    committed = gitstate.show(root, "HEAD", PATH)
    current = path.read_text(encoding="utf-8", errors="surrogateescape") if path.is_file() else None
    if current != committed:
        raise PostsError(f"{PATH} differs from the base")
    old = gitstate.head(root, "HEAD")
    content = (current if current is not None else HEADER + "\n") + line + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", errors="surrogateescape")
    try:
        gitstate.commit(root, [PATH], message, "factory")
        gh.push(root, base)
    except (gitstate.GitError, github.GitHubError):
        if gitstate.head(root, "HEAD") != old:
            gitstate.git(root, "reset", "-q", "--keep", old)
        if committed is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(committed, encoding="utf-8", errors="surrogateescape")
        raise


def append(p, gh, root, unit: str, number: int, head: str) -> str:
    """Append the row of the post ``unit`` locked by pull ``number`` at ``head``; the line to print."""
    answer = parse_answer(gitstate.show(root, head, ANSWER.format(unit)))
    if answer is None:
        raise PostsError(f"{ANSWER.format(unit)} at {head[:12]} is not a sound answer")
    episode = tickets.episode(unit)
    beat_map = beats(gitstate.show(root, production.base(p), EDIT_LIST.format(episode)))
    row = (production.now(), unit, episode, answer["platform"], answer["post_id"], answer["visibility"],
           answer["sha256"], answer["job"], str(number), head, beat_map)
    _commit(p, gh, root, "\t".join(row), f"registry: post {unit}")
    return f"{PATH}: {unit} {answer['post_id']}"
