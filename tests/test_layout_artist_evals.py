"""The layout artist's eval suite in kit/evals/layout-artist/ (brief section 13), run offline (ISSUE-564).

The `layout` stage has no judge, so the suite measures the lint pass rate only. Recorded outputs in
tests/fixtures/story_evals/ stand in for the role: nothing here runs a model. The role is found as a maker
of the pipeline's `production/layout` entry, not in a loop list.
The layout lint does not read requests, so a request's shape is not graded here.
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
             if e["room"] == "production" and e["stage"] == "layout")
ROLE = "layout-artist"
DIR = "production/ep01/Q1-1/layout"
OTHER = "production/ep01/Q1-2/layout"
SCENE = f"{DIR}/scene.py"
REQUEST = f"{DIR}/requests/render.json"
SHOT = "episodes/ep01/sequences/Q1/shots/Q1-1.json"
READY = {"marker": "READY", "summary": "Done.",
         "evidence": [{"criterion": "stage files", "result": "written", "command": "ls"}]}


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


def graded(case, change=None):
    """``(grade, lint text)`` of READY on the case's tree with its recorded output, the run directory then
    edited by ``change(run_dir)``."""
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        shutil.copytree(case.path / "tree", run_dir)
        shutil.copytree(FIX / ROLE / case.id, run_dir, dirs_exist_ok=True)
        if change:
            change(run_dir)
        return (evals.grade(case, READY, run_dir=run_dir),
                lint.text(lint.run(run_dir, only=ENTRY["lint"])))


def remove(path):
    def change(run_dir):
        (run_dir / path).unlink()
    return change


def move(path, to):
    def change(run_dir):
        (run_dir / to).parent.mkdir(parents=True, exist_ok=True)
        (run_dir / path).rename(run_dir / to)
    return change


def write_manifest(run_dir):
    (run_dir / DIR / "manifest.json").write_text("{}\n", encoding="utf-8")


def edit_request(edit):
    def change(run_dir):
        data = json.loads((run_dir / REQUEST).read_text(encoding="utf-8"))
        edit(data)
        (run_dir / REQUEST).write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    return change


NO_JOB = f"B1 layout {SCENE}:1 {DIR}/manifest.json is missing and no request is under {DIR}/requests/"
CUTS = (("request removed", remove(REQUEST), f"lint FAIL; missing {REQUEST}", ["layout FAIL 2 findings", NO_JOB]),
        ("request renamed", move(REQUEST, f"{DIR}/requests/render.txt"), f"lint FAIL; missing {REQUEST}",
         ["layout FAIL 2 findings", NO_JOB]),
        ("manifest written", write_manifest, "lint FAIL",
         ["layout FAIL 1 finding", f"B1 layout {SCENE}:1 {DIR}/state.json is missing"]),
        ("scene script moved", move(SCENE, f"{OTHER}/scene.py"), f"lint FAIL; missing {SCENE}",
         [f"B1 layout {OTHER}/scene.py:1 {OTHER}/manifest.json is missing and no request is under {OTHER}/requests/"]),
        ("scene script removed", remove(SCENE), f"lint PASS; missing {SCENE}", []))
NEAR_MISSES = (("no refs", edit_request(lambda d: d.update(refs=[]))),
               ("a fourth key", edit_request(lambda d: d.update(tool="unreal"))),
               ("another shot", edit_request(lambda d: d.update({"for": "ep01/Q1-2"}))))


class LayoutArtistEvalsTest(unittest.TestCase):
    def test_the_suite_is_the_layout_entrys(self):
        self.assertIn(ROLE, ENTRY["maker"])
        self.assertTrue((KIT / "evals" / ROLE / "suite.json").is_file())
        self.assertFalse((KIT / "evals" / f"{ROLE}.no-suite.md").exists())
        cases = evals.load(KIT, ROLE)
        self.assertEqual(len(cases), 3)
        for case in cases:
            with self.subTest(case=case.id):
                self.assertEqual(case.kind, "replay")
                self.assertEqual(case.spec["lint"], ENTRY["lint"])
                self.assertEqual(case.spec["lint"], ["layout"])
                self.assertEqual(case.spec["expect_files"], [SCENE, REQUEST])
                self.assertEqual(sorted(recorded(case)), sorted([SCENE, REQUEST]))

    def test_each_tree_passes_and_holds_its_locked_assets(self):
        cases = evals.load(KIT, ROLE)
        self.assertEqual(len(cases), 3)
        for case in cases:
            with self.subTest(case=case.id):
                tree = case.path / "tree"
                self.assertEqual(lint.text(lint.run(tree, only=["layout"])).splitlines()[0], "layout PASS")
                shot = json.loads((tree / SHOT).read_text(encoding="utf-8"))
                for subject in shot["characters"] + [shot["set"]]:
                    self.assertTrue((tree / "production/assets" / subject / "assets/state.json").is_file(), subject)
                grade, text = graded(case)
                self.assertTrue(grade["pass"], grade["detail"])
                self.assertEqual(grade["detail"], "lint PASS")
                self.assertEqual(text.splitlines()[0], "layout PASS")

    def test_each_cut_fails(self):
        cases = evals.load(KIT, ROLE)
        self.assertEqual(len(cases), 3)
        for case in cases:
            for name, change, detail, lines in CUTS:
                with self.subTest(case=case.id, cut=name):
                    grade, text = graded(case, change)
                    self.assertFalse(grade["pass"])
                    self.assertEqual(grade["detail"], detail)
                    for line in lines:
                        self.assertIn(line, text.splitlines())

    def test_near_misses_pass(self):
        cases = evals.load(KIT, ROLE)
        self.assertEqual(len(cases), 3)
        for case in cases:
            for name, change in NEAR_MISSES:
                with self.subTest(case=case.id, near_miss=name):
                    grade, _ = graded(case, change)
                    self.assertTrue(grade["pass"], grade["detail"])
                    self.assertEqual(grade["detail"], "lint PASS")

    def test_it_runs_on_the_fake_harness(self):
        before = tree_hash(KIT / "evals")
        done, summary, found = run_eval(recorded)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "run 1: score 1.0\nnoise floor: null over 1 runs (not measured)\n")
        self.assertEqual(summary["per_run"], [{"blocked": 0, "lint_pass_rate": 1.0, "score": 1.0}])
        self.assertEqual(found, ["lint PASS"] * 3)
        self.assertEqual(tree_hash(KIT / "evals"), before)

    def test_a_withheld_output_scores_zero(self):
        before = tree_hash(KIT / "evals")
        done, _, found = run_eval(lambda c: {})
        self.assertEqual(done.stdout.splitlines()[0], "run 1: score 0.0", done.stderr)
        self.assertEqual(found, [f"lint PASS; missing {SCENE}, {REQUEST}"] * 3)
        self.assertEqual(tree_hash(KIT / "evals"), before)


if __name__ == "__main__":
    unittest.main()
