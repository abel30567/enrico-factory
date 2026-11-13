"""Tests for kit/lib/monitor_chasing_metrics.py (ISSUE-370): the chasing-metrics monitor of the producer gate.

Every test runs `factory tick` on the world (15, 16, 17) of tests/test_tick.py: ticket #15 at the summary stage
pitch, its pitch #16 selected, ep07 to ep09 locked. The pitches, the calibration set and the records are written
inside each world; no fixture is added. The monitor is found in kit/lib, as a production finds it.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from kit.lib import calibration  # noqa: E402
from test_tick import LABEL, Case, lines  # noqa: E402

PITCHES = {"ep07": "# Pitch\n\nThe keeper and her daughter guard the harbour lighthouse in a winter storm.\n",
           "ep08": "# Pitch\n\nThe daughter leaves the harbour for a city of glass towers and neon markets.\n",
           "ep09": "# Pitch\n\nThe keeper and her daughter guard the glass towers of the harbour city.\n"}
NEW = "# Pitch\n\nA robot detective solves murders on a desert moon.\n"
LOCKS = {"ep07": (31, 32, "2026-11-04T09:00:00Z"), "ep08": (33, 34, "2026-11-05T09:00:00Z"),
         "ep09": (35, 36, "2026-11-06T09:00:00Z")}
ENDS = {"ep07/beta": "0.3", "ep08/beta": "0.4", "ep09/beta": "0.5"}
REST = "ep07, ep08, ep09: end retention rises (0.3, 0.4, 0.5) while distinctiveness falls (1.00, 0.67, 0.00)"
REASON = f"beta {REST}"
HOLD = [f"NEED - monitor chasing-metrics: {REASON}", f"NEED #15 chasing-metrics {REASON}"]
CLEAN = ["STEP #15 sync PR #16", "WAIT #15 lockable PR #17 is not the ticket's next step"]
TITLE = "[MONITOR] chasing-metrics beta ep09"
BODY = ("Monitor: chasing metrics\nPlatform: beta\nEpisodes: ep07, ep08, ep09\nEnd retention: 0.3, 0.4, 0.5\n"
        "Distinctiveness: 1.00, 0.67, 0.00\n"
        "Action: hold the next pitch lock until keeper rules (system brief section 11).\n"
        "To clear: post a RULING on an open pitch ticket. The round ends when a pitch pull merges after this "
        "issue.\n")


def table(ends):
    """The calibration set: per post a beat ``b1`` at 0.9, then a beat ``b2`` at its end value."""
    rows = ["\t".join(calibration.HEADER)]
    for post, end in sorted(ends.items()):
        ep = post.split("/")[0]
        for beat, value in (("b1", "0.9"), ("b2", end)):
            rows.append("\t".join((post, f"id-{ep}", beat, "0", "10", value, "a" * 64)))
    return "\n".join(rows) + "\n"


class ChasingMetrics(Case):
    def record(self, w, kind, number, data):
        path = w.github / kind / f"{number}.json"
        path.write_text(json.dumps({"number": number, **data}, indent=1, sort_keys=True) + "\n")

    def lock(self, w, issue, pull, merged_at, unit, stage="pitch"):
        """A closed issue and its merged pull, as a lock long ago left them."""
        self.record(w, "issues", issue, {
            "body": f"{stage} for {unit}.\n", "created_at": "2026-11-01T09:00:00Z", "state": "closed",
            "labels": ["room:story", f"stage:{stage}", "tracked", f"unit:{unit}"],
            "title": f"[LK-{issue}] {stage} {unit}"})
        self.record(w, "pulls", pull, {
            "base_ref": "trunk", "body": f"Ticket: #{issue}\n", "created_at": "2026-11-02T09:00:00Z",
            "head_ref": f"{stage}/{unit}", "labels": [], "merged_at": merged_at, "state": "merged", "ticket": issue,
            "title": f"Work for #{issue}"})

    def chase_world(self, ends=ENDS, pitches=None, text=None, selected=True):
        """``text`` (or the table of ``ends``; None: no table) and the pitches committed on the base."""
        w = self.world((15, 16, 17))
        for ep, premise in sorted({**PITCHES, **(pitches or {})}.items()):
            w.write(f"episodes/{ep}/pitch.md", premise)
        if text is None and ends is not None:
            text = table(ends)
        if text is not None:
            w.write(calibration.PATH, text)
        w.commit("pitches and the calibration set")
        for ep, (issue, pull, merged_at) in sorted(LOCKS.items()):
            self.lock(w, issue, pull, merged_at, ep)
        if selected:
            w.add(15, "2026-11-10T10:00:00Z", "keeper", "[KEEPER] SELECTED #16\n\nThe pier holds.\n")
        return w

    def created(self, w):
        return [(e["title"], e["body"], e["labels"]) for e in w.ops("create_issue")]

    def applied(self):
        """The world after a first ``--apply``: issue #37 filed at 2026-11-11T12:00:00Z."""
        w = self.chase_world()
        self.assertEqual(lines(self.ticked(w, "--apply")), HOLD)
        issue = w.record("issues", 37)
        self.assertEqual((issue["title"], issue["created_at"]), (TITLE, "2026-11-11T12:00:00Z"))
        return w

    # Behaviour 1 (seeded defect: no `holds` for the file monitor prints the NEED #15 line twice)
    def test_a_trip_holds_the_lock_and_files_once(self):
        w = self.chase_world()
        self.dry_then_apply(w, HOLD)
        self.assertEqual(self.created(w), [(TITLE, BODY, [LABEL])])
        self.assertEqual(lines(self.ticked(w, "--apply")), HOLD)
        self.assertEqual(len(w.ops("create_issue")), 1)
        self.assertEqual({w.record("pulls", n)["state"] for n in (16, 17)}, {"open"})

    # Behaviour 2
    def test_distinctiveness_that_does_not_fall(self):
        w = self.chase_world(pitches={"ep09": NEW})
        self.assertEqual(lines(self.ticked(w)), CLEAN)

    # Behaviour 3 (seeded defect: `-` read as 0 trips the ep07 twin on (-, 0.4, 0.5))
    def test_engagement_that_does_not_rise_or_is_left_out(self):
        for name, ends in (("ep09 0.35", {**ENDS, "ep09/beta": "0.35"}), ("ep08 -", {**ENDS, "ep08/beta": "-"}),
                           ("ep07 -", {**ENDS, "ep07/beta": "-"}), ("no table", None)):
            with self.subTest(name):
                w = self.chase_world(ends=ends)
                out = self.ticked(w)
                self.assertNotIn("chasing-metrics", out)
                self.assertEqual(lines(out), CLEAN)

    # Behaviour 4
    def test_platforms_are_not_merged(self):
        ends = {"ep07/beta": "0.3", "ep08/beta": "0.4", "ep09/alpha": "0.5"}
        self.assertEqual(lines(self.ticked(self.chase_world(ends=ends))), CLEAN)
        ends = {**ENDS, "ep07/alpha": "0.3", "ep08/alpha": "0.4", "ep09/alpha": "0.5"}
        self.assertEqual(lines(self.ticked(self.chase_world(ends=ends))), [
            f"NEED - monitor chasing-metrics: alpha {REST}", f"NEED - monitor chasing-metrics: beta {REST}",
            f"NEED #15 chasing-metrics alpha {REST}"])

    # Behaviour 5 (seeded defect: a reader of each post's first row, 0.9, prints CLEAN in Behaviour 1)
    def test_the_last_three_by_lock_time(self):
        w = self.chase_world(ends={**ENDS, "ep06/beta": "0.6"}, pitches={"ep06": NEW})
        self.lock(w, 29, 30, "2026-11-03T09:00:00Z", "ep06")
        reason = ("beta ep07, ep08, ep09: end retention rises (0.3, 0.4, 0.5) "
                  "while distinctiveness falls (0.88, 0.67, 0.00)")
        self.assertEqual(lines(self.ticked(w)), [f"NEED - monitor chasing-metrics: {reason}",
                                                 f"NEED #15 chasing-metrics {reason}"])

    def test_order_is_by_lock_time(self):
        w = self.chase_world()
        self.assertEqual(lines(self.ticked(w)), HOLD)
        w.set_record("pulls", 32, merged_at=LOCKS["ep09"][2])
        w.set_record("pulls", 36, merged_at=LOCKS["ep07"][2])
        self.assertEqual(lines(self.ticked(w)), CLEAN)

    # Behaviour 6 (seeded defect: a ruling older than the issue clears it)
    def test_a_ruling_after_the_issue_clears_it(self):
        for number, at in ((15, "2026-11-11T13:00:00Z"), (15, "2026-11-11T12:00:00Z"), (16, "2026-11-11T13:00:00Z")):
            with self.subTest(number=number, at=at):
                w = self.applied()
                w.add(number, at, "keeper", "[KEEPER] RULING\nThe next pitch is chosen on its merits.\n")
                self.assertEqual(lines(self.ticked(w)), CLEAN)

    def test_an_older_or_another_actors_ruling_or_no_issue_does_not(self):
        for role, at, apply in (("keeper", "2026-11-11T11:00:00Z", True), ("writer", "2026-11-11T13:00:00Z", True),
                                ("keeper", "2026-11-11T13:00:00Z", False)):
            with self.subTest(role=role, at=at, apply=apply):
                w = self.applied() if apply else self.chase_world()
                w.add(15, at, role, f"[{role.upper()}] RULING\nThe next pitch is chosen on its merits.\n")
                self.assertEqual(lines(self.ticked(w)), HOLD)

    # Behaviour 7
    def test_a_pitch_merged_after_the_issue_ends_the_round(self):
        for merged_at, expected in (("2026-11-11T12:30:00Z", CLEAN), ("2026-11-11T11:30:00Z", HOLD)):
            with self.subTest(merged_at=merged_at):
                w = self.applied()
                self.lock(w, 41, 42, merged_at, "ep10")
                self.assertEqual(lines(self.ticked(w)), expected)

    # Behaviour 8
    def test_only_the_lock_is_held(self):
        w = self.chase_world(selected=False)
        self.assertEqual(lines(self.ticked(w)), [*HOLD, "STEP #15 launch keeper on #15"])

    # Behaviour 9
    def test_a_table_that_does_not_parse(self):
        fail = "FAIL - monitor chasing-metrics: calibration/retention.tsv"
        good = table(ENDS)
        half = good.splitlines()
        half[6] = half[6].replace("\t0.5\t", "\thalf\t")
        for text, line in (
                ("post\tbeat\n" + "".join(r + "\n" for r in good.splitlines()[1:]),
                 f"{fail}: header is not post, post_id, beat, start, end, retention, snapshot_sha256"),
                ("\n".join(half) + "\n", f"{fail} line 7: retention is not a number or -"),
                (good + "ep10/beta\tx\n", f"{fail} line 8 is not a row of the header")):
            with self.subTest(line=line):
                w = self.chase_world(text=text)
                self.assertEqual(lines(self.ticked(w, code=1)), [line, *CLEAN])


if __name__ == "__main__":
    unittest.main()
