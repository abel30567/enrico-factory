"""kit: rule 1."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lint import FIX as LINT_FIX, ROOT, LintCase, lint  # noqa: E402

from kit.lib import roles  # noqa: E402

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

    def test_rule1_factory_dir_is_checked_before_kit_dir(self):
        root = self.variant({".factory/pipeline.json": "[]\n", "kit/pipeline.json": "[]\n"}, base=FIX / "clean")
        self.assertEqual([f.path for f in self.kit(root).findings], [".factory/formats/tiny-two-beat.md"])

    def test_rule1_factory_repository_kit_dir_with_pipeline(self):
        root = self.tree()
        shutil.copytree(FIX / "clean" / ".factory", root / "kit")
        self.assertEqual((self.kit(root).status, self.kit(root).findings), (lint.PASS, []))
        (root / "kit" / "pipeline.json").write_text("[]\n", encoding="utf-8")
        result = self.kit(root)
        self.assertEqual(result.status, lint.FAIL)
        self.assertEqual([f"{f.path}: {f.message}" for f in result.findings],
                         ["kit/" + p for p in roles.validate(root / "kit")])
        self.assertEqual([f.path for f in result.findings], ["kit/formats/tiny-two-beat.md"])

    def test_rule1_tree_without_a_kit_passes_and_never_reads_the_running_kit(self):
        empty = self.tree()
        no_pipeline = self.tree()
        shutil.copytree(FIX / "clean" / ".factory" / "roles", no_pipeline / "kit" / "roles")
        (no_pipeline / "kit" / "roles" / "watcher.md").write_text((FIX / "r1_role_parse.md").read_text())
        for root in (empty, no_pipeline):
            result = self.kit(root)
            self.assertEqual((result.status, result.findings, result.reason), (lint.PASS, [], ""))

    def test_rule1_framework_clean_fixture_passes_only_kit(self):
        proc = subprocess.run([sys.executable, str(ROOT / "kit" / "lib" / "cmd_lint.py"),
                               "--root", str(LINT_FIX / "clean"), "--only", "kit"],
                              capture_output=True, text=True)
        self.assertEqual((proc.returncode, proc.stdout), (0, "kit PASS\n"), proc.stderr)

    def tree(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def test_output_is_deterministic(self):
        root = self.variant({".factory/pipeline.json": "[]\n"}, base=FIX / "clean")
        self.assertEqual(lint.text(lint.run(root, only=["kit"])), lint.text(lint.run(root, only=["kit"])))


if __name__ == "__main__":
    unittest.main()
