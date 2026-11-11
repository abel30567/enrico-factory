"""canon: rules 1 to 3."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lint import LintCase, lint  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "lint_canon"
HEADER = "subject\tattribute\tvalue\ttag\tsource\n"


def ledger(*entries):
    """An episode ledger with one entry per line, the first on line 2."""
    rows = ",\n".join(f'  {{"subject": "{s}", "attribute": "{a}", "value": "{v}", "tag": "invented", "scene": "S1"}}'
                      for s, a, v in entries)
    return f"[\n{rows}\n]\n"


class Canon(LintCase):
    CHECKS = ("canon", "media", "provenance", "references", "spoiler")

    def canon(self, root):
        return lint.run(root, only=["canon"])["canon"]

    def test_clean_passes(self):
        result = self.canon(FIX / "clean")
        self.assertEqual(result.status, lint.PASS, result.findings)
        self.assertEqual({r.status for r in lint.run(FIX / "clean", only=self.CHECKS).values()}, {lint.PASS})

    def test_rule1_canon_ledger_contradicts_itself(self):
        self.assertOnly(FIX / "r1_canon_self", "canon", [("bible/canon/ledger.tsv", 4, "canon-self")])
        self.assertIn("'alive' contradicts 'dead' on line 2", self.canon(FIX / "r1_canon_self").findings[0].message)

    def test_rule1_same_value_after_normalising_is_not_a_contradiction(self):
        root = self.variant({"bible/canon/ledger.tsv": HEADER + "Mira\tHeight\t5 ft 2\tinvented\t\n"
                                                                "mira\t height \t 5 FT 2 \tinvented\t\n"},
                            base=FIX / "clean")
        self.assertEqual(self.canon(root).status, lint.PASS)

    def test_rule2_episode_contradicts_canon(self):
        self.assertOnly(FIX / "r2_episode_contradiction", "canon",
                        [("episodes/ep01/ledger.json", 3, "canon-contradiction")])
        message = self.canon(FIX / "r2_episode_contradiction").findings[0].message
        self.assertEqual(message, "mira / height: episode says '5 ft 4', canon says '5 ft 2' "
                                  "(bible/canon/ledger.tsv:2)")

    def test_rule2_trimmed_and_lower_cased(self):
        for entry in (("Mira", "Height", "5 ft 2"), (" mira ", " height ", "5 FT 2"), ("MIRA", "height", " 5 ft 2 ")):
            with self.subTest(entry):
                root = self.variant({"episodes/ep01/ledger.json": ledger(entry)}, base=FIX / "clean")
                self.assertEqual(self.canon(root).status, lint.PASS)
        root = self.variant({"episodes/ep01/ledger.json": ledger(("Mira", " Height ", "5 ft 4"))}, base=FIX / "clean")
        self.assertOnly(root, "canon", [("episodes/ep01/ledger.json", 2, "canon-contradiction")])

    def test_rule2_every_episode_ledger_is_read(self):
        root = self.variant({"episodes/ep02/ledger.json": ledger(("ferryman", "status", "dead")),
                             "episodes/ep03/ledger.json": ledger(("x", "y", "z"), ("ferryman", "status", "alive"))},
                            base=FIX / "clean")
        self.assertOnly(root, "canon", [("episodes/ep03/ledger.json", 3, "canon-contradiction")])

    def test_rule3_episode_ledgers_and_no_canon_ledger_is_error(self):
        result = self.canon(FIX / "r3_no_canon")
        self.assertEqual(result.status, lint.ERROR)
        self.assertIn("bible/canon/ledger.tsv", result.reason)

    def test_rule3_no_episode_ledgers_passes(self):
        root = self.variant({}, base=FIX / "clean")
        (root / "episodes/ep01/ledger.json").unlink()
        self.assertEqual(self.canon(root).status, lint.PASS)
        (root / "bible/canon/ledger.tsv").unlink()
        self.assertEqual(self.canon(root).status, lint.PASS)


if __name__ == "__main__":
    unittest.main()
