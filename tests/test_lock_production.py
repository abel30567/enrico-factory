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

import hashlib
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

from kit.lib import gitstate, github, ledger, lockrules, production, tickets  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "lock_production"
NOW = "2026-11-11T12:00:00Z"
MERGED_AT = "2026-11-07T12:00:00Z"
STAGE_FILES = {"layout": ("manifest.json", "state.json"), "render": ("manifest.json", "render.json", "state.json")}


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

    def add_ticket(self, number, stage, unit, merged=True):
        """A ticket ``number`` for ``production/<stage>`` of ``unit`` with the pull ``number + 1``, merged or
        closed without a merge."""
        self.set_record_new("issues", number, {
            "body": f"Stage {stage} of {unit}.\n", "created_at": "2026-11-05T09:00:00Z",
            "labels": sorted(["tracked", "room:production", f"stage:{stage}", f"unit:{unit}"]),
            "number": number, "state": "closed" if merged else "open",
            "title": f"[ST-{number}] {stage} of {unit}"})
        self.set_record_new("pulls", number + 1, {
            "base_ref": "main", "body": f"Ticket: #{number}\n", "created_at": "2026-11-05T10:00:00Z",
            "head_ref": f"feat/ST-{number}-{stage}", "labels": [], "merged_at": MERGED_AT if merged else None,
            "number": number + 1, "state": "merged" if merged else "closed", "ticket": number,
            "title": f"Work for #{number}"})

    def shot_file(self, shot, data, sequence="Q1"):
        """The shot file ``episodes/ep01/sequences/<sequence>/shots/<shot>.json``, committed on ``main``."""
        path = self.root / "episodes" / "ep01" / "sequences" / sequence / "shots" / f"{shot}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data, indent=1, sort_keys=True) + "\n")
        self.commit(f"the shot file of {shot}")

    def operator_pull(self, stage, tool="render", run="DONE", extra=()):
        """Open ticket #21 and pull #22 for ``production/<stage>`` of ep01/Q1-1: on the branch the request
        ``production/ep01/Q1-1/<stage>/requests/r1.json``, its job by `render-wrangler` with ``tool``, the
        scene script at `layout`, the stage files of ``stage`` and the paths ``extra``; on ``main`` the job's
        ``run`` row ``run`` (none when None)."""
        unit, ref = "ep01/Q1-1", f"feat/ST-21-{stage}"
        directory = f"production/{unit}/{stage}"
        request = f"{directory}/requests/r1.json"
        text = '{"shot": "Q1-1"}\n'
        job = {"caller": "render-wrangler", "expected": {"compute_min": 5, "usd": 1},
               "inputs": [{"path": request, "sha256": hashlib.sha256(text.encode()).hexdigest()}],
               "ticket": 21, "tool": tool}
        files = {request: text, f"jobs/{request}": json.dumps(job, indent=1, sort_keys=True) + "\n"}
        if stage == "layout":
            files[f"{directory}/scene.py"] = "# the layout\n"
        files.update({f"{directory}/{name}": "{}\n" for name in STAGE_FILES[stage]})
        files.update({path: "{}\n" for path in extra})
        self.set_record_new("issues", 21, {
            "body": f"Stage {stage} of {unit}.\n", "created_at": "2026-11-05T09:00:00Z",
            "labels": sorted(["tracked", "room:production", f"stage:{stage}", f"unit:{unit}"]),
            "number": 21, "state": "open", "title": f"[ST-21] {stage} of {unit}"})
        self.set_record_new("pulls", 22, {
            "base_ref": "main", "body": "Ticket: #21\n", "created_at": "2026-11-05T10:00:00Z",
            "head_ref": ref, "labels": [], "merged_at": None, "number": 22, "state": "open", "ticket": 21,
            "title": "Work for #21"})
        self.git("checkout", "-q", "-b", ref, "main")
        for rel, content in files.items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text(content)
        self.commit(f"work on {ref}")
        self.git("checkout", "-q", "main")
        if run is not None:
            canonical = json.dumps(job, sort_keys=True, separators=(",", ":"))
            row = {"time": NOW, "kind": "run", "hash": hashlib.sha256(canonical.encode()).hexdigest(),
                   "ticket": 21, "episode": "ep01", "tool": tool, "usd": "1", "compute_min": "5",
                   "caller": "render-wrangler", "verdict": run, "cap": "-"}
            path = self.root / ledger.PATH
            current = path.read_text() if path.is_file() else ledger.HEADER + "\n"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(current + ledger.line(row) + "\n")
            self.commit("the run row")

    def rule7(self, maker):
        """``lockrules._writes`` of pull #22 for ``maker`` with the paths ``operator_paths`` takes."""
        p, state = self.load()
        pull = state.pulls[22]
        ticket = state.tickets[21]
        return lockrules._writes(p, "production", maker, pull["git"]["changed"],
                                 lockrules.operator_paths(p, state, ticket, pull, self.root))

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


    def test_rule_7_takes_the_layout_stage_files(self):
        w = World(self)
        w.operator_pull("layout")
        self.assertEqual(w.rule7("layout-artist"), "")

    def test_rule_7_takes_the_render_stage_files(self):
        w = World(self)
        w.operator_pull("render")
        self.assertEqual(w.rule7("render-wrangler"), "")

    def test_rule_7_stage_files_seeded(self):
        w = World(self)
        w.operator_pull("layout", extra=["production/ep01/Q1-1/layout/notes.json"])
        self.assertEqual(w.rule7("layout-artist"),
                         "outside the writes of layout-artist: production/ep01/Q1-1/layout/notes.json")
        w = World(self)
        w.operator_pull("layout", extra=["production/ep01/Q1-2/layout/state.json"])
        self.assertEqual(w.rule7("layout-artist"),
                         "outside the writes of layout-artist: production/ep01/Q1-2/layout/state.json")
        for run in ("FAILED", None):
            with self.subTest(run=run):
                w = World(self)
                w.operator_pull("layout", run=run)
                reason = w.rule7("layout-artist")
                self.assertTrue(reason.startswith("outside the writes of layout-artist: "), reason)
                for path in ("jobs/production/ep01/Q1-1/layout/requests/r1.json",
                             "production/ep01/Q1-1/layout/manifest.json", "production/ep01/Q1-1/layout/state.json"):
                    self.assertIn(path, reason)
        w = World(self)
        w.operator_pull("layout", tool="renderbox")
        self.assertEqual(w.rule7("layout-artist"), "outside the writes of layout-artist: "
                         "production/ep01/Q1-1/layout/manifest.json, production/ep01/Q1-1/layout/state.json")

    def assets_world(self):
        w = World(self)
        w.shot_file("Q1-2", {"id": "Q1-2", "characters": ["tobi", "ama"], "set": "lighthouse"})
        return w

    def test_rule_4_waits_for_the_assets(self):
        w = self.assets_world()
        w.add_ticket(21, "assets", "ama")
        w.add_ticket(23, "assets", "tobi", merged=False)
        w.add_ticket(25, "rigging", "ama")
        reason = "cheaper stage(s) not locked: assets for lighthouse, assets for tobi, rigging for tobi"
        rule = w.rule4(14)
        self.assertEqual((rule.ok, rule.reason), (False, reason))
        p, state = w.load()
        self.assertEqual(lockrules.cheaper_stages(
            p, state, {"room": "production", "stage": "layout", "unit": "ep01/Q1-2"}, w.root), reason)
        w = self.assets_world()
        for number, (stage, unit) in zip(range(21, 31, 2), (("assets", "ama"), ("assets", "tobi"),
                                                            ("assets", "lighthouse"), ("rigging", "ama"),
                                                            ("rigging", "tobi"))):
            w.add_ticket(number, stage, unit)
        rule = w.rule4(14)
        self.assertEqual((rule.ok, rule.reason), (True, ""))

    def test_rule_4_a_set_is_not_rigged(self):
        for merged in (True, False):
            with self.subTest(rigging_tobi_merged=merged):
                w = self.assets_world()
                w.add_ticket(21, "assets", "ama")
                w.add_ticket(23, "assets", "tobi")
                w.add_ticket(25, "assets", "lighthouse")
                w.add_ticket(27, "rigging", "ama")
                w.add_ticket(29, "rigging", "tobi", merged=merged)
                rule = w.rule4(14)
                self.assertEqual((rule.ok, rule.reason),
                                 (True, "") if merged else (False, "cheaper stage(s) not locked: rigging for tobi"))

    def test_rule_4_a_shot_with_no_asset(self):
        for shot_file in (True, False):
            with self.subTest(shot_file=shot_file):
                w = World(self)
                if shot_file:
                    w.shot_file("Q1-2", {"id": "Q1-2", "characters": []})
                w.unmerge(4)
                self.assertEqual(w.rule4(14).reason, "cheaper stage(s) not locked: animation for ep01/Q1-2")
                p, state = w.load()
                self.assertEqual(lockrules.cheaper_stages(
                    p, state, {"room": "production", "stage": "layout", "unit": "ep01/Q1-2"}, w.root), "")

    def test_shotfile_subjects(self):
        from kit.lib import shotfile
        w = World(self)
        p = production.load(w.root)
        w.shot_file("Q1-2", {"characters": ["tobi", "ama", "ama", "ama lee", 7], "set": 7})
        self.assertEqual(shotfile.subjects(p, w.root, "ep01/Q1-2"), (["ama", "tobi"], []))
        w.shot_file("Q1-2", "not json\n")
        self.assertEqual(shotfile.subjects(p, w.root, "ep01/Q1-2"), ([], []))
        w.shot_file("Q1-2", {"characters": ["ama"], "set": "lighthouse"})
        for unit in ("ep01", "ep01/Q1/Q1-2"):
            self.assertEqual(shotfile.subjects(p, w.root, unit), ([], []), unit)
        w = World(self)
        p = production.load(w.root)
        w.shot_file("Q1-2", {"characters": ["tobi"]}, sequence="Q9")
        self.assertEqual(shotfile.path(p, w.root, "ep01/Q1-2"), "episodes/ep01/sequences/Q9/shots/Q1-2.json")
        self.assertEqual(shotfile.subjects(p, w.root, "ep01/Q1-2"), (["tobi"], []))
        w.shot_file("Q1-2", {"characters": ["ama"], "set": "lighthouse"})
        self.assertEqual(shotfile.subjects(p, w.root, "ep01/Q1-2"), (["ama"], ["lighthouse"]))


class CrossShotLockTest(unittest.TestCase):
    """The ``cross-shot`` stage has no maker: it locks with its lint and checker, and a render waits for it."""

    def review(self, w, approved=True):
        """``[LINT] PASS`` and, when ``approved``, ``[CONTINUITY-SUPERVISOR] APPROVED`` on pull #16, both on its
        current head."""
        head = w.git("rev-parse", "feat/XS-2-cross-shot").strip()
        reviewed = f"reviewed head {head} manifest none\n"
        comments = [{"body": "[LINT] PASS\nChecks required by stage cross-shot: cross-shot\n" + reviewed,
                     "created_at": "2026-11-06T10:00:00Z", "id": 1601, "role": "program:lint"}]
        if approved:
            comments.append({"body": "[CONTINUITY-SUPERVISOR] APPROVED\n" + reviewed,
                             "created_at": "2026-11-06T11:00:00Z", "id": 1602, "role": "continuity-supervisor"})
        (w.dir / "comments" / "16.json").write_text(json.dumps(comments, indent=1, sort_keys=True) + "\n")

    def ready(self, w):
        w.merge(20)
        self.review(w)

    def failed(self, w):
        return [(r.number, r.name, r.reason) for r in w.rules(16) if not r.ok]

    def test_cross_shot_locks_with_no_maker(self):
        w = World(self)
        self.ready(w)
        self.assertEqual(self.failed(w), [])
        self.assertTrue(lockrules.lockable(w.rules(16)))

    def test_cross_shot_without_approval(self):
        w = World(self)
        w.merge(20)
        self.review(w, approved=False)
        self.assertEqual(self.failed(w), [(2, "checkers", "checker continuity-supervisor has no counting verdict")])

    def test_cross_shot_pull_that_changes_a_file(self):
        w = World(self)
        path = "production/ep01/Q2-1/lighting/state.json"
        w.git("checkout", "-q", "feat/XS-2-cross-shot")
        (w.root / path).parent.mkdir(parents=True, exist_ok=True)
        (w.root / path).write_text("{}\n")
        w.commit("a file on the cross-shot pull")
        w.git("checkout", "-q", "main")
        self.ready(w)
        self.assertEqual(self.failed(w),
                         [(7, "writes", f"stage cross-shot has no maker and the pull changes files: {path}")])

    def test_factory_lock_locks_the_cross_shot(self):
        w = World(self)
        self.ready(w)
        command = [sys.executable, str(w.root / ".factory" / "factory"), "lock", "--root", str(w.root)]
        proc = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("PR #16 #15 production/cross-shot ep01/Q2: LOCKABLE", proc.stdout.splitlines())
        proc = subprocess.run(command + ["--apply"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.splitlines()[-1].startswith("locked PR #16 at "), proc.stdout)
        p, state = w.load()
        self.assertTrue(tickets.is_locked(state, "production", "cross-shot", "ep01/Q2"))

    def test_render_waits_for_cross_shot(self):
        w = World(self)
        p, state = w.load()
        self.assertEqual(tickets.upstream(p, state, state.tickets[13], w.root),
                         (True, "upstream stage lighting is locked for ep01/Q1-2"))
        w.unmerge(12)
        p, state = w.load()
        self.assertEqual(tickets.upstream(p, state, state.tickets[13], w.root),
                         (False, "cheaper stage(s) not locked: cross-shot for ep01/Q1"))

    def test_render_waits_for_cross_shot_after_its_assets(self):
        w = World(self)
        w.shot_file("Q1-2", {"characters": ["ama"], "set": "lighthouse"})
        w.unmerge(12)
        reason = ("cheaper stage(s) not locked: assets for ama, assets for lighthouse, rigging for ama, "
                  "cross-shot for ep01/Q1")
        p, state = w.load()
        self.assertEqual(tickets.upstream(p, state, state.tickets[13], w.root), (False, reason))
        self.assertEqual(w.rule4(14).reason, reason)

    def test_no_other_stage_waits_for_cross_shot(self):
        w = World(self)
        w.unmerge(12)
        p, state = w.load()
        for stage in ("layout", "animation", "simulation", "lighting"):
            with self.subTest(stage=stage):
                ticket = {"room": "production", "stage": stage, "unit": "ep01/Q1-2"}
                _, reason = tickets.upstream(p, state, ticket, w.root)
                self.assertNotIn("cross-shot", reason)


if __name__ == "__main__":
    unittest.main()
