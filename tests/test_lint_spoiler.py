"""spoiler: rules 4 to 7."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lint import LintCase, lint  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "lint_spoiler"
POST = "post/ep01/youtube/extra.json"


class Spoiler(LintCase):
    CHECKS = ("canon", "media", "provenance", "references", "spoiler")

    def spoiler(self, root):
        return lint.run(root, only=["spoiler"])["spoiler"]

    def post(self, data, extra=None):
        """The clean fixture with one more packaging file holding `data`."""
        return self.variant({POST: json.dumps(data, indent=2) + "\n", **(extra or {})}, base=FIX / "clean")

    def test_clean_passes(self):
        result = self.spoiler(FIX / "clean")
        self.assertEqual(result.status, lint.PASS, result.findings)
        self.assertEqual({r.status for r in lint.run(FIX / "clean", only=self.CHECKS).values()}, {lint.PASS})

    def test_rule4_every_packaging_key_at_any_depth_and_no_other_key(self):
        data = {"title": "the map is forged", "caption": "the map is forged", "description": "the map is forged",
                "cta": ["the map is forged"], "hook": {"text": "the map is forged"},
                "notes": "the map is forged", "variants": [{"title": "the map is forged"}]}
        found = self.spoiler(self.post(data)).findings
        self.assertEqual([(f.path, f.line, f.message.split()[0]) for f in found], [
            (POST, 2, "title"), (POST, 3, "caption"), (POST, 4, "description"), (POST, 6, "cta"),
            (POST, 9, "hook"), (POST, 14, "title")])

    def test_rule4_only_post_files_of_that_episode(self):
        root = self.variant({"episodes/ep01/notes.json": '{"title": "the captain dies"}\n',
                             "post/ep01/youtube/notes.txt": "the captain dies\n"}, base=FIX / "clean")
        self.assertEqual(self.spoiler(root).status, lint.PASS)

    def test_rule5_spoiler_in_packaging(self):
        self.assertOnly(FIX / "r5_spoiler", "spoiler", [("post/ep01/tiktok/caption.json", 3, "spoiler")])
        self.assertEqual(self.spoiler(FIX / "r5_spoiler").findings[0].message,
                         "title gives away 'the captain dies' (episodes/ep01/intent-packet.json)")

    def test_rule5_case_whitespace_and_whole_words(self):
        for text, caught in (("The Captain  Dies tonight", True), ("the captain diesel engine", False),
                             ("THE\tCAPTAIN\n dies.", True), ("Before the captain dies!", True),
                             ("the captain diesthe", False), ("xthe captain dies", False)):
            with self.subTest(text):
                rules = [f.rule for f in self.spoiler(self.post({"caption": text})).findings]
                self.assertEqual(rules, ["spoiler"] if caught else [])

    def test_rule5_spoiler_phrase_whitespace_collapsed(self):
        packet = {"hook": "h", "spoilers": ["  The   map\tis forged "], "cta": [], "tone": "t"}
        root = self.post({"title": "the map is  forged"},
                         {"episodes/ep01/intent-packet.json": json.dumps(packet)})
        self.assertEqual([f.rule for f in self.spoiler(root).findings], ["spoiler"])

    def test_rule6_default_bait(self):
        self.assertOnly(FIX / "r6_bait", "spoiler", [("post/ep01/youtube/variant.json", 4, "bait")])
        for text in ("Like if you would sail", "Comment below to vote", "TAG A FRIEND", "Share this with Mira",
                     "Last chance to board", "Watch before it's deleted", "You won't believe the ending",
                     "Wait for it..."):
            with self.subTest(text):
                rules = [f.rule for f in self.spoiler(self.post({"cta": [text]})).findings]
                self.assertEqual(rules, ["bait"])

    def test_rule6_rulebook_patterns_replace_the_default(self):
        rulebook = {"rulebook/bait.json": json.dumps({"patterns": [r"\bsmash that\b"]})}
        root = self.post({"cta": ["Smash that follow", "Tag a friend"]}, rulebook)
        self.assertEqual([(f.line, f.rule) for f in self.spoiler(root).findings], [(3, "bait")])

    def test_rule6_bad_rulebook_is_error(self):
        for body in ('{"patterns": ["("]}', '{"patterns": "x"}', "[]"):
            with self.subTest(body):
                root = self.post({"cta": ["hi"]}, {"rulebook/bait.json": body})
                self.assertEqual(self.spoiler(root).status, lint.ERROR)

    def test_rule7_packaging_and_no_intent_packet_is_error(self):
        result = self.spoiler(FIX / "r7_no_packet")
        self.assertEqual(result.status, lint.ERROR)
        self.assertIn("episodes/ep02/intent-packet.json", result.reason)

    def test_rule7_no_packaging_text_needs_no_packet(self):
        root = self.variant({"post/ep02/youtube/variant.json": '{"duration": 30, "notes": "draft"}\n'},
                            base=FIX / "clean")
        self.assertEqual(self.spoiler(root).status, lint.PASS)


if __name__ == "__main__":
    unittest.main()
