"""Tests for the eval case kind `verdict` (kit/lib/evals.py): the two trend cases of `researcher` and
`trend-scout` under tests/fixtures/trend_evals/, their grade, their spec problems and their metrics.

The cases are fixtures, not the kit's suites: each test copies `kit/` to a temporary directory and the
role's fixture directory to `<copy>/evals/<role>/`, in place of the suite `kit/evals/trend-scout/`.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from kit.lib import evals, lint  # noqa: E402
from kit.lib.evals import EvalError  # noqa: E402
from test_story_evals import TELLS  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "trend_evals"
FAKE = ROOT / "tests" / "fixtures" / "harness" / "fake-harness"
ROLES = ("researcher", "trend-scout")
CASE = "01-quay-lanterns"
CARD = "research/trends/quay-lanterns.youtube.tsv"
IDS = ("yt-fold", "yt-trip", "yt-wick")
EVIDENCE = [{"criterion": "each video judged", "result": "pass", "command": "read .snapshot/youtube.ranking.txt"}]
READY = {"marker": "READY", "summary": "The quay-lanterns card is noted.", "evidence": EVIDENCE}
FLIP = {"usable: ": "not usable: ", "not usable: ": "usable: "}


def temp_dir(test):
    path = Path(os.path.realpath(tempfile.mkdtemp()))
    test.addCleanup(shutil.rmtree, path)
    return path


def kit_copy(test, roles=ROLES):
    """A copy of `kit/` whose `evals/<role>/` is a copy of the role's fixture directory, in place of
    the role's own suite where the kit holds one."""
    kit = temp_dir(test) / "kit"
    shutil.copytree(ROOT / "kit", kit, ignore=shutil.ignore_patterns("__pycache__"))
    for role in roles:
        shutil.rmtree(kit / "evals" / role, ignore_errors=True)
        shutil.copytree(FIX / role, kit / "evals" / role)
    return kit


def case_of(kit, role):
    [case] = evals.load(kit, role)
    return case


def card_text():
    return (FIX / "researcher" / CASE / CARD).read_text(encoding="utf-8")


def scout_result():
    return json.loads((FIX / "trend-scout" / f"{CASE}.json").read_text(encoding="utf-8"))


def flip(note, vid=None):
    """``note`` with its verdict turned the other way."""
    head = f"{vid} " if vid else ""
    for word, other in FLIP.items():
        if note.startswith(head + word):
            return head + other + note[len(head + word):]
    raise AssertionError(note)


class CasesLoad(unittest.TestCase):
    """Behaviour 6."""

    def test_one_verdict_case_each(self):
        kit = kit_copy(self)
        for role in ROLES:
            case = case_of(kit, role)
            self.assertEqual((case.id, case.kind), (CASE, "verdict"), role)
            self.assertEqual(case.spec["verdicts"],
                             {"yt-fold": "usable", "yt-trip": "not usable", "yt-wick": "not usable"})
            tree = case.path / "tree"
            results = lint.run(tree, only=["provenance", "references", "media"])
            self.assertEqual(lint.overall(results), lint.PASS, role)
            files = [p for p in tree.rglob("*") if p.is_file()]
            self.assertLessEqual(len(files), 20, role)
            for path in files:
                self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), 80, path)
            self.assertNotRegex(case.task, TELLS, role)


class VerdictsGrade(unittest.TestCase):
    """Behaviour 7."""

    def setUp(self):
        self.kit = kit_copy(self)
        self.researcher = case_of(self.kit, "researcher")
        self.scout = case_of(self.kit, "trend-scout")

    def run_dir(self, card):
        """The researcher case's tree with ``card`` (None: the tree's own) as its trend card."""
        run = temp_dir(self) / "run"
        shutil.copytree(self.researcher.path / "tree", run)
        if card is not None:
            (run / CARD).write_text(card, encoding="utf-8")
        return run

    def grade_card(self, card):
        return evals.grade(self.researcher, READY, run_dir=self.run_dir(card))

    def flip_card(self, vid):
        lines = card_text().split("\n")
        for index, line in enumerate(lines):
            fields = line.split("\t")
            if len(fields) == 6 and fields[2] == vid and fields[5]:
                fields[5] = flip(fields[5])
                lines[index] = "\t".join(fields)
        return "\n".join(lines)

    def test_recorded_results_pass_and_seeded_fail(self):
        graded = self.grade_card(card_text())
        self.assertEqual((graded["pass"], graded["detail"]), (True, "every verdict right"))
        graded = evals.grade(self.scout, scout_result())
        self.assertEqual((graded["pass"], graded["detail"]), (True, "every verdict right"))
        # Seeded, one at a time.
        for vid in IDS:
            graded = self.grade_card(self.flip_card(vid))
            self.assertEqual((graded["pass"], graded["detail"]), (False, f"wrong or no verdict: {vid}"))
            result = scout_result()
            for note in result["notes"]:
                if note["text"].startswith(vid + " "):
                    note["text"] = flip(note["text"], vid)
            graded = evals.grade(self.scout, result)
            self.assertEqual((graded["pass"], graded["detail"]), (False, f"wrong or no verdict: {vid}"))
        graded = self.grade_card(None)
        self.assertEqual((graded["pass"], graded["detail"]),
                         (False, "wrong or no verdict: yt-fold, yt-trip, yt-wick"))
        card = card_text()
        lines = card.split("\n")
        rows = [i for i, line in enumerate(lines) if line.split("\t")[2:3] == ["yt-fold"]]
        fields = lines[rows[1]].split("\t")
        fields[5] = "not usable: x"
        lines[rows[1]] = "\t".join(fields)
        graded = self.grade_card("\n".join(lines))
        self.assertEqual((graded["pass"], graded["detail"]), (False, "wrong or no verdict: yt-fold"))
        result = {**scout_result(), "marker": "NOTES"}
        self.assertEqual(evals._grade_verdict(self.scout, result, None), (False, "marker NOTES, not SYMPTOMS"))
        self.assertEqual(evals.grade(self.researcher, {**READY, "marker": "BLOCKED",
                                                       "blocked": {"missing": "m", "tried": "t",
                                                                   "resume_from": "r"}},
                                     run_dir=self.run_dir(card))["detail"], "marker BLOCKED, not READY")


class SpecProblems(unittest.TestCase):
    """Behaviour 8."""

    def test_verdicts_and_kind(self):
        for verdicts in ({}, {"yt-a": "maybe"}, {"": "usable"}, ["yt-a"]):
            for role in ROLES:
                kit = kit_copy(self, roles=(role,))
                path = kit / "evals" / role / CASE / "case.json"
                path.write_text(json.dumps({"kind": "verdict", "verdicts": verdicts}))
                with self.assertRaises(EvalError) as caught:
                    evals.load(kit, role)
                self.assertEqual(str(caught.exception),
                                 f"{role}/{CASE}: verdicts is not an object of video id to usable or not usable")
        kit = kit_copy(self, roles=())
        role = "canon-reviewer"
        shutil.rmtree(kit / "evals" / role)
        shutil.copytree(FIX / "researcher" / CASE, kit / "evals" / role / CASE)
        with self.assertRaises(EvalError) as caught:
            evals.load(kit, role)
        self.assertEqual(str(caught.exception), f"{role}/{CASE}: a verdict case does not fit a role of kind checker")


class Metrics(unittest.TestCase):
    """Behaviour 9."""

    def test_rates_and_factory_eval(self):
        self.assertEqual(evals.metrics("analyst", [{"kind": "verdict", "pass": True, "marker": "SYMPTOMS"}]),
                         {"blocked": 0, "score": 1.0, "verdict_rate": 1.0})
        maker = [{"kind": "replay", "pass": True, "marker": "READY"},
                 {"kind": "verdict", "pass": False, "marker": "READY"}]
        self.assertEqual(evals.metrics("maker", maker),
                         {"blocked": 0, "lint_pass_rate": 1.0, "score": 1.0, "verdict_rate": 0.0})
        self.assertEqual(evals.metrics("maker", [{"kind": "verdict", "pass": True, "marker": "READY"}]),
                         {"blocked": 0, "score": 1.0, "verdict_rate": 1.0})
        with self.assertRaises(EvalError) as caught:
            evals.metrics("analyst", [])
        self.assertEqual(str(caught.exception), "analyst score: verdict_rate is null, no case to count")
        answers = {"researcher": {"result": READY, "files": {CARD: card_text()}},
                   "trend-scout": {"result": scout_result()}}
        for role in ROLES:
            tmp = temp_dir(self)
            kit = kit_copy(self, roles=(role,))
            (tmp / "bin").mkdir()
            (tmp / "root").mkdir()
            shutil.copy2(FAKE, tmp / "bin" / "fake-harness")
            steps = [{"match": rf"eval {CASE}(?![\w-])", **answers[role]}]
            (tmp / "bin" / "fake-harness.json").write_text(json.dumps(steps))
            env = {**os.environ, "FACTORY_FAKE_HARNESS": str(tmp / "bin" / "fake-harness")}
            done = subprocess.run([sys.executable, str(kit / "factory"), "eval", role, "--root", str(tmp / "root"),
                                   "--out", str(tmp / "out")], capture_output=True, text=True, timeout=120,
                                  env=env, stdin=subprocess.DEVNULL)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertIn("run 1: score 1.0", done.stdout.splitlines(), role)
            [line] = (tmp / "out" / "results.jsonl").read_text().splitlines()
            self.assertEqual(json.loads(line)["detail"], "every verdict right", role)


if __name__ == "__main__":
    unittest.main()
