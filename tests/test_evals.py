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

from kit.lib import evals
from kit.lib.evals import EvalError

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "evals"
PROD = FIX / "prod"
KIT = PROD / ".factory"
FAKE = ROOT / "tests" / "fixtures" / "harness" / "fake-harness"
FACTORY = ROOT / "kit" / "factory"

JUDGE_ROLE = """---
role: umpire
room: story                # invented fixture role
kind: judge
analog: umpire
stage: pages
reads: A/**, B/**
model: checker
family: any
tools: Read
knowledge:
secrets:
gated: no
---
You compare A and B for {{REPO}} on {{TICKET}} in {{WORKTREE}}, pull request {{PR}}.

Task: {{TASK}}

Rules: {{RULES}}
"""

ANALYST_ROLE = JUDGE_ROLE.replace("role: umpire", "role: tallier").replace(
    "kind: judge", "kind: analyst")


def tree_hash(path):
    digest = hashlib.sha256()
    for item in sorted(p for p in Path(path).rglob("*") if p.is_file()):
        digest.update(item.relative_to(path).as_posix().encode() + b"\0" + item.read_bytes())
    return digest.hexdigest()


def note(note_id, severity, text, evidence="pages/page-1.md:1"):
    return {"id": note_id, "severity": severity, "text": text, "evidence": evidence}


def notes(*items):
    return {"marker": "NOTES", "summary": "s", "notes": list(items)}


def approved(*items):
    result = {"marker": "APPROVED", "summary": "s"}
    if items:
        result["notes"] = list(items)
    return result


READY = {"marker": "READY", "summary": "s",
         "evidence": [{"criterion": "page", "result": "written", "command": "ls pages"}]}


BLOCKED = {"marker": "BLOCKED", "summary": "s",
           "blocked": {"missing": "m", "tried": "t", "resume_from": "r"}}


REFUSAL = ('{"kind": "refusal", "lint": ["references"], "expect_files": ["pages/*.md"], '
           '"forbid": ["(?i)\\\\bbeacon\\\\b"]}')


def prefers(side):
    return {"marker": f"PREFERS {side}", "summary": "s", "prefers": {"choice": side, "cited": ["p1"]}}


class KitCopy(unittest.TestCase):
    """A writable copy of the fixture kit in a temporary directory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.kit = Path(self.tmp.name) / "kit"
        shutil.copytree(KIT, self.kit)

    def tearDown(self):
        self.tmp.cleanup()

    def add_judge(self):
        (self.kit / "roles" / "umpire.md").write_text(JUDGE_ROLE)
        case = self.kit / "evals" / "umpire" / "pair-01"
        for side in ("old", "new"):
            (case / side).mkdir(parents=True)
            (case / side / "page.md").write_text(f"# {side}\n")
        (case / "case.json").write_text('{"kind": "pair", "better": "new"}')
        (case / "task.md").write_text("Compare the two pages.\n")
        return case

    def add_refusal(self):
        replay = self.kit / "evals" / "scribe" / "replay-01"
        case = self.kit / "evals" / "scribe" / "refusal-01"
        shutil.copytree(replay / "tree", case / "tree")
        shutil.copy(replay / "task.md", case / "task.md")
        (case / "case.json").write_text(REFUSAL)
        return case


class LoadTest(KitCopy):
    def test_fixture_suites_load(self):
        proofer = evals.load(KIT, "proofer")
        self.assertEqual([c.id for c in proofer], ["clean-01", "seeded-01", "seeded-02"])
        self.assertEqual([c.kind for c in proofer], ["clean", "seeded", "seeded"])
        self.assertEqual({(c.role, c.role_kind, c.actor) for c in proofer},
                         {("proofer", "checker", "PROOFER")})
        self.assertEqual(proofer[2].spec["catches"], ["(?i)missing title", "(?i)page 2"])
        self.assertEqual(proofer[1].task, "Proofread pages/page-1.md.\n")
        scribe = evals.load(KIT, "scribe")
        self.assertEqual([(c.id, c.kind, c.role_kind) for c in scribe],
                         [("replay-01", "replay", "maker")])

    def test_pair_suite_loads_for_a_judge(self):
        self.add_judge()
        [case] = evals.load(self.kit, "umpire")
        self.assertEqual((case.id, case.kind, case.spec["better"]), ("pair-01", "pair", "new"))

    def test_seeded_defects_raise(self):
        case = self.kit / "evals" / "proofer" / "clean-01"
        defects = [
            ("unknown key", lambda: (case / "case.json").write_text('{"kind": "clean", "extra": 1}'),
             "proofer/clean-01: unknown key 'extra'"),
            ("unknown kind", lambda: (case / "case.json").write_text('{"kind": "mystery"}'),
             "proofer/clean-01: unknown kind 'mystery'"),
            ("pair for a checker",
             lambda: (case / "case.json").write_text('{"kind": "pair", "better": "new"}'),
             "proofer/clean-01: a pair case does not fit a role of kind checker"),
            ("missing task.md", lambda: (case / "task.md").unlink(),
             "proofer/clean-01: missing task.md"),
            ("regex does not compile",
             lambda: (case / "case.json").write_text('{"kind": "seeded", "catches": ["(open"]}'),
             r"proofer/clean-01: catches: regex '\(open' does not compile"),
            ("missing key", lambda: (case / "case.json").write_text('{"kind": "seeded"}'),
             "proofer/clean-01: missing key 'catches'"),
            ("not JSON", lambda: (case / "case.json").write_text("{"),
             "proofer/clean-01: case.json is not readable JSON"),
            ("missing tree", lambda: shutil.rmtree(case / "tree"), "proofer/clean-01: missing tree/"),
        ]
        original = (case / "case.json").read_text()
        for name, seed, message in defects:
            with self.subTest(name):
                seed()
                with self.assertRaisesRegex(EvalError, "^" + message + "$"):
                    evals.load(self.kit, "proofer")
                (case / "case.json").write_text(original)
                (case / "task.md").write_text("Proofread pages/page-1.md.\n")
                (case / "tree" / "pages").mkdir(parents=True, exist_ok=True)
        self.assertEqual(len(evals.load(self.kit, "proofer")), 3)

    def test_replay_defects_raise(self):
        case = self.kit / "evals" / "scribe" / "replay-01" / "case.json"
        case.write_text('{"kind": "replay", "lint": ["no-such-lint"], "expect_files": []}')
        with self.assertRaisesRegex(EvalError, "^scribe/replay-01: lint: 'no-such-lint' is not a lint$"):
            evals.load(self.kit, "scribe")
        case.write_text('{"kind": "seeded", "catches": ["x"]}')
        with self.assertRaisesRegex(EvalError, "a seeded case does not fit a role of kind maker"):
            evals.load(self.kit, "scribe")

    def test_refusal_loads_and_its_defects_raise(self):
        case = self.add_refusal()
        refusal = next(c for c in evals.load(self.kit, "scribe") if c.id == "refusal-01")
        self.assertEqual((refusal.kind, refusal.spec["forbid"]), ("refusal", [r"(?i)\bbeacon\b"]))
        spec = json.loads(REFUSAL)
        defects = [
            (dict(spec, forbid="beacon"), "forbid is not a non-empty list of regexes"),
            (dict(spec, forbid=[]), "forbid is not a non-empty list of regexes"),
            (dict(spec, forbid=[7]), "forbid is not a non-empty list of regexes"),
            (dict(spec, forbid=["(open"]), r"forbid: regex '\(open' does not compile"),
            (dict(spec, expect_files=[]), "expect_files is empty"),
            (dict(spec, lint=["no-such-lint"]), "lint: 'no-such-lint' is not a lint"),
        ]
        for seeded, message in defects:
            with self.subTest(message):
                (case / "case.json").write_text(json.dumps(seeded))
                with self.assertRaisesRegex(EvalError, "^scribe/refusal-01: " + message + "$"):
                    evals.load(self.kit, "scribe")
        (case / "case.json").write_text(REFUSAL)
        self.assertEqual(len(evals.load(self.kit, "scribe")), 2)
        (self.kit / "evals" / "proofer" / "clean-01" / "case.json").write_text(REFUSAL)
        with self.assertRaisesRegex(EvalError,
                                    "^proofer/clean-01: a refusal case does not fit a role of kind checker$"):
            evals.load(self.kit, "proofer")

    def test_pair_needs_old_and_new(self):
        case = self.add_judge()
        shutil.rmtree(case / "old")
        with self.assertRaisesRegex(EvalError, "^umpire/pair-01: missing old/$"):
            evals.load(self.kit, "umpire")

    def test_suites_and_roles(self):
        shutil.rmtree(self.kit / "evals" / "proofer")
        with self.assertRaisesRegex(EvalError, "^proofer: no cases in evals/proofer/$"):
            evals.load(self.kit, "proofer")
        with self.assertRaisesRegex(EvalError, "^nobody: no role file"):
            evals.load(self.kit, "nobody")
        with self.assertRaisesRegex(EvalError, "not a role id"):
            evals.load(self.kit, "../roles/proofer")
        (self.kit / "roles" / "tallier.md").write_text(ANALYST_ROLE)
        self.assertEqual(evals.load(self.kit, "tallier"), [])


class GradeTest(KitCopy):
    def setUp(self):
        super().setUp()
        self.cases = {c.id: c for c in evals.load(KIT, "proofer") + evals.load(KIT, "scribe")}

    def grade(self, case_id, result, **kwargs):
        return evals.grade(self.cases[case_id], result, **kwargs)

    def test_seeded(self):
        caught = self.grade("seeded-02", notes(note("B1", "Blocker", "Missing title.", "page 2")))
        self.assertEqual(caught, {"case": "seeded-02", "kind": "seeded", "marker": "NOTES",
                                  "pass": True, "detail": "caught"})
        missed = self.grade("seeded-02", notes(note("B1", "Blocker", "Missing title.")))
        self.assertFalse(missed["pass"])
        self.assertEqual(missed["detail"], "not caught: no Blocker matches (?i)page 2")
        issue_only = self.grade("seeded-01", notes(note("I1", "Issue", "Unsigned page.")))
        self.assertFalse(issue_only["pass"])
        self.assertFalse(self.grade("seeded-01", approved(note("I1", "Issue", "unsigned")))["pass"])

    def test_clean(self):
        quiet = self.grade("clean-01", approved(note("I1", "Issue", "Title could be shorter.")))
        self.assertEqual((quiet["pass"], quiet["marker"]), (True, "APPROVED"))
        self.assertTrue(self.grade("clean-01", notes(note("I1", "Issue", "Minor.")))["pass"])
        alarm = self.grade("clean-01", notes(note("B1", "Blocker", "Wrong.")))
        self.assertEqual((alarm["pass"], alarm["detail"]), (False, "false alarm: 1 Blocker(s)"))

    def test_blocked_fails_every_kind(self):
        blocked = self.grade("clean-01", BLOCKED)
        self.assertEqual(blocked, {"case": "clean-01", "kind": "clean", "marker": "BLOCKED",
                                   "pass": False, "detail": "blocked: marker BLOCKED"})
        self.assertTrue(self.grade("clean-01", approved())["pass"])
        alarm = self.grade("clean-01", notes(note("B1", "Blocker", "Wrong.")))
        self.assertEqual((alarm["pass"], alarm["detail"]), (False, "false alarm: 1 Blocker(s)"))
        seeded = self.grade("seeded-01", BLOCKED)
        self.assertEqual((seeded["pass"], seeded["detail"]), (False, "not caught: marker BLOCKED"))
        replay = self.grade("replay-01", BLOCKED, run_dir=self.replay_dir(None))
        self.assertEqual((replay["pass"], replay["detail"]), (False, "marker BLOCKED, not READY"))
        self.add_judge()
        [case] = evals.load(self.kit, "umpire")
        pair = evals.grade(case, BLOCKED)
        self.assertEqual((pair["pass"], pair["detail"]), (False, "marker BLOCKED, no preference given"))

    def replay_dir(self, page):
        run_dir = Path(self.tmp.name) / "run"
        shutil.rmtree(run_dir, ignore_errors=True)
        shutil.copytree(self.cases["replay-01"].path / "tree", run_dir)
        if page is not None:
            (run_dir / "pages").mkdir()
            (run_dir / "pages" / "page-1.md").write_text(page)
        return run_dir

    def test_replay(self):
        good = self.replay_dir("# Lantern\n\nFrom [the brief](../notes/brief.md).\n")
        passed = self.grade("replay-01", READY, run_dir=good)
        self.assertEqual((passed["pass"], passed["detail"]), (True, "lint PASS"))
        broken = self.replay_dir("# Lantern\n\nFrom [the brief](../notes/gone.md).\n")
        failed = self.grade("replay-01", READY, run_dir=broken)
        self.assertEqual((failed["pass"], failed["detail"]), (False, "lint FAIL"))
        empty = self.replay_dir(None)
        missing = self.grade("replay-01", READY, run_dir=empty)
        self.assertEqual((missing["pass"], missing["detail"]), (False, "lint PASS; missing pages/*.md"))
        blocked = {"marker": "BLOCKED", "summary": "s",
                   "blocked": {"missing": "m", "tried": "t", "resume_from": "r"}}
        self.assertFalse(self.grade("replay-01", blocked, run_dir=good)["pass"])

    def test_refusal(self):
        self.add_refusal()
        case = next(c for c in evals.load(self.kit, "scribe") if c.id == "refusal-01")
        refused = dict(BLOCKED, blocked={"missing": "pages/beacon.md: no sheet for Beacon", "tried": "t",
                                         "resume_from": "r"})
        self.assertEqual(evals.grade(case, refused),
                         {"case": "refusal-01", "kind": "refusal", "marker": "BLOCKED", "pass": True,
                          "detail": "refused: marker BLOCKED"})
        other = evals.grade(case, BLOCKED)
        self.assertEqual((other["pass"], other["detail"]),
                         (False, r"blocked: missing does not match (?i)\bbeacon\b"))
        good = self.replay_dir("# Lantern\n\nFrom [the brief](../notes/brief.md).\n")
        passed = evals.grade(case, READY, run_dir=good)
        self.assertEqual((passed["pass"], passed["detail"]), (True, "lint PASS"))
        named = self.replay_dir("# Beacon\n\nFrom [the brief](../notes/brief.md).\n")
        graded = evals.grade(case, READY, run_dir=named)
        self.assertFalse(graded["pass"])
        self.assertEqual(graded["detail"], r"lint PASS; names (?i)\bbeacon\b")
        empty = evals.grade(case, READY, run_dir=self.replay_dir(None))
        self.assertEqual((empty["pass"], empty["detail"]), (False, "lint PASS; missing pages/*.md"))
        addressed = dict(READY, marker="ADDRESSED",
                         addressed=[{"note": "B1", "by": "abc123", "action": "changed", "text": "Fixed."}])
        graded = evals.grade(case, addressed, run_dir=good)
        self.assertEqual((graded["marker"], graded["pass"], graded["detail"]),
                         ("ADDRESSED", False, "marker ADDRESSED, not READY or BLOCKED"))

    def test_pair_both_assignments(self):
        self.add_judge()
        [case] = evals.load(self.kit, "umpire")  # better: new

        def a_is_new(run):
            return hashlib.sha256(f"pair-01\n{run}".encode()).digest()[0] % 2 == 0

        run_new = next(r for r in range(1, 50) if a_is_new(r))
        run_old = next(r for r in range(1, 50) if not a_is_new(r))
        self.assertEqual(evals.sides("pair-01", run_new), {"A": "new", "B": "old"})
        self.assertEqual(evals.sides("pair-01", run_old), {"A": "old", "B": "new"})
        self.assertTrue(evals.grade(case, prefers("A"), run=run_new)["pass"])
        self.assertFalse(evals.grade(case, prefers("B"), run=run_new)["pass"])
        self.assertTrue(evals.grade(case, prefers("B"), run=run_old)["pass"])
        graded = evals.grade(case, prefers("A"), run=run_old)
        self.assertEqual((graded["pass"], graded["detail"]), (False, "preferred old, better is new"))
        none = {"marker": "NO PREFERENCE", "summary": "s", "prefers": {"choice": "none", "cited": []}}
        self.assertFalse(evals.grade(case, none, run=run_new)["pass"])

    def test_none_and_invalid_results(self):
        nothing = self.grade("seeded-01", None)
        self.assertEqual(nothing, {"case": "seeded-01", "kind": "seeded", "marker": None,
                                   "pass": False, "detail": "no result"})
        invalid = [
            {"marker": "NOTES", "summary": "s"},                     # NOTES without notes
            dict(READY),                                             # a checker may not post READY
            notes(note("I1", "Blocker", "unsigned")),                # id does not fit severity
            {"marker": 7, "summary": ["s"], "notes": "x"},
            ["NOTES"], "NOTES", 3, {},
        ]
        for result in invalid:
            with self.subTest(result=result):
                graded = self.grade("seeded-01", result)
                self.assertEqual((graded["pass"], graded["marker"]), (False, None))
                self.assertTrue(graded["detail"].startswith("invalid result: "))


class MetricsNoiseTest(unittest.TestCase):
    @staticmethod
    def graded(kind, *passes):
        return [{"kind": kind, "pass": p} for p in passes]

    def test_checker(self):
        graded = self.graded("seeded", True, False, True, True) + self.graded("clean", True, False, True)
        self.assertEqual(evals.metrics("checker", graded),
                         {"blocked": 0, "catch_rate": 0.75, "false_alarm_rate": 0.3333, "score": 0.4167})

    def test_maker_and_judge(self):
        self.assertEqual(evals.metrics("maker", self.graded("replay", True, True, False)),
                         {"blocked": 0, "lint_pass_rate": 0.6667, "score": 0.6667})
        self.assertEqual(evals.metrics("judge", self.graded("pair", True, False)),
                         {"agreement": 0.5, "blocked": 0, "score": 0.5})

    def test_maker_with_refusal_cases(self):
        graded = self.graded("replay", True, True, False) + self.graded("refusal", True, False)
        self.assertEqual(evals.metrics("maker", graded),
                         {"blocked": 0, "lint_pass_rate": 0.6667, "refusal_rate": 0.5, "score": 0.6})
        with self.assertRaisesRegex(EvalError, "^maker score: lint_pass_rate is null, no case to count$"):
            evals.metrics("maker", self.graded("refusal", True))

    def test_blocked_is_counted_and_is_no_alarm(self):
        def graded(kind, passed, marker):
            return {"kind": kind, "pass": passed, "marker": marker}
        checker = [graded("seeded", True, "NOTES"), graded("seeded", False, "BLOCKED"),
                   graded("clean", False, "BLOCKED"), graded("clean", False, "NOTES"),
                   graded("clean", True, "APPROVED")]
        self.assertEqual(evals.metrics("checker", checker),
                         {"blocked": 2, "catch_rate": 0.5, "false_alarm_rate": 0.3333, "score": 0.1667})
        maker = [graded("replay", True, "READY"), graded("replay", False, "BLOCKED")]
        self.assertEqual(evals.metrics("maker", maker),
                         {"blocked": 1, "lint_pass_rate": 0.5, "score": 0.5})
        judge = [graded("pair", True, "PREFERS A"), graded("pair", False, "BLOCKED")]
        self.assertEqual(evals.metrics("judge", judge), {"agreement": 0.5, "blocked": 1, "score": 0.5})
        self.assertEqual(evals.metrics("judge", self.graded("pair", True, False))["blocked"], 0)

    def test_zero_denominator_has_no_score(self):
        with self.assertRaisesRegex(EvalError, "false_alarm_rate is null"):
            evals.metrics("checker", self.graded("seeded", True))
        with self.assertRaisesRegex(EvalError, "catch_rate is null"):
            evals.metrics("checker", self.graded("clean", True))
        with self.assertRaisesRegex(EvalError, "lint_pass_rate is null"):
            evals.metrics("maker", [])
        with self.assertRaisesRegex(EvalError, "no eval score for a role of kind analyst"):
            evals.metrics("analyst", [])

    def test_noise(self):
        self.assertEqual(evals.noise([0.5, 0.5, 0.75, 0.75, 1.0]),
                         {"runs": 5, "mean": 0.7, "floor": 0.2092, "measured": True})
        self.assertEqual(evals.noise([0.5, 0.5, 0.75, 1.0]),
                         {"runs": 4, "mean": 0.6875, "floor": 0.2394, "measured": False})
        self.assertEqual(evals.noise([0.5]),
                         {"runs": 1, "mean": 0.5, "floor": None, "measured": False})


class CommandTest(unittest.TestCase):
    """`factory eval` end to end, on the fixture production, with the fake harness."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        shutil.copy(FAKE, self.bin / "fake-harness")
        os.chmod(self.bin / "fake-harness", 0o755)
        shutil.copy(FIX / "fake-harness.json", self.bin / "fake-harness.json")
        self.out = self.dir / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def steps(self, steps):
        (self.bin / "fake-harness.json").write_text(json.dumps(steps))
        for name in ("fake-harness.used", "fake-harness.calls.jsonl"):
            (self.bin / name).unlink(missing_ok=True)

    def calls(self):
        path = self.bin / "fake-harness.calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def eval(self, *args, fake=True):
        env = {k: v for k, v in os.environ.items() if k not in ("FACTORY_LIVE", "FACTORY_FAKE_HARNESS")}
        if fake:
            env["FACTORY_FAKE_HARNESS"] = str(self.bin / "fake-harness")
        return subprocess.run([sys.executable, str(FACTORY), "eval", *args], env=env, cwd=ROOT,
                              capture_output=True, text=True, timeout=120)

    def results(self, out=None):
        return [json.loads(line) for line in ((out or self.out) / "results.jsonl").read_text().splitlines()]

    def test_end_to_end_three_runs(self):
        before = tree_hash(KIT / "evals")
        done = self.eval("proofer", "--runs", "3", "--root", str(PROD), "--out", str(self.out))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "run 1: score 1.0\nrun 2: score 0.5\nrun 3: score 0.0\n"
                                      "noise floor: 0.5 over 3 runs (not measured)\n")
        self.assertEqual((self.out / "summary.json").read_text(),
                         (FIX / "expected-summary.json").read_text())
        lines = self.results()
        self.assertEqual(len(lines), 3 * 3)
        self.assertEqual([(r["run"], r["case"]) for r in lines],
                         [(run, case) for run in (1, 2, 3)
                          for case in ("clean-01", "seeded-01", "seeded-02")])
        self.assertEqual({tuple(sorted(r)) for r in lines},
                         {("case", "detail", "kind", "marker", "pass", "role", "run", "status")})
        for raw in (self.out / "results.jsonl").read_text().splitlines():
            self.assertEqual(raw, json.dumps(json.loads(raw), sort_keys=True))
        self.assertEqual([r["pass"] for r in lines if r["run"] == 2], [True, True, False])
        self.assertEqual(tree_hash(KIT / "evals"), before)
        calls = self.calls()
        self.assertEqual(len(calls), 9)
        self.assertTrue(all(c["fd3"] for c in calls))
        self.assertFalse(any(str(KIT) in c["cwd"] for c in calls))

        again = self.dir / "again"
        self.steps(json.loads((FIX / "fake-harness.json").read_text()))
        self.assertEqual(self.eval("proofer", "--runs", "3", "--root", str(PROD), "--out",
                                   str(again)).returncode, 0)
        for name in ("summary.json", "results.jsonl"):
            self.assertEqual((again / name).read_bytes(), (self.out / name).read_bytes())

    def test_outputs_hold_no_prompt_or_model_text(self):
        self.eval("proofer", "--runs", "3", "--root", str(PROD), "--out", str(self.out))
        written = (self.out / "results.jsonl").read_text() + (self.out / "summary.json").read_text()
        for text in ("Proofread", "unsigned", "Rules of power", "Notes on the pages", str(self.dir)):
            self.assertNotIn(text, written)

    def test_real_harness_refused_without_live(self):
        done = self.eval("proofer", "--harness", "real", "--root", str(PROD), "--out", str(self.out))
        self.assertEqual(done.returncode, 2)
        self.assertEqual(done.stderr, "eval: live runs are orchestrator-only (FACTORY_LIVE=1)\n")
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.out.exists())

    def test_fake_harness_must_be_named(self):
        done = self.eval("proofer", "--root", str(PROD), fake=False)
        self.assertEqual(done.returncode, 2)
        self.assertIn("FACTORY_FAKE_HARNESS", done.stderr)

    def test_failing_step_grades_false_and_exits_3(self):
        steps = json.loads((FIX / "fake-harness.json").read_text())[:3]
        steps[1]["exit"] = 1  # seeded-01
        self.steps(steps)
        done = self.eval("proofer", "--root", str(PROD), "--out", str(self.out))
        self.assertEqual(done.returncode, 3, done.stderr)
        lines = self.results()
        self.assertEqual([(r["case"], r["status"], r["pass"], r["marker"]) for r in lines],
                         [("clean-01", "ok", True, "APPROVED"),
                          ("seeded-01", "failed", False, None),
                          ("seeded-02", "ok", True, "NOTES")])
        self.assertEqual(len(self.calls()), 3)
        self.assertEqual(done.stdout.splitlines()[0], "run 1: score 0.5")

    def test_replay_suite_runs_in_the_copied_tree(self):
        page = "# Lantern\n\nFrom [the brief](../notes/brief.md).\n"
        self.steps([{"match": r"eval replay-01\b", "files": {"pages/page-1.md": page},
                     "result": READY}])
        done = self.eval("scribe", "--root", str(PROD), "--out", str(self.out))
        self.assertEqual(done.returncode, 0, done.stderr)
        summary = json.loads((self.out / "summary.json").read_text())
        self.assertEqual(summary["per_run"], [{"blocked": 0, "lint_pass_rate": 1.0, "score": 1.0}])
        self.assertEqual(summary["noise"], {"runs": 1, "mean": 1.0, "floor": None, "measured": False})
        self.assertIn("noise floor: null over 1 runs (not measured)", done.stdout)
        self.assertFalse((KIT / "evals" / "scribe" / "replay-01" / "tree" / "pages").exists())

    def test_run_directory_is_a_production_without_the_suite(self):
        listing = self.dir / "listing.txt"
        lister = self.dir / "lister"
        lister.write_text(f"""#!/bin/sh
if [ "$(basename "$PWD")" = run ]; then find . -type f | sort > '{listing}'; fi
exec '{self.bin / "fake-harness"}' "$@"
""")
        os.chmod(lister, 0o755)
        self.steps([{"match": r"eval replay-01\b", "result": READY}])
        env = {k: v for k, v in os.environ.items() if k not in ("FACTORY_LIVE", "FACTORY_FAKE_HARNESS")}
        env["FACTORY_FAKE_HARNESS"] = str(lister)
        done = subprocess.run([sys.executable, str(FACTORY), "eval", "scribe", "--root", str(PROD),
                               "--out", str(self.out)], env=env, cwd=ROOT, capture_output=True,
                              text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        files = listing.read_text().splitlines()
        self.assertIn("./.factory/roles/scribe.md", files)
        self.assertIn("./notes/brief.md", files)
        self.assertEqual([f for f in files if f.startswith("./.factory/evals") or f.endswith("case.json")],
                         [])

    def test_blocked_is_counted_and_printed(self):
        steps = json.loads((FIX / "fake-harness.json").read_text())[:3]
        steps[0]["result"] = BLOCKED  # clean-01
        self.steps(steps)
        done = self.eval("proofer", "--root", str(PROD), "--out", str(self.out))
        self.assertEqual(done.returncode, 0, done.stderr)
        clean = next(r for r in self.results() if r["case"] == "clean-01")
        self.assertEqual((clean["pass"], clean["marker"], clean["detail"]),
                         (False, "BLOCKED", "blocked: marker BLOCKED"))
        summary = json.loads((self.out / "summary.json").read_text())
        self.assertEqual(summary["per_run"],
                         [{"blocked": 1, "catch_rate": 1.0, "false_alarm_rate": 0.0, "score": 1.0}])
        self.assertEqual(done.stdout.splitlines()[:2], ["run 1: score 1.0", "run 1: 1 blocked"])

        again = self.dir / "again"
        self.steps(json.loads((FIX / "fake-harness.json").read_text())[:3])
        done = self.eval("proofer", "--root", str(PROD), "--out", str(again))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("blocked", done.stdout)
        self.assertEqual(json.loads((again / "summary.json").read_text())["per_run"][0]["blocked"], 0)

    def test_suite_without_a_score_stops_before_running(self):
        done = self.eval("proofer", "--cases", "seeded-*", "--root", str(PROD), "--out", str(self.out))
        self.assertEqual(done.returncode, 2)
        self.assertIn("false_alarm_rate is null", done.stderr)
        self.assertEqual(self.calls(), [])
        done = self.eval("proofer", "--cases", "nothing-*", "--root", str(PROD))
        self.assertEqual((done.returncode, done.stderr), (2, "eval: proofer: no case to run\n"))

    def test_out_inside_the_suite_is_refused(self):
        prod = self.dir / "prod"
        shutil.copytree(PROD, prod)
        before = tree_hash(prod / ".factory" / "evals")
        done = self.eval("proofer", "--root", str(prod), "--out",
                         str(prod / ".factory" / "evals" / "proofer" / "out"))
        self.assertEqual(done.returncode, 2)
        self.assertIn("read-only", done.stderr)
        self.assertEqual(tree_hash(prod / ".factory" / "evals"), before)
        self.assertEqual(self.calls(), [])

    def test_default_out_is_a_new_directory_printed(self):
        steps = json.loads((FIX / "fake-harness.json").read_text())[:3]
        self.steps(steps)
        done = self.eval("proofer", "--root", str(PROD))
        self.assertEqual(done.returncode, 0, done.stderr)
        last = done.stdout.splitlines()[-1]
        self.assertTrue(last.startswith("out: "))
        out = Path(last[len("out: "):])
        try:
            self.assertEqual(len(self.results(out)), 3)
        finally:
            shutil.rmtree(out)

    def test_without_a_production_the_kit_is_the_running_kit(self):
        done = self.eval("proofer", "--root", str(self.dir))
        self.assertEqual(done.returncode, 2)
        self.assertEqual(done.stderr, "eval: proofer: no role file roles/proofer.md\n")

    def test_runs_must_be_positive_and_help_works(self):
        self.assertEqual(self.eval("proofer", "--runs", "0", "--root", str(PROD)).returncode, 2)
        done = self.eval("--help")
        self.assertEqual(done.returncode, 0)
        self.assertTrue(done.stdout.startswith("usage: factory eval"))
        direct = subprocess.run([sys.executable, str(ROOT / "kit" / "lib" / "cmd_eval.py"), "--help"],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(direct.returncode, 0)


class BoundaryTest(unittest.TestCase):
    def test_no_harness_flag_and_no_github(self):
        for name in ("evals.py", "cmd_eval.py"):
            text = (ROOT / "kit" / "lib" / name).read_text()
            with self.subTest(name):
                self.assertIsNone(re.search(r"--(model|tools|mcp-config|json-schema)", text))
                self.assertIsNone(re.search(r"\b(github|gitstate)\b", text))


if __name__ == "__main__":
    unittest.main()
