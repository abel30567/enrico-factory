"""The chasing-metrics monitor of the producer gate (system brief section 11).

Over the last ``EPISODES`` episodes with engagement on one platform, in the order their summary stage locked,
it trips when the end retention rises strictly while the premise distinctiveness falls strictly. Platforms are
never merged. For one round the trip holds the next lock of that stage until the story orchestrator rules: a
``RULING`` newer than the trip's issue clears it, and the round ends when a pull of that stage merges after the
issue. Engagement is the retention cell of a post's last row in the calibration set; ``-`` leaves the episode
out. A pure reading: it writes nothing.
"""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

from kit.lib import calibration, monitors, production, tickets

NAME = "chasing-metrics"
EPISODES = 3


def _locked(state, stage) -> list:
    """``(merged_at, episode)`` of every episode whose ``stage`` locked, by its first lock time."""
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
    return sorted((at, ep) for ep, at in locked.items())


def _engagement(text) -> dict:
    """The retention cell of each post's last row; raises ProductionError on a table that does not parse."""
    found = {}
    rows = text.splitlines()
    if not rows or rows[0] != "\t".join(calibration.HEADER):
        raise production.ProductionError(f"{calibration.PATH}: {calibration.HEADER_MESSAGE}")
    column = calibration.HEADER.index("retention")
    for k, row in enumerate(rows[1:], start=2):
        if not row.strip():
            continue
        cells = row.split("\t")
        if len(cells) != len(calibration.HEADER) or not calibration.POST.fullmatch(cells[0]):
            raise production.ProductionError(f"{calibration.PATH} line {k} is not a row of the header")
        cell = cells[column]
        if cell != "-" and not calibration.NUMBER.fullmatch(cell):
            raise production.ProductionError(f"{calibration.PATH} line {k}: retention is not a number or -")
        found[cells[0]] = cell
    return found


def _distinctiveness(premises) -> dict:
    """Episode -> the share of its premise's words found in no earlier premise."""
    found, earlier = {}, set()
    for episode, premise in premises:
        words = monitors._words(premise)
        found[episode] = 1 - Fraction(len(words & earlier), len(words)) if words else Fraction(0)
        earlier |= words
    return found


def _issue_at(state, title):
    """``created_at`` of the oldest issue of ``state.others`` with ``title``, open or closed, or None."""
    return min((issue["created_at"] for issue in state.others if issue["title"] == title), default=None)


def _cleared(p, state, stage, at) -> bool:
    for pull in state.pulls.values():
        if pull.get("merged_at") and pull["merged_at"] > at and pull["ticket"] in state.tickets:
            ticket = state.tickets[pull["ticket"]]
            if ticket["room"] == "story" and ticket["stage"] == stage:
                return True
    return any(ticket["room"] == "story" and ticket["stage"] == stage
               and monitors._ruled_after(p, state, ticket, tickets.pulls_of(state, ticket["number"]), (at, 0))
               for ticket in monitors._open(state))


def trips(p, state, rows, now, read) -> list:
    entry = monitors._summary_stage(p)
    if entry is None or not entry.get("paths"):
        return []
    stage, glob = entry["stage"], entry["paths"][0]
    text = read(calibration.PATH)
    if text is None:
        return []
    engagement = _engagement(text)
    premises = []
    for _, episode in _locked(state, stage):
        premise = read(glob.replace("*", episode, 1))
        if premise is not None:
            premises.append((episode, premise))
    distinct = _distinctiveness(premises)
    platforms = sorted({post.split("/", 1)[1] for post in engagement if post.split("/", 1)[0] in distinct})
    role = production.orchestrator(p, "story")
    found = []
    for platform in platforms:
        measured = [ep for ep, _ in premises if engagement.get(f"{ep}/{platform}", "-") != "-"][-EPISODES:]
        if len(measured) < EPISODES:
            continue
        cells = [engagement[f"{ep}/{platform}"] for ep in measured]
        values = [Decimal(cell) for cell in cells]
        shares = [distinct[ep] for ep in measured]
        if not (all(a < b for a, b in zip(values, values[1:])) and all(a > b for a, b in zip(shares, shares[1:]))):
            continue
        title = f"[MONITOR] {NAME} {platform} {measured[-1]}"
        at = _issue_at(state, title)
        if at is not None and _cleared(p, state, stage, at):
            continue
        episodes = ", ".join(measured)
        retention = ", ".join(cells)
        distinctiveness = ", ".join(f"{float(d):.2f}" for d in shares)
        reason = (f"{platform} {episodes}: end retention rises ({retention}) "
                  f"while distinctiveness falls ({distinctiveness})")
        found.append({"stage": stage, "reason": reason, "line": f"monitor {NAME}: {reason}", "title": title,
                      "body": monitors._body([
                          "Monitor: chasing metrics", f"Platform: {platform}", f"Episodes: {episodes}",
                          f"End retention: {retention}", f"Distinctiveness: {distinctiveness}",
                          f"Action: hold the next {stage} lock until {role} rules (system brief section 11).",
                          f"To clear: post a RULING on an open {stage} ticket. The round ends when a {stage} "
                          "pull merges after this issue."])})
    return found


def instead(p, trip, ticket, entry):
    if ticket["room"] != "story" or ticket["stage"] != trip["stage"] or entry["step"] != "lock":
        return entry
    return {"state": NAME, "step": "wake", "reason": trip["reason"]}
