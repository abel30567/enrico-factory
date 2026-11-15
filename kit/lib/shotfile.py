"""The subjects a shot names (system brief section 16, the shot file ``sequences/<Q>/shots/<shot>.json``).

A shot unit ``<ep>/<shot>`` has its file at ``episodes/<ep>/sequences/<Q>/shots/<shot>.json`` on the base. Its
``characters`` and its ``set`` are ids of canon sheets, and each is the unit of an ``assets`` ticket. The id
pattern is ``filing.ID``: it lists exactly the ids ``filing._assets`` files a ticket for. Props are not named.

Reads git through ``kit.lib.gitstate``; writes nothing.
"""

from __future__ import annotations

import json
import re

from kit.lib import gitstate, production

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")


def path(p, root, unit) -> str | None:
    """The first path, in sorted order, of ``episodes/<ep>/sequences/<Q>/shots/<shot>.json`` on the base for the
    unit ``<ep>/<shot>``; None for any other unit, when there is none, or when the base cannot be read."""
    parts = unit.split("/") if isinstance(unit, str) else []
    if len(parts) != 2 or not all(ID.fullmatch(part) for part in parts):
        return None
    episode, shot = parts
    prefix = f"episodes/{episode}/sequences/"
    try:
        found = gitstate.files(root, production.base(p), prefix)
    except gitstate.GitError:
        return None
    for name in sorted(found):
        rest = name[len(prefix):].split("/") if name.startswith(prefix) else []
        if len(rest) == 3 and rest[0] and rest[1] == "shots" and rest[2] == f"{shot}.json":
            return name
    return None


def subjects(p, root, unit) -> tuple[list, list]:
    """``(characters, sets)`` the unit's shot file names on the base, each sorted and each id once; empty parts
    when the file is missing, is not JSON or has no such values."""
    found = path(p, root, unit)
    try:
        text = gitstate.show(root, production.base(p), found) if found is not None else None
    except gitstate.GitError:
        text = None
    try:
        data = json.loads(text) if text is not None else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return [], []
    names = data.get("characters") if isinstance(data.get("characters"), list) else []
    characters = sorted({name for name in names if isinstance(name, str) and ID.fullmatch(name)})
    value = data.get("set")
    sets = [value] if isinstance(value, str) and ID.fullmatch(value) else []
    return characters, sets
