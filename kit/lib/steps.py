"""What comes next for a ticket: the next-step table (system brief sections 8, 9 and 12).

A pure reading of a ``tickets.State``: nothing here writes to GitHub or git. ``next_step`` gives the
first row of the table that applies; ``all_steps`` gives one entry per open pull, sorted by number,
then the entry for maker launches that have no pull yet. Each entry is
``{"state", "step", "role", "pull", "reason"}``.

Rows 1 to 5 are read on the ticket; rows 6 to 12 on each open pull, and a pull none of them applies
to is at row 14. Row 13 and the choice among pulls at row 14 are read on the ticket again.

A pull with a request at a stage with an operator (``kit.lib.jobs``) has more rows: before the lint, a
failed job, the operator's launch to write the jobs and its launch to report on them; in the place of
row 14, a stopped job, the gate and the run of each job. A ``gate`` or ``run`` entry also has ``job``,
the job path. While a job of the pull has not run, its gate and its run come right after the lint, before
the checkers and the judges, which read what the jobs make. A job of a shot stage waits while a cheaper stage of
its shot is not locked (``lockrules.cheaper_stages``), and its run waits while the resource of its tool has an
open down issue. A job stopped at the gate on a cap that was not set is gated again once that cap has a value.

At a stage of one pitch with two or more makers that are not operators, once one of them has a ``READY`` on the
pull and the pull has no request or every job ran ``DONE``, the next of them with no ``READY`` there is launched
on the pull before the lint. Notes go to the maker whose ``writes`` cover the first file a Blocker line cites,
else to the pull's newest maker.
"""

from __future__ import annotations

import re
from datetime import datetime

from kit.lib import cmd_gate, cmd_run, down, frontmatter, harness, jobs, lockrules, markers, production, tickets

RUN_TIMEOUT_MIN = "120"
RUN_ATTEMPTS = 2
RUN_MARGIN_MIN = 10
NOT_SET = "cap not set"
# The reasons of a launcher's BLOCKED for an outage that the launch checks wait out (a resource down, no
# usage left): the role is launched again, as before ISSUE-327.
OUTAGES = tuple(harness.PHRASES[s] for s in ("login_ended", "out_of_usage", "proxy_down")) + (
    "every account's login has ended", "every account is out of usage")
_BLOCKER_LINE = re.compile(r"(?:[-*]\s+)?B\d+\b")
_LINE_SUFFIX = re.compile(r":\d+(?:-\d+)?$")


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


def _answers(p, role, m):
    """Whether marker ``m`` answers a ``LAUNCH <role>``: the role's actor, and not a ``LAUNCH`` itself."""
    return m.actor == _actor(p, role) and m.verb != "LAUNCH"


def _waiting_launch(p, comments):
    """The oldest role whose latest ``LAUNCH`` in ``comments`` no marker of that role follows."""
    marked = _marked(comments)
    latest = {}
    for i, (comment, m) in enumerate(marked):
        if m.verb == "LAUNCH":
            latest[m.args[0]] = i
    open_launches = []
    for role, i in latest.items():
        if not any(_answers(p, role, m) for _, m in marked[i + 1:]):
            open_launches.append((i, role))
    if not open_launches:
        return None
    i, role = min(open_launches)
    return marked[i][0], role


def _latest_round(pull, entry, p):
    """The comment that is the latest round of notes on the pull, as ``markers.summary`` counts rounds."""
    makers = production.stage_roles(entry, "maker")
    checkers = production.stage_roles(entry, "checker")
    ready_seen, latest = False, None
    for comment, m in _marked(pull["comments"]):
        role = m.actor.lower()
        if m.verb in markers.MAKER_VERBS and role in makers:
            ready_seen = ready_seen or m.verb == "READY"
            continue
        if ready_seen and m.verb == "NOTES" and role in checkers and p.kinds.get(role) == "checker":
            latest = comment
    return latest


def _launched(comments, role):
    """The ``LAUNCH <role>`` comments in ``comments``, oldest first."""
    return [c for c, m in _marked(comments) if m.verb == "LAUNCH" and m.args[0] == role]


def _unanswered(p, comments, role):
    """The newest ``LAUNCH <role>`` in ``comments`` when no later marker answers it, else None."""
    launched = _launched(comments, role)
    if not launched:
        return None
    last = launched[-1]
    if any(_answers(p, role, m) for c, m in _marked(comments) if _order(c) > _order(last)):
        return None
    return last


def _ruled_after(comments, comment):
    return any(m.verb == "RULING" and _order(c) > _order(comment) for c, m in _marked(comments))


def _ruling_after(p, ticket, comments, comment):
    """Whether a ``RULING`` of the room's orchestrator in ``comments`` is newer than ``comment``."""
    actor = _actor(p, production.orchestrator(p, ticket["room"]))
    return any(m.verb == "RULING" and m.actor == actor and _order(c) > _order(comment)
               for c, m in _marked(comments))


def _maker_of(p, entry, pull, root):
    """The maker, not an operator, whose marker is the pull's latest, else the stage's first such maker; at a
    stage whose makers are all operators, the pull's operator, else the place's first name."""
    names = production.stage_roles(entry, "maker")
    makers = [r for r in names if p.kinds.get(r) != "operator"]
    if not makers:
        return jobs.operator_of(p, root, entry, pull) or names[0]
    found = makers[0]
    for _, m in _marked(pull["comments"]):
        if m.verb in markers.MAKER_VERBS and m.actor.lower() in makers:
            found = m.actor.lower()
    return found


def _makers(p, entry):
    """The stage's makers that are not operators, in the stage's order."""
    return [r for r in production.stage_roles(entry, "maker") if p.kinds.get(r) != "operator"]


def _next_maker(p, entry, pull, found):
    """At a stage of one pitch with two or more makers that are not operators: once one of them has a
    ``READY`` on the pull, and the pull has no request (``found`` is None) or none is uncovered and every
    job ran ``DONE``, the first of them in the stage's order with no ``READY`` there; else None."""
    makers = _makers(p, entry)
    if entry["pitches"] != 1 or len(makers) < 2:
        return None
    if found and (found["uncovered"] or not all(job["run"] == "DONE" for job in found["jobs"])):
        return None
    ready = {m.actor for _, m in _marked(pull["comments"]) if m.verb == "READY"}
    if not any(_actor(p, r) in ready for r in makers):
        return None
    return next((r for r in makers if _actor(p, r) not in ready), None)


def _cited_maker(p, entry, body):
    """The maker, not an operator, whose ``writes`` match the first path a Blocker line of ``body`` cites
    (``- B<n> <check> <path>:<line> ...`` or ``B<n> (Blocker): ...``), the first in the stage's order if two
    match; else None. The marker line is not read."""
    makers = _makers(p, entry)
    writes = {r: frontmatter.as_list(p.roles[r]["meta"].get("writes")) for r in makers if r in p.roles}
    for line in body.splitlines()[1:]:
        line = line.strip()
        if not _BLOCKER_LINE.match(line):
            continue
        for word in line.split():
            word = _LINE_SUFFIX.sub("", word.strip("`*,;.()"))
            role = next((r for r in makers if production.matches(word, writes.get(r, []))), None)
            if role:
                return role
    return None


def _operator_left(p, entry, pull, role):
    """Whether the operator's newest marker on the pull is newer than every other maker's newest
    ``READY`` or ``ADDRESSED``."""
    others = [r for r in production.stage_roles(entry, "maker") if r != role]
    mine, theirs = None, None
    for comment, m in _marked(pull["comments"]):
        if m.actor == _actor(p, role) and m.verb != "LAUNCH":
            mine = comment
        elif m.verb in ("READY", "ADDRESSED") and m.actor.lower() in others:
            theirs = comment
    return mine is not None and (theirs is None or _order(mine) > _order(theirs))


def _outage(body):
    """Whether a ``BLOCKED`` body is a launcher's for an outage in ``OUTAGES``."""
    return any(line.startswith(f"Missing: a valid result ({o}") for line in body.splitlines() for o in OUTAGES)


def _reviewer_blocked(p, state, ticket, entry, pull, role):
    """Whether the checker or judge ``role``, which has no counting verdict on the pull, is BLOCKED: its
    newest marker there among the verbs its kind may post is ``BLOCKED``, not for an outage, and no maker's
    ``READY`` or ``ADDRESSED`` and no ``RULING`` of the room's orchestrator is newer."""
    verbs = markers.MAY_POST.get(p.kinds.get(role), ())
    marked = _marked(pull["comments"])
    newest = None
    for comment, m in marked:
        if m.actor == _actor(p, role) and m.verb in verbs:
            newest = (comment, m)
    if newest is None or newest[1].verb != "BLOCKED" or _outage(newest[0]["body"]):
        return False
    blocked = newest[0]
    makers = production.stage_roles(entry, "maker")
    if any(m.verb in ("READY", "ADDRESSED") and m.actor.lower() in makers and _order(c) > _order(blocked)
           for c, m in marked):
        return False
    comments = pull["comments"] + state.ticket_comments.get(ticket["number"], [])
    return not _ruling_after(p, ticket, comments, blocked)


def _job_rows(p, ticket, entry, pull, found, root):
    """The rows read before the lint for a pull with a request, or None."""
    n, role = pull["number"], found["operator"]
    for job in found["jobs"]:
        if job["run"] == "FAILED":
            return _wake(p, ticket, "job-failed", n, f"job {job['path']} failed on #{n}")
    k = len(found["uncovered"])
    if k:
        if _operator_left(p, entry, pull, role):
            return _wake(p, ticket, "jobs", n, f"{role} left {k} request(s) without a job on #{n}")
        return _entry("jobs", "launch", role, n, f"{k} request(s) on #{n} have no job")
    if all(job["run"] == "DONE" for job in found["jobs"]):
        launched = _launched(pull["comments"], role)
        if any(job["gate"] == "PASS" and (not launched or _order(job["gate_comment"]) > _order(launched[-1]))
               for job in found["jobs"]):
            k = len(found["jobs"])
            return _entry("report", "launch", role, n, f"{k} job(s) of #{n} ran and {role} has not reported")
        maker = _maker_of(p, entry, pull, root)
        if maker != role and _operator_left(p, entry, pull, role):
            k = len(found["jobs"])
            return _entry("cite", "launch", maker, n,
                          f"{role} reported {k} job(s) on #{n} and {maker} has not cited their media")
    return None


def _set_since(p, job):
    """The cap a ``STOP`` of ``job`` named as not set that has a value now, else None."""
    lines = job["gate_comment"]["body"].split("\n")
    for name, value in production.caps(p).items():
        if cmd_gate._cap_line(name, NOT_SET) in lines and value is not None:
            return name
    return None


def _down(p, state, job):
    """``(resource id, issue number)`` while the resource of the job's tool has an open down issue, else None."""
    issues = [i for i in state.others if i["state"] == "open" and down.LABEL in i["labels"]]
    if not issues:
        return None
    try:
        resource = cmd_run._load_tool(job["job"]["tool"]).RESOURCE
    except cmd_run.Refused:
        return None
    found = down._matching(issues, resource)
    return (resource, found[0]) if found else None


def _job_steps(p, state, ticket, pull, found, root):
    """The rows read in the place of row 14 for a pull with a request, or None."""
    n = pull["number"]
    for job in found["jobs"]:
        if job["gate"] == "STOP" and _set_since(p, job) is None:
            return _wake(p, ticket, "job-stopped", n, f"job {job['path']} stopped at the gate on #{n}")
    waiting = [job for job in found["jobs"] if job["gate"] != "PASS" or job["run"] is None]
    held = lockrules.cheaper_stages(p, state, ticket, root) if waiting else ""
    for job in waiting:
        if job["gate"] != "PASS":
            if held:
                return _entry("gate", "none", None, n, held)
            why = (f"job {job['path']} has no gate decision" if job["gate"] is None
                   else f"job {job['path']} stopped at the gate on {_set_since(p, job)}, which is set now")
            return {**_entry("gate", "gate", None, n, why), "job": job["path"]}
    for job in waiting:
        if held:
            return _entry("run", "none", None, n, held)
        stopped = _down(p, state, job)
        if stopped:
            return _entry("run", "none", None, n, f"resource {stopped[0]} is down (#{stopped[1]})")
        return {**_entry("run", "run", None, n, f"job {job['path']} has a PASS and has not run"), "job": job["path"]}
    return None


def _pull_step(p, state, ticket, entry, pull, now, has_key, root):
    """Rows 6 to 12 for one open pull, else row 14 for it."""
    n = pull["number"]
    summary = pull["summary"]
    launch = _waiting_launch(p, pull["comments"])
    if launch:
        return _running(p, launch[0], launch[1], n, now)
    if summary["maker"] and summary["maker"]["verb"] == "BLOCKED":
        blocked = next(c for c in pull["comments"] if c["id"] == summary["maker"]["id"])
        comments = pull["comments"] + state.ticket_comments.get(ticket["number"], [])
        if _ruling_after(p, ticket, comments, blocked):
            actor = summary["maker"]["actor"].lower()
            role = actor if p.kinds.get(actor) == "operator" else _maker_of(p, entry, pull, root)
            return _entry("blocked", "launch", role, n, f"the maker is BLOCKED on #{n} and has a ruling")
        role = production.orchestrator(p, ticket["room"])
        if _launched(pull["comments"], role):
            return _wake(p, ticket, "blocked", n, f"the maker is BLOCKED on #{n}")
        return _entry("blocked", "launch", role, n, f"the maker is BLOCKED on #{n}")
    if summary["needs_ruling"]:
        comments = pull["comments"] + state.ticket_comments.get(ticket["number"], [])
        if not _ruled_after(comments, _latest_round(pull, entry, p)):
            why = f"three rounds of notes on #{n} and no ruling"
            role = production.orchestrator(p, ticket["room"])
            if _launched(pull["comments"], role):
                return _wake(p, ticket, "ruling", n, why)
            return _entry("ruling", "launch", role, n, why)
    found = jobs.of(p, root, state, ticket, pull)
    if found:
        row = _job_rows(p, ticket, entry, pull, found, root)
        if row:
            return row
        # Section 12, step 5: once a gated job has run, the other jobs run before the review is made again.
        if all(job["gate"] == "PASS" for job in found["jobs"]) and any(job["run"] for job in found["jobs"]):
            row = _job_steps(p, state, ticket, pull, found, root)
            if row:
                return row
    role = _next_maker(p, entry, pull, found)
    if role:
        return _entry("maker", "launch", role, n, f"{role} has no READY on #{n}")
    lint = summary["lint"]
    if entry.get("lint"):
        if lint is None:
            return _entry("lint", "lint", None, n, f"no counting [LINT] verdict on #{n}")
        if lint["verb"] == "ERROR":
            return _wake(p, ticket, "lint-error", n, f"[LINT] ERROR counts on #{n}")
    noted = sorted(r for r, v in summary["checkers"].items() if v and v["verb"] == "NOTES")
    if lint and lint["verb"] == "FAIL" or noted:
        why = f"[LINT] FAIL on #{n}" if lint and lint["verb"] == "FAIL" else f"{noted[0]} NOTES on #{n}"
        if not production.stage_roles(entry, "maker"):
            return _entry("notes", "none", None, n, f"{why} and stage {ticket['stage']} has no maker")
        counted = lint if lint and lint["verb"] == "FAIL" else summary["checkers"][noted[0]]
        body = next(c["body"] for c in pull["comments"] if c["id"] == counted["id"])
        role = _cited_maker(p, entry, body) or _maker_of(p, entry, pull, root)
        return _entry("notes", "launch", role, n, why)
    if found and any(job["run"] is None for job in found["jobs"]):
        # The checkers and judges read what the jobs make: they come after every job has run.
        return _job_steps(p, state, ticket, pull, found, root)
    stuck = None
    for role, verdict in summary["checkers"].items():
        if verdict is None:
            if _reviewer_blocked(p, state, ticket, entry, pull, role):
                stuck = stuck or role
                continue
            return _entry("review", "launch", role, n, f"{role} has no counting verdict on #{n}")
    if stuck:
        return _wake(p, ticket, "review", n, f"{stuck} is BLOCKED on #{n}")
    for role, verdict in summary["judges"].items():
        if verdict is None:
            if _reviewer_blocked(p, state, ticket, entry, pull, role):
                stuck = stuck or role
                continue
            unit = (ticket["room"], ticket["stage"], ticket["unit"])
            if entry.get("blind") and tickets.is_locked(state, *unit) and not has_key(pull):
                return _entry("judging", "blind", role, n, f"{ticket['unit']} has a locked version and #{n} no key")
            return _entry("judging", "launch", role, n, f"{role} has no counting verdict on #{n}")
    if stuck:
        return _wake(p, ticket, "judging", n, f"{stuck} is BLOCKED on #{n}")
    if found:
        row = _job_steps(p, state, ticket, pull, found, root)
        if row:
            return row
    return _entry("lockable", "lock", None, n, f"#{n} has every counting verdict")


def _maker_launches(p, ticket, marked, makers):
    """The maker ``LAUNCH`` comments on the ticket, oldest first, less those set aside: a launch whose
    first answer is ``BLOCKED`` with a ``RULING`` of the room's orchestrator newer than that answer."""
    comments = [c for c, _ in marked]
    found = []
    for comment, m in marked:
        if m.verb != "LAUNCH" or m.args[0] not in makers:
            continue
        answer = next(((c, x) for c, x in marked if _order(c) > _order(comment) and _answers(p, m.args[0], x)),
                      None)
        if answer and answer[1].verb == "BLOCKED" and _ruling_after(p, ticket, comments, answer[0]):
            continue
        found.append((comment, m))
    return found


def _blocked_answer(p, marked, comment, role):
    """The newest marker of ``role``'s actor after ``comment`` when it is ``BLOCKED``, else None."""
    later = [(c, x) for c, x in marked if _order(c) > _order(comment) and x.actor == _actor(p, role)]
    if later and later[-1][1].verb == "BLOCKED":
        return later[-1][0]
    return None


def _pending(p, state, ticket, entry, now):
    """Row 6 for maker launches on the ticket that have no pull yet, or None when there are none."""
    number = ticket["number"]
    makers = production.stage_roles(entry, "maker")
    comments = state.ticket_comments.get(number, [])
    marked = _marked(comments)
    launched = _maker_launches(p, ticket, marked, makers)
    pending = max(0, len(launched) - len(tickets.pulls_of(state, number)))
    if not pending:
        return 0, None
    comment, m = launched[-1]
    role = m.args[0]
    if _blocked_answer(p, marked, comment, role) is not None:
        why = f"the maker is BLOCKED on #{number}"
        orchestrator = production.orchestrator(p, ticket["room"])
        running = _unanswered(p, comments, orchestrator)
        if running is not None:
            return pending, _running(p, running, orchestrator, None, now)
        if _launched(comments, orchestrator):
            return pending, _wake(p, ticket, "blocked", None, why)
        return pending, _entry("blocked", "launch", orchestrator, None, why)
    return pending, _running(p, comment, role, None, now)


def blocked_on_ticket(p, state, ticket):
    """The ``BLOCKED`` comment that answers the newest maker launch on the ticket ``_pending`` reads, or
    None."""
    entry = production.stage(p, ticket["room"], ticket["stage"])
    marked = _marked(state.ticket_comments.get(ticket["number"], []))
    launched = _maker_launches(p, ticket, marked, production.stage_roles(entry, "maker"))
    if len(launched) <= len(tickets.pulls_of(state, ticket["number"])):
        return None
    comment, m = launched[-1]
    return _blocked_answer(p, marked, comment, m.args[0])


def blocked_reviewers(p, state, ticket, pull, kind):
    """The newest ``BLOCKED`` comment of each ``kind`` (``checker`` or ``judge``) of the stage that has no
    counting verdict on the pull and is BLOCKED there (``_reviewer_blocked``), oldest first."""
    entry = production.stage(p, ticket["room"], ticket["stage"])
    marked = _marked(pull["comments"])
    found = []
    for role, verdict in pull["summary"][kind + "s"].items():
        if verdict is None and _reviewer_blocked(p, state, ticket, entry, pull, role):
            found.append([c for c, m in marked if m.actor == _actor(p, role) and m.verb == "BLOCKED"][-1])
    return sorted(found, key=_order)


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
        if not makers:
            why = f"stage {ticket['stage']} has no maker and #{number} has no open pull"
            return _entry("waiting", "none", reason=why), [], None
        makers = [r for r in makers if p.kinds.get(r) != "operator"] or makers
        role = next((r for r in makers if tickets.launches(state, number, r) == 0), makers[0])
        used = len(open_pulls) + pending
        return _entry("ready", "launch", role, None,
                      f"{used} of {entry['pitches']} pitch(es) in use"), [], None
    return None, [_pull_step(p, state, ticket, entry, pull, now, has_key, root) for pull in open_pulls], waiting


def _always(pull):
    return True


def _selection(p, state, ticket, entry, now):
    """Row 14 when no pitch is selected: the orchestrator's run on the ticket, its launch, or a wake. Only a
    launch of the orchestrator newer than the ticket's newest maker launch counts."""
    role = production.orchestrator(p, ticket["room"])
    comments = state.ticket_comments.get(ticket["number"], [])
    why = f"every pitch of #{ticket['number']} is lockable and none is selected"
    running = _unanswered(p, comments, role)
    if running is not None:
        return _running(p, running, role, None, now)
    makers = production.stage_roles(entry, "maker")
    made = [c for c, m in _marked(comments) if m.verb == "LAUNCH" and m.args[0] in makers]
    if not [c for c in _launched(comments, role) if not made or _order(c) > _order(made[-1])]:
        return _entry("selection", "launch", role, None, why)
    return _wake(p, ticket, "selection", None, why)


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
            return _selection(p, state, ticket, entry, now)
        return found
    return per_pull[0]


def _running_unanswered(p, comments, skip=()):
    """The roles not in ``skip`` whose newest ``LAUNCH`` in ``comments`` no later marker answers."""
    marked = _marked(comments)
    latest = {}
    for i, (_, m) in enumerate(marked):
        if m.verb == "LAUNCH" and m.args[0] not in skip:
            latest[m.args[0]] = i
    return sum(1 for role, i in latest.items() if not any(_answers(p, role, m) for _, m in marked[i + 1:]))


def _running_makers(p, state, ticket, makers):
    """The maker launches on the ticket that have no pull yet and are not set aside, less one when the
    newest is ``BLOCKED``."""
    number = ticket["number"]
    marked = _marked(state.ticket_comments.get(number, []))
    launched = _maker_launches(p, ticket, marked, makers)
    pending = max(0, len(launched) - len(tickets.pulls_of(state, number)))
    if not pending:
        return 0
    comment, m = launched[-1]
    if _blocked_answer(p, marked, comment, m.args[0]) is not None:
        return pending - 1
    return pending


def running(p, state, ticket) -> int:
    """The workers running for an open ticket: one per ``LAUNCH`` on its open pulls or on the ticket that no
    marker answers. A pure reading: no setting, no clock."""
    makers = production.stage_roles(production.stage(p, ticket["room"], ticket["stage"]), "maker")
    count = sum(_running_unanswered(p, pull["comments"])
                for pull in tickets.pulls_of(state, ticket["number"]) if pull["state"] == "open")
    count += _running_makers(p, state, ticket, makers)
    return count + _running_unanswered(p, state.ticket_comments.get(ticket["number"], []), makers)
