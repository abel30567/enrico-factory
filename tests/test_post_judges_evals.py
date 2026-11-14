"""The post room's eval suites in kit/evals/ (brief section 13), run offline (ISSUE-395).

Two checkers, `charter-judge` and `packaging-judge`, with seeded and clean cases, and the
orchestrator `post-supervisor`, with ruling and selection cases. Hand-written results stand in for
the roles: nothing here runs a model.
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
FIX = ROOT / "tests" / "fixtures" / "story_evals"
FAKE = ROOT / "tests" / "fixtures" / "harness" / "fake-harness"
FACTORY = ROOT / "kit" / "factory"

CHECKERS = ("charter-judge", "packaging-judge")
SUPERVISOR = "post-supervisor"
APPROVED = {"marker": "APPROVED", "summary": "Approved."}
CHECKER_RUN = {"blocked": 0, "catch_rate": 1.0, "false_alarm_rate": 0.0, "score": 1.0}
ORCHESTRATOR_RUN = {"blocked": 0, "decision_rate": 1.0, "score": 1.0}
PR_FILES = sorted(["bible/charter.md", "research/not-ours.tsv", "brand/brand.json", "brand/voice.md",
                   "episodes/ep01/intent-packet.json", "production/ep01/conform/mix.json",
                   "rulebook/youtube.json", "rulebook/bait.json", "post/ep01/youtube/shorts/variant.json",
                   "post/ep01/youtube/shorts/cut.json"])
CASE_ID = re.compile(r"^[0-9]{2}-[a-z0-9]+(-[a-z0-9]+)*$")
TELLS = re.compile(r"(?i)seeded|defect|planted|clean case")


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


def ruling(results):
    return {"marker": "RULING", "summary": "Ruled each open note.",
            "evidence": [{"criterion": note, "result": value, "command": "bible/charter.md:9"}
                         for note, value in sorted(results.items())]}


def selected(number):
    return {"marker": "SELECTED", "summary": "Selected one.", "selected": number}


def right(role, case):
    """The result that passes ``case``."""
    if case.kind == "seeded":
        return fixture(role, case)
    if case.kind == "clean":
        return APPROVED
    if case.kind == "ruling":
        return ruling(case.spec["results"])
    return selected(case.spec["selected"])


def pull_requests(case):
    return sorted(int(p.name[3:]) for p in (case.path / "tree").iterdir())


def graded(kind, passed, marker):
    return {"kind": kind, "pass": passed, "marker": marker}


class PostSuitesRunTest(unittest.TestCase):
    def test_each_suite_scores_one_with_the_right_results(self):
        for role, per_run in [(r, CHECKER_RUN) for r in CHECKERS] + [(SUPERVISOR, ORCHESTRATOR_RUN)]:
            with self.subTest(role=role):
                before = tree_hash(KIT / "evals")
                done, summary = run_eval(role, lambda case: right(role, case))
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(done.stdout, "run 1: score 1.0\nnoise floor: null over 1 runs (not measured)\n")
                self.assertEqual(summary["per_run"], [per_run])
                self.assertEqual(tree_hash(KIT / "evals"), before)

    def test_wrong_results_score_zero_or_fail(self):
        for role in CHECKERS:
            with self.subTest(role=role):
                done, summary = run_eval(role, lambda case: APPROVED)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(done.stdout.splitlines()[0], "run 1: score 0.0")
                self.assertEqual(summary["per_run"][0]["catch_rate"], 0.0)
        flip = {"acted on": "rejected", "rejected": "acted on"}
        for case in evals.load(KIT, SUPERVISOR):
            where = f"{SUPERVISOR}/{case.id}"
            wrong = []
            if case.kind == "selection":
                want = case.spec["selected"]
                other = next(n for n in pull_requests(case) if n != want)
                wrong.append((selected(other), f"selected #{other}, better is #{want}"))
                wrong.append((ruling({"packaging-judge B1": "acted on"}), "marker RULING, not SELECTED"))
            else:
                results = case.spec["results"]
                for note in sorted(results):
                    wrong.append((ruling({**results, note: flip[results[note]]}), f"wrong or no ruling: {note}"))
                    left = {k: v for k, v in results.items() if k != note}
                    wrong.append((ruling(left), f"wrong or no ruling: {note}"))
                wrong.append((selected(1), "marker SELECTED, not RULING"))
            for result, detail in wrong:
                out = evals.grade(case, result)
                self.assertIs(out["pass"], False, where)
                self.assertEqual(out["detail"], detail, where)
            self.assertIs(evals.grade(case, right(SUPERVISOR, case))["pass"], True, where)


class PostSuitesLoadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.kit = Path(self.tmp.name) / "kit"
        shutil.copytree(KIT / "roles", self.kit / "roles")
        for role in CHECKERS + (SUPERVISOR,):
            shutil.copytree(KIT / "evals" / role, self.kit / "evals" / role)

    def tearDown(self):
        self.tmp.cleanup()

    def refused(self, role, case_id, spec):
        path = self.kit / "evals" / role / case_id / "case.json"
        shipped = path.read_bytes()
        path.write_text(json.dumps(spec), encoding="utf-8")
        with self.assertRaises(evals.EvalError) as caught:
            evals.load(self.kit, role)
        path.write_bytes(shipped)
        return str(caught.exception)

    def test_a_case_kind_or_value_that_does_not_fit_is_refused(self):
        self.assertEqual(self.refused(SUPERVISOR, "01-ember-gate", {"kind": "seeded", "catches": ["B1"]}),
                         f"{SUPERVISOR}/01-ember-gate: a seeded case does not fit a role of kind orchestrator")
        self.assertEqual(self.refused("charter-judge", "01-low-lanes",
                                      {"kind": "ruling", "results": {"charter-judge B1": "rejected"}}),
                         "charter-judge/01-low-lanes: a ruling case does not fit a role of kind checker")
        self.assertEqual(self.refused(SUPERVISOR, "01-ember-gate",
                                      {"kind": "ruling", "results": {"packaging-judge B1": "kept"}}),
                         f"{SUPERVISOR}/01-ember-gate: results is not an object of note to acted on or rejected")
        for value in (0, True):
            self.assertEqual(self.refused(SUPERVISOR, "03-soot-hill", {"kind": "selection", "selected": value}),
                             f"{SUPERVISOR}/03-soot-hill: selected is not a pull request number >= 1")

    def test_the_shipped_suites_load(self):
        for role in CHECKERS + (SUPERVISOR,):
            suite = evals.load(KIT, role)
            declared = json.loads((KIT / "evals" / role / "suite.json").read_text(encoding="utf-8"))["cases"]
            self.assertEqual(len(suite), declared, role)
        kinds = sorted(case.kind for case in evals.load(KIT, SUPERVISOR))
        self.assertEqual(kinds, ["ruling", "ruling", "selection", "selection"])


class PostSupervisorRunDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_run_directory_is_the_pull_requests_only(self):
        for case in evals.load(KIT, SUPERVISOR):
            where = f"{SUPERVISOR}/{case.id}"
            run_dir = self.dir / case.id / "run"
            cmd_eval.build_run_dir(KIT, case, 1, run_dir)
            entries = sorted(p.name for p in run_dir.iterdir())
            self.assertEqual(entries, sorted(p.name for p in (case.path / "tree").iterdir()), where)
            self.assertNotIn(".factory", entries, where)
            self.assertTrue(all(re.fullmatch(r"pr-[0-9]+", name) for name in entries), where)
            for name in entries:
                built = sorted(p.relative_to(run_dir / name).as_posix()
                               for p in (run_dir / name).rglob("*") if p.is_file())
                self.assertEqual(built, PR_FILES, f"{where}/{name}")
            named = sorted({int(n) for n in re.findall(r"#([0-9]+)", case.task)})
            self.assertEqual(named, pull_requests(case), where)

    def test_a_checker_run_directory_is_a_production(self):
        case = evals.load(KIT, "charter-judge")[0]
        run_dir = self.dir / "checker" / "run"
        cmd_eval.build_run_dir(KIT, case, 1, run_dir)
        self.assertTrue((run_dir / ".factory").is_dir())


class OrchestratorMetricsTest(unittest.TestCase):
    def test_decision_rate_is_the_score(self):
        out = evals.metrics("orchestrator", [graded("ruling", True, "RULING"),
                                             graded("selection", False, "SELECTED"),
                                             graded("ruling", False, "BLOCKED")])
        self.assertEqual(out, {"blocked": 1, "decision_rate": 0.3333, "score": 0.3333})
        with self.assertRaises(evals.EvalError) as caught:
            evals.metrics("orchestrator", [])
        self.assertEqual(str(caught.exception), "orchestrator score: decision_rate is null, no case to count")


class PostSupervisorSizesTest(unittest.TestCase):
    def test_sizes_ids_and_tasks(self):
        for case in evals.load(KIT, SUPERVISOR):
            where = f"{SUPERVISOR}/{case.id}"
            tree = [p for p in (case.path / "tree").rglob("*") if p.is_file()]
            self.assertLessEqual(len(tree), 20, where)
            for path in tree + [case.path / "task.md", case.path / "case.json"]:
                self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), 80, path)
            self.assertRegex(case.id, CASE_ID, where)
            self.assertNotRegex(case.task, TELLS, where)


if __name__ == "__main__":
    unittest.main()
