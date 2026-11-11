"""provenance: rules 1 to 3."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lint import FIX, LintCase, lint  # noqa: E402


class Provenance(LintCase):
    def test_clean_passes(self):
        self.assertEqual(lint.run(FIX / "clean", only=["provenance"])["provenance"].status, lint.PASS)

    def test_rule1_untagged_list_item_and_table_row(self):
        self.assertOnly(FIX / "r1_tag_missing", "provenance",
                        [("research/notes.md", 3, "tag-missing"), ("research/notes.md", 8, "tag-missing")])
        root = self.variant({"episodes/ep01/outline.md": "# Outline\n\n1. The keeper wakes.\n"})
        self.assertOnly(root, "provenance", [("episodes/ep01/outline.md", 3, "tag-missing")])

    def test_rule1_only_applies_to_research_bible_episodes(self):
        root = self.variant({"notes/free.md": "- untagged item\n\n| a |\n|---|\n| untagged row |\n"})
        self.assertEqual(lint.run(root, only=["provenance"])["provenance"].status, lint.PASS)

    def test_rule2_bare_text_and_art_tags(self):
        self.assertOnly(FIX / "r2_bare_tag", "provenance",
                        [("bible/notes.md", 3, "tag-source"), ("bible/notes.md", 4, "tag-source")])
        for line in ("- Fact [text](media:harbour-sketch).", "- Fact [art](sources/harbour-log.md).",
                     "- Fact [art](media:)."):
            with self.subTest(line):
                root = self.variant({"research/wrong.md": f"# Wrong\n\n{line}\n"})
                self.assertOnly(root, "provenance", [("research/wrong.md", 3, "tag-source")])

    def test_rule3_ledger_tags_and_sources(self):
        self.assertOnly(FIX / "r3_ledger_tag", "provenance", [
            ("bible/canon/ledger.tsv", 3, "ledger-source"),
            ("bible/canon/ledger.tsv", 4, "ledger-tag"),
            ("episodes/ep01/ledger.json", 3, "ledger-tag"),
            ("episodes/ep01/ledger.json", 4, "ledger-source"),
        ])

    def test_rule3_ledger_shape(self):
        root = self.variant({"episodes/ep02/ledger.json": '{"tag": "text"}\n'})
        self.assertOnly(root, "provenance", [("episodes/ep02/ledger.json", 1, "ledger-shape")])
        root = self.variant({"bible/canon/ledger.tsv": "subject\tattribute\tvalue\nkeeper\tboat colour\tblue\n"})
        self.assertOnly(root, "provenance", [("bible/canon/ledger.tsv", 1, "ledger-shape")])

    def test_unparseable_ledger_is_error(self):
        root = self.variant({"episodes/ep01/ledger.json": "[\n  {\"tag\": \n"})
        result = lint.run(root, only=["provenance"])["provenance"]
        self.assertEqual(result.status, lint.ERROR)
        self.assertIn("episodes/ep01/ledger.json", result.reason)


if __name__ == "__main__":
    unittest.main()
