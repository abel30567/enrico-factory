"""The preview comment on a pull that hands off an artifact: what a job made or a maker committed, made visible.

``after_job`` (called by ``factory run`` on a DONE job) and ``after_maker`` (called by ``finish.post`` after a
maker's ``READY`` or ``ADDRESSED``) post one comment with role ``ROLE`` whose first line is ``HEADING``, a
heading and never a marker. Per item, in name order: a picture is embedded by its public link in the media
store; a GLB gets its ``glb.summary`` line and a sheet of four rendered views; a video a contact sheet of
frames; a markdown package a readable summary (``summarise``). The sheets come from the previewer, by default
``kit.lib.preview``, imported only when a file that is not markdown is looked at; a caller may pass any object
with ``kind_of``, ``of``, ``Preview`` and ``PreviewError``.

The comment ends with a key line, ``<!-- preview-of <sha256> ... -->``. ``post`` reads the earlier comments
of ``ROLE`` first: one with the same set of hashes and no ``Preview not available:`` line means nothing is
built, uploaded or posted, so a rerun posts nothing twice. Bytes never go in git, and the body never holds a
machine path (``guard``). The two ``after_*`` hooks never raise: they return the reason a comment was not
posted, or None.
"""

from __future__ import annotations

import hashlib
import importlib
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Callable, NamedTuple

from kit.lib import gitstate, github, glb, media, resources
from kit.lib.github import GitHubError
from kit.lib.gitstate import GitError

ROLE = "program:preview"
HEADING = "## Preview of what this pull hands off"
PREVIEW_MODULE = "kit.lib.preview"
MEDIA = "store.media"
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

def of_job(out_dir, outputs) -> list[Item]:
    """An item per output row whose file in ``out_dir`` is a regular file, sorted by name."""
    found = []
    for row in outputs:
        name = row["name"]
        path = Path(out_dir) / name
        if not _NAME.fullmatch(name) or path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        found.append(Item(name, hashlib.sha256(data).hexdigest(), len(data), str(path), None))
    return sorted(found)


def of_branch(root, base_ref, branch) -> list[Item]:
    """An item per markdown file ``branch`` changes against ``base_ref``, read from the branch, sorted by name."""
    found = []
    for name in gitstate.changed(root, base_ref, branch):
        if not name.endswith(".md") or not _NAME.fullmatch(name):
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

def guard(body: str, forbid=()) -> None:
    """``HandoffError`` when the body, its URLs and key line removed, holds a machine path or a ``forbid``."""
    lines = [line for line in body.split("\n") if not _KEY.fullmatch(line.strip())]
    text = _URL.sub(" ", "\n".join(lines))
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


def _sheet(preview, put):
    """The link of the sheet ``preview.data``, put from a temporary file that is removed after."""
    tmp = tempfile.mkdtemp(prefix="factory-preview-")
    try:
        path = os.path.join(tmp, f"{hashlib.sha256(preview.data).hexdigest()}.{preview.ext}")
        with open(path, "wb") as handle:
            handle.write(preview.data)
        return put(path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _text_of(item):
    if item.text is not None:
        return item.text
    with open(item.path, "rb") as handle:
        return handle.read(TEXT_MAX_BYTES).decode("utf-8", "replace")


def _block(item, kind, put, previews):
    """``(lines, complete)`` of one item."""
    lines = [f"### `{item.name}`", f"sha256 {item.sha256[:12]}, {_size(item.size)}"]
    if kind == "text":
        found = [" " + line if line.startswith("~~~") else line for line in summarise(_text_of(item))]
        return lines + [FENCE] + found + [FENCE], True
    errors = (previews.PreviewError, HandoffError)
    if kind == "picture":
        try:
            return lines + [f"![{item.name}]({put(item.path)})"], True
        except HandoffError as error:
            return lines + [f"{UNAVAILABLE} {error}"], False
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
        return lines + [f"{UNAVAILABLE} {error}"], False
    notes = "; ".join(preview.notes)
    try:
        lines.append(f"![{alt}]({_sheet(preview, put)})")
        complete = True
    except errors as error:
        lines.append(f"{UNAVAILABLE} {error}")
        complete = False
    return lines + ([notes] if notes else []), complete


def _showable(items, previews):
    found = []
    for item in sorted(items):
        kind = _kind(item, previews)
        if kind is not None:
            found.append((item, kind))
    return found


def _key_line(hashes):
    return "<!-- preview-of " + " ".join(sorted(hashes)) + " -->"


def build(items, put, source, previews=None, forbid=()) -> tuple[str, bool]:
    """``(body, complete)``: the comment for ``items``; ``complete`` is False when a preview is not available."""
    previews = _DEFAULT if previews is None else previews
    showable = _showable(items, previews)
    key = _key_line(item.sha256 for item, _ in showable)
    lines, complete, images, shown = [HEADING, "", source, ""], True, 0, 0
    for item, kind in showable[:MAX_ITEMS]:
        if kind != "text":
            if images == MAX_IMAGES:
                continue
            # The image line is short: the item fits when its first lines and a long link do.
            probe = [f"### `{item.name}`", "x" * 400, f"![{item.name}, contact sheet](" + "x" * 400 + ")"]
            if not _fits(lines + probe, key):
                break
            images += 1
        block, done = _block(item, kind, put, previews)
        if not _fits(lines + block, key):
            break
        lines += block
        complete = complete and done
        shown += 1
    if shown < len(showable):
        lines.append(f"{len(showable) - shown} more file(s) are not shown.")
    body = "\n".join(lines + ["", key]) + "\n"
    guard(body, forbid)
    return body, complete


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


def post(gh, number, items, put, source, previews=None, forbid=()) -> dict | None:
    """Post the comment for ``items`` on ``number`` unless an earlier one says the same; the comment or None."""
    previews = _DEFAULT if previews is None else previews
    showable = _showable(items, previews)
    if not showable:
        return None
    hashes = {item.sha256 for item, _ in showable}
    same = [c for c in gh.comments(number) if c["role"] == ROLE and _hashes(c["body"]) == hashes]
    if any(not _incomplete(c["body"]) for c in same):
        return None
    body, _ = build(items, put, source, previews, forbid)
    if any(c["body"].rstrip("\n") == body.rstrip("\n") for c in same):
        return None
    return gh.post(number, body, ROLE)


def uploader(p) -> Callable[[str], str]:
    """``put(path) -> link`` through the media store; the register and the key are read on the first call."""
    state = {}

    def put(path):
        if not state:
            register, problems = resources.load(resources.REGISTER)
            if problems:
                raise HandoffError("; ".join(problems))
            found = {r["id"]: r for r in register["resources"]}
            if MEDIA not in found:
                raise HandoffError(f"the register has no resource {MEDIA}")
            values = resources.settings(p.root, register)
            ok, reasons = resources.check(found[MEDIA], values, register, p.root)
            if not ok:
                raise HandoffError(f"resource {MEDIA} is not usable: {'; '.join(reasons)}")
            key_file = os.path.join(resources.expand(values[register["secrets_dir_setting"]]), MEDIA)
            state.update(values=values, key_file=key_file)
        try:
            return media.put(state["values"], state["key_file"], path)["link"]
        except (media.Refused, media.Failed) as error:
            raise HandoffError(f"the media store did not take a preview: {error}") from None

    return put


def _reason(error):
    """The fixed text of an error that stopped a preview."""
    if isinstance(error, ImportError):
        return NOT_INSTALLED
    known = (HandoffError, GitHubError, GitError, media.Refused, media.Failed)
    try:
        known += (_DEFAULT.PreviewError,)
    except Exception:  # the previewer is not there: none of its errors can have been raised
        pass
    return str(error) if isinstance(error, known) else type(error).__name__


def after_job(p, gh, number, digest, tool, out_dir, outputs) -> str | None:
    """Post the preview of a DONE job's outputs on the pull ``number``; why it was not posted, or None."""
    try:
        post(gh, number, of_job(out_dir, outputs), uploader(p), f"The outputs of job {digest[:12]} by tool {tool}.",
             forbid=_forbidden(p))
    except Exception as error:  # the run is recorded whatever happens here: say why, never raise
        return _reason(error)
    return None


def after_maker(p, gh, meta, number) -> str | None:
    """Post the preview of the markdown a maker's branch changes on the pull ``number``; why not, or None."""
    try:
        post(gh, number, of_branch(p.root, meta["base_ref"], meta["branch"]), uploader(p),
             f"The markdown files the {meta['role']} committed on this pull.", forbid=_forbidden(p))
    except Exception as error:  # the marker is posted whatever happens here: say why, never raise
        return _reason(error)
    return None
