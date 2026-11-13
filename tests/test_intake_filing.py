"""Tests for the intake filing of kit/lib/filing.py: a locked story package files the production tickets.

Each test builds the world of the ticket: a show made by ``factory init --rooms story,production`` on branch
``main``, the fixture ``tests/fixtures/intake_filing/production/`` committed over it, and a recorded GitHub
copied from ``tests/fixtures/intake_filing/github/`` holding the closed reel ticket #1 of ``ep01`` and its
merged pull #2. Every tick runs ``cmd_tick.main`` in process; no step launches a role.
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import cmd_init, cmd_tick, gitstate  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "intake_filing"
NOW = "2026-11-13T09:00:00Z"
EDIT_LIST = "episodes/ep01/reel/edit-list.json"
SHOT = "episodes/ep01/sequences/Q1/shots/Q1-1.json"
SEVEN = ["STEP #1 file assets ama", "STEP #1 file assets harbour", "STEP #1 file assets lighthouse",
         "STEP #1 file assets tobi", "STEP #1 file layout ep01/Q1-1", "STEP #1 file layout ep01/Q1-2",
         "STEP #1 file layout ep01/Q2-1"]
UNITS = [("assets", "ama"), ("assets", "harbour"), ("assets", "lighthouse"), ("assets", "tobi"),
         ("layout", "ep01/Q1-1"), ("layout", "ep01/Q1-2"), ("layout", "ep01/Q2-1")]
TITLES = ["[ASSETS-AMA] assets for ama", "[ASSETS-HARBOUR] assets for harbour",
          "[ASSETS-LIGHTHOUSE] assets for lighthouse", "[ASSETS-TOBI] assets for tobi",
          "[LAYOUT-EP01-Q1-1] layout for ep01/Q1-1", "[LAYOUT-EP01-Q1-2] layout for ep01/Q1-2",
          "[LAYOUT-EP01-Q2-1] layout for ep01/Q2-1"]
PACKET = "STEP #1 file intent-packet ep01"
SHOT_LIST = [{"shot": "Q1-1", "first": 0, "last": 59}, {"shot": "Q1-2", "first": 60, "last": 131},
             {"shot": "Q2-1", "first": 132, "last": 233}]


def file_lines(out):
    return [line for line in out.splitlines()
            if line.startswith("STEP #") and " file " in line and " file intent-packet " not in line]


def packet_lines(out):
    return [line for line in out.splitlines() if line.startswith("STEP #") and " file intent-packet " in line]


def dump(value):
    return json.dumps(value, indent=1) + "\n"


class World(unittest.TestCase):
    """The world of ruling 9."""

    def setUp(self):
        self.make(["--rooms", "story,production"])

    def make(self, rooms):
        """A new world in a new temporary directory, its show made by ``factory init <show> *rooms``."""
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.github = self.tmp / "github"
        env = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "FACTORY_NOW": NOW,
               "FACTORY_GITHUB": str(self.github)}
        patch = mock.patch.dict(os.environ, env)
        patch.start()
        self.addCleanup(patch.stop)
        self.root = self.tmp / "show"
        self.root.mkdir()
        self.git("init", "-q", "-b", "main")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cmd_init.main([str(self.root), *rooms]), 0)
        (self.root / "production.local.env").write_text(f"STATE_DIR={self.tmp / 'state'}\n")
        shutil.copytree(FIX / "production", self.root, dirs_exist_ok=True)
        self.commit()
        shutil.copytree(FIX / "github", self.github)

    # -- git

    def git(self, *args):
        return gitstate.git(self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", *args)

    def write(self, files):
        for rel, text in files.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    def commit(self, files=None, removed=()):
        self.write(files or {})
        for rel in removed:
            (self.root / rel).unlink()
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "files")

    # -- the recorded GitHub

    def record(self, kind, number):
        return json.loads((self.github / kind / f"{number}.json").read_text())

    def set_record(self, kind, number, value):
        (self.github / kind / f"{number}.json").write_text(json.dumps(value, indent=1, sort_keys=True) + "\n")

    def issues(self):
        return sorted(int(path.stem) for path in (self.github / "issues").glob("*.json"))

    def issue(self, number, title, labels, state="open"):
        self.set_record("issues", number, {"body": "A ticket.\n", "created_at": "2026-11-10T11:00:00Z",
                                           "labels": labels, "number": number, "state": state, "title": title})

    # -- the tick

    def tick(self, *args):
        """``(exit code, stdout)`` of ``factory tick --root <show> *args``."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cmd_tick.main(["--root", str(self.root), *args])
        return code, out.getvalue()


class IntakeFiling(World):
    def test_dry_tick_prints_the_filings(self):
        code, out = self.tick()
        self.assertEqual(code, 0, out)
        self.assertEqual(out.splitlines(), [*SEVEN, PACKET, "tick: 8 steps, 0 need, 0 wait, 0 failed"])
        self.assertEqual(self.issues(), [1])

    def test_apply_files_the_tickets(self):
        code, out = self.tick("--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.splitlines(), [*SEVEN, PACKET, "tick: 8 steps, 0 need, 0 wait, 0 failed"])
        self.assertEqual(self.issues(), [1, *range(3, 11)])
        for number, (stage, unit), title in zip(range(3, 10), UNITS, TITLES):
            issue = self.record("issues", number)
            self.assertEqual((issue["state"], issue["title"]), ("open", title))
            self.assertEqual(issue["labels"], ["room:production", f"stage:{stage}", "tracked", f"unit:{unit}"])
            self.assertEqual(issue["body"], f"Do the stage {stage} for unit {unit}.\n\n"
                                            "Upstream: #1 reel ep01, locked by PR #2.\nFiled by factory tick.\n")
        self.assertEqual(self.record("issues", 10)["title"], "[INTENT-EP01] story intent packet for ep01")
        text = "".join(json.dumps(self.record("issues", n)) for n in self.issues())
        for name in ("mira", "tide-flats", "Q2-2"):
            self.assertNotIn(name, text)

    def test_never_twice(self):
        self.tick("--apply")
        code, out = self.tick("--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.issues(), [1, *range(3, 11)])
        waits = [f"WAIT #{n} waiting upstream stage intake is not locked for {unit}"
                 for n, (_, unit) in zip(range(3, 10), UNITS)]
        self.assertEqual(out.splitlines(), [*waits, "tick: 0 steps, 0 need, 7 wait, 0 failed"])

    def test_reel_not_locked(self):
        self.set_record("pulls", 2, {**self.record("pulls", 2), "state": "closed", "merged_at": None})
        code, out = self.tick()
        self.assertEqual(code, 0, out)
        self.assertEqual(out.splitlines(), ["tick: 0 steps, 0 need, 0 wait, 0 failed"])

    def test_base_only(self):
        edit_list = json.loads((self.root / EDIT_LIST).read_text())
        edit_list["events"].append({"shot": "Q2-2", "beat": "B3", "start": 9.75, "duration": 2})
        self.write({EDIT_LIST: dump(edit_list)})
        self.assertEqual(file_lines(self.tick()[1]), SEVEN)
        self.commit()
        self.assertEqual(file_lines(self.tick()[1]), [
            "STEP #1 file assets ama", "STEP #1 file assets harbour", "STEP #1 file assets lighthouse",
            "STEP #1 file assets mira", "STEP #1 file assets tide-flats", "STEP #1 file assets tobi",
            "STEP #1 file layout ep01/Q1-1", "STEP #1 file layout ep01/Q1-2", "STEP #1 file layout ep01/Q2-1",
            "STEP #1 file layout ep01/Q2-2"])

    def test_two_episodes(self):
        self.commit({"episodes/ep02/reel/edit-list.json": dump(
            {"fps": 24, "bible": "A-1", "events": [{"shot": "Q1-1", "beat": "B1", "start": 0, "duration": 2}]}),
            "episodes/ep02/sequences/Q1/shots/Q1-1.json": dump(
                {"id": "Q1-1", "scene": "S1", "beat": "B1", "duration": 2, "characters": ["ama", "nell"],
                 "set": "harbour", "camera": "wide", "action": "They meet."})})
        self.issue(3, "[REEL-EP02] reel for ep02", ["room:story", "stage:reel", "tracked", "unit:ep02"],
                   state="closed")
        self.set_record("pulls", 4, {**self.record("pulls", 2), "number": 4, "ticket": 3, "body": "Ticket: #3\n",
                                     "head_ref": "reel/REEL-EP02-editorial-1",
                                     "title": "[REEL-EP02] reel for ep02"})
        code, out = self.tick()
        self.assertEqual(code, 0, out)
        self.assertEqual(file_lines(out), [*SEVEN, "STEP #3 file assets nell", "STEP #3 file layout ep02/Q1-1"])
        lines = out.splitlines()
        self.assertEqual(lines[9:11], [PACKET, "STEP #3 file intent-packet ep02"])

    def test_existing_ticket(self):
        labels = ["room:production", "stage:assets", "tracked", "unit:ama"]
        self.issue(3, "[ASSETS-AMA] assets for ama", labels)
        self.assertEqual(file_lines(self.tick()[1]), SEVEN[1:])
        self.issue(3, "[ASSETS-AMA] assets for ama", labels, state="closed")
        self.assertEqual(file_lines(self.tick()[1]), SEVEN[1:])
        self.issue(3, "[ASSETS-AMA] assets for ama", ["room:production", "stage:rigging", "tracked", "unit:ama"])
        self.assertEqual(file_lines(self.tick()[1]), SEVEN)

    def test_rooms(self):
        out = self.tick("--room", "story")[1]
        self.assertEqual((file_lines(out), packet_lines(out)), ([], [PACKET]))
        out = self.tick("--room", "production")[1]
        self.assertEqual((file_lines(out), packet_lines(out)), (SEVEN, []))
        self.make([])
        code, out = self.tick()
        self.assertEqual(code, 0, out)
        self.assertEqual((file_lines(out), packet_lines(out)), ([], [PACKET]))

    def test_unreadable_inputs(self):
        edit_list = (self.root / EDIT_LIST).read_text()
        for change in ({"removed": [EDIT_LIST]}, {"files": {EDIT_LIST: "{\n"}}):
            with self.subTest(change=change):
                self.commit(**change)
                code, out = self.tick()
                self.assertEqual(code, 0, out)
                self.assertEqual(out.splitlines(), [PACKET, "tick: 1 steps, 0 need, 0 wait, 0 failed"])
        events = json.loads(edit_list)
        events["events"][1]["shot"] = "Q1/2"
        events["events"][2]["take"] = events["events"][2].pop("shot")
        self.commit({EDIT_LIST: dump(events)})
        self.assertEqual(file_lines(self.tick()[1]), [
            "STEP #1 file assets ama", "STEP #1 file assets lighthouse", "STEP #1 file assets tobi",
            "STEP #1 file layout ep01/Q1-1"])
        self.commit({EDIT_LIST: edit_list,
                     SHOT: dump({"id": "Q1-1", "characters": ["ama tobi", 7, "tobi"], "set": None})})
        less = [line for line in SEVEN if line != "STEP #1 file assets lighthouse"]
        self.assertEqual(file_lines(self.tick()[1]), less)
        self.commit(removed=[SHOT])
        self.assertEqual(file_lines(self.tick()[1]), less)

    def test_lint_intake(self):
        self.assertEqual(self.tick("--apply")[0], 0)
        self.assertEqual(self.lint(), (0, True))
        self.write({"production/ep01/shots.json": json.dumps(SHOT_LIST) + "\n"})
        self.assertEqual(self.lint(), (0, True))
        rows = [dict(row) for row in SHOT_LIST]
        rows[1]["last"] = 143
        self.write({"production/ep01/shots.json": json.dumps(rows) + "\n"})
        proc = self.run_lint()
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("B1 intake production/ep01/shots.json:1 row 2 (Q1-2): frames 60-143, the edit list gives "
                      "60-131", proc.stdout)

    def run_lint(self):
        return subprocess.run([sys.executable, str(self.root / ".factory" / "factory"), "lint", "--root",
                               str(self.root), "--only", "intake"], capture_output=True, text=True, check=False)

    def lint(self):
        proc = self.run_lint()
        return proc.returncode, "intake PASS" in proc.stdout.splitlines()


if __name__ == "__main__":
    unittest.main()
