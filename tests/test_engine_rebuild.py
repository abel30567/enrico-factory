"""Tests for the second build of `kit/engine/job.sh` at `simulation` (#500): the job builds the level twice and
packs the second build's raw scene as `rerun-scene.json`; every other stage builds once. `job.sh` runs with
the fake programs of `tests/fixtures/engine/bin`, as in tests/test_engine_side.py.
"""

import json
import os
import shutil
import subprocess
import sys
import tarfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_engine_side import ENGINE, FIX, IMAGE, LINKS, STORE, EngineCase  # noqa: E402

FRAMES = [f"frame-{n:06d}.{e}" for n in (8, 9, 10) for e in ("exr", "png")]


class RebuildTest(EngineCase):
    def run_job(self, **fake) -> dict:
        """Runs a copy of job.sh with the fake programs first on PATH; returns what it left."""
        bin_ = self.tmp / "bin"
        shutil.copytree(FIX / "bin", bin_)
        for path in bin_.iterdir():
            path.chmod(0o755)
        work = self.tmp / "work"
        (work / "job").mkdir(parents=True)
        shutil.copy(ENGINE / "job.sh", work / "job/job.sh")
        shutil.copy(ENGINE / "build.py", work / "job/build.py")
        (self.tmp / "links.txt").write_text(LINKS, encoding="utf-8")
        (self.tmp / "fake").mkdir()
        env = {"PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}", "WORK": str(work), "IMAGE": IMAGE,
               "LINKS": str(self.tmp / "links.txt"), "NODE_LOG": str(self.tmp / "node.log"),
               "FAKE": str(self.tmp / "fake"), **fake}
        run = subprocess.run(["bash", str(work / "job/job.sh")], env=env, capture_output=True, text=True,
                             timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        fake_dir = self.tmp / "fake"
        with tarfile.open(work / "out.tar") as tar:
            names = tar.getnames()
        return {"work": work, "names": names,
                "status": (work / "status.json").read_text(encoding="utf-8"),
                "node": json.loads((work / "out/node.json").read_text(encoding="utf-8")),
                "log": (work / "log.txt").read_text(encoding="utf-8"),
                "curl": (fake_dir / "curl.log").read_text(encoding="utf-8").splitlines(),
                "timeout": (fake_dir / "timeout.log").read_text(encoding="utf-8").split(),
                "put": fake_dir / "put"}

    def env(self, stage: str) -> str:
        """`FAKE_ENV`: a `job.env` of frames 8 to 10 at ``stage``, nothing sampled."""
        path = self.tmp / "job.env"
        path.write_text(f'STAGE={stage}\nWIDTH=640\nHEIGHT=360\nFIRST=8\nLAST=10\nSAMPLED=""\n', encoding="utf-8")
        return str(path)

    def test_1_two_builds_at_simulation(self):
        rerun = self.tmp / "rerun-scene.json"
        rerun.write_text('{"actors": [1]}\n', encoding="utf-8")
        found = self.run_job(FAKE_ENV=self.env("simulation"), FAKE_RERUN_SCENE=str(rerun))
        self.assertEqual(found["status"], '{"result": "ok"}\n')
        self.assertEqual([s["step"] for s in found["node"]["steps"]], ["build", "rebuild", "render"])
        self.assertEqual(found["timeout"], ["900", "900", "1800"])
        self.assertEqual(found["names"], ["engine-build.log", "engine-render.log"] + FRAMES
                         + ["node.json", "rerun-scene.json", "scene.json"])
        out = found["work"] / "out"
        self.assertEqual((out / "scene.json").read_text(encoding="utf-8"), '{"actors": []}\n')
        self.assertEqual((out / "rerun-scene.json").read_text(encoding="utf-8"), '{"actors": [1]}\n')
        self.assertFalse((out / "first-scene.json").exists())
        self.assertEqual(found["node"]["steps"][1]["markers"], ["FACTORY build ok"])
        self.assertEqual(found["node"]["steps"][1]["command"], found["node"]["steps"][0]["command"])
        self.assertRegex(found["log"], r"(?m)^NODE \d\d:\d\d:\d\d step rebuild rc=0 seconds=\d+$")

    def test_2_a_second_build_without_the_ok_line_fails_at_rebuild(self):
        found = self.run_job(FAKE_ENV=self.env("simulation"), FAKE_RERUN_NO_OK="1")
        self.assertEqual(found["status"], '{"result": "failed", "step": "rebuild"}\n')
        self.assertEqual([s["step"] for s in found["node"]["steps"]], ["build", "rebuild"])
        self.assertEqual(found["node"]["steps"][0]["markers"], ["FACTORY build ok"])
        self.assertEqual(found["node"]["steps"][1]["markers"], [])
        self.assertEqual(found["curl"], [f"PUT {STORE}/out", f"PUT {STORE}/log", f"PUT {STORE}/status"])
        self.assertRegex(found["log"], r"(?m)^NODE \d\d:\d\d:\d\d result failed step=rebuild$")

    def test_3_every_other_stage_builds_once(self):
        for stage in ("layout", "animation", "lighting"):
            with self.subTest(stage=stage):
                self.setUp()
                found = self.run_job(FAKE_ENV=self.env(stage))
                self.assertEqual(found["status"], '{"result": "ok"}\n')
                self.assertEqual([s["step"] for s in found["node"]["steps"]], ["build", "render"])
                self.assertEqual(found["timeout"], ["900", "1800"])
                self.assertNotIn("rerun-scene.json", found["names"])
                self.assertFalse((found["work"] / "out/rerun-scene.json").exists())


if __name__ == "__main__":
    unittest.main()
