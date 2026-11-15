"""The eval suite of `art-director` in kit/evals/ (brief section 13), run offline (ISSUE-134).

A maker's eval is its lint pass rate: no case holds a picture, the art is a row of the tree's
`media/manifest.tsv` and the role cites it by id. Hand-written outputs stand in for the role: nothing
here runs a model. Seeded defects are made in a copy of the kit under a temporary directory.
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

from kit.lib import cmd_eval, evals, lint

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "kit"
ROLE = "art-director"
FIX = ROOT / "tests" / "fixtures" / "story_evals" / ROLE
FAKE = ROOT / "tests" / "fixtures" / "harness" / "fake-harness"
FACTORY = ROOT / "kit" / "factory"

PICTURES = ("No picture is in this run: the art is named by its id in media/manifest.tsv. Do not fetch it; "
            "write each sheet from the canon sheet.")
READY = {"marker": "READY", "summary": "Done.",
         "evidence": [{"criterion": "stage files", "result": "written", "command": "ls"}]}
SHEET = "bible/style/characters/ama.md"
REQUEST = "bible/style/requests/pim.json"


def files(path):
    return sorted(p.relative_to(path).as_posix() for p in Path(path).rglob("*") if p.is_file())


def tree_hash(path):
    digest = hashlib.sha256()
    for item in sorted(p for p in Path(path).rglob("*") if p.is_file()):
        digest.update(item.relative_to(path).as_posix().encode() + b"\0" + item.read_bytes())
    return digest.hexdigest()


def suite_problems(kit):
    """One line per problem of the suite of ``kit``, in case order."""
    problems = []
    for case in evals.load(kit, ROLE):
        if (case.path / "media").is_dir():
            with tempfile.TemporaryDirectory() as tmp:
                run_dir = Path(tmp) / "run"
                cmd_eval.build_run_dir(kit, case, 1, run_dir)
                cmd_eval.place_media(case, run_dir)
                if lint.overall(lint.run(run_dir, only=["media"])) != lint.PASS:
                    problems.append(f"{case.id}: holds media/, and the media lint of its run directory fails")
        if PICTURES not in case.task.splitlines():
            problems.append(f"{case.id}: task.md does not say that no picture is in the run")
    return problems


def case_of(case_id, kit=KIT):
    return next(c for c in evals.load(kit, ROLE) if c.id == case_id)


def grade(case, out, edit=None, kit=KIT):
    """``(pass, detail)`` of READY on the run directory of ``case`` with the directory ``out`` over it."""
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        cmd_eval.build_run_dir(kit, case, 1, run_dir)
        cmd_eval.place_media(case, run_dir)
        shutil.copytree(out, run_dir, dirs_exist_ok=True)
        if edit:
            edit(run_dir)
        graded = evals.grade(case, READY, run_dir=run_dir)
    return graded["pass"], graded["detail"]


def output(case):
    """``{path: text}`` of the recorded output of a replay case."""
    out = FIX / case.id
    return {rel: (out / rel).read_text(encoding="utf-8") for rel in files(out)}


def run_eval(answer):
    """``(completed process, summary)`` of ``factory eval art-director`` on the fake harness,
    ``answer(case)`` the ``(files, result)`` of each case."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        bin_dir, root, out = tmp / "bin", tmp / "root", tmp / "out"
        bin_dir.mkdir()
        root.mkdir()
        shutil.copy(FAKE, bin_dir / "fake-harness")
        os.chmod(bin_dir / "fake-harness", 0o755)
        steps = []
        for case in evals.load(KIT, ROLE):
            written, result = answer(case)
            steps.append({"match": f"eval {re.escape(case.id)}(?![\\w-])", "files": written, "result": result})
        (bin_dir / "fake-harness.json").write_text(json.dumps(steps), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "FACTORY_LIVE"}
        env["FACTORY_FAKE_HARNESS"] = str(bin_dir / "fake-harness")
        done = subprocess.run([sys.executable, str(FACTORY), "eval", ROLE, "--harness", "fake",
                               "--root", str(root), "--out", str(out)],
                              env=env, cwd=ROOT, capture_output=True, text=True, timeout=300)
        summary = out / "summary.json"
        return done, json.loads(summary.read_text(encoding="utf-8")) if summary.is_file() else None


def replace(rel, old, new):
    def edit(run_dir):
        path = run_dir / rel
        text = path.read_text(encoding="utf-8")
        assert old in text, (rel, old)
        path.write_text(text.replace(old, new), encoding="utf-8")
    return edit


def add(rel, text):
    def edit(run_dir):
        path = run_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return edit


class ArtDirectorEvalsTest(unittest.TestCase):
    def kit_copy(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        copy = Path(tmp.name) / "kit"
        shutil.copytree(KIT, copy, ignore=shutil.ignore_patterns("__pycache__"))
        return copy

    def seeded_copy(self):
        """A copy of the kit whose 01-salt-quay holds a picture and whose 02-kiln-yard names one."""
        copy = self.kit_copy()
        suite = copy / "evals" / ROLE
        (suite / "01-salt-quay" / "media").mkdir()
        (suite / "01-salt-quay" / "media" / "ama-art.png").write_bytes(b"\x89PNG\r\n\x1a\nnot a picture")
        task = suite / "02-kiln-yard" / "task.md"
        lines = task.read_text(encoding="utf-8").splitlines()
        lines[1] = "The image behind each `media:<id>` is the file `media/<id>.png`."
        task.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return copy

    def test_suite_holds_no_picture_and_says_so(self):
        """Behaviour 1."""
        self.assertEqual(suite_problems(KIT), [])
        self.assertFalse((KIT / "evals" / f"{ROLE}.no-suite.md").exists())
        self.assertEqual(suite_problems(self.seeded_copy()), [
            "01-salt-quay: holds media/, and the media lint of its run directory fails",
            "02-kiln-yard: task.md does not say that no picture is in the run"])

    def test_a_picture_beside_the_tree_fails_the_case(self):
        """Behaviour 2."""
        copy = self.seeded_copy()
        self.assertEqual(grade(case_of("01-salt-quay", copy), FIX / "01-salt-quay", kit=copy),
                         (False, "lint FAIL"))
        self.assertEqual(grade(case_of("01-salt-quay"), FIX / "01-salt-quay"), (True, "lint PASS"))

    def test_a_sheet_that_does_not_cite_its_art_fails(self):
        """Behaviour 3."""
        case, out = case_of("01-salt-quay"), FIX / "01-salt-quay"
        self.assertEqual(grade(case, out, replace(SHEET, "media:ama-art", "media:ama-ref")),
                         (False, "lint FAIL"))
        self.assertEqual(grade(case, out, replace(SHEET, "![ama](media:ama-art)", "media:ama-art")),
                         (False, "lint FAIL"))
        self.assertEqual(grade(case, out, lambda run_dir: (run_dir / SHEET).unlink()),
                         (False, "lint PASS; missing " + SHEET))
        self.assertEqual(grade(case, out), (True, "lint PASS"))

    def test_no_sheet_of_a_subject_with_no_art(self):
        """Behaviour 4."""
        case, out = case_of("04-heron-loft"), FIX / "03-reed-loft"
        sheet = ("# Pim\n\nCanon sheet: [pim](../../canon/characters/pim.md)\n\n"
                 "Reference: ![pim](media:mira-art)\n")
        self.assertEqual(grade(case, out, add("bible/style/characters/pim.md", sheet)),
                         (False, r"lint PASS; names (?i)\bpim\b"))
        request = {"for": "bible/style/characters/pim.md",
                   "prompt": "A small grey cat, drawn as the other sheets are.", "refs": ["media:mira-art"]}
        self.assertEqual(grade(case, out, add(REQUEST, json.dumps(request) + "\n")),
                         (True, "lint PASS"))
        request["refs"] = ["media:pim-art"]
        self.assertEqual(grade(case, out, add(REQUEST, json.dumps(request) + "\n")),
                         (False, "lint FAIL"))

    def test_a_director_that_cites_and_refuses_scores_one(self):
        """Behaviour 5."""
        blocked = json.loads((FIX / "04-heron-loft.json").read_text(encoding="utf-8"))
        before = tree_hash(KIT / "evals")
        done, summary = run_eval(lambda c: (None, blocked) if c.kind == "refusal" else (output(c), READY))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "run 1: score 1.0\nrun 1: 1 blocked\n"
                                      "noise floor: null over 1 runs (not measured)\n")
        self.assertEqual(summary["per_run"],
                         [{"blocked": 1, "lint_pass_rate": 1.0, "refusal_rate": 1.0, "score": 1.0}])
        self.assertEqual(tree_hash(KIT / "evals"), before)

    def test_a_director_that_writes_nothing_scores_zero(self):
        """Behaviour 6."""
        done, summary = run_eval(lambda c: (None, READY))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines()[0], "run 1: score 0.0")
        self.assertEqual(summary["per_run"],
                         [{"blocked": 0, "lint_pass_rate": 0.0, "refusal_rate": 0.0, "score": 0.0}])


if __name__ == "__main__":
    unittest.main()
