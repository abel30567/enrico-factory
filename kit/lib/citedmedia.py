"""The pictures a checker's pull request cites, placed where the checker can open them before it starts.

A role asks with the front-matter key ``media: cited``. The ids are every ``media:<id>`` in the text, at the
pull's head, of the files the pull changes, and every id whose row in ``media/manifest.tsv`` at the head is not
in it at the merge base of the pull's base and head (a picture the pull adds); each one's row is the first of
``media/manifest.tsv`` at the head.
A job's picture is copied from ``<STATE_DIR>/jobs/<digest>/out/<name>`` when it holds the row's bytes; any
other comes from the media store's public link. The files go to a staged directory under ``<STATE_DIR>/runs``
that the launcher renames to ``<run dir>/media`` once the run directory exists. A store that does not serve
a picture is an outage: the staged directory is removed and ``[DOWN] store.media`` is found or filed.

``sheets`` draws each GLB of the checker's job in its staged ``review`` directory (``kit.lib.preview``,
loaded when there is a model to draw) and writes the four-view sheet to ``sheets/<name>.png`` of the same
staged directory, so it lands in ``<run dir>/media/sheets``. Its block names each sheet, or why there is none,
with the model's bounds and its depth (the smaller of x and z over the height), which is under the line below
``DEPTH_MIN`` for a character or a set.
"""

import importlib
import math
import os
import re
import shutil
from pathlib import Path

from kit.lib import down, glb, gitstate, media, production
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
SHEETS = "sheets"
# The depth under which a character or a set is flat: the thinnest human measured is 0.1, a card plank 0.034.
DEPTH_MIN = 0.09
KINDS = (("character", "bible/canon/characters"), ("set", "bible/canon/places"))
NOT_INSTALLED = "kit.lib.preview is not installed"
NO_BOUNDS = "no bounds: not a GLB the kit can read"

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
    """The ids cited by the text at the pull's head of the paths it changes, and those whose manifest row the
    pull adds against the merge base of its base and head, sorted, each once."""
    head, found = pull["git"]["head"], set()
    for path in pull["git"]["changed"]:
        text = gitstate.show(root, head, path)
        if text is not None:
            found.update(CITED.findall(text))
    before = rows(root, gitstate.merge_base(root, pull["base_ref"], pull["head_ref"]))
    found.update(name for name, row in rows(root, head).items() if before.get(name) != row)
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


def _index(value):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("index")
    return value


def _corner(value):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError("bound")
    for number in value:
        if not isinstance(number, (int, float)) or isinstance(number, bool) or not math.isfinite(number):
            raise ValueError("bound")
    return value


def _extents(document):
    accessors, lows, highs = document["accessors"], [], []
    for mesh in document["meshes"]:
        for primitive in mesh["primitives"]:
            position = accessors[_index(primitive["attributes"]["POSITION"])]
            lows.append(_corner(position["min"]))
            highs.append(_corner(position["max"]))
    if not lows:
        raise ValueError("no primitive")
    size = tuple(round(max(h[i] for h in highs) - min(low[i] for low in lows), 4) for i in range(3))
    if not all(math.isfinite(value) for value in size) or not size[1] > 0:
        raise ValueError("height")
    return size


def extents(data):
    """``(x, y, z)`` in metres of a GLB: per axis, the highest ``max`` less the lowest ``min`` of the
    ``POSITION`` accessors, rounded to 4 places, no node transform. ``glb.Broken`` when it cannot be read."""
    document, _ = glb.read(data)
    try:
        return _extents(document)
    except (KeyError, IndexError, TypeError, ValueError, AttributeError, OverflowError):
        raise glb.Broken() from None


def _kind(root, pull, unit):
    head = pull["git"]["head"]
    try:
        for kind, directory in KINDS:
            if gitstate.show(root, head, f"{directory}/{unit}.md") is not None:
                return kind
    except GitError as error:
        raise CitedError(f"cited media: {error}") from None
    return "prop"


def _models(review):
    found = []
    for path in sorted(Path(review).iterdir()) if Path(review).is_dir() else []:
        if path.is_file() and not path.is_symlink():
            with open(path, "rb") as handle:
                if handle.read(len(glb.MAGIC)) == glb.MAGIC:
                    found.append(path)
    return found


def _bounds(data, kind):
    try:
        x, y, z = extents(data)
    except glb.Broken:
        return f"  {NO_BOUNDS}"
    depth = round(min(x, z) / y, 4)
    if kind == "prop":
        verdict = "no line for a prop"
    else:
        verdict = f"{'under' if depth < DEPTH_MIN else 'not under'} the line of {DEPTH_MIN:g} for a {kind}"
    return f"  x {x:g} m, y {y:g} m, z {z:g} m; depth {depth:g} of the height: {verdict}"


def _write_sheet(staged, path, data):
    sheets_dir = staged / SHEETS
    if not sheets_dir.exists():
        _mkdirs(sheets_dir)
        os.chmod(staged, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def sheets(root, ticket, pull, review, staged, final, previews=None):
    """Write a sheet of each GLB in ``review`` to ``staged``/sheets (made only for the first sheet) and return
    the task block, which names each sheet under ``final``; None when ``review`` holds no GLB."""
    try:
        models = _models(review) if review is not None else []
    except OSError as error:
        raise CitedError(f"cited media: {error.strerror}") from None
    if not models:
        return None
    staged, final = Path(staged), Path(final)
    kind = _kind(root, pull, ticket["unit"])
    lines = [f"Sheets of the models of the job of pull request #{pull['number']}, outside the worktree; "
             "open each with Read:"]
    missing = False
    if previews is None:
        try:
            previews = importlib.import_module("kit.lib.preview")
        except ImportError:
            missing = True
    for path in models:
        try:
            data = path.read_bytes()
        except OSError as error:
            raise CitedError(f"cited media: {error.strerror}") from None
        sheet, text = None, NOT_INSTALLED
        if not missing:
            try:
                sheet = previews.glb_sheet(data)
            except previews.PreviewError as error:
                text = str(error)
            except Exception as error:  # noqa: BLE001 - a sheet that fails does not fail the launch
                text = type(error).__name__
        if sheet is None:
            lines.append(f"- review/{path.name}: no sheet: {text}")
        else:
            file = f"{path.name}.png"
            try:
                _write_sheet(staged, staged / SHEETS / file, sheet.data)
            except OSError as error:
                raise CitedError(f"cited media: {error.strerror}") from None
            lines.append(f"- review/{path.name}: {final / SHEETS / file}")
            lines.append("  " + "; ".join(sheet.notes))
        lines.append(_bounds(data, kind))
    return "\n".join(lines)
