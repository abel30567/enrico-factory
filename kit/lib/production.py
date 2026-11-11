"""What every command needs to know about the production repository it runs in (brief sections 15, 16).

A `ProductionError` message never contains a setting's value: only key names, file names relative to
the root and fixed words.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from pathlib import Path

from kit.lib import resources, roles
from kit.lib.lints.kit import kit_dir

CAPS = ("CAP_EPISODE_USD", "CAP_SEASON_USD", "CAP_EPISODE_COMPUTE_MIN", "CAP_SEASON_COMPUTE_MIN",
        "CAP_TICKET_ITERATIONS", "CAP_EPISODE_HOURS", "CAP_SEASON_HOURS")
STAGE_ROLE_KEYS = ("maker", "checker", "judge")
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

_NUMBER = re.compile(r"\d+(\.\d*)?|\.\d+")
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


class ProductionError(Exception):
    pass


@dataclass(frozen=True)
class Production:
    root: Path
    kit: Path
    settings: dict[str, str]
    roles: dict[str, dict]
    formats: dict[str, dict]
    pipeline: list[dict]
    kinds: dict[str, str]
    actors: dict[str, str]


def find_root(start) -> Path:
    """The nearest directory at or above `start` that holds a file `production.env`."""
    here = Path(start).resolve()
    for directory in (here, *here.parents):
        if (directory / "production.env").is_file():
            return directory
    raise ProductionError("no production.env at or above the start directory")


def load(root) -> Production:
    root = Path(root).resolve()
    kit = kit_dir(root)
    if kit is None:
        raise ProductionError("no kit in the tree: neither .factory/ nor kit/pipeline.json")
    problems = roles.validate(kit)
    if problems:
        raise ProductionError(f"kit: {len(problems)} problem(s); first: {problems[0]}")
    role_docs = roles.load_roles(kit / "roles")
    return Production(
        root=root,
        kit=kit,
        settings=resources.settings(root),
        roles=role_docs,
        formats=roles.load_formats(kit / "formats"),
        pipeline=roles.load_pipeline(kit / "pipeline.json"),
        kinds={rid: doc["meta"]["kind"] for rid, doc in role_docs.items()},
        actors={rid: roles.actor({**doc["meta"], "role": rid}) for rid, doc in role_docs.items()},
    )


def setting(p: Production, name: str, default: str | None = None) -> str:
    value = p.settings.get(name, "")
    if value:
        return value
    if default is None:
        raise ProductionError(f"setting {name} is empty")
    return default


def base(p: Production) -> str:
    return setting(p, "BASE", "main")


def rooms(p: Production) -> list[str]:
    """Setting `ROOMS` as a sorted list; empty means every room that has a pipeline entry."""
    named = [r.strip() for r in setting(p, "ROOMS", "").split(",") if r.strip()]
    return sorted(named or {entry["room"] for entry in p.pipeline})


def state_dir(p: Production) -> Path:
    """Setting `STATE_DIR`, `~/` expanded: an absolute path outside the repository. Not created."""
    value = setting(p, "STATE_DIR")
    path = Path(os.path.expanduser(value) if value.startswith("~/") else value)
    if not path.is_absolute():
        raise ProductionError("setting STATE_DIR must be an absolute path outside the repository")
    resolved = path.resolve()
    if resolved == p.root or p.root in resolved.parents:
        raise ProductionError("setting STATE_DIR must be an absolute path outside the repository")
    return path


def stages(p: Production, room: str) -> list[dict]:
    return [entry for entry in p.pipeline if entry["room"] == room]


def stage(p: Production, room: str, name: str) -> dict | None:
    return next((entry for entry in stages(p, room) if entry["stage"] == name), None)


def stage_roles(entry: dict, key: str) -> list[str]:
    """`entry[key]` (`maker`, `checker` or `judge`) as a list of role ids."""
    value = entry.get(key)
    if not value:
        return []
    return [value] if isinstance(value, str) else list(value)


def orchestrator(p: Production, room: str) -> str:
    found = sorted(rid for rid, doc in p.roles.items()
                   if doc["meta"]["kind"] == "orchestrator" and doc["meta"]["room"] == room)
    if len(found) != 1:
        raise ProductionError(f"room {room} has {len(found)} orchestrator role(s), not 1")
    return found[0]


def caps(p: Production) -> dict[str, float | None]:
    """One entry per name in `CAPS`; None when the setting is empty or absent (no cap set)."""
    result = {}
    for name in CAPS:
        value = p.settings.get(name, "")
        if not value:
            result[name] = None
        elif _NUMBER.fullmatch(value):
            result[name] = float(value)
        else:
            raise ProductionError(f"setting {name} is not a number >= 0")
    return result


def rules_text(p: Production) -> str:
    """The rules of power, then a blank line, then the production rules when the file exists."""
    power = p.kit / "rules-of-power.md"
    if not power.is_file():
        raise ProductionError(f"{power.relative_to(p.root).as_posix()} is missing")
    text = power.read_text(encoding="utf-8")
    production = p.root / "rules" / "production-rules.md"
    if production.is_file():
        text = text.rstrip("\n") + "\n\n" + production.read_text(encoding="utf-8")
    return text


def _strip(path: str) -> str:
    while path.startswith("./"):
        path = path[2:]
    return path


def _match(glob: list[str], path: list[str]) -> bool:
    if not glob:
        return not path
    if glob[0] == "**":
        if len(glob) == 1:
            return bool(path)  # a trailing `**` is what is inside the directory, not the directory
        return any(_match(glob[1:], path[i:]) for i in range(len(path) + 1))
    return bool(path) and fnmatchcase(path[0], glob[0]) and _match(glob[1:], path[1:])


def matches(path: str, globs) -> bool:
    """True when any glob matches the whole root-relative posix `path`.

    `**` matches zero or more whole segments (one or more when it is the last segment); any other
    segment matches one path segment with `fnmatchcase`, so nothing crosses a `/`.
    """
    parts = _strip(path).split("/")
    return any(_match(_strip(glob).split("/"), parts) for glob in globs if _strip(glob))


def now(env=None) -> str:
    """UTC now as `YYYY-MM-DDTHH:MM:SSZ`, or `FACTORY_NOW` when set (empty counts as unset)."""
    env = os.environ if env is None else env
    value = env.get("FACTORY_NOW", "")
    if not value:
        return datetime.now(timezone.utc).strftime(TIME_FORMAT)
    try:
        if not _TIME.fullmatch(value):
            raise ValueError
        datetime.strptime(value, TIME_FORMAT)
    except ValueError:
        raise ProductionError("FACTORY_NOW is not a UTC time YYYY-MM-DDTHH:MM:SSZ") from None
    return value
