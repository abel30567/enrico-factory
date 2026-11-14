"""The devil's advocate's and the researcher's eval suites in kit/evals/ (brief section 13), run offline
(ISSUE-426).

Recorded results in tests/fixtures/story_evals/ stand in for the roles: nothing here runs a model. The
devil's advocate is a checker: its catch rate on seeded rulings and its false-alarm rate on clean ones.
The researcher's cases are the trend-scout's verdict cases, graded on the notes of its trend cards.
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
OLD = ROOT / "tests" / "fixtures" / "trend_evals" / "researcher" / "01-quay-lanterns"
FAKE = ROOT / "tests" / "fixtures" / "harness" / "fake-harness"
FACTORY = ROOT / "kit" / "factory"

READY = {"marker": "READY", "summary": "Done.",
         "evidence": [{"criterion": "stage files", "result": "written", "command": "ls"}]}
APPROVED = {"marker": "APPROVED", "summary": "Approved."}
DA_IDS = ("01-net-loft", "02-lamp-row", "03-slack-tide", "04-pole-hook", "05-wet-steps", "06-oil-store",
          "07-harbour-wall", "08-tar-shed", "09-low-light")
DA_LINTS = ["canon", "media", "provenance", "references", "structure"]
RS_IDS = ("01-quay-lanterns", "02-net-mending", "03-rock-pools")
CARD = re.compile(r"^research/trends/[a-z0-9-]+\.youtube\.tsv$")
FLIP = {"usable: ": "not usable: ", "not usable: ": "usable: "}
NOISE = "noise floor: null over 1 runs (not measured)\n"


def tree_hash(path):
    digest = hashlib.sha256()
    for item in sorted(p for p in Path(path).rglob("*") if p.is_file()):
        digest.update(item.relative_to(path).as_posix().encode() + b"\0" + item.read_bytes())
    return digest.hexdigest()


def run_eval(role, step):
    """``(completed process, summary, details)`` of ``factory eval role`` on the fake harness, each case's
    step being ``step(case)``; ``details`` the graded detail of each result."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        bin_dir, root, out = tmp / "bin", tmp / "root", tmp / "out"
        bin_dir.mkdir()
        root.mkdir()
        shutil.copy(FAKE, bin_dir / "fake-harness")
        os.chmod(bin_dir / "fake-harness", 0o755)
        steps = [{"match": f"eval {re.escape(case.id)}(?![\\w-])", **step(case)}
                 for case in evals.load(KIT, role)]
        (bin_dir / "fake-harness.json").write_text(json.dumps(steps), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "FACTORY_LIVE"}
        env["FACTORY_FAKE_HARNESS"] = str(bin_dir / "fake-harness")
        done = subprocess.run([sys.executable, str(FACTORY), "eval", role, "--harness", "fake",
                               "--root", str(root), "--out", str(out)],
                              env=env, cwd=ROOT, capture_output=True, text=True, timeout=300)
        summary, results = out / "summary.json", out / "results.jsonl"
        return (done, json.loads(summary.read_text(encoding="utf-8")) if summary.is_file() else None,
                [json.loads(line)["detail"] for line in results.read_text(encoding="utf-8").splitlines()]
                if results.is_file() else [])


def recorded_result(case):
    """The recorded result of a seeded case, APPROVED for a clean one."""
    if case.kind == "seeded":
        return json.loads((FIX / "devils-advocate" / f"{case.id}.json").read_text(encoding="utf-8"))
    return APPROVED


def cards(case):
    """The researcher's recorded trend cards of the case: ``{path: text}``."""
    output = FIX / "researcher" / case.id
    return {p.relative_to(output).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(output.rglob("*")) if p.is_file()}


def flipped(text, vid):
    """``text`` of a card with the verdict of the first row of ``vid`` turned the other way."""
    lines = text.split("\n")
    for index, line in enumerate(lines):
        fields = line.split("\t")
        if len(fields) == 6 and fields[2] == vid:
            for word, other in FLIP.items():
                if fields[5].startswith(word):
                    fields[5] = other + fields[5][len(word):]
                    lines[index] = "\t".join(fields)
                    return "\n".join(lines)
    raise AssertionError(vid)


def grade_cards(case, written):
    """``(pass, detail)`` of READY on the case's tree with ``written`` laid over it."""
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        shutil.copytree(case.path / "tree", run_dir)
        for path, text in written.items():
            (run_dir / path).parent.mkdir(parents=True, exist_ok=True)
            (run_dir / path).write_text(text, encoding="utf-8")
        graded = evals.grade(case, READY, run_dir=run_dir)
        return graded["pass"], graded["detail"]


class DevilsAdvocateTest(unittest.TestCase):
    def test_suite_shape(self):
        self.assertFalse((KIT / "evals" / "devils-advocate.no-suite.md").exists())
        cases = evals.load(KIT, "devils-advocate")
        self.assertEqual(tuple(c.id for c in cases), DA_IDS)
        self.assertEqual("".join(c.kind[0] for c in cases), "scsscsscs")
        tree = cases[0].path / "tree"
        for case in cases:
            self.assertEqual(tree_hash(case.path / "tree"), tree_hash(tree), case.id)
        self.assertEqual(lint.overall(lint.run(tree, only=DA_LINTS)), lint.PASS)
        stems = sorted(p.stem for p in (FIX / "devils-advocate").glob("*.json"))
        self.assertEqual(stems, [c.id for c in cases if c.kind == "seeded"])

    def test_a_reviewer_that_catches_scores_above_one_that_approves(self):
        done, summary, _ = run_eval("devils-advocate", lambda c: {"result": recorded_result(c)})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "run 1: score 1.0\n" + NOISE)
        self.assertEqual(summary["per_run"],
                         [{"blocked": 0, "catch_rate": 1.0, "false_alarm_rate": 0.0, "score": 1.0}])
        done, summary, _ = run_eval("devils-advocate", lambda c: {"result": APPROVED})
        self.assertEqual(done.stdout, "run 1: score 0.0\n" + NOISE, done.stderr)
        self.assertEqual(summary["per_run"],
                         [{"blocked": 0, "catch_rate": 0.0, "false_alarm_rate": 0.0, "score": 0.0}])


class ResearcherTest(unittest.TestCase):
    def test_suite_is_the_trend_scout_cases(self):
        self.assertFalse((KIT / "evals" / "researcher.no-suite.md").exists())
        cases = evals.load(KIT, "researcher")
        self.assertEqual(tuple(c.id for c in cases), RS_IDS)
        for case in cases:
            self.assertEqual(tree_hash(case.path), tree_hash(KIT / "evals" / "trend-scout" / case.id), case.id)
            self.assertEqual(set(case.spec["verdicts"].values()), {"usable", "not usable"}, case.id)
            [path] = cards(case)
            self.assertRegex(path, CARD, case.id)
        self.assertEqual(tree_hash(FIX / "researcher" / RS_IDS[0] / "research"), tree_hash(OLD / "research"))

    def test_a_flipped_verdict_fails(self):
        cases = evals.load(KIT, "researcher")
        self.assertEqual(len(cases), 3)
        for case in cases:
            written = cards(case)
            self.assertEqual(grade_cards(case, written), (True, "every verdict right"), case.id)
            [(path, text)] = written.items()
            for vid in sorted(case.spec["verdicts"]):
                with self.subTest(case=case.id, video=vid):
                    self.assertEqual(grade_cards(case, {path: flipped(text, vid)}),
                                     (False, f"wrong or no verdict: {vid}"))

    def test_factory_eval(self):
        done, summary, details = run_eval("researcher", lambda c: {"files": cards(c), "result": READY})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "run 1: score 1.0\n" + NOISE)
        self.assertEqual(summary["per_run"], [{"blocked": 0, "score": 1.0, "verdict_rate": 1.0}])
        self.assertEqual(details, ["every verdict right"] * 3)
        done, _, details = run_eval("researcher", lambda c: {"files": {}, "result": READY})
        self.assertEqual(done.stdout.splitlines()[0], "run 1: score 0.0", done.stderr)
        self.assertEqual(details, ["wrong or no verdict: yt-fold, yt-trip, yt-wick",
                                   "wrong or no verdict: yt-flat, yt-hum, yt-knot",
                                   "wrong or no verdict: yt-crab, yt-prank, yt-shell"])


if __name__ == "__main__":
    unittest.main()
