"""The analyst `trend-scout`'s eval suite in kit/evals/trend-scout/ (brief section 13), run offline (ISSUE-416).

Three verdict cases, each a snapshot of one topic's YouTube reads. Hand-written results stand in for
the role: nothing here runs a model.
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from kit.lib import evals, lint  # noqa: E402
from test_story_evals import CASE_ID, TELLS, TREES  # noqa: E402

KIT = ROOT / "kit"
FIX = ROOT / "tests" / "fixtures" / "story_evals"
FAKE = ROOT / "tests" / "fixtures" / "harness" / "fake-harness"
FACTORY = ROOT / "kit" / "factory"
ROLE = "trend-scout"
IDS = ("01-quay-lanterns", "02-net-mending", "03-rock-pools")
FLIP = {"usable: ": "not usable: ", "not usable: ": "usable: "}


def tree_hash(path):
    digest = hashlib.sha256()
    for item in sorted(p for p in Path(path).rglob("*") if p.is_file()):
        digest.update(item.relative_to(path).as_posix().encode() + b"\0" + item.read_bytes())
    return digest.hexdigest()


def cases():
    return evals.load(KIT, ROLE)


def recorded(case):
    return json.loads((FIX / ROLE / f"{case.id}.json").read_text(encoding="utf-8"))


def flip(text):
    """``text``, a note's text, with its verdict turned the other way."""
    vid, rest = text.split(" ", 1)
    for word, other in FLIP.items():
        if rest.startswith(word):
            return f"{vid} {other}{rest[len(word):]}"
    raise AssertionError(text)


def flipped(case, vid):
    """The recorded result of ``case`` with the verdict on ``vid`` (None: on every video) turned."""
    result = recorded(case)
    for note in result["notes"]:
        if vid is None or note["text"].startswith(vid + " "):
            note["text"] = flip(note["text"])
    return result


def run_eval(answer):
    """``(completed process, summary)`` of ``factory eval trend-scout`` on the fake harness,
    ``answer(case)`` the result of each case."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        bin_dir, root, out = tmp / "bin", tmp / "root", tmp / "out"
        bin_dir.mkdir()
        root.mkdir()
        shutil.copy(FAKE, bin_dir / "fake-harness")
        os.chmod(bin_dir / "fake-harness", 0o755)
        steps = [{"match": f"eval {re.escape(case.id)}(?![\\w-])", "result": answer(case)} for case in cases()]
        (bin_dir / "fake-harness.json").write_text(json.dumps(steps), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "FACTORY_LIVE"}
        env["FACTORY_FAKE_HARNESS"] = str(bin_dir / "fake-harness")
        done = subprocess.run([sys.executable, str(FACTORY), "eval", ROLE, "--harness", "fake",
                               "--root", str(root), "--out", str(out)],
                              env=env, cwd=ROOT, capture_output=True, text=True, timeout=300)
        summary = out / "summary.json"
        return done, json.loads(summary.read_text(encoding="utf-8")) if summary.is_file() else None


class TrendScoutSuite(unittest.TestCase):
    def test_shape_and_trees(self):
        """Behaviour 1 and 2."""
        self.assertFalse((KIT / "evals" / f"{ROLE}.no-suite.md").exists())
        self.assertEqual(len(cases()), 3)
        self.assertEqual(tuple(case.id for case in cases()), IDS)
        suite = (KIT / "evals" / ROLE / "suite.json").read_text(encoding="utf-8")
        self.assertEqual(json.loads(suite), {"cases": 3})
        self.assertEqual(sorted(p.name for p in (FIX / ROLE).iterdir()), [f"{cid}.json" for cid in IDS])
        for case in cases():
            self.assertEqual((case.kind, case.role_kind), ("verdict", "analyst"), case.id)
            self.assertRegex(case.id, CASE_ID)
            self.assertNotRegex(case.id, TELLS)
            self.assertNotRegex(case.task, TELLS, case.id)
            self.assertEqual([p.name for p in case.path.iterdir() if p.is_dir() and p.name in TREES], ["tree"])
            tree = case.path / "tree"
            self.assertLessEqual(len([p for p in tree.rglob("*") if p.is_file()]), 20, case.id)
            ids = sorted(video["id"] for read in sorted((tree / ".snapshot").glob("*.read.json"))
                         for video in json.loads(read.read_text(encoding="utf-8"))["videos"])
            self.assertEqual(sorted(case.spec["verdicts"]), ids, case.id)
            results = lint.run(tree, only=["provenance", "references", "media"])
            self.assertEqual(lint.overall(results), lint.PASS, case.id)

    def test_grade(self):
        """Behaviour 3."""
        self.assertEqual(len(cases()), 3)
        for case in cases():
            graded = evals.grade(case, recorded(case))
            self.assertEqual((graded["pass"], graded["detail"]), (True, "every verdict right"), case.id)
            # Seeded, one video at a time.
            for vid in sorted(case.spec["verdicts"]):
                graded = evals.grade(case, flipped(case, vid))
                self.assertEqual((graded["pass"], graded["detail"]), (False, f"wrong or no verdict: {vid}"))
            result = recorded(case)
            first = result["notes"].pop(0)["text"].split(" ", 1)[0]
            graded = evals.grade(case, result)
            self.assertEqual((graded["pass"], graded["detail"]), (False, f"wrong or no verdict: {first}"))
            result = {**recorded(case), "marker": "NOTES"}
            graded = evals.grade(case, result)
            self.assertEqual((graded["pass"], graded["detail"]), (False, "invalid result: 1 problem(s)"))
            self.assertEqual(evals._grade_verdict(case, result, None), (False, "marker NOTES, not SYMPTOMS"))

    def test_factory_eval(self):
        """Behaviour 4."""
        self.assertEqual(len(cases()), 3)
        before = tree_hash(KIT / "evals")
        done, summary = run_eval(recorded)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout.splitlines(),
                         ["run 1: score 1.0", "noise floor: null over 1 runs (not measured)"])
        self.assertEqual(summary["per_run"], [{"blocked": 0, "score": 1.0, "verdict_rate": 1.0}])
        # Seeded: every verdict turned.
        done, summary = run_eval(lambda case: flipped(case, None))
        self.assertEqual(done.stdout.splitlines()[0], "run 1: score 0.0", done.stdout + done.stderr)
        self.assertEqual(summary["per_run"], [{"blocked": 0, "score": 0.0, "verdict_rate": 0.0}])
        self.assertEqual(tree_hash(KIT / "evals"), before)


if __name__ == "__main__":
    unittest.main()
