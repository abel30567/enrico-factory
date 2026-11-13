"""The snapshot of a watched topic's reads and rankings, for the roles that read trends (brief section 16).

At launch, ``build`` runs the ranking command (``cmd_trends``) for each platform the topic watches and
keeps its output and a byte copy of the current read; ``write`` puts them in ``<worktree>/.snapshot/``,
outside git, and for a maker writes the trend cards there too. ``remove`` takes the snapshot away when
the run ends, before anything of the worktree is committed. A ``SnapshotError`` message names a read by
its path under the state directory, never by its absolute path.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import shutil
from pathlib import Path

from kit.lib import cmd_trends, trends

ROLES = ("researcher", "trend-scout")
DIR = ".snapshot"
# A platform the topic does not watch, or one with no read yet, has no snapshot file.
SKIPPED = ("does not watch", "no reads of")

_READ_AT = re.compile(r"trends \S+ \S+: read (\S+),")


class SnapshotError(Exception):
    """A snapshot that could not be made; the text is the whole launch refusal."""


def _trends(argv):
    """``(exit code, stdout, stderr)`` of ``cmd_trends.main(argv)`` in this process."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cmd_trends.main(argv)
    return code, out.getvalue(), err.getvalue()


def _refusal(err):
    lines = err.splitlines()
    return SnapshotError("snapshot: " + (lines[0] if lines else "trends gave no reason"))


def build(p, role, topic) -> dict[str, bytes]:
    """The snapshot files of ``topic`` for ``role``, by name; empty for another role or an unwatched topic.

    Creates nothing.
    """
    if role not in ROLES or not topic or not (Path(p.root) / "research" / "topics" / f"{topic}.json").is_file():
        return {}
    files = {}
    for platform in trends.PLATFORMS:
        code, out, err = _trends([topic, platform, "--root", str(p.root)])
        if code == 1 and any(word in err for word in SKIPPED):
            continue
        if code != 0:
            raise _refusal(err)
        found = _READ_AT.match(out)
        if found is None:
            raise SnapshotError(f"snapshot: trends {topic} {platform}: no read time in its first line")
        stamp = found.group(1).replace("-", "").replace(":", "")
        try:
            read = (trends.reads_dir(p, topic, platform) / f"{stamp}.json").read_bytes()
        except OSError as error:
            raise SnapshotError(f"snapshot: {error.strerror}") from None
        files[f"{platform}.ranking.txt"] = out.encode("utf-8")
        files[f"{platform}.read.json"] = read
    return files


def write(p, worktree, topic, files, cards) -> None:
    """Write ``files`` to ``<worktree>/.snapshot/`` (mode 0700); with ``cards``, also the trend card of
    each platform that has files, under ``research/trends/`` of the worktree. Nothing with no files."""
    if not files:
        return
    directory = Path(worktree) / DIR
    try:
        directory.mkdir(mode=0o700)
        os.chmod(directory, 0o700)
        for name, data in sorted(files.items()):
            (directory / name).write_bytes(data)
    except OSError as error:
        raise SnapshotError(f"snapshot: {error.strerror}") from None
    if not cards:
        return
    for platform in trends.PLATFORMS:
        if f"{platform}.ranking.txt" in files:
            code, _, err = _trends([topic, platform, "--root", str(p.root), "--write", str(worktree)])
            if code != 0:
                raise _refusal(err)


def remove(worktree) -> None:
    """Remove ``<worktree>/.snapshot/``, if there is one."""
    shutil.rmtree(Path(worktree) / DIR, ignore_errors=True)
