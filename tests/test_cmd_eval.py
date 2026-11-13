"""Tests for kit/lib/cmd_eval.py: with --out, `factory eval` keeps each result object under runs/.

The world is the one of test_evals.CommandTest: the fixture production under tests/fixtures/evals/prod,
suite `proofer`, and the fake harness copied into a temporary bin/ with its steps written there.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_evals import BLOCKED, FACTORY, FAKE, FIX, PROD, ROOT  # noqa: E402

CASES = ("clean-01", "seeded-01", "seeded-02")


def kept_text(result):
    return json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def files(path):
    return sorted(str(p.relative_to(path)) for p in path.rglob("*") if p.is_file())


class KeepTest(unittest.TestCase):
    """`factory eval --out` keeps the result object of each run of each case as runs/<run>/<case>.json."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        shutil.copy(FAKE, self.bin / "fake-harness")
        os.chmod(self.bin / "fake-harness", 0o755)
        self.fixture = json.loads((FIX / "fake-harness.json").read_text(encoding="utf-8"))
        self.steps(self.fixture)
        self.out = self.dir / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def steps(self, steps):
        (self.bin / "fake-harness.json").write_text(json.dumps(steps), encoding="utf-8")
        for name in ("fake-harness.used", "fake-harness.calls.jsonl"):
            (self.bin / name).unlink(missing_ok=True)

    def eval(self, *args):
        env = {k: v for k, v in os.environ.items() if k not in ("FACTORY_LIVE", "FACTORY_FAKE_HARNESS")}
        env["FACTORY_FAKE_HARNESS"] = str(self.bin / "fake-harness")
        return subprocess.run([sys.executable, str(FACTORY), "eval", "proofer", "--root", str(PROD), *args],
                              env=env, cwd=ROOT, capture_output=True, text=True, timeout=120)

    def results(self):
        return [json.loads(line) for line in (self.out / "results.jsonl").read_text().splitlines()]

    def test_every_result_is_kept(self):
        steps = [dict(step) for step in self.fixture[:3]]
        steps[0]["result"] = BLOCKED
        steps[2]["result"] = {**steps[2]["result"], "summary": "Café pages."}
        self.steps(steps)
        done = self.eval("--out", str(self.out))
        self.assertEqual(done.returncode, 0, done.stderr)
        runs = self.out / "runs"
        self.assertEqual(files(runs), [f"1/{case}.json" for case in CASES])
        for step, case in zip(steps, CASES):
            self.assertEqual((runs / "1" / f"{case}.json").read_text(encoding="utf-8"),
                             kept_text(step["result"]))
        raw = (runs / "1" / "seeded-02.json").read_bytes()
        self.assertIn('"summary": "Café pages."'.encode("utf-8"), raw)
        self.assertNotIn(b"\\u00e9", raw)

    def test_three_runs_other_outputs_unchanged(self):
        done = self.eval("--runs", "3", "--out", str(self.out))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "run 1: score 1.0\nrun 2: score 0.5\nrun 3: score 0.0\n"
                                      "noise floor: 0.5 over 3 runs (not measured)\n")
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["results.jsonl", "runs", "summary.json"])
        self.assertEqual((self.out / "summary.json").read_text(), (FIX / "expected-summary.json").read_text())
        runs = self.out / "runs"
        self.assertEqual(files(runs), [f"{run}/{case}.json" for run in (1, 2, 3) for case in CASES])
        for index, name in enumerate(f"{run}/{case}.json" for run in (1, 2, 3) for case in CASES):
            self.assertEqual((runs / name).read_text(encoding="utf-8"), kept_text(self.fixture[index]["result"]))
        self.assertNotIn("Notes on the pages", (self.out / "results.jsonl").read_text())

        again = self.dir / "again"
        self.steps(self.fixture)
        self.assertEqual(self.eval("--runs", "3", "--out", str(again)).returncode, 0)
        self.assertEqual(files(again / "runs"), files(runs))
        for name in files(runs):
            self.assertEqual((again / "runs" / name).read_bytes(), (runs / name).read_bytes())

    def test_no_result_no_file(self):
        steps = [dict(step) for step in self.fixture[:3]]
        steps[1]["exit"] = 1  # seeded-01
        self.steps(steps)
        done = self.eval("--out", str(self.out))
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertEqual(sorted(p.name for p in (self.out / "runs" / "1").iterdir()),
                         ["clean-01.json", "seeded-02.json"])

    def test_invalid_result_is_kept(self):
        invalid = {"marker": "NOTES", "summary": "s"}
        steps = [dict(step) for step in self.fixture[:3]]
        steps[1]["result"] = invalid
        self.steps(steps)
        self.eval("--out", str(self.out))
        line = next(r for r in self.results() if r["case"] == "seeded-01")
        self.assertEqual(line["detail"], "invalid result: 1 problem(s)")
        self.assertEqual((self.out / "runs" / "1" / "seeded-01.json").read_text(encoding="utf-8"),
                         kept_text(invalid))

    def test_without_out_nothing_is_kept(self):
        done = self.eval()
        self.assertEqual(done.returncode, 0, done.stderr)
        last = done.stdout.splitlines()[-1]
        self.assertTrue(last.startswith("out: "), done.stdout)
        out = Path(last[len("out: "):])
        try:
            self.assertEqual(sorted(p.name for p in out.iterdir()), ["results.jsonl", "summary.json"])
        finally:
            shutil.rmtree(out)

    def test_used_out_is_refused(self):
        (self.out / "runs").mkdir(parents=True)
        done = self.eval("--out", str(self.out))
        self.assertEqual(done.returncode, 2)
        self.assertEqual(done.stdout, "")
        self.assertEqual(done.stderr, f"eval: --out {self.out} already holds runs/: give a directory without it\n")
        self.assertFalse((self.bin / "fake-harness.calls.jsonl").exists())
        self.assertFalse((self.out / "results.jsonl").exists())

        old = self.dir / "old"
        old.mkdir()
        (old / "results.jsonl").write_text("old\n", encoding="utf-8")
        self.steps(self.fixture[:3])
        done = self.eval("--out", str(old))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(files(old / "runs"), [f"1/{case}.json" for case in CASES])


if __name__ == "__main__":
    unittest.main()
