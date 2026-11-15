"""Tests for a job of a shot stage before its cheaper stages lock (section 9 rule 4, ISSUE-491).

The world is ``tests/test_lock_production.World`` with ``operator_pull("render", run=None)``: open ticket #21
and pull #22 for `render` of ep01/Q1-1, whose job has not run; `layout`, `animation` and `simulation` of
ep01/Q1-1 are locked only when the test adds their merged tickets #23, #25 and #27. Neither `factory gate`
nor `factory run` takes the job while one of them is not locked, and the tick waits.
"""

import contextlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from kit.lib import cmd_gate, cmd_run, gitstate, steps  # noqa: E402
from test_lock_production import World  # noqa: E402

JOB = "jobs/production/ep01/Q1-1/render/requests/r1.json"
HELD = ("cheaper stage(s) not locked: layout for ep01/Q1-1, animation for ep01/Q1-1, "
        "simulation for ep01/Q1-1")
SPEND_CAPS = "CAP_EPISODE_USD=10\nCAP_SEASON_USD=100\nCAP_EPISODE_COMPUTE_MIN=600\nCAP_SEASON_COMPUTE_MIN=6000\n"


class JobBeforeCheaperStagesTest(unittest.TestCase):
    def world(self, locked):
        w = World(self)
        w.operator_pull("render", run=None)
        if locked:
            for number, stage in ((23, "layout"), (25, "animation"), (27, "simulation")):
                w.add_ticket(number, stage, "ep01/Q1-1")
        p, state = w.load()
        head = gitstate.head(w.root, state.pulls[22]["head_ref"])
        manifest = state.pulls[22]["git"]["manifest"]
        (w.dir / "comments" / "22.json").write_text(json.dumps([{
            "body": f"[LINT] PASS\nrender: pass\nreviewed head {head} manifest {manifest}\n",
            "created_at": "2026-11-06T10:00:00Z", "id": 2201, "role": "program:lint"}], indent=1) + "\n")
        local = w.root / "production.local.env"
        local.write_text(local.read_text() + SPEND_CAPS)
        return w

    def step(self, w):
        p, state = w.load()
        entry = steps.next_step(p, state, state.tickets[21], w.root, "2026-11-11T12:00:00Z")
        return entry["state"], entry["step"], entry["role"], entry["pull"], entry["reason"]

    def command(self, main, w, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main([JOB, "--pull", "22", "--root", str(w.root), *extra])
        return code, out.getvalue(), err.getvalue()

    def tick(self, w):
        done = subprocess.run([sys.executable, str(w.root / ".factory" / "factory"), "tick", "--root", str(w.root)],
                              capture_output=True, text=True)
        return [line for line in done.stdout.splitlines() if line.startswith(("STEP #21", "WAIT #21", "FAIL #21"))]

    def test_the_render_job_waits_for_layout(self):
        # Seeded: nothing cheaper is locked.
        w = self.world(locked=False)
        self.assertEqual(self.step(w), ("gate", "none", None, 22, HELD))
        self.assertEqual(self.command(cmd_gate.main, w), (2, "", f"gate: {HELD}\n"))
        self.assertEqual(self.command(cmd_run.main, w), (2, "", f"run: {HELD}\n"))
        self.assertEqual(self.tick(w), [f"WAIT #21 gate {HELD}"])

    def test_a_passed_render_job_waits_for_layout(self):
        # Seeded: the gate passed while the three stages were locked, then layout's pull is not merged.
        w = self.world(locked=True)
        code, out, err = self.command(cmd_gate.main, w, "--apply")
        self.assertEqual(code, 0, out + err)
        w.unmerge(24)
        self.assertEqual(self.step(w), ("run", "none", None, 22, "cheaper stage(s) not locked: layout for ep01/Q1-1"))

    def test_the_render_job_gates_once_they_lock(self):
        w = self.world(locked=True)
        self.assertEqual(self.step(w), ("gate", "gate", None, 22, f"job {JOB} has no gate decision"))
        code, out, err = self.command(cmd_gate.main, w)
        self.assertEqual(code, 0, out + err)
        self.assertTrue(out.endswith(": PASS cap - reason within caps\n"), out)
        self.assertEqual(self.tick(w), [f"STEP #21 gate {JOB} PR #22"])


if __name__ == "__main__":
    unittest.main()
