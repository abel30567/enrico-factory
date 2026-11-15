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

from kit.lib import cmd_init, cmd_launch, cmd_tick, gitstate, production, tickets  # noqa: E402

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
WRITE = "STEP #1 write production/ep01/shots.json"
SHOTS = "production/ep01/shots.json"
WAITS = [f"WAIT #{n} waiting upstream stage intake is not locked for {unit}"
         for n, (_, unit) in zip(range(3, 10), UNITS)]
SUBJECT_WAITS = [
    "WAIT #7 waiting cheaper stage(s) not locked: assets for ama, assets for lighthouse, assets for tobi, "
    "rigging for ama, rigging for tobi",
    "WAIT #8 waiting cheaper stage(s) not locked: assets for ama, assets for harbour, rigging for ama",
    "WAIT #9 waiting cheaper stage(s) not locked: assets for ama, assets for harbour, assets for tobi, "
    "rigging for ama, rigging for tobi"]


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

    def on_main(self, rel):
        return gitstate.show(self.root, "main", rel)

    def pushes(self):
        log = self.github / "log.jsonl"
        lines = log.read_text().splitlines() if log.is_file() else []
        return [line for line in lines if json.loads(line).get("op") == "push"]


class IntakeFiling(World):
    def test_dry_tick_prints_the_filings(self):
        code, out = self.tick()
        self.assertEqual(code, 0, out)
        self.assertEqual(out.splitlines(), [WRITE, *SEVEN, PACKET, "tick: 9 steps, 0 need, 0 wait, 0 failed"])
        self.assertEqual(self.issues(), [1])
        self.assertIsNone(self.on_main(SHOTS))
        self.assertFalse((self.root / SHOTS).exists())

    def test_apply_files_the_tickets(self):
        code, out = self.tick("--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.splitlines(), [WRITE, *SEVEN, PACKET, "tick: 9 steps, 0 need, 0 wait, 0 failed"])
        self.assertEqual(self.on_main(SHOTS), dump(SHOT_LIST))
        self.assertEqual(self.git("log", "-1", "--format=%an %s", "main").strip(),
                         "factory intake: production/ep01/shots.json from PR #2")
        self.assertEqual(self.git("status", "--short"), "")
        self.assertEqual(len(self.pushes()), 1)
        self.assertEqual(self.issues(), [1, *range(3, 11)])
        for number, (stage, unit), title in zip(range(3, 10), UNITS, TITLES):
            issue = self.record("issues", number)
            self.assertEqual((issue["state"], issue["title"]), ("open", title))
            self.assertEqual(issue["labels"], ["room:production", f"stage:{stage}", "tracked", f"unit:{unit}"])
            self.assertTrue(issue["body"].startswith(f"Do the stage {stage} for unit {unit}.\n\nTool: "), issue["body"])
            self.assertTrue(issue["body"].endswith("\n\nUpstream: #1 reel ep01, locked by PR #2.\n"
                                                   "Filed by factory tick.\n"), issue["body"])
        self.assertEqual(self.record("issues", 10)["title"], "[INTENT-EP01] story intent packet for ep01")
        text = "".join(json.dumps(self.record("issues", n)) for n in self.issues())
        for name in ("mira", "tide-flats", "Q2-2"):
            self.assertNotIn(name, text)

    def test_never_twice(self):
        self.tick("--apply")
        commits = self.git("rev-list", "--count", "main")
        code, out = self.tick()
        self.assertEqual(code, 0, out)
        launches = [f"STEP #{n} launch asset-builder on #{n}" for n in range(3, 7)]
        self.assertEqual(out.splitlines(), [*launches, *SUBJECT_WAITS, "tick: 4 steps, 0 need, 3 wait, 0 failed"])
        for n in range(3, 10):
            issue = self.record("issues", n)
            self.set_record("issues", n, {**issue, "labels": sorted([*issue["labels"], "hold"])})
        code, out = self.tick("--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.issues(), [1, *range(3, 11)])
        held = [f"WAIT #{n} held ticket #{n} has the label hold" for n in range(3, 10)]
        self.assertEqual(out.splitlines(), [*held, "tick: 0 steps, 0 need, 7 wait, 0 failed"])
        self.assertEqual(self.git("rev-list", "--count", "main"), commits)
        self.assertEqual(len(self.pushes()), 1)

    def test_shot_list_not_on_the_base(self):
        self.tick("--apply")
        self.commit(removed=[SHOTS])
        code, out = self.tick()
        self.assertEqual(code, 0, out)
        self.assertEqual(out.splitlines(), [WRITE, *WAITS, "tick: 1 steps, 0 need, 7 wait, 0 failed"])

    def test_upstream_held(self):
        self.tick("--apply")

        def upstream():
            p = production.load(self.root)
            state = tickets.load(p, cmd_tick.github.connect(p.settings), self.root)
            return [tickets.upstream(p, state, state.tickets[n], self.root) for n in (3, 7)]
        self.assertEqual(upstream(), [
            (True, "upstream stage intake is held for ama: production/ep01/shots.json is on the base"),
            (False, "cheaper stage(s) not locked: assets for ama, assets for lighthouse, assets for tobi, "
                    "rigging for ama, rigging for tobi")])
        self.set_record("pulls", 2, {**self.record("pulls", 2), "state": "closed", "merged_at": None})
        self.assertIsNotNone(self.on_main(SHOTS))
        self.assertEqual(upstream(), [(False, "upstream stage intake is not locked for ama"),
                                      (False, "upstream stage intake is not locked for ep01/Q1-1")])

    def lock(self, issue, pull, stage, unit):
        """Lock ``production/<stage>`` of ``unit``: a closed ticket ``issue`` when none exists, and its merged pull."""
        key = f"{stage}-{unit.replace('/', '-')}".upper()
        title = f"[{key}] {stage} for {unit}"
        if not (self.github / "issues" / f"{issue}.json").is_file():
            self.issue(issue, title, ["room:production", f"stage:{stage}", "tracked", f"unit:{unit}"],
                       state="closed")
        self.set_record("pulls", pull, {**self.record("pulls", 2), "number": pull, "ticket": issue,
                                        "body": f"Ticket: #{issue}\n", "head_ref": f"{stage}/{key}-maker-1",
                                        "title": title})

    def shot_lines(self, out):
        return [line for line in out.splitlines() if line.split()[1:2] in (["#7"], ["#8"], ["#9"])]

    def launch(self, number):
        """``(exit code, stderr)`` of ``factory launch layout-artist <number> --root <show>``."""
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = cmd_launch.main(["layout-artist", str(number), "--root", str(self.root)])
        return code, err.getvalue()

    def test_layout_waits_for_the_assets(self):
        self.tick("--apply")
        for issue, pull, unit in ((3, 11, "ama"), (4, 12, "harbour"), (5, 13, "lighthouse")):
            self.lock(issue, pull, "assets", unit)
        self.lock(15, 16, "rigging", "ama")
        self.assertEqual(self.shot_lines(self.tick()[1]), [
            "WAIT #7 waiting cheaper stage(s) not locked: assets for tobi, rigging for tobi",
            "STEP #8 launch layout-artist on #8",
            "WAIT #9 waiting cheaper stage(s) not locked: assets for tobi, rigging for tobi"])
        self.assertEqual(self.launch(7),
                         (1, "launch: cheaper stage(s) not locked: assets for tobi, rigging for tobi\n"))

    def test_layout_launches_when_the_assets_lock(self):
        self.tick("--apply")
        for issue, pull, unit in ((3, 11, "ama"), (4, 12, "harbour"), (5, 13, "lighthouse")):
            self.lock(issue, pull, "assets", unit)
        self.lock(15, 16, "rigging", "ama")
        self.lock(6, 14, "assets", "tobi")
        self.lock(17, 18, "rigging", "tobi")
        self.assertEqual(self.shot_lines(self.tick()[1]),
                         [f"STEP #{n} launch layout-artist on #{n}" for n in (7, 8, 9)])
        self.assertNotIn("cheaper stage(s)", self.launch(7)[1])

    def test_animation_waits_too(self):
        self.tick("--apply")
        self.lock(8, 12, "layout", "ep01/Q1-2")
        self.issue(13, "[ANIMATION-EP01-Q1-2] animation for ep01/Q1-2",
                   ["room:production", "stage:animation", "tracked", "unit:ep01/Q1-2"])

        def lines():
            return [line for line in self.tick()[1].splitlines() if line.split()[1:2] == ["#13"]]
        self.assertEqual(lines(), ["WAIT #13 waiting cheaper stage(s) not locked: assets for ama, "
                                   "assets for harbour, rigging for ama"])
        self.lock(3, 14, "assets", "ama")
        self.lock(4, 15, "assets", "harbour")
        self.lock(16, 17, "rigging", "ama")
        self.assertEqual(lines(), ["STEP #13 launch animator on #13"])

    def test_a_shot_with_no_subject(self):
        rel = "episodes/ep01/sequences/Q1/shots/Q1-2.json"
        for change in ({"files": {rel: dump({"id": "Q1-2", "characters": []})}}, {"removed": [rel]}):
            with self.subTest(change=change):
                self.make(["--rooms", "story,production"])
                self.commit(**change)
                self.tick("--apply")
                self.assertEqual(self.shot_lines(self.tick()[1]),
                                 [SUBJECT_WAITS[0], "STEP #8 launch layout-artist on #8", SUBJECT_WAITS[2]])

    def test_write_refused(self):
        self.git("checkout", "-q", "-b", "elsewhere")
        code, out = self.tick("--apply")
        self.assertEqual(code, 1, out)
        self.assertEqual(out.splitlines()[0], "FAIL #1 write: the checkout is not on main with a clean index")
        self.assertEqual(file_lines(out), SEVEN)
        self.assertIsNone(self.on_main(SHOTS))
        self.git("checkout", "-q", "main")
        self.write({SHOTS: dump(SHOT_LIST)})
        code, out = self.tick("--apply")
        self.assertEqual(code, 1, out)
        self.assertEqual(out.splitlines()[0],
                         "FAIL #1 write: production/ep01/shots.json is in the checkout and not on main")
        self.assertIsNone(self.on_main(SHOTS))

    def test_edit_list_not_whole_frames(self):
        edit_list = json.loads((self.root / EDIT_LIST).read_text())
        edit_list["events"][1]["start"] = 2.51
        self.commit({EDIT_LIST: dump(edit_list)})
        code, out = self.tick()
        self.assertEqual(code, 0, out)
        lines = out.splitlines()
        self.assertEqual(lines[0], "NEED #1 intake ep01: episodes/ep01/reel/edit-list.json: event 2: start 2.51 s "
                                   "at 24 fps is not a whole frame")
        self.assertEqual(lines[1:8], SEVEN)
        self.assertEqual(lines[-1], "tick: 8 steps, 1 need, 0 wait, 0 failed")

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
        self.assertEqual(lines[:2], [WRITE, "STEP #3 write production/ep02/shots.json"])
        self.assertEqual(lines[11:13], [PACKET, "STEP #3 file intent-packet ep02"])

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
        self.assertNotIn(" write ", out)
        out = self.tick("--room", "production")[1]
        self.assertEqual((file_lines(out), packet_lines(out)), (SEVEN, []))
        self.assertEqual(out.splitlines()[0], WRITE)
        self.make([])
        code, out = self.tick()
        self.assertEqual(code, 0, out)
        self.assertEqual((file_lines(out), packet_lines(out)), ([], [PACKET]))

    def test_unreadable_inputs(self):
        edit_list = (self.root / EDIT_LIST).read_text()
        for change, reason in (({"removed": [EDIT_LIST]}, "is not on the base"),
                               ({"files": {EDIT_LIST: "{\n"}}, "is not JSON")):
            with self.subTest(change=change):
                self.commit(**change)
                code, out = self.tick()
                self.assertEqual(code, 0, out)
                self.assertEqual(out.splitlines(), [f"NEED #1 intake ep01: {EDIT_LIST} {reason}", PACKET,
                                                    "tick: 1 steps, 1 need, 0 wait, 0 failed"])
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
