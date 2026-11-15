"""The files of the job of a checker's pull request, placed in ``review/`` of its worktree before it starts.

A checker's stage has a job when ``<unit dir>/manifest.json`` is at the pull's head; its ``outputs`` are
``{name, sha256, bytes}``. An output committed in the unit directory is already in the worktree and is only
named. Every other output is copied from ``<STATE_DIR>/jobs/<job>/out/<name>`` when that file holds its bytes,
or fetched from the asset store. The files go to a staged directory under ``<STATE_DIR>/runs`` that the
launcher renames to ``<worktree>/review`` once the worktree exists. A store that cannot be reached is an
outage: ``[DOWN] store.assets`` is found or filed.
"""

import json
import os
import re
import shutil
from pathlib import Path

from kit.lib import assets, citedmedia, cmd_run, down, gitstate, media, production, resources
from kit.lib.gitstate import GitError

RESOURCE = cmd_run.STORE
_SHA256 = re.compile(r"[0-9a-f]{64}")
# The store answered: no login renewed or proxy restarted clears these, so no outage is filed.
ANSWERED = (assets.NO_SUCH_OBJECT, assets.OTHER_BYTES_SERVED, assets.PATH_UNWRITABLE)


class ReviewError(Exception):
    """A launch that cannot place its review files. The message never holds a setting value."""


def _output(item):
    return (isinstance(item, dict) and isinstance(item.get("name"), str)
            and cmd_run.OUTPUT_NAME.fullmatch(item["name"]) is not None
            and isinstance(item.get("sha256"), str) and _SHA256.fullmatch(item["sha256"]) is not None
            and type(item.get("bytes")) is int and item["bytes"] >= 1)


def manifest(text, path):
    """``(job, outputs)`` of the manifest's text, the outputs sorted by name; ``ReviewError`` otherwise."""
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    outputs = data.get("outputs") if isinstance(data, dict) else None
    if (not isinstance(data, dict) or not isinstance(data.get("job"), str)
            or _SHA256.fullmatch(data["job"]) is None or not isinstance(outputs, list)
            or not all(_output(item) for item in outputs)
            or len({item["name"] for item in outputs}) != len(outputs)):
        raise ReviewError(f"review files: {path} is not {{job, outputs}} with outputs of {{name, sha256, bytes}}")
    return data["job"], sorted(outputs, key=lambda item: item["name"])


def _job_file(path, sha256, size):
    try:
        return path if media.digest(path) == (sha256, size) else None
    except (media.Refused, OSError):
        return None


def _store(p, issues):
    """``(settings, key file)`` of the asset store, checked before any request."""
    found = down._matching(issues, RESOURCE)
    if found:
        raise ReviewError(f"resource {RESOURCE} is down (#{found[0]})")
    try:
        register, values = cmd_run._register(p.root)
        resource = next(r for r in register["resources"] if r["id"] == RESOURCE)
        cmd_run._usable(resource, values, register, p.root)
    except cmd_run.Refused as error:
        raise ReviewError(f"review files: {error}") from None
    secrets_dir = resources.expand(values[register["secrets_dir_setting"]])
    return values, os.path.abspath(os.path.join(secrets_dir, RESOURCE))


def block(number, path, fetched, committed):
    """The task block: the outputs in ``review/`` and the paths in git, each list sorted by name."""
    lines = [f"Files of the job of pull request #{number}, from {path}, in review/ of the worktree:"]
    lines += [f"- review/{item['name']}: {item['bytes']} bytes" for item in fetched] or ["- none"]
    if committed:
        lines.append("In git, not fetched:")
        lines += [f"- {name}" for name in committed]
    return "\n".join(lines)


def place(p, gh, root, role, ticket, pull, issues, staged):
    """Write the outputs of the job of ``pull``'s stage into ``staged`` (made only when there is one to place)
    and return the task block; None when the stage has no manifest at the head. ``ReviewError`` otherwise."""
    staged, number, head = Path(staged), pull["number"], pull["git"]["head"]
    unit_dir = cmd_run._unit_dir(p, ticket["room"], ticket["stage"], ticket["unit"])
    if unit_dir is None:
        return None
    path = f"{unit_dir}/manifest.json"
    try:
        text = gitstate.show(root, head, path)
        if text is None:
            return None
        in_git = set(gitstate.files(root, head, unit_dir))
    except GitError as error:
        raise ReviewError(f"review files: {error}") from None
    job, outputs = manifest(text, path)
    fetched = [item for item in outputs if f"{unit_dir}/{item['name']}" not in in_git]
    committed = [f"{unit_dir}/{item['name']}" for item in outputs if f"{unit_dir}/{item['name']}" in in_git]
    out = production.state_dir(p) / "jobs" / job / "out"
    sources = {item["name"]: _job_file(out / item["name"], item["sha256"], item["bytes"]) for item in fetched}
    values = key_file = None
    if any(source is None for source in sources.values()):
        values, key_file = _store(p, issues)
    shutil.rmtree(staged, ignore_errors=True)
    if fetched:
        name = None
        try:
            citedmedia._mkdirs(staged.parent)
            staged.mkdir(mode=0o700)
            os.chmod(staged, 0o700)
            for item in fetched:
                name = item["name"]
                if sources[name] is not None:
                    citedmedia._copy(sources[name], staged / name)
                else:
                    assets.get(values, key_file, item["sha256"], str(staged / name))
        except media.Refused as error:
            raise ReviewError(f"review files: {error}") from None
        except media.Failed as error:
            if str(error) in ANSWERED:
                raise ReviewError(f"review files: {name}: {error}") from None
            issue = down.report(p, gh, RESOURCE, f"a review file could not be fetched: {error}",
                                f"launch {role} on #{number}")
            raise ReviewError(f"resource {RESOURCE} is down (#{issue})") from None
        except OSError as error:
            raise ReviewError(f"review files: {error.strerror}") from None
    return block(number, path, fetched, committed)
