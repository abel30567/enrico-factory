"""Tests for kit/lib/cmd_launch.py: the command, the blind judge, the detached supervisor and the markers lint.

The world (production, recorded GitHub, fake harness) is the one of tests/test_finish.py.
"""

import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from kit.lib import blind, lint, markers  # noqa: E402
from test_finish import CRITIC, WEIGHER, WRITER, World, approved, last_line, notes, ready, result_step  # noqa: E402

FACTORY = ROOT / "kit" / "factory"


def prefers(choice="A"):
    return result_step(WEIGHER, {"marker": f"PREFERS {choice}", "summary": "A keeps the flood's cause.",
                                 "prefers": {"choice": choice,
                                             "cited": ["A/episodes/ep01/outline.md", "B/episodes/ep01/outline.md"]}})


def addressed():
    return result_step(WRITER, {"marker": "ADDRESSED", "summary": "The heron leaves at dawn now.",
                                "evidence": [{"criterion": "dawn", "result": "pass", "command": "read"}],
                                "addressed": [{"note": "B1", "by": "critic", "action": "changed",
                                               "text": "dawn"}]},
                       {"episodes/ep02/outline.md": "# Two\n\nThe heron leaves at dawn.\n"})


class BlindJudge(unittest.TestCase):
    """Criterion 3."""

    def setUp(self):
        self.w = World(self)
        self.head = self.w.head("outline/OL-3-writer-1")

    def test_with_a_key(self):
        w = self.w
        state = w.state_now()
        key = blind.prepare(w.p, state, w.root, state.pulls[5])
        pair = blind.pair_dir(w.p, 5, self.head)
        w.steps(prefers())
        code, out, err = w.launch("weigher", "5")
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual([c["cwd"] for c in w.calls()], [str(pair)])
        posted = w.by(5, "weigher")
        self.assertEqual(len(posted), 1)
        body = posted[0]["body"]
        self.assertEqual(body, "\n".join([
            "[WEIGHER] PREFERS A", "", "A keeps the flood's cause.", "",
            f"Prefers: A. Cited: A/episodes/ep01/outline.md; B/episodes/ep01/outline.md. Blinding id: {key['id']}",
            "Launch: 5-1", f"reviewed head {self.head} manifest none"]) + "\n")
        self.assertEqual(key["id"], json.loads(blind.key_path(w.p, 5, self.head).read_text())["id"])
        self.assertTrue((pair / "A").is_dir() and (pair / "B").is_dir())
        self.assertTrue(json.loads((w.run_dir("weigher-5-1") / "run.json").read_text())["blind"])

    def test_without_a_key(self):
        w = self.w
        w.steps(prefers())
        before = sorted((w.github / "comments").glob("*.json"))
        texts = [p.read_text() for p in before]
        code, out, err = w.launch("weigher", "5")
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, "launch: no blinded pair for #5; run factory blind 5\n")
        self.assertEqual([p.read_text() for p in sorted((w.github / "comments").glob("*.json"))], texts)
        self.assertFalse((w.state / "runs").exists())
        self.assertEqual(w.calls(), [])

    def test_not_blind_when_nothing_is_locked(self):
        w = self.w
        pull = json.loads((w.github / "pulls" / "2.json").read_text())
        (w.github / "pulls" / "2.json").write_text(json.dumps({**pull, "state": "closed", "merged_at": None}))
        w.steps(prefers())
        code, out, err = w.launch("weigher", "5")
        self.assertEqual((code, err), (0, ""), out)
        self.assertIn("Blinding id: none", w.by(5, "weigher")[0]["body"])
        self.assertFalse(json.loads((w.run_dir("weigher-5-1") / "run.json").read_text())["blind"])
        self.assertFalse((w.state / "worktrees" / "weigher-5-1").exists())


class Detached(unittest.TestCase):
    """Criterion 8: without --wait the command returns at once; the supervisor posts later."""

    def test_returns_before_the_worker_ends(self):
        w = World(self)
        w.steps(ready(sleep=1))
        started = time.monotonic()
        done = subprocess.run([sys.executable, str(FACTORY), "launch", "writer", "3", "--root", str(w.root)],
                              capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        returned = time.monotonic()
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "launched writer 3-1\n", ""))
        self.assertEqual(w.posted("writer"), [])
        while not w.posted("writer") and time.monotonic() < returned + 10:
            time.sleep(0.1)
        posted = w.posted("writer")
        self.assertEqual([markers.parse(c["body"]).verb for c in posted], ["READY"])
        self.assertGreaterEqual(time.monotonic() - started, 1)
        log = w.run_dir("writer-3-1") / "supervisor.log"
        deadline = time.monotonic() + 10
        while "posted" not in log.read_text() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertEqual(log.read_text(), "posted [WRITER] READY for launch 3-1\n")

    def test_environment_wait_runs_in_this_process(self):
        w = World(self)
        w.steps({"match": WRITER, "stdout": "", "exit": 1})
        with mock.patch.dict(os.environ, {"FACTORY_LAUNCH_WAIT": "1"}):
            code, out, err = w.launch("writer", "3", wait=False)
        self.assertEqual(code, 3, out + err)
        self.assertEqual(out, "launched writer 3-1\nposted [WRITER] BLOCKED for launch 3-1\n")
        self.assertFalse((w.run_dir("writer-3-1") / "supervisor.log").exists())


class Refusals(unittest.TestCase):

    def setUp(self):
        self.w = World(self)
        self.w.steps(ready())

    def assert_nothing(self):
        self.assertFalse((self.w.state / "runs").exists())
        self.assertEqual(self.w.posted("writer") + self.w.posted("keeper"), [])
        self.assertEqual(self.w.calls(), [])

    def test_gateway_on(self):
        """Criterion 10."""
        self.w.local(GATEWAY="on")
        code, out, err = self.w.launch("writer", "3")
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err.startswith("launch: "), err)
        self.assertIn("GATEWAY", err)
        self.assert_nothing()

    def test_gateway_off_twin(self):
        self.assertEqual(self.w.launch("writer", "3")[0], 0)

    def test_launch_error(self):
        code, out, err = self.w.launch("ghost", "3")
        self.assertEqual((code, out, err), (1, "", "launch: no role ghost\n"))
        self.assert_nothing()

    def test_no_production(self):
        empty = self.w.tmp / "empty"
        empty.mkdir()
        code, out, err = self.w.main(["writer", "3", "--root", str(empty), "--wait"])
        self.assertEqual((code, out, err), (2, "", "launch: no production.env at or above the start directory\n"))
        self.assert_nothing()

    def test_github_unreadable(self):
        with mock.patch.dict(os.environ, {"FACTORY_GITHUB": str(self.w.tmp / "missing")}):
            code, out, err = self.w.launch("writer", "3")
        self.assertEqual((code, out, err), (2, "", "launch: connect: FACTORY_GITHUB is not a directory\n"))
        self.assert_nothing()

    def test_supervise_unreadable_run(self):
        code, out, err = self.w.main(["--supervise", str(self.w.tmp / "no-run"), "--root", str(self.w.root)])
        self.assertEqual((code, out), (2, ""))
        self.assertTrue(err.startswith("launch: FileNotFoundError: "), err)
        self.assertNotIn(str(self.w.tmp), err)

    def test_usage(self):
        done = subprocess.run([sys.executable, str(ROOT / "kit" / "lib" / "cmd_launch.py"), "writer"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 2)
        self.assertIn("give a role and a ticket or pull request number", done.stderr)


class MarkersLint(unittest.TestCase):
    """Criterion 11: everything criteria 1, 2 and 3 posted passes the markers lint."""

    @classmethod
    def setUpClass(cls):
        cls.holder = unittest.TestCase()  # carries the world's cleanups until tearDownClass
        w = cls.w = World(cls.holder)
        w.steps(ready(), addressed(), notes(), prefers())
        codes = [w.launch("writer", "3")[0], w.launch("writer", "6")[0], w.launch("critic", "5")[0]]
        state = w.state_now()
        blind.prepare(w.p, state, w.root, state.pulls[5])
        codes.append(w.launch("weigher", "5")[0])
        cls.codes = codes
        cls.comments = [{"id": c["id"], "role": c["role"], "body": c["body"]}
                        for path in sorted((w.github / "comments").glob("*.json"), key=lambda p: int(p.stem))
                        for c in json.loads(path.read_text())]

    @classmethod
    def tearDownClass(cls):
        cls.holder.doCleanups()

    def lint(self, comments):
        tree = self.w.tmp / f"tree-{len(list(self.w.tmp.glob('tree-*')))}"
        shutil.copytree(self.w.root / ".factory", tree / ".factory")
        (tree / ".factory-state").mkdir()
        (tree / ".factory-state" / "comments.json").write_text(json.dumps(comments, indent=1) + "\n")
        return lint.run(tree, only=["markers"])["markers"]

    def test_everything_posted_passes(self):
        self.assertEqual(self.codes, [0, 0, 0, 0])
        verbs = sorted(markers.parse(c["body"]).verb for c in self.comments)
        self.assertEqual(verbs, ["ADDRESSED", "LAUNCH", "LAUNCH", "LAUNCH", "LAUNCH", "NOTES", "PREFERS A",
                                 "READY", "READY"])
        result = self.lint(self.comments)
        self.assertEqual((result.status, result.findings), (lint.PASS, []))

    def test_checker_comment_without_its_last_line_fails(self):
        seeded = []
        for c in self.comments:
            if c["role"] == "critic":
                c = {**c, "body": c["body"].rstrip("\n").rsplit("\n", 1)[0] + "\n"}
                self.assertFalse(last_line(c["body"]).startswith("reviewed head"))
            seeded.append(c)
        result = self.lint(seeded)
        self.assertEqual(result.status, lint.FAIL)
        self.assertEqual([f.rule for f in result.findings], ["marker-reviewed"])


if __name__ == "__main__":
    unittest.main()
