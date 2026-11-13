"""Tests for kit/lib/gitstate.py. Each test builds its own repository with `git init` in a temporary directory."""

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kit.lib import gitstate, markers  # noqa: E402
from kit.lib.gitstate import GitError  # noqa: E402

HEX64 = re.compile(r"[0-9a-f]{64}")
STAGE = {"maker": "writer", "checker": ["editor"], "judge": None, "lint": []}
ROLES = {"writer": "maker", "editor": "checker"}
NOW = "2026-11-11T10:00:00Z"


def isolated_env():
    """No user or system git config, no FACTORY_NOW from the caller."""
    patch = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
    patch.start()
    os.environ.pop("FACTORY_NOW", None)
    return patch


class RepoTest(unittest.TestCase):
    """A base branch `main` with two commits and a `media/manifest.tsv`, and a branch `feature` at its tip."""

    def setUp(self):
        self.addCleanup(isolated_env().stop)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        self.git("init", "-q", "-b", "main")
        self.write("README.md", "hello\n")
        self.write("old.txt", "old\n")
        self.write("media/manifest.tsv", "id\tfile\n")
        self.write("bin/run.sh", "#!/bin/sh\necho run\n")
        os.chmod(self.root / "bin/run.sh", 0o755)
        self.m1 = self.commit_all("first")
        self.write("notes.txt", "notes\n")
        self.m2 = self.commit_all("second")
        self.git("branch", "feature")

    def git(self, *args, cwd=None):
        return gitstate.git(cwd or self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid",
                            *args)

    def write(self, path, text, cwd=None):
        full = Path(cwd or self.root) / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(text)

    def read(self, path, cwd=None):
        return (Path(cwd or self.root) / path).read_text()

    def commit_all(self, message, cwd=None):
        self.git("add", "-A", cwd=cwd)
        self.git("commit", "-q", "-m", message, cwd=cwd)
        return gitstate.head(cwd or self.root)

    def on(self, branch):
        self.git("checkout", "-q", branch)

    def merge_main(self):
        self.on("feature")
        self.git("merge", "-q", "--no-ff", "--no-edit", "main")
        return gitstate.head(self.root)

    def feature_commits(self, n=2):
        self.on("feature")
        shas = []
        for i in range(n):
            self.write("f%d.txt" % i, "feature %d\n" % i)
            shas.append(self.commit_all("feature %d" % i))
        return shas

    def main_moves(self, path="base.txt", text="base\n"):
        self.on("main")
        self.write(path, text)
        sha = self.commit_all("base moves")
        self.on("feature")
        return sha


class GitTest(RepoTest):
    def test_git_returns_stdout(self):
        self.assertEqual(gitstate.git(self.root, "rev-parse", "--abbrev-ref", "HEAD"), "main\n")

    def test_failure_raises_with_subcommand_and_status(self):
        with self.assertRaises(GitError) as cm:
            gitstate.git(self.root, "-c", "x.y=z", "rev-parse", "--verify", "no-such-ref")
        self.assertEqual(cm.exception.command, "rev-parse")
        self.assertEqual(cm.exception.status, 128)
        with self.assertRaises(GitError):
            gitstate.head(self.root, "no-such-ref")
        with self.assertRaises(GitError):
            gitstate.git(self.tmp / "not-a-repo", "status")

    def test_env_is_added(self):
        out = gitstate.git(self.root, "var", "GIT_AUTHOR_IDENT", env={"GIT_AUTHOR_NAME": "someone"})
        self.assertTrue(out.startswith("someone "))

    def test_no_shell_and_stdin_closed(self):
        calls = []
        real = subprocess.run

        def spy(*args, **kwargs):
            calls.append((args, kwargs))
            return real(*args, **kwargs)

        with mock.patch.object(gitstate.subprocess, "run", spy):
            gitstate.pull_state(self.root, {"base_ref": "main", "head_ref": "feature"}, [])
            gitstate.status(self.root)
            gitstate.export(self.root, "main", self.tmp / "out")
        self.assertTrue(calls)
        for args, kwargs in calls:
            self.assertIsInstance(args[0], list)
            self.assertEqual(args[0][:3], ["git", "-C", str(self.root)])
            self.assertFalse(kwargs.get("shell", False))
            self.assertIs(kwargs["stdin"], subprocess.DEVNULL)

    def test_source_never_names_a_remote_command_or_a_shell(self):
        source = (ROOT / "kit" / "lib" / "gitstate.py").read_text()
        self.assertEqual(re.findall(r'shell=True|"push"|"fetch"|"pull"|"remote"', source), [])


class RefsTest(RepoTest):
    def test_head_is_full_id(self):
        self.assertRegex(gitstate.head(self.root), r"^[0-9a-f]{40}$")
        self.assertEqual(gitstate.head(self.root, "main~1"), self.m1)

    def test_merge_base(self):
        self.feature_commits()
        self.main_moves()
        self.assertEqual(gitstate.merge_base(self.root, "main", "feature"), self.m2)

    def test_base_shas(self):
        self.assertEqual(gitstate.base_shas(self.root, "main"), [self.m2, self.m1])
        self.assertEqual(gitstate.base_shas(self.root, "main", limit=1), [self.m2])

    def test_show(self):
        self.assertEqual(gitstate.show(self.root, "main", "README.md"), "hello\n")
        self.assertEqual(gitstate.show(self.root, "main", "media/manifest.tsv"), "id\tfile\n")
        self.assertIsNone(gitstate.show(self.root, "main", "missing.txt"))
        self.assertIsNone(gitstate.show(self.root, self.m1, "notes.txt"))
        with self.assertRaises(GitError):
            gitstate.show(self.root, "no-such-ref", "README.md")


class CommitsTest(RepoTest):
    """Criterion 1."""

    def test_feature_commits_oldest_first_with_parents(self):
        f1, f2 = self.feature_commits()
        self.assertEqual(gitstate.commits(self.root, "main", "feature"),
                         [{"sha": f1, "parents": [self.m2]}, {"sha": f2, "parents": [f1]}])

    def test_merge_of_the_base(self):
        f1, f2 = self.feature_commits()
        m3 = self.main_moves()
        merge = self.merge_main()
        got = gitstate.commits(self.root, "main", "feature")
        self.assertEqual([c["sha"] for c in got], [f1, f2, merge])
        self.assertEqual(got[-1]["parents"], [f2, m3])
        base = set(gitstate.base_shas(self.root, "main"))
        self.assertFalse(base & {c["sha"] for c in got})

    def test_no_commits(self):
        self.assertEqual(gitstate.commits(self.root, "main", "feature"), [])


class PullStateTest(RepoTest):
    """Criterion 2: the shape markers.counts reads."""

    def verdict(self, state):
        return {"id": 2, "created_at": "2026-11-11T10:05:00Z",
                "body": "[EDITOR] APPROVED\n\nreviewed head %s manifest %s" % (state["head"], state["manifest"])}

    def test_counts_follow_the_git_history(self):
        self.feature_commits()
        pull = {"base_ref": "main", "head_ref": "feature"}
        ready = {"id": 1, "created_at": "2026-11-11T10:00:00Z", "body": "[WRITER] READY\nok"}
        state = gitstate.pull_state(self.root, pull, [ready])
        self.assertEqual(list(state), ["head", "manifest", "commits", "base_shas", "comments", "changed"])
        self.assertEqual(state["head"], gitstate.head(self.root, "feature"))
        self.assertEqual(state["comments"], [ready])
        self.assertEqual(state["changed"], ["f0.txt", "f1.txt"])
        verdict = self.verdict(state)
        comments = [ready, verdict]
        self.assertEqual(markers.counts(verdict, gitstate.pull_state(self.root, pull, comments), STAGE, ROLES),
                         (True, "counts"))

        self.main_moves()
        self.merge_main()
        merged = gitstate.pull_state(self.root, pull, comments)
        self.assertNotEqual(merged["head"], state["head"])
        self.assertEqual(markers.counts(verdict, merged, STAGE, ROLES), (True, "counts"))

        self.write("f0.txt", "changed\n")
        self.commit_all("ordinary")
        ok, reason = markers.counts(verdict, gitstate.pull_state(self.root, pull, comments), STAGE, ROLES)
        self.assertFalse(ok)
        self.assertTrue(reason.startswith("head: "), reason)


class ManifestTest(RepoTest):
    """Criterion 3."""

    def test_manifest_hash(self):
        self.feature_commits(1)
        self.assertEqual(gitstate.manifest(self.root, "main", "feature"), "none")

        self.write("media/manifest.tsv", "id\tfile\nclip-a\ta.mp4\n")
        self.commit_all("add a row")
        added = gitstate.manifest(self.root, "main", "feature")
        self.assertRegex(added, HEX64)
        self.assertEqual(added, markers.manifest_hash(["id\tfile"], ["id\tfile", "clip-a\ta.mp4"]))

        self.write("unrelated.txt", "x\n")
        self.commit_all("unrelated")
        self.assertEqual(gitstate.manifest(self.root, "main", "feature"), added)

        self.main_moves("media/manifest.tsv", "clip-b\tb.mp4\nid\tfile\n")
        self.merge_main()
        self.assertIn("clip-b", self.read("media/manifest.tsv"))
        self.assertEqual(gitstate.manifest(self.root, "main", "feature"), added)

    def test_absent_manifest_is_no_rows(self):
        self.on("feature")
        self.git("rm", "-q", "media/manifest.tsv")
        self.commit_all("drop manifest")
        self.assertEqual(gitstate.manifest(self.root, "main", "feature"), "none")


class ChangedTest(RepoTest):
    """Criterion 4."""

    def test_added_modified_deleted_and_not_the_base(self):
        self.on("feature")
        self.write("docs/new.md", "new\n")
        self.write("README.md", "hello again\n")
        os.remove(self.root / "old.txt")
        self.commit_all("work")
        self.main_moves("base-only.txt")
        self.merge_main()
        self.assertEqual(gitstate.changed(self.root, "main", "feature"), ["README.md", "docs/new.md", "old.txt"])

    def test_rename_is_removed_and_added(self):
        self.on("feature")
        self.git("mv", "notes.txt", "renamed.txt")
        self.commit_all("rename")
        self.assertEqual(gitstate.changed(self.root, "main", "feature"), ["notes.txt", "renamed.txt"])


class ContainsTest(RepoTest):
    """Criterion 5."""

    def test_contains(self):
        self.feature_commits(1)
        self.assertTrue(gitstate.contains(self.root, "feature", "main"))
        self.main_moves()
        self.assertFalse(gitstate.contains(self.root, "feature", "main"))
        self.merge_main()
        self.assertTrue(gitstate.contains(self.root, "feature", "main"))

    def test_bad_ref_raises(self):
        with self.assertRaises(GitError):
            gitstate.contains(self.root, "feature", "no-such-ref")


class WorktreeTest(RepoTest):
    """Criterion 6."""

    def branch_of(self, path):
        return gitstate.git(path, "rev-parse", "--abbrev-ref", "HEAD").strip()

    def worktrees(self):
        return gitstate.git(self.root, "worktree", "list", "--porcelain").count("worktree ")

    def test_new_branch(self):
        path = self.tmp / "wt-new"
        gitstate.add_worktree(self.root, path, self.m1, branch="task-1")
        self.assertEqual(self.branch_of(path), "task-1")
        self.assertEqual(gitstate.head(path), self.m1)
        self.assertEqual(gitstate.head(self.root, "task-1"), self.m1)
        gitstate.remove_worktree(self.root, path)
        self.assertFalse(path.exists())
        self.assertEqual(self.worktrees(), 1)

    def test_existing_branch_ignores_ref(self):
        (f1,) = self.feature_commits(1)
        self.on("main")
        path = self.tmp / "wt-feature"
        gitstate.add_worktree(self.root, path, self.m1, branch="feature")
        self.assertEqual(self.branch_of(path), "feature")
        self.assertEqual(gitstate.head(path), f1)

    def test_detached(self):
        path = self.tmp / "wt-detached"
        gitstate.add_worktree(self.root, path, "main")
        self.assertEqual(self.branch_of(path), "HEAD")
        self.assertEqual(gitstate.head(path), self.m2)

    def test_existing_path_raises_before_git_runs(self):
        for path in (self.tmp / "taken-dir", self.tmp / "taken-file"):
            if path.name.endswith("dir"):
                path.mkdir()
            else:
                path.write_text("x")
            with mock.patch.object(gitstate.subprocess, "run") as run:
                with self.assertRaises(GitError):
                    gitstate.add_worktree(self.root, path, "main", branch="task-2")
                run.assert_not_called()
        self.assertEqual(self.worktrees(), 1)


class ExportTest(RepoTest):
    """Criterion 7."""

    def snapshot(self, dest):
        out = []
        for top, dirs, files in os.walk(dest):
            dirs.sort()
            for name in sorted(dirs + files):
                full = os.path.join(top, name)
                st = os.lstat(full)
                body = Path(full).read_bytes() if stat.S_ISREG(st.st_mode) else b""
                out.append((os.path.relpath(full, dest), st.st_mode, st.st_mtime, body))
        return out

    def test_export(self):
        a, b = self.tmp / "a", self.tmp / "b"
        b.mkdir()
        gitstate.export(self.root, "main", a)
        gitstate.export(self.root, self.m2, b)
        self.assertEqual((a / "README.md").read_text(), "hello\n")
        self.assertEqual((a / "media" / "manifest.tsv").read_text(), "id\tfile\n")
        self.assertFalse((a / ".git").exists())
        self.assertEqual(stat.S_IMODE((a / "bin" / "run.sh").stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((a / "README.md").stat().st_mode), 0o644)
        snap = self.snapshot(a)
        self.assertEqual(sorted(p for p, *_ in snap),
                         ["README.md", "bin", "bin/run.sh", "media", "media/manifest.tsv", "notes.txt", "old.txt"])
        self.assertTrue(all(mtime == 0 for _, _, mtime, _ in snap))
        self.assertEqual(snap, self.snapshot(b))

    def test_non_empty_dest_raises(self):
        dest = self.tmp / "full"
        dest.mkdir()
        (dest / "x").write_text("x")
        with self.assertRaises(GitError):
            gitstate.export(self.root, "main", dest)


    def test_absolute_symlink_raises_and_leaves_no_dest(self):
        os.symlink("/etc/passwd", self.root / "lnk")
        sha = self.commit_all("absolute link")
        dest = self.tmp / "abs"
        with self.assertRaises(GitError) as cm:
            gitstate.export(self.root, sha, dest)
        self.assertEqual(cm.exception.command, "archive")
        self.assertIn(sha, str(cm.exception))
        self.assertIn("'lnk'", str(cm.exception))
        self.assertNotIn("/etc/passwd", str(cm.exception))
        self.assertIsNone(cm.exception.__cause__)
        self.assertTrue(cm.exception.__suppress_context__)
        self.assertFalse(os.path.lexists(dest))
        self.assertEqual(sorted(os.listdir(self.tmp)), ["repo"])

    def test_absolute_symlink_leaves_empty_dest_empty(self):
        os.symlink("/etc/passwd", self.root / "lnk")
        self.commit_all("absolute link")
        dest = self.tmp / "empty"
        dest.mkdir()
        with self.assertRaises(GitError):
            gitstate.export(self.root, "main", dest)
        self.assertEqual(os.listdir(dest), [])
        self.assertEqual(sorted(os.listdir(self.tmp)), ["empty", "repo"])

    def test_relative_symlink_is_kept_as_a_link(self):
        os.symlink("../README.md", self.root / "bin" / "readme")
        self.commit_all("relative link")
        dest = self.tmp / "rel"
        gitstate.export(self.root, "main", dest)
        link = dest / "bin" / "readme"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), "../README.md")
        self.assertEqual(link.read_text(), "hello\n")
        self.assertEqual(os.lstat(link).st_mtime, 0)
        self.assertEqual(sorted(os.listdir(self.tmp)), ["rel", "repo"])


class StatusTest(RepoTest):
    def test_status(self):
        self.assertEqual(gitstate.status(self.root), [])
        self.write("README.md", "changed\n")
        self.write("new/deep/file.txt", "n\n")
        self.write(".gitignore", "*.log\n")
        self.write("debug.log", "ignored\n")
        os.remove(self.root / "old.txt")
        self.git("mv", "notes.txt", "moved.txt")
        self.assertEqual(gitstate.status(self.root),
                         [".gitignore", "README.md", "moved.txt", "new/deep/file.txt", "notes.txt", "old.txt"])


class CommitTest(RepoTest):
    """Criterion 8."""

    def test_commits_exactly_the_paths(self):
        self.on("feature")
        before = gitstate.head(self.root)
        self.write("README.md", "edited\n")
        self.write("added.txt", "added\n")
        self.write("notes.txt", "kept out\n")
        self.git("add", "notes.txt")
        sha = gitstate.commit(self.root, ["README.md", "added.txt"], "role output", "writer")
        self.assertEqual(sha, gitstate.head(self.root))
        self.assertEqual(gitstate.commits(self.root, before, sha), [{"sha": sha, "parents": [before]}])
        self.assertEqual(gitstate.changed(self.root, before, sha), ["README.md", "added.txt"])
        self.assertEqual(gitstate.status(self.root), ["notes.txt"])
        self.assertEqual(self.read("notes.txt"), "kept out\n")
        ident = gitstate.git(self.root, "log", "-1", "--format=%an <%ae>|%cn <%ce>").strip()
        self.assertEqual(ident, "writer <writer@factory.invalid>|writer <writer@factory.invalid>")

    def test_deleted_path(self):
        os.remove(self.root / "old.txt")
        sha = gitstate.commit(self.root, ["old.txt"], "drop", "writer")
        self.assertIsNone(gitstate.show(self.root, sha, "old.txt"))
        self.assertEqual(gitstate.status(self.root), [])

    def test_nothing_staged(self):
        before = gitstate.head(self.root)
        self.write("README.md", "edited\n")
        self.assertIsNone(gitstate.commit(self.root, [], "empty", "writer"))
        self.assertIsNone(gitstate.commit(self.root, ["notes.txt"], "unchanged", "writer"))
        self.assertEqual(gitstate.head(self.root), before)
        self.assertEqual(gitstate.status(self.root), ["README.md"])

    def test_factory_now_makes_the_commit_reproducible(self):
        os.environ["FACTORY_NOW"] = NOW
        shas = []
        for name in ("one", "two"):
            path = self.tmp / name
            gitstate.add_worktree(self.root, path, "main")
            self.write("out.txt", "same\n", cwd=path)
            shas.append(gitstate.commit(path, ["out.txt"], "same message", "writer"))
        self.assertIsNotNone(shas[0])
        self.assertEqual(shas[0], shas[1])
        dates = gitstate.git(self.tmp / "one", "log", "-1", "--format=%at %ct").split()
        self.assertEqual(dates, ["1794391200"] * 2)


class RestoreTest(RepoTest):
    """Criterion 9."""

    def test_restore(self):
        self.write("README.md", "drop me\n")
        self.write("stray/u.txt", "drop me\n")
        self.write("notes.txt", "keep me\n")
        self.write("v.txt", "keep me\n")
        self.write("staged.txt", "drop me\n")
        self.git("add", "staged.txt")
        gitstate.restore(self.root, ["README.md", "stray/u.txt", "staged.txt"])
        self.assertEqual(self.read("README.md"), "hello\n")
        self.assertFalse((self.root / "stray" / "u.txt").exists())
        self.assertFalse((self.root / "staged.txt").exists())
        self.assertEqual(self.read("notes.txt"), "keep me\n")
        self.assertEqual(self.read("v.txt"), "keep me\n")
        self.assertEqual(gitstate.status(self.root), ["notes.txt", "v.txt"])

    def test_restore_deleted_tracked_file(self):
        os.remove(self.root / "old.txt")
        gitstate.restore(self.root, ["old.txt"])
        self.assertEqual(self.read("old.txt"), "old\n")
        self.assertEqual(gitstate.status(self.root), [])


if __name__ == "__main__":
    unittest.main()
