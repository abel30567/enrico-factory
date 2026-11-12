"""Tests for kit/lib/filing.py: the tickets ``factory tick`` files after a lock.

Each test builds a ``tickets.State`` by hand and a temporary git repository on branch ``main`` as the base.
The pipeline and formats are those of the shipped kit unless a test says otherwise.
"""

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
        [found] = self.due(self.with_boards())
        self.assertEqual((found["from"], found["pull"], found["room"], found["stage"], found["unit"]), REEL[0])
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


if __name__ == "__main__":
    unittest.main()
