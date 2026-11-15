"""The pictures a checker's pull request cites, placed where the checker can open them before it starts.

A role asks with the front-matter key ``media: cited``. The ids are every ``media:<id>`` in the text, at the
pull's head, of the files the pull changes; each one's row is the first of ``media/manifest.tsv`` at the head.
A job's picture is copied from ``<STATE_DIR>/jobs/<digest>/out/<name>`` when it holds the row's bytes; any
other comes from the media store's public link. The files go to a staged directory under ``<STATE_DIR>/runs``
that the launcher renames to ``<run dir>/media`` once the run directory exists. A store that does not serve
a picture is an outage: the staged directory is removed and ``[DOWN] store.media`` is found or filed.
"""

import os
import re
import shutil
from pathlib import Path

from kit.lib import down, gitstate, media, production
from kit.lib.gitstate import GitError

# The alphabet of an id (``cmd_media.ID``) with no trailing ``.``, ``-`` or ``_``.
CITED = re.compile(r"media:([a-z0-9](?:[a-z0-9._-]*[a-z0-9])?)")
KEY = "media"
CHOICE = "cited"
RESOURCE = "store.media"
# The caps, each a setting and its default: pictures, bytes of one picture, bytes of all.
COUNT, BYTES, TOTAL = "MEDIA_FETCH_COUNT", "MEDIA_FETCH_BYTES", "MEDIA_FETCH_TOTAL"
DEFAULTS = {COUNT: "20", BYTES: "5000000", TOTAL: "40000000"}
_SIZE = re.compile(r"[1-9][0-9]*")
_DIGEST = re.compile(r"[0-9a-f]{64}")

NO_ROW = "no row in media/manifest.tsv"
NO_SIZE = "its manifest row gives no size"
NOT_A_PICTURE = "not a PNG, JPEG, GIF or WebP picture"


class CitedError(Exception):
    """A launch that cannot place its pictures. The message never holds a setting value."""


def wanted(meta):
    """Whether a role's front matter asks for the pictures its pull request cites."""
    return meta.get(KEY) == CHOICE


def _cap(p, name):
    value = production.setting(p, name, DEFAULTS[name])
    if not (value.isascii() and value.isdigit() and value[0] != "0"):  # [1-9][0-9]*
        raise CitedError(f"setting {name} is not a whole number >= 1")
    return int(value)


def cited(root, pull):
    """The ids cited by the text at the pull's head of the paths it changes, sorted, each once."""
    head, found = pull["git"]["head"], set()
    for path in pull["git"]["changed"]:
        text = gitstate.show(root, head, path)
        if text is not None:
            found.update(CITED.findall(text))
    return sorted(found)


def rows(root, head):
    """``{id: (sha256, bytes, source)}`` of the manifest at ``head``: rows of four fields, the first row of an
    id wins."""
    found = {}
    for line in (gitstate.show(root, head, gitstate.MANIFEST) or "").splitlines()[1:]:
        fields = line.split("\t")
        if len(fields) == 4 and fields[0] not in found:
            found[fields[0]] = tuple(fields[1:])
    return found


def plan(p, ids, found):
    """``(wanted, skipped)``: ``[(id, sha256, bytes, source)]`` to place and ``{id: reason}`` not to, the caps
    applied to the ids in order."""
    count, size_cap, total_cap = _cap(p, COUNT), _cap(p, BYTES), _cap(p, TOTAL)
    chosen, skipped, total = [], {}, 0
    for name in ids:
        if name not in found:
            skipped[name] = NO_ROW
            continue
        sha256, size, source = found[name]
        if not _SIZE.fullmatch(size):
            skipped[name] = NO_SIZE
        elif int(size) > size_cap:
            skipped[name] = f"{size} bytes, over {BYTES} {size_cap}"
        elif len(chosen) >= count:
            skipped[name] = f"over {COUNT} {count}"
        elif total + int(size) > total_cap:
            skipped[name] = f"over {TOTAL} {total_cap}"
        else:
            chosen.append((name, sha256, int(size), source))
            total += int(size)
    return chosen, skipped


def job_file(p, name, sha256, size, source):
    """The job output that holds the row's bytes, or None."""
    digest = source[len("job "):] if source.startswith("job ") else ""
    if not _DIGEST.fullmatch(digest) or not name.startswith(digest[:16] + "-"):
        return None
    path = production.state_dir(p) / "jobs" / digest / "out" / name[17:]
    try:
        return path if media.digest(path) == (sha256, size) else None
    except media.Refused:
        return None


def _file_name(name, kind):
    extension = name.rsplit(".", 1)[-1] if "." in name else ""
    if (extension == "jpeg" and kind == "jpg") or extension == kind:
        return name
    return f"{name}.{kind}"


def _mkdirs(path):
    missing = []
    while not path.exists():
        missing.append(path)
        path = path.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)


def _copy(source, path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle, open(source, "rb") as read:
        shutil.copyfileobj(read, handle, media.CHUNK)


def _head(path):
    with open(path, "rb") as handle:
        return handle.read(media.PICTURE_HEAD_BYTES)


def block(number, placed, skipped):
    """The task block: the files placed, ``{id: path}``, and the ids not fetched, ``{id: reason}``."""
    if not placed and not skipped:
        return f"The files of pull request #{number} cite no media:<id>."
    lines = [f"Pictures cited by the files of pull request #{number}, outside the worktree; open each with Read:"]
    lines += [f"- media:{name}: {placed[name]}" for name in sorted(placed)] or ["- none"]
    if skipped:
        lines.append("Not fetched:")
        lines += [f"- media:{name}: {skipped[name]}" for name in sorted(skipped)]
    return "\n".join(lines)


def place(p, gh, root, role, pull, issues, staged, final):
    """Write the pictures ``pull`` cites into ``staged`` (made only when there is one to place) and return the
    task block, which names each file under ``final``. ``CitedError`` otherwise, and ``staged`` is gone."""
    staged, final, number = Path(staged), Path(final), pull["number"]
    try:
        ids = cited(root, pull)
        chosen, skipped = plan(p, ids, rows(root, pull["git"]["head"]))
    except GitError as error:
        raise CitedError(f"cited media: {error}") from None
    sources = {name: job_file(p, name, sha256, size, source) for name, sha256, size, source in chosen}
    if any(path is None for path in sources.values()):
        found = down._matching(issues, RESOURCE)
        if found:
            raise CitedError(f"resource {RESOURCE} is down (#{found[0]})")
        try:
            media.public_base(p.settings)
        except media.Refused as error:
            raise CitedError(f"cited media: {error}") from None
    shutil.rmtree(staged, ignore_errors=True)
    if not chosen:
        return block(number, {}, skipped)
    placed, files = {}, {}
    try:
        _mkdirs(staged.parent)
        staged.mkdir(mode=0o700)
        os.chmod(staged, 0o700)
        for name, sha256, size, _ in chosen:
            part = staged / f".{name}.part"
            if sources[name] is not None:
                _copy(sources[name], part)
            else:
                media.fetch(p.settings, sha256, size, part)
            kind = media.picture_type(_head(part))
            file = _file_name(name, kind) if kind else None
            if file is None or file in files:
                part.unlink()
                skipped[name] = NOT_A_PICTURE if file is None else f"its file {file} is media:{files[file]}'s"
                continue
            os.rename(part, staged / file)
            files[file], placed[name] = name, str(final / file)
    except media.Failed as error:
        shutil.rmtree(staged, ignore_errors=True)
        issue = down.report(p, gh, RESOURCE, f"a cited picture could not be fetched: {error}",
                            f"launch {role} on #{number}")
        raise CitedError(f"resource {RESOURCE} is down (#{issue})") from None
    except OSError as error:
        shutil.rmtree(staged, ignore_errors=True)
        raise CitedError(f"cited media: {error.strerror}") from None
    if not placed:
        staged.rmdir()
    return block(number, placed, skipped)
