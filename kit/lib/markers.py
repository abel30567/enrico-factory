"""Reading pull request markers and deciding which verdicts count (system brief, section 8).

Pure functions over recorded data: nothing here calls GitHub, git or a model.

Shapes:
- a comment is ``{"id": int, "created_at": "2026-11-11T10:00:00Z", "body": str}``;
- a pull request ``pr`` is ``{"head": sha, "manifest": hash, "commits": [{"sha", "parents"}],
  "base_shas": [sha, ...], "comments": [comment, ...]}``, commits oldest first;
- a ``stage`` is a pipeline entry; its ``maker``, ``checker`` and ``judge`` are a role id, a list of
  role ids, or null, and ``lint`` is a list of lint names;
- ``roles`` maps a role id to its kind.

Comments are ordered by ``(created_at, id)``.
"""

import hashlib
import re
from typing import NamedTuple


class Marker(NamedTuple):
    actor: str
    verb: str
    args: tuple


ROLE_VERBS = (
    "LAUNCH", "RULING", "SELECTED", "LOCK",
    "READY", "ADDRESSED", "BLOCKED",
    "APPROVED", "NOTES",
    "PREFERS A", "PREFERS B", "NO PREFERENCE",
)

# Actors that are programs (or the lab) post only their own row's verbs.
PROGRAM_VERBS = {
    "LINT": ("PASS", "FAIL", "ERROR"),
    "PRODUCER": ("GATE PASS", "GATE STOP"),
    "PLATFORM-GATE": ("PASS", "FAIL"),
    "AUTORESEARCH": ("KEEP", "NOISE", "DISCARD", "REBASELINE"),
}
CHANNEL_VERBS = ("POSTED", "VERIFIED")
# Actors that also have a row of their own besides the role verbs.
EXTRA_VERBS = {"ANALYST": ("SYMPTOMS",), "BRAINTRUST": ("VOTE",)}

MAY_POST = {
    "maker": ("READY", "ADDRESSED", "BLOCKED"),
    "operator": ("READY", "ADDRESSED", "BLOCKED"),
    "checker": ("APPROVED", "NOTES", "BLOCKED"),
    "judge": ("PREFERS A", "PREFERS B", "NO PREFERENCE", "BLOCKED"),
    "analyst": ("SYMPTOMS", "BLOCKED"),
    "orchestrator": ("LAUNCH", "RULING", "SELECTED", "BLOCKED"),
}

LINT_VERDICTS = PROGRAM_VERBS["LINT"]
ROLE_VERDICTS = ("APPROVED", "NOTES", "PREFERS A", "PREFERS B", "NO PREFERENCE")
MAKER_VERBS = ("READY", "ADDRESSED", "BLOCKED")

_UPPER_ID = r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*"
_FIRST_LINE = re.compile(r"\[(%s|CHANNEL:%s)\]\s+(\S.*)" % (_UPPER_ID, _UPPER_ID))
_ROLE_ID = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
_REVIEWED = re.compile(r"reviewed head ([0-9a-f]{7,64}) manifest ([0-9a-f]{64}|none)")
_NOTE = re.compile(r"(?:[-*]\s+|\|\s*)?\**(B\d+)\b")


def _verbs_for(actor):
    if actor in PROGRAM_VERBS:
        return PROGRAM_VERBS[actor]
    if actor.startswith("CHANNEL:"):
        return CHANNEL_VERBS
    return ROLE_VERBS + EXTRA_VERBS.get(actor, ())


def _args(verb, words):
    """The values of the verb's placeholders, or None when they do not fit the table."""
    if verb == "LAUNCH":
        if len(words) == 2 and _ROLE_ID.fullmatch(words[0]):
            return tuple(words)
        return None
    if verb == "SELECTED":
        if len(words) == 1 and re.fullmatch(r"#\d+", words[0]):
            return (words[0][1:],)
        return None
    if verb == "VOTE":
        if len(words) == 1 and re.fullmatch(r"\d+-\d+", words[0]):
            return tuple(words[0].split("-"))
        return None
    return () if not words else None


def parse(first_line):
    """The marker on the first line of ``first_line``, or None. Later lines are never read."""
    line = first_line.split("\n", 1)[0].strip()
    m = _FIRST_LINE.fullmatch(line)
    if not m:
        return None
    actor, words = m.group(1), m.group(2).split()
    for verb in sorted(_verbs_for(actor), key=lambda v: -len(v.split())):
        n = len(verb.split())
        if words[:n] == verb.split():
            args = _args(verb, words[n:])
            return None if args is None else Marker(actor, verb, args)
    return None


def may_post(kind, verb):
    """Whether a role of ``kind`` may post ``verb``. Program markers belong to no kind."""
    return verb in MAY_POST.get(kind, ())


def manifest_hash(base_rows, head_rows):
    """sha256 of the manifest rows the head adds or changes against the base, sorted; ``none`` if none."""
    base = {r.rstrip("\r\n") for r in base_rows}
    added = sorted({r.rstrip("\r\n") for r in head_rows} - base - {""})
    if not added:
        return "none"
    return hashlib.sha256("\n".join(added).encode("utf-8")).hexdigest()


def reviewed(body):
    """``(sha, manifest)`` from a ``reviewed head <sha> manifest <hash>`` last non-empty line, or None."""
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    if not lines:
        return None
    m = _REVIEWED.fullmatch(lines[-1])
    return (m.group(1), m.group(2)) if m else None


def only_base_merges(commits, from_sha, to_sha, base_shas):
    """True when every commit after ``from_sha`` up to ``to_sha`` merges a base commit as second parent."""
    return _head_reason(commits, from_sha, to_sha, base_shas) is None


def _head_reason(commits, from_sha, to_sha, base_shas):
    """None when ``to_sha`` is ``from_sha`` plus only merges of the base, else why not."""
    shas = [c["sha"] for c in commits]
    if from_sha == to_sha:
        return None
    if from_sha not in shas or to_sha not in shas:
        return "head: %s is not in the pull request" % from_sha
    i, j = shas.index(from_sha), shas.index(to_sha)
    if i > j:
        return "head: %s is newer than the head %s" % (from_sha, to_sha)
    base = set(base_shas)
    for c in commits[i + 1:j + 1]:
        parents = c.get("parents") or []
        if len(parents) < 2 or parents[1] not in base:
            return "head: commit %s after %s is not a merge of the base" % (c["sha"], from_sha)
    return None


def _names(stage, key):
    value = stage.get(key)
    if not value:
        return []
    return [value] if isinstance(value, str) else list(value)


def _order(comment):
    return (comment["created_at"], comment["id"])


def _ordered(comments):
    return sorted(comments, key=_order)


def _maker_marker(comment, stage):
    m = parse(comment["body"])
    if m and m.verb in MAKER_VERBS and m.actor.lower() in _names(stage, "maker"):
        return m
    return None


def _role_reason(marker, stage, roles):
    """None when the marker comes from a role the stage names for this verdict, else the reason."""
    if marker.actor == "LINT":
        return None if _names(stage, "lint") else "role: the stage names no lint"
    role = marker.actor.lower()
    kind = roles.get(role)
    if kind not in ("checker", "judge") or role not in _names(stage, kind):
        return "role: %s is not a checker or judge the stage names" % role
    if not may_post(kind, marker.verb):
        return "role: a %s may not post %s" % (kind, marker.verb)
    return None


def _is_verdict(marker):
    if marker is None:
        return False
    if marker.actor == "LINT":
        return marker.verb in LINT_VERDICTS
    return marker.verb in ROLE_VERDICTS


def counts(verdict, pr, stage, roles):
    """``(True, "counts")``, or ``(False, reason)`` naming the first condition of section 8 that fails."""
    marker = parse(verdict["body"])
    if not _is_verdict(marker):
        return False, "marker: not a lint, checker or judge verdict"
    seen = reviewed(verdict["body"])
    if seen is None:
        return False, "reviewed: no reviewed head line"
    sha, manifest = seen
    reason = _head_reason(pr["commits"], sha, pr["head"], pr["base_shas"])
    if reason:
        return False, reason
    if manifest != pr["manifest"]:
        return False, "manifest: %s is not the current manifest %s" % (manifest, pr["manifest"])
    last = None
    for c in _ordered(pr["comments"]):
        m = _maker_marker(c, stage)
        if m and m.verb in ("READY", "ADDRESSED"):
            last = c
    if last is not None and _order(verdict) <= _order(last):
        return False, "order: not newer than the maker's last %s (comment %s)" % (
            parse(last["body"]).verb, last["id"])
    reason = _role_reason(marker, stage, roles)
    if reason:
        return False, reason
    return True, "counts"


def _entry(comment, marker):
    return {"id": comment["id"], "created_at": comment["created_at"],
            "actor": marker.actor, "verb": marker.verb}


def _notes(body):
    """Blocker ids in a NOTES body: lines that begin with ``B<n>``, as text, a list item or a table cell."""
    found = []
    for line in body.splitlines()[1:]:
        m = _NOTE.match(line.strip())
        if m:
            found.append(m.group(1))
    return found


def summary(pr, stage, roles):
    """The state of one pull request at one stage, from its markers."""
    comments = _ordered(pr["comments"])
    checkers = _names(stage, "checker")
    judges = _names(stage, "judge")
    out = {
        "maker": None,
        "lint": None,
        "checkers": {r: None for r in sorted(checkers)},
        "judges": {r: None for r in sorted(judges)},
        "blockers": [],
        "rounds": 0,
        "needs_ruling": False,
    }
    blockers = {}
    ready_seen = False
    for c in comments:
        m = parse(c["body"])
        if m is None:
            continue
        if _maker_marker(c, stage):
            out["maker"] = _entry(c, m)
            ready_seen = ready_seen or m.verb == "READY"
            continue
        role = m.actor.lower()
        if (ready_seen and m.verb == "NOTES" and role in checkers
                and roles.get(role) == "checker"):
            out["rounds"] += 1
        if not counts(c, pr, stage, roles)[0]:
            continue
        if m.actor == "LINT":
            out["lint"] = _entry(c, m)
        elif role in out["checkers"] and roles.get(role) == "checker":
            out["checkers"][role] = _entry(c, m)
        elif role in out["judges"] and roles.get(role) == "judge":
            out["judges"][role] = _entry(c, m)
        if m.verb == "NOTES":
            for note in _notes(c["body"]):
                blockers[(role, int(note[1:]))] = c["id"]
    out["blockers"] = [{"by": by, "note": "B%d" % n, "comment": blockers[(by, n)]}
                       for by, n in sorted(blockers)]
    out["needs_ruling"] = out["rounds"] >= 3
    return out
