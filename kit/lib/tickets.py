"""What is on GitHub for the tickets of a production (system brief sections 6, 8 and 9).

A pure reading: nothing here writes to GitHub or git. GitHub is read through the adapter passed in
(``kit.lib.github``) and git only through ``kit.lib.gitstate``.

A ticket is an issue titled ``[KEY] summary`` with labels ``tracked``, ``room:<room>``,
``stage:<stage>`` and ``unit:<unit-id>``. A unit is locked when a tracked ticket for it has a merged
pull request; the ``LOCK`` marker is only the record of that and is never read here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from kit.lib import frontmatter, gitstate, markers, production

FREEZE = "factory:freeze"
TRACKED = "tracked"
HOLD = "hold"
LABEL_KEYS = ("room", "stage", "unit")

_TITLE = re.compile(r"\[([^\[\]\s]+)\]\s+(\S.*)")


class TicketError(Exception):
    pass


@dataclass
class State:
    tickets: dict[int, dict] = field(default_factory=dict)
    pulls: dict[int, dict] = field(default_factory=dict)
    ticket_comments: dict[int, list] = field(default_factory=dict)
    frozen: bool = False
    errors: list[str] = field(default_factory=list)


def parse(issue: dict) -> dict | None:
    """The ticket an issue is, None when it is not tracked; ``TicketError`` when it is malformed."""
    labels = issue["labels"]
    if TRACKED not in labels:
        return None
    number = issue["number"]
    m = _TITLE.fullmatch(issue["title"].strip())
    if not m:
        raise TicketError(f"ticket #{number}: title is not [KEY] summary")
    ticket = {"number": number, "key": m.group(1), "summary": m.group(2).strip()}
    for key in LABEL_KEYS:
        values = [label[len(key) + 1:] for label in labels if label.startswith(key + ":")]
        if not values:
            raise TicketError(f"ticket #{number}: no {key}: label")
        if len(values) > 1:
            raise TicketError(f"ticket #{number}: {len(values)} {key}: labels")
        if not values[0]:
            raise TicketError(f"ticket #{number}: empty {key}: label")
        ticket[key] = values[0]
    ticket.update(hold=HOLD in labels, state=issue["state"], created_at=issue["created_at"],
                  labels=list(labels))
    return ticket


def episode(unit: str) -> str:
    """The unit id up to its first ``/``."""
    return unit.split("/", 1)[0]


def _frozen(issues: list) -> bool:
    return any(i["state"] == "open" and FREEZE in i["labels"] for i in issues)


def frozen(gh) -> bool:
    """True when an open issue carries the label ``factory:freeze``."""
    return _frozen(gh.issues("open"))


def load(p, gh, root) -> State:
    """Every ticket, the pulls that name one and, for an open pull, its git state. Comments are read only for
    an open ticket and for a pull that is open or names an open ticket; any other pull holds none."""
    issues = gh.issues("all")
    state = State(frozen=_frozen(issues))
    entries = {}
    for issue in issues:
        try:
            ticket = parse(issue)
        except TicketError as error:
            state.errors.append(str(error))
            continue
        if ticket is None:
            continue
        entry = production.stage(p, ticket["room"], ticket["stage"])
        if entry is None:
            state.errors.append(f"ticket #{ticket['number']}: stage {ticket['room']}/{ticket['stage']} "
                                "is not in the pipeline")
            continue
        state.tickets[ticket["number"]] = ticket
        entries[ticket["number"]] = entry
    for number in sorted(state.tickets):
        if state.tickets[number]["state"] == "open":
            state.ticket_comments[number] = gh.comments(number)
    for pull in gh.pulls("all"):
        number = pull.get("ticket")
        if number not in state.tickets:
            continue
        read = pull["state"] == "open" or state.tickets[number]["state"] == "open"
        comments = gh.comments(pull["number"]) if read else []
        pull = {**pull, "comments": comments}
        if pull["state"] == "open":
            pull["git"] = gitstate.pull_state(root, pull, comments)
            pull["summary"] = markers.summary(pull["git"], entries[number], p.kinds)
        state.pulls[pull["number"]] = pull
    state.errors.sort()
    return state


def pulls_of(state: State, number: int) -> list:
    """The ticket's pulls, in any state, sorted by number."""
    return [state.pulls[n] for n in sorted(state.pulls) if state.pulls[n]["ticket"] == number]


def locked_pull(state: State, number: int) -> dict | None:
    """The ticket's merged pull, the lowest numbered when there are several."""
    return next((pull for pull in pulls_of(state, number) if pull["state"] == "merged"), None)


def is_locked(state: State, room: str, stage: str, unit: str) -> bool:
    """Some ticket for this room, stage and unit has a merged pull."""
    return any(t["room"] == room and t["stage"] == stage and t["unit"] == unit
               and locked_pull(state, n) is not None for n, t in state.tickets.items())


def _prefix(short: str, long: str) -> bool:
    """``short`` is ``long`` or a ``/``-prefix of it."""
    return short == long or long.startswith(short + "/")


def _format(p, root, unit: str) -> str | None:
    """The format the unit's episode premise names on the base, or None."""
    text = gitstate.show(root, production.base(p), f"episodes/{episode(unit)}/premise.md")
    if text is None:
        return None
    try:
        meta, _ = frontmatter.parse(text)
    except frontmatter.FrontMatterError:
        return None
    return meta.get("format") or None


def skips(p, root, unit: str) -> list:
    """The stages skipped by the format the unit's episode premise names on the base; empty when none."""
    format_id = _format(p, root, unit)
    return frontmatter.as_list(p.formats[format_id]["meta"].get("skips")) if format_id in p.formats else []


def sequences(p, root, episode: str) -> list:
    """The directory names ``<Q>`` of ``episodes/<episode>/sequences/<Q>/sequence.json`` on the base, sorted."""
    prefix = f"episodes/{episode}/sequences/"
    sha = gitstate.head(root, production.base(p))
    out = gitstate.git(root, "--literal-pathspecs", "ls-tree", "-r", "-z", "--name-only", sha, "--", prefix)
    found = []
    for path in out.split("\0"):
        parts = path[len(prefix):].split("/") if path.startswith(prefix) else []
        if len(parts) == 2 and parts[0] and parts[1] == "sequence.json":
            found.append(parts[0])
    return sorted(found)


def upstream(p, state: State, ticket: dict, root) -> tuple[bool, str]:
    """Brief section 9 rule 4: the upstream stage of the same unit is locked, or the format skips it.

    An ``episode`` stage after a ``sequence`` stage needs it locked for every sequence of the episode on
    the base; a ``sequence`` stage after an ``episode`` stage needs it locked for the sequence's episode.
    """
    room, unit = ticket["room"], ticket["unit"]
    entry = production.stage(p, room, ticket["stage"])
    after = entry["after"]
    skipped = skips(p, root, unit) if after else []
    skipped_by = _format(p, root, unit) if after and after in skipped else None
    while after and after in skipped:
        after = production.stage(p, room, after)["after"]
    note = f"skipped by format {skipped_by}: " if skipped_by else ""
    if not after:
        return True, note + "no upstream stage"
    theirs_type, ours_type = production.stage(p, room, after)["unit"], entry["unit"]
    if ours_type == "episode" and theirs_type == "sequence":
        found = sequences(p, root, unit)
        if not found:
            return False, note + f"upstream stage {after} is not locked for {unit}: no sequence on the base"
        for q in found:
            if not is_locked(state, room, after, f"{unit}/{q}"):
                return False, note + f"upstream stage {after} is not locked for {unit}/{q}"
        return True, note + f"upstream stage {after} is locked for {unit}"
    unrelated = theirs_type != ours_type and not (ours_type == "sequence" and theirs_type == "episode")
    for number, other in state.tickets.items():
        if other["room"] != room or other["stage"] != after or locked_pull(state, number) is None:
            continue
        theirs = other["unit"]
        if (_prefix(theirs, unit)
                or unrelated and not _prefix(unit, theirs)):
            return True, note + f"upstream stage {after} is locked for {unit}"
    return False, note + f"upstream stage {after} is not locked for {unit}"


def _marker(comment):
    return markers.parse(comment["body"])


def launches(state: State, number: int, role: str) -> int:
    """Comments on the ticket and its pulls whose marker is ``LAUNCH <role> ...``."""
    comments = list(state.ticket_comments.get(number, []))
    for pull in pulls_of(state, number):
        comments += pull["comments"]
    count = 0
    for comment in comments:
        m = _marker(comment)
        if m and m.verb == "LAUNCH" and m.args[0] == role:
            count += 1
    return count


def selected(state: State, number: int) -> int | None:
    """The pull number of the latest ``SELECTED`` marker on the ticket."""
    found = None
    for comment in sorted(state.ticket_comments.get(number, []), key=lambda c: (c["created_at"], c["id"])):
        m = _marker(comment)
        if m and m.verb == "SELECTED":
            found = int(m.args[0])
    return found
