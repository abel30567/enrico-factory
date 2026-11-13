"""Tests for the production stages of the lock: section 9 rule 4 across shots, sequences and episodes.

The production is made by ``factory init --rooms story,production`` in a temporary directory, with
tests/fixtures/lock_production/production/ copied over it (``ep01/shots.json``: shots Q1-1, Q1-2 and
Q2-1), committed on ``main``. GitHub is a copy of tests/fixtures/lock_production/github, and each open
pull's branch is ``main`` plus one empty commit.

Recorded (ticket -> pull, stage, unit):
- #1 -> #2 layout, #3 -> #4 animation, #5 -> #6 simulation, #7 -> #8 lighting, all ep01/Q1-2, merged;
- #9 -> #10 lighting ep01/Q1-1 and #11 -> #12 cross-shot ep01/Q1, merged;
- #13 -> #14 render ep01/Q1-2, open, `[RENDER-WRANGLER] READY`;
- #15 -> #16 cross-shot ep01/Q2, open;
- #17 -> #18 conform ep01, open, `[SOUND-MIXER] READY`;
- #19 -> #20 lighting ep01/Q2-1, closed without a merge.
"""

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

from kit.lib import gitstate, github, lockrules, production, tickets  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "lock_production"
NOW = "2026-11-11T12:00:00Z"
MERGED_AT = "2026-11-07T12:00:00Z"


class World:
    """The production repository, its state directory and a recorded GitHub, all temporary."""

    def __init__(self, test):
        tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, tmp)
        self.root = tmp / "show"
        self.dir = tmp / "github"
        self.state_dir = tmp / "state"
        patch = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                                             "FACTORY_GITHUB": str(self.dir), "FACTORY_NOW": NOW})
        patch.start()
        test.addCleanup(patch.stop)
        self.root.mkdir()
        subprocess.run([sys.executable, str(ROOT / "kit" / "factory"), "init", str(self.root),
                        "--rooms", "story,production"], check=True, capture_output=True, text=True)
        (self.root / "production.local.env").write_text(f"STATE_DIR={self.state_dir}\n")
        shutil.copytree(FIX / "production", self.root / "production", dirs_exist_ok=True)
        self.git("init", "-q", "-b", "main")
        self.commit("the production")
        shutil.copytree(FIX / "github", self.dir)
        for path in sorted((self.dir / "pulls").glob("*.json")):
            pull = json.loads(path.read_text())
            if pull["state"] == "open":
                ref = pull["head_ref"]
                self.git("checkout", "-q", "-b", ref, "main")
                self.git("commit", "-q", "--allow-empty", "-m", f"work on {ref}")
                self.git("checkout", "-q", "main")
        self.gh = github.Recorded(self.dir)

    def git(self, *args):
        return gitstate.git(self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", *args)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def set_record(self, kind, number, **fields):
        path = self.dir / kind / f"{number}.json"
        path.write_text(json.dumps({**json.loads(path.read_text()), **fields}, indent=1, sort_keys=True) + "\n")

    def unmerge(self, *pulls):
        for number in pulls:
            self.set_record("pulls", number, state="closed", merged_at=None)

    def merge(self, *pulls):
        for number in pulls:
            self.set_record("pulls", number, state="merged", merged_at=MERGED_AT)

    def add_render(self, number, unit):
        """A render ticket ``number`` for ``unit`` with the merged pull ``number + 1``."""
        self.set_record_new("issues", number, {
            "body": f"Stage render of {unit}.\n", "created_at": "2026-11-05T09:00:00Z",
            "labels": sorted(["tracked", "room:production", "stage:render", f"unit:{unit}"]),
            "number": number, "state": "closed", "title": f"[RN-{number}] Render of {unit}"})
        self.set_record_new("pulls", number + 1, {
            "base_ref": "main", "body": f"Ticket: #{number}\n", "created_at": "2026-11-05T10:00:00Z",
            "head_ref": f"feat/RN-{number}-render", "labels": [], "merged_at": MERGED_AT,
            "number": number + 1, "state": "merged", "ticket": number, "title": f"Work for #{number}"})

    def set_record_new(self, kind, number, record):
        (self.dir / kind / f"{number}.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")

    def load(self):
        p = production.load(self.root)
        return p, tickets.load(p, self.gh, self.root)

    def rules(self, number):
        p, state = self.load()
        pull = state.pulls[number]
        return lockrules.evaluate(p, state, state.tickets[pull["ticket"]], pull, self.root)

    def rule4(self, number):
        return self.rules(number)[3]


class LockProductionTest(unittest.TestCase):
    def test_render_clean(self):
        w = World(self)
        failed = {(r.number, r.reason) for r in w.rules(14) if not r.ok}
        self.assertEqual(failed, {(1, "no counting [LINT] verdict"),
                                  (2, "checker dailies-reviewer has no counting verdict")})

    def test_render_without_lighting(self):
        w = World(self)
        w.unmerge(8)
        self.assertEqual(w.rule4(14).reason, "upstream stage lighting is not locked for ep01/Q1-2")

    def test_render_without_cross_shot(self):
        w = World(self)
        w.unmerge(12)
        self.assertEqual(w.rule4(14).reason, "cheaper stage(s) not locked: cross-shot for ep01/Q1")

    def test_render_without_animation(self):
        w = World(self)
        w.unmerge(4)
        self.assertEqual(w.rule4(14).reason, "cheaper stage(s) not locked: animation for ep01/Q1-2")

    def test_render_without_two(self):
        w = World(self)
        w.unmerge(4, 12)
        self.assertEqual(w.rule4(14).reason,
                         "cheaper stage(s) not locked: animation for ep01/Q1-2, cross-shot for ep01/Q1")

    def test_cross_shot_waits_for_every_shot(self):
        w = World(self)
        rule = w.rule4(16)
        self.assertEqual((rule.ok, rule.reason), (False, "upstream stage lighting is not locked for ep01/Q2-1"))
        w.merge(20)
        rule = w.rule4(16)
        self.assertEqual((rule.ok, rule.reason), (True, ""))

    def test_cross_shot_without_shots(self):
        w = World(self)
        (w.root / "production" / "ep01" / "shots.json").unlink()
        w.commit("remove the shot list")
        self.assertEqual(w.rule4(16).reason, "upstream stage lighting is not locked for ep01/Q2: "
                                             "no shot in production/ep01/shots.json on the base")

    def test_conform(self):
        w = World(self)
        rules = w.rules(18)
        self.assertEqual(rules[3].reason, "upstream stage render is not locked for ep01/Q1-1")
        self.assertEqual(rules[8].reason, "stage conform is the last of room production and no counting "
                                          "[PRODUCER] GATE PASS covers the head")
        w.merge(14)
        w.add_render(21, "ep01/Q1-1")
        w.add_render(23, "ep01/Q2-1")
        rule = w.rule4(18)
        self.assertEqual((rule.ok, rule.reason), (True, ""))

    def test_shot_units(self):
        for unit, sequence in (("ep01/Q1-2", "ep01/Q1"), ("ep01/Q1-2-3", "ep01/Q1-2"), ("ep01/hero", None),
                               ("ep01/Q1-x", None), ("ep01/Q1/Q1-2", None), ("Q1-2", None)):
            self.assertEqual(tickets.shot_sequence(unit), sequence, unit)
        w = World(self)
        p, state = w.load()
        self.assertEqual(lockrules.cheaper_stages(
            p, state, {"room": "production", "stage": "render", "unit": "ep01/hero"}, w.root),
            "unit ep01/hero is not <episode>/<sequence>-<n>")
        self.assertEqual(lockrules.cheaper_stages(
            p, state, {"room": "production", "stage": "conform", "unit": "ep01"}, w.root), "")

    def test_factory_lock_refuses_the_render(self):
        w = World(self)
        w.unmerge(12)
        proc = subprocess.run([sys.executable, str(w.root / ".factory" / "factory"), "lock", "--root", str(w.root)],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.splitlines()
        self.assertIn("PR #14 #13 production/render ep01/Q1-2: NO 3 rule(s)", lines)
        self.assertIn("  rule 4 upstream: cheaper stage(s) not locked: cross-shot for ep01/Q1", lines)


if __name__ == "__main__":
    unittest.main()
