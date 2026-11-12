"""Tests for kit/lib/steps.py: the next-step table, one row at a time.

`tests/fixtures/tickets/steps/chain.json` takes ticket #3 (stage `beats`: lint, a checker, a judge) from
waiting to lockable, then held and frozen, one recorded change at a time; `pitches/chain.json` does the
same for ticket #1 (stage `premise`: two pitches, no lint). Each step is the previous one plus one
comment, label or pull record.
"""

import dataclasses
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from kit.lib import steps, tickets  # noqa: E402
from test_tickets import FIX, World, pull_record, ticket_issue  # noqa: E402

NOW = "2026-11-11T11:00:00Z"
LATE = "2026-11-11T15:00:00Z"

# step name -> (state, step, role, pull) for ticket #3
BEATS = {
    "base": ("waiting", "none", None, None),
    "merge-upstream": ("ready", "launch", "writer", None),
    "launch-maker": ("running", "none", "writer", None),
    "open-pull": ("lint", "lint", None, 5),
    "ready": ("lint", "lint", None, 5),
    "lint-error": ("lint-error", "wake", "chief", 5),
    "lint-fail": ("notes", "launch", "writer", 5),
    "relaunch-maker": ("running", "none", "writer", 5),
    "maker-blocked": ("blocked", "wake", "chief", 5),
    "relaunch-again": ("running", "none", "writer", 5),
    "addressed": ("lint", "lint", None, 5),
    "lint-pass": ("review", "launch", "editor", 5),
    "launch-checker": ("running", "none", "editor", 5),
    "notes-1": ("notes", "launch", "writer", 5),
    "notes-2": ("notes", "launch", "writer", 5),
    "notes-3": ("ruling", "launch", "chief", 5),
    "ruling": ("notes", "launch", "writer", 5),
    "approved": ("judging", "launch", "critic", 5),
    "launch-judge": ("running", "none", "critic", 5),
    "judged": ("lockable", "lock", None, 5),
    "hold": ("held", "none", None, None),
    "freeze": ("frozen", "none", None, None),
}

# step name -> (state, step, role, pull) for ticket #1
PITCHES = {
    "base": ("ready", "launch", "writer", None),
    "launch-1": ("ready", "launch", "writer", None),
    "launch-2": ("running", "none", "writer", None),
    "open-4": ("review", "launch", "editor", 4),
    "ready-4": ("review", "launch", "editor", 4),
    "approved-4": ("running", "none", "writer", None),
    "open-5": ("review", "launch", "editor", 5),
    "ready-5": ("review", "launch", "editor", 5),
    "approved-5": ("selection", "launch", "chief", None),
    "selected": ("lockable", "lock", None, 5),
}


def short(entry):
    return (entry["state"], entry["step"], entry["role"], entry["pull"])


class ChainCase(unittest.TestCase):
    FIXTURE = None
    TICKET = None

    def setUp(self):
        self.w = World(self, self.FIXTURE + "/base")
        self.chain = json.loads((FIX / self.FIXTURE / "chain.json").read_text())
        self.applied = 0

    def until(self, name):
        """Apply the rest of the chain up to and including the step `name`."""
        while self.applied < len(self.chain):
            change = self.chain[self.applied]
            self.w.apply(change)
            self.applied += 1
            if change["name"] == name:
                return
        raise AssertionError(name)

    def next(self, now=NOW, **kw):
        state = self.w.load()
        return steps.next_step(self.w.p, state, state.tickets[self.TICKET], self.w.root, now, **kw)

    def all(self, now=NOW, **kw):
        state = self.w.load()
        return steps.all_steps(self.w.p, state, state.tickets[self.TICKET], self.w.root, now, **kw)


class BeatsChainTest(ChainCase):
    FIXTURE, TICKET = "steps", 3

    def test_chain(self):
        self.assertEqual([c["name"] for c in self.chain], list(BEATS)[1:])
        self.assertEqual(short(self.next()), BEATS["base"])
        for change in self.chain:
            self.w.apply(change)
            with self.subTest(step=change["name"]):
                self.assertEqual(short(self.next()), BEATS[change["name"]])

    def test_entry_shape(self):
        self.until("lint-pass")
        self.assertEqual(self.next(), {"state": "review", "step": "launch", "role": "editor", "pull": 5,
                                       "reason": "editor has no counting verdict on #5"})
        self.assertEqual(self.all(), [self.next()])

    def test_waiting_reason(self):
        self.assertEqual(self.next()["reason"], "upstream stage premise is not locked for ep01")

    def test_locked_by_merge(self):
        self.until("judged")
        self.w.set_pull(5, state="merged", merged_at="2026-11-11T10:30:00Z")
        self.assertEqual(short(self.next()), ("locked", "none", None, 5))

    def test_locked_by_close(self):
        self.until("judged")
        self.w.set_issue(3, state="closed")
        self.assertEqual(short(self.next()), ("locked", "none", None, None))

    def test_timeout_of_a_maker_with_no_pull(self):
        self.until("launch-maker")
        self.assertEqual(short(self.next(LATE)), ("running", "timeout", "writer", None))
        # launched at 10:01: 2 * 120 + 10 = 250 minutes is not dead, one second more is
        self.assertEqual(short(self.next("2026-11-11T14:11:00Z")), ("running", "none", "writer", None))
        self.assertEqual(short(self.next("2026-11-11T14:11:01Z")), ("running", "timeout", "writer", None))

    def test_dead_line_is_attempts_times_the_setting_plus_the_margin(self):
        self.until("launch-maker")
        # launched at 10:01: alive past RUN_TIMEOUT_MIN (12:01:01) and at 250 minutes, dead one second later
        self.assertEqual(short(self.next("2026-11-11T12:01:01Z")), ("running", "none", "writer", None))
        self.assertEqual(short(self.next("2026-11-11T14:11:00Z")), ("running", "none", "writer", None))
        self.assertEqual(short(self.next("2026-11-11T14:11:01Z")), ("running", "timeout", "writer", None))

    def test_timeout_on_a_pull(self):
        self.until("relaunch-maker")
        self.assertEqual(short(self.next(LATE)), ("running", "timeout", "writer", 5))

    def test_timeout_setting(self):
        self.until("relaunch-maker")
        with open(self.w.root / "production.env", "a") as handle:
            handle.write("RUN_TIMEOUT_MIN=500\n")
        self.w.reload()
        self.assertEqual(short(self.next(LATE)), ("running", "none", "writer", 5))

    def test_maker_blocked_before_a_pull(self):
        self.until("launch-maker")
        self.w.apply({"comment": {"number": 3, "id": 900, "created_at": "2026-11-11T10:05:00Z", "role": "writer",
                                  "body": "[WRITER] BLOCKED\nMissing: the format\n"}})
        self.assertEqual(short(self.next()), ("blocked", "wake", "chief", None))

    def test_ruling_on_the_ticket(self):
        self.until("notes-3")
        self.w.apply({"comment": {"number": 3, "id": 900, "created_at": "2026-11-11T10:59:00Z", "role": "chief",
                                  "body": "[CHIEF] RULING\nThe sting stays where it is.\n"}})
        self.assertEqual(short(self.next()), ("notes", "launch", "writer", 5))

    def test_ruling_older_than_the_third_notes(self):
        self.until("notes-2")
        self.w.apply({"comment": {"number": 5, "id": 900, "created_at": "2026-11-11T10:13:30Z", "role": "chief",
                                  "body": "[CHIEF] RULING\nToo early.\n"}})
        self.until("notes-3")
        self.assertEqual(short(self.next()), ("ruling", "launch", "chief", 5))


class BlindTest(ChainCase):
    """Row 12: `blind` first when the stage is blind, the unit has a locked version and no key exists."""

    FIXTURE, TICKET = "steps", 3

    def blind(self):
        pipeline = [{**e, "blind": True} if e["stage"] == "beats" else e for e in self.w.p.pipeline]
        self.w.p = dataclasses.replace(self.w.p, pipeline=pipeline)

    def locked_version(self):
        self.w.apply({"issue": ticket_issue(6, "BT-0", "beats", "ep01", state="closed")})
        self.w.apply({"pull": pull_record(7, 6, "feat/BT-0-ep01", state="merged")})

    def no_key(self, pull):
        return False

    def test_blind_first(self):
        self.until("approved")
        self.blind()
        self.locked_version()
        self.assertEqual(short(self.next(has_key=self.no_key)), ("judging", "blind", "critic", 5))
        self.assertEqual(short(self.next()), ("judging", "launch", "critic", 5))

    def test_not_blind(self):
        self.until("approved")
        self.locked_version()
        self.assertEqual(short(self.next(has_key=self.no_key)), ("judging", "launch", "critic", 5))

    def test_no_locked_version(self):
        self.until("approved")
        self.blind()
        self.assertEqual(short(self.next(has_key=self.no_key)), ("judging", "launch", "critic", 5))


class PitchesChainTest(ChainCase):
    FIXTURE, TICKET = "pitches", 1

    def test_chain(self):
        self.assertEqual([c["name"] for c in self.chain], list(PITCHES)[1:])
        self.assertEqual(short(self.next()), PITCHES["base"])
        for change in self.chain:
            self.w.apply(change)
            with self.subTest(step=change["name"]):
                self.assertEqual(short(self.next()), PITCHES[change["name"]])

    def test_ready_reason(self):
        self.until("launch-1")
        self.assertEqual(self.next()["reason"], "1 of 2 pitch(es) in use")

    def test_all_steps_order(self):
        self.until("open-4")
        self.assertEqual([short(e) for e in self.all()], [("review", "launch", "editor", 4),
                                                          ("running", "none", "writer", None)])
        self.until("approved-4")
        self.assertEqual([short(e) for e in self.all()], [("lockable", "lock", None, 4),
                                                          ("running", "none", "writer", None)])
        self.until("approved-5")
        self.assertEqual([short(e) for e in self.all()], [("lockable", "lock", None, 4),
                                                          ("lockable", "lock", None, 5)])

    def test_all_steps_on_ticket_rows(self):
        self.assertEqual([short(e) for e in self.all()], [("ready", "launch", "writer", None)])

    def test_lowest_pull_not_lockable(self):
        self.until("ready-5")
        self.w.apply({"comment": {"number": 4, "id": 900, "created_at": "2026-11-11T10:30:00Z", "role": "writer",
                                  "body": "[WRITER] ADDRESSED\nhead {head:4}\n"}})
        self.assertEqual(short(self.next()), ("review", "launch", "editor", 4))

    def test_closed_pull_frees_its_slot(self):
        self.until("open-5")
        self.w.set_pull(5, state="closed")
        self.assertEqual(short(self.next()), ("ready", "launch", "writer", None))

    def test_first_maker_without_a_launch(self):
        pipeline = [{**e, "maker": ["writer", "scout"]} if e["stage"] == "premise" else e for e in self.w.p.pipeline]
        self.w.p = dataclasses.replace(self.w.p, pipeline=pipeline)
        self.assertEqual(short(self.next()), ("ready", "launch", "writer", None))
        self.until("launch-1")
        self.assertEqual(short(self.next()), ("ready", "launch", "scout", None))


def comment(number, cid, minute, role, body):
    return {"comment": {"number": number, "id": cid, "created_at": f"2026-11-11T10:{minute:02d}:00Z",
                        "role": role, "body": body}}


class RulingEntryTest(ChainCase):
    """Behaviours 9 and 10: tick starts the ruling once per pull request, and every round from the third
    on needs its own ruling."""
    FIXTURE, TICKET = "steps", 3

    def post(self, cid, minute, role, body, number=5):
        self.w.apply(comment(number, cid, minute, role, body))

    def test_no_launch_is_a_launch(self):
        self.until("notes-3")
        self.assertEqual(short(self.next()), ("ruling", "launch", "chief", 5))
        self.assertEqual(self.next()["reason"], "three rounds of notes on #5 and no ruling")

    def test_unanswered_launch_runs(self):
        self.until("notes-3")
        self.post(900, 20, "chief", "[CHIEF] LAUNCH chief 5-1\n")
        self.assertEqual(short(self.next()), ("running", "none", "chief", 5))
        # Seeded defect: a LAUNCH of another role in the orchestrator's name does not answer it.
        self.post(901, 21, "chief", "[CHIEF] LAUNCH writer 5-9\n")
        self.assertEqual(short(self.next(LATE)), ("running", "timeout", "chief", 5))

    def test_launch_answered_by_blocked_wakes(self):
        self.until("notes-3")
        self.post(900, 20, "chief", "[CHIEF] LAUNCH chief 5-1\n")
        self.post(901, 21, "chief", "[CHIEF] BLOCKED\nMissing: the charter\n")
        self.assertEqual(short(self.next()), ("ruling", "wake", "chief", 5))

    def test_launch_answered_by_ruling(self):
        self.until("notes-3")
        self.post(900, 20, "chief", "[CHIEF] LAUNCH chief 5-1\n")
        self.post(901, 21, "chief", "[CHIEF] RULING\nThe sting stays.\n")
        self.assertEqual(short(self.next()), ("notes", "launch", "writer", 5))

    def fourth_round(self, launched):
        self.until("notes-3")
        if launched:
            self.post(900, 20, "chief", "[CHIEF] LAUNCH chief 5-1\n")
        self.post(901, 21, "chief", "[CHIEF] RULING\nThe sting stays.\n")
        self.post(902, 22, "writer", "[WRITER] ADDRESSED\nhead {head:5}\n")
        self.post(903, 23, "program:lint", "[LINT] PASS\nstructure: pass\nreviewed head {head:5} manifest none\n")
        self.post(904, 24, "editor", "[EDITOR] NOTES\nB1 the sting is early a fourth time\n"
                                     "reviewed head {head:5} manifest none\n")

    def test_fourth_round_with_a_launch_wakes(self):
        self.fourth_round(launched=True)
        self.assertEqual(short(self.next()), ("ruling", "wake", "chief", 5))

    def test_fourth_round_after_a_ruling_by_hand_launches(self):
        self.fourth_round(launched=False)
        self.assertEqual(short(self.next()), ("ruling", "launch", "chief", 5))

    def test_ruling_newer_than_the_fourth_round(self):
        self.fourth_round(launched=True)
        self.post(905, 25, "chief", "[CHIEF] RULING\nThe sting stays, finally.\n")
        self.assertEqual(short(self.next()), ("notes", "launch", "writer", 5))


class SelectionEntryTest(ChainCase):
    """Behaviour 11: tick starts the selection once per ticket; ``all_steps`` does not change."""
    FIXTURE, TICKET = "pitches", 1
    PER_PULL = [("lockable", "lock", None, 4), ("lockable", "lock", None, 5)]

    def setUp(self):
        super().setUp()
        self.until("approved-5")

    def post(self, cid, minute, role, body, number=1):
        self.w.apply(comment(number, cid, minute, role, body))

    def check(self, expected, now=NOW):
        self.assertEqual(short(self.next(now)), expected)
        self.assertEqual([short(e) for e in self.all(now)], self.PER_PULL)

    def test_no_launch_is_a_launch(self):
        self.check(("selection", "launch", "chief", None))
        self.assertEqual(self.next()["reason"], "every pitch of #1 is lockable and none is selected")

    def test_unanswered_launch_runs_then_times_out(self):
        self.post(900, 20, "chief", "[CHIEF] LAUNCH chief 1-1\n")
        self.check(("running", "none", "chief", None))
        self.check(("running", "timeout", "chief", None), LATE)

    def test_launch_answered_by_blocked_wakes(self):
        self.post(900, 20, "chief", "[CHIEF] LAUNCH chief 1-1\n")
        self.post(901, 21, "chief", "[CHIEF] BLOCKED\nMissing: the charter\n")
        self.check(("selection", "wake", "chief", None))

    def test_launch_answered_by_selected_locks(self):
        self.post(900, 20, "chief", "[CHIEF] LAUNCH chief 1-1\n")
        self.post(901, 21, "chief", "[CHIEF] SELECTED #4\nThe first hook.\n")
        self.check(("lockable", "lock", None, 4))

    def test_launch_on_a_pull_is_not_the_ticket_s(self):
        # Seeded defect: a LAUNCH of the orchestrator on one of the ticket's pulls is a ruling's.
        self.post(900, 20, "chief", "[CHIEF] LAUNCH chief 4-1\n", number=4)
        self.post(901, 21, "chief", "[CHIEF] RULING\nNot a selection.\n", number=4)
        self.check(("selection", "launch", "chief", None))


if __name__ == "__main__":
    unittest.main()
