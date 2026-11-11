"""The local git state the launcher and the markers read (system brief, sections 8 and 9; harness, section 3).

Every function takes the repository (or worktree) directory first and runs ``git -C <dir> ...`` as an
argument list, never through a shell, with standard input closed. Nothing here contacts a remote.
Refs are resolved to commit ids first, so a ref is never read as an option. Paths are plain lists,
matched literally (no pathspec magic, no globs). Filesystem paths given to ``add_worktree``,
``remove_worktree`` and ``export`` are taken relative to the current directory, as Python does.
"""

import io
import os
import subprocess
import tarfile

from kit.lib import markers

MANIFEST = "media/manifest.tsv"


class GitError(Exception):
    """A git command that failed: ``command`` is the git subcommand, ``status`` its exit status."""

    def __init__(self, command, status, message=""):
        super().__init__("git %s: exit %s: %s" % (command, status, message.strip()))
        self.command = command
        self.status = status


def _subcommand(args):
    it = iter(args)
    for a in it:
        if a == "-c":
            next(it, None)
        elif not a.startswith("-"):
            return a
    return ""


def _run(root, args, env=None, ok=(0,), binary=False):
    full_env = None
    if env:
        full_env = dict(os.environ)
        full_env.update(env)
    text = {} if binary else {"encoding": "utf-8", "errors": "surrogateescape"}
    proc = subprocess.run(["git", "-C", str(root)] + list(args), stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=full_env, **text)
    if proc.returncode not in ok:
        err = proc.stderr.decode("utf-8", "replace") if binary else proc.stderr
        raise GitError(_subcommand(args), proc.returncode, err)
    return proc


def git(root, *args, env=None):
    """Standard output of ``git -C root *args`` as text; ``env`` adds variables to the environment."""
    return _run(root, args, env=env).stdout


def _split(out):
    return [p for p in out.split("\0") if p]


def head(root, ref="HEAD"):
    """The full 40-character commit id ``ref`` names."""
    return git(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").strip()


def merge_base(root, base_ref, head_ref):
    return git(root, "merge-base", head(root, base_ref), head(root, head_ref)).strip()


def commits(root, base_ref, head_ref):
    """The commits a pull request from ``head_ref`` into ``base_ref`` shows, oldest first, with parents."""
    out = git(root, "rev-list", "--reverse", "--topo-order", "--parents",
              "%s..%s" % (head(root, base_ref), head(root, head_ref)))
    rows = [line.split() for line in out.splitlines() if line.strip()]
    return [{"sha": r[0], "parents": r[1:]} for r in rows]


def base_shas(root, base_ref, limit=2000):
    return git(root, "rev-list", "-n", str(int(limit)), head(root, base_ref)).split()


def contains(root, head_ref, base_ref):
    """Whether ``head_ref`` contains ``base_ref``."""
    proc = _run(root, ["merge-base", "--is-ancestor", head(root, base_ref), head(root, head_ref)], ok=(0, 1))
    return proc.returncode == 0


def show(root, ref, path):
    """The text of ``path`` at ``ref``, or None when the path does not exist there."""
    spec = "%s:%s" % (head(root, ref), path)
    if _run(root, ["cat-file", "-e", spec], ok=(0, 1, 128)).returncode != 0:
        return None
    return git(root, "cat-file", "blob", spec)


def _rows(text):
    return [] if text is None else text.splitlines()


def manifest(root, base_ref, head_ref):
    """``markers.manifest_hash`` of the manifest rows at the merge base and at ``head_ref``."""
    base = merge_base(root, base_ref, head_ref)
    return markers.manifest_hash(_rows(show(root, base, MANIFEST)), _rows(show(root, head_ref, MANIFEST)))


def changed(root, base_ref, head_ref):
    """The paths ``head_ref`` changes against its merge base with ``base_ref``, sorted."""
    out = git(root, "diff", "--name-only", "-z", "--no-renames",
              merge_base(root, base_ref, head_ref), head(root, head_ref))
    return sorted(set(_split(out)))


def pull_state(root, pull, comments):
    """The ``pr`` shape ``markers.counts`` reads, plus ``changed``, for a pull record."""
    base_ref, head_ref = pull["base_ref"], pull["head_ref"]
    return {
        "head": head(root, head_ref),
        "manifest": manifest(root, base_ref, head_ref),
        "commits": commits(root, base_ref, head_ref),
        "base_shas": base_shas(root, base_ref),
        "comments": comments,
        "changed": changed(root, base_ref, head_ref),
    }


def add_worktree(root, path, ref, branch=None):
    """A new branch at ``ref``, an existing ``branch``, or a detached checkout of ``ref``, at ``path``."""
    path = os.path.abspath(path)
    if os.path.lexists(path):
        raise GitError("worktree", None, "%s already exists" % path)
    if branch is None:
        git(root, "worktree", "add", "--detach", "--end-of-options", path, head(root, ref))
    elif _run(root, ["show-ref", "--verify", "--quiet", "refs/heads/" + branch], ok=(0, 1)).returncode == 0:
        git(root, "worktree", "add", "--end-of-options", path, branch)
    else:
        git(root, "worktree", "add", "-b", branch, "--end-of-options", path, head(root, ref))


def remove_worktree(root, path):
    git(root, "worktree", "remove", "--force", os.path.abspath(path))


def export(root, ref, dest):
    """The files of ``ref`` under ``dest``, without git metadata, every modification time 0."""
    dest = os.path.abspath(dest)
    if os.path.isdir(dest) and os.listdir(dest) or os.path.lexists(dest) and not os.path.isdir(dest):
        raise GitError("archive", None, "%s is not an empty directory" % dest)
    data = _run(root, ["-c", "tar.umask=022", "archive", "--format=tar", head(root, ref)], binary=True).stdout
    os.makedirs(dest, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
        tar.extractall(dest, filter="data")
    for top, dirs, files in os.walk(dest, topdown=False):
        for name in files + dirs:
            os.utime(os.path.join(top, name), (0, 0), follow_symlinks=False)


def status(worktree):
    """Paths that differ from ``HEAD`` or are untracked and not ignored, sorted; both sides of a rename."""
    entries = _split(git(worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all"))
    paths, i = set(), 0
    while i < len(entries):
        entry = entries[i]
        paths.add(entry[3:])
        if "R" in entry[:2] or "C" in entry[:2]:
            i += 1
            paths.add(entries[i])
        i += 1
    return sorted(paths)


def commit(worktree, paths, message, author):
    """Commit exactly ``paths`` as ``author``; the new head, or None when none of them changed."""
    paths = list(paths)
    if not paths:
        return None
    git(worktree, "--literal-pathspecs", "add", "-A", "--", *paths)
    staged = _run(worktree, ["--literal-pathspecs", "diff", "--cached", "--quiet", "--", *paths], ok=(0, 1))
    if staged.returncode == 0:
        return None
    now = os.environ.get("FACTORY_NOW")
    env = {"GIT_AUTHOR_DATE": now, "GIT_COMMITTER_DATE": now} if now else None
    git(worktree, "-c", "user.name=" + author, "-c", "user.email=%s@factory.invalid" % author,
        "--literal-pathspecs", "commit", "-q", "-m", message, "--only", "--", *paths, env=env)
    return head(worktree)


def restore(worktree, paths):
    """Discard changes to ``paths``: tracked ones from ``HEAD``, untracked ones deleted."""
    paths = list(paths)
    if not paths:
        return
    tracked = set(_split(git(worktree, "--literal-pathspecs", "ls-tree", "-r", "-z", "--name-only",
                             "--full-tree", "HEAD", "--", *paths)))
    kept = [p for p in paths if p in tracked]
    gone = [p for p in paths if p not in tracked]
    if kept:
        git(worktree, "--literal-pathspecs", "checkout", "-q", "HEAD", "--", *kept)
    if gone:
        git(worktree, "--literal-pathspecs", "rm", "-q", "--cached", "--ignore-unmatch", "--", *gone)
        for p in gone:
            full = os.path.join(worktree, p)
            if os.path.lexists(full):
                os.remove(full)
