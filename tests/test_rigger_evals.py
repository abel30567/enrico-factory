"""The rigger's eval suite in kit/evals/rigger/ (brief section 13), run offline (ISSUE-176).

The `rigging` stage has no judge, so the suite measures the lint pass rate only. Recorded outputs in
tests/fixtures/story_evals/ stand in for the role: nothing here runs a model. The role is found as a maker
of the pipeline's `production/rigging` entry, not in a loop list.
The rigging lint does not read rig requests, so a request's shape is not graded here.
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
             if e["room"] == "production" and e["stage"] == "rigging")
ROLE = "rigger"
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


def asset(case):
    return case.id.split("-", 1)[1]


def scene(case):
    return f"production/assets/{asset(case)}/rigging/scene.py"


def request(case):
    return f"production/assets/{asset(case)}/rigging/requests/rig.json"


def graded(case, change=None):
    """``(grade, lint text)`` of READY on the case's tree with its recorded output, the run directory then
    edited by ``change(case, run_dir)``."""
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        shutil.copytree(case.path / "tree", run_dir)
        shutil.copytree(FIX / ROLE / case.id, run_dir, dirs_exist_ok=True)
        if change:
            change(case, run_dir)
        return (evals.grade(case, READY, run_dir=run_dir),
                lint.text(lint.run(run_dir, only=case.spec["lint"])))


def remove(path):
    def change(case, run_dir):
        (run_dir / path(case)).unlink()
    return change


def rename(case, run_dir):
    path = run_dir / request(case)
    path.rename(path.with_name("rigging.json"))


def edit(key, value):
    def change(case, run_dir):
        path = run_dir / request(case)
        data = json.loads(path.read_text(encoding="utf-8"))
        data[key] = value(case) if callable(value) else value
        path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    return change


NEXT = {"tamsin": "orrin", "orrin": "juno", "juno": "tamsin"}
NEAR_MISSES = (("refs the next case's asset", edit("refs", lambda c: [f"asset:{NEXT[asset(c)]}"])),
               ("a fourth key", edit("extra", "x")))


class RiggerEvalsTest(unittest.TestCase):
    def tamsin(self):
        return next(c for c in evals.load(KIT, ROLE) if c.id == "01-tamsin")

    def test_the_suite_is_the_rigging_entrys(self):
        self.assertIn(ROLE, ENTRY["maker"])
        self.assertEqual(ENTRY["lint"], ["rigging"])
        self.assertTrue((KIT / "evals" / ROLE / "suite.json").is_file())
        self.assertFalse((KIT / "evals" / f"{ROLE}.no-suite.md").exists())
        cases = evals.load(KIT, ROLE)
        self.assertEqual([c.id for c in cases], ["01-tamsin", "02-orrin", "03-juno"])
        for case in cases:
            with self.subTest(case=case.id):
                self.assertEqual(case.kind, "replay")
                self.assertEqual(case.spec["lint"], ENTRY["lint"])
                self.assertEqual(case.spec["expect_files"], [scene(case), request(case)])
                self.assertEqual(sorted(recorded(case)), sorted([scene(case), request(case)]))

    def test_each_tree_and_its_recorded_output_pass(self):
        cases = evals.load(KIT, ROLE)
        self.assertEqual(len(cases), 3)
        for case in cases:
            with self.subTest(case=case.id):
                for only in (["rigging"], ["assets"]):
                    self.assertEqual(lint.overall(lint.run(case.path / "tree", only=only)), lint.PASS, only)
                grade, _ = graded(case)
                self.assertTrue(grade["pass"], grade["detail"])
                self.assertEqual(grade["detail"], "lint PASS")

    def test_a_wrong_height_fails_the_assets_lint(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "tree"
            shutil.copytree(self.tamsin().path / "tree", tree)
            path = tree / "production/assets/tamsin/assets/state.json"
            path.write_text(path.read_text(encoding="utf-8").replace('"height_m": 1.4,', '"height_m": 2.0,'),
                            encoding="utf-8")
            self.assertEqual(lint.blockers(lint.run(tree, only=["assets"])),
                             ["B1 assets production/assets/tamsin/assets/state.json:1 height_m 2 is not within "
                              "10% of 1.4 m (bible/canon/ledger.tsv:2)"])

    def test_a_removed_request_fails_the_lint(self):
        tamsin = self.tamsin()
        grade, text = graded(tamsin, remove(request))
        self.assertFalse(grade["pass"])
        self.assertEqual(grade["detail"], f"lint FAIL; missing {request(tamsin)}")
        self.assertEqual([row for row in text.splitlines() if row.startswith("B")],
                         ["B1 rigging production/assets/tamsin/rigging/scene.py:1 production/assets/tamsin/"
                          "rigging/state.json is missing and no request is under production/assets/tamsin/"
                          "rigging/requests/"])

    def test_a_misnamed_request_or_a_removed_scene_script_is_missing(self):
        tamsin = self.tamsin()
        for name, change, missing in (("request renamed", rename, request(tamsin)),
                                      ("scene script removed", remove(scene), scene(tamsin))):
            with self.subTest(cut=name):
                grade, _ = graded(tamsin, change)
                self.assertFalse(grade["pass"])
                self.assertEqual(grade["detail"], f"lint PASS; missing {missing}")

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
        cases = evals.load(KIT, ROLE)
        self.assertEqual(len(cases), 3)
        self.assertEqual(found, [f"lint PASS; missing {scene(c)}, {request(c)}" for c in cases])
        self.assertEqual(tree_hash(KIT / "evals"), before)


if __name__ == "__main__":
    unittest.main()
