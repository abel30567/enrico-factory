"""references: rules 4 to 6."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lint import FIX, LintCase, lint  # noqa: E402


class References(LintCase):
    def test_clean_passes(self):
        self.assertEqual(lint.run(FIX / "clean", only=["references"])["references"].status, lint.PASS)

    def test_rule4_missing_link_target(self):
        self.assertOnly(FIX / "r4_link_target", "references", [("notes/index.md", 3, "link-target")])

    def test_rule4_target_outside_root_fails(self):
        root = self.variant({"notes/up.md": "[out](../../outside.md)\n"})
        (root.parent / "outside.md").write_text("x\n")
        self.assertOnly(root, "references", [("notes/up.md", 1, "link-target")])

    def test_rule5_missing_json_path(self):
        self.assertOnly(FIX / "r5_json_path", "references", [("data/scene.json", 4, "json-path")])
        root = self.variant({"data/a.json": '[{"ref": "nowhere.txt"}, {"path": "sources/harbour-log.md"}]'})
        self.assertOnly(root, "references", [("data/a.json", 1, "json-path")])

    def test_rule6_media_id_not_in_manifest(self):
        self.assertOnly(FIX / "r6_media_id", "references",
                        [("data/shot.json", 3, "media-id"), ("index.md", 3, "media-id")])

    def test_rule6_no_manifest_is_error(self):
        root = self.variant({}, base="r6_media_id")
        (root / "media" / "manifest.tsv").unlink()
        results = lint.run(root)
        self.assertEqual(results["references"].status, lint.ERROR)
        self.assertIn("media/manifest.tsv", results["references"].reason)
        self.assertEqual(lint.exit_code(results), 2)

    def test_no_manifest_and_no_media_ids_passes(self):
        result = lint.run(FIX / "r1_tag_missing", only=["references"])["references"]
        self.assertEqual(result.status, lint.PASS)


if __name__ == "__main__":
    unittest.main()
