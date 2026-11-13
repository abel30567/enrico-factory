"""The asset registry and the shots a new asset version makes stale (system brief sections 6, 7 and 16).

``registry/assets.tsv`` is append-only and written only by ``factory lock``, straight on the base branch:
one row per lock of an ``assets`` ticket, with the version (the ``sha256`` of the asset's state on the
base after the merge) and the shots it makes stale. A stale shot gets a new ticket for its earliest
broken stage; a locked ticket is never reopened.
"""

from __future__ import annotations

import json
from pathlib import Path

from kit.lib import filing, gitstate, production, tickets
from kit.lib.lints.assets import ASSET_ID, SHA256

PATH = "registry/assets.tsv"
COLUMNS = ("time", "asset", "version", "ticket", "pull", "stale")
HEADER = "\t".join(COLUMNS)
STAGE = "assets"
STATE = "production/assets/{}/assets/state.json"
SHOT = "shot"


class RegistryError(Exception):
    pass


def _json(root, ref, path):
    text = gitstate.show(root, ref, path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def version(root, base, asset: str) -> str | None:
    """The ``sha256`` of the asset's state on ``base``, None when it is missing or not 64 hex characters."""
    data = _json(root, base, STATE.format(asset))
    value = data.get("sha256") if isinstance(data, dict) else None
    return value if isinstance(value, str) and SHA256.fullmatch(value) else None


def stale(p, root, base, room: str, asset: str, new: str) -> list:
    """``(unit, stage)`` for every shot whose state names ``asset`` at another version than ``new``, at the
    shot's earliest such stage, sorted by unit."""
    order = [entry["stage"] for entry in production.stages(p, room) if entry["unit"] == SHOT]
    found = {}
    for path in gitstate.files(root, base, "production"):
        parts = path.split("/")
        if len(parts) != 5 or parts[1] == "assets" or parts[3] not in order or parts[4] != "state.json":
            continue
        data = _json(root, base, path)
        actors = data.get("actors") if isinstance(data, dict) else None
        if not isinstance(actors, list):
            continue
        if any(isinstance(a, dict) and a.get("asset") == f"asset:{asset}" and a.get("sha256") != new
               for a in actors):
            unit, stage = f"{parts[1]}/{parts[2]}", parts[3]
            if unit not in found or order.index(stage) < order.index(found[unit]):
                found[unit] = stage
    return sorted(found.items())


def _append(p, gh, root, line: str, message: str) -> None:
    base = production.base(p)
    try:
        branch = gitstate.git(root, "symbolic-ref", "--short", "HEAD").strip()
    except gitstate.GitError:
        branch = None
    if branch != base or gitstate.git(root, "diff", "--cached", "--name-only").strip():
        raise RegistryError(f"the checkout is not on {base} with a clean index")
    path = Path(root) / PATH
    committed = gitstate.show(root, "HEAD", PATH)
    current = path.read_text(encoding="utf-8", errors="surrogateescape") if path.is_file() else None
    if current != committed:
        raise RegistryError(f"{PATH} differs from the base")
    content = (current if current is not None else HEADER + "\n") + line + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", errors="surrogateescape")
    gitstate.commit(root, [PATH], message, "factory")
    gh.push(root, base)


def _refile(gh, state, room: str, asset: str, new: str, unit: str, stage: str) -> str:
    """Ruling 6: what happens to the stale ``(unit, stage)``, as the end of its printed line."""
    mine = [state.tickets[n] for n in sorted(state.tickets)
            if state.tickets[n]["room"] == room and state.tickets[n]["stage"] == stage
            and state.tickets[n]["unit"] == unit]
    for ticket in mine:
        if ticket["state"] == "open" and tickets.locked_pull(state, ticket["number"]) is None:
            return f"ticket #{ticket['number']} is open"
    locked = next((t for t in mine if tickets.locked_pull(state, t["number"]) is not None), None)
    if locked is None:
        return "no locked ticket"
    merged = tickets.locked_pull(state, locked["number"])
    body = (f"Do the stage {stage} for unit {unit} again: asset {asset} has the version {new[:12]}.\n\n"
            f"Stale: #{locked['number']} {stage} {unit}, locked by PR #{merged['number']}.\n\n"
            "Filed by factory lock.\n")
    number = gh.create_issue(f"[{filing.key(stage, unit)}] {stage} for {unit}", body,
                             [tickets.TRACKED, f"room:{room}", f"stage:{stage}", f"unit:{unit}"])
    return f"filed #{number}"


def record(p, gh, root, state, ticket, pull) -> list:
    """After the lock of ``pull`` for ``ticket``: append the asset's row, file the stale shots' tickets and
    return the lines to print. ``[]`` and nothing written unless the ticket's stage is ``assets``."""
    if ticket["stage"] != STAGE:
        return []
    number, asset, room = pull["number"], ticket["unit"], ticket["room"]
    if not ASSET_ID.fullmatch(asset):
        return [f"registry: no row for PR #{number}: unit {asset} is not an asset id"]
    base = production.base(p)
    new = version(root, base, asset)
    if new is None:
        return [f"registry: no row for PR #{number}: {STATE.format(asset)} has no sha256"]
    shots = stale(p, root, base, room, asset, new)
    pairs = ",".join(f"{unit}:{stage}" for unit, stage in shots) or "none"
    row = (production.now(), asset, new, str(ticket["number"]), str(number), pairs)
    _append(p, gh, root, "\t".join(row), f"registry: asset {asset} {new[:12]} PR #{number}")
    lines = [f"registry: asset {asset} version {new[:12]}, {len(shots)} stale shot(s)"]
    for unit, stage in shots:
        lines.append(f"stale: {unit} at {stage}: {_refile(gh, state, room, asset, new, unit, stage)}")
    return lines
