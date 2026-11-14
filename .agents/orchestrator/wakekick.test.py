# Tests for wakekick.py. Run: python3 -I wakekick.test.py (also part of `orch selftest`).
import sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wakekick  # noqa: E402

MERGED = "merged #7 ISSUE-3: tick the plan"
IDLE = "0 work stream(s) in flight, fewer than two: write the next ticket and label it status:ready"


class Note(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        self.kicks = 0

    def kick(self):
        self.kicks += 1
        return True

    def note(self, needs, in_wake=False, wake_alive=False):
        return wakekick.note(needs, self.state, in_wake, wake_alive, self.kick)

    def pending(self):
        f = self.state / "wake.pending"
        return f.read_text().splitlines() if f.exists() else []

    def test_a_new_need_line_starts_the_wake(self):
        self.assertEqual(self.note([MERGED]), 1)
        self.assertEqual(self.kicks, 1)
        self.assertEqual(self.pending(), [MERGED])

    def test_no_need_line_starts_nothing(self):
        self.assertEqual(self.note([]), 0)
        self.assertEqual(self.kicks, 0)

    def test_a_need_line_that_stays_never_starts_a_second_wake(self):
        self.note([IDLE])
        (self.state / "wake.pending").unlink()      # wake.sh took it
        self.assertEqual(self.note([IDLE]), 0)
        self.assertEqual(self.note([IDLE.replace("0 work", "1 work")]), 0)
        self.assertEqual(self.kicks, 1)

    def test_a_need_line_that_went_away_and_came_back_is_new(self):
        self.note([IDLE])
        (self.state / "wake.pending").unlink()
        self.note([])
        self.assertEqual(self.note([IDLE]), 1)
        self.assertEqual(self.kicks, 2)

    def test_a_live_wake_keeps_the_line_pending_until_it_ends(self):
        self.assertEqual(self.note([MERGED], wake_alive=True), 0)
        self.assertEqual(self.kicks, 0)
        self.assertEqual(self.note([]), 1)             # the line is gone from the tick, not from pending
        self.assertEqual(self.pending(), [MERGED])
        self.assertEqual(self.kicks, 1)

    def test_a_tick_run_by_the_wake_is_seen_not_pending(self):
        self.assertEqual(self.note([IDLE], in_wake=True, wake_alive=True), 0)
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.note([IDLE]), 0)         # the wake already read it
        self.assertEqual(self.kicks, 0)

    def test_a_pending_line_is_held_once(self):
        self.note([IDLE], wake_alive=True)
        self.note([], wake_alive=True)
        self.note([IDLE], wake_alive=True)
        self.assertEqual(self.pending(), [IDLE])

    def test_a_failed_start_reports_nothing_and_keeps_the_line(self):
        self.assertEqual(wakekick.note([MERGED], self.state, False, False, lambda: False), 0)
        self.assertEqual(self.pending(), [MERGED])

    def test_minutes_do_not_make_a_stalled_line_new(self):
        self.assertEqual(wakekick.key("#4 K-1 STALLED: V4 has written nothing for 31 min"),
                         wakekick.key("#4 K-1 STALLED: V4 has written nothing for 41 min"))
        self.assertNotEqual(wakekick.key("merged #7 K-1"), wakekick.key("merged #8 K-1"))


if __name__ == "__main__":
    unittest.main()
