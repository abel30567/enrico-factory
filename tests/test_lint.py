"""The lint framework: discovery, PASS/FAIL/ERROR, output, exit codes, the [LINT] marker."""

import contextlib
import importlib.util
import io
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

    def git_init(self, root, ignore=None):
        """Make root a git work tree, blind to any user-wide ignore file; `ignore` is its .gitignore."""
        git(root, "init", "-q")
        (root / ".git" / "no-excludes").write_text("")
        git(root, "config", "core.excludesFile", str(root / ".git" / "no-excludes"))
        if ignore is not None:
            (root / ".gitignore").write_text(ignore, encoding="utf-8")


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True).stdout.decode()


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
                a = cli("--root", str(FIX / name), "--json", "--only", ",".join(CHECKS))
                b = cli("--root", str(FIX / name), "--json", "--only", ",".join(CHECKS))
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


class FileSet(LintCase):
    """`lint.files`: the files git would commit, or every file but `.git/` outside a work tree."""

    PYC = {"__pycache__/x.pyc": b"\x00compiled\n"}

    def media(self, root):
        r = lint.run(root, only=["media"])["media"]
        return r.status, [(f.path, f.line, f.rule) for f in r.findings]

    def test_ignored_file_is_not_read(self):
        root = self.variant(self.PYC)
        self.git_init(root, ignore="__pycache__/\n")
        self.assertEqual(self.media(root), (lint.PASS, []))
        self.assertNotIn(Path("__pycache__/x.pyc"), lint.files(root))

    def test_untracked_not_ignored_file_is_read(self):
        root = self.variant(self.PYC)
        self.git_init(root)
        self.assertEqual(self.media(root), (lint.FAIL, [("__pycache__/x.pyc", 1, "media-bytes")]))

    def test_outside_a_work_tree_every_file_but_git_is_read(self):
        root = self.variant({**self.PYC, ".gitignore": "__pycache__/\n", ".git/objects/x.png": b"\x00"})
        self.assertEqual(self.media(root), (lint.FAIL, [("__pycache__/x.pyc", 1, "media-bytes")]))
        self.assertIn(Path("__pycache__/x.pyc"), lint.files(root))
        self.assertFalse(any(p.parts[0] == ".git" for p in lint.files(root)))

    def test_tracked_and_untracked_files_sorted_and_deleted_files_dropped(self):
        root = self.variant({"research/gone.md": "- a tracked file deleted from disk\n",
                             "ignored/a.md": "- ignored\n"})
        self.git_init(root, ignore="ignored/\n")
        git(root, "add", "-A")
        (root / "research" / "gone.md").unlink()
        (root / "research" / "new.md").write_text("- an untracked untagged item\n")
        (root / "untracked.md").write_text("[x](missing.md)\n")
        got = lint.files(root)
        self.assertEqual(got, sorted(got))
        self.assertIn(Path("research/new.md"), got)
        self.assertIn(Path("research/harbour.md"), got)
        self.assertNotIn(Path("research/gone.md"), got)
        self.assertNotIn(Path("ignored/a.md"), got)
        self.assertIn("research/gone.md", git(root, "ls-files"))
        self.assertNotIn("research/new.md", git(root, "ls-files"))
        results = lint.run(root, only=CHECKS)
        self.assertEqual({k: r.status for k, r in results.items()},
                         {"media": lint.PASS, "provenance": lint.FAIL, "references": lint.FAIL})
        self.assertEqual([(f.path, f.line) for f in results["provenance"].findings], [("research/new.md", 1)])
        self.assertEqual([(f.path, f.line) for f in results["references"].findings], [("untracked.md", 1)])

    def test_ignored_files_skipped_by_provenance_and_references(self):
        root = self.variant({"research/draft.md": "- untagged\n[x](missing.md)\n"})
        self.git_init(root, ignore="draft.md\n")
        results = lint.run(root, only=CHECKS)
        self.assertEqual({k: r.status for k, r in results.items()}, dict.fromkeys(CHECKS, lint.PASS))



DEFECT = "[x](missing.md)\n"
IN_KIT = ".factory/evals/x/tree/research/a.md"


class InstalledKitSkipped(LintCase):
    """`lint.walk` and `lint.files` never list the top-level `.factory/`, the installed kit."""

    def references(self, root):
        r = lint.run(root, only=["references"])["references"]
        return r.status, [(f.path, f.line) for f in r.findings]

    def assertNoKit(self, root):
        self.assertEqual([p for p in lint.walk(root) if p.split("/")[0] == ".factory"], [])
        self.assertEqual([p for p in lint.files(root) if p.parts[0] == ".factory"], [])

    def test_outside_a_work_tree_factory_is_not_listed(self):
        root = self.variant({IN_KIT: DEFECT})
        self.assertNoKit(root)
        self.assertEqual(self.references(root), (lint.PASS, []))

    def test_outside_a_work_tree_same_file_outside_factory_is_a_finding(self):
        root = self.variant({"research/a.md": DEFECT})
        self.assertIn("research/a.md", lint.walk(root))
        self.assertEqual(self.references(root), (lint.FAIL, [("research/a.md", 1)]))

    def test_work_tree_untracked_factory_is_not_listed(self):
        root = self.variant({IN_KIT: DEFECT})
        self.git_init(root)
        self.assertIn(IN_KIT, git(root, "ls-files", "--others"))
        self.assertNoKit(root)
        self.assertEqual(self.references(root), (lint.PASS, []))

    def test_work_tree_tracked_factory_is_not_listed(self):
        root = self.variant({IN_KIT: DEFECT})
        self.git_init(root)
        git(root, "add", "-A")
        self.assertIn(IN_KIT, git(root, "ls-files", "--cached"))
        self.assertNoKit(root)
        self.assertEqual(self.references(root), (lint.PASS, []))

    def test_work_tree_same_file_outside_factory_is_a_finding(self):
        for add in (False, True):
            with self.subTest(tracked=add):
                root = self.variant({"research/a.md": DEFECT})
                self.git_init(root)
                if add:
                    git(root, "add", "-A")
                self.assertIn(Path("research/a.md"), lint.files(root))
                self.assertEqual(self.references(root), (lint.FAIL, [("research/a.md", 1)]))

    def test_only_the_top_level_directory_named_factory_is_skipped(self):
        listed = [".factory-state/note.md", "episodes/ep01/.factory/a.md"]
        for in_git in (False, True):
            with self.subTest(git=in_git):
                root = self.variant({rel: DEFECT for rel in listed})
                (root / "episodes" / ".factory").write_text("a file named .factory\n")
                if in_git:
                    self.git_init(root)
                for rel in [*listed, "episodes/.factory"]:
                    self.assertIn(rel, lint.walk(root))
                    self.assertIn(Path(rel), lint.files(root))
                self.assertEqual(self.references(root), (lint.FAIL, [(rel, 1) for rel in listed]))

    def test_top_level_file_named_factory_is_listed(self):
        for in_git in (False, True):
            with self.subTest(git=in_git):
                root = self.variant({".factory": "a file, not the kit\n"})
                if in_git:
                    self.git_init(root)
                self.assertIn(".factory", lint.walk(root))
                self.assertIn(Path(".factory"), lint.files(root))

    def test_walk_with_suffix_skips_factory(self):
        root = self.variant({IN_KIT: DEFECT, ".factory-state/note.md": DEFECT,
                             "episodes/ep01/.factory/a.md": DEFECT})
        got = lint.walk(root, ".md")
        self.assertNotIn(IN_KIT, got)
        self.assertEqual([p for p in got if p.startswith(".factory/")], [])
        self.assertIn(".factory-state/note.md", got)
        self.assertIn("episodes/ep01/.factory/a.md", got)


class FreshProduction(LintCase):
    """A production `factory init` makes from this repository's kit, in a git work tree."""

    def production(self):
        from kit.lib import cmd_init
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "prod"
        root.mkdir()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cmd_init.main([str(root)]), 0)
        self.assertTrue((root / ".factory" / "evals").is_dir())
        self.git_init(root)
        return root

    def summary(self, results):
        return {cid: (r.status, len(r.findings), r.reason) for cid, r in results.items()}

    def test_every_lint_passes(self):
        results = lint.run(self.production())
        bad = {cid: v for cid, v in self.summary(results).items() if v[0] in (lint.FAIL, lint.ERROR)}
        self.assertEqual(bad, {}, lint.text(results)[:2000])
        self.assertIn("kit", results)

    def test_invalid_installed_role_is_a_kit_finding_only(self):
        root = self.production()
        role = root / ".factory" / "roles" / "fact-checker.md"
        # A placeholder a checker may not have: roles.validate rejects it (a second `## Task` it does not).
        role.write_text(role.read_text(encoding="utf-8") + "{{BRANCH}}\n", encoding="utf-8")
        results = lint.run(root)
        self.assertEqual(results["kit"].status, lint.FAIL, results["kit"].reason)
        self.assertTrue(results["kit"].findings)
        self.assertTrue(all(f.path.startswith(".factory/roles/") for f in results["kit"].findings),
                        results["kit"].findings)
        others = {cid: v for cid, v in self.summary(results).items() if cid != "kit" and v[0] != lint.PASS}
        self.assertEqual(others, {})

if __name__ == "__main__":
    unittest.main()
