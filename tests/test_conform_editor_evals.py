"""The conform editor's eval suite in kit/evals/conform-editor/ (brief section 13), run offline (ISSUE-414).

The `conform` stage has no judge, so the suite measures the lint pass rate only. Recorded outputs in
tests/fixtures/story_evals/ stand in for the role: nothing here runs a model. The role is found as a maker
of the pipeline's `production/conform` entry, not in a loop list.
The conform lint does not read caption text, so caption text is not graded here.
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

from kit.lib import evals

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "kit"
FIX = ROOT / "tests" / "fixtures" / "story_evals"
FAKE = ROOT / "tests" / "fixtures" / "harness" / "fake-harness"
FACTORY = ROOT / "kit" / "factory"

ENTRY = next(e for e in json.loads((KIT / "pipeline.json").read_text(encoding="utf-8"))
             if e["room"] == "production" and e["stage"] == "conform")
ROLE = "conform-editor"
EDIT_LIST = "production/ep01/conform/edit-list.json"
CAPTIONS = "production/ep01/conform/captions.json"
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


def drop_last(data):
    data["events"].pop()


def set_duration(data):
    data["events"][0]["duration"] = 1.0


def swap_events(data):
    data["events"][0], data["events"][1] = data["events"][1], data["events"][0]


def drop_last_cue(data):
    data.pop()


def move_cue(frames):
    def change(data):
        data[0]["start"] += frames
        data[0]["end"] += frames
    return change


def set_text(data):
    data[0]["text"] = "Other words."


CUTS = (("a dropped event", EDIT_LIST, drop_last), ("a duration changed", EDIT_LIST, set_duration),
        ("two events swapped", EDIT_LIST, swap_events), ("a dropped cue", CAPTIONS, drop_last_cue),
        ("a cue moved 3 frames", CAPTIONS, move_cue(3)))
NEAR_MISSES = (("a cue moved 2 frames", CAPTIONS, move_cue(2)), ("a cue's text changed", CAPTIONS, set_text))


def graded(case, path=None, change=None):
    """The grade of READY on the case's tree with its recorded output, ``path`` changed by ``change``."""
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        shutil.copytree(case.path / "tree", run_dir)
        shutil.copytree(FIX / ROLE / case.id, run_dir, dirs_exist_ok=True)
        if path:
            data = json.loads((run_dir / path).read_text(encoding="utf-8"))
            change(data)
            (run_dir / path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return evals.grade(case, READY, run_dir=run_dir)


class ConformEditorEvalsTest(unittest.TestCase):
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
                self.assertEqual(case.spec["expect_files"], [EDIT_LIST, CAPTIONS])
                self.assertEqual(sorted(recorded(case)), sorted([EDIT_LIST, CAPTIONS]))

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
        self.assertEqual(found, [f"lint FAIL; missing {EDIT_LIST}, {CAPTIONS}"] * 3)

    def test_each_cut_fails_the_lint(self):
        for case in evals.load(KIT, ROLE):
            clean = graded(case)
            self.assertTrue(clean["pass"], f"{case.id}: {clean['detail']}")
            for name, path, change in CUTS:
                with self.subTest(case=case.id, cut=name):
                    cut = graded(case, path, change)
                    self.assertFalse(cut["pass"])
                    self.assertTrue(cut["detail"].startswith("lint FAIL"), cut["detail"])

    def test_near_misses_pass(self):
        for case in evals.load(KIT, ROLE):
            for name, path, change in NEAR_MISSES:
                with self.subTest(case=case.id, near_miss=name):
                    near = graded(case, path, change)
                    self.assertTrue(near["pass"], near["detail"])
                    self.assertEqual(near["detail"], "lint PASS")


if __name__ == "__main__":
    unittest.main()
