"""Media: no media bytes in git, and a well-formed media manifest."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from ..lint import Finding, LintError, files, read_tsv

MEDIA_EXTENSIONS = frozenset(
    "png jpg jpeg webp gif tif tiff exr psd mp4 mov mkv webm avi wav mp3 flac ogg aac "
    "fbx obj glb gltf usd usdz blend uasset umap abc vdb".split())
MAX_BYTES = 1024 * 1024
SNIFF_BYTES = 8192
MANIFEST = "media/manifest.tsv"
HEADER = ["id", "sha256", "bytes", "source"]
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _bytes_finding(root: Path, rel: str) -> Finding | None:
    if (root / rel).is_symlink() and not (root / rel).exists():
        return Finding(rel, 1, "media-link", "dangling symbolic link")
    ext = PurePosixPath(rel).suffix[1:].lower()
    if ext in MEDIA_EXTENSIONS:
        return Finding(rel, 1, "media-bytes", f"media file (.{ext}) in the tree; keep it in the media store")
    path = root / rel
    try:
        with path.open("rb") as f:
            head = f.read(SNIFF_BYTES)
        size = path.stat().st_size
    except OSError as e:
        raise LintError(f"cannot read {rel}: {type(e).__name__}") from None
    nul = head.find(b"\0")
    if nul >= 0:
        return Finding(rel, head.count(b"\n", 0, nul) + 1, "media-bytes", "binary file (NUL byte) in the tree")
    if size > MAX_BYTES:
        return Finding(rel, 1, "media-bytes", f"file is {size} bytes, over the {MAX_BYTES} byte limit")
    return None


def _manifest(root: Path) -> list[Finding]:
    rows = read_tsv(root, MANIFEST)
    if not rows or [c.strip() for c in rows[0][1]] != HEADER:
        n = rows[0][0] if rows else 1
        return [Finding(MANIFEST, n, "manifest-header", "header must be: " + "\\t".join(HEADER))]
    found, seen = [], {}
    for n, fields in rows[1:]:
        fields = [c.strip() for c in fields]
        if len(fields) != 4 or not all(fields):
            found.append(Finding(MANIFEST, n, "manifest-row", "row must have four non-empty fields"))
            continue
        mid, sha, size, _ = fields
        if not SHA256.match(sha):
            found.append(Finding(MANIFEST, n, "manifest-sha256", "sha256 must be 64 lower-case hex characters"))
        if not (size.isdigit() and size.isascii() and int(size) > 0):
            found.append(Finding(MANIFEST, n, "manifest-bytes", "bytes must be a positive integer"))
        if mid in seen:
            found.append(Finding(MANIFEST, n, "manifest-id", f"id {mid} repeats line {seen[mid]}"))
        else:
            seen[mid] = n
    return found


def check(root: Path) -> list[Finding]:
    found = [f for f in (_bytes_finding(root, p.as_posix()) for p in files(root)) if f]
    if (root / MANIFEST).is_file():
        found += _manifest(root)
    return found
