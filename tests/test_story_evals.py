"""The story room's role eval suites in kit/evals/ (brief section 13), checked offline.

Hand-written results stand in for the roles: nothing here runs a model.
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

from kit.lib import evals, frontmatter, harness, lint, production

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "kit"
FIX = ROOT / "tests" / "fixtures" / "story_evals"
FAKE = ROOT / "tests" / "fixtures" / "harness" / "fake-harness"
FACTORY = ROOT / "kit" / "factory"
PIPELINE = json.loads((KIT / "pipeline.json").read_text(encoding="utf-8"))

CHECKERS = ("fact-checker", "canon-reviewer", "story-editor", "continuity-supervisor", "look-reviewer")
MAKERS = ("story-writer", "script-writer", "head-of-story", "story-artist", "editorial")
JUDGES = ("pitch-judge",)
NO_SUITE = ("showrunner", "devils-advocate", "retro-agent", "researcher", "canon-keeper", "art-director",
            "trend-scout", "audience-analyst", "render-reviewer", "braintrust", "audience-sim",
            "distinctiveness-judge", "held-out-judge")
CASE_ID = re.compile(r"^[0-9]{2}-[a-z0-9]+(-[a-z0-9]+)*$")
TELLS = re.compile(r"(?i)seeded|defect|planted|clean case")
SIDE_NAMES = re.compile(r"(?i)\b(old|new)/")
TREES = ("tree", "old", "new")


def cases(role):
    return evals.load(KIT, role)


def blocker(text="A note.", evidence="a file"):
    return {"marker": "NOTES", "summary": "Notes.",
            "notes": [{"id": "B1", "severity": "Blocker", "text": text, "evidence": evidence}]}


APPROVED = {"marker": "APPROVED", "summary": "Approved."}
READY = {"marker": "READY", "summary": "Done.",
         "evidence": [{"criterion": "stage files", "result": "written", "command": "ls"}]}


def prefers(side):
    return {"marker": f"PREFERS {side}", "summary": "One is better.", "prefers": {"choice": side, "cited": []}}


def stage_lints(key, role):
    """The union of `lint` over the pipeline entries whose ``key`` is ``role``."""
    return sorted({check for entry in PIPELINE if role in production.stage_roles(entry, key)
                   for check in entry["lint"]})


def lint_exempt(case):
    """The check a one-line lint-exempt.txt leaves out, or None."""
    path = case.path / "lint-exempt.txt"
    if not path.exists():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, f"{case.role}/{case.id}: lint-exempt.txt is not one line"
    return lines[0].split()[0]


def tree_hash(path):
    digest = hashlib.sha256()
    for item in sorted(p for p in Path(path).rglob("*") if p.is_file()):
        digest.update(item.relative_to(path).as_posix().encode() + b"\0" + item.read_bytes())
    return digest.hexdigest()


def reaches_evals(role_text):
    """Whether a role file's `writes` reaches a kit's eval suites."""
    meta, _ = frontmatter.parse(role_text)
    writes = frontmatter.as_list(meta.get("writes"))
    return any(production.matches(path, writes) for path in ("kit/evals/x", ".factory/evals/x"))


class SuitesTest(unittest.TestCase):
    def test_every_suite_loads_with_its_counts(self):
        for role in CHECKERS:
            kinds = [c.kind for c in cases(role)]
            self.assertGreaterEqual(kinds.count("seeded"), 6, role)
            self.assertGreaterEqual(kinds.count("clean"), 3, role)
        for role in MAKERS:
            self.assertGreaterEqual(len(cases(role)), 3, role)
        pairs = cases("pitch-judge")
        self.assertGreaterEqual(len(pairs), 4)
        self.assertEqual(sum(c.spec["better"] == "old" for c in pairs), 2)

    def test_case_ids(self):
        for role in CHECKERS + MAKERS + JUDGES:
            for case in cases(role):
                self.assertRegex(case.id, CASE_ID, role)
                self.assertNotRegex(case.id, TELLS, role)
                if role in CHECKERS:
                    self.assertNotRegex(case.id, r"(?i)seed|clean", role)

    def test_seeded_and_clean_are_mixed(self):
        for role in CHECKERS:
            kinds = "".join("s" if c.kind == "seeded" else "c" for c in cases(role))
            self.assertNotIn(kinds, ("s" * kinds.count("s") + "c" * kinds.count("c"),
                                     "c" * kinds.count("c") + "s" * kinds.count("s")), role)

    def test_suites_and_readme_cover_every_role(self):
        suites = sorted(p.name for p in (KIT / "evals").iterdir() if p.is_dir())
        self.assertEqual(suites, sorted(CHECKERS + MAKERS + JUDGES))
        self.assertEqual(sorted(p.stem for p in (KIT / "roles").glob("*.md")), sorted(suites + list(NO_SUITE)))
        readme = (KIT / "evals" / "README.md").read_text(encoding="utf-8")
        for role in NO_SUITE:
            self.assertIn(f"`{role}`", readme)
        for rule in ("The lab owns the suites", "<nn>-<slug>"):
            self.assertIn(rule, readme)

    def test_tasks_do_not_tell(self):
        for role in CHECKERS + MAKERS + JUDGES:
            for case in cases(role):
                self.assertNotRegex(case.task, TELLS, f"{role}/{case.id}")

    def test_sizes(self):
        for role in CHECKERS + MAKERS + JUDGES:
            for case in cases(role):
                for name in TREES:
                    if (case.path / name).is_dir():
                        count = sum(p.is_file() for p in (case.path / name).rglob("*"))
                        self.assertLessEqual(count, 16, f"{role}/{case.id}/{name}")
        for path in (KIT / "evals").rglob("*"):
            if path.is_file():
                self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), 80, path)

    def test_no_role_writes_its_suite(self):
        for path in sorted((KIT / "roles").glob("*.md")):
            self.assertFalse(reaches_evals(path.read_text(encoding="utf-8")), path.name)

    def test_writes_into_the_suites_is_refused(self):
        text = (KIT / "roles" / "story-writer.md").read_text(encoding="utf-8")
        head = text.split("\n---", 1)[0]
        seeded = re.sub(r"(?m)^writes:.*$", "writes: kit/evals/**", head) + "\n---\n"
        self.assertTrue(reaches_evals(seeded))
        self.assertTrue(reaches_evals(seeded.replace("kit/evals/**", "bible/**, .factory/evals/**")))


class CheckerTest(unittest.TestCase):
    def test_trees_pass_their_stages_lints(self):
        for role in CHECKERS:
            ids = stage_lints("checker", role)
            self.assertTrue(ids, role)
            exempt = 0
            for case in cases(role):
                skip = lint_exempt(case)
                exempt += skip is not None
                only = [check for check in ids if check != skip]
                results = lint.run(case.path / "tree", only=only)
                self.assertEqual(lint.overall(results), lint.PASS, f"{role}/{case.id}: {lint.text(results)}")
            self.assertLessEqual(exempt, 2, role)

    def test_seeded_cases_can_pass_and_fail(self):
        for role in CHECKERS:
            seeded = [c for c in cases(role) if c.kind == "seeded"]
            fixtures = sorted(p.stem for p in (FIX / role).glob("*.json"))
            self.assertEqual(fixtures, [c.id for c in seeded], role)
            for case in seeded:
                where = f"{role}/{case.id}"
                result = json.loads((FIX / role / f"{case.id}.json").read_text(encoding="utf-8"))
                self.assertEqual(harness.validate_result(case.role_kind, case.actor, result), [], where)
                self.assertEqual(result["marker"], "NOTES", where)
                self.assertTrue(evals.grade(case, result)["pass"], where)
                self.assertIn(len(case.spec["catches"]), (1, 2), where)
                for pattern in case.spec["catches"]:
                    other = json.loads(json.dumps(result))
                    for note in other["notes"]:
                        for key in ("text", "evidence"):
                            note[key] = re.sub(pattern, "Z99", note[key])
                    self.assertFalse(evals.grade(case, other)["pass"], f"{where}: {pattern}")

    def test_clean_cases_can_pass_and_fail(self):
        for role in CHECKERS:
            for case in (c for c in cases(role) if c.kind == "clean"):
                self.assertTrue(evals.grade(case, APPROVED)["pass"], f"{role}/{case.id}")
                self.assertFalse(evals.grade(case, blocker())["pass"], f"{role}/{case.id}")


class MakerTest(unittest.TestCase):
    def test_replay_cases(self):
        for role in MAKERS:
            entries = [e for e in PIPELINE if role in production.stage_roles(e, "maker")]
            for case in cases(role):
                where = f"{role}/{case.id}"
                self.assertEqual(case.kind, "replay", where)
                entry = next((e for e in entries if e["lint"] == case.spec["lint"]), None)
                self.assertIsNotNone(entry, where)
                output = FIX / role / case.id
                written = sorted(p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file())
                self.assertTrue(written, where)
                for pattern in case.spec["expect_files"]:
                    hits = [p.relative_to(output).as_posix() for p in output.glob(pattern) if p.is_file()]
                    self.assertTrue(hits, f"{where}: {pattern}")
                    for hit in hits:
                        self.assertTrue(any(production.matches(hit, e["paths"]) for e in entries
                                            if e["lint"] == case.spec["lint"]), f"{where}: {hit}")
                with tempfile.TemporaryDirectory() as tmp:
                    run_dir = Path(tmp) / "run"
                    shutil.copytree(case.path / "tree", run_dir)
                    bare = evals.grade(case, READY, run_dir=run_dir)
                    self.assertFalse(bare["pass"], where)
                    self.assertIn("missing", bare["detail"], where)
                    shutil.copytree(output, run_dir, dirs_exist_ok=True)
                    graded = evals.grade(case, READY, run_dir=run_dir)
                    self.assertTrue(graded["pass"], f"{where}: {graded['detail']}")

    def test_stages_covered(self):
        def stages(role):
            found = set()
            for case in cases(role):
                for entry in PIPELINE:
                    if role in production.stage_roles(entry, "maker") and all(
                            production.matches(p.replace("*", "ep01"), entry["paths"])
                            for p in case.spec["expect_files"]):
                        found.add(entry["stage"])
            return found
        self.assertLessEqual({"premise", "beats"}, stages("story-writer"))
        self.assertLessEqual({"outline", "script"}, stages("script-writer"))


class JudgeTest(unittest.TestCase):
    def test_pair_cases(self):
        ids = next(e["lint"] for e in PIPELINE if "pitch-judge" in production.stage_roles(e, "judge"))
        for case in cases("pitch-judge"):
            where = case.id
            self.assertNotEqual(tree_hash(case.path / "old"), tree_hash(case.path / "new"), where)
            for name in ("old", "new"):
                results = lint.run(case.path / name, only=ids)
                self.assertEqual(lint.overall(results), lint.PASS, f"{where}/{name}: {lint.text(results)}")
            self.assertNotRegex(case.task, SIDE_NAMES, where)
            self.assertIn("A/", case.task, where)
            self.assertIn("B/", case.task, where)
            for run in (1, 2):
                sides = evals.sides(case.id, run)
                right = next(side for side, version in sides.items() if version == case.spec["better"])
                wrong = "B" if right == "A" else "A"
                for result in (prefers(right), prefers(wrong)):
                    self.assertEqual(harness.validate_result(case.role_kind, case.actor, result), [], where)
                self.assertTrue(evals.grade(case, prefers(right), run=run)["pass"], f"{where} run {run}")
                self.assertFalse(evals.grade(case, prefers(wrong), run=run)["pass"], f"{where} run {run}")


class EndToEndTest(unittest.TestCase):
    def test_story_editor_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bin_dir, root, out = tmp / "bin", tmp / "root", tmp / "out"
            bin_dir.mkdir()
            root.mkdir()
            shutil.copy(FAKE, bin_dir / "fake-harness")
            os.chmod(bin_dir / "fake-harness", 0o755)
            steps = []
            for case in cases("story-editor"):
                result = APPROVED if case.kind == "clean" else \
                    json.loads((FIX / "story-editor" / f"{case.id}.json").read_text(encoding="utf-8"))
                steps += [{"match": f"eval {re.escape(case.id)}(?![\\w-])", "result": result}] * 2
            (bin_dir / "fake-harness.json").write_text(json.dumps(steps), encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if k != "FACTORY_LIVE"}
            env["FACTORY_FAKE_HARNESS"] = str(bin_dir / "fake-harness")
            before = tree_hash(KIT / "evals")
            done = subprocess.run([sys.executable, str(FACTORY), "eval", "story-editor", "--runs", "2",
                                   "--root", str(root), "--out", str(out)],
                                  env=env, cwd=ROOT, capture_output=True, text=True, timeout=300)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("run 1: score 1.0\n", done.stdout)
            self.assertIn("run 2: score 1.0\n", done.stdout)
            self.assertTrue((out / "summary.json").is_file())
            self.assertEqual(tree_hash(KIT / "evals"), before)


if __name__ == "__main__":
    unittest.main()
