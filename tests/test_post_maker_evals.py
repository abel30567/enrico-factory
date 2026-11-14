"""The post room's variant maker eval suites in kit/evals/ (brief section 13), run offline (ISSUE-394).

Recorded outputs in tests/fixtures/story_evals/ stand in for the roles: nothing here runs a model. No test
names a role in a loop list: the roles are the makers of the pipeline's `post/variant` entry.
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
FIX = ROOT / "tests" / "fixtures" / "story_evals"
FAKE = ROOT / "tests" / "fixtures" / "harness" / "fake-harness"
FACTORY = ROOT / "kit" / "factory"

ENTRY = next(e for e in json.loads((KIT / "pipeline.json").read_text(encoding="utf-8"))
             if e["room"] == "post" and e["stage"] == "variant")
ROLES = tuple(ENTRY["maker"])
READY = {"marker": "READY", "summary": "Done.",
         "evidence": [{"criterion": "stage files", "result": "written", "command": "ls"}]}


def tree_hash(path):
    digest = hashlib.sha256()
    for item in sorted(p for p in Path(path).rglob("*") if p.is_file()):
        digest.update(item.relative_to(path).as_posix().encode() + b"\0" + item.read_bytes())
    return digest.hexdigest()


def recorded(role, case):
    """The recorded output of the case: ``{path: text}``."""
    output = FIX / role / case.id
    return {p.relative_to(output).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(output.rglob("*")) if p.is_file()}


def run_eval(role, files):
    """``(completed process, summary, details)`` of ``factory eval role`` on the fake harness, each case's
    READY writing ``files(case)``; ``details`` the graded detail of each result."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        bin_dir, root, out = tmp / "bin", tmp / "root", tmp / "out"
        bin_dir.mkdir()
        root.mkdir()
        shutil.copy(FAKE, bin_dir / "fake-harness")
        os.chmod(bin_dir / "fake-harness", 0o755)
        steps = [{"match": f"eval {re.escape(case.id)}(?![\\w-])", "files": files(case), "result": READY}
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


def seeded(role, key, value):
    """Each case's recorded output of ``role`` with ``key`` set to ``value(case)``."""
    def files(case):
        out = {}
        for path, text in recorded(role, case).items():
            data = json.loads(text)
            data[key] = value(case)
            out[path] = json.dumps(data, indent=2) + "\n"
        return out
    return files


def first_spoiler(case):
    packet = case.path / "tree" / "episodes" / "ep01" / "intent-packet.json"
    return json.loads(packet.read_text(encoding="utf-8"))["spoilers"][0]


class PostMakerEvalsTest(unittest.TestCase):
    def test_every_variant_maker_has_a_suite(self):
        self.assertTrue(ROLES)
        for role in ROLES:
            with self.subTest(role=role):
                self.assertTrue((KIT / "evals" / role / "suite.json").is_file())
                self.assertFalse((KIT / "evals" / f"{role}.no-suite.md").exists())
                cases = evals.load(KIT, role)
                self.assertTrue(cases)
                for case in cases:
                    self.assertEqual(case.kind, "replay", case.id)
                    self.assertEqual(case.spec["lint"], ENTRY["lint"], case.id)

    def test_each_suite_runs_on_the_fake_harness(self):
        for role in ROLES:
            with self.subTest(role=role):
                before = tree_hash(KIT / "evals")
                done, summary, _ = run_eval(role, lambda c: recorded(role, c))
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(done.stdout, "run 1: score 1.0\nnoise floor: null over 1 runs (not measured)\n")
                self.assertEqual(summary["per_run"], [{"blocked": 0, "lint_pass_rate": 1.0, "score": 1.0}])
                self.assertEqual(tree_hash(KIT / "evals"), before)

    def test_a_withheld_output_scores_zero(self):
        for role in ROLES:
            with self.subTest(role=role):
                expected = {f"lint PASS; missing {' '.join(c.spec['expect_files'])}" for c in evals.load(KIT, role)}
                done, _, found = run_eval(role, lambda c: {})
                self.assertEqual(done.stdout.splitlines()[0], "run 1: score 0.0", done.stderr)
                self.assertEqual(len(expected), 1)
                self.assertTrue(next(iter(expected)).startswith("lint PASS; missing post/ep01/youtube/shorts/"))
                self.assertEqual(len(found), len(evals.load(KIT, role)))
                self.assertEqual(set(found), expected)

    def test_a_spoiler_or_a_bait_line_scores_zero(self):
        for role, files in (("caption-writer", seeded("caption-writer", "title", first_spoiler)),
                            ("cta-writer", seeded("cta-writer", "cta", lambda c: ["Tag a friend who loves Lanternfall."]))):
            with self.subTest(role=role):
                self.assertIn(role, ROLES)
                done, _, found = run_eval(role, files)
                self.assertEqual(done.stdout.splitlines()[0], "run 1: score 0.0", done.stderr)
                self.assertEqual(len(found), len(evals.load(KIT, role)))
                self.assertEqual(set(found), {"lint FAIL"})


if __name__ == "__main__":
    unittest.main()
