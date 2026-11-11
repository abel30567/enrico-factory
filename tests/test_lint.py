"""The lint framework: discovery, PASS/FAIL/ERROR, output, exit codes, the [LINT] marker."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import lint  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "lint"
CMD = ROOT / "kit" / "lib" / "cmd_lint.py"
CHECKS = ["media", "provenance", "references"]


def cli(*args):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, str(CMD), *args], capture_output=True, env=env, cwd=ROOT)


class TempCheck:
    """A check file dropped into kit/lib/lints/ for one test and removed afterwards."""

    def __init__(self, body):
        self.id = f"zz_temp_check_{os.getpid()}"
        self.path = lint.LINTS_DIR / f"{self.id}.py"
        self.body = body

    def __enter__(self):
        self.path.write_text(self.body, encoding="utf-8")
        return self

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)
        Path(importlib.util.cache_from_source(str(self.path))).unlink(missing_ok=True)
        sys.modules.pop(f"kit.lib.lints.{self.id}", None)


class LintCase(unittest.TestCase):
    """Helpers shared by the per-check tests."""

    CHECKS = CHECKS  # the checks a test class's fixtures were written for

    def assertOnly(self, root, check, expected):
        """`check` FAILs with exactly `expected` (path, line, rule); the other checks of CHECKS PASS."""
        results = lint.run(root, only=[check, *self.CHECKS])
        self.assertEqual(results[check].status, lint.FAIL, results[check].reason)
        self.assertEqual([(f.path, f.line, f.rule) for f in results[check].findings], expected)
        for other, r in results.items():
            if other != check:
                self.assertEqual(r.status, lint.PASS, f"{other}: {r.findings} {r.reason}")

    def variant(self, files, base="clean"):
        """A temporary copy of a fixture with `files` (path -> str or bytes) written over it."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "tree"
        shutil.copytree(FIX / base, root)
        for rel, body in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            if isinstance(body, bytes):
                (root / rel).write_bytes(body)
            else:
                (root / rel).write_text(body, encoding="utf-8")
        return root


RAISES = "def check(root):\n    raise RuntimeError('boom')\n"


class CleanTree(unittest.TestCase):
    def test_cli_clean_exits_0_and_prints_pass_for_three_checks(self):
        p = cli("--root", str(FIX / "clean"), "--only", ",".join(CHECKS))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stdout.decode(), "media PASS\nprovenance PASS\nreferences PASS\n")

    def test_discover_finds_checks_by_file_name(self):
        files = sorted(p.stem for p in lint.LINTS_DIR.iterdir()
                       if p.suffix == ".py" and not p.stem.startswith("_"))
        self.assertEqual(sorted(lint.discover()), files)
        self.assertLessEqual(set(CHECKS), set(files))

    def test_new_check_file_is_discovered_and_run_by_default(self):
        with TempCheck("def check(root):\n    return []\n") as tc:
            self.assertIn(tc.id, lint.discover())
            self.assertEqual(lint.run(FIX / "clean")[tc.id].status, lint.PASS)
            self.assertIn(f"{tc.id} PASS\n", cli("--root", str(FIX / "clean")).stdout.decode())
        self.assertNotIn(tc.id, lint.discover())
        self.assertNotIn(tc.id, lint.run(FIX / "clean"))

    def test_run_clean(self):
        results = lint.run(FIX / "clean", only=CHECKS)
        self.assertEqual({k: r.status for k, r in results.items()}, dict.fromkeys(CHECKS, "PASS"))


class Error(unittest.TestCase):
    def test_raising_check_is_error_exit_2_and_marker_error(self):
        with TempCheck(RAISES) as tc:
            results = lint.run(FIX / "clean")
            self.assertEqual(results[tc.id].status, lint.ERROR)
            self.assertEqual(results[tc.id].reason, "RuntimeError: boom")
            self.assertEqual(lint.exit_code(results), 2)
            self.assertEqual(lint.marker(results).splitlines()[0], "[LINT] ERROR")
            p = cli("--root", str(FIX / "clean"))
            self.assertEqual(p.returncode, 2)
            self.assertIn(f"{tc.id} ERROR RuntimeError: boom", p.stdout.decode())
        self.assertFalse(tc.path.exists())
        self.assertNotIn(tc.id, lint.discover())

    def test_error_wins_over_fail(self):
        with TempCheck(RAISES):
            results = lint.run(FIX / "r1_tag_missing")
            self.assertEqual(results["provenance"].status, lint.FAIL)
            self.assertEqual(lint.marker(results).splitlines()[0], "[LINT] ERROR")
            self.assertEqual(cli("--root", str(FIX / "r1_tag_missing")).returncode, 2)

    def test_check_without_check_function_is_error(self):
        with TempCheck("X = 1\n") as tc:
            self.assertEqual(lint.run(FIX / "clean", only=[tc.id])[tc.id].status, lint.ERROR)

    def test_unknown_check_and_missing_root_are_error(self):
        self.assertEqual(lint.run(FIX / "clean", only=["nope"])["nope"].status, lint.ERROR)
        self.assertEqual(cli("--root", str(FIX / "clean"), "--only", "nope").returncode, 2)
        results = lint.run(FIX / "does-not-exist")
        self.assertEqual({r.status for r in results.values()}, {lint.ERROR})


class Output(LintCase):
    def test_json_twice_is_byte_identical(self):
        for name in ("clean", "r3_ledger_tag", "r6_media_id"):
            with self.subTest(name):
                a = cli("--root", str(FIX / name), "--json")
                b = cli("--root", str(FIX / name), "--json")
                self.assertEqual(a.stdout, b.stdout)
                self.assertIn(json.loads(a.stdout)["status"], ("PASS", "FAIL"))

    def test_json_shape(self):
        data = json.loads(cli("--root", str(FIX / "r4_link_target"), "--json", "--only", ",".join(CHECKS)).stdout)
        self.assertEqual(data["status"], "FAIL")
        self.assertEqual(sorted(data["checks"]), CHECKS)
        self.assertEqual(data["checks"]["references"]["findings"], [{
            "path": "notes/index.md", "line": 3, "rule": "link-target",
            "message": "link target missing.md does not exist"}])

    def test_text_findings_numbered_and_sorted_by_check_path_line(self):
        root = self.variant({"z.png": "not really an image\n"}, base="r1_tag_missing")
        p = cli("--root", str(root), "--only", ",".join(CHECKS))
        self.assertEqual(p.returncode, 1)
        self.assertEqual(p.stdout.decode().splitlines(), [
            "media FAIL 1 finding",
            "provenance FAIL 2 findings",
            "references PASS",
            "B1 media z.png:1 media file (.png) in the tree; keep it in the media store",
            "B2 provenance research/notes.md:3 list item has none of [text], [art], [inferred], [invented]",
            "B3 provenance research/notes.md:8 table row has none of [text], [art], [inferred], [invented]",
        ])

    def test_only(self):
        p = cli("--root", str(FIX / "r1_tag_missing"), "--only", "media, references")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.decode(), "media PASS\nreferences PASS\n")

    def test_marker_pass_and_fail(self):
        self.assertEqual(lint.marker(lint.run(FIX / "clean", only=CHECKS)), "\n".join([
            "[LINT] PASS", "", "| Check | Result | Reason |", "|---|---|---|",
            "| media | PASS |  |", "| provenance | PASS |  |", "| references | PASS |  |",
            "", "Blockers: none", ""]))
        body = lint.marker(lint.run(FIX / "r5_json_path", only=CHECKS)).splitlines()
        self.assertEqual(body[0], "[LINT] FAIL")
        self.assertIn("| references | FAIL | 1 finding |", body)
        self.assertEqual(body[-2:], ["Blockers:",
                                     "- B1 references data/scene.json:4 source missing/notes.md does not exist"])


if __name__ == "__main__":
    unittest.main()
