"""Tests for kit/lib/cmd_lock.py: `factory lock` reports, and with --apply locks one pull request.

The world is test_lockrules.World: the fixture production under tests/fixtures/lock/ in a temporary git
repository, and a Recorded GitHub. Each test keeps only the open pulls it reads.
"""

import contextlib
import io
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lockrules import FIX, ROOT, World  # noqa: E402

from kit.lib import cmd_lock, github, lint  # noqa: E402
from kit.lib.github import GitHubError  # noqa: E402


def snapshot(w):
    """Every byte of the recorded GitHub, and every ref of the repository."""
    files = {str(path.relative_to(w.dir)): path.read_bytes() for path in sorted(w.dir.rglob("*")) if path.is_file()}
    return files, w.git("rev-parse", "--all")


class FailingMerge(github.Recorded):
    def merge(self, root, number, head_sha):
        raise GitHubError(f"merge #{number}: refused")


class CountingSync(github.Recorded):
    """A recorded GitHub whose ``sync_base`` records each base it is called with, and fails from call
    ``fail_from`` on (never when ``None``)."""

    def __init__(self, directory, fail_from=None):
        super().__init__(directory)
        self.fail_from = fail_from
        self.synced = []

    def sync_base(self, root, base_ref):
        self.synced.append(base_ref)
        if self.fail_from is not None and len(self.synced) >= self.fail_from:
            raise GitHubError(f"sync_base {base_ref}: git fetch failed (exit 128)")
        return super().sync_base(root, base_ref)


class FailingMergeCountingSync(FailingMerge, CountingSync):
    pass


class ApplyTest(unittest.TestCase):
    def apply(self, w, number, *argv):
        """Run --apply; return the stdout, the comments it posted on ``number`` and the merge commit."""
        before = w.comments(number)
        code, out, err = w.lock("--apply", *argv)
        self.assertEqual((code, err), (0, ""))
        posted = w.comments(number)[len(before):]
        self.assertEqual(w.comments(number)[:len(before)], before)
        return out, posted

    def test_apply_on_a_clean_pull(self):
        """Criterion 6."""
        w = World(self, (8,))
        p, state = w.load()
        git = state.pulls[8]["git"]
        out, posted = self.apply(w, 8)
        self.assertEqual(len(posted), 1)
        lock = posted[0]
        self.assertEqual(lock["role"], "program:lock")
        lines = lock["body"].splitlines()
        self.assertEqual(lines[0], "[CHIEF] LOCK")
        self.assertEqual(lines[1], "")
        self.assertEqual(lines[2], f"locked head {git['head']} manifest {git['manifest']}")
        self.assertEqual(lines[3], "relied on: lint #807; editor APPROVED #808")
        self.assertEqual(w.record("pulls", 8)["state"], "merged")
        self.assertEqual(w.record("issues", 7)["state"], "closed")
        trunk = w.git("rev-parse", "trunk").strip()
        self.assertEqual(w.git("rev-parse", "trunk^2").strip(), git["head"])
        self.assertRegex(out, r"\nlocked PR #8 at [0-9a-f]{12}\n$")
        self.assertTrue(out.endswith(f"locked PR #8 at {trunk[:12]}\n"), out)
        self.assertTrue(out.startswith("PR #8 #7 story/script ep01: LOCKABLE\n"), out)
        self.assertEqual([entry["op"] for entry in map(json.loads, w.log_bytes().decode().splitlines())],
                         ["post", "merge", "close_issue"])
        self.markers_lint(w, 8, posted)

    def test_relied_on_names_judges_their_side_and_the_gate(self):
        for number, relied in (
                (19, "relied on: lint #1902; editor APPROVED #1903; critic PREFERS A #1904 (new); "
                     "elder PREFERS A #1905 (new); viewer PREFERS B #1906 (old)"),
                (21, "relied on: lint #2102; editor APPROVED #2103; gate #2104"),
                (10, "relied on: lint #1004; canon-reviewer APPROVED #1005; braintrust VOTE #1007"),
                (6, "relied on: inspector APPROVED #602")):
            with self.subTest(pull=number):
                w = World(self, (number,))
                _, posted = self.apply(w, number)
                self.assertEqual(posted[0]["body"].splitlines()[3], relied)
                self.assertEqual(w.record("pulls", number)["state"], "merged")

    def test_without_apply_nothing_is_written(self):
        """Criterion 6: without --apply GitHub and the repository are byte-identical."""
        w = World(self, (8,))
        w.gh.add_label(7, "seen")  # so log.jsonl exists before the run
        before = snapshot(w)
        code, out, err = w.lock()
        self.assertEqual((code, out, err), (0, "PR #8 #7 story/script ep01: LOCKABLE\n", ""))
        self.assertEqual(snapshot(w), before)
        self.assertEqual(w.record("pulls", 8)["state"], "open")

    def test_two_rooms_one_merge(self):
        """Criterion 7: two lockable pulls in two rooms, one run merges only the lower number."""
        w = World(self, (6, 8))
        code, out, err = w.lock("--apply")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("PR #6 #5 production/frames ep01: LOCKABLE\nPR #8 #7 story/script ep01: LOCKABLE\n", out)
        self.assertRegex(out, r"\nlocked PR #6 at [0-9a-f]{12}\n$")
        self.assertEqual((w.record("pulls", 6)["state"], w.record("issues", 5)["state"]), ("merged", "closed"))
        self.assertEqual((w.record("pulls", 8)["state"], w.record("issues", 7)["state"]), ("open", "open"))
        self.assertEqual(w.comments(6)[-1]["body"].splitlines()[0], "[FOREMAN] LOCK")
        self.assertFalse(any("LOCK" in c["body"] for c in w.comments(8)))

    def test_room_filter(self):
        w = World(self, (6, 8))
        code, out, err = w.lock("--room", "story", "--apply")
        self.assertEqual((code, err), (0, ""))
        self.assertNotIn("PR #6 ", out)
        self.assertRegex(out, r"\nlocked PR #8 at [0-9a-f]{12}\n$")
        self.assertEqual(w.record("pulls", 6)["state"], "open")

    def test_unknown_room(self):
        w = World(self, (8,))
        before = snapshot(w)
        self.assertEqual(w.lock("--room", "attic", "--apply"), (2, "", "lock: no room attic\n"))
        self.assertEqual(snapshot(w), before)

    def test_nothing_to_lock(self):
        w = World(self, (17,))
        before = snapshot(w)
        code, out, err = w.lock("--apply")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, "PR #17 #15 story/premise ep02: NO 1 rule(s)\n"
                              "  rule 5 selected: ticket #15 selected PR #16\nnothing to lock\n")
        self.assertEqual(snapshot(w), before)

    def test_merge_fails(self):
        """Criterion 8: exit 3, pull and issue open, the void note after the LOCK."""
        w = World(self, (8,))
        trunk = w.git("rev-parse", "trunk")
        failing = FailingMerge(w.dir)
        with mock.patch.object(github, "connect", return_value=failing):
            code, out, err = w.lock("--apply")
        self.assertEqual((code, err), (3, "lock: merge failed\n"))
        self.assertTrue(out.startswith("PR #8 #7 story/script ep01: LOCKABLE\n"), out)
        self.assertNotIn("locked PR", out)
        self.assertEqual(w.record("pulls", 8)["state"], "open")
        self.assertEqual(w.record("issues", 7)["state"], "open")
        posted = w.comments(8)[-2:]
        self.assertEqual(posted[0]["body"].splitlines()[0], "[CHIEF] LOCK")
        self.assertEqual(posted[1]["body"], "lock: merge failed, the LOCK above is void\n")
        self.assertEqual([c["role"] for c in posted], ["program:lock", "program:lock"])
        self.assertLess((posted[0]["created_at"], posted[0]["id"]), (posted[1]["created_at"], posted[1]["id"]))
        self.assertEqual(w.git("rev-parse", "trunk"), trunk)
        self.markers_lint(w, 8, posted)

    def markers_lint(self, w, number, posted):
        """Criterion 10: the markers lint accepts every comment on the pull, and refuses the LOCK as the
        orchestrator's own role."""
        tree = w.root.parent / "tree"
        shutil.rmtree(tree, ignore_errors=True)
        shutil.copytree(FIX / "production" / ".factory", tree / ".factory")
        (tree / ".factory-state").mkdir()
        comments = [{"id": c["id"], "role": c["role"], "body": c["body"]} for c in w.comments(number)]
        path = tree / ".factory-state" / "comments.json"
        path.write_text(json.dumps(comments, indent=1) + "\n")
        result = lint.run(tree, only=["markers"])["markers"]
        self.assertEqual((result.status, result.findings, result.reason), ("PASS", [], ""))
        lock_id = posted[0]["id"]
        seeded = [{**c, "role": "chief"} if c["id"] == lock_id else c for c in comments]
        path.write_text(json.dumps(seeded, indent=1) + "\n")
        result = lint.run(tree, only=["markers"])["markers"]
        self.assertEqual(result.status, "FAIL")
        self.assertEqual([f.rule for f in result.findings], ["marker-verb"])
        self.assertIn(f"comment {lock_id}:", result.findings[0].message)


class SyncTest(unittest.TestCase):
    """ISSUE-227: ``--apply`` brings the local base up to GitHub's before it reads and after it merges."""

    def run_with(self, w, gh, *argv):
        with mock.patch.object(github, "connect", return_value=gh):
            return w.lock(*argv)

    def test_a_sync_that_fails_after_the_merge_is_not_a_void_lock(self):
        w = World(self, (8,))
        gh = CountingSync(w.dir, fail_from=2)
        code, out, err = self.run_with(w, gh, "--apply")
        trunk = w.git("rev-parse", "trunk").strip()[:12]
        self.assertEqual((code, err), (4, f"lock: PR #8 is merged at {trunk}, base not synced: "
                                          "sync_base trunk: git fetch failed (exit 128)\n"))
        self.assertTrue(out.endswith(f"locked PR #8 at {trunk}\n"), out)
        self.assertEqual((w.record("pulls", 8)["state"], w.record("issues", 7)["state"]), ("merged", "closed"))
        locks = [c for c in w.comments(8) if c["role"] == cmd_lock.ROLE]
        self.assertEqual([c["body"].splitlines()[0] for c in locks], ["[CHIEF] LOCK"])
        self.assertFalse([c for c in w.comments(8) if c["body"].rstrip() == cmd_lock.VOID])
        self.assertEqual(gh.synced, ["trunk", "trunk"])

    def test_a_sync_that_succeeds_syncs_before_and_after(self):
        w = World(self, (8,))
        gh = CountingSync(w.dir)
        code, out, err = self.run_with(w, gh, "--apply")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.endswith(f"locked PR #8 at {w.git('rev-parse', 'trunk').strip()[:12]}\n"), out)
        self.assertEqual(gh.synced, ["trunk", "trunk"])

    def test_a_sync_that_fails_before_the_read_writes_nothing(self):
        w = World(self, (8,))
        gh = CountingSync(w.dir, fail_from=1)
        before = snapshot(w)
        self.assertEqual(self.run_with(w, gh, "--apply"),
                         (2, "", "lock: sync_base trunk: git fetch failed (exit 128)\n"))
        self.assertEqual(snapshot(w), before)
        self.assertEqual(gh.synced, ["trunk"])

    def test_without_apply_nothing_is_synced(self):
        w = World(self, (8,))
        gh = CountingSync(w.dir, fail_from=1)
        self.assertEqual(self.run_with(w, gh), (0, "PR #8 #7 story/script ep01: LOCKABLE\n", ""))
        self.assertEqual(gh.synced, [])

    def test_a_merge_github_refuses_stays_void_and_is_not_synced(self):
        w = World(self, (8,))
        gh = FailingMergeCountingSync(w.dir)
        code, out, err = self.run_with(w, gh, "--apply")
        self.assertEqual((code, err), (3, "lock: merge failed\n"))
        self.assertEqual(w.comments(8)[-1]["body"].rstrip(), cmd_lock.VOID)
        self.assertEqual((w.record("pulls", 8)["state"], w.record("issues", 7)["state"]), ("open", "open"))
        self.assertEqual(gh.synced, ["trunk"])


class FrozenTest(unittest.TestCase):
    """Criterion 9."""

    def freeze(self, w):
        w.gh.create_issue("Freeze", "Hold every lock.", ["factory:freeze"])

    def test_frozen_with_apply(self):
        w = World(self, (8,))
        self.freeze(w)
        before = snapshot(w)
        self.assertEqual(w.lock("--apply"), (2, "", "lock: frozen\n"))
        self.assertEqual(snapshot(w), before)

    def test_frozen_without_apply_reports(self):
        w = World(self, (8,))
        self.freeze(w)
        before = snapshot(w)
        self.assertEqual(w.lock(), (0, "PR #8 #7 story/script ep01: LOCKABLE\n", ""))
        self.assertEqual(snapshot(w), before)


class JsonTest(unittest.TestCase):
    def test_json(self):
        """Criterion 11."""
        w = World(self, (6, 8, 17))
        code, first, err = w.lock("--json")
        self.assertEqual((code, err), (0, ""))
        found = json.loads(first)
        self.assertEqual([(o["pull"], o["ticket"], o["lockable"]) for o in found],
                         [(6, 5, True), (8, 7, True), (17, 15, False)])
        for o in found:
            self.assertEqual(sorted(o), ["lockable", "pull", "rules", "ticket"])
            self.assertEqual([r["number"] for r in o["rules"]], list(range(1, 13)))
            self.assertTrue(all(sorted(r) == ["name", "number", "ok", "reason"] for r in o["rules"]))
        self.assertEqual([r for r in found[2]["rules"] if not r["ok"]],
                         [{"name": "selected", "number": 5, "ok": False, "reason": "ticket #15 selected PR #16"}])
        self.assertEqual(w.lock("--json"), (0, first, ""))
        self.assertEqual(first, json.dumps(found, indent=2, sort_keys=True) + "\n")


class ErrorTest(unittest.TestCase):
    def test_no_production(self):
        w = World(self, ())
        empty = w.root.parent / "empty"
        empty.mkdir()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_lock.main(["--root", str(empty)])
        self.assertEqual((code, out.getvalue()), (2, ""))
        self.assertEqual(err.getvalue(), "lock: no production.env at or above the start directory\n")

    def test_usage(self):
        w = World(self, ())
        with self.assertRaises(SystemExit) as raised, mock.patch("sys.stderr"):
            cmd_lock.main(["--colour"])
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(w.log_bytes(), None)


class EntryPointTest(unittest.TestCase):
    def test_factory_lock_runs(self):
        w = World(self, (8,))
        for argv in (["kit/factory", "lock"], ["kit/lib/cmd_lock.py"]):
            with self.subTest(argv=argv):
                done = subprocess.run([sys.executable, *argv, "--root", str(w.root)],
                                      cwd=ROOT, capture_output=True, text=True)
                self.assertEqual((done.returncode, done.stdout, done.stderr),
                                 (0, "PR #8 #7 story/script ep01: LOCKABLE\n", ""))
        self.assertIsNone(w.log_bytes())


if __name__ == "__main__":
    unittest.main()
