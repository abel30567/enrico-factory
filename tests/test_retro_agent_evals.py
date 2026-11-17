"""The maker `retro-agent`'s eval suite in kit/evals/retro-agent/ (brief section 13), run offline (ISSUE-175).

Three replay cases, each a locked season and one verdict to learn from, graded by the `playbook` lint.
Recorded outputs stand in for the role: nothing here runs a model.
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
from test_story_evals import CASE_ID, READY, TELLS, TREES  # noqa: E402

KIT = ROOT / "kit"
FIX = ROOT / "tests" / "fixtures" / "story_evals"
FAKE = ROOT / "tests" / "fixtures" / "harness" / "fake-harness"
FACTORY = ROOT / "kit" / "factory"
ROLE = "retro-agent"
IDS = ("01-boat-shed", "02-orchard-gate", "03-well-yard")
SPEC = {"kind": "replay", "lint": ["playbook"], "expect_files": ["playbook/PB-2.md", "lab/proposals/*.md"]}
LESSON = "playbook/PB-2.md"
EVIDENCE = re.compile(r"(?m)^evidence: episodes/ep01/beats\.md:[0-9]+$")
FAR = "evidence: episodes/ep01/beats.md:99"
SCRIPT = "episodes/ep01/script.md"
NUMBER = re.compile(r"#[1-9][0-9]*")


def tree_hash(path):
    digest = hashlib.sha256()
    for item in sorted(p for p in Path(path).rglob("*") if p.is_file()):
        digest.update(item.relative_to(path).as_posix().encode() + b"\0" + item.read_bytes())
    return digest.hexdigest()


def cases():
    return evals.load(KIT, ROLE)


def output(case):
    """``{path: text}`` of the recorded output of ``case``."""
    base = FIX / ROLE / case.id
    return {p.relative_to(base).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(base.rglob("*")) if p.is_file()}


def far(case):
    """The recorded output of ``case`` with its lesson citing line 99 of a 21-line file."""
    files = output(case)
    files[LESSON] = EVIDENCE.sub(FAR, files[LESSON])
    return files


def grade_with(case, files):
    """The grade of READY for ``case`` in a run directory holding its tree plus ``files``."""
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        shutil.copytree(case.path / "tree", run_dir)
        for relative, text in files.items():
            (run_dir / relative).parent.mkdir(parents=True, exist_ok=True)
            (run_dir / relative).write_text(text, encoding="utf-8")
        return evals.grade(case, READY, run_dir=run_dir)


def scene_beats(tree):
    """``{S<n>: beats line}`` of the scenes of ``tree``'s ep01 script; empty when there is no script."""
    path = Path(tree) / SCRIPT
    if not path.is_file():
        return {}
    found, scene = {}, None
    for line in path.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^## (S[0-9]+) \S", line)
        if heading:
            scene = heading.group(1)
        elif scene and line.startswith("beats:"):
            found.setdefault(scene, line[len("beats:"):].strip())
    return found


def structure(tree):
    return [(f.path, f.message) for f in lint.run(Path(tree), only=["structure"])["structure"].findings]


def run_eval(answer):
    """``(completed process, summary)`` of ``factory eval retro-agent`` on the fake harness,
    ``answer(case)`` the files each case's step writes."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        bin_dir, root, out = tmp / "bin", tmp / "root", tmp / "out"
        bin_dir.mkdir()
        root.mkdir()
        shutil.copy(FAKE, bin_dir / "fake-harness")
        os.chmod(bin_dir / "fake-harness", 0o755)
        steps = [{"match": f"eval {re.escape(case.id)}(?![\\w-])", "files": answer(case), "result": READY}
                 for case in cases()]
        (bin_dir / "fake-harness.json").write_text(json.dumps(steps), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "FACTORY_LIVE"}
        env["FACTORY_FAKE_HARNESS"] = str(bin_dir / "fake-harness")
        done = subprocess.run([sys.executable, str(FACTORY), "eval", ROLE, "--harness", "fake",
                               "--root", str(root), "--out", str(out)],
                              env=env, cwd=ROOT, capture_output=True, text=True, timeout=300)
        summary = out / "summary.json"
        return done, json.loads(summary.read_text(encoding="utf-8")) if summary.is_file() else None


class RetroAgentSuite(unittest.TestCase):
    def test_shape_and_trees(self):
        """Behaviour 6."""
        self.assertFalse((KIT / "evals" / f"{ROLE}.no-suite.md").exists())
        self.assertEqual(len(cases()), 3)
        self.assertEqual(tuple(case.id for case in cases()), IDS)
        suite = (KIT / "evals" / ROLE / "suite.json").read_text(encoding="utf-8")
        self.assertEqual(json.loads(suite), {"cases": 3})
        self.assertEqual(sorted(p.name for p in (FIX / ROLE).iterdir()), list(IDS))
        for case in cases():
            self.assertEqual((case.spec, case.role_kind), (SPEC, "maker"), case.id)
            self.assertRegex(case.id, CASE_ID)
            self.assertNotRegex(case.id, TELLS)
            self.assertNotRegex(case.task, TELLS, case.id)
            self.assertEqual([p.name for p in case.path.iterdir() if p.is_dir() and p.name in TREES], ["tree"])
            results = lint.run(case.path / "tree", only=["playbook"])
            self.assertEqual(lint.overall(results), lint.PASS, f"{case.id}: {lint.text(results)}")

    def test_grade(self):
        """Behaviour 6: the recorded output passes; a lesson citing a line past the end, or no proposal, fails."""
        self.assertEqual(len(cases()), 3)
        for case in cases():
            graded = grade_with(case, output(case))
            self.assertEqual((graded["pass"], graded["detail"]), (True, "lint PASS"), case.id)
            graded = grade_with(case, far(case))
            self.assertEqual((graded["pass"], graded["detail"]), (False, "lint FAIL"), case.id)
            files = {k: v for k, v in output(case).items() if not k.startswith("lab/proposals/")}
            self.assertEqual(len(files), 1, case.id)
            graded = grade_with(case, files)
            self.assertFalse(graded["pass"], case.id)
            self.assertIn("missing lab/proposals/*.md", graded["detail"], case.id)

    def test_the_script_verdict_has_its_scene_and_numbers(self):
        """ISSUE-574: the verdict on the script of 02-orchard-gate names notes #<n> and a scene the tree holds."""
        case = next(c for c in cases() if c.id == "02-orchard-gate")
        tree = case.path / "tree"
        self.assertEqual(scene_beats(tree).get("S2"), "B2")
        self.assertRegex(case.task, NUMBER)
        boat_shed = next(c for c in cases() if c.id == "01-boat-shed")
        self.assertEqual(structure(tree), structure(boat_shed.path / "tree"))
        self.assertEqual(structure(tree), [("episodes/ep01/beats.md", "episodes/ep01/premise.md is missing")])
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "tree"
            shutil.copytree(tree, copy)
            (copy / SCRIPT).unlink()
            self.assertIsNone(scene_beats(copy).get("S2"))
        self.assertNotRegex(NUMBER.sub("", case.task), NUMBER)
        self.assertNotRegex("two notes in a row that B2 says the same thing twice in S2", NUMBER)

    def test_factory_eval(self):
        """Behaviour 7."""
        self.assertEqual(len(cases()), 3)
        before = tree_hash(KIT / "evals")
        done, summary = run_eval(output)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout.splitlines(),
                         ["run 1: score 1.0", "noise floor: null over 1 runs (not measured)"])
        self.assertEqual(summary["per_run"], [{"blocked": 0, "lint_pass_rate": 1.0, "score": 1.0}])
        # Seeded: every lesson cites line 99.
        done, summary = run_eval(far)
        self.assertEqual(done.stdout.splitlines()[0], "run 1: score 0.0", done.stdout + done.stderr)
        self.assertEqual(summary["per_run"], [{"blocked": 0, "lint_pass_rate": 0.0, "score": 0.0}])
        self.assertEqual(tree_hash(KIT / "evals"), before)


if __name__ == "__main__":
    unittest.main()
