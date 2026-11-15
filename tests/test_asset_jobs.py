"""Tests for a job that names an asset: `factory gate` on an `asset:<id>` input, `registry.locked` and the
engine state of a scene whose actor is a job asset.

The gate runs on the temporary production of tests/test_gate.py; the scene is the `lit-render` golden unit's
`scene.json` of tests/fixtures/engine. Every state is written in a temporary repository: no new fixture.
"""

import contextlib
import copy
import hashlib
import io
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from kit.lib import cmd_gate, golden, registry, scenestate  # noqa: E402
from test_engine_side import FIX, _job  # noqa: E402
from test_gate import CLEAN_JOB, JOB_PULL, World  # noqa: E402

BIN = hashlib.sha256(b"wren glb").hexdigest()
STATE = {"asset": "wren", "kind": "character", "sha256": BIN}
STATE_PATH = "production/assets/wren/assets/state.json"
PULL_JOB = "jobs/renders/requests/r1.json"


def _text(state):
    return json.dumps(state, indent=1) + "\n"


class GateTest(unittest.TestCase):
    def setUp(self):
        self.w = World(self)

    def lock(self, state=STATE):
        self.w.write(STATE_PATH, _text(state))
        self.w.commit("lock wren")

    def job(self, path="asset:wren", digest=BIN):
        inputs = json.loads((self.w.root / CLEAN_JOB).read_text())["inputs"]
        return self.w.job(inputs=inputs + [{"path": path, "sha256": digest}])

    def assert_refused(self, message):
        self.assertEqual(self.w.gate(self.job_path, apply=False), (2, "", f"gate: {message}\n"))

    def test_a_locked_asset_passes(self):
        self.lock()
        code, out, err = self.w.gate(self.job(), apply=False)
        self.assertEqual((code, err), (0, ""))
        self.assertRegex(out, r"^gate job [0-9a-f]{64}: PASS cap - reason within caps\n$")

    def test_no_state(self):
        self.job_path = self.job()
        self.assert_refused("input asset:wren has no locked state on the base")

    def test_another_sha256(self):
        self.lock()
        self.job_path = self.job(digest="0" * 64)
        self.assert_refused("input asset:wren does not match the sha256 of its locked state")

    def test_kind_tree(self):
        self.lock(dict(STATE, kind="tree"))
        self.job_path = self.job()
        self.assert_refused("input asset:wren has no locked state on the base")

    def test_another_asset(self):
        self.lock(dict(STATE, asset="bo"))
        self.job_path = self.job()
        self.assert_refused("input asset:wren has no locked state on the base")

    def test_sha256_not_hex(self):
        self.lock(dict(STATE, sha256="x"))
        self.job_path = self.job()
        self.assert_refused("input asset:wren has no locked state on the base")

    def test_id_not_an_asset_id(self):
        self.lock()
        for path in ("asset:Wren", "asset:", "asset:wren/x"):
            self.job_path = self.job(path)
            self.assert_refused(f"input {path} has no locked state on the base")

    def test_state_only_on_the_pull(self):
        job = json.loads((self.w.root / CLEAN_JOB).read_text())
        job["inputs"] = [{"path": "asset:wren", "sha256": BIN}]
        ref = self.w.pull_record(JOB_PULL)["head_ref"]
        self.w.git("checkout", "-q", ref)
        self.w.write(PULL_JOB, json.dumps(job, indent=1))
        self.w.write(STATE_PATH, _text(STATE))
        self.w.commit("job and state on " + ref)
        self.w.git("checkout", "-q", "trunk")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_gate.main([PULL_JOB, "--pull", str(JOB_PULL), "--root", str(self.w.root)])
        self.assertEqual((code, err.getvalue()), (2, "gate: input asset:wren has no locked state on the base\n"))


class LockedTest(unittest.TestCase):
    def test_locked(self):
        w = World(self)

        def locked(text, asset="wren"):
            w.write(STATE_PATH, text)
            w.commit("state")
            return registry.locked(w.root, "trunk", asset)

        self.assertIsNone(registry.locked(w.root, "trunk", "wren"))
        self.assertEqual(locked(_text(STATE)), STATE)
        self.assertIsNone(registry.locked(w.root, "trunk", "Wren"))
        self.assertIsNone(locked("not json\n"))
        self.assertIsNone(locked(_text([STATE])))
        self.assertIsNone(locked(_text(dict(STATE, kind="tree"))))
        self.assertIsNone(locked(_text(dict(STATE, asset="bo"))))
        self.assertIsNone(locked(_text(dict(STATE, sha256=BIN[:63]))))


class StateTest(unittest.TestCase):
    level = golden.UNITS["lit-render"]

    def scene(self, wren=True, label="wren"):
        scene = json.loads((FIX / "lit-render/scene.json").read_text(encoding="utf-8"))
        if wren:
            actor = copy.deepcopy(next(a for a in scene["actors"] if a["label"] == "ball"))
            actor["label"] = label
            scene["actors"].append(actor)
        return scene

    def raises(self, scene, job, message, **kw):
        with self.assertRaises(scenestate.SceneError) as caught:
            scenestate.state(scene, job, self.level, **kw)
        self.assertEqual(str(caught.exception), message)

    def test_state_of_an_asset(self):
        job = dict(_job("lit-render"), assets={"wren": BIN})
        state = scenestate.state(self.scene(), job, self.level, assets={"wren": STATE})
        actors = {a["id"]: a for a in state["actors"]}
        wren = actors.pop("wren")
        self.assertEqual((wren["kind"], wren["asset"], wren["sha256"]), ("character", "asset:wren", BIN))
        plain = scenestate.state(self.scene(False), _job("lit-render"), self.level)
        self.assertEqual(actors, {a["id"]: a for a in plain["actors"]})
        ball = next(a for a in plain["actors"] if a["id"] == "ball")
        self.assertEqual({k: wren[k] for k in ("scale", "height_m")}, {k: ball[k] for k in ("scale", "height_m")})
        self.assertNotEqual(ball["kind"], "character")
        over = scenestate.state(self.scene(False), dict(_job("lit-render"), assets={"ball": BIN}), self.level,
                                assets={"ball": dict(STATE, asset="ball")})
        ball = next(a for a in over["actors"] if a["id"] == "ball")
        self.assertEqual((ball["kind"], ball["asset"], ball["sha256"]), ("character", "asset:ball", BIN))
        locked = "scene names asset:wren; it has no locked state"
        self.raises(self.scene(), job, locked)
        for assets in ({}, {"wren": None}, {"wren": dict(STATE, sha256="0" * 64)}, {"wren": dict(STATE, kind="tree")}):
            self.raises(self.scene(), job, locked, assets=assets)
        self.raises(self.scene(False), job, "scene names asset:wren; no actor is labelled wren", assets={"wren": STATE})
        self.raises(self.scene(), _job("lit-render"), "actor wren has no kind: no level names it",
                    assets={"wren": STATE})


class NoAssetTest(unittest.TestCase):
    def test_no_asset_is_as_before(self):
        scene = json.loads((FIX / "lit-render/scene.json").read_text(encoding="utf-8"))
        job, level = _job("lit-render"), golden.UNITS["lit-render"]
        before = scenestate.state(scene, job, level)
        texts = {scenestate.dumps(before), scenestate.dumps(scenestate.state(scene, job, level, assets={})),
                 scenestate.dumps(scenestate.state(scene, job, level, assets={"wren": STATE}))}
        self.assertEqual(len(texts), 1)
        self.assertTrue(all(a["asset"] is None and a["sha256"] is None for a in before["actors"]))


if __name__ == "__main__":
    unittest.main()
