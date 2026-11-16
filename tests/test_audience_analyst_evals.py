"""The analyst `audience-analyst`'s eval suite in kit/evals/audience-analyst/ (brief section 13), run offline
(ISSUE-175).

Three symptoms cases, each one episode's YouTube telemetry window with its retention per beat. Hand-written
results stand in for the role: nothing here runs a model.
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

from kit.lib import evals, lint, retention  # noqa: E402
from test_story_evals import CASE_ID, TELLS, TREES  # noqa: E402

KIT = ROOT / "kit"
FIX = ROOT / "tests" / "fixtures" / "story_evals"
FAKE = ROOT / "tests" / "fixtures" / "harness" / "fake-harness"
FACTORY = ROOT / "kit" / "factory"
ROLE = "audience-analyst"
IDS = ("01-ferry-steps", "02-kite-field", "03-salt-pans")
FILES = ["distribution/ep01/youtube/retention.json", "distribution/ep01/youtube/snapshot.json",
         "episodes/ep01/reel/edit-list.json", "registry/posts.tsv"]
SNAPSHOT = "distribution/ep01/youtube/snapshot.json"
TURN = {"held": "dropped", "dropped": "rewatched", "rewatched": "held"}


def tree_hash(path):
    digest = hashlib.sha256()
    for item in sorted(p for p in Path(path).rglob("*") if p.is_file()):
        digest.update(item.relative_to(path).as_posix().encode() + b"\0" + item.read_bytes())
    return digest.hexdigest()


def cases():
    return evals.load(KIT, ROLE)


def recorded(case):
    return json.loads((FIX / ROLE / f"{case.id}.json").read_text(encoding="utf-8"))


def turn(text):
    """``text``, a note's text, with its symptom turned to another word."""
    beat, word, rest = re.match(r"(\S+) (held|dropped|rewatched)(: .*)", text).groups()
    return f"{beat} {TURN[word]}{rest}"


def turned(case, beat):
    """The recorded result of ``case`` with the symptom of ``beat`` (None: of every beat) turned."""
    result = recorded(case)
    for note in result["notes"]:
        if beat is None or note["text"].startswith(beat + " "):
            note["text"] = turn(note["text"])
    return result


def run_eval(answer):
    """``(completed process, summary)`` of ``factory eval audience-analyst`` on the fake harness,
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


def graded(kind, passed):
    return {"case": "x", "kind": kind, "marker": "SYMPTOMS", "pass": passed, "detail": ""}


class AudienceAnalystSuite(unittest.TestCase):
    def test_shape(self):
        """Behaviour 1."""
        self.assertFalse((KIT / "evals" / f"{ROLE}.no-suite.md").exists())
        self.assertEqual(len(cases()), 3)
        self.assertEqual(tuple(case.id for case in cases()), IDS)
        suite = (KIT / "evals" / ROLE / "suite.json").read_text(encoding="utf-8")
        self.assertEqual(json.loads(suite), {"cases": 3})
        self.assertEqual(sorted(p.name for p in (FIX / ROLE).iterdir()), [f"{cid}.json" for cid in IDS])
        for case in cases():
            self.assertEqual((case.kind, case.role_kind), ("symptoms", "analyst"), case.id)
            self.assertRegex(case.id, CASE_ID)
            self.assertNotRegex(case.id, TELLS)
            self.assertNotRegex(case.task, TELLS, case.id)
            self.assertEqual([p.name for p in case.path.iterdir() if p.is_dir() and p.name in TREES], ["tree"])

    def test_seeded_specs_fail_to_load(self):
        """Behaviour 1, seeded."""
        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp) / "kit"
            shutil.copytree(KIT / "roles", kit / "roles")
            for role in (ROLE, "retro-agent"):
                shutil.copytree(KIT / "evals" / role, kit / "evals" / role)
            self.assertEqual(len(evals.load(kit, ROLE)), 3)
            spec = kit / "evals" / ROLE / "01-ferry-steps" / "case.json"
            spec.write_text(json.dumps({"kind": "symptoms", "beats": {"B1": "fell"}}) + "\n", encoding="utf-8")
            with self.assertRaises(evals.EvalError) as caught:
                evals.load(kit, ROLE)
            self.assertEqual(str(caught.exception),
                             f"{ROLE}/01-ferry-steps: beats is not an object of beat id to held, dropped or rewatched")
            spec = kit / "evals" / "retro-agent" / "01-boat-shed" / "case.json"
            spec.write_text(json.dumps({"kind": "symptoms", "beats": {"B1": "held"}}) + "\n", encoding="utf-8")
            with self.assertRaises(evals.EvalError) as caught:
                evals.load(kit, "retro-agent")
            self.assertEqual(str(caught.exception),
                             "retro-agent/01-boat-shed: a symptoms case does not fit a role of kind maker")

    def test_trees(self):
        """Behaviour 2."""
        self.assertEqual(len(cases()), 3)
        for case in cases():
            tree = case.path / "tree"
            self.assertEqual(sorted(p.relative_to(tree).as_posix() for p in tree.rglob("*") if p.is_file()),
                             FILES, case.id)
            events = json.loads((tree / "episodes/ep01/reel/edit-list.json").read_text(encoding="utf-8"))["events"]
            self.assertEqual(sorted(case.spec["beats"]), sorted(e["beat"] for e in events), case.id)
            results = lint.run(tree, only=["telemetry"])
            self.assertEqual(lint.overall(results), lint.PASS, f"{case.id}: {lint.text(results)}")
            self.assertEqual((tree / "distribution/ep01/youtube/retention.json").read_text(encoding="utf-8"),
                             retention.build(tree, "ep01/youtube"), case.id)
        # Seeded: point 61 of the derived curve no longer what the answers give.
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "tree"
            shutil.copytree(KIT / "evals" / ROLE / "01-ferry-steps" / "tree", tree)
            data = json.loads((tree / SNAPSHOT).read_text(encoding="utf-8"))
            data["metrics"]["audienceWatchRatio"][60] = 0.99
            (tree / SNAPSHOT).write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")
            results = lint.run(tree, only=["telemetry"])
            self.assertEqual(lint.overall(results), lint.FAIL)
            self.assertIn("audienceWatchRatio point 61 is 0.99, the answers give 0.6917", lint.text(results))
            self.assertNotEqual((tree / "distribution/ep01/youtube/retention.json").read_text(encoding="utf-8"),
                                retention.build(tree, "ep01/youtube"))

    def test_grade(self):
        """Behaviour 3."""
        self.assertEqual(len(cases()), 3)
        for case in cases():
            graded_ = evals.grade(case, recorded(case))
            self.assertEqual((graded_["pass"], graded_["detail"]), (True, "every beat right"), case.id)
            beats = sorted(case.spec["beats"])
            self.assertEqual(len(beats), 4, case.id)
            for beat in beats:
                fail = (False, f"wrong or no symptom: {beat}")
                result = evals.grade(case, turned(case, beat))
                self.assertEqual((result["pass"], result["detail"]), fail, f"{case.id}: turned {beat}")
                result = recorded(case)
                result["notes"] = [n for n in result["notes"] if not n["text"].startswith(beat + " ")]
                self.assertEqual(len(result["notes"]), 3, case.id)
                result = evals.grade(case, result)
                self.assertEqual((result["pass"], result["detail"]), fail, f"{case.id}: removed {beat}")
                result = recorded(case)
                note = next(n for n in result["notes"] if n["text"].startswith(beat + " "))
                result["notes"].append(dict(note, id="I5", text=turn(note["text"])))
                result = evals.grade(case, result)
                self.assertEqual((result["pass"], result["detail"]), fail, f"{case.id}: second {beat}")
            result = {**recorded(case), "marker": "NOTES"}
            graded_ = evals.grade(case, result)
            self.assertEqual((graded_["pass"], graded_["detail"]), (False, "invalid result: 1 problem(s)"))
            self.assertEqual(evals._grade_symptoms(case, result), (False, "marker NOTES, not SYMPTOMS"))
            result = recorded(case)
            result["notes"][0]["text"] = "B10 " + case.spec["beats"]["B1"] + ": x"
            graded_ = evals.grade(case, result)
            self.assertEqual((graded_["pass"], graded_["detail"]), (False, "wrong or no symptom: B1"), case.id)

    def test_metrics(self):
        """Behaviour 4."""
        self.assertEqual(evals.metrics("analyst", [graded("symptoms", True), graded("symptoms", False)]),
                         {"blocked": 0, "score": 0.5, "symptom_rate": 0.5})
        self.assertEqual(evals.metrics("analyst", [graded("verdict", True), graded("symptoms", False)]),
                         {"blocked": 0, "score": 0.5, "symptom_rate": 0.0, "verdict_rate": 1.0})
        self.assertEqual(evals.metrics("analyst", [graded("verdict", True)]),
                         {"blocked": 0, "score": 1.0, "verdict_rate": 1.0})
        with self.assertRaises(evals.EvalError) as caught:
            evals.metrics("analyst", [])
        self.assertEqual(str(caught.exception), "analyst score: verdict_rate is null, no case to count")

    def test_factory_eval(self):
        """Behaviour 5."""
        self.assertEqual(len(cases()), 3)
        before = tree_hash(KIT / "evals")
        done, summary = run_eval(recorded)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout.splitlines(),
                         ["run 1: score 1.0", "noise floor: null over 1 runs (not measured)"])
        self.assertEqual(summary["per_run"], [{"blocked": 0, "score": 1.0, "symptom_rate": 1.0}])
        # Seeded: every symptom turned.
        done, summary = run_eval(lambda case: turned(case, None))
        self.assertEqual(done.stdout.splitlines()[0], "run 1: score 0.0", done.stdout + done.stderr)
        self.assertEqual(summary["per_run"], [{"blocked": 0, "score": 0.0, "symptom_rate": 0.0}])
        self.assertEqual(tree_hash(KIT / "evals"), before)


if __name__ == "__main__":
    unittest.main()
