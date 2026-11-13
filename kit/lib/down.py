"""A resource that is down: one open issue ``[DOWN] <resource id>`` with the label ``resource:down``.

``report`` finds or files that issue and records a stopped account in ``stopped.json`` in the state
directory, ``{"<account>": <issue number>}``, under an exclusive lock on ``down.lock`` there. An account
is stopped while its entry names an open issue that carries the label. ``blocking`` says whether a launch
of a role must wait. ``waiting`` says whether a launch must wait because no account of its slot has usage
left, and files nothing. A person closes the issue once the login is renewed or the proxy restarted; no
program closes, edits or comments on it, and a closed issue is never reused.

Nothing written here holds an account name, a setting value, a token or a path: the issue names the
resource, a fixed cause phrase and the caller's own words.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from datetime import datetime

from kit.lib import harness, production
from kit.lib.harness import HarnessError
from kit.lib.production import ProductionError

LABEL = "resource:down"
PRIMARY = "inference.primary"
OTHER = "inference.other"
STOPPED = "stopped.json"
ACCOUNTS = "accounts.json"
LOCK = "down.lock"


def title(resource) -> str:
    return f"[DOWN] {resource}"


def body(resource, cause, seen_by, stopped) -> str:
    lines = [f"Resource: {resource}", f"Cause: {cause}", f"Seen by: {seen_by}", "",
             "Launches that need this resource skip a stopped login and wait when none is left.",
             "To clear: renew the login or restart the proxy, then close this issue."]
    if stopped:
        lines.append("The stopped account is named in stopped.json in the state directory of the machine "
                     "that ran the launch.")
    return "\n".join(lines) + "\n"


def _labelled(issues) -> set:
    return {i["number"] for i in issues if LABEL in i["labels"]}


def _matching(issues, resource) -> list:
    name = title(resource)
    return sorted(i["number"] for i in issues if LABEL in i["labels"] and i["title"] == name)


def _read(state_dir) -> dict:
    """``stopped.json`` as written; empty when there is none. Never writes, never locks."""
    try:
        data = json.loads((state_dir / STOPPED).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise HarnessError(f"{STOPPED} is not a JSON object")
    return data


def exhausted(state_dir) -> dict:
    """``{account: time it is usable again}`` from ``accounts.json``; empty when there is none."""
    try:
        data = json.loads((state_dir / ACCOUNTS).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise HarnessError(f"{ACCOUNTS} is not a JSON object")
    return data


def _write(state_dir, data) -> None:
    fd, tmp = tempfile.mkstemp(dir=state_dir, prefix=".stopped-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, indent=1, sort_keys=True) + "\n")
    os.replace(tmp, state_dir / STOPPED)


def _stopped_in(state_dir, issues) -> dict:
    live = _labelled(issues)
    return {account: number for account, number in _read(state_dir).items() if number in live}


def stopped(p, gh) -> dict:
    """``{account: issue number}`` of the accounts stopped now. GitHub is read only when ``stopped.json``
    names an account."""
    state_dir = production.state_dir(p)
    if not _read(state_dir):
        return {}
    return _stopped_in(state_dir, gh.issues("open"))


def report(p, gh, resource, cause, seen_by, account=None) -> int:
    """Find or file the open issue of ``resource``, stop ``account`` under it when given; its number."""
    state_dir = production.state_dir(p)
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(state_dir / LOCK, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        issues = gh.issues("open")
        found = _matching(issues, resource)
        if found:
            number = found[0]
        else:
            number = gh.create_issue(title(resource), body(resource, cause, seen_by, account is not None), [LABEL])
        live = _labelled(issues) | {number}
        old = _read(state_dir)
        data = {name: n for name, n in old.items() if n in live}
        if account is not None:
            data[account] = number
        if data != old:
            _write(state_dir, data)
    return number


def blocking(p, issues, role):
    """``(resource id, issue number)`` when a launch of ``role`` must wait, given the open ``issues``;
    else None. A setting that cannot be read is not a block."""
    try:
        meta = p.roles[role]["meta"]
        if harness.model(p.settings, meta)[1] == "other":
            found = _matching(issues, OTHER)
            return (OTHER, found[0]) if found else None
        names = harness.accounts(p.settings, meta["model"].upper())
        held = _stopped_in(production.state_dir(p), issues)
    except (HarnessError, ProductionError, OSError, KeyError):
        return None
    if all(name in held for name in names):
        return PRIMARY, min(held[name] for name in names)
    return None


def waiting(p, issues, role, now):
    """``(slot, time it is usable again)`` when no account of the slot of ``role`` has usage left at
    ``now``, given the open ``issues``; else None. The time is the earliest written for an account not
    stopped, as written. A setting or file that cannot be read is not a wait."""
    try:
        meta = p.roles[role]["meta"]
        if harness.model(p.settings, meta)[1] == "other":
            return None
        slot = meta["model"].upper()
        state_dir = production.state_dir(p)
        held = _stopped_in(state_dir, issues)
        left = [name for name in harness.accounts(p.settings, slot) if name not in held]
        if not left:
            return None
        times = exhausted(state_dir)
        if harness.pick(left, times, now) is not None:
            return None
        return slot, min((times[name] for name in left),
                         key=lambda value: datetime.strptime(value, production.TIME_FORMAT))
    except (HarnessError, ProductionError, OSError, KeyError, TypeError, ValueError):
        return None
