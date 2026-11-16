"""The sound mixer's eval suite in kit/evals/sound-mixer/ (brief section 13), run offline (ISSUE-567).

The `conform` stage has no judge, so the suite measures the lint pass rate only. Recorded outputs in
tests/fixtures/story_evals/ stand in for the role: nothing here runs a model. The role is found as a maker
of the pipeline's `production/conform` entry, not in a loop list.
The conform lint does not read the scene script, so a scene script's content is not graded here.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kit.lib import evals, lint

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "kit"
FIX = ROOT / "tests" / "fixtures" / "story_evals"
FAKE = ROOT / "tests" / "fixtures" / "harness" / "fake-harness"
FACTORY = ROOT / "kit" / "factory"

ENTRY = next(e for e in json.loads((KIT / "pipeline.json").read_text(encoding="utf-8"))
             if e["room"] == "production" and e["stage"] == "conform")
ROLE = "sound-mixer"
CONFORM = "production/ep01/conform"
SCENE = f"{CONFORM}/scene.py"
READY = {"marker": "READY", "summary": "Done.",
         "evidence": [{"criterion": "stage files", "result": "written", "command": "ls"}]}
CUES = {"L1": (2, 10), "L2": (13, 22), "L3": (26, 44)}


def tree_hash(path):
    digest = hashlib.sha256()
    for item in sorted(p for p in Path(path).rglob("*") if p.is_file()):
        digest.update(item.relative_to(path).as_posix().encode() + b"\0" + item.read_bytes())
    return digest.hexdigest()


def recorded(case):
    """The recorded output of the case: ``{path: text}``."""
    output = FIX / ROLE / case.id
    return {p.relative_to(output).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(output.rglob("*")) if p.is_file()}


def run_eval(files):
    """``(completed process, summary, details)`` of ``factory eval`` on the fake harness, each case's READY
    writing ``files(case)``; ``details`` the graded detail of each result."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        bin_dir, root, out = tmp / "bin", tmp / "root", tmp / "out"
        bin_dir.mkdir()
        root.mkdir()
        shutil.copy(FAKE, bin_dir / "fake-harness")
        os.chmod(bin_dir / "fake-harness", 0o755)
        steps = [{"match": f"eval {re.escape(case.id)}(?![\\w-])", "files": files(case), "result": READY}
                 for case in evals.load(KIT, ROLE)]
        (bin_dir / "fake-harness.json").write_text(json.dumps(steps), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "FACTORY_LIVE"}
        env["FACTORY_FAKE_HARNESS"] = str(bin_dir / "fake-harness")
        done = subprocess.run([sys.executable, str(FACTORY), "eval", ROLE, "--harness", "fake",
                               "--root", str(root), "--out", str(out)],
                              env=env, cwd=ROOT, capture_output=True, text=True, timeout=300)
        summary, results = out / "summary.json", out / "results.jsonl"
        return (done, json.loads(summary.read_text(encoding="utf-8")) if summary.is_file() else None,
                [json.loads(line)["detail"] for line in results.read_text(encoding="utf-8").splitlines()]
                if results.is_file() else [])


def write(run_dir, rel, text):
    path = run_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def writes(rel, text):
    return lambda run_dir: write(run_dir, rel, text)


def moves(rel):
    def change(run_dir):
        write(run_dir, rel, (run_dir / SCENE).read_text(encoding="utf-8"))
        (run_dir / SCENE).unlink()
    return change


def removes(run_dir):
    (run_dir / SCENE).unlink()


def conforms(run_dir):
    """All three conform files of the editor and the job, consistent with each other and the tree."""
    lines = json.loads((run_dir / "episodes/ep01/lines.json").read_text(encoding="utf-8"))
    write(run_dir, f"{CONFORM}/edit-list.json",
          (run_dir / "episodes/ep01/reel/edit-list.json").read_text(encoding="utf-8"))
    write(run_dir, f"{CONFORM}/captions.json", json.dumps(
        [{"line": e["id"], "text": e["text"], "start": CUES[e["id"]][0], "end": CUES[e["id"]][1]}
         for e in lines]) + "\n")
    write(run_dir, f"{CONFORM}/mix.json", json.dumps(
        {"loudness_lufs": -14.0, "true_peak_dbtp": -1.5,
         "lines": [{"line": e["id"], "start": CUES[e["id"]][0], "end": CUES[e["id"]][1]} for e in lines],
         "stems": [], "master": "0" * 64}) + "\n")


STRAY = (("a mix", writes(f"{CONFORM}/mix.json", "{}"),
          ["conform FAIL 2 findings", f"B1 conform {CONFORM}/mix.json:1 {CONFORM}/captions.json is missing",
           f"B2 conform {CONFORM}/mix.json:1 {CONFORM}/edit-list.json is missing"]),
         ("captions", writes(f"{CONFORM}/captions.json", "[]"),
          ["conform FAIL 2 findings", f"B1 conform {CONFORM}/captions.json:1 {CONFORM}/edit-list.json is missing",
           f"B2 conform {CONFORM}/captions.json:1 {CONFORM}/mix.json is missing"]),
         ("an edit list", writes(f"{CONFORM}/edit-list.json", "{}"),
          ["conform FAIL 2 findings", f"B1 conform {CONFORM}/edit-list.json:1 {CONFORM}/captions.json is missing",
           f"B2 conform {CONFORM}/edit-list.json:1 {CONFORM}/mix.json is missing"]))
MISSING = (("removed", removes), ("another episode", moves("production/ep02/conform/scene.py")),
           ("renamed", moves(f"{CONFORM}/mix.py")))
NEAR_MISSES = (("a manifest", writes(f"{CONFORM}/manifest.json", "{}")),
               ("a clock read", writes(SCENE, "import time\n\nSTART = time.time()\n")),
               ("every conform file, consistent", conforms))


def graded(case, change=None):
    """``(grade, run directory text of the conform lint)`` of READY on the case's tree with its recorded
    output, the run directory then edited by ``change(run_dir)``."""
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        shutil.copytree(case.path / "tree", run_dir)
        shutil.copytree(FIX / ROLE / case.id, run_dir, dirs_exist_ok=True)
        if change:
            change(run_dir)
        return evals.grade(case, READY, run_dir=run_dir), lint.text(lint.run(run_dir, only=["conform"]))


class SoundMixerEvalsTest(unittest.TestCase):
    def test_the_suite_is_the_conform_entrys(self):
        self.assertIn(ROLE, ENTRY["maker"])
        self.assertTrue((KIT / "evals" / ROLE / "suite.json").is_file())
        self.assertFalse((KIT / "evals" / f"{ROLE}.no-suite.md").exists())
        cases = evals.load(KIT, ROLE)
        self.assertEqual(len(cases), 3)
        for case in cases:
            with self.subTest(case=case.id):
                self.assertEqual(case.kind, "replay")
                self.assertEqual(case.spec["lint"], ENTRY["lint"])
                self.assertEqual(case.spec["lint"], ["conform"])
                self.assertEqual(case.spec["expect_files"], [SCENE])
                self.assertEqual(sorted(recorded(case)), [SCENE])

    def test_each_tree_passes_its_lints(self):
        cases = evals.load(KIT, ROLE)
        self.assertEqual(len(cases), 3)
        for case in cases:
            for check in ("conform", "render"):
                with self.subTest(case=case.id, check=check):
                    found = lint.run(case.path / "tree", only=[check])
                    self.assertEqual(found[check].status, "PASS", lint.text(found))

    def test_the_recorded_output_passes(self):
        cases = evals.load(KIT, ROLE)
        self.assertEqual(len(cases), 3)
        for case in cases:
            with self.subTest(case=case.id):
                clean, _ = graded(case)
                self.assertTrue(clean["pass"], clean["detail"])
                self.assertEqual(clean["detail"], "lint PASS")

    def test_a_short_render_fails_the_render_lint(self):
        case = next(c for c in evals.load(KIT, ROLE) if c.id == "01-ferry-steps")
        rel = "production/ep01/Q1-2/render/render.json"
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "tree"
            shutil.copytree(case.path / "tree", tree)
            text = (tree / rel).read_text(encoding="utf-8")
            (tree / rel).write_text(text.replace('"count": 12,', '"count": 11,'), encoding="utf-8")
            found = lint.text(lint.run(tree, only=["render"])).splitlines()
        self.assertIn(f"B1 render {rel}:1 count 11, the shot has 12 frames (12 to 23)", found)

    def test_a_stray_conform_file_fails_the_lint(self):
        cases = evals.load(KIT, ROLE)
        self.assertEqual(len(cases), 3)
        for case in cases:
            for name, change, lines in STRAY:
                with self.subTest(case=case.id, cut=name):
                    cut, text = graded(case, change)
                    self.assertFalse(cut["pass"])
                    self.assertEqual(cut["detail"], "lint FAIL")
                    self.assertEqual(text.splitlines(), lines)

    def test_a_missing_scene_script_fails(self):
        cases = evals.load(KIT, ROLE)
        self.assertEqual(len(cases), 3)
        for case in cases:
            for name, change in MISSING:
                with self.subTest(case=case.id, cut=name):
                    cut, _ = graded(case, change)
                    self.assertFalse(cut["pass"])
                    self.assertEqual(cut["detail"], f"lint PASS; missing {SCENE}")

    def test_near_misses_pass(self):
        cases = evals.load(KIT, ROLE)
        self.assertEqual(len(cases), 3)
        for case in cases:
            for name, change in NEAR_MISSES:
                with self.subTest(case=case.id, near_miss=name):
                    near, _ = graded(case, change)
                    self.assertTrue(near["pass"], near["detail"])
                    self.assertEqual(near["detail"], "lint PASS")

    def test_it_runs_on_the_fake_harness(self):
        before = tree_hash(KIT / "evals")
        done, summary, found = run_eval(recorded)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "run 1: score 1.0\nnoise floor: null over 1 runs (not measured)\n")
        self.assertEqual(summary["per_run"], [{"blocked": 0, "lint_pass_rate": 1.0, "score": 1.0}])
        self.assertEqual(found, ["lint PASS"] * 3)
        self.assertEqual(tree_hash(KIT / "evals"), before)

    def test_a_withheld_output_scores_zero(self):
        done, _, found = run_eval(lambda c: {})
        self.assertEqual(done.stdout.splitlines()[0], "run 1: score 0.0", done.stderr)
        self.assertEqual(found, [f"lint PASS; missing {SCENE}"] * 3)


if __name__ == "__main__":
    unittest.main()
