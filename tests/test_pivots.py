"""Tests for rule 14 of kit/lib/lockrules.py, ``pivot`` (system brief section 7, "Pivots"; ISSUE-199).

The world is test_lockrules.World: the fixture production under tests/fixtures/lock/ in a temporary git
repository, and a Recorded GitHub. A test labels a ticket of that world a pivot, and writes into its
temporary copy the locked pivots of the season and the comments of a lost vote or blind panel.
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lockrules import CLEAN, OneRule, World  # noqa: E402

from kit.lib import lockrules  # noqa: E402

PIVOT = 14


def label(w, number, *labels):
    for name in labels:
        w.add_label("issues", number, name)


def pivot(w, number, kind):
    label(w, number, "pivot", f"pivot:{kind}")


def locked_pivot(w, number, kind):
    """A closed pivot ticket ``number`` of class ``kind`` with a merged pull ``number + 1``."""
    issue = {"body": "A pivot.\n", "created_at": "2026-11-01T09:00:00Z", "number": number, "state": "closed",
             "labels": sorted(["tracked", "room:story", "stage:premise", f"unit:ep{number}", "pivot",
                               f"pivot:{kind}"]),
             "title": f"[PV-{number}] Pivot {number}"}
    pull = {"base_ref": "trunk", "body": f"Ticket: #{number}\n", "created_at": "2026-11-01T10:00:00Z",
            "head_ref": f"feat/PV-{number}-pivot", "labels": [], "merged_at": "2026-11-01T12:00:00Z",
            "number": number + 1, "state": "merged", "ticket": number, "title": f"Work for #{number}"}
    for kind_dir, record in (("issues", issue), ("pulls", pull)):
        (w.dir / kind_dir / f"{record['number']}.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")


def at_time(now):
    return mock.patch.dict(os.environ, {"FACTORY_NOW": now})


class PivotRule(OneRule):
    def assertLockable(self, w, number):
        """Every rule holds for pull ``number``, rule 14 among them, and the report says LOCKABLE."""
        rules = w.rules(number)
        self.assertEqual([r for r in rules if not r.ok], [])
        self.assertEqual(rules[-1], lockrules.Rule(PIVOT, "pivot", True, ""))
        self.assertTrue(lockrules.lockable(rules))
        code, out, err = w.lock()
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(any(line.startswith(f"PR #{number} ") and line.endswith(": LOCKABLE")
                            for line in out.splitlines()), out)


class ConstantsTest(unittest.TestCase):
    def test_rule_fourteen_is_pivot(self):
        self.assertEqual(lockrules.NAMES[-1], "pivot")
        self.assertEqual(len(lockrules.NAMES), PIVOT)
        self.assertEqual(lockrules.NAMES[12], "keep")
        self.assertEqual((lockrules.PIVOT, lockrules.PIVOT_CLASSES, lockrules.PIVOT_LIMITS,
                          lockrules.PIVOT_VOTED, lockrules.COOLDOWN_DAYS),
                         ("pivot", ("P0", "P1", "P2", "P3"), {"P2": 3, "P3": 1}, ("P2", "P3"), 14))


class NotAPivotTest(PivotRule):
    """Behaviour 1."""

    def test_1_clean_pulls_hold_rule_fourteen(self):
        w = World(self)
        for number in CLEAN:
            with self.subTest(pull=number):
                rules = w.rules(number)
                self.assertEqual(rules[-1], lockrules.Rule(PIVOT, "pivot", True, ""))
                self.assertTrue(lockrules.lockable(rules))

    def test_1_a_p0_pivot_is_lockable(self):
        w = World(self, (8,))
        pivot(w, 7, "P0")
        p, state = w.load()
        self.assertEqual(lockrules.pivot_class(state.tickets[7]), "P0")
        self.assertLockable(w, 8)


class LabelsTest(PivotRule):
    """Behaviour 2: the four label problems of ruling 3."""

    def check(self, labels, reason):
        w = World(self, (8,))
        label(w, 7, *labels)
        rules = self.assertOnly(w, 8, {PIVOT})
        self.assertEqual(rules[-1].reason, reason)
        p, state = w.load()
        self.assertIsNone(lockrules.pivot_class(state.tickets[7]))

    def test_2_pivot_alone(self):
        self.check(["pivot"], "ticket #7 has the label pivot and no class label pivot:P0 to pivot:P3")

    def test_2_class_alone(self):
        self.check(["pivot:P1"], "ticket #7 has the label pivot:P1 and not the label pivot")

    def test_2_two_classes(self):
        self.check(["pivot", "pivot:P0", "pivot:P1"], "ticket #7 has 2 class labels: pivot:P0, pivot:P1")

    def test_2_no_such_class(self):
        self.check(["pivot", "pivot:P4"], "ticket #7: pivot:P4 is not a class pivot:P0 to pivot:P3")


class BlindWinTest(PivotRule):
    """Behaviour 3."""

    def test_3_p1_on_a_stage_that_is_not_blind(self):
        w = World(self, (8,))
        pivot(w, 7, "P1")
        rules = self.assertOnly(w, 8, {PIVOT})
        self.assertEqual(rules[-1].reason, "pivot P1 needs a blind win: stage script is not blind")

    def test_3_p1_with_no_locked_version(self):
        w = World(self, (19,), blind_key=False)
        w.set_record("pulls", 4, state="closed", merged_at=None)
        pivot(w, 18, "P1")
        rules = self.assertOnly(w, 19, {PIVOT})
        self.assertEqual(rules[-1].reason, "pivot P1 needs a blind win: story/board ep01 has no locked version")

    def test_3_twin_p1_with_a_blind_win_is_lockable(self):
        w = World(self, (19,))
        pivot(w, 18, "P1")
        self.assertLockable(w, 19)


class LostPanelTest(PivotRule):
    """Behaviour 4."""

    def world(self):
        w = World(self, (19,))
        pivot(w, 18, "P1")
        w.replace(19, 1904, "[CRITIC] PREFERS A", "[CRITIC] PREFERS B")
        return w

    def test_4_a_lost_panel_starts_a_cooldown(self):
        w = self.world()
        rules = self.assertOnly(w, 19, {3, PIVOT})
        self.assertEqual(rules[-1].reason,
                         "pivot P1 lost on PR #19 at 2026-11-06T10:50:00Z; cooldown until 2026-11-20T10:50:00Z")
        p, state = w.load()
        self.assertEqual(lockrules.pivot_losses(p, state, state.tickets[18]), [("2026-11-06T10:50:00Z", 19)])

    def test_4_the_cooldown_ends(self):
        w = self.world()
        with at_time("2026-11-20T10:50:00Z"):
            self.assertOnly(w, 19, {3})

    def test_4_a_head_with_no_key_is_skipped(self):
        w = World(self, (19,), blind_key=False)
        pivot(w, 18, "P1")
        w.replace(19, 1904, "[CRITIC] PREFERS A", "[CRITIC] PREFERS B")
        p, state = w.load()
        self.assertEqual(lockrules.pivot_losses(p, state, state.tickets[18]), [])


class VoteTest(PivotRule):
    """Behaviour 5."""

    def test_5_a_p2_with_the_vote_is_lockable(self):
        w = World(self, (10,))
        pivot(w, 9, "P2")
        self.assertLockable(w, 10)

    def test_5_a_p2_with_no_vote(self):
        w = World(self, (10,))
        pivot(w, 9, "P2")
        w.edit(10, 1007, body="[BRAINTRUST] NO PREFERENCE\nNo vote.\nreviewed head {head:10} manifest none\n")
        rules = self.assertOnly(w, 10, {6, PIVOT})
        self.assertEqual(rules[-1].reason, "pivot P2 needs a [BRAINTRUST] VOTE with 5 or more for, "
                                           "later than the maker's last READY or ADDRESSED")


class SeasonTest(PivotRule):
    """Behaviour 6."""

    def test_6_other_classes_do_not_count(self):
        w = World(self, (10,))
        pivot(w, 9, "P2")
        locked_pivot(w, 30, "P2")
        locked_pivot(w, 32, "P2")
        locked_pivot(w, 34, "P3")
        self.assertLockable(w, 10)

    def test_6_a_fourth_p2_is_refused(self):
        w = World(self, (10,))
        pivot(w, 9, "P2")
        for number in (30, 32, 34):
            locked_pivot(w, number, "P2")
        rules = self.assertOnly(w, 10, {PIVOT})
        self.assertEqual(rules[-1].reason, "pivot P2: 3 a season, already locked: #30, #32, #34")

    def test_6_a_second_p3_is_refused(self):
        w = World(self, (10,))
        pivot(w, 9, "P3")
        locked_pivot(w, 30, "P3")
        rules = self.assertOnly(w, 10, {PIVOT})
        self.assertEqual(rules[-1].reason, "pivot P3: 1 a season, already locked: #30")


def lost_vote_world(test):
    w = World(test, (10,))
    pivot(w, 9, "P2")
    w.add(10, 1000, "2026-11-06T10:05:00Z", "braintrust",
          "[BRAINTRUST] VOTE 4-3\nFour seats for, three against.\nreviewed head {head:10} manifest none\n")
    return w


LOST = "pivot P2 lost on PR #10 at 2026-11-06T10:05:00Z; cooldown until 2026-11-20T10:05:00Z"


class CooldownTest(PivotRule):
    """Behaviour 7."""

    def test_7_a_lost_vote_starts_a_cooldown(self):
        w = lost_vote_world(self)
        rules = self.assertOnly(w, 10, {PIVOT})
        self.assertEqual(rules[-1].reason, LOST)

    def test_7_still_refused_a_second_before(self):
        w = lost_vote_world(self)
        with at_time("2026-11-20T10:04:59Z"):
            rules = self.assertOnly(w, 10, {PIVOT})
        self.assertEqual(rules[-1].reason, LOST)

    def test_7_lockable_when_the_cooldown_ends(self):
        w = lost_vote_world(self)
        with at_time("2026-11-20T10:05:00Z"):
            self.assertLockable(w, 10)


class ClosedPullTest(PivotRule):
    """Behaviour 8."""

    def test_8_a_loss_on_a_closed_pull(self):
        w = World(self, (10,))
        pivot(w, 9, "P2")
        pull = {**w.record("pulls", 10), "number": 36, "state": "closed", "merged_at": None,
                "head_ref": "feat/BI-1-charter-first", "created_at": "2026-11-04T10:00:00Z"}
        (w.dir / "pulls" / "36.json").write_text(json.dumps(pull, indent=1, sort_keys=True) + "\n")
        w.add(36, 3601, "2026-11-04T11:00:00Z", "braintrust",
              "[BRAINTRUST] VOTE 3-4\nThree seats for, four against.\n")
        rules = self.assertOnly(w, 10, {PIVOT})
        self.assertEqual(rules[-1].reason,
                         "pivot P2 lost on PR #36 at 2026-11-04T11:00:00Z; cooldown until 2026-11-18T11:00:00Z")


class JsonTest(unittest.TestCase):
    """Behaviour 9."""

    def test_9_json_is_deterministic(self):
        w = lost_vote_world(self)
        code, first, err = w.lock("--json")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(w.lock("--json"), (0, first, ""))
        found = json.loads(first)
        rule = next(o for o in found if o["pull"] == 10)["rules"][-1]
        self.assertEqual(rule, {"name": "pivot", "number": PIVOT, "ok": False, "reason": LOST})


if __name__ == "__main__":
    unittest.main()
