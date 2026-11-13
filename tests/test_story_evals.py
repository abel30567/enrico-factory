"""The story room's role eval suites in kit/evals/ (brief section 13), checked offline.

Hand-written results stand in for the roles: nothing here runs a model.
"""

import contextlib
import hashlib
import io
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
from kit.lib import cmd_eval, cmd_init

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "kit"
FIX = ROOT / "tests" / "fixtures" / "story_evals"
FAKE = ROOT / "tests" / "fixtures" / "harness" / "fake-harness"
FACTORY = ROOT / "kit" / "factory"
PIPELINE = json.loads((KIT / "pipeline.json").read_text(encoding="utf-8"))

SUITES = tuple(sorted(p.name for p in (KIT / "evals").iterdir() if p.is_dir() and p.name != "golden"))
KIND = {role: evals.role_file(KIT, role)[0]["kind"] for role in SUITES}
CHECKERS = tuple(role for role in SUITES if KIND[role] == "checker")
MAKERS = tuple(role for role in SUITES if KIND[role] == "maker")
JUDGES = tuple(role for role in SUITES if KIND[role] == "judge")
NOTE = ".no-suite.md"
NO_SUITE = tuple(sorted(p.name[:-len(NOTE)] for p in (KIT / "evals").glob("*" + NOTE)))
NOTE_TEXT = re.compile(r"# `(?P<role>[a-z0-9-]+)`: no suite yet\n\nWhy not yet: .+\nWave: (?P<wave>[0-9]+|never)\n")
MINIMUM = {"seeded": 6, "clean": 3, "replay": 3, "refusal": 0, "pair": 4}
NEVER = "held-out-judge"
CASE_ID = re.compile(r"^[0-9]{2}-[a-z0-9]+(-[a-z0-9]+)*$")
TELLS = re.compile(r"(?i)seeded|defect|planted|clean case")
SIDE_NAMES = re.compile(r"(?i)\b(old|new)/")
TREES = ("tree", "old", "new")
READ_FIRST = re.compile(r"(?ms)^## Read first\n(.*?)(?=^## |\Z)")
INSTALLED = ("bible/", "media/", "registry/", ".factory/")
# Per role: the refusal case, its twin, and a replay case whose output names no such subject.
TWINS = {"story-writer": ("04-heron-fog", "05-heron-tide", "01-tide-bell"),
         "story-artist": ("04-heron-tower", "05-heron-flats", "01-tide-bell")}


def declared(role):
    return json.loads((KIT / "evals" / role / "suite.json").read_text(encoding="utf-8"))["cases"]


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


def unnamed_stage_lints(kit, role):
    """The check named after the role file's `stage`, for a role no pipeline entry names yet."""
    stage = evals.role_file(kit, role)[0].get("stage", "")
    return [stage] if (kit / "lib" / "lints" / f"{stage}.py").is_file() else []


def stage_lints(key, role):
    """The union of `lint` of the pipeline entries whose ``key`` is ``role``, else the role's stage check."""
    return sorted({check for entry in PIPELINE if role in production.stage_roles(entry, key)
                   for check in entry["lint"]}) or unnamed_stage_lints(KIT, role)


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


def files(path):
    return sorted(p.relative_to(path).as_posix() for p in Path(path).rglob("*") if p.is_file())


def read_first(kit, role):
    """The installed paths ``## Read first`` of the role file names."""
    section = READ_FIRST.search((kit / "roles" / f"{role}.md").read_text(encoding="utf-8")).group(1)
    named = {path for path in re.findall(r"`([^`]+)`", section)
             if path.startswith(INSTALLED) and not set(path) & set("<>*")}
    return sorted(named)


def checked_lints(kit, case):
    """The lints a built run directory must pass: a replay or refusal case's own, else its checker
    stages'."""
    if case.kind in ("replay", "refusal"):
        return case.spec["lint"]
    pipeline = json.loads((kit / "pipeline.json").read_text(encoding="utf-8"))
    return sorted({check for entry in pipeline if case.role in production.stage_roles(entry, "checker")
                   for check in entry["lint"]}) or unnamed_stage_lints(kit, case.role)


def completeness(kit, case, run_dir):
    """What the run directory built for ``case`` from ``kit`` lacks against the role's own file."""
    where = f"{case.role}/{case.id}"
    problems = []
    for name in TREES:
        if (case.path / name).is_dir() and any(f.startswith(".factory/") for f in files(case.path / name)):
            problems.append(f"d: {where}: {name}/ holds a path under .factory/")
    cmd_eval.build_run_dir(kit, case, 1, run_dir)
    built = files(run_dir)
    if (run_dir / ".factory" / "evals").exists() or any(f.split("/")[-1] == "case.json" for f in built):
        problems.append(f"d: {where}: the run directory holds an answer")
    if case.kind == "pair":
        if sorted(p.name for p in run_dir.iterdir()) != ["A", "B"]:
            problems.append(f"{where}: the run directory is not A/ and B/")
        if any(".factory" in Path(f).parts for f in built):
            problems.append(f"{where}: a side holds .factory")
        roots = [run_dir / "A", run_dir / "B"]
    else:
        ids = checked_lints(kit, case)
        done = subprocess.run([sys.executable, str(run_dir / ".factory" / "factory"), "lint", "--only",
                               ",".join(ids)], cwd=run_dir, capture_output=True, text=True, timeout=120)
        if done.returncode != 0:
            problems.append(f"a: {where}: lint exits {done.returncode}: {done.stdout}")
        formats = files(kit / "formats")
        if files(run_dir / ".factory" / "formats") != formats:
            problems.append(f"c: {where}: .factory/formats/ is not the kit's formats/")
        for premise in sorted(run_dir.glob("episodes/**/premise.md")):
            meta, _ = frontmatter.parse(premise.read_text(encoding="utf-8"))
            name = meta.get("format")
            if f"{name}.md" not in formats:
                problems.append(f"c: {where}: {premise.relative_to(run_dir)} names format {name!r}")
        roots = [run_dir]
    for root in roots:
        side = "" if root == run_dir else f"{root.name}/"
        for path in read_first(kit, case.role):
            if not (root / path).is_file():
                problems.append(f"b: {where}: {side}{path} is missing")
    return problems


class BuildRunDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, role, case_id, run=1, kit=KIT):
        case = next(c for c in evals.load(kit, role) if c.id == case_id)
        run_dir = self.dir / f"{role}-{case_id}-{run}" / "run"
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            cmd_eval.build_run_dir(kit, case, run, run_dir)
        self.assertEqual(out.getvalue(), "")
        return case, run_dir

    def test_a_tree_case_is_a_production(self):
        case, run_dir = self.build("story-writer", "01-tide-bell")
        built = files(run_dir)
        for path in (".factory/factory", ".factory/formats/short-four-act.md",
                     ".factory/roles/story-writer.md", "bible/charter.md", "bible/canon/ledger.tsv",
                     "media/manifest.tsv", "production.env"):
            self.assertIn(path, built)
        tree = files(case.path / "tree")
        self.assertEqual(len(tree), 3)
        for path in tree:
            self.assertEqual((run_dir / path).read_bytes(), (case.path / "tree" / path).read_bytes(), path)

    def test_no_answer_in_the_run_directory(self):
        _, run_dir = self.build("story-writer", "01-tide-bell")
        self.assertFalse((run_dir / ".factory" / "evals").exists())
        self.assertEqual([f for f in files(run_dir) if f.endswith("case.json")], [])
        self.assertTrue((run_dir / ".factory" / "lib" / "evals.py").is_file())
        init = self.dir / "init"
        init.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cmd_init.main([str(init)]), 0)
        self.assertTrue((init / ".factory/evals/story-writer/01-tide-bell/case.json").is_file())

    def test_a_tree_file_wins(self):
        case, run_dir = self.build("canon-reviewer", "01-lamplighters")
        for path in ("bible/charter.md", "bible/canon/ledger.tsv"):
            self.assertEqual((run_dir / path).read_bytes(), (case.path / "tree" / path).read_bytes(), path)
            self.assertNotEqual((run_dir / path).read_text(), cmd_init.FILES[path], path)
        case, run_dir = self.build("fact-checker", "02-salt-road")
        for path in ("bible/charter.md", "bible/canon/ledger.tsv"):
            self.assertFalse((case.path / "tree" / path).exists(), path)
            self.assertEqual((run_dir / path).read_text(encoding="utf-8"), cmd_init.FILES[path], path)

    def test_a_pair_case_is_two_sides_with_the_same_bible(self):
        for case in cases("pitch-judge"):
            for run in (1, 2):
                _, run_dir = self.build("pitch-judge", case.id, run)
                where = f"{case.id} run {run}"
                self.assertEqual(sorted(p.name for p in run_dir.iterdir()), ["A", "B"], where)
                self.assertEqual([f for f in files(run_dir) if ".factory" in Path(f).parts], [], where)
                for path in ("bible/charter.md", "bible/canon/ledger.tsv"):
                    self.assertTrue((run_dir / "A" / path).is_file(), f"{where}: {path}")
                    self.assertEqual((run_dir / "A" / path).read_bytes(), (run_dir / "B" / path).read_bytes(),
                                     f"{where}: {path}")
                for side, version in evals.sides(case.id, run).items():
                    for path in files(case.path / version):
                        self.assertEqual((run_dir / side / path).read_bytes(),
                                         (case.path / version / path).read_bytes(), f"{where}: {side}/{path}")


class CompletenessTest(unittest.TestCase):
    """Every case's run directory holds what its role file names (ISSUE-247 ruling 6)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def problems(self, kit, role, case_id):
        case = next(c for c in evals.load(kit, role) if c.id == case_id)
        return completeness(kit, case, self.dir / f"{role}-{case_id}" / "run")

    def test_every_case_is_complete(self):
        count = 0
        for role in CHECKERS + MAKERS + JUDGES:
            for case in cases(role):
                count += 1
                self.assertEqual(completeness(KIT, case, self.dir / f"{role}-{case.id}" / "run"), [])
        self.assertEqual(count, sum(declared(role) for role in SUITES))
        self.assertEqual(read_first(KIT, "look-reviewer"),
                         ["bible/canon/ledger.tsv", "bible/charter.md", "media/manifest.tsv"])

    def kit_copy(self):
        kit = self.dir / "kit"
        shutil.copytree(KIT, kit, ignore=shutil.ignore_patterns("__pycache__"))
        return kit

    def test_a_tree_under_factory_fails_d(self):
        kit = self.kit_copy()
        planted = kit / "evals" / "story-writer" / "01-tide-bell" / "tree" / ".factory" / "x"
        planted.parent.mkdir()
        planted.write_text("x\n")
        self.assertIn("d: story-writer/01-tide-bell: tree/ holds a path under .factory/",
                      self.problems(kit, "story-writer", "01-tide-bell"))

    def test_a_path_the_run_lacks_fails_b(self):
        kit = self.kit_copy()
        role = kit / "roles" / "story-writer.md"
        text = role.read_text(encoding="utf-8")
        role.write_text(text.replace("## Read first\n\n", "## Read first\n\n0. `bible/absent.md`.\n", 1),
                        encoding="utf-8")
        self.assertIn("b: story-writer/01-tide-bell: bible/absent.md is missing",
                      self.problems(kit, "story-writer", "01-tide-bell"))
        self.assertEqual(self.problems(KIT, "story-writer", "02-fox-lane"), [])

    def test_a_premise_with_no_format_file_fails_c(self):
        kit = self.kit_copy()
        premise = kit / "evals" / "head-of-story" / "03-tide-bell" / "tree" / "episodes" / "ep01" / "premise.md"
        premise.write_text(premise.read_text(encoding="utf-8").replace("format: short-four-act",
                                                                     "format: no-such-format"))
        self.assertIn("c: head-of-story/03-tide-bell: episodes/ep01/premise.md names format 'no-such-format'",
                      self.problems(kit, "head-of-story", "03-tide-bell"))


class SuitesTest(unittest.TestCase):
    def test_every_suite_loads_with_its_counts(self):
        for group in (CHECKERS, MAKERS, JUDGES):
            self.assertTrue(group)
        for role in SUITES:
            suite = cases(role)
            self.assertEqual(len(suite), declared(role), role)
            self.assertEqual(len(suite), sum(p.is_dir() for p in (KIT / "evals" / role).iterdir()), role)
            kinds = [c.kind for c in suite]
            for kind in evals.CASE_KINDS[KIND[role]]:
                self.assertGreaterEqual(kinds.count(kind), MINIMUM[kind], f"{role}: {kind}")
            if KIND[role] == "judge":
                old = sum(c.spec["better"] == "old" for c in suite)
                self.assertLessEqual(abs(old - (len(suite) - old)), 1, role)

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

    def test_suites_and_notes_cover_every_role(self):
        self.assertEqual(sorted(p.stem for p in (KIT / "roles").glob("*.md")), sorted(SUITES + NO_SUITE))
        self.assertEqual(set(SUITES) & set(NO_SUITE), set())
        for role in NO_SUITE:
            note = NOTE_TEXT.fullmatch((KIT / "evals" / (role + NOTE)).read_text(encoding="utf-8"))
            self.assertIsNotNone(note, role)
            self.assertEqual(note["role"], role)
            self.assertEqual(note["wave"] == "never", role == NEVER, role)
        self.assertIn(NEVER, NO_SUITE)
        readme = (KIT / "evals" / "README.md").read_text(encoding="utf-8")
        for rule in ("The lab owns the suites", "<nn>-<slug>", "suite.json", "evals/<role>.no-suite.md"):
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
                        self.assertLessEqual(count, 20, f"{role}/{case.id}/{name}")
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
                if case.kind == "refusal":
                    continue
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

    def test_reel_output_without_its_package_fails_lint(self):
        """Behaviour 8 of ISSUE-97: an editorial output needs its intent packet and a bible that exists."""
        packet, reel = "episodes/ep01/intent-packet.json", "episodes/ep01/reel/edit-list.json"

        def no_packet(run):
            (run / packet).unlink()

        def unknown_bible(run):
            data = json.loads((run / reel).read_text(encoding="utf-8"))
            (run / reel).write_text(json.dumps(dict(data, bible="A-9")), encoding="utf-8")

        for case in cases("editorial"):
            where = f"editorial/{case.id}"
            self.assertIn(packet, case.spec["expect_files"], where)
            self.assertIn(packet, case.task, where)
            self.assertTrue((case.path / "tree" / "bible" / "amendments" / "A-1.json").is_file(), where)
            output = FIX / "editorial" / case.id
            self.assertEqual(json.loads((output / reel).read_text(encoding="utf-8"))["bible"], "A-1", where)
            for seed in (no_packet, unknown_bible):
                with self.subTest(case=case.id, defect=seed.__name__), tempfile.TemporaryDirectory() as tmp:
                    run_dir = Path(tmp) / "run"
                    shutil.copytree(case.path / "tree", run_dir)
                    shutil.copytree(output, run_dir, dirs_exist_ok=True)
                    self.assertTrue(evals.grade(case, READY, run_dir=run_dir)["pass"], where)
                    seed(run_dir)
                    graded = evals.grade(case, READY, run_dir=run_dir)
                    self.assertFalse(graded["pass"], where)
                    self.assertTrue(graded["detail"].startswith("lint FAIL"), graded["detail"])
        tree = KIT / "evals" / "story-editor" / "07-bell-tower" / "tree"
        self.assertTrue((tree / packet).is_file())
        results = lint.run(tree, only=["structure", "media"])
        self.assertEqual(lint.overall(results), lint.PASS, lint.text(results))

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


class RefusalTest(unittest.TestCase):
    """ISSUE-289: a maker's refusal case and its twin."""

    def suite(self, role):
        refusal, twin, plain = TWINS[role]
        found = {c.id: c for c in cases(role)}
        return found[refusal], found[twin], found[plain]

    def recorded(self, role, case):
        return json.loads((FIX / role / f"{case.id}.json").read_text(encoding="utf-8"))

    def grade_ready(self, case, output, seed=None):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            shutil.copytree(case.path / "tree", run_dir)
            shutil.copytree(output, run_dir, dirs_exist_ok=True)
            if seed:
                seed(run_dir)
            graded = evals.grade(case, READY, run_dir=run_dir)
        return graded["pass"], graded["detail"]

    def test_blocked_naming_the_subject_passes_the_refusal_and_fails_the_twin(self):
        for role in TWINS:
            refusal, twin, _ = self.suite(role)
            result = self.recorded(role, refusal)
            self.assertEqual(harness.validate_result(refusal.role_kind, refusal.actor, result), [], role)
            graded = evals.grade(refusal, result)
            self.assertEqual((graded["pass"], graded["detail"]), (True, "refused: marker BLOCKED"), role)
            graded = evals.grade(twin, result)
            self.assertEqual((graded["pass"], graded["detail"]), (False, "marker BLOCKED, not READY"), role)
            other = json.loads(json.dumps(result))
            other["blocked"]["missing"] = re.sub(refusal.spec["forbid"][0], "Z99", other["blocked"]["missing"])
            graded = evals.grade(refusal, other)
            self.assertEqual((graded["pass"], graded["detail"]),
                             (False, r"blocked: missing does not match (?i)\bpim\b"), role)

    def test_ready_that_names_the_subject_fails(self):
        named = r"names (?i)\bpim\b"
        expected = {"story-writer": "lint PASS; " + named, "story-artist": "lint FAIL; " + named}
        for role in TWINS:
            refusal, twin, plain = self.suite(role)
            self.assertEqual(self.grade_ready(refusal, FIX / role / twin.id), (False, expected[role]), role)
            self.assertEqual(self.grade_ready(refusal, FIX / role / plain.id), (True, "lint PASS"), role)
        refusal, twin, _ = self.suite("story-artist")
        shot = Path("episodes/ep01/sequences/Q1/shots/Q1-3.json")

        def unlisted(run_dir):
            data = json.loads((run_dir / shot).read_text(encoding="utf-8"))
            self.assertIn("Pim", data["action"])
            (run_dir / shot).write_text(json.dumps(dict(data, characters=["mira"]), indent=2) + "\n",
                                        encoding="utf-8")

        self.assertEqual(self.grade_ready(refusal, FIX / "story-artist" / twin.id, unlisted),
                         (False, "lint PASS; " + named))

    def test_twins_are_near_misses(self):
        added = {"story-writer": ["bible/canon/characters/pim.md"],
                 "story-artist": ["bible/canon/characters/pim.md", "bible/style/characters/pim.md",
                                  "media/manifest.tsv"]}
        for role in TWINS:
            refusal, twin, _ = self.suite(role)
            self.assertEqual((refusal.kind, twin.kind), ("refusal", "replay"), role)
            self.assertEqual(twin.task, refusal.task, role)
            for key in ("lint", "expect_files"):
                self.assertEqual(twin.spec[key], refusal.spec[key], f"{role}: {key}")
            old, new = refusal.path / "tree", twin.path / "tree"
            changed = sorted(f for f in set(files(old)) | set(files(new))
                             if not (old / f).is_file() or not (new / f).is_file()
                             or (old / f).read_bytes() != (new / f).read_bytes())
            self.assertEqual(changed, added[role], role)
        refusals = sorted((role, c.id) for role in MAKERS for c in cases(role) if c.kind == "refusal")
        self.assertEqual(refusals, sorted((role, ids[0]) for role, ids in TWINS.items()))


class JudgeTest(unittest.TestCase):
    def test_pair_cases(self):
        for role in JUDGES:
            self.check_pairs(role)

    def check_pairs(self, role):
        ids = next(e["lint"] for e in PIPELINE if role in production.stage_roles(e, "judge"))
        for case in cases(role):
            where = f"{role}/{case.id}"
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

    def test_story_writer_refusal_offline(self):
        refusal, twin, _ = TWINS["story-writer"]
        premise = "episodes/ep01/premise.md"
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bin_dir, root, out = tmp / "bin", tmp / "root", tmp / "out"
            bin_dir.mkdir()
            root.mkdir()
            shutil.copy(FAKE, bin_dir / "fake-harness")
            os.chmod(bin_dir / "fake-harness", 0o755)
            blocked = json.loads((FIX / "story-writer" / f"{refusal}.json").read_text(encoding="utf-8"))
            written = (FIX / "story-writer" / twin / premise).read_text(encoding="utf-8")
            steps = [{"match": f"eval {re.escape(refusal)}(?![\\w-])", "result": blocked},
                     {"match": f"eval {re.escape(twin)}(?![\\w-])", "files": {premise: written},
                      "result": READY}]
            (bin_dir / "fake-harness.json").write_text(json.dumps(steps), encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if k != "FACTORY_LIVE"}
            env["FACTORY_FAKE_HARNESS"] = str(bin_dir / "fake-harness")
            done = subprocess.run([sys.executable, str(FACTORY), "eval", "story-writer", "--cases", "0[45]-*",
                                   "--root", str(root), "--out", str(out)],
                                  env=env, cwd=ROOT, capture_output=True, text=True, timeout=300)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("run 1: score 1.0\nrun 1: 1 blocked\n", done.stdout)
            summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["per_run"],
                             [{"blocked": 1, "lint_pass_rate": 1.0, "refusal_rate": 1.0, "score": 1.0}])


if __name__ == "__main__":
    unittest.main()
