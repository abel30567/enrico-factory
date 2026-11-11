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
LATE = "2026-11-11T13:00:00Z"

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
    "notes-3": ("ruling", "wake", "chief", 5),
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
    "approved-5": ("selection", "wake", "chief", None),
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
        # launched at 10:01: 120 minutes is not older than 120 minutes, one second more is
        self.assertEqual(short(self.next("2026-11-11T12:01:00Z")), ("running", "none", "writer", None))
        self.assertEqual(short(self.next("2026-11-11T12:01:01Z")), ("running", "timeout", "writer", None))

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
        self.assertEqual(short(self.next()), ("ruling", "wake", "chief", 5))


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


if __name__ == "__main__":
    unittest.main()
