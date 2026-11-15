"""The shot list ``factory tick`` writes at intake (system brief sections 6 and 16).

``production/<ep>/shots.json`` is a list of ``{shot, first, last}``, one row per event of the episode's edit list
``episodes/<ep>/reel/edit-list.json`` on the base, in order, by the formula of the ``intake`` lint
(``kit.lib.lints.intake``), so the rows pass it. ``due`` gives one item per episode of a locked ``story/reel``
ticket whose shot list is not on the base; a shot list on the base is never written again, whatever it holds.
``write`` commits the file on the base as ``factory`` and pushes, as ``kit.lib.registry`` does.
"""

from __future__ import annotations

import json
from pathlib import Path

from kit.lib import gitstate, production, tickets
from kit.lib.lints.intake import _events

EDIT_LIST = "episodes/{}/reel/edit-list.json"


class ShotListError(Exception):
    pass


def rows(text, rel: str) -> tuple[list | None, str]:
    """The shot list of the edit list ``text`` at ``rel`` and ``""``, or None and why it cannot be made."""
    if text is None:
        return None, f"{rel} is not on the base"
    try:
        data = json.loads(text)
    except ValueError:
        return None, f"{rel} is not JSON"
    events, found = _events(rel, data)
    if events is None or found:
        return None, f"{rel}: {found[0].message}"
    return [{"shot": shot, "first": first, "last": last} for shot, first, last in events], ""


def due(p, state, root) -> list:
    """``{"from", "pull", "episode", "path", "rows", "reason"}`` per episode of a locked ``story/reel`` ticket
    whose shot list is not on the base, ``from`` the lowest numbered such ticket, in the order of ``from``.
    Empty while a tracked issue does not parse or the pipeline has no ``production/intake``."""
    if state.errors or production.stage(p, *tickets.INTAKE) is None:
        return []
    base = production.base(p)
    found, seen = [], set()
    for number in sorted(state.tickets):
        ticket = state.tickets[number]
        pull = tickets.locked_pull(state, number)
        if (ticket["room"], ticket["stage"]) != tickets.PACKAGE or pull is None:
            continue
        ep = tickets.episode(ticket["unit"])
        if ep in seen:
            continue
        seen.add(ep)
        path = tickets.SHOTS.format(ep)
        if gitstate.show(root, base, path) is not None:
            continue
        rel = EDIT_LIST.format(ep)
        shots, reason = rows(gitstate.show(root, base, rel), rel)
        found.append({"from": number, "pull": pull["number"], "episode": ep, "path": path, "rows": shots,
                      "reason": reason})
    return found


def write(p, gh, root, item: dict) -> None:
    """Write the item's shot list, commit it on the base as ``factory`` and push."""
    base = production.base(p)
    try:
        branch = gitstate.git(root, "symbolic-ref", "--short", "HEAD").strip()
    except gitstate.GitError:
        branch = None
    if branch != base or gitstate.git(root, "diff", "--cached", "--name-only").strip():
        raise ShotListError(f"the checkout is not on {base} with a clean index")
    rel = item["path"]
    path = Path(root) / rel
    if path.exists():
        raise ShotListError(f"{rel} is in the checkout and not on {base}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(item["rows"], indent=1) + "\n", encoding="utf-8")
    gitstate.commit(root, [rel], f"intake: {rel} from PR #{item['pull']}", "factory")
    gh.push(root, base)
