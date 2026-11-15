"""A production maker's request reaches the stage's operator (ISSUE-487), through `factory launch ... --wait`.

The production is made by ``factory init --rooms story,production`` in a temporary directory, committed
on ``main``. GitHub is recorded in the test: the intake ticket #1 (ep01) with the merged pull #2, the
`assets` ticket #3 (wren), the `layout` ticket #4 (ep01/Q1-1), the `lighting` ticket #5 (ep01/Q1-1)
with the merged pull #6 and the `render` ticket #7 (ep01/Q1-1); a test may add the `cross-shot` ticket #8
(ep01/Q1) with the merged pull #9, which a render waits for. The harness is a copy of
tests/fixtures/harness/fake-harness, its steps written per test.
"""

import contextlib
import hashlib
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

from kit.lib import cmd_launch, gitstate, github, production, steps, tickets  # noqa: E402

FAKE = ROOT / "tests" / "fixtures" / "harness" / "fake-harness"
NOW = "2026-11-11T12:00:00Z"
CREATED = "2026-11-05T09:00:00Z"
MERGED_AT = "2026-11-07T12:00:00Z"
ACCOUNTS = ("acct-one", "acct-two")
REQUEST = '{"for": "wren", "prompt": "A small brown wren.", "refs": ["media:wren-ref"]}\n'
SCENE = "import unreal\n"
EVIDENCE = [{"criterion": "files written", "result": "pass", "command": "ls"}]
# number: (stage, unit, key, state); the pull number + 1 of a merged ticket is merged.
TICKETS = {1: ("intake", "ep01", "IN-1", "closed"), 3: ("assets", "wren", "AS-1", "open"),
           4: ("layout", "ep01/Q1-1", "LA-1", "open"), 5: ("lighting", "ep01/Q1-1", "LI-1", "closed"),
           7: ("render", "ep01/Q1-1", "RN-1", "open")}
MERGED = (1, 5)
CROSS_SHOT = {8: ("cross-shot", "ep01/Q1", "XS-1", "closed")}


def record(directory, kind, number, value):
    path = directory / kind / f"{number}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1, sort_keys=True) + "\n")


class World:
    """The production repository, its state directory, a fake harness and a recorded GitHub, all temporary."""

    def __init__(self, test, extra=None):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        test.addCleanup(shutil.rmtree, self.tmp)
        self.root = self.tmp / "show"
        self.dir = self.tmp / "github"
        patch = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                                             "FACTORY_GITHUB": str(self.dir), "FACTORY_NOW": NOW})
        patch.start()
        test.addCleanup(patch.stop)
        os.environ.pop("FACTORY_LAUNCH_WAIT", None)
        self.root.mkdir()
        subprocess.run([sys.executable, str(ROOT / "kit" / "factory"), "init", str(self.root),
                        "--rooms", "story,production"], check=True, capture_output=True, text=True)
        self.git("init", "-q", "-b", "main")
        self.commit("the production")
        extra = extra or {}
        found = {**TICKETS, **extra}
        for number, (stage, unit, key, state) in found.items():
            record(self.dir, "issues", number, {
                "body": f"Stage {stage} of {unit}.\n", "created_at": CREATED,
                "labels": sorted(["tracked", "room:production", f"stage:{stage}", f"unit:{unit}"]),
                "number": number, "state": state, "title": f"[{key}] {stage} of {unit}"})
        for number in MERGED + tuple(extra):
            record(self.dir, "pulls", number + 1, {
                "base_ref": "main", "body": f"Ticket: #{number}\n", "created_at": CREATED,
                "head_ref": f"feat/{found[number][2]}-work", "labels": [], "merged_at": MERGED_AT,
                "number": number + 1, "state": "merged", "ticket": number, "title": f"Work for #{number}"})
        self.harness = self.tmp / "harness"
        self.harness.mkdir()
        shutil.copy2(FAKE, self.harness / FAKE.name)
        secrets = self.tmp / "secrets"
        (secrets / "inference.primary").mkdir(parents=True)
        for name in ACCOUNTS:
            path = secrets / "inference.primary" / name
            path.write_text("token\n")
            path.chmod(0o600)
        settings = {"STATE_DIR": str(self.tmp / "state"), "SECRETS_DIR": str(secrets),
                    "HARNESS": str(self.harness / FAKE.name), "MODEL_MAKER": "maker-model-1",
                    "MODEL_CHECKER": "checker-model-1", "ACCOUNTS_MAKER": ", ".join(ACCOUNTS),
                    "ACCOUNTS_CHECKER": ", ".join(ACCOUNTS), "REPO": "owner/show", "SANDBOX": "off"}
        (self.root / "production.local.env").write_text("".join(f"{k}={v}\n" for k, v in settings.items()))
        self.gh = github.Recorded(self.dir)

    def git(self, *args):
        return gitstate.git(self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", *args)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def steps(self, *found):
        (self.harness / "fake-harness.json").write_text(json.dumps(list(found), indent=1) + "\n")
        for name in ("fake-harness.used", "fake-harness.calls.jsonl"):
            (self.harness / name).unlink(missing_ok=True)

    def launch(self, role, number):
        """``(exit code, stdout, stderr)`` of ``factory launch <role> <number> --root <root> --wait``."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_launch.main([role, str(number), "--root", str(self.root), "--wait"])
        return code, out.getvalue(), err.getvalue()

    def next_step(self, number):
        p = production.load(self.root)
        state = tickets.load(p, self.gh, self.root)
        step = steps.next_step(p, state, state.tickets[number], self.root, NOW)
        return step["state"], step["step"], step["role"], step["pull"]

    def pull_of(self, number):
        p = production.load(self.root)
        state = tickets.load(p, self.gh, self.root)
        return max(pull["number"] for pull in tickets.pulls_of(state, number))

    def ready(self, number, role):
        """The body of the newest READY on ``number`` posted with ``role``."""
        found = [c["body"] for c in self.gh.comments(number) if c["role"] == role and " READY" in c["body"]]
        return found[-1]


def maker_step(match, files):
    return {"match": match, "files": files,
            "result": {"marker": "READY", "summary": "The files are written.", "evidence": EVIDENCE}}


def dropped_line(body):
    return next(line for line in body.splitlines() if line.startswith("Dropped (outside writes): "))


class RequestsTest(unittest.TestCase):
    def maker(self, number, role, match, scene, request):
        """Launch the maker of ticket ``number`` writing ``scene`` and ``request``: (world, READY, pull)."""
        w = World(self)
        w.steps(maker_step(match, {scene: SCENE, request: REQUEST}))
        code, out, err = w.launch(role, number)
        self.assertEqual(code, 0, out + err)
        pull = w.pull_of(number)
        return w, w.ready(pull, role), pull

    def test_assets_request_outside_writes_is_dropped(self):
        request = "production/assets/wren/requests/model3d.json"
        _, body, _ = self.maker(3, "asset-builder", "You are the asset builder",
                                "production/assets/wren/assets/scene.py", request)
        self.assertEqual(dropped_line(body), f"Dropped (outside writes): {request}")

    def test_assets_request_reaches_the_operator(self):
        w, body, pull = self.maker(3, "asset-builder", "You are the asset builder",
                                   "production/assets/wren/assets/scene.py",
                                   "production/assets/wren/assets/requests/model3d.json")
        self.assertEqual(dropped_line(body), "Dropped (outside writes): none")
        self.assertEqual(w.next_step(3), ("jobs", "launch", "render-wrangler", pull))

    def test_layout_request_outside_writes_is_dropped(self):
        request = "production/ep01/Q1-1/requests/render.json"
        _, body, _ = self.maker(4, "layout-artist", "You are the layout artist",
                                "production/ep01/Q1-1/layout/scene.py", request)
        self.assertEqual(dropped_line(body), f"Dropped (outside writes): {request}")

    def test_layout_request_reaches_the_operator(self):
        w, body, pull = self.maker(4, "layout-artist", "You are the layout artist",
                                   "production/ep01/Q1-1/layout/scene.py",
                                   "production/ep01/Q1-1/layout/requests/render.json")
        self.assertEqual(dropped_line(body), "Dropped (outside writes): none")
        self.assertEqual(w.next_step(4), ("jobs", "launch", "render-wrangler", pull))

    def test_render_waits_for_its_cross_shot(self):
        self.assertEqual(World(self).next_step(7), ("waiting", "none", None, None))

    def test_render_has_a_maker_before_its_operator(self):
        w = World(self, CROSS_SHOT)
        self.assertEqual(w.next_step(7), ("ready", "launch", "lighter", None))
        request = "production/ep01/Q1-1/render/requests/render.json"
        w.steps({"match": "You are the lighter", "files": {request: REQUEST},
                 "result": {"marker": "READY", "summary": "The render is requested.", "evidence": EVIDENCE}})
        code, out, err = w.launch("lighter", 7)
        self.assertEqual(code, 0, out + err)
        pull = w.pull_of(7)
        self.assertEqual(dropped_line(w.ready(pull, "lighter")), "Dropped (outside writes): none")
        self.assertEqual(w.next_step(7), ("jobs", "launch", "render-wrangler", pull))
        job = {"ticket": 7, "tool": "render", "caller": "render-wrangler",
               "inputs": [{"path": request, "sha256": hashlib.sha256(REQUEST.encode()).hexdigest()}]}
        w.steps({"match": "You are the render wrangler",
                 "files": {"jobs/" + request: json.dumps(job, indent=1) + "\n"},
                 "result": {"marker": "READY", "summary": "The job file is written.", "evidence": EVIDENCE}})
        code, out, err = w.launch("render-wrangler", pull)
        self.assertEqual(code, 0, out + err)
        self.assertRegex(out.splitlines()[-1], r"^posted \[RENDER-WRANGLER\] READY for launch \S+$")
        self.assertNotIn("give the pull request number", out + err)
        self.assertEqual(dropped_line(w.ready(pull, "render-wrangler")), "Dropped (outside writes): none")


if __name__ == "__main__":
    unittest.main()
