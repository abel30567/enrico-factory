"""The showrunner's eval suite in kit/evals/showrunner/ (brief section 13), run offline (ISSUE-415).

The orchestrator of the story room, with ruling cases (rule 5: a note raised twice cannot be rejected)
and selection cases (one pitch approved, one with a Blocker on the charter). Hand-written results stand
in for the role: nothing here runs a model.
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

from kit.lib import cmd_eval, evals

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "kit"
FAKE = ROOT / "tests" / "fixtures" / "harness" / "fake-harness"
FACTORY = ROOT / "kit" / "factory"

ROLE = "showrunner"
CASES = [("01-lamplighters", "ruling"), ("02-lamp-street", "ruling"),
         ("03-kite-hill", "selection"), ("04-glass-kiln", "selection")]
PR_HOLDS = ("bible/charter.md", "bible/canon/ledger.tsv", "bible/amendments/A-1.json")
CASE_ID = re.compile(r"^[0-9]{2}-[a-z0-9]+(-[a-z0-9]+)*$")
TELLS = re.compile(r"(?i)seeded|defect|planted|clean case")
REPEAT = re.compile(r"^- (B[0-9]+): REPEAT B[0-9]+: ", re.M)
ROUND_3 = re.compile(r"^Round 3, \[([A-Z-]+)\] NOTES:$", re.M)
FLIP = {"acted on": "rejected", "rejected": "acted on"}


def tree_hash(path):
    digest = hashlib.sha256()
    for item in sorted(p for p in Path(path).rglob("*") if p.is_file()):
        digest.update(item.relative_to(path).as_posix().encode() + b"\0" + item.read_bytes())
    return digest.hexdigest()


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


def ruling(results):
    return {"marker": "RULING", "summary": "Ruled each open note.",
            "evidence": [{"criterion": note, "result": value, "command": "bible/charter.md:9"}
                         for note, value in sorted(results.items())]}


def pull_requests(case):
    return sorted(int(p.name[3:]) for p in (case.path / "tree").iterdir())


def selected(number):
    return {"marker": "SELECTED", "summary": "Selected one.", "selected": number,
            "evidence": [{"criterion": f"#{number}", "result": "selected", "command": "bible/charter.md:1"}]}


def right(case):
    """The result that passes ``case``."""
    if case.kind == "ruling":
        return ruling(case.spec["results"])
    return selected(case.spec["selected"])


def other(case):
    want = case.spec["selected"]
    return next(n for n in pull_requests(case) if n != want)


def wrong(case):
    """Every ruling note rejected, or the other pull request selected."""
    if case.kind == "ruling":
        return ruling({note: "rejected" for note in case.spec["results"]})
    return selected(other(case))


class ShowrunnerSuiteRunTest(unittest.TestCase):
    def test_the_suite_scores_one_with_the_right_results(self):
        before = tree_hash(KIT / "evals")
        done, summary = run_eval(ROLE, right)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "run 1: score 1.0\nnoise floor: null over 1 runs (not measured)\n")
        self.assertEqual(summary["per_run"], [{"blocked": 0, "decision_rate": 1.0, "score": 1.0}])
        self.assertEqual(tree_hash(KIT / "evals"), before)

    def test_each_wrong_result_fails_its_case_with_its_detail(self):
        for case in evals.load(KIT, ROLE):
            where = f"{ROLE}/{case.id}"
            spoiled = []
            if case.kind == "selection":
                want = case.spec["selected"]
                spoiled.append((selected(other(case)), f"selected #{other(case)}, better is #{want}"))
                spoiled.append((ruling({"story-editor B1": "acted on"}), "marker RULING, not SELECTED"))
            else:
                results = case.spec["results"]
                for note in sorted(results):
                    spoiled.append((ruling({**results, note: FLIP[results[note]]}), f"wrong or no ruling: {note}"))
                    left = {k: v for k, v in results.items() if k != note}
                    spoiled.append((ruling(left), f"wrong or no ruling: {note}"))
                spoiled.append((selected(3), "marker SELECTED, not RULING"))
            for result, detail in spoiled:
                out = evals.grade(case, result)
                self.assertIs(out["pass"], False, where)
                self.assertEqual(out["detail"], detail, where)
            self.assertIs(evals.grade(case, right(case))["pass"], True, where)

    def test_the_suite_scores_zero_with_every_answer_wrong(self):
        done, summary = run_eval(ROLE, wrong)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines()[0], "run 1: score 0.0")
        self.assertEqual(summary["per_run"], [{"blocked": 0, "decision_rate": 0.0, "score": 0.0}])


class ShowrunnerSuiteLoadTest(unittest.TestCase):
    def test_the_cases_and_a_repeated_note_is_acted_on(self):
        suite = evals.load(KIT, ROLE)
        self.assertEqual([(case.id, case.kind) for case in suite], CASES)
        for case in suite:
            if case.kind != "ruling":
                continue
            where = f"{ROLE}/{case.id}"
            results = case.spec["results"]
            self.assertEqual(sorted(results.values()), ["acted on", "rejected"], where)
            roles = ROUND_3.findall(case.task)
            self.assertEqual(len(roles), 1, where)
            repeated = REPEAT.findall(case.task)
            self.assertTrue(repeated, where)
            for note in repeated:
                self.assertEqual(results.get(f"{roles[0].lower()} {note}"), "acted on", f"{where} {note}")


class ShowrunnerRunDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_run_directory_is_the_pull_requests_the_task_names(self):
        for case in evals.load(KIT, ROLE):
            where = f"{ROLE}/{case.id}"
            run_dir = self.dir / case.id / "run"
            cmd_eval.build_run_dir(KIT, case, 1, run_dir)
            entries = sorted(p.name for p in run_dir.iterdir())
            named = sorted({f"pr-{n}" for n in re.findall(r"#([0-9]+)", case.task)})
            self.assertEqual(entries, named, where)
            self.assertNotIn(".factory", entries, where)
            for name in entries:
                for path in PR_HOLDS:
                    self.assertTrue((run_dir / name / path).is_file(), f"{where}/{name}/{path}")


class ShowrunnerSizesTest(unittest.TestCase):
    def test_sizes_ids_and_tasks(self):
        for case in evals.load(KIT, ROLE):
            where = f"{ROLE}/{case.id}"
            tree = [p for p in (case.path / "tree").rglob("*") if p.is_file()]
            self.assertLessEqual(len(tree), 20, where)
            for path in tree + [case.path / "task.md", case.path / "case.json"]:
                self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), 80, path)
            self.assertRegex(case.id, CASE_ID, where)
            self.assertNotRegex(case.task, TELLS, where)
            self.assertNotIn("bible/amendments/", case.task, where)


if __name__ == "__main__":
    unittest.main()
