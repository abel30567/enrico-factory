"""A blinded old-against-new pair for a judge, and its key (system brief sections 6, 8, 9 and 15).

Everything lives under ``<state_dir>/blind/``, which no worker can read:
- ``salt``: 32 random bytes, mode 0600, created on first use;
- ``<pr>-<id>/key.json``: which of ``A`` and ``B`` is the old version and which the new one;
- ``<pr>-<id>/pair/A/``, ``pair/B/``: the stage's files of each side, every modification time 0.

The id is derived from the salt, the pull number and the head, so the directory names no sha and no
branch. The judge is given ``pair/`` alone. Nothing here talks to GitHub; git is read through
``kit.lib.gitstate``.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import tempfile
from pathlib import Path

from kit.lib import gitstate, production, tickets

PREFERENCES = {"PREFERS A": "A", "PREFERS B": "B", "NO PREFERENCE": None}


class BlindError(Exception):
    pass


def _dir(p) -> Path:
    return production.state_dir(p) / "blind"


def _salt(p, create: bool) -> bytes | None:
    path = _dir(p) / "salt"
    if path.is_file():
        return path.read_bytes()
    if not create:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_bytes()
    with os.fdopen(fd, "wb") as f:
        f.write(secrets.token_bytes(32))
    return path.read_bytes()


def _blinding(salt: bytes, pull_number: int, head: str) -> tuple[str, str]:
    """The id and the side ``A`` shows."""
    digest = hashlib.sha256(salt + b"\n" + str(pull_number).encode() + b"\n" + head.encode())
    return digest.hexdigest()[:12], "new" if digest.digest()[0] % 2 == 0 else "old"


def _home(p, pull_number: int, salt: bytes, head: str) -> Path:
    return _dir(p) / f"{pull_number}-{_blinding(salt, pull_number, head)[0]}"


def key_path(p, pull_number: int, head: str) -> Path:
    """``<state_dir>/blind/<pr>-<id>/key.json``; creates the salt when there is none yet."""
    return _home(p, pull_number, _salt(p, create=True), head) / "key.json"


def has_key(p, pull_number: int, head: str) -> bool:
    """A complete pair exists for this pull and head. Never creates the salt."""
    salt = _salt(p, create=False)
    return salt is not None and (_home(p, pull_number, salt, head) / "key.json").is_file()


def pair_dir(p, pull_number: int, head: str) -> Path | None:
    """The directory holding ``A/`` and ``B/``, the judge's working directory; None when there is no key."""
    if not has_key(p, pull_number, head):
        return None
    return key_path(p, pull_number, head).parent / "pair"


def old_exists(p, state, pull) -> bool:
    """The pull's stage is ``blind`` and its unit is locked."""
    ticket = state.tickets[pull["ticket"]]
    entry = production.stage(p, ticket["room"], ticket["stage"])
    return bool(entry["blind"]) and tickets.is_locked(state, ticket["room"], ticket["stage"], ticket["unit"])


def _reduce(tree: Path, paths) -> None:
    """Keep only the files whose tree-relative path matches ``paths``; drop the directories left empty."""
    for top, dirs, files in os.walk(tree, topdown=False):
        here = Path(top)
        for name in files + [d for d in dirs if (here / d).is_symlink()]:
            rel = (here / name).relative_to(tree).as_posix()
            if rel == gitstate.MANIFEST or not production.matches(rel, paths):
                os.remove(here / name)
        if here != tree and not any(here.iterdir()):
            here.rmdir()


def _has_file(tree: Path) -> bool:
    return any(not path.is_dir() or path.is_symlink() for path in tree.rglob("*"))


def _zero_times(tree: Path) -> None:
    for path in [tree, *tree.rglob("*")]:
        os.utime(path, (0, 0), follow_symlinks=False)


def _write_key(path: Path, key: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".key-", suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(key, indent=1, sort_keys=True) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def prepare(p, state, root, pull) -> dict:
    """The key of the pull's blinded pair at its head, writing the pair the first time."""
    number = pull["number"]
    if pull["state"] != "open":
        raise BlindError(f"pull request #{number} is not open")
    if not old_exists(p, state, pull):
        raise BlindError("no locked version to compare")
    ticket = state.tickets[pull["ticket"]]
    entry = production.stage(p, ticket["room"], ticket["stage"])
    head = pull["git"]["head"]
    path = key_path(p, number, head)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))

    salt = _salt(p, create=False)
    blinding_id, a_side = _blinding(salt, number, head)
    sides = {"A": a_side, "B": "new" if a_side == "old" else "old"}
    base = gitstate.merge_base(root, pull["base_ref"], pull["head_ref"])
    refs = {"old": base, "new": head}
    work = Path(tempfile.mkdtemp(prefix=".prepare-", dir=_dir(p)))
    try:
        for letter in ("A", "B"):
            tree = work / "pair" / letter
            gitstate.export(root, refs[sides[letter]], tree)
            _reduce(tree, entry["paths"])
        old_letter = "A" if sides["A"] == "old" else "B"
        if not _has_file(work / "pair" / old_letter):
            raise BlindError("no locked version to compare")
        _zero_times(work / "pair")
        home = path.parent
        if home.exists():  # an earlier run stopped before its key: not a pair
            shutil.rmtree(home)
        os.rename(work, home)
    finally:
        if work.exists():
            shutil.rmtree(work)
    key = {"id": blinding_id, "pull": number, "head": head, "base": base, **sides,
           "paths": list(entry["paths"]), "created_at": production.now()}
    _write_key(path, key)
    return key


def resolve(p, pull_number: int, head: str, marker_verb: str) -> str:
    """``new``, ``old`` or ``none``: what a judge's ``PREFERS A`` / ``PREFERS B`` / ``NO PREFERENCE`` means."""
    if marker_verb not in PREFERENCES:
        raise BlindError(f"{marker_verb!r} is not a preference")
    if not has_key(p, pull_number, head):
        raise BlindError(f"no blinding key for pull request #{pull_number} at this head")
    letter = PREFERENCES[marker_verb]
    if letter is None:
        return "none"
    key = json.loads(key_path(p, pull_number, head).read_text(encoding="utf-8"))
    return key[letter]
