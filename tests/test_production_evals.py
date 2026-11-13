"""The production room's checker eval suites in kit/evals/ (brief section 13), run offline (ISSUE-152).

Hand-written results stand in for the roles: nothing here runs a model. No test names a role or a count.
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

ROLES = tuple(sorted(p.stem for p in (KIT / "roles").glob("*.md")
                     if evals.role_file(KIT, p.stem)[0].get("room") == "production"
                     and evals.role_file(KIT, p.stem)[0].get("kind") == "checker"
                     and (KIT / "evals" / p.stem).is_dir()))
APPROVED = {"marker": "APPROVED", "summary": "Approved."}
NOT_FETCHED = ("The files of `outputs` in `{}` are not in `review/` for this review: judge the files in git. "
               "Approve it, or give notes.")


def tree_hash(path):
    digest = hashlib.sha256()
    for item in sorted(p for p in Path(path).rglob("*") if p.is_file()):
        digest.update(item.relative_to(path).as_posix().encode() + b"\0" + item.read_bytes())
    return digest.hexdigest()


def fixture(role, case):
    return json.loads((FIX / role / f"{case.id}.json").read_text(encoding="utf-8"))


def run_eval(role, answer):
    """``(completed process, summary)`` of ``factory eval role`` on the fake harness, ``answer(case)``
    the result of each case."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        bin_dir, root, out = tmp / "bin", tmp / "root", tmp / "out"
        bin_dir.mkdir()
        root.mkdir()
        shutil.copy(FAKE, bin_dir / "fake-harness")
        os.chmod(bin_dir / "fake-harness", 0o755)
        steps = [{"match": f"eval {re.escape(case.id)}(?![\\w-])", "result": answer(case)}
                 for case in evals.load(KIT, role)]
        (bin_dir / "fake-harness.json").write_text(json.dumps(steps), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "FACTORY_LIVE"}
        env["FACTORY_FAKE_HARNESS"] = str(bin_dir / "fake-harness")
        done = subprocess.run([sys.executable, str(FACTORY), "eval", role, "--harness", "fake",
                               "--root", str(root), "--out", str(out)],
                              env=env, cwd=ROOT, capture_output=True, text=True, timeout=300)
        summary = out / "summary.json"
        return done, json.loads(summary.read_text(encoding="utf-8")) if summary.is_file() else None


def task_messages(case_dir):
    """What the ``task.md`` of the case in ``case_dir`` lacks: two lines, the second saying that the
    outputs of the tree's one manifest are not fetched."""
    where = f"{case_dir.parent.name}/{case_dir.name}"
    manifests = sorted(p.relative_to(case_dir / "tree").as_posix()
                       for p in (case_dir / "tree").rglob("manifest.json"))
    if len(manifests) != 1:
        return [f"{where}: {len(manifests)} manifest.json under tree/, not 1"]
    lines = (case_dir / "task.md").read_text(encoding="utf-8").splitlines()
    if len(lines) != 2:
        return [f"{where}: task.md is {len(lines)} lines, not 2"]
    if lines[1] != NOT_FETCHED.format(manifests[0]):
        return [f"{where}: line 2 of task.md does not say the outputs of {manifests[0]} are not fetched"]
    return []


class ProductionEvalsTest(unittest.TestCase):
    def test_each_suite_runs_on_the_fake_harness(self):
        self.assertTrue(ROLES)
        for role in ROLES:
            with self.subTest(role=role):
                before = tree_hash(KIT / "evals")
                done, summary = run_eval(role, lambda c: APPROVED if c.kind == "clean" else fixture(role, c))
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(done.stdout, "run 1: score 1.0\nnoise floor: null over 1 runs (not measured)\n")
                self.assertEqual(summary["per_run"],
                                 [{"blocked": 0, "catch_rate": 1.0, "false_alarm_rate": 0.0, "score": 1.0}])
                self.assertEqual(tree_hash(KIT / "evals"), before)

    def test_a_checker_that_approves_everything_scores_zero(self):
        self.assertTrue(ROLES)
        for role in ROLES:
            with self.subTest(role=role):
                done, summary = run_eval(role, lambda c: APPROVED)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(done.stdout.splitlines()[0], "run 1: score 0.0")
                self.assertEqual(summary["per_run"][0]["catch_rate"], 0.0)

    def test_tasks_say_review_is_not_fetched(self):
        self.assertTrue(ROLES)
        for role in ROLES:
            with self.subTest(role=role):
                for case in evals.load(KIT, role):
                    self.assertEqual(task_messages(case.path), [])
                first = evals.load(KIT, role)[0]
                with tempfile.TemporaryDirectory() as tmp:
                    copy = Path(tmp) / role / first.id
                    shutil.copytree(first.path, copy)
                    task = copy / "task.md"
                    text = task.read_text(encoding="utf-8")
                    self.assertIn("are not in", text)
                    task.write_text(text.replace("are not in", "are in"), encoding="utf-8")
                    self.assertEqual(len(task_messages(copy)), 1)


if __name__ == "__main__":
    unittest.main()
