"""The eval suites of the story room's three judges in kit/evals/ (brief section 13), run offline
(ISSUE-413).

`braintrust`, `audience-sim` and `distinctiveness-judge` each have four pair cases whose two versions
differ for one criterion of the role's Rubric. Hand-written results stand in for the roles: nothing
here runs a model.
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

from kit.lib import evals

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "kit"
FAKE = ROOT / "tests" / "fixtures" / "harness" / "fake-harness"
FACTORY = ROOT / "kit" / "factory"

ROLES = ("braintrust", "audience-sim", "distinctiveness-judge")
BETTER = {"01": "new", "02": "old", "03": "new", "04": "old"}
EP1 = "episodes/ep01/"
EP2 = "episodes/ep02/"
SHOTS = "sequences/Q1/shots/"
DIFFERS = {
    "braintrust": {
        "01-quarry-pond": [EP1 + "beats.md", EP1 + "script.md", EP1 + SHOTS + "Q1-3.json"],
        "02-night-market": [EP1 + "beats.md", EP1 + "script.md", EP1 + SHOTS + "Q1-4.json"],
        "03-clock-shop": [EP1 + "beats.md", EP1 + "script.md", EP1 + SHOTS + "Q1-3.json"],
        "04-reed-marsh": [EP1 + "beats.md", EP1 + "script.md", EP1 + SHOTS + "Q1-4.json"],
    },
    "audience-sim": {
        "01-snow-bus": [EP1 + "beats.md", EP1 + "script.md", EP1 + SHOTS + "Q1-1.json"],
        "02-tin-drum": [EP1 + "beats.md", EP1 + "script.md", EP1 + SHOTS + "Q1-1.json"],
        "03-mill-race": [EP1 + "beats.md", EP1 + "script.md", EP1 + SHOTS + "Q1-2.json"],
        "04-fig-yard": [EP1 + "script.md", EP1 + SHOTS + "Q1-3.json"],
    },
    "distinctiveness-judge": {
        "01-kelp-cove": [EP2 + "beats.md", EP2 + "premise.md", EP2 + "script.md", EP2 + SHOTS + "Q1-3.json"],
        "02-glass-roof": [EP2 + "beats.md", EP2 + "script.md"] + [EP2 + SHOTS + f"Q1-{n}.json" for n in range(1, 5)],
        "03-cliff-path": [EP1 + "beats.md", EP1 + "premise.md", EP1 + "script.md", EP1 + SHOTS + "Q1-3.json"],
        "04-hill-farm": [EP1 + "beats.md", EP1 + "script.md", EP1 + SHOTS + "Q1-3.json"],
    },
}
RELEASED = ("distribution/ep01/release.json", "episodes/ep01/beats.md", "episodes/ep01/premise.md")
RELEASE = ('{"job": "83730008351a0d35dface40b77d6c16cfd508884f1494229c8700d61c71dff52", "platform": "youtube", '
           '"post_id": "ep01-public", "sha256": "83730008351a0d35dface40b77d6c16cfd508884f1494229c8700d61c71dff52", '
           '"ticket": 1}\n')
CHARTER = ("# Charter\n\nOur show is not Skarrowfen, and our lead is not Wickabel. Skarrowfen is known for one "
           "premise: a child finds a door that opens onto yesterday.\n\nNobody in our show gets what they want "
           "by lying.\n")
NOISE = "noise floor: null over 1 runs (not measured)\n"
JUDGE_RUN = {"agreement": 1.0, "blocked": 0, "score": 1.0}


def tree_hash(path):
    digest = hashlib.sha256()
    for item in sorted(p for p in Path(path).rglob("*") if p.is_file()):
        digest.update(item.relative_to(path).as_posix().encode() + b"\0" + item.read_bytes())
    return digest.hexdigest()


def files(path):
    return sorted(p.relative_to(path).as_posix() for p in Path(path).rglob("*") if p.is_file())


def differing(old, new):
    """The sorted paths whose bytes differ, then a path only one side holds."""
    old_files, new_files = set(files(old)), set(files(new))
    changed = sorted(f for f in old_files & new_files if (old / f).read_bytes() != (new / f).read_bytes())
    return (changed + [f"only in old: {f}" for f in sorted(old_files - new_files)]
            + [f"only in new: {f}" for f in sorted(new_files - old_files)])


def prefers(side):
    return {"marker": f"PREFERS {side}", "summary": "One is better.", "prefers": {"choice": side, "cited": []}}


def right_side(case):
    return next(side for side, version in evals.sides(case.id, 1).items() if version == case.spec["better"])


def wrong_side(case):
    return "B" if right_side(case) == "A" else "A"


def run_eval(role, side):
    """``(completed process, summary)`` of ``factory eval role`` on the fake harness, ``side(case)`` the
    side each case prefers."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        bin_dir, root, out = tmp / "bin", tmp / "root", tmp / "out"
        bin_dir.mkdir()
        root.mkdir()
        shutil.copy(FAKE, bin_dir / "fake-harness")
        os.chmod(bin_dir / "fake-harness", 0o755)
        steps = [{"match": f"eval {re.escape(case.id)}(?![\\w-])", "result": prefers(side(case))}
                 for case in evals.load(KIT, role)]
        (bin_dir / "fake-harness.json").write_text(json.dumps(steps), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "FACTORY_LIVE"}
        env["FACTORY_FAKE_HARNESS"] = str(bin_dir / "fake-harness")
        done = subprocess.run([sys.executable, str(FACTORY), "eval", role, "--harness", "fake",
                               "--root", str(root), "--out", str(out)],
                              env=env, cwd=ROOT, capture_output=True, text=True, timeout=300)
        summary = out / "summary.json"
        return done, json.loads(summary.read_text(encoding="utf-8")) if summary.is_file() else None


class JudgeSuitesTest(unittest.TestCase):
    def test_1_suites_replace_the_notes(self):
        for role in ROLES:
            suite = KIT / "evals" / role
            self.assertEqual(json.loads((suite / "suite.json").read_text(encoding="utf-8")), {"cases": 4}, role)
            loaded = evals.load(KIT, role)
            self.assertEqual([case.id for case in loaded], sorted(DIFFERS[role]), role)
            for case in loaded:
                self.assertEqual(case.spec, {"kind": "pair", "better": BETTER[case.id[:2]]}, f"{role}/{case.id}")
            self.assertFalse((KIT / "evals" / f"{role}.no-suite.md").exists(), role)

    def test_2_sides_differ_only_in_the_named_files(self):
        for role in ROLES:
            for case_id, paths in DIFFERS[role].items():
                case = KIT / "evals" / role / case_id
                self.assertEqual(differing(case / "old", case / "new"), paths, f"{role}/{case_id}")
        base = DIFFERS["braintrust"]["01-quarry-pond"]
        seeds = (("media/manifest.tsv", sorted(base + ["media/manifest.tsv"])),
                 ("bible/amendments/A-1.json", base + ["only in old: bible/amendments/A-1.json"]))
        with tempfile.TemporaryDirectory() as tmp:
            for seed, expected in seeds:
                case = Path(tmp) / seed.replace("/", "-")
                shutil.copytree(KIT / "evals" / "braintrust" / "01-quarry-pond", case)
                if seed == "media/manifest.tsv":
                    with open(case / "new" / seed, "a", encoding="utf-8") as handle:
                        handle.write("extra-ref\t0\t1\tart-director\n")
                else:
                    (case / "new" / seed).unlink()
                got = differing(case / "old", case / "new")
                self.assertEqual(got, expected, seed)
                self.assertNotEqual(got, base, seed)

    def test_3_side_shapes(self):
        for role in ROLES:
            for case_id in DIFFERS[role]:
                for name in ("old", "new"):
                    where = f"{role}/{case_id}/{name}"
                    side = KIT / "evals" / role / case_id / name
                    held = files(side)
                    if role != "distinctiveness-judge":
                        self.assertEqual(len(held), 17, where)
                        self.assertNotIn("bible/charter.md", held, where)
                    elif case_id[:2] in ("01", "02"):
                        self.assertEqual(len(held), 20, where)
                        for path in RELEASED:
                            self.assertIn(path, held, where)
                        self.assertNotIn("episodes/ep01/script.md", held, where)
                        self.assertEqual((side / RELEASED[0]).read_text(encoding="utf-8"), RELEASE, where)
                    else:
                        self.assertEqual(len(held), 18, where)
                        self.assertEqual((side / "bible/charter.md").read_text(encoding="utf-8"), CHARTER, where)
                    for top in ("calibration", "research"):
                        self.assertFalse((side / top).exists(), f"{where}: {top}/")


class JudgeSuitesRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = tree_hash(KIT / "evals")

    def test_4_the_right_side_scores_one(self):
        for role in ROLES:
            with self.subTest(role=role):
                done, summary = run_eval(role, right_side)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(done.stdout, "run 1: score 1.0\n" + NOISE)
                self.assertEqual(summary["per_run"], [JUDGE_RUN])

    def test_5_the_wrong_side_scores_zero(self):
        for role in ROLES:
            with self.subTest(role=role):
                done, _ = run_eval(role, wrong_side)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(done.stdout, "run 1: score 0.0\n" + NOISE)

    def test_6_a_one_sided_judge_does_not_score_one(self):
        for role, score in (("braintrust", "0.25"), ("audience-sim", "0.75"), ("distinctiveness-judge", "0.5")):
            with self.subTest(role=role):
                done, _ = run_eval(role, lambda case: "A")
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(done.stdout.splitlines()[0], f"run 1: score {score}")

    def test_7_the_suites_are_not_written(self):
        for role in ROLES:
            run_eval(role, right_side)
            run_eval(role, wrong_side)
            run_eval(role, lambda case: "A")
        self.assertEqual(tree_hash(KIT / "evals"), self.before)


if __name__ == "__main__":
    unittest.main()
