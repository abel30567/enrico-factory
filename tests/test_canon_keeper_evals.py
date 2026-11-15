"""The canon keeper's eval suite in kit/evals/canon-keeper/ (brief section 13), run offline (ISSUE-99).

The judge of the `bible` stage votes on `charter` and `arc` amendments only, so the suite measures the lint
pass rate. Recorded outputs in tests/fixtures/story_evals/ stand in for the role: nothing here runs a model.
The role is found as the maker of the pipeline's `story/bible` entry. A defect or near miss is one text
change to one file of a temporary copy of a case's tree plus its recorded output.
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
FAKE = ROOT / "tests" / "fixtures" / "harness" / "fake-harness"
FACTORY = ROOT / "kit" / "factory"

ENTRY = next(e for e in json.loads((KIT / "pipeline.json").read_text(encoding="utf-8"))
             if e["room"] == "story" and e["stage"] == "bible")
ROLE = "canon-keeper"
AMENDMENT = re.compile(r"^bible/amendments/(A-[0-9]+)\.json$")
CLASSES = {"01-tide-bell": "charter", "02-net-loft": "canon", "03-kiln-night": "canon"}
READY = {"marker": "READY", "summary": "Done.",
         "evidence": [{"criterion": "amendment files", "result": "written", "command": "ls"}]}

JEM = "bible/canon/characters/jem.md"
ROS = "bible/canon/characters/ros.md"
LEDGER = "bible/canon/ledger.tsv"
SHED = "bible/canon/places/drying-shed.md"
CHARTER = "bible/charter.md"
JEM_ORIGIN = "origin: source sources/net-loft.md\n"


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


def amendment(case):
    """The path of the amendment the case's ``expect_files`` names."""
    return next(p for p in case.spec["expect_files"] if AMENDMENT.match(p))


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


def replace(old, new):
    def change(text):
        assert text.count(old) == 1, old
        return text.replace(old, new)
    return change


def append(row):
    return lambda text: text + row


def move_origin(text):
    """The sheet with its `origin:` line right under the title."""
    title, rest = text.split("\n", 1)
    return f"{title}\n{JEM_ORIGIN}" + rest.replace(f"\n{JEM_ORIGIN}", "", 1)


def motivation_first(text):
    """The sheet with `## Motivation` and its text before `## Behaviour`."""
    head, behaviour = text.split("## Behaviour\n", 1)
    behaviour, motivation = behaviour.split("## Motivation\n", 1)
    return f"{head}## Motivation\n{motivation}\n## Behaviour\n{behaviour.rstrip()}\n"


# (name, case, path, change, check, line or None for the line of the change, message)
DEFECTS = (
    ("jem.md without its origin line", "02-net-loft", JEM, replace(JEM_ORIGIN, ""), "structure", 1,
     "no line 'origin: source' or 'origin: house'"),
    ("ros.md without ## Motivation", "02-net-loft", ROS, replace("## Motivation\n", ""), "structure", 1,
     "no '## Motivation' heading"),
    ("the tag of the K2 row emptied", "02-net-loft", LEDGER, replace("\tbone\ttext\t", "\tbone\t\t"),
     "provenance", None, "tag '' is not one of text, art, inferred, invented"),
    ("the K6 item without its tag", "03-kiln-night", SHED, replace("- K6 [text](sources/kiln-night.md) ", "- K6 "),
     "provenance", None, "list item has none of [text], [art], [inferred], [invented]"),
    ("a second job for nell", "03-kiln-night", LEDGER,
     append("nell\tjob\tnight watchman\ttext\tsources/kiln-night.md K2\n"),
     "canon", None, "nell / job: 'night watchman' contradicts 'fires the big kiln' on line 2"),
    ("the C5 link to sources/rules.md", "01-tide-bell", CHARTER,
     replace("- C5 [text](sources/charter.md)", "- C5 [text](sources/rules.md)"),
     "references", None, "link target sources/rules.md does not exist"),
)
NEAR_MISSES = (
    ("jem.md with its origin line under the title", "02-net-loft", JEM, move_origin),
    ("ros.md with ## Motivation before ## Behaviour", "02-net-loft", ROS, motivation_first),
    ("the drying-shed lamp row repeated", "03-kiln-night", LEDGER,
     append("drying-shed\tlamp\tnone\ttext\tsources/kiln-night.md K5\n")),
)


def first_changed_line(before, after):
    pairs = zip(before.splitlines(), after.splitlines())
    return next((n for n, (a, b) in enumerate(pairs, 1) if a != b), len(before.splitlines()) + 1)


def case_named(case_id):
    return next(c for c in evals.load(KIT, ROLE) if c.id == case_id)


def graded(case, path=None, change=None):
    """``(grade, lint results, line of the change)`` of READY on the case's tree with its recorded output,
    ``path`` changed by ``change``."""
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        shutil.copytree(case.path / "tree", run_dir)
        shutil.copytree(FIX / ROLE / case.id, run_dir, dirs_exist_ok=True)
        line = None
        if path:
            before = (run_dir / path).read_text(encoding="utf-8")
            after = change(before)
            (run_dir / path).write_text(after, encoding="utf-8")
            line = first_changed_line(before, after)
        return evals.grade(case, READY, run_dir=run_dir), lint.run(run_dir, only=ENTRY["lint"]), line


class CanonKeeperEvalsTest(unittest.TestCase):
    def test_the_suite_is_the_bible_entrys(self):
        self.assertIn(ROLE, ENTRY["maker"])
        self.assertTrue((KIT / "evals" / ROLE / "suite.json").is_file())
        self.assertFalse((KIT / "evals" / f"{ROLE}.no-suite.md").exists())
        cases = evals.load(KIT, ROLE)
        self.assertEqual([c.id for c in cases], sorted(CLASSES))
        for case in cases:
            with self.subTest(case=case.id):
                self.assertEqual(case.kind, "replay")
                self.assertEqual(case.spec["lint"], ENTRY["lint"])
                output = recorded(case)
                self.assertEqual(sorted(output), sorted(case.spec["expect_files"]))
                path = amendment(case)
                self.assertFalse((case.path / "tree" / path).exists())
                doc = json.loads(output[path])
                self.assertEqual(sorted(doc), ["changes", "class", "evidence", "why"])
                self.assertEqual(doc["class"], CLASSES[case.id])
                self.assertEqual(sorted(doc["changes"]), sorted(p for p in case.spec["expect_files"] if p != path))
                self.assertRegex(case.task, rf"\b{AMENDMENT.match(path).group(1)}\b")

    def test_it_runs_on_the_fake_harness(self):
        before = tree_hash(KIT / "evals")
        done, summary, found = run_eval(recorded)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "run 1: score 1.0\nnoise floor: null over 1 runs (not measured)\n")
        self.assertEqual(summary["per_run"], [{"blocked": 0, "lint_pass_rate": 1.0, "score": 1.0}])
        self.assertEqual(found, ["lint PASS"] * 3)
        self.assertEqual(tree_hash(KIT / "evals"), before)

    def test_a_withheld_amendment_scores_zero(self):
        done, _, found = run_eval(lambda c: {p: t for p, t in recorded(c).items() if p != amendment(c)})
        self.assertEqual(done.stdout.splitlines()[0], "run 1: score 0.0", done.stderr)
        self.assertEqual(found, [f"lint PASS; missing bible/amendments/A-{n}.json" for n in (1, 2, 3)])

    def test_each_defect_fails_its_lint(self):
        for name, case_id, path, change, check, line, message in DEFECTS:
            case = case_named(case_id)
            with self.subTest(defect=name):
                clean, _, _ = graded(case)
                self.assertEqual((clean["pass"], clean["detail"]), (True, "lint PASS"))
                grade, results, changed = graded(case, path, change)
                self.assertEqual((grade["pass"], grade["detail"]), (False, "lint FAIL"))
                self.assertEqual(sorted(results), sorted(ENTRY["lint"]))
                for other in ENTRY["lint"]:
                    if other != check:
                        self.assertEqual(results[other].status, lint.PASS, f"{other}: {lint.text(results)}")
                self.assertNotEqual(results[check].status, lint.PASS)
                self.assertEqual([(f.path, f.line, f.message) for f in results[check].findings],
                                 [(path, line or changed, message)])

    def test_near_misses_pass(self):
        for name, case_id, path, change in NEAR_MISSES:
            with self.subTest(near_miss=name):
                grade, _, _ = graded(case_named(case_id), path, change)
                self.assertEqual((grade["pass"], grade["detail"]), (True, "lint PASS"))


if __name__ == "__main__":
    unittest.main()
