"""The asset builder's eval suite in kit/evals/asset-builder/ (brief section 13), run offline (ISSUE-176).

The `assets` stage has no judge, so the suite measures the lint pass rate only. Recorded outputs in
tests/fixtures/story_evals/ stand in for the role: nothing here runs a model. The role is found as a maker
of the pipeline's `production/assets` entry, not in a loop list.
The assets lint does not read a request's prompt, so prompt text is not graded here.
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
             if e["room"] == "production" and e["stage"] == "assets")
ROLE = "asset-builder"
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
    return f"production/assets/{asset(case)}/assets/scene.py"


def request(case):
    return f"production/assets/{asset(case)}/assets/requests/model3d.json"


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


def edit(key, value):
    def change(case, run_dir):
        path = run_dir / request(case)
        data = json.loads(path.read_text(encoding="utf-8"))
        data[key] = value
        path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    return change


CUTS = (("01-pell", "refs media:pell-sketch", edit("refs", ["media:pell-sketch"]),
         "refs[0] media:pell-sketch has no row in media/manifest.tsv"),
        ("01-pell", "refs empty", edit("refs", []), "request names no reference image"),
        ("01-pell", "refs without media:", edit("refs", ["pell-turnaround"]), "refs[0] is not media:<id>"),
        ("01-pell", "a fourth key", edit("tool", "x"), "request is not {for, prompt, refs}"),
        ("01-pell", "for nessa", edit("for", "nessa"), "request for nessa is not for asset pell"),
        ("02-mill-yard", "refs media:mill-yard-sketch", edit("refs", ["media:mill-yard-sketch"]),
         "refs[0] media:mill-yard-sketch has no row in media/manifest.tsv"),
        ("03-nessa", "refs another subject's", edit("refs", ["media:pell-turnaround"]),
         "refs[0] media:pell-turnaround is cited by bible/style/characters/pell.md, "
         "not by bible/style/characters/nessa.md"))


class AssetBuilderEvalsTest(unittest.TestCase):
    def cases(self):
        return {case.id: case for case in evals.load(KIT, ROLE)}

    def test_the_suite_is_the_assets_entrys(self):
        self.assertIn(ROLE, ENTRY["maker"])
        self.assertEqual(ENTRY["lint"], ["assets"])
        self.assertTrue((KIT / "evals" / ROLE / "suite.json").is_file())
        self.assertFalse((KIT / "evals" / f"{ROLE}.no-suite.md").exists())
        cases = evals.load(KIT, ROLE)
        self.assertEqual([c.id for c in cases], ["01-pell", "02-mill-yard", "03-nessa"])
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
                self.assertEqual(lint.overall(lint.run(case.path / "tree", only=["assets"])), lint.PASS)
                grade, _ = graded(case)
                self.assertTrue(grade["pass"], grade["detail"])
                self.assertEqual(grade["detail"], "lint PASS")

    def test_a_removed_request_fails_the_lint(self):
        pell = self.cases()["01-pell"]
        grade, text = graded(pell, remove(request))
        self.assertFalse(grade["pass"])
        self.assertEqual(grade["detail"], f"lint FAIL; missing {request(pell)}")
        self.assertEqual(text.splitlines()[0], "assets FAIL 2 findings")
        self.assertIn("B1 assets production/assets/pell/assets/scene.py:1 production/assets/pell/assets/"
                      "manifest.json is missing and no request is under production/assets/pell/assets/"
                      "requests/", text.splitlines())

    def test_each_cut_fails_the_lint(self):
        cases = self.cases()
        self.assertEqual(len(CUTS), 7)
        for case_id, name, change, line in CUTS:
            with self.subTest(case=case_id, cut=name):
                grade, text = graded(cases[case_id], change)
                self.assertFalse(grade["pass"])
                self.assertTrue(grade["detail"].startswith("lint FAIL"), grade["detail"])
                found = [row for row in text.splitlines() if row.startswith("B")]
                self.assertEqual(len(found), 1, text)
                self.assertTrue(found[0].endswith(" " + line), found[0])

    def test_a_removed_scene_script_is_missing(self):
        pell = self.cases()["01-pell"]
        grade, _ = graded(pell, remove(scene))
        self.assertFalse(grade["pass"])
        self.assertEqual(grade["detail"], f"lint PASS; missing {scene(pell)}")

    def test_a_twin_prompt_passes(self):
        cases = evals.load(KIT, ROLE)
        self.assertEqual(len(cases), 3)
        for case in cases:
            for name, change in (("recorded", None), ("prompt x", edit("prompt", "x"))):
                with self.subTest(case=case.id, twin=name):
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
