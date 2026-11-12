"""The rules a pull request must meet to be locked (system brief section 9).

Pure: nothing here writes to GitHub or git, nor posts anything. It reads the state ``kit.lib.tickets``
loaded, git through ``kit.lib.gitstate`` and the blinding key through ``kit.lib.blind``.

``evaluate`` always returns eleven rules, in order: the ten of section 9 and, eleventh, that a released
episode's files are never changed. A reason is one line of fixed words, ids and paths; it never quotes
a comment's prose.
"""

from __future__ import annotations

import json
import re
from typing import NamedTuple

from kit.lib import blind, frontmatter, gitstate, ledger, markers, production, tickets

NAMES = ("lint", "checkers", "judges", "upstream", "selected", "amendment", "writes", "repeat-notes", "gate",
         "base", "released")
CANON_REVIEWER = "canon-reviewer"
CHARTER = "bible/charter.md"
AMENDMENT_CLASSES = ("charter", "arc", "canon")
VOTE_CLASSES = ("charter", "arc")
VOTE_ACTOR = "BRAINTRUST"
VOTE_MIN = 5
RELEASE = "distribution/{}/release.json"

_AMENDMENT = re.compile(r"bible/amendments/A-\d+\.json")
_CHECKS = re.compile(r"Checks required by stage (\S+): (.*)")
_REPEAT = re.compile(r"(?:[-*]\s+)?(?:(B\d+) \(Blocker\)|(I\d+) \(Issue\)): REPEAT [^\s:]+:")
_EPISODE_FILE = re.compile(r"(?:episodes|production|post)/([^/]+)/")


class Rule(NamedTuple):
    number: int
    name: str
    ok: bool
    reason: str


def _order(comment):
    return (comment["created_at"], comment["id"])


def _ordered(comments):
    return sorted(comments, key=_order)


def _comment(pull, comment_id):
    return next(c for c in pull["comments"] if c["id"] == comment_id)


def _makers_last(entry, comments):
    """The order key of the stage maker's last ``READY`` or ``ADDRESSED``, or None."""
    makers = production.stage_roles(entry, "maker")
    last = None
    for c in _ordered(comments):
        m = markers.parse(c["body"])
        if m and m.verb in ("READY", "ADDRESSED") and m.actor.lower() in makers:
            last = _order(c)
    return last


def maker_role(entry, pull):
    """The role of the pull's first ``READY`` comment when the stage names it as a maker, else None."""
    for c in _ordered(pull["comments"]):
        m = markers.parse(c["body"])
        if m and m.verb == "READY":
            role = c.get("role")
            return role if role in production.stage_roles(entry, "maker") else None
    return None


def judge_sides(p, state, pull):
    """``{judge role: new|old|none}`` for the counting judge verdicts when a locked version exists for a
    blind stage, else None. ``blind.BlindError`` when there is no key for the head a verdict names."""
    if not blind.old_exists(p, state, pull):
        return None
    sides = {}
    for role, found in pull["summary"]["judges"].items():
        if found is None:
            continue
        sha = markers.reviewed(_comment(pull, found["id"])["body"])[0]
        sides[role] = blind.resolve(p, pull["number"], sha, found["verb"])
    return sides


def cheaper_stages_locked(p, state, ticket) -> bool:
    """In production, every cheaper stage of the shot is locked. Not built yet (ruling Q19)."""
    return True


def needs_amendment(entry, changed) -> bool:
    """The stage's unit is an amendment, or the pull changes a ``bible/**`` path outside the stage's paths."""
    return entry["unit"] == "amendment" or any(
        production.matches(path, ["bible/**"]) and not production.matches(path, entry["paths"])
        for path in changed)


def gate_needed(p, state, ticket, entry, maker) -> bool:
    """Section 11: the last stage of the room hands its package over, or the ticket passed its iteration cap."""
    if production.stages(p, ticket["room"])[-1]["stage"] == entry["stage"]:
        return True
    cap = production.caps(p)["CAP_TICKET_ITERATIONS"]
    return cap is not None and tickets.launches(state, ticket["number"], maker) > cap


# Each rule returns its reason: "" when it holds.

def _lint(entry, pull):
    names = entry.get("lint") or []
    if not names:
        return ""
    found = pull["summary"]["lint"]
    if found is None:
        return "no counting [LINT] verdict"
    if found["verb"] != "PASS":
        return f"lint #{found['id']} is {found['verb']}"
    listed = set()
    for line in _comment(pull, found["id"])["body"].splitlines():
        m = _CHECKS.fullmatch(line.strip())
        if m and m.group(1) == entry["stage"]:
            listed |= set(frontmatter.as_list(m.group(2)))
    missing = sorted(set(names) - listed)
    if missing:
        return f"lint #{found['id']} does not list {', '.join(missing)} for stage {entry['stage']}"
    return ""


def _checkers(summary):
    for role, found in summary["checkers"].items():
        if found is None:
            return f"checker {role} has no counting verdict"
        if found["verb"] != "APPROVED":
            return f"checker {role} {found['verb']} #{found['id']}"
    if summary["blockers"]:
        b = summary["blockers"][0]
        return f"Blocker {b['note']} by {b['by']} is open (#{b['comment']})"
    return ""


def _judges(p, state, pull):
    judges = pull["summary"]["judges"]
    missing = [role for role, found in judges.items() if found is None]
    if missing:
        return f"judge {', '.join(missing)} has no counting verdict"
    try:
        sides = judge_sides(p, state, pull)
    except blind.BlindError:
        return "no blinding key"
    if sides is None:
        return ""
    new = sum(1 for side in sides.values() if side == "new")
    if new * 2 <= len(judges):
        return f"{new} of {len(judges)} judges prefer the new version"
    return ""


def _upstream(p, state, ticket, root):
    ok, reason = tickets.upstream(p, state, ticket, root)
    if not ok:
        return reason
    if not cheaper_stages_locked(p, state, ticket):
        return "a cheaper stage of the shot is not locked"
    return ""


def _selected(state, ticket, entry, pull):
    if entry["pitches"] == 1:
        return ""
    chosen = tickets.selected(state, ticket["number"])
    if chosen == pull["number"]:
        return ""
    if chosen is None:
        return f"ticket #{ticket['number']} has no SELECTED"
    return f"ticket #{ticket['number']} selected PR #{chosen}"


def _voted(entry, pull):
    """``(enough, reason)`` for the latest ``[BRAINTRUST] VOTE`` after the maker's last READY or ADDRESSED:
    ``enough`` when it has enough votes for, ``reason`` from ``markers.reviewed_reason`` for that vote."""
    comments = pull["comments"]
    last = _makers_last(entry, comments)
    vote = None
    for c in _ordered(comments):
        m = markers.parse(c["body"])
        if m and m.actor == VOTE_ACTOR and m.verb == "VOTE" and (last is None or _order(c) > last):
            vote = (c, m)
    if vote is None or int(vote[1].args[0]) < VOTE_MIN:
        return False, None
    return True, markers.reviewed_reason(vote[0], pull["git"])


def _amendment(entry, pull, root):
    changed = pull["git"]["changed"]
    if not needs_amendment(entry, changed):
        return ""
    head = pull["git"]["head"]
    base = gitstate.merge_base(root, pull["base_ref"], head)
    added = [path for path in changed if _AMENDMENT.fullmatch(path) and gitstate.show(root, base, path) is None]
    if len(added) != 1:
        return f"adds {len(added)} amendment file(s) bible/amendments/A-<n>.json, not 1"
    path = added[0]
    text = gitstate.show(root, head, path)
    try:
        doc = json.loads(text) if text is not None else None
    except ValueError:
        doc = None
    kind = doc.get("class") if isinstance(doc, dict) else None
    if kind not in AMENDMENT_CLASSES:
        return f"{path} has no class among {', '.join(AMENDMENT_CLASSES)}"
    approved = pull["summary"]["checkers"].get(CANON_REVIEWER)
    if approved is None or approved["verb"] != "APPROVED":
        return f"{CANON_REVIEWER} has no counting APPROVED"
    if kind in VOTE_CLASSES or CHARTER in changed:
        enough, reason = _voted(entry, pull)
        if not enough:
            why = f"class {kind}" if kind in VOTE_CLASSES else f"a change to {CHARTER}"
            return (f"{path}: {why} needs a [{VOTE_ACTOR}] VOTE with {VOTE_MIN} or more for, "
                    "later than the maker's last READY or ADDRESSED")
        if reason:
            return f"{path}: the [{VOTE_ACTOR}] VOTE does not count: {reason}"
    return ""


def _writes(p, room, maker, changed):
    writes = frontmatter.as_list(p.roles[maker]["meta"].get("writes"))
    room_paths = [glob for e in production.stages(p, room) for glob in e["paths"]]
    outside_writes = [path for path in changed if not production.matches(path, writes)]
    outside_room = [path for path in changed if not production.matches(path, room_paths)]
    reasons = []
    if outside_writes:
        reasons.append(f"outside the writes of {maker}: {', '.join(outside_writes)}")
    if outside_room:
        reasons.append(f"outside room {room}: {', '.join(outside_room)}")
    return "; ".join(reasons)


def _answered(p, state, ticket, pull, notes, checker, note):
    """A later ADDRESSED says the note was changed, or a later RULING by the room's orchestrator, on the
    pull or the ticket, names the checker and the note."""
    after = _order(notes)
    changed = f"- {note} by {checker}: changed."
    for c in pull["comments"]:
        m = markers.parse(c["body"])
        if (m and m.verb == "ADDRESSED" and _order(c) > after
                and any(line.strip().startswith(changed) for line in c["body"].splitlines()[1:])):
            return True
    actor = p.actors[production.orchestrator(p, ticket["room"])]
    named = re.compile(r"(?<![\w-])%s %s\b" % (re.escape(checker), re.escape(note)))
    for c in pull["comments"] + state.ticket_comments.get(ticket["number"], []):
        m = markers.parse(c["body"])
        if m and m.verb == "RULING" and m.actor == actor and _order(c) > after and named.search(c["body"]):
            return True
    return False


def _repeat_notes(p, state, ticket, entry, pull):
    checkers = production.stage_roles(entry, "checker")
    open_notes = []
    for c in _ordered(pull["comments"]):
        m = markers.parse(c["body"])
        if not (m and m.verb == "NOTES" and m.actor.lower() in checkers):
            continue
        checker = m.actor.lower()
        for line in c["body"].splitlines()[1:]:
            found = _REPEAT.match(line.strip())
            if not found:
                continue
            note = found.group(1) or found.group(2)
            if not _answered(p, state, ticket, pull, c, checker, note):
                open_notes.append(f"{checker} {note} (#{c['id']})")
    if open_notes:
        return "repeated note(s) with no change or ruling: " + ", ".join(open_notes)
    return ""


def _gate(p, state, ticket, entry, pull, maker):
    if not gate_needed(p, state, ticket, entry, maker) or ledger.covers(pull["git"], pull["comments"], entry):
        return ""
    if production.stages(p, ticket["room"])[-1]["stage"] == entry["stage"]:
        why = f"stage {entry['stage']} is the last of room {ticket['room']}"
    else:
        why = f"ticket #{ticket['number']} passed CAP_TICKET_ITERATIONS for {maker}"
    return f"{why} and no counting [PRODUCER] GATE PASS covers the head"


def _base(ticket, pull, root):
    reasons = []
    if not gitstate.contains(root, pull["head_ref"], pull["base_ref"]):
        reasons.append(f"head does not contain the base {pull['base_ref']}")
    if tickets.HOLD in ticket["labels"]:
        reasons.append(f"ticket #{ticket['number']} has the label {tickets.HOLD}")
    if tickets.HOLD in pull["labels"]:
        reasons.append(f"PR #{pull['number']} has the label {tickets.HOLD}")
    return "; ".join(reasons)


def _released(pull, root):
    released, hits = {}, []
    for path in pull["git"]["changed"]:
        m = _EPISODE_FILE.match(path)
        if not m:
            continue
        episode = m.group(1)
        if episode not in released:
            released[episode] = gitstate.show(root, pull["base_ref"], RELEASE.format(episode)) is not None
        if released[episode]:
            hits.append(path)
    return f"changes files of a released episode: {', '.join(hits)}" if hits else ""


def evaluate(p, state, ticket, pull, root) -> list[Rule]:
    """The eleven rules for locking ``pull`` on ``ticket``, every one evaluated."""
    entry = production.stage(p, ticket["room"], ticket["stage"])
    changed = pull["git"]["changed"]
    maker = maker_role(entry, pull)
    no_maker = f"no maker of stage {entry['stage']} has posted READY"
    reasons = [
        _lint(entry, pull),
        _checkers(pull["summary"]),
        _judges(p, state, pull),
        _upstream(p, state, ticket, root),
        _selected(state, ticket, entry, pull),
        _amendment(entry, pull, root),
        _writes(p, ticket["room"], maker, changed) if maker else no_maker,
        _repeat_notes(p, state, ticket, entry, pull),
        _gate(p, state, ticket, entry, pull, maker) if maker else no_maker,
        _base(ticket, pull, root),
        _released(pull, root),
    ]
    return [Rule(number, name, not reason, reason)
            for number, (name, reason) in enumerate(zip(NAMES, reasons), 1)]


def lockable(rules) -> bool:
    """All eleven rules hold."""
    return len(rules) == len(NAMES) and all(rule.ok for rule in rules)
