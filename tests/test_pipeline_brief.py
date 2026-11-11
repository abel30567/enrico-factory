"""The shipped `kit/pipeline.json` matches docs/system-brief.md sections 6 and 16."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import roles  # noqa: E402

PIPELINE = ROOT / "kit" / "pipeline.json"
BRIEF_STAGES = ["research", "bible", "premise", "beats", "outline", "script", "sequences", "style",
                "board", "reel"]


def bad_afters(entries):
    """(room, stage, after) for every `after` that is neither empty nor an earlier entry of its room."""
    seen, bad = set(), []
    for entry in entries:
        room, after = entry["room"], entry["after"]
        if after and (room, after) not in seen:
            bad.append((room, entry["stage"], after))
        seen.add((room, entry["stage"]))
    return bad


def entry(room, stage, after):
    return {"room": room, "stage": stage, "after": after}


class ShippedPipelineTest(unittest.TestCase):
    def setUp(self):
        self.entries = roles.load_pipeline(PIPELINE)
        self.by_stage = {e["stage"]: e for e in self.entries}

    def test_stage_names_in_order(self):
        self.assertEqual([e["stage"] for e in self.entries], BRIEF_STAGES)

    def test_sequences_writes_sequence_json(self):
        self.assertEqual(self.by_stage["sequences"]["paths"], ["episodes/*/sequences/*/sequence.json"])

    def test_board_comes_after_sequences(self):
        self.assertEqual(self.by_stage["board"]["after"], "sequences")

    def test_reel_is_checked_by_the_story_editor(self):
        self.assertEqual(self.by_stage["reel"]["checker"], "story-editor")

    def test_every_after_names_an_earlier_entry_of_the_same_room(self):
        self.assertEqual(bad_afters(self.entries), [])


class BadAftersTest(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(bad_afters([entry("a", "one", ""), entry("a", "two", "one"),
                                     entry("b", "one", ""), entry("a", "three", "one")]), [])

    def test_after_names_a_later_entry(self):
        self.assertEqual(bad_afters([entry("a", "one", "two"), entry("a", "two", "")]),
                         [("a", "one", "two")])

    def test_after_names_an_entry_of_another_room(self):
        self.assertEqual(bad_afters([entry("a", "one", ""), entry("b", "two", "one")]),
                         [("b", "two", "one")])

    def test_after_names_no_entry(self):
        self.assertEqual(bad_afters([entry("a", "one", ""), entry("a", "two", "zero")]),
                         [("a", "two", "zero")])

    def test_after_names_its_own_entry(self):
        self.assertEqual(bad_afters([entry("a", "one", "one")]), [("a", "one", "one")])


if __name__ == "__main__":
    unittest.main()
