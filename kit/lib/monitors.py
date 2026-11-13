"""The producer's monitors the tick runs (system brief section 11): budget, canon contradiction, no convergence,
charter drift.

A pure reading: nothing here writes to GitHub, git or the disk. ``trips(p, state, rows, now, read)`` gives one
dict per trip, budget trips first (caps in the order of ``_SEASON`` then ``_EPISODE``, episodes sorted),
then canon by pull number, then no convergence by ticket number, then charter drift (one trip at most; only
when ``read``, the text of a path on the base, is given). A monitor that cannot be read gives
``{"monitor": <name>, "error": <exception>}`` in its place and no trip. ``instead(p, trips, ticket, entry)``
is the step the tick takes in place of ``entry`` (a ``steps._entry``), or ``entry`` itself.

A trip holds ``monitor``, ``line`` (after ``NEED - ``), ``title`` and ``body`` of its ``[MONITOR]`` issue.
No amount and no cap value is written anywhere.
"""

from __future__ import annotations

import re
from decimal import Decimal
from fractions import Fraction

from kit.lib import blind, gitstate, ledger, markers, production, tickets

NO_WIN_ROUNDS = 3
SHIP_ROUNDS = 2 * NO_WIN_ROUNDS
DRIFT_EPISODES = 3
CHARTER = "bible/charter.md"  # lockrules.CHARTER
MONITOR_ERRORS = (production.ProductionError, blind.BlindError, OSError, gitstate.GitError)
STOPPED = ("launch", "blind", "gate", "run")

_SEASON = (("CAP_SEASON_USD", "usd"), ("CAP_SEASON_COMPUTE_MIN", "compute_min"), ("CAP_SEASON_HOURS", None))
_EPISODE = (("CAP_EPISODE_USD", "usd"), ("CAP_EPISODE_COMPUTE_MIN", "compute_min"), ("CAP_EPISODE_HOURS", None))
_CANON = re.compile(r"- B\d+ canon episodes/[^/]+/ledger\.json:\d+ .*")


def _order(comment):
    return (comment["created_at"], comment["id"])


def _body(lines) -> str:
    return "\n".join(lines) + "\n"


def _open(state) -> list:
    return [state.tickets[n] for n in sorted(state.tickets) if state.tickets[n]["state"] == "open"]


def _ruled_after(p, state, ticket, pulls, after) -> bool:
    """A ``RULING`` by the room's orchestrator on the ticket or one of ``pulls``, newer than ``after``."""
    actor = p.actors[production.orchestrator(p, ticket["room"])]
    comments = list(state.ticket_comments.get(ticket["number"], []))
    for pull in pulls:
        comments += pull["comments"]
    for comment in comments:
        m = markers.parse(comment["body"])
        if m is not None and m.verb == "RULING" and m.actor == actor and _order(comment) > after:
            return True
    return False


# -- budget

def _budget_trip(cap, episode):
    scope = "the season" if episode is None else f"episode {episode}"
    return {"monitor": "budget", "cap": cap, "episode": episode,
            "reason": f"{cap} is passed for {scope}",
            "line": f"monitor budget: {cap} is passed for {scope}",
            "title": f"[MONITOR] budget {cap} " + ("season" if episode is None else f"episode {episode}"),
            "body": _body(["Monitor: budget", f"Cap: {cap}", f"Scope: {scope}",
                           "Action: stop the run, ship the best so far (system brief section 11).",
                           "To clear: raise the cap in production.env, then close this issue."])}


def _budget(p, state, rows, now) -> list:
    caps = {name: None if value is None else Decimal(str(value)) for name, value in production.caps(p).items()}
    everyone = list(state.tickets.values())
    found = []

    def check(names, episode, group):
        spent = ledger.spent(rows) if episode is None else ledger.spent(rows, episode)
        for name, what in names:
            if caps[name] is None:
                continue
            amount = spent[what] if what else ledger._hours([t["created_at"] for t in group], now)
            if amount is not None and amount > caps[name]:
                found.append(_budget_trip(name, episode))

    check(_SEASON, None, everyone)
    for episode in sorted({tickets.episode(t["unit"]) for t in _open(state)}):
        check(_EPISODE, episode, [t for t in everyone if tickets.episode(t["unit"]) == episode])
    return found


# -- canon contradiction

def _canon(p, state) -> list:
    found = []
    for number in sorted(state.pulls):
        pull = state.pulls[number]
        ticket = state.tickets[pull["ticket"]]
        if pull["state"] != "open" or ticket["state"] != "open":
            continue
        lint = pull["summary"]["lint"]
        if lint is None or lint["verb"] != "FAIL":
            continue
        comment = next(c for c in pull["comments"] if c["id"] == lint["id"])
        if not any(_CANON.fullmatch(line.strip()) for line in comment["body"].splitlines()):
            continue
        role = production.orchestrator(p, ticket["room"])
        if _ruled_after(p, state, ticket, [pull], _order(comment)):
            continue
        n = ticket["number"]
        reason = f"[LINT] FAIL on #{number} finds a fact against the canon ledger"
        found.append({"monitor": "canon", "ticket": n, "pull": number, "reason": reason,
                      "line": f"monitor canon: #{n} {reason}",
                      "title": f"[MONITOR] canon PR #{number}",
                      "body": _body(["Monitor: canon contradiction", f"Ticket: #{n}", f"Pull request: #{number}",
                                     f"Action: block the lock, route to head of story ({role}) "
                                     "(system brief section 11).",
                                     "To clear: post a RULING on the ticket or the pull, then close this issue."])})
    return found


# -- no convergence

def _rounds(p, state, ticket, judges) -> list:
    """``(newest verdict order, won)`` per head of the ticket's pulls every judge has a verdict on, oldest
    first. A head with no blinding key is no round."""
    actors = {p.actors[role]: role for role in judges}
    found = []
    for pull in tickets.pulls_of(state, ticket["number"]):
        newest = {}  # (head, role) -> (comment, verb)
        for comment in sorted(pull["comments"], key=_order):
            m = markers.parse(comment["body"])
            seen = markers.reviewed(comment["body"])
            if m is None or m.actor not in actors or m.verb not in blind.PREFERENCES or seen is None:
                continue
            newest[(seen[0], actors[m.actor])] = (comment, m.verb)
        for head in sorted({head for head, _ in newest}):
            verdicts = [newest.get((head, role)) for role in judges]
            if None in verdicts:
                continue
            try:
                sides = [blind.resolve(p, pull["number"], head, verb) for _, verb in verdicts]
            except blind.BlindError:
                continue
            won = sum(1 for side in sides if side == "new") * 2 > len(judges)
            found.append((max(_order(comment) for comment, _ in verdicts), won))
    return sorted(found)


def _no_convergence(p, state) -> list:
    found = []
    for ticket in _open(state):
        n = ticket["number"]
        entry = production.stage(p, ticket["room"], ticket["stage"])
        judges = production.stage_roles(entry, "judge")
        if (not entry.get("blind") or not judges or tickets.locked_pull(state, n) is not None
                or not tickets.is_locked(state, ticket["room"], ticket["stage"], ticket["unit"])):
            continue
        rounds = _rounds(p, state, ticket, judges)
        k = len(rounds)
        if any(won for _, won in rounds) or k < NO_WIN_ROUNDS:
            continue
        if k >= SHIP_ROUNDS:
            reason = f"{k} blind rounds and no win on #{n}; the locked version ships"
            found.append({"monitor": "no-convergence", "ticket": n, "step": "close", "reason": reason,
                          "line": f"monitor no-convergence: {reason}",
                          "title": f"[MONITOR] no-convergence ship #{n}",
                          "body": _body(["Monitor: no convergence", f"Ticket: #{n}",
                                         "Action: ship the best so far (system brief section 11). "
                                         "The tick closed the ticket; the locked version stays.",
                                         "To undo: reopen the ticket, then close this issue."])})
            continue
        if _ruled_after(p, state, ticket, tickets.pulls_of(state, n), rounds[NO_WIN_ROUNDS - 1][0]):
            continue
        reason = f"{k} blind rounds and no win on #{n}; re-break to the outline"
        found.append({"monitor": "no-convergence", "ticket": n, "step": "wake", "reason": reason,
                      "line": f"monitor no-convergence: {reason}",
                      "title": f"[MONITOR] no-convergence re-break #{n}",
                      "body": _body(["Monitor: no convergence", f"Ticket: #{n}",
                                     "Action: one re-break to the outline, then ship the best so far "
                                     "(system brief section 11).",
                                     "To clear: re-break the outline, post a RULING on the ticket, then close "
                                     f"this issue. After {SHIP_ROUNDS} rounds with no win the tick closes the "
                                     "ticket."])})
    return found


# -- charter drift

def _words(text) -> set:
    return {w for w in re.findall(r"\w+", text.lower()) if len(w) >= 4 and not w.isdigit()}


def _closeness(summary, charter) -> Fraction:
    words = _words(summary)
    if not words:
        return Fraction(0)
    return Fraction(len(words & charter), len(words))


def _summary_stage(p):
    """The first ``story`` stage whose unit is ``episode``, or None."""
    return next((entry for entry in production.stages(p, "story") if entry.get("unit") == "episode"), None)


def _charter_drift(p, state, read) -> list:
    entry = _summary_stage(p)
    if entry is None or not entry.get("paths"):
        return []
    stage, glob = entry["stage"], entry["paths"][0]
    locked = {}  # episode -> merged_at
    for n in sorted(state.tickets):
        ticket = state.tickets[n]
        if ticket["room"] != "story" or ticket["stage"] != stage:
            continue
        pull = tickets.locked_pull(state, n)
        if pull is None or not pull.get("merged_at"):
            continue
        episode = tickets.episode(ticket["unit"])
        if episode not in locked or pull["merged_at"] < locked[episode]:
            locked[episode] = pull["merged_at"]
    measured = []
    for merged_at, episode in sorted((at, ep) for ep, at in locked.items()):
        summary = read(glob.replace("*", episode, 1))
        if summary is not None:
            measured.append((merged_at, episode, summary))
    measured = measured[-DRIFT_EPISODES:]
    if len(measured) < DRIFT_EPISODES:
        return []
    charter = read(CHARTER)
    charter = _words(charter) if charter is not None else set()
    values = [_closeness(summary, charter) for _, _, summary in measured]
    if not all(a > b for a, b in zip(values, values[1:])):
        return []
    after = measured[-1][0]
    for pull in state.pulls.values():
        if pull.get("merged_at") and pull["merged_at"] > after and pull["ticket"] in state.tickets:
            ticket = state.tickets[pull["ticket"]]
            amended = production.stage(p, ticket["room"], ticket["stage"])
            if amended is not None and production.matches(CHARTER, amended.get("paths") or []):
                return []
    for ticket in _open(state):
        if ticket["room"] == "story" and ticket["stage"] == stage and \
                _ruled_after(p, state, ticket, tickets.pulls_of(state, ticket["number"]), (after, 0)):
            return []
    episodes = ", ".join(episode for _, episode, _ in measured)
    closeness = ", ".join(f"{float(c):.2f}" for c in values)
    reason = f"{episodes} move away from the charter (closeness {closeness})"
    role = production.orchestrator(p, "story")
    return [{"monitor": "charter-drift", "stage": stage, "reason": reason,
             "line": f"monitor charter-drift: {reason}",
             "title": f"[MONITOR] charter-drift {measured[-1][1]}",
             "body": _body(["Monitor: charter drift", f"Episodes: {episodes}", f"Closeness: {closeness}",
                            f"Action: force a charter review; no {stage} ticket locks until it "
                            f"(system brief section 11). Routed to {role}.",
                            f"To clear: lock a change to {CHARTER}, or post a RULING on an open {stage} "
                            "ticket, then close this issue."])}]


def trips(p, state, rows, now, read=None) -> list:
    """Every trip, and an error item for a monitor that could not be read. ``rows`` None: no budget monitor.
    ``read(path)`` gives the text of a path on the base or None; ``read`` None: no charter-drift monitor."""
    found = []
    monitors = [("canon", lambda: _canon(p, state)), ("no-convergence", lambda: _no_convergence(p, state))]
    if rows is not None:
        monitors.insert(0, ("budget", lambda: _budget(p, state, rows, now)))
    if read is not None:
        monitors.append(("charter-drift", lambda: _charter_drift(p, state, read)))
    for name, run in monitors:
        try:
            found += run()
        except MONITOR_ERRORS as error:
            found.append({"monitor": name, "error": error})
    return found


def _budget_instead(p, trip, ticket, entry):
    if trip["episode"] is not None and tickets.episode(ticket["unit"]) != trip["episode"]:
        return entry
    if entry["step"] not in STOPPED:
        return entry
    if entry["step"] == "launch" and entry["role"] == production.orchestrator(p, ticket["room"]):
        return entry
    return {**entry, "state": "budget", "step": "none", "reason": trip["reason"]}


def _canon_instead(trip, ticket, entry):
    if trip["ticket"] != ticket["number"] or entry["pull"] != trip["pull"] or entry["state"] != "notes":
        return entry
    return {**entry, "state": "canon", "step": "wake", "role": None, "reason": trip["reason"]}


def _no_convergence_instead(trip, ticket, entry):
    if trip["ticket"] != ticket["number"]:
        return entry
    return {"state": "no-convergence", "step": trip["step"], "role": None, "pull": None, "reason": trip["reason"]}


def _charter_drift_instead(trip, ticket, entry):
    if ticket["room"] != "story" or ticket["stage"] != trip["stage"] or entry["step"] != "lock":
        return entry
    return {"state": "charter-drift", "step": "wake", "role": None, "pull": None, "reason": trip["reason"]}


def instead(p, trips, ticket, entry) -> dict:
    """The entry the first trip that changes it gives (no convergence, then canon, then charter drift, then
    budget), else ``entry``. Only an open ticket is changed."""
    if ticket["state"] != "open":
        return entry
    found = [t for t in trips if "error" not in t]
    for trip in [t for t in found if t["monitor"] == "no-convergence"]:
        new = _no_convergence_instead(trip, ticket, entry)
        if new != entry:
            return new
    for trip in [t for t in found if t["monitor"] == "canon"]:
        new = _canon_instead(trip, ticket, entry)
        if new != entry:
            return new
    for trip in [t for t in found if t["monitor"] == "charter-drift"]:
        new = _charter_drift_instead(trip, ticket, entry)
        if new != entry:
            return new
    for trip in [t for t in found if t["monitor"] == "budget"]:
        new = _budget_instead(p, trip, ticket, entry)
        if new != entry:
            return new
    return entry
