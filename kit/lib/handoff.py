"""The preview comment on a pull that hands off an artifact: what a job made or a maker committed, made visible.

``after_job`` (called by ``factory run`` on a DONE job) and ``after_maker`` (called by ``finish.post`` after a
maker's ``READY`` or ``ADDRESSED``) post one comment with role ``ROLE`` whose first line is ``HEADING``, a
heading and never a marker. Per item, in name order: a picture is shown as it is; a GLB gets its
``glb.summary`` line and a sheet of four rendered views; a video a contact sheet of frames; a markdown package
a readable summary (``summarise``). The sheets come from the previewer, by default
``kit.lib.preview``, imported only when a file that is not markdown is looked at; a caller may pass any object
with ``kind_of``, ``of``, ``Preview`` and ``PreviewError``.

The pictures and sheets are GitHub attachments of the comment, so only people who can read the repository
see them; nothing goes to the media store. ``build`` copies each picture, under its name, and writes each
sheet, as ``<sha256>.<ext>``, into a directory ``post`` makes and removes after the post; the body
references each by that relative name (``![panel.png](./panel.png)``), and ``post`` passes the files to
``gh.post`` with that directory as the ``root`` the names are relative to; it uploads them and rewrites the
references to their links. A file larger than ``github.ATTACH_MAX`` gets a ``Preview not available:`` line
instead of an attachment. A name with an empty, ``.`` or ``..`` part is not an item.

The comment ends with a key line, ``<!-- preview-of <sha256> ... -->``. ``post`` reads the earlier comments
of ``ROLE`` first: one with the same set of hashes and no ``Preview not available:`` line means nothing is
built, attached or posted, so a rerun posts nothing twice. Bytes never go in git, and the body never holds a
machine path (``guard``; only the reference ``](./<name>)`` to a file the comment attaches is not one). The
two ``after_*`` hooks never raise: they return the reason a comment was not posted, or None.
"""

from __future__ import annotations

import hashlib
import importlib
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import NamedTuple

from kit.lib import gitstate, github, glb, resources
from kit.lib.github import GitHubError
from kit.lib.gitstate import GitError

ROLE = "program:preview"
HEADING = "## Preview of what this pull hands off"
PREVIEW_MODULE = "kit.lib.preview"
MAX_ITEMS = 24
MAX_IMAGES = 12
TEXT_CHARS = 4000
TEXT_MAX_BYTES = 1048576
LINES = 3
SECTIONS = 8
LINE_CHARS = 240
MORE_TITLES = 20
KEPT_FOR_THE_END = 2000
HEAD_BYTES = 16
CUT = f"[cut at {TEXT_CHARS} characters]"
WITHHELD = "[line withheld: it holds a path]"
UNAVAILABLE = "Preview not available:"
NOT_INSTALLED = f"{PREVIEW_MODULE} is not installed"
HOLDS_A_PATH = "the comment holds a path"
FENCE = "~~~~"

_NAME = re.compile(r"[A-Za-z0-9._-][A-Za-z0-9._/-]*")
_HEADING = re.compile(r"#{1,6}[ \t]+\S")
_KEY = re.compile(r"<!-- preview-of ([0-9a-f ]*) -->")
_URL = re.compile(r"https?://\S+")
_ROOTS = frozenset({"Users", "home", "tmp", "var", "private", "etc", "opt", "root", "mnt", "Volumes"})
_SLASHED = re.compile(r"(?<!\w)/([\w.@%+~-]+)(/[\w.@%+~-]+)?")
_PATHS = (re.compile(r"~/"), re.compile(r"(?<!\S)~(?!\S)"), re.compile(r"file://"),
          re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]"), re.compile(r"\$HOME"))


class HandoffError(Exception):
    """A fixed message or a store's or GitHub's reason; never a path."""


class Item(NamedTuple):
    name: str
    sha256: str
    size: int
    path: str | None
    text: str | None


class _Default:
    """The module ``kit.lib.preview``, imported on the first use of one of its names."""

    def __getattr__(self, name):
        return getattr(importlib.import_module(PREVIEW_MODULE), name)


_DEFAULT = _Default()


# -- the items

def _named(name):
    """Whether ``name`` is a relative name with no empty, ``.`` or ``..`` part."""
    return bool(_NAME.fullmatch(name)) and all(part not in ("", ".", "..") for part in name.split("/"))


def of_job(out_dir, outputs) -> list[Item]:
    """An item per output row whose file in ``out_dir`` is a regular file, sorted by name."""
    found = []
    for row in outputs:
        name = row["name"]
        path = Path(out_dir) / name
        if not _named(name) or path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        found.append(Item(name, hashlib.sha256(data).hexdigest(), len(data), str(path), None))
    return sorted(found)


def of_branch(root, base_ref, branch) -> list[Item]:
    """An item per markdown file ``branch`` changes against ``base_ref``, read from the branch, sorted by name."""
    found = []
    for name in gitstate.changed(root, base_ref, branch):
        if not name.endswith(".md") or not _named(name):
            continue
        text = gitstate.show(root, branch, name)
        if text is None:
            continue
        try:
            data = text.encode("utf-8")
        except UnicodeEncodeError:
            continue
        if len(data) > TEXT_MAX_BYTES:
            continue
        found.append(Item(name, hashlib.sha256(data).hexdigest(), len(data), None, text))
    return sorted(found)


def _kind(item, previews):
    """``text``, ``picture``, ``glb``, ``video`` or None (not shown)."""
    if item.name.endswith(".md"):
        return "text"
    if item.path is None:
        return None
    with open(item.path, "rb") as handle:
        return previews.kind_of(handle.read(HEAD_BYTES))


# -- the guard

def guard(body: str, forbid=(), attached=()) -> None:
    """``HandoffError`` when the body, its URLs, key line and references ``](./<name>)`` to the ``attached``
    names removed, holds a machine path or a ``forbid``."""
    lines = [line for line in body.split("\n") if not _KEY.fullmatch(line.strip())]
    text = _URL.sub(" ", "\n".join(lines))
    for name in sorted(attached, key=len, reverse=True):
        text = text.replace(f"](./{name})", "]( )")
    for found in _SLASHED.finditer(text):
        if found.group(2) or found.group(1) in _ROOTS:
            raise HandoffError(HOLDS_A_PATH)
    if any(pattern.search(text) for pattern in _PATHS) or any(s and s in text for s in forbid):
        raise HandoffError(HOLDS_A_PATH)


def _forbidden(p):
    """The strings no comment of the production ``p`` may hold."""
    found = [str(Path.home()), tempfile.gettempdir(), str(p.root)]
    for name in ("STATE_DIR", "SECRETS_DIR"):
        value = p.settings.get(name, "")
        if value:
            found += [value, resources.expand(value)]
    return tuple(sorted({s for s in found if len(s) > 3}))


# -- the text summary

def _cut(line):
    return line if len(line) <= LINE_CHARS else line[:LINE_CHARS - 1] + "…"


def _shown(line):
    try:
        guard(line)
    except HandoffError:
        return WITHHELD
    return _cut(line)


def summarise(text: str) -> list[str]:
    """The lead, the first ``SECTIONS`` headings with their first lines, and the titles of the rest."""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for at in range(1, len(lines)):
            if lines[at].strip() == "---":
                lines = lines[at + 1:]
                break
    lead, sections, fenced = [], [], False
    for line in lines:
        if line.startswith("```") or line.startswith("~~~"):
            fenced = not fenced
        elif not fenced and _HEADING.match(line):
            sections.append((line, []))
            continue
        if not line.strip():
            continue
        target = sections[-1][1] if sections else lead
        if len(target) < LINES:
            target.append(line)
    found = [_shown(line) for line in lead]
    for heading, under in sections[:SECTIONS]:
        found += [_shown(heading)] + [_shown(line) for line in under]
    rest = sections[SECTIONS:]
    if rest:
        titles = [heading.lstrip("#").strip() for heading, _ in rest[:MORE_TITLES]]
        found.append(_shown(f"... {len(rest)} more headings: " + "; ".join(titles)))
    if not found:
        return ["(empty)"]
    if len("\n".join(found)) <= TEXT_CHARS:
        return found
    kept, size = [], len(CUT)
    for line in found:
        if size + len(line) + 1 > TEXT_CHARS:
            break
        kept.append(line)
        size += len(line) + 1
    return kept + [CUT]


# -- the body

def _size(n):
    if n < 1000:
        return f"{n} B"
    if n < 1000000:
        return f"{n / 1000:.1f} kB"
    return f"{n / 1000000:.1f} MB"


def _glb_line(data):
    found = glb.summary(data)
    textures = ", ".join(f"{t['width']}x{t['height']}" for t in found["textures"]) or "none"
    skeleton = found["skeleton"]
    bones = "no skeleton" if skeleton is None else f"{len(skeleton['bones'])} bones"
    return (f"{found['triangles']} triangles, {found['materials']} material(s), textures {textures}, "
            f"height {float(found['height_m'])} m, {bones}")


TOO_LARGE = f"{UNAVAILABLE} larger than {_size(github.ATTACH_MAX)}, the most a GitHub attachment holds"


def _attach(directory, name, write):
    """The path of ``name`` in ``directory``, made by ``write(path)`` unless it is there; ``HandoffError`` for a
    name that is not a relative name inside it."""
    if not _named(name):
        raise HandoffError(HOLDS_A_PATH)
    path = os.path.join(directory, *name.split("/"))
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write(path)
    return path


def _text_of(item):
    if item.text is not None:
        return item.text
    with open(item.path, "rb") as handle:
        return handle.read(TEXT_MAX_BYTES).decode("utf-8", "replace")


def _write(data):
    def write(path):
        with open(path, "wb") as handle:
            handle.write(data)
    return write


def _block(item, kind, directory, previews):
    """``(lines, complete, files)`` of one item; ``files`` are the paths in ``directory`` to attach."""
    lines = [f"### `{item.name}`", f"sha256 {item.sha256[:12]}, {_size(item.size)}"]
    if kind == "text":
        found = [" " + line if line.startswith("~~~") else line for line in summarise(_text_of(item))]
        return lines + [FENCE] + found + [FENCE], True, []
    if kind == "picture":
        if item.size > github.ATTACH_MAX:
            return lines + [TOO_LARGE], False, []
        path = _attach(directory, item.name, lambda path: shutil.copyfile(item.path, path))
        return lines + [f"![{item.name}](./{item.name})"], True, [path]
    if kind == "glb":
        with open(item.path, "rb") as handle:
            data = handle.read()
        try:
            lines.append(_glb_line(data))
        except glb.Broken:
            pass
    alt = f"{item.name}, 4 views" if kind == "glb" else f"{item.name}, contact sheet"
    try:
        preview = previews.of(item.path)
    except previews.PreviewError as error:
        return lines + [f"{UNAVAILABLE} {error}"], False, []
    notes = [s for s in ["; ".join(preview.notes)] if s]
    if len(preview.data) > github.ATTACH_MAX:
        return lines + [TOO_LARGE] + notes, False, []
    name = f"{hashlib.sha256(preview.data).hexdigest()}.{preview.ext}"
    path = _attach(directory, name, _write(preview.data))
    return lines + [f"![{alt}](./{name})"] + notes, True, [path]


def _showable(items, previews):
    found = []
    for item in sorted(items):
        kind = _kind(item, previews)
        if kind is not None:
            found.append((item, kind))
    return found


def _key_line(hashes):
    return "<!-- preview-of " + " ".join(sorted(hashes)) + " -->"


def build(items, source, directory, previews=None, forbid=()) -> tuple[str, bool, list]:
    """``(body, complete, files)``: the comment for ``items`` and the files in ``directory`` it attaches;
    ``complete`` is False when a preview is not available."""
    previews = _DEFAULT if previews is None else previews
    showable = _showable(items, previews)
    key = _key_line(item.sha256 for item, _ in showable)
    lines, complete, images, shown, files = [HEADING, "", source, ""], True, 0, 0, []
    for item, kind in showable[:MAX_ITEMS]:
        if kind != "text":
            if images == MAX_IMAGES:
                continue
            # The image line is short: the item fits when its first lines and a long link do.
            probe = [f"### `{item.name}`", "x" * 400, f"![{item.name}, contact sheet](" + "x" * 400 + ")"]
            if not _fits(lines + probe, key):
                break
            images += 1
        block, done, attached = _block(item, kind, directory, previews)
        if not _fits(lines + block, key):
            break
        lines += block
        files += [path for path in attached if path not in files]
        complete = complete and done
        shown += 1
    if shown < len(showable):
        lines.append(f"{len(showable) - shown} more file(s) are not shown.")
    body = "\n".join(lines + ["", key]) + "\n"
    guard(body, forbid, [Path(os.path.relpath(path, directory)).as_posix() for path in files])
    return body, complete, files


def _fits(lines, key):
    return github.fits("\n".join(lines) + "x" * (KEPT_FOR_THE_END + len(key)), ROLE)


# -- posting

def _hashes(body):
    for line in body.split("\n"):
        found = _KEY.fullmatch(line.strip())
        if found:
            return set(found.group(1).split())
    return None


def _incomplete(body):
    return any(line.startswith(UNAVAILABLE) for line in body.split("\n"))


def post(gh, number, items, source, previews=None, forbid=()) -> dict | None:
    """Post the comment for ``items`` on ``number``, its pictures and sheets attached, unless an earlier one
    says the same; the comment or None. The files are made in a temporary directory removed after the post."""
    previews = _DEFAULT if previews is None else previews
    showable = _showable(items, previews)
    if not showable:
        return None
    hashes = {item.sha256 for item, _ in showable}
    same = [c for c in gh.comments(number) if c["role"] == ROLE and _hashes(c["body"]) == hashes]
    if any(not _incomplete(c["body"]) for c in same):
        return None
    tmp = tempfile.mkdtemp(prefix="factory-preview-")
    try:
        body, _, files = build(items, source, tmp, previews, forbid)
        if any(c["body"].rstrip("\n") == body.rstrip("\n") for c in same):
            return None
        if not files:
            return gh.post(number, body, ROLE)
        return gh.post(number, body, ROLE, attachments=files, root=tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _reason(error):
    """The fixed text of an error that stopped a preview."""
    if isinstance(error, ImportError):
        return NOT_INSTALLED
    known = (HandoffError, GitHubError, GitError)
    try:
        known += (_DEFAULT.PreviewError,)
    except Exception:  # the previewer is not there: none of its errors can have been raised
        pass
    return str(error) if isinstance(error, known) else type(error).__name__


def after_job(p, gh, number, digest, tool, out_dir, outputs) -> str | None:
    """Post the preview of a DONE job's outputs on the pull ``number``; why it was not posted, or None."""
    try:
        post(gh, number, of_job(out_dir, outputs), f"The outputs of job {digest[:12]} by tool {tool}.",
             forbid=_forbidden(p))
    except Exception as error:  # the run is recorded whatever happens here: say why, never raise
        return _reason(error)
    return None


def after_maker(p, gh, meta, number) -> str | None:
    """Post the preview of the markdown a maker's branch changes on the pull ``number``; why not, or None."""
    try:
        post(gh, number, of_branch(p.root, meta["base_ref"], meta["branch"]),
             f"The markdown files the {meta['role']} committed on this pull.", forbid=_forbidden(p))
    except Exception as error:  # the marker is posted whatever happens here: say why, never raise
        return _reason(error)
    return None
