"""Tests for kit/lib/filing.py: the tickets ``factory tick`` files after a lock.

Each test builds a ``tickets.State`` by hand and a temporary git repository on branch ``main`` as the base.
The pipeline and formats are those of the shipped kit unless a test says otherwise.
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import filing, github, gitstate, production, roles, tickets  # noqa: E402
from kit.lib import cmd_tick  # noqa: E402

KIT = ROOT / "kit"
SEQUENCE = '{"id": "Q"}\n'
SCRATCH = {"episodes/ep01/sequences/Q1/sequence.json": SEQUENCE,
           "episodes/ep01/sequences/Q1/shots/Q1-1.json": "{}\n",
           "episodes/ep01/sequences/Q2/sequence.json": SEQUENCE,
           "episodes/ep01/sequences/Q10/sequence.json": SEQUENCE,
           "episodes/ep01/sequences/notes.md": "Notes.\n",
           "episodes/ep02/sequences/Q1/sequence.json": SEQUENCE}


def premise(format_id):
    return f"---\nformat: {format_id}\nlogline: A test premise.\n---\nA test premise.\n"


def ticket(number, stage, unit, state="closed", room="story"):
    return {"number": number, "key": f"K-{number}", "summary": f"{stage} for {unit}", "room": room,
            "stage": stage, "unit": unit, "state": state, "hold": False, "created_at": "2026-11-01T09:00:00Z",
            "labels": [tickets.TRACKED, f"room:{room}", f"stage:{stage}", f"unit:{unit}"]}


def pull(number, of, state="merged"):
    return {"number": number, "ticket": of, "state": state, "labels": [], "comments": []}


def make_state(found, pulls=(), errors=()):
    return tickets.State(tickets={t["number"]: t for t in found}, pulls={p["number"]: p for p in pulls},
                         errors=list(errors))


class Case(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
        patch.start()
        self.addCleanup(patch.stop)
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = self.tmp / "show"
        self.root.mkdir()
        self.git("init", "-q", "-b", "main")
        self.commit({"README.md": "A test production.\n"})

    def git(self, *args):
        return gitstate.git(self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", *args)

    def write(self, files):
        for rel, text in files.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    def commit(self, files):
        self.write(files)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "files")

    def p(self, pipeline=None):
        return production.Production(
            root=self.root, kit=KIT, settings={"BASE": "main"}, roles={},
            formats=roles.load_formats(KIT / "formats"),
            pipeline=pipeline if pipeline is not None else roles.load_pipeline(KIT / "pipeline.json"),
            kinds={}, actors={})

    def due(self, state, pipeline=None):
        return filing.due(self.p(pipeline), state, self.root)

    def filed(self, state, pipeline=None):
        return [(f["from"], f["pull"], f["room"], f["stage"], f["unit"]) for f in self.due(state, pipeline)]


BEATS_LOCKED = ([ticket(1, "beats", "ep01")], [pull(2, 1)])
SEQUENCES_LOCKED = ([ticket(1, "sequences", "ep01")], [pull(2, 1)])
BOARDS = [(1, 2, "story", "board", "ep01/Q1"), (1, 2, "story", "board", "ep01/Q10"),
          (1, 2, "story", "board", "ep01/Q2")]


class SameUnit(Case):
    """Behaviour 1."""

    def setUp(self):
        super().setUp()
        self.commit({"episodes/ep01/premise.md": premise("episode-circle")})

    def test_a_locked_beats_files_the_outline(self):
        self.assertEqual(self.filed(make_state(*BEATS_LOCKED)), [(1, 2, "story", "outline", "ep01")])

    def test_an_open_ticket_with_an_open_pull_files_nothing(self):
        state = make_state([ticket(1, "beats", "ep01", state="open")], [pull(2, 1, state="open")])
        self.assertEqual(self.filed(state), [])

    def test_a_closed_ticket_with_no_merged_pull_files_nothing(self):
        self.assertEqual(self.filed(make_state([ticket(1, "beats", "ep01")], [pull(2, 1, state="closed")])), [])
        self.assertEqual(self.filed(make_state([ticket(1, "beats", "ep01")])), [])


class Skips(Case):
    """Behaviour 2."""

    def test_a_format_that_skips_the_outline_files_the_script(self):
        self.commit({"episodes/ep01/premise.md": premise("short-circle")})
        self.assertEqual(self.filed(make_state(*BEATS_LOCKED)), [(1, 2, "story", "script", "ep01")])

    def test_no_premise_on_the_base_files_the_outline(self):
        self.assertEqual(self.filed(make_state(*BEATS_LOCKED)), [(1, 2, "story", "outline", "ep01")])

    def test_a_premise_only_in_the_working_tree_is_not_read(self):
        self.write({"episodes/ep01/premise.md": premise("short-circle")})
        self.assertEqual(self.filed(make_state(*BEATS_LOCKED)), [(1, 2, "story", "outline", "ep01")])


class OnePerSequence(Case):
    """Behaviour 3."""

    def setUp(self):
        super().setUp()
        self.commit(SCRATCH)

    def test_one_board_per_sequence_file_on_the_base(self):
        self.assertEqual(self.filed(make_state(*SEQUENCES_LOCKED)), BOARDS)

    def test_a_sequence_only_in_the_working_tree_is_not_filed(self):
        self.write({"episodes/ep01/sequences/Q3/sequence.json": SEQUENCE})
        self.assertEqual(self.filed(make_state(*SEQUENCES_LOCKED)), BOARDS)

    def test_a_sequence_only_on_another_branch_is_not_filed(self):
        self.git("checkout", "-q", "-b", "other")
        self.commit({"episodes/ep01/sequences/Q3/sequence.json": SEQUENCE})
        self.git("checkout", "-q", "main")
        self.assertEqual(self.filed(make_state(*SEQUENCES_LOCKED)), BOARDS)
        self.git("checkout", "-q", "other")
        self.assertEqual(self.filed(make_state(*SEQUENCES_LOCKED)), BOARDS)

    def test_a_sequence_committed_on_the_base_is_filed(self):
        self.commit({"episodes/ep01/sequences/Q3/sequence.json": SEQUENCE})
        self.assertEqual(self.filed(make_state(*SEQUENCES_LOCKED)),
                         BOARDS + [(1, 2, "story", "board", "ep01/Q3")])


class NeverTwice(Case):
    """Behaviour 4."""

    def setUp(self):
        super().setUp()
        self.commit(SCRATCH)

    def with_ticket(self, other, pulls=()):
        found, locked = SEQUENCES_LOCKED
        return self.filed(make_state(found + [other], locked + list(pulls)))

    def test_an_open_ticket_for_the_unit(self):
        self.assertEqual(self.with_ticket(ticket(3, "board", "ep01/Q1", state="open")), BOARDS[1:])

    def test_a_ticket_closed_without_a_pull(self):
        self.assertEqual(self.with_ticket(ticket(3, "board", "ep01/Q1")), BOARDS[1:])

    def test_a_ticket_with_an_open_pull(self):
        self.assertEqual(self.with_ticket(ticket(3, "board", "ep01/Q1", state="open"), [pull(4, 3, "open")]),
                         BOARDS[1:])

    def test_other_rooms_stages_and_units_do_not_count(self):
        for other in (ticket(3, "board", "ep01/Q1", room="production"), ticket(3, "reel", "ep01/Q1"),
                      ticket(3, "board", "ep02/Q1")):
            with self.subTest(other=other["summary"], room=other["room"]):
                self.assertEqual(self.with_ticket(other), BOARDS)

    def test_two_locked_tickets_for_the_same_unit_file_once_from_the_lower(self):
        state = make_state([ticket(5, "script", "ep01"), ticket(7, "script", "ep01")], [pull(6, 5), pull(8, 7)])
        self.assertEqual(self.filed(state), [(5, 6, "story", "sequences", "ep01")])


class OtherUnitTypes(Case):
    """Behaviour 5."""

    def test_research_bible_and_board_file_nothing(self):
        self.commit(SCRATCH)
        state = make_state([ticket(1, "research", "T-1"), ticket(3, "bible", "A-1"),
                            ticket(5, "board", "ep01/Q1")], [pull(2, 1), pull(4, 3), pull(6, 5)])
        self.assertEqual(self.filed(state), [])

    def test_two_stages_after_one_are_filed_in_pipeline_order(self):
        entry = roles.load_pipeline(KIT / "pipeline.json")[3]
        pipeline = [{**entry, "stage": "a", "after": ""}, {**entry, "stage": "c", "after": "a"},
                    {**entry, "stage": "b", "after": "a"}]
        state = make_state([ticket(1, "a", "ep01")], [pull(2, 1)])
        self.assertEqual(self.filed(state, pipeline), [(1, 2, "story", "c", "ep01"), (1, 2, "story", "b", "ep01")])


class Shape(Case):
    """Behaviour 6."""

    def made(self, found):
        gh = github.Recorded(self.tmp / "github")
        number = gh.create_issue(found["title"], found["body"], found["labels"])
        parsed = tickets.parse(gh.issue(number))
        return parsed["room"], parsed["stage"], parsed["unit"]

    def test_same_unit(self):
        [found] = self.due(make_state(*BEATS_LOCKED))
        self.assertEqual(found["title"], "[OUTLINE-EP01] outline for ep01")
        self.assertEqual(found["labels"], ["tracked", "room:story", "stage:outline", "unit:ep01"])
        self.assertEqual(found["body"], "Do the stage outline for unit ep01.\n\n"
                                        "Upstream: #1 beats ep01, locked by PR #2.\n"
                                        "Filed by factory tick.\n")
        self.assertEqual(self.made(found), ("story", "outline", "ep01"))

    def test_one_per_sequence(self):
        self.commit(SCRATCH)
        found = self.due(make_state(*SEQUENCES_LOCKED))
        self.assertEqual([f["title"] for f in found], ["[BOARD-EP01-Q1] board for ep01/Q1",
                                                       "[BOARD-EP01-Q10] board for ep01/Q10",
                                                       "[BOARD-EP01-Q2] board for ep01/Q2"])
        self.assertEqual(found[0]["labels"], ["tracked", "room:story", "stage:board", "unit:ep01/Q1"])
        self.assertEqual(found[0]["body"], "Do the stage board for unit ep01/Q1.\n\n"
                                           "Upstream: #1 sequences ep01, locked by PR #2.\n"
                                           "Filed by factory tick.\n")
        self.assertEqual([self.made(f) for f in found],
                         [("story", "board", "ep01/Q1"), ("story", "board", "ep01/Q10"),
                          ("story", "board", "ep01/Q2")])

    def test_key(self):
        self.assertEqual(filing.key("beats", "ep01"), "BEATS-EP01")
        self.assertEqual(filing.key("board", "ep01/Q1"), "BOARD-EP01-Q1")
        self.assertEqual(filing.key("style", "style lock"), "STYLE-STYLE-LOCK")
        self.assertEqual(filing.key("board", "ep01//Q.1"), "BOARD-EP01-Q-1")


BOARDS_LOCKED = ([ticket(11, "board", "ep01/Q1"), ticket(13, "board", "ep01/Q10"), ticket(15, "board", "ep01/Q2")],
                 [pull(12, 11), pull(14, 13), pull(16, 15)])
REEL = [(11, 12, "story", "reel", "ep01")]


class EpisodeAfterSequence(Case):
    """ISSUE-251 behaviours 7 to 9: an episode stage is filed once every sequence of the episode is locked."""

    def setUp(self):
        super().setUp()
        self.commit(SCRATCH)

    def with_boards(self, extra=(), pulls=(), errors=()):
        found, locked = BOARDS_LOCKED
        return make_state(found + list(extra), locked + list(pulls), errors)

    def test_filed_once_every_board_is_locked(self):
        self.assertEqual(self.filed(self.with_boards()), REEL)
        [found] = self.due(self.with_boards())
        self.assertEqual(found["title"], "[REEL-EP01] reel for ep01")
        self.assertEqual(found["labels"], ["tracked", "room:story", "stage:reel", "unit:ep01"])
        self.assertEqual(found["body"], "Do the stage reel for unit ep01.\n\n"
                                        "Upstream: #11 board ep01/Q1, locked by PR #12.\n"
                                        "Upstream: #13 board ep01/Q10, locked by PR #14.\n"
                                        "Upstream: #15 board ep01/Q2, locked by PR #16.\n"
                                        "Filed by factory tick.\n")
        gh = github.Recorded(self.tmp / "github")
        parsed = tickets.parse(gh.issue(gh.create_issue(found["title"], found["body"], found["labels"])))
        self.assertEqual((parsed["room"], parsed["stage"], parsed["unit"]), ("story", "reel", "ep01"))

    def test_a_board_not_locked_files_nothing(self):
        found, locked = BOARDS_LOCKED
        cases = {"pull 16 absent": make_state(found, locked[:2]),
                 "pull 16 closed": make_state(found, locked[:2] + [pull(16, 15, state="closed")]),
                 "ticket 15 absent": make_state(found[:2], locked),
                 "an unreadable ticket": self.with_boards(errors=["ticket #9: title is not [KEY] summary"])}
        for name, state in cases.items():
            with self.subTest(name):
                self.assertEqual(self.filed(state), [])

    def test_never_twice(self):
        for other in (ticket(21, "reel", "ep01", state="open"), ticket(21, "reel", "ep01")):
            with self.subTest(state=other["state"]):
                self.assertEqual(self.filed(self.with_boards([other])), [])
        self.assertEqual(self.filed(self.with_boards([ticket(21, "reel", "ep02", state="open")])), REEL)

    def test_one_source(self):
        [found] = self.due(self.with_boards([ticket(31, "board", "ep01/Q1")], [pull(32, 31)]))
        self.assertEqual((found["from"], found["pull"]), (11, 12))
        self.assertIn("Upstream: #11 board ep01/Q1, locked by PR #12.\n", found["body"])
        self.assertNotIn("#31", found["body"])

    def test_a_board_off_the_base(self):
        [found] = self.due(self.with_boards([ticket(5, "board", "ep01/Q9")], [pull(6, 5)]))
        self.assertEqual((found["from"], found["pull"], found["unit"]), (11, 12, "ep01"))
        self.assertNotIn("Q9", found["body"])
        self.assertEqual(self.filed(make_state([ticket(5, "board", "ep01/Q9")], [pull(6, 5)])), [])


class UnitTypesNotNames(Case):
    """ISSUE-251 behaviour 9: the rule reads unit types from the pipeline, and a skip is stepped over."""

    def setUp(self):
        super().setUp()
        entry = roles.load_pipeline(KIT / "pipeline.json")[3]
        self.pipeline = [{**entry, "stage": "cut", "unit": "episode", "after": ""},
                         {**entry, "stage": "draw", "unit": "sequence", "after": "cut"},
                         {**entry, "stage": "mid", "unit": "episode", "after": "draw"},
                         {**entry, "stage": "join", "unit": "episode", "after": "mid"}]
        formats = self.tmp / "formats"
        formats.mkdir()
        (formats / "skip-mid.md").write_text("---\nformat: skip-mid\nskips: mid\n---\nA test format.\n")
        self.formats = roles.load_formats(formats)
        self.commit(SCRATCH)
        self.draws = [ticket(11, "draw", "ep01/Q1"), ticket(13, "draw", "ep01/Q10"), ticket(15, "draw", "ep01/Q2")]
        self.pulls = [pull(12, 11), pull(14, 13), pull(16, 15)]

    def p(self, pipeline=None):
        return production.Production(root=self.root, kit=KIT, settings={"BASE": "main"}, roles={},
                                     formats=self.formats, pipeline=self.pipeline, kinds={}, actors={})

    def test_a_skip_files_the_stage_after_it(self):
        self.commit({"episodes/ep01/premise.md": premise("skip-mid")})
        self.assertEqual(self.filed(make_state(self.draws, self.pulls)), [(11, 12, "story", "join", "ep01")])
        self.assertEqual(self.filed(make_state(self.draws[:2], self.pulls[:2])), [])
        join = ticket(21, "join", "ep01", state="open")
        state = make_state(self.draws + [join], self.pulls)
        self.assertEqual(tickets.upstream(self.p(), state, join, self.root),
                         (True, "skipped by format skip-mid: upstream stage draw is locked for ep01"))

    def test_without_the_skip(self):
        self.assertEqual(self.filed(make_state(self.draws, self.pulls)), [(11, 12, "story", "mid", "ep01")])


class Unreadable(Case):
    """Behaviour 7."""

    def test_an_unreadable_ticket_files_nothing(self):
        found, pulls = BEATS_LOCKED
        state = make_state(found, pulls, ["ticket #9: title is not [KEY] summary"])
        self.assertEqual(self.filed(state), [])

    def test_without_it_one_filing(self):
        self.assertEqual(len(self.due(make_state(*BEATS_LOCKED))), 1)


WINDOW = {"room": "distribution", "stage": "window", "unit": "post", "after": "", "maker": "telemetry-puller",
          "lint": ["telemetry"], "checker": "", "judge": "", "blind": False, "pitches": 1,
          "paths": ["distribution/*/*/requests/window.json", "distribution/*/*/snapshot.json"]}
PACKET = '{"hook": "A hook.", "spoilers": ["A spoiler."], "cta": ["A prompt?"], "tone": "dry"}\n\n'
PACKET_PATH = "episodes/ep01/intent-packet.json"
REEL_LOCKED = ([ticket(11, "reel", "ep01")], [pull(12, 11)])
WINDOW_LOCKED = ([ticket(21, "window", "ep01/alpha", room="distribution")], [pull(22, 21)])
INTENT_TITLE = "[INTENT-EP01] story intent packet for ep01"
NOTES_TITLE = "[NOTES-EP01-ALPHA] platform notes for ep01/alpha"
INTENT_BODY = ("Story intent packet for episode ep01, locked by PR #12 (ticket #11).\n\n"
               "```json\n"
               '{"hook": "A hook.", "spoilers": ["A spoiler."], "cta": ["A prompt?"], "tone": "dry"}\n'
               "```\n\n"
               "The story room owns this packet; post and distribution read it and do not edit it.\n"
               "Filed by factory tick.\n")
NO_PACKET_BODY = ("Story intent packet for episode ep01, locked by PR #12 (ticket #11).\n\n"
                  "episodes/ep01/intent-packet.json is not on the base.\n\n"
                  "The story room owns this packet; post and distribution read it and do not edit it.\n"
                  "Filed by factory tick.\n")
NOTES_BODY = ("Platform notes for ep01/alpha, after the window locked by PR #22 (ticket #21).\n\n"
              "Format limits:\n"
              "CTA results:\n"
              "Audience theories:\n\n"
              "The distribution room writes these notes; the story room answers here and does not edit them.\n"
              "Filed by factory tick.\n")
PACKET_KINDS = ("intent-packet", "platform-notes")


def story_pipeline():
    return [entry for entry in roles.load_pipeline(KIT / "pipeline.json") if entry["room"] == "story"]


def with_window():
    """The shipped story entries and the ``window`` entry of ISSUE-169 ruling 2 (ISSUE-171 ruling 10)."""
    return story_pipeline() + [WINDOW]


def other(number, title, state="open"):
    return {"number": number, "title": title, "body": "", "state": state, "labels": [],
            "created_at": "2026-11-01T09:00:00Z"}


def issue_record(number, stage, unit, room="story"):
    return {"number": number, "title": f"[K-{number}] {stage} for {unit}", "body": "", "state": "closed",
            "labels": sorted([tickets.TRACKED, f"room:{room}", f"stage:{stage}", f"unit:{unit}"]),
            "created_at": "2026-11-01T09:00:00Z"}


def pull_record(number, of):
    return {"number": number, "title": f"Work for #{of}", "body": f"Ticket: #{of}\n", "state": "merged",
            "labels": [], "head_ref": f"feat/K-{of}", "base_ref": "main", "ticket": of,
            "created_at": "2026-11-02T09:00:00Z", "merged_at": "2026-11-03T09:00:00Z"}


def record(gh, *items):
    for item in items:
        kind = "pulls" if "ticket" in item else "issues"
        path = gh.dir / kind / f"{item['number']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(item, indent=1, sort_keys=True) + "\n")


class PacketCase(Case):
    def packets(self, state, pipeline=None):
        return [f for f in self.due(state, pipeline) if f["stage"] in PACKET_KINDS]

    def kinds(self, state, pipeline=None):
        return [(f["from"], f["pull"], f["room"], f["stage"], f["unit"]) for f in self.packets(state, pipeline)]


class IntentPacket(PacketCase):
    """ISSUE-171 behaviours 1, 2, 5 and 6: a locked story reel files the story intent packet."""

    def test_a_locked_reel_files_the_packet(self):
        self.commit({PACKET_PATH: PACKET})
        found = self.due(make_state(*REEL_LOCKED))
        self.assertEqual(found, [{"from": 11, "pull": 12, "room": "story", "stage": "intent-packet", "unit": "ep01",
                                  "title": INTENT_TITLE, "body": INTENT_BODY,
                                  "labels": ["room:post", "room:distribution"]}])
        self.assertIn(PACKET.rstrip("\n"), found[0]["body"])

    def test_a_reel_not_locked_files_no_packet(self):
        self.commit({PACKET_PATH: PACKET})
        cases = {"open with an open pull": make_state([ticket(11, "reel", "ep01", state="open")],
                                                      [pull(12, 11, state="open")]),
                 "closed with a closed pull": make_state([ticket(11, "reel", "ep01")], [pull(12, 11, state="closed")])}
        for name, state in cases.items():
            with self.subTest(name):
                self.assertEqual(self.due(state), [])

    def test_no_packet_on_the_base(self):
        self.commit({PACKET_PATH: PACKET})
        self.git("rm", "-q", PACKET_PATH)
        self.git("commit", "-q", "-m", "remove")
        [found] = self.due(make_state(*REEL_LOCKED))
        self.assertEqual(found["body"], NO_PACKET_BODY)

    def test_a_packet_only_in_the_working_tree_is_not_read(self):
        self.write({PACKET_PATH: PACKET})
        [found] = self.due(make_state(*REEL_LOCKED))
        self.assertEqual(found["body"], NO_PACKET_BODY)

    def test_a_locked_beats_gives_only_its_outline(self):
        self.assertEqual(self.filed(make_state(*BEATS_LOCKED)), [(1, 2, "story", "outline", "ep01")])

    def test_a_post_reel_files_no_packet(self):
        pipeline = with_window() + [{**WINDOW, "room": "post", "stage": "reel"}]
        self.assertEqual(self.due(make_state([ticket(11, "reel", "ep01", room="post")], [pull(12, 11)]), pipeline),
                         [])
        self.assertEqual(self.kinds(make_state(*REEL_LOCKED), pipeline), [(11, 12, "story", "intent-packet", "ep01")])

    def test_an_unreadable_ticket_files_no_packet(self):
        state = make_state(*REEL_LOCKED, errors=["ticket #9: title is not [KEY] summary"])
        self.assertEqual(self.due(state), [])
        self.assertEqual(len(self.due(make_state(*REEL_LOCKED))), 1)

    def test_tickets_first_then_packets(self):
        found, pulls = BEATS_LOCKED
        state = make_state(REEL_LOCKED[0] + found, REEL_LOCKED[1] + pulls)
        self.assertEqual(self.filed(state), [(1, 2, "story", "outline", "ep01"),
                                             (11, 12, "story", "intent-packet", "ep01")])


class PlatformNotes(PacketCase):
    """ISSUE-171 behaviour 3: a locked distribution window files the platform notes."""

    def test_a_locked_window_files_the_notes(self):
        self.assertEqual(self.due(make_state(*WINDOW_LOCKED), with_window()),
                         [{"from": 21, "pull": 22, "room": "distribution", "stage": "platform-notes",
                           "unit": "ep01/alpha", "title": NOTES_TITLE, "body": NOTES_BODY, "labels": ["room:story"]}])

    def test_a_window_not_locked_files_nothing(self):
        found, _ = WINDOW_LOCKED
        window = ticket(21, "window", "ep01/alpha", state="open", room="distribution")
        cases = {"no pull": make_state(found), "a closed pull": make_state(found, [pull(22, 21, state="closed")]),
                 "an open pull": make_state([window], [pull(22, 21, state="open")])}
        for name, state in cases.items():
            with self.subTest(name):
                self.assertEqual(self.due(state, with_window()), [])

    def recorded(self):
        gh = github.Recorded(self.tmp / "github")
        record(gh, issue_record(21, "window", "ep01/alpha", room="distribution"), pull_record(22, 21))
        return gh

    def test_without_the_window_entry_the_ticket_does_not_load(self):
        p = self.p(story_pipeline())
        state = tickets.load(p, self.recorded(), self.root)
        self.assertEqual(state.errors, ["ticket #21: stage distribution/window is not in the pipeline"])
        self.assertEqual(filing.due(p, state, self.root), [])

    def test_with_the_window_entry_the_ticket_loads(self):
        p = self.p(with_window())
        state = tickets.load(p, self.recorded(), self.root)
        self.assertEqual(state.errors, [])
        self.assertEqual([f["title"] for f in filing.due(p, state, self.root)], [NOTES_TITLE])


class PacketsOnce(PacketCase):
    """ISSUE-171 behaviour 4: a packet issue whose title is in ``State.others`` is not filed again."""

    def test_an_intent_packet_already_filed(self):
        for state in ("open", "closed"):
            with self.subTest(state=state):
                found = make_state(*REEL_LOCKED)
                found.others = [other(40, INTENT_TITLE, state)]
                self.assertEqual(self.due(found), [])
                found.others = [other(40, "[INTENT-EP02] story intent packet for ep02", state)]
                self.assertEqual(self.kinds(found), [(11, 12, "story", "intent-packet", "ep01")])

    def test_two_locked_reels_of_one_episode_file_one_packet(self):
        state = make_state([ticket(11, "reel", "ep01"), ticket(31, "reel", "ep01")], [pull(12, 11), pull(32, 31)])
        self.assertEqual(self.kinds(state), [(11, 12, "story", "intent-packet", "ep01")])
        state = make_state([ticket(11, "reel", "ep01"), ticket(31, "reel", "ep02")], [pull(12, 11), pull(32, 31)])
        self.assertEqual(self.kinds(state), [(11, 12, "story", "intent-packet", "ep01"),
                                             (31, 32, "story", "intent-packet", "ep02")])

    def test_platform_notes_already_filed(self):
        for state in ("open", "closed"):
            with self.subTest(state=state):
                found = make_state(*WINDOW_LOCKED)
                found.others = [other(40, NOTES_TITLE, state)]
                self.assertEqual(self.due(found, with_window()), [])
                found.others = [other(40, "[NOTES-EP02-ALPHA] platform notes for ep02/alpha", state)]
                self.assertEqual(self.kinds(found, with_window()),
                                 [(21, 22, "distribution", "platform-notes", "ep01/alpha")])

    def test_two_locked_windows_of_one_unit_file_one_notes(self):
        windows = [ticket(21, "window", "ep01/alpha", room="distribution"),
                   ticket(41, "window", "ep01/alpha", room="distribution")]
        state = make_state(windows, [pull(22, 21), pull(42, 41)])
        self.assertEqual(self.kinds(state, with_window()), [(21, 22, "distribution", "platform-notes", "ep01/alpha")])


class PacketsThroughTheTick(PacketCase):
    """ISSUE-171 behaviour 7: ``factory tick`` prints and files both packets, once."""

    def setUp(self):
        super().setUp()
        self.commit({PACKET_PATH: PACKET})
        self.gh = github.Recorded(self.tmp / "github")
        record(self.gh, issue_record(11, "reel", "ep01"), pull_record(12, 11),
               issue_record(21, "window", "ep01/alpha", room="distribution"), pull_record(22, 21))

    def tick(self, rooms, apply=True):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cmd_tick.Tick(self.p(with_window()), self.gh, apply, production.now()).file(rooms)
        return out.getvalue()

    def created(self):
        return [i for i in self.gh.issues("all") if i["number"] not in (11, 21)]

    def test_files_both_once(self):
        self.assertEqual(self.tick(["story", "distribution"]),
                         "STEP #11 file intent-packet ep01\nSTEP #21 file platform-notes ep01/alpha\n")
        made = self.created()
        self.assertEqual([(i["title"], i["labels"]) for i in made],
                         [(INTENT_TITLE, ["room:distribution", "room:post"]), (NOTES_TITLE, ["room:story"])])
        self.assertEqual([i["body"] for i in made], [INTENT_BODY, NOTES_BODY])
        self.assertEqual([tickets.parse(i) for i in made], [None, None])
        self.assertEqual(self.tick(["story", "distribution"]), "")
        self.assertEqual(len(self.created()), 2)

    def test_without_apply_prints_and_files_nothing(self):
        self.assertEqual(self.tick(["story", "distribution"], apply=False),
                         "STEP #11 file intent-packet ep01\nSTEP #21 file platform-notes ep01/alpha\n")
        self.assertEqual(self.created(), [])

    def test_another_room_files_neither(self):
        self.assertEqual(self.tick(["post"]), "")
        self.assertEqual(self.created(), [])


if __name__ == "__main__":
    unittest.main()
