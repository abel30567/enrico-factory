"""playbook: the lint of the playbook lessons (ISSUE-200), on tests/fixtures/lint_playbook/clean and on
copies of it with one defect each."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import lint, playbook  # noqa: E402

KIT = ROOT / "kit"
CLEAN = ROOT / "tests" / "fixtures" / "lint_playbook" / "clean"
PB1 = (CLEAN / "playbook" / "PB-1.md").read_text(encoding="utf-8")
TEXT = "A cold open holds the first three seconds better than a title card.\n"
BAD_VALUES = ("---\nconfidence: sure\nexpires: 2026-02-30\n"
              "evidence: nowhere.md:3, notes/season-1.md:99, a b\nseason: 1\n---\n")
ALL_DEFECTS = [
    "playbook FAIL 13 findings",
    "B1 playbook playbook/PB-04.md:1 PB-04.md is not named PB-<n>.md",
    "B2 playbook playbook/PB-1.md:1 confidence is not low, medium or high",
    "B3 playbook playbook/PB-1.md:1 evidence 'a b' is not <path>:<line> or #<n>",
    "B4 playbook playbook/PB-1.md:1 evidence notes/season-1.md:99: notes/season-1.md has 4 line(s)",
    "B5 playbook playbook/PB-1.md:1 evidence nowhere.md:3: nowhere.md is not in the tree",
    "B6 playbook playbook/PB-1.md:1 expires is not a date YYYY-MM-DD",
    "B7 playbook playbook/PB-1.md:1 no lesson text after the front matter",
    "B8 playbook playbook/PB-1.md:1 unknown key season",
    "B9 playbook playbook/PB-2.md:1 front matter: missing opening ---",
    "B10 playbook playbook/PB-3.md:1 no confidence",
    "B11 playbook playbook/PB-3.md:1 no evidence",
    "B12 playbook playbook/PB-3.md:1 no expires",
    "B13 playbook playbook/lesson.md:1 lesson.md is not named PB-<n>.md",
]


def factory(*args):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, str(KIT / "factory"), *map(str, args)], capture_output=True,
                          text=True, env=env, cwd=ROOT, stdin=subprocess.DEVNULL)


def lesson(evidence="notes/season-1.md:2,#12", expires="2027-05-31"):
    return f"---\nconfidence: high\nexpires: {expires}\nevidence: {evidence}\n---\n{TEXT}"


class Playbook(unittest.TestCase):
    def copy(self, **files):
        """A copy of the clean fixture with ``files`` (`playbook/<key>` with `_` for `-`, or a path) written."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "tree"
        shutil.copytree(CLEAN, root)
        for rel, text in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return root

    def messages(self, root):
        result = lint.run(root, only=["playbook"])["playbook"]
        self.assertNotEqual(result.status, lint.ERROR, result.reason)
        return [(f.path, f.line, f.rule, f.message) for f in result.findings]

    def assertOnly(self, root, rel, *messages):
        self.assertEqual(self.messages(root), [(rel, 1, "lesson", m) for m in messages])

    # Behaviour 1
    def test_clean_fixture_passes(self):
        p = factory("lint", "--root", CLEAN, "--only", "playbook")
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "playbook PASS\n", ""))

    def test_clean_fixture_passes_every_check(self):
        p = factory("lint", "--root", CLEAN)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        lines = p.stdout.splitlines()
        self.assertIn("playbook PASS", lines)
        self.assertTrue(all(line.endswith(" PASS") for line in lines), p.stdout)

    def test_files_that_are_not_lessons_and_an_expired_lesson_pass(self):
        root = self.copy(**{"playbook/.gitkeep": "", "playbook/notes.txt": "not a lesson\n",
                            "playbook/old/PB-9.md": "no front matter\n",
                            "playbook/PB-1.md": lesson(expires="2020-01-01")})
        self.assertEqual(self.messages(root), [])

    # Behaviour 2
    def test_no_confidence_no_evidence_no_expiry_fail(self):
        root = self.copy(**{"playbook/PB-3.md": "---\nevidence: ,\n---\ntext\n"})
        self.assertOnly(root, "playbook/PB-3.md", "no confidence", "no evidence", "no expires")

    def test_empty_values_count_as_absent(self):
        root = self.copy(**{"playbook/PB-3.md": "---\nconfidence:\nevidence:\nexpires:\n---\ntext\n"})
        self.assertOnly(root, "playbook/PB-3.md", "no confidence", "no evidence", "no expires")

    # Behaviour 3
    def test_bad_values_fail_with_seven_findings(self):
        root = self.copy(**{"playbook/PB-1.md": BAD_VALUES})
        self.assertOnly(root, "playbook/PB-1.md",
                        "confidence is not low, medium or high",
                        "evidence 'a b' is not <path>:<line> or #<n>",
                        "evidence notes/season-1.md:99: notes/season-1.md has 4 line(s)",
                        "evidence nowhere.md:3: nowhere.md is not in the tree",
                        "expires is not a date YYYY-MM-DD",
                        "no lesson text after the front matter",
                        "unknown key season")

    def test_last_line_of_an_evidence_file_passes(self):
        root = self.copy(**{"playbook/PB-1.md": lesson(evidence="notes/season-1.md:4")})
        self.assertEqual(self.messages(root), [])

    # Behaviour 4
    def test_no_front_matter_fails_only_with_its_message(self):
        root = self.copy(**{"playbook/PB-2.md": "no front matter\n"})
        self.assertOnly(root, "playbook/PB-2.md", "front matter: missing opening ---")

    # Behaviour 5
    def test_misnamed_lessons_fail(self):
        root = self.copy(**{"playbook/lesson.md": "anything\n", "playbook/PB-04.md": PB1})
        self.assertEqual(self.messages(root), [
            ("playbook/PB-04.md", 1, "lesson", "PB-04.md is not named PB-<n>.md"),
            ("playbook/lesson.md", 1, "lesson", "lesson.md is not named PB-<n>.md")])

    def test_every_defect_in_one_tree(self):
        root = self.copy(**{"playbook/PB-3.md": "---\nevidence: ,\n---\ntext\n", "playbook/PB-1.md": BAD_VALUES,
                            "playbook/PB-2.md": "no front matter\n", "playbook/lesson.md": "anything\n",
                            "playbook/PB-04.md": PB1})
        p = factory("lint", "--root", root, "--only", "playbook")
        self.assertEqual((p.returncode, p.stdout.splitlines(), p.stderr), (1, ALL_DEFECTS, ""))
        again = factory("lint", "--root", root, "--only", "playbook")
        self.assertEqual(again.stdout, p.stdout)

    # Behaviour 11
    def test_a_ref_after_a_space_is_a_comment(self):
        root = self.copy(**{"playbook/PB-1.md": lesson(evidence="#12")})
        self.assertOnly(root, "playbook/PB-1.md", "no evidence")

    def test_ref_zero_is_not_a_ref(self):
        root = self.copy(**{"playbook/PB-1.md": lesson(evidence="notes/season-1.md:2,#0")})
        self.assertOnly(root, "playbook/PB-1.md", "evidence '#0' is not <path>:<line> or #<n>")

    def test_clean_lesson_with_a_ref_passes(self):
        self.assertIn(",#12", PB1)
        self.assertEqual(self.messages(self.copy()), [])

    def test_unreadable_evidence_file_is_an_error(self):
        root = self.copy(**{"playbook/PB-1.md": lesson(evidence="notes/bin.md:1")})
        (root / "notes" / "bin.md").write_bytes(b"\xff\xfe\n")
        result = lint.run(root, only=["playbook"])["playbook"]
        self.assertEqual((result.status, result.reason), (lint.ERROR, "cannot read notes/bin.md: UnicodeDecodeError"))


class Library(unittest.TestCase):
    def test_number(self):
        self.assertEqual([playbook.number(p) for p in ("playbook/PB-1.md", "PB-10.md", "playbook/PB-04.md",
                                                       "playbook/PB-0.md", "playbook/lesson.md", "PB-1.txt")],
                         [1, 10, None, None, None, None])

    def test_lesson_paths_by_number_misnamed_last(self):
        paths = ["playbook/PB-10.md", "playbook/notes.md", "playbook/PB-2.md", "playbook/old/PB-1.md",
                 "playbook/PB-3.txt", "playbook/.gitkeep", "notes/PB-4.md", "playbook/a.md", Path("playbook/PB-1.md")]
        self.assertEqual(playbook.lesson_paths(paths), ["playbook/PB-1.md", "playbook/PB-2.md", "playbook/PB-10.md",
                                                        "playbook/a.md", "playbook/notes.md"])

    def test_a_lesson_holds_on_its_expiry_date(self):
        text = lesson(expires="2026-11-13")
        self.assertEqual(playbook.expires(text), date(2026, 11, 13))
        self.assertFalse(playbook.expired(text, date(2026, 11, 13)))
        self.assertTrue(playbook.expired(text, date(2026, 11, 14)))

    def test_no_valid_date_never_expires(self):
        for text in (lesson(expires="2026-02-30"), lesson(expires="2026-1-01"), "no front matter\n",
                     "---\nconfidence: high\n---\ntext\n"):
            self.assertIsNone(playbook.expires(text))
            self.assertFalse(playbook.expired(text, date(2100, 1, 1)))


if __name__ == "__main__":
    unittest.main()
