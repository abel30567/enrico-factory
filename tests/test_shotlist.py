"""Tests for kit/lib/shotlist.py: the rows of the shot list ``factory tick`` writes at intake."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import shotlist  # noqa: E402

REL = "episodes/ep01/reel/edit-list.json"
EDIT_LIST = ROOT / "tests" / "fixtures" / "intake_filing" / "production" / REL
SHOT_LIST = [{"shot": "Q1-1", "first": 0, "last": 59}, {"shot": "Q1-2", "first": 60, "last": 131},
             {"shot": "Q2-1", "first": 132, "last": 233}]


class Rows(unittest.TestCase):
    def test_the_fixture(self):
        rows, reason = shotlist.rows(EDIT_LIST.read_text(), REL)
        self.assertEqual((rows, reason), (SHOT_LIST, ""))
        self.assertEqual([list(row) for row in rows], [["shot", "first", "last"]] * 3)

    def test_no_events(self):
        self.assertEqual(shotlist.rows(json.dumps({"fps": 24, "events": []}), REL), ([], ""))

    def test_not_on_the_base(self):
        self.assertEqual(shotlist.rows(None, REL), (None, f"{REL} is not on the base"))

    def test_not_json(self):
        self.assertEqual(shotlist.rows("{", REL), (None, f"{REL} is not JSON"))

    def test_not_an_edit_list(self):
        self.assertEqual(shotlist.rows("[]", REL),
                         (None, f"{REL}: not {{fps, events: [...]}} with fps above zero"))

    def test_not_whole_frames(self):
        data = json.loads(EDIT_LIST.read_text())
        data["events"][1]["start"] = 2.51
        self.assertEqual(shotlist.rows(json.dumps(data), REL),
                         (None, f"{REL}: event 2: start 2.51 s at 24 fps is not a whole frame"))


if __name__ == "__main__":
    unittest.main()
