"""Tests for kit/lib/tickets.py.

GitHub is a recorded directory under tests/fixtures/tickets/ (copied, never changed in place). The git
repository is built in a temporary directory from the production fixture, with one branch per pull.
A comment body's ``{head:N}`` stands for the head commit of pull N's branch, filled in when it is copied.
"""

import ast
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import gitstate, github, production, tickets  # noqa: E402
from kit.lib.tickets import TicketError  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "tickets"
PRODUCTION = FIX / "production"
HEAD = re.compile(r"\{head:(\d+)\}")


class World:
    """A production repository on branch `trunk` and a recorded GitHub directory, both temporary."""

    def __init__(self, test, github_fixture=None):
        patch = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
        patch.start()
        test.addCleanup(patch.stop)
        os.environ.pop("FACTORY_NOW", None)
        tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, tmp)
        self.root = tmp / "show"
        shutil.copytree(PRODUCTION, self.root)
        self.git("init", "-q", "-b", "trunk")
        self.commit("the production")
        self.dir = tmp / "github"
        if github_fixture:
            shutil.copytree(FIX / github_fixture, self.dir)
        else:
            self.dir.mkdir()
        for path in sorted((self.dir / "pulls").glob("*.json")) if (self.dir / "pulls").is_dir() else []:
            self.branch(json.loads(path.read_text())["head_ref"])
        for path in sorted((self.dir / "comments").glob("*.json")) if (self.dir / "comments").is_dir() else []:
            path.write_text(self.heads(path.read_text()))
        self.gh = github.Recorded(self.dir)
        self.reload()

    def reload(self):
        self.p = production.load(self.root)

    def git(self, *args):
        return gitstate.git(self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", *args)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def commit_file(self, path, text):
        """Write `path` and commit it on the checked-out branch."""
        full = self.root / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(text)
        self.commit("write " + path)

    def branch(self, ref):
        """A branch `ref` with one commit on top of `trunk`, unless it exists."""
        if self.git("branch", "--list", ref).strip():
            return
        self.git("checkout", "-q", "-b", ref, "trunk")
        self.commit_file("work/" + ref.replace("/", "-") + ".md", ref + "\n")
        self.git("checkout", "-q", "trunk")

    def heads(self, text):
        def head(m):
            pull = json.loads((self.dir / "pulls" / f"{m.group(1)}.json").read_text())
            return gitstate.head(self.root, pull["head_ref"])
        return HEAD.sub(head, text)

    def _write(self, rel, data):
        path = self.dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")

    def _read(self, rel, default=None):
        path = self.dir / rel
        return json.loads(path.read_text()) if path.is_file() else default

    def apply(self, change):
        """One recorded change: a comment, a label added, or a pull or issue record written."""
        if "comment" in change:
            comment = dict(change["comment"])
            number = comment.pop("number")
            comment["body"] = self.heads(comment["body"])
            self._write(f"comments/{number}.json", self._read(f"comments/{number}.json", []) + [comment])
        elif "label" in change:
            number = change["label"]["number"]
            issue = self._read(f"issues/{number}.json")
            self._write(f"issues/{number}.json",
                        {**issue, "labels": sorted(set(issue["labels"]) | {change["label"]["add"]})})
        elif "pull" in change:
            self.branch(change["pull"]["head_ref"])
            self._write(f"pulls/{change['pull']['number']}.json", change["pull"])
        elif "issue" in change:
            self._write(f"issues/{change['issue']['number']}.json", change["issue"])
        else:
            raise ValueError(change)

    def set_pull(self, number, **fields):
        self.apply({"pull": {**self._read(f"pulls/{number}.json"), **fields}})

    def set_issue(self, number, **fields):
        self.apply({"issue": {**self._read(f"issues/{number}.json"), **fields}})

    def load(self):
        return tickets.load(self.p, self.gh, self.root)


def issue(number=7, title="[EP-7] Beats for episode seven", labels=None, state="open"):
    if labels is None:
        labels = ["room:story", "stage:beats", "tracked", "unit:ep07"]
    return {"number": number, "title": title, "body": "", "state": state, "labels": labels,
            "created_at": "2026-11-01T09:00:00Z"}


def ticket_issue(number, key, stage, unit, state="open"):
    return issue(number, f"[{key}] {stage} for {unit}",
                 ["room:story", f"stage:{stage}", "tracked", f"unit:{unit}"], state)


def pull_record(number, ticket, head_ref, state="open"):
    return {"number": number, "title": f"Work for #{ticket}", "body": f"Ticket: #{ticket}\n", "state": state,
            "labels": [], "head_ref": head_ref, "base_ref": "trunk", "ticket": ticket,
            "created_at": "2026-11-02T09:00:00Z", "merged_at": "2026-11-03T09:00:00Z" if state == "merged" else None}


class ParseTest(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(tickets.parse(issue()), {
            "number": 7, "key": "EP-7", "summary": "Beats for episode seven", "room": "story",
            "stage": "beats", "unit": "ep07", "hold": False, "state": "open",
            "created_at": "2026-11-01T09:00:00Z", "labels": ["room:story", "stage:beats", "tracked", "unit:ep07"]})

    def test_hold(self):
        labels = ["hold", "room:story", "stage:beats", "tracked", "unit:ep07"]
        self.assertTrue(tickets.parse(issue(labels=labels))["hold"])

    def test_not_tracked(self):
        self.assertIsNone(tickets.parse(issue(labels=["room:story", "stage:beats", "unit:ep07"])))

    def test_no_brackets(self):
        with self.assertRaisesRegex(TicketError, r"^ticket #7: title is not \[KEY\] summary$"):
            tickets.parse(issue(title="EP-7 Beats for episode seven"))

    def test_no_summary(self):
        with self.assertRaisesRegex(TicketError, r"^ticket #7: title is not \[KEY\] summary$"):
            tickets.parse(issue(title="[EP-7]"))

    def test_missing_stage(self):
        with self.assertRaisesRegex(TicketError, r"^ticket #7: no stage: label$"):
            tickets.parse(issue(labels=["room:story", "tracked", "unit:ep07"]))

    def test_two_rooms(self):
        with self.assertRaisesRegex(TicketError, r"^ticket #7: 2 room: labels$"):
            tickets.parse(issue(labels=["room:post", "room:story", "stage:beats", "tracked", "unit:ep07"]))

    def test_episode(self):
        self.assertEqual(tickets.episode("ep01/Q1"), "ep01")
        self.assertEqual(tickets.episode("ep01/Q1/3"), "ep01")
        self.assertEqual(tickets.episode("ep01"), "ep01")


class Counting:
    """The recorded adapter, counting each read by its arguments."""

    def __init__(self, gh):
        self.gh, self.calls = gh, []

    def __getattr__(self, name):
        method = getattr(self.gh, name)

        def call(*args):
            self.calls.append((name, *args))
            return method(*args)
        return call


class LoadTest(unittest.TestCase):
    def setUp(self):
        self.w = World(self, "load")

    def test_shapes(self):
        state = self.w.load()
        self.assertIsInstance(state, tickets.State)
        self.assertEqual(sorted(state.tickets), [1, 2])
        self.assertEqual(state.tickets[2], {
            "number": 2, "key": "PM-1", "summary": "Pitch episode one", "room": "story", "stage": "premise",
            "unit": "ep01", "hold": False, "state": "open", "created_at": "2026-11-01T09:00:00Z",
            "labels": ["room:story", "stage:premise", "tracked", "unit:ep01"]})
        self.assertEqual(state.tickets[1]["state"], "closed")
        self.assertEqual(state.errors, ["ticket #3: title is not [KEY] summary",
                                        "ticket #6: stage story/nope is not in the pipeline"])
        self.assertEqual(sorted(state.pulls), [4, 5])
        merged, open_pull = state.pulls[4], state.pulls[5]
        self.assertEqual(merged["state"], "merged")
        self.assertEqual(merged["comments"], [])
        self.assertNotIn("git", merged)
        self.assertNotIn("summary", merged)
        head = gitstate.head(self.w.root, "feat/PM-1-ep01")
        self.assertEqual(open_pull["git"]["head"], head)
        self.assertEqual(open_pull["git"]["manifest"], "none")
        self.assertEqual(open_pull["git"]["changed"], ["work/feat-PM-1-ep01.md"])
        self.assertEqual(open_pull["git"]["comments"], open_pull["comments"])
        self.assertEqual(open_pull["summary"]["maker"]["verb"], "READY")
        self.assertEqual(open_pull["summary"]["checkers"]["editor"]["verb"], "APPROVED")
        self.assertEqual(sorted(state.ticket_comments), [2])
        self.assertEqual([c["id"] for c in state.ticket_comments[2]], [1])
        self.assertFalse(state.frozen)

    def test_reads_once(self):
        counting = Counting(self.w.gh)
        tickets.load(self.w.p, counting, self.w.root)
        self.assertEqual(sorted(counting.calls, key=repr), sorted([
            ("issues", "all"), ("pulls", "all"),
            ("comments", 2), ("comments", 5)], key=repr))

    def test_others(self):
        state = self.w.load()
        self.assertEqual([i["number"] for i in state.others], [7])
        self.assertEqual(state.others[0]["title"], "Plan the release")
        self.assertEqual(state.others[0]["labels"], [])

    def test_queries(self):
        state = self.w.load()
        self.assertEqual([p["number"] for p in tickets.pulls_of(state, 2)], [5])
        self.assertEqual([p["number"] for p in tickets.pulls_of(state, 1)], [4])
        self.assertEqual(tickets.locked_pull(state, 1)["number"], 4)
        self.assertIsNone(tickets.locked_pull(state, 2))
        self.assertTrue(tickets.is_locked(state, "story", "research", "tides"))
        self.assertFalse(tickets.is_locked(state, "story", "premise", "ep01"))
        self.assertFalse(tickets.is_locked(state, "story", "research", "ep01"))
        self.assertEqual(tickets.launches(state, 2, "writer"), 1)
        self.assertEqual(tickets.launches(state, 2, "editor"), 0)
        self.assertIsNone(tickets.selected(state, 2))

    def test_lowest_merged_pull(self):
        self.w.apply({"pull": pull_record(10, 1, "feat/RS-1-again", state="merged")})
        self.w.apply({"pull": pull_record(9, 1, "feat/RS-1-other", state="merged")})
        self.assertEqual(tickets.locked_pull(self.w.load(), 1)["number"], 4)
        self.w.set_pull(4, state="closed", merged_at=None)
        self.assertEqual(tickets.locked_pull(self.w.load(), 1)["number"], 9)

    def test_launches_on_pulls_and_selected(self):
        self.w.apply({"comment": {"number": 5, "id": 20, "created_at": "2026-11-02T10:00:00Z", "role": "chief",
                                  "body": "[CHIEF] LAUNCH editor PM-1-b\n"}})
        self.w.apply({"comment": {"number": 2, "id": 21, "created_at": "2026-11-02T11:00:00Z", "role": "chief",
                                  "body": "[CHIEF] SELECTED #5\nthe stronger hook\n"}})
        self.w.apply({"comment": {"number": 2, "id": 22, "created_at": "2026-11-02T12:00:00Z", "role": "chief",
                                  "body": "[CHIEF] SELECTED #4\nchanged my mind\n"}})
        self.w.apply({"comment": {"number": 2, "id": 23, "created_at": "2026-11-02T13:00:00Z", "role": "chief",
                                  "body": "SELECTED #9 is not a marker\n"}})
        state = self.w.load()
        self.assertEqual(tickets.launches(state, 2, "editor"), 1)
        self.assertEqual(tickets.launches(state, 2, "writer"), 1)
        self.assertEqual(tickets.selected(state, 2), 4)


class UpstreamTest(unittest.TestCase):
    """One test per branch of Behaviour 7, each with its clean twin."""

    def setUp(self):
        self.w = World(self, "upstream")
        self.w.commit_file("episodes/ep01/premise.md", "---\nformat: lean\nlogline: A tide comes in.\n---\nPitch.\n")

    def upstream(self, number):
        state = self.w.load()
        return tickets.upstream(self.w.p, state, state.tickets[number], self.w.root)

    def test_no_after(self):
        self.assertEqual(self.upstream(1), (True, "no upstream stage"))
        self.assertEqual(self.upstream(3), (True, "upstream stage premise is locked for ep01"))

    def test_same_unit(self):
        self.assertEqual(self.upstream(3), (True, "upstream stage premise is locked for ep01"))
        self.w.set_pull(32, state="closed", merged_at=None)
        self.assertEqual(self.upstream(3), (False, "upstream stage premise is not locked for ep01"))

    def test_prefix_unit(self):
        self.assertEqual(self.upstream(9), (True, "upstream stage script is locked for ep03/Q1"))
        self.w.set_pull(34, state="closed", merged_at=None)
        self.assertEqual(self.upstream(9), (False, "upstream stage script is not locked for ep03/Q1"))

    def test_unrelated_types(self):
        self.assertEqual(self.upstream(2), (True, "upstream stage research is locked for ep01"))
        self.w.set_pull(31, state="closed", merged_at=None)
        self.assertEqual(self.upstream(2), (False, "upstream stage research is not locked for ep01"))

    def test_same_type_other_unit_does_not_count(self):
        # beats ep02 after premise: premise ep01 is locked, but the unit types are the same.
        self.assertEqual(self.upstream(4), (False, "upstream stage premise is not locked for ep02"))

    def test_skipped_by_format(self):
        self.assertEqual(self.upstream(6), (True, "skipped by format lean: upstream stage beats is locked for ep01"))
        self.w.commit_file("episodes/ep01/premise.md", "---\nformat: full\nlogline: A tide comes in.\n---\nPitch.\n")
        self.assertEqual(self.upstream(6), (False, "upstream stage outline is not locked for ep01"))

    def test_premise_off_base_is_not_read(self):
        self.w.git("checkout", "-q", "-b", "elsewhere")
        self.w.commit_file("episodes/ep01/premise.md", "---\nformat: full\nlogline: A tide comes in.\n---\nPitch.\n")
        self.assertTrue(self.upstream(6)[0])

    def test_not_locked(self):
        self.w.apply({"issue": ticket_issue(12, "PM-2", "premise", "ep02", state="closed")})
        self.w.apply({"pull": pull_record(40, 12, "feat/PM-2-ep02", state="open")})
        self.assertEqual(self.upstream(4), (False, "upstream stage premise is not locked for ep02"))
        self.w.set_pull(40, state="merged", merged_at="2026-11-03T09:00:00Z")
        self.assertEqual(self.upstream(4), (True, "upstream stage premise is locked for ep02"))


SEQUENCE = '{"id": "Q"}\n'


class EpisodeAfterSequenceTest(unittest.TestCase):
    """ISSUE-251 behaviours 1 to 6: rule 4 across ``episode`` and ``sequence`` stages."""

    def setUp(self):
        self.w = World(self)
        for rel in ("episodes/ep01/sequences/Q1/sequence.json", "episodes/ep01/sequences/Q2/sequence.json",
                    "episodes/ep02/sequences/Q1/sequence.json"):
            self.w.commit_file(rel, SEQUENCE)
        self.w.apply({"issue": ticket_issue(11, "BD-1", "board", "ep01/Q1", state="closed")})
        self.w.apply({"pull": pull_record(12, 11, "feat/BD-1", state="merged")})
        self.w.apply({"issue": ticket_issue(13, "BD-2", "board", "ep01/Q2", state="closed")})
        self.w.apply({"pull": pull_record(14, 13, "feat/BD-2", state="merged")})
        for number, unit in ((21, "ep01"), (22, "ep02"), (23, "ep03")):
            self.w.apply({"issue": ticket_issue(number, f"RL-{number}", "reel", unit)})

    def upstream(self, number):
        state = self.w.load()
        return tickets.upstream(self.w.p, state, state.tickets[number], self.w.root)

    def test_every_board(self):
        self.assertEqual(self.upstream(21), (True, "upstream stage board is locked for ep01"))
        self.w.set_pull(14, state="closed", merged_at=None)
        self.assertEqual(self.upstream(21), (False, "upstream stage board is not locked for ep01/Q2"))

    def test_another_episode(self):
        self.assertEqual(self.upstream(22), (False, "upstream stage board is not locked for ep02/Q1"))
        self.w.apply({"issue": ticket_issue(15, "BD-3", "board", "ep02/Q1", state="closed")})
        self.w.apply({"pull": pull_record(16, 15, "feat/BD-3", state="merged")})
        self.assertEqual(self.upstream(22), (True, "upstream stage board is locked for ep02"))

    def test_no_sequence(self):
        self.assertEqual(self.upstream(23),
                         (False, "upstream stage board is not locked for ep03: no sequence on the base"))
        self.assertEqual(self.upstream(21), (True, "upstream stage board is locked for ep01"))

    def test_a_sequence_added_on_the_base(self):
        self.w.commit_file("episodes/ep01/sequences/Q3/sequence.json", SEQUENCE)
        self.assertEqual(self.upstream(21), (False, "upstream stage board is not locked for ep01/Q3"))

    def test_a_sequence_off_the_base_is_not_read(self):
        path = self.w.root / "episodes/ep01/sequences/Q3/sequence.json"
        path.parent.mkdir(parents=True)
        path.write_text(SEQUENCE)
        self.assertEqual(self.upstream(21), (True, "upstream stage board is locked for ep01"))
        self.w.git("checkout", "-q", "-b", "elsewhere")
        self.w.commit("Q3 elsewhere")
        self.assertEqual(self.upstream(21), (True, "upstream stage board is locked for ep01"))

    def test_sequence_after_episode(self):
        w = World(self, "upstream")
        w.set_pull(34, state="closed", merged_at=None)
        w.apply({"issue": ticket_issue(12, "SC-4", "script", "ep04", state="closed")})
        w.apply({"pull": pull_record(40, 12, "feat/SC-4-ep04", state="merged")})
        state = w.load()
        self.assertEqual(tickets.upstream(w.p, state, state.tickets[9], w.root),
                         (False, "upstream stage script is not locked for ep03/Q1"))

    def test_sequences(self):
        self.w.commit_file("episodes/ep05/sequences/Q10/sequence.json", SEQUENCE)
        self.w.commit_file("episodes/ep05/sequences/Q2/sequence.json", SEQUENCE)
        self.w.commit_file("episodes/ep05/sequences/Q1/sequence.json", SEQUENCE)
        self.w.commit_file("episodes/ep05/sequences/Q1/shots/Q1-1.json", "{}\n")
        self.w.commit_file("episodes/ep05/sequences/notes.md", "Notes.\n")
        self.assertEqual(tickets.sequences(self.w.p, self.w.root, "ep05"), ["Q1", "Q10", "Q2"])
        self.assertEqual(tickets.sequences(self.w.p, self.w.root, "ep03"), [])


class FrozenTest(unittest.TestCase):
    def test_open_freeze_issue(self):
        self.assertTrue(tickets.frozen(github.Recorded(FIX / "freeze" / "open")))
        self.assertTrue(World(self, "freeze/open").load().frozen)

    def test_closed_freeze_issue(self):
        self.assertFalse(tickets.frozen(github.Recorded(FIX / "freeze" / "closed")))
        self.assertFalse(World(self, "freeze/closed").load().frozen)

    def test_no_freeze_issue(self):
        self.assertFalse(tickets.frozen(github.Recorded(FIX / "freeze" / "none")))
        self.assertFalse(World(self, "freeze/none").load().frozen)


class PureTest(unittest.TestCase):
    def test_no_process_or_network_imports(self):
        for name in ("tickets.py", "steps.py"):
            tree = ast.parse((ROOT / "kit" / "lib" / name).read_text())
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom):
                    imported.add((node.module or "").split(".")[0])
                    imported |= {a.name for a in node.names}
            with self.subTest(module=name):
                self.assertFalse(imported & {"subprocess", "socket", "urllib", "github"}, imported)


if __name__ == "__main__":
    unittest.main()
