"""kit: rule 1."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lint import FIX as LINT_FIX, ROOT, LintCase, lint  # noqa: E402

from kit.lib import roles  # noqa: E402
from kit.lib.lints import kit  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "lint_kit"


class Kit(LintCase):
    CHECKS = ("kit", "markers")

    def kit(self, root):
        return lint.run(root, only=["kit"])["kit"]

    def test_clean_passes_both_checks(self):
        results = lint.run(FIX / "clean", only=self.CHECKS)
        self.assertEqual({k: (r.status, r.findings) for k, r in results.items()},
                         {"kit": (lint.PASS, []), "markers": (lint.PASS, [])})

    def test_rule1_role_file_that_does_not_parse(self):
        root = self.variant({".factory/roles/watcher.md": (FIX / "r1_role_parse.md").read_text()}, base=FIX / "clean")
        self.assertOnly(root, "kit", [(".factory/roles/watcher.md", 1, "kit")])
        self.assertEqual(self.kit(root).findings[0].message,
                         "line 1: missing closing --- for the opening --- on this line")

    def test_rule1_every_problem_is_a_finding(self):
        root = self.variant({".factory/pipeline.json": "[]\n"}, base=FIX / "clean")
        result = self.kit(root)
        self.assertEqual(result.status, lint.FAIL)
        self.assertEqual([f.path + ": " + f.message for f in result.findings],
                         [".factory/" + p for p in roles.validate(root / ".factory")])
        self.assertEqual([f.path for f in result.findings], [".factory/formats/tiny-two-beat.md"])

    def test_rule1_root_without_factory_reads_the_running_kit(self):
        self.assertEqual(kit.RUNNING_KIT, ROOT / "kit")
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        for root in (LINT_FIX / "clean", Path(empty.name)):
            result = self.kit(root)
            self.assertNotEqual(result.status, lint.ERROR, result.reason)
            self.assertEqual([f"{f.path}: {f.message}" for f in result.findings],
                             ["kit/" + p for p in roles.validate(ROOT / "kit")])
            self.assertEqual(result.status, lint.FAIL if result.findings else lint.PASS)

    def test_output_is_deterministic(self):
        root = self.variant({".factory/pipeline.json": "[]\n"}, base=FIX / "clean")
        self.assertEqual(lint.text(lint.run(root, only=["kit"])), lint.text(lint.run(root, only=["kit"])))


if __name__ == "__main__":
    unittest.main()
