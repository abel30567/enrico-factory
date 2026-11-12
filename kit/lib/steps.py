"""What comes next for a ticket: the next-step table (system brief sections 8, 9 and 12).

A pure reading of a ``tickets.State``: nothing here writes to GitHub or git. ``next_step`` gives the
first row of the table that applies; ``all_steps`` gives one entry per open pull, sorted by number,
then the entry for maker launches that have no pull yet. Each entry is
``{"state", "step", "role", "pull", "reason"}``.

Rows 1 to 5 are read on the ticket; rows 6 to 12 on each open pull, and a pull none of them applies
to is at row 14. Row 13 and the choice among pulls at row 14 are read on the ticket again.
"""

from __future__ import annotations

from datetime import datetime

from kit.lib import markers, production, tickets

RUN_TIMEOUT_MIN = "120"
RUN_ATTEMPTS = 2
RUN_MARGIN_MIN = 10


def _entry(state, step, role=None, pull=None, reason=""):
    return {"state": state, "step": step, "role": role, "pull": pull, "reason": reason}


def _order(comment):
    return (comment["created_at"], comment["id"])


def _marked(comments):
    """``(comment, marker)`` for every comment with a marker, oldest first."""
    found = []
    for comment in sorted(comments, key=_order):
        m = markers.parse(comment["body"])
        if m is not None:
            found.append((comment, m))
    return found


def _actor(p, role):
    return p.actors.get(role, role.upper())


def _minutes(earlier, later):
    fmt = production.TIME_FORMAT
    return (datetime.strptime(later, fmt) - datetime.strptime(earlier, fmt)).total_seconds() / 60


def _timeout(p):
    value = production.setting(p, "RUN_TIMEOUT_MIN", RUN_TIMEOUT_MIN)
    try:
        return float(value)
    except ValueError:
        raise tickets.TicketError("setting RUN_TIMEOUT_MIN is not a number") from None


def _wake(p, ticket, state, pull, reason):
    """A judgment for the room's orchestrator."""
    return _entry(state, "wake", production.orchestrator(p, ticket["room"]), pull, reason)


def _running(p, launch, role, pull, now):
    """Row 6 for a ``LAUNCH`` of ``role`` that no marker of that role follows. The run is dead only
    after every attempt the supervisor may make, plus a margin for its post."""
    age = _minutes(launch["created_at"], now)
    if age > RUN_ATTEMPTS * _timeout(p) + RUN_MARGIN_MIN:
        return _entry("running", "timeout", role, pull,
                      f"{role} launched at {launch['created_at']} has run {int(age)} min")
    return _entry("running", "none", role, pull, f"{role} launched at {launch['created_at']}")


def _waiting_launch(p, comments):
    """The oldest role whose latest ``LAUNCH`` in ``comments`` no marker of that role follows."""
    marked = _marked(comments)
    latest = {}
    for i, (comment, m) in enumerate(marked):
        if m.verb == "LAUNCH":
            latest[m.args[0]] = i
    open_launches = []
    for role, i in latest.items():
        actor = _actor(p, role)
        if not any(m.actor == actor for _, m in marked[i + 1:]):
            open_launches.append((i, role))
    if not open_launches:
        return None
    i, role = min(open_launches)
    return marked[i][0], role


def _third_notes(pull, entry, p):
    """The comment that is the third round of notes on the pull, as ``markers.summary`` counts rounds."""
    makers = production.stage_roles(entry, "maker")
    checkers = production.stage_roles(entry, "checker")
    ready_seen, rounds = False, 0
    for comment, m in _marked(pull["comments"]):
        role = m.actor.lower()
        if m.verb in markers.MAKER_VERBS and role in makers:
            ready_seen = ready_seen or m.verb == "READY"
            continue
        if ready_seen and m.verb == "NOTES" and role in checkers and p.kinds.get(role) == "checker":
            rounds += 1
            if rounds == 3:
                return comment
    return None


def _ruled_after(comments, comment):
    return any(m.verb == "RULING" and _order(c) > _order(comment) for c, m in _marked(comments))


def _maker_of(entry, summary):
    """The maker whose marker is the pull's latest, else the stage's first maker."""
    if summary["maker"]:
        return summary["maker"]["actor"].lower()
    return production.stage_roles(entry, "maker")[0]


def _pull_step(p, state, ticket, entry, pull, now, has_key):
    """Rows 6 to 12 for one open pull, else row 14 for it."""
    n = pull["number"]
    summary = pull["summary"]
    launch = _waiting_launch(p, pull["comments"])
    if launch:
        return _running(p, launch[0], launch[1], n, now)
    if summary["maker"] and summary["maker"]["verb"] == "BLOCKED":
        return _wake(p, ticket, "blocked", n, f"the maker is BLOCKED on #{n}")
    if summary["needs_ruling"]:
        comments = pull["comments"] + state.ticket_comments.get(ticket["number"], [])
        if not _ruled_after(comments, _third_notes(pull, entry, p)):
            return _wake(p, ticket, "ruling", n, f"three rounds of notes on #{n} and no ruling")
    lint = summary["lint"]
    if entry.get("lint"):
        if lint is None:
            return _entry("lint", "lint", None, n, f"no counting [LINT] verdict on #{n}")
        if lint["verb"] == "ERROR":
            return _wake(p, ticket, "lint-error", n, f"[LINT] ERROR counts on #{n}")
    noted = sorted(r for r, v in summary["checkers"].items() if v and v["verb"] == "NOTES")
    if lint and lint["verb"] == "FAIL" or noted:
        why = f"[LINT] FAIL on #{n}" if lint and lint["verb"] == "FAIL" else f"{noted[0]} NOTES on #{n}"
        return _entry("notes", "launch", _maker_of(entry, summary), n, why)
    for role, verdict in summary["checkers"].items():
        if verdict is None:
            return _entry("review", "launch", role, n, f"{role} has no counting verdict on #{n}")
    for role, verdict in summary["judges"].items():
        if verdict is None:
            unit = (ticket["room"], ticket["stage"], ticket["unit"])
            if entry.get("blind") and tickets.is_locked(state, *unit) and not has_key(pull):
                return _entry("judging", "blind", role, n, f"{ticket['unit']} has a locked version and #{n} no key")
            return _entry("judging", "launch", role, n, f"{role} has no counting verdict on #{n}")
    return _entry("lockable", "lock", None, n, f"#{n} has every counting verdict")


def _pending(p, state, ticket, entry, now):
    """Row 6 for maker launches on the ticket that have no pull yet, or None when there are none."""
    number = ticket["number"]
    makers = production.stage_roles(entry, "maker")
    marked = _marked(state.ticket_comments.get(number, []))
    launched = [(c, m) for c, m in marked if m.verb == "LAUNCH" and m.args[0] in makers]
    pending = max(0, len(launched) - len(tickets.pulls_of(state, number)))
    if not pending:
        return 0, None
    comment, m = launched[-1]
    role = m.args[0]
    later = [x for c, x in marked if _order(c) > _order(comment) and x.actor == _actor(p, role)]
    if later and later[-1].verb == "BLOCKED":
        return pending, _wake(p, ticket, "blocked", None, f"the maker is BLOCKED on #{number}")
    return pending, _running(p, comment, role, None, now)


def _ticket_step(p, state, ticket, entry, root):
    """Rows 1 to 4, or None."""
    number = ticket["number"]
    if state.frozen:
        return _entry("frozen", "none", reason=f"an open issue carries {tickets.FREEZE}")
    merged = tickets.locked_pull(state, number)
    if merged is not None:
        return _entry("locked", "none", pull=merged["number"], reason=f"#{merged['number']} is merged")
    if ticket["state"] == "closed":
        return _entry("locked", "none", reason=f"ticket #{number} is closed")
    if ticket["hold"]:
        return _entry("held", "none", reason=f"ticket #{number} has the label {tickets.HOLD}")
    ok, why = tickets.upstream(p, state, ticket, root)
    if not ok:
        return _entry("waiting", "none", reason=why)
    return None


def _steps(p, state, ticket, root, now, has_key):
    """``(ticket entry or None, per-pull entries, pending entry or None)``."""
    entry = production.stage(p, ticket["room"], ticket["stage"])
    first = _ticket_step(p, state, ticket, entry, root)
    if first:
        return first, [], None
    number = ticket["number"]
    pending, waiting = _pending(p, state, ticket, entry, now)
    open_pulls = [pull for pull in tickets.pulls_of(state, number) if pull["state"] == "open"]
    if len(open_pulls) + pending < entry["pitches"]:
        makers = production.stage_roles(entry, "maker")
        role = next((r for r in makers if tickets.launches(state, number, r) == 0), makers[0])
        used = len(open_pulls) + pending
        return _entry("ready", "launch", role, None,
                      f"{used} of {entry['pitches']} pitch(es) in use"), [], None
    return None, [_pull_step(p, state, ticket, entry, pull, now, has_key) for pull in open_pulls], waiting


def _always(pull):
    return True


def all_steps(p, state, ticket, root, now, has_key=_always) -> list:
    """Rows 1 to 5 as one entry; else one entry per open pull by number, then the pending launches."""
    first, per_pull, waiting = _steps(p, state, ticket, root, now, has_key)
    if first:
        return [first]
    return per_pull + ([waiting] if waiting else [])


def next_step(p, state, ticket, root, now, has_key=_always) -> dict:
    """The first row of the table that applies to the ticket."""
    first, per_pull, waiting = _steps(p, state, ticket, root, now, has_key)
    if first:
        return first
    if not per_pull:
        return waiting
    behind = [e for e in per_pull if e["state"] != "lockable"] + ([waiting] if waiting else [])
    if behind:
        return behind[0]
    entry = production.stage(p, ticket["room"], ticket["stage"])
    if entry["pitches"] > 1:
        chosen = tickets.selected(state, ticket["number"])
        found = next((e for e in per_pull if e["pull"] == chosen), None)
        if found is None:
            return _wake(p, ticket, "selection", None,
                         f"every pitch of #{ticket['number']} is lockable and none is selected")
        return found
    return per_pull[0]
