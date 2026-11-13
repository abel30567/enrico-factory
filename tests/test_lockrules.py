"""Tests for kit/lib/lockrules.py: the twelve rules of the lock (system brief section 9).

The production is tests/fixtures/lock/production (its own `.factory/` kit, invented roles, rooms `story`
and `production`), made into a git repository on branch `trunk` in a temporary directory. Each open
pull's branch has one commit per directory tests/fixtures/lock/branches/<pull>/<k>/, whose files it
writes. GitHub is a copy of tests/fixtures/lock/github. A comment body's ``{head:N}`` is the head of pull
N's branch and ``{head:N~1}`` its parent, filled in when the comment is written. ``STATE_DIR`` is a
temporary directory, and the blind pair of pull 19 is prepared with a salt that shows the new version as A.

Recorded (ticket -> pull, stage):
- #1 -> #2 story/premise ep01, merged; #3 -> #4 story/board ep01, merged (the locked versions);
- #5 -> #6 production/frames: no lint, one checker;
- #7 -> #8 story/script: two lints, a REPEAT note answered `changed`, upstream premise locked;
- #9 -> #10 story/bible: a `charter` amendment, `canon-reviewer` APPROVED, `[BRAINTRUST] VOTE 5-2`;
- #11 -> #12 story/bible: a `canon` amendment, no vote;
- #13 -> #14 story/style: changes only `bible/style/**`;
- #15 -> #16, #17 story/premise, pitched: #16 is SELECTED;
- #18 -> #19 story/board, blind, three judges: 2 of 3 prefer the new version;
- #20 -> #21 story/cut, the last stage of the room: a counting `[PRODUCER] GATE PASS`.
"""

import contextlib
import hashlib
import io
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

from kit.lib import blind, cmd_lock, gitstate, github, ledger, lockrules, production, tickets  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "lock"
HEAD = re.compile(r"\{head:(\d+)(~1)?\}")
NOW = "2026-11-11T12:00:00Z"
CLEAN = (6, 8, 10, 12, 14, 16, 19, 21)
OPEN = (6, 8, 10, 12, 14, 16, 17, 19, 21)
TICKET = {6: 5, 8: 7, 10: 9, 12: 11, 14: 13, 16: 15, 17: 15, 19: 18, 21: 20}
BLIND_PULL = 19


def at(hour, minute):
    return f"2026-11-06T{hour:02d}:{minute:02d}:00Z"


class World:
    """The production repository, its state directory and a recorded GitHub, all temporary.

    ``pulls`` are the open pulls kept; the other open pulls are left out of GitHub, which keeps a test
    to the git work of the pulls it reads. ``edits`` maps a pull number to ``{path: text}`` written by the
    last commit of its branch; a text of None removes the path instead. ``blind_key`` False leaves pull 19
    without a blinding key.
    """

    def __init__(self, test, pulls=OPEN, edits=None, blind_key=True):
        tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, tmp)
        self.root = tmp / "show"
        self.dir = tmp / "github"
        self.state_dir = tmp / "state"
        patch = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                                             "FACTORY_GITHUB": str(self.dir), "FACTORY_NOW": NOW})
        patch.start()
        test.addCleanup(patch.stop)
        shutil.copytree(FIX / "production", self.root)
        (self.root / "production.local.env").write_text(f"STATE_DIR={self.state_dir}\n")
        self.git("init", "-q", "-b", "trunk")
        self.commit("the production")
        shutil.copytree(FIX / "github", self.dir)
        self.pulls = tuple(sorted(pulls))
        for number in OPEN:
            if number in self.pulls:
                self.branch(number, self.record("pulls", number)["head_ref"], (edits or {}).get(number, {}))
            else:
                (self.dir / "pulls" / f"{number}.json").unlink()
                (self.dir / "comments" / f"{number}.json").unlink()
        for path in sorted((self.dir / "comments").glob("*.json")):
            path.write_text(self.heads(path.read_text()))
        self.gh = github.Recorded(self.dir)
        if blind_key and BLIND_PULL in self.pulls:
            self.prepare_blind()

    # -- git

    def git(self, *args):
        return gitstate.git(self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", *args)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def write(self, rel, text):
        path = self.root / rel
        if text is None:
            path.unlink()
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def branch(self, number, ref, edits):
        self.git("checkout", "-q", "-b", ref, "trunk")
        commits = sorted((FIX / "branches" / str(number)).iterdir(), key=lambda d: int(d.name))
        for i, directory in enumerate(commits):
            shutil.copytree(directory, self.root, dirs_exist_ok=True)
            if i == len(commits) - 1:
                for rel, text in sorted(edits.items()):
                    self.write(rel, text)
            self.commit(f"work {directory.name} on {ref}")
        self.git("checkout", "-q", "trunk")

    def head(self, number):
        return gitstate.head(self.root, self.record("pulls", number)["head_ref"])

    def heads(self, text):
        def head(m):
            ref = self.record("pulls", int(m.group(1)))["head_ref"] + (m.group(2) or "")
            return gitstate.head(self.root, ref)
        return HEAD.sub(head, text)

    def move_base(self, rel="notes/later.md", text="Written on trunk after the pulls were made.\n"):
        self.write(rel, text)
        self.commit("move the base")

    def push(self, number, rel, text):
        """One more commit on pull ``number``'s branch, writing ``rel``."""
        ref = self.record("pulls", number)["head_ref"]
        self.git("checkout", "-q", ref)
        self.write(rel, text)
        self.commit(f"more work on {ref}")
        self.git("checkout", "-q", "trunk")

    def merge_base_into(self, number):
        ref = self.record("pulls", number)["head_ref"]
        self.git("checkout", "-q", ref)
        self.git("merge", "-q", "--no-edit", "trunk")
        self.git("checkout", "-q", "trunk")

    def release(self, episode):
        """Release ``episode`` on trunk and merge trunk into every open branch, so rule 10 still holds."""
        self.move_base(f"distribution/{episode}/release.json", '{"episode": "%s"}\n' % episode)
        for number in self.pulls:
            self.merge_base_into(number)

    # -- the recorded GitHub

    def record(self, kind, number):
        return json.loads((self.dir / kind / f"{number}.json").read_text())

    def set_record(self, kind, number, **fields):
        path = self.dir / kind / f"{number}.json"
        path.write_text(json.dumps({**json.loads(path.read_text()), **fields}, indent=1, sort_keys=True) + "\n")

    def add_label(self, kind, number, label):
        self.set_record(kind, number, labels=sorted(set(self.record(kind, number)["labels"]) | {label}))

    def _comments(self, number):
        path = self.dir / "comments" / f"{number}.json"
        return json.loads(path.read_text()) if path.is_file() else []

    def _save(self, number, comments):
        path = self.dir / "comments" / f"{number}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(comments, indent=1, sort_keys=True) + "\n")

    def edit(self, number, comment_id, **fields):
        if "body" in fields:
            fields["body"] = self.heads(fields["body"])
        found = self._comments(number)
        assert any(c["id"] == comment_id for c in found), comment_id
        self._save(number, [{**c, **fields} if c["id"] == comment_id else c for c in found])

    def replace(self, number, comment_id, old, new):
        body = next(c["body"] for c in self._comments(number) if c["id"] == comment_id)
        assert old in body, (comment_id, old)
        self.edit(number, comment_id, body=body.replace(old, new))

    def drop(self, number, comment_id):
        self._save(number, [c for c in self._comments(number) if c["id"] != comment_id])

    def add(self, number, comment_id, created_at, role, body):
        self._save(number, self._comments(number) + [
            {"id": comment_id, "created_at": created_at, "role": role, "body": self.heads(body)}])

    def add_by(self, author, number, comment_id, created_at, role, body):
        """Like ``add``, with the login ``author`` stored as the comment's author."""
        self._save(number, self._comments(number) + [
            {"id": comment_id, "created_at": created_at, "author": author, "role": role, "body": self.heads(body)}])

    def comments(self, number):
        return self.gh.comments(number)

    def log_bytes(self):
        path = self.dir / "log.jsonl"
        return path.read_bytes() if path.is_file() else None

    # -- reading

    def load(self):
        p = production.load(self.root)
        return p, tickets.load(p, self.gh, self.root)

    def rules(self, number):
        p, state = self.load()
        pull = state.pulls[number]
        return lockrules.evaluate(p, state, state.tickets[pull["ticket"]], pull, self.root)

    def failed(self, number):
        return {rule.number for rule in self.rules(number) if not rule.ok}

    def lock(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_lock.main([*argv, "--root", str(self.root)])
        return code, out.getvalue(), err.getvalue()

    def prepare_blind(self):
        """Prepare pull 19's pair with a salt that shows the new version as A."""
        head = self.head(BLIND_PULL)
        for i in range(256):
            salt = bytes([i]) * 32
            if hashlib.sha256(salt + b"\n" + str(BLIND_PULL).encode() + b"\n" + head.encode()).digest()[0] % 2 == 0:
                break
        (self.state_dir / "blind").mkdir(parents=True)
        (self.state_dir / "blind" / "salt").write_bytes(salt)
        p, state = self.load()
        key = blind.prepare(p, state, self.root, state.pulls[BLIND_PULL])
        assert key["A"] == "new", key


def report_block(out, number):
    """The report lines of one pull: its own line and the indented rule lines after it."""
    lines = out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"PR #{number} "))
    block = [lines[start]]
    for line in lines[start + 1:]:
        if not line.startswith("  "):
            break
        block.append(line)
    return block


class OneRule(unittest.TestCase):
    def assertOnly(self, w, number, expected, reason=None):
        """Exactly the rules ``expected`` fail for pull ``number``, and the report names each of them."""
        p, state = w.load()
        found = cmd_lock.candidates(p, state, production.rooms(p))
        rules = next(rules for pull, _, rules in found if pull["number"] == number)
        failed = {rule.number for rule in rules if not rule.ok}
        self.assertEqual(failed, set(expected), [r for r in rules if not r.ok])
        for rule in rules:
            self.assertEqual(rule.reason == "", rule.ok, rule)
            self.assertNotIn("\n", rule.reason)
        if reason is not None:
            self.assertTrue(any(reason in r.reason for r in rules if not r.ok), [r for r in rules if not r.ok])
        block = report_block(cmd_lock.text(found), number)
        self.assertTrue(block[0].endswith(f": NO {len(expected)} rule(s)"), block)
        names = {rule.number: rule.name for rule in rules}
        self.assertEqual([line.split(":")[0] for line in block[1:]],
                         [f"  rule {n} {names[n]}" for n in sorted(expected)])
        return rules


class CleanTest(unittest.TestCase):
    """Criterion 1: on the clean fixture every clean pull holds all twelve rules."""

    @classmethod
    def setUpClass(cls):
        cls._case = unittest.TestCase()
        cls._cleanups = []
        cls.w = World(cls)

    @classmethod
    def addCleanup(cls, function, *args, **kwargs):
        cls._cleanups.append((function, args, kwargs))

    @classmethod
    def tearDownClass(cls):
        for function, args, kwargs in reversed(cls._cleanups):
            function(*args, **kwargs)

    def test_twelve_rules_in_order_all_ok(self):
        for number in CLEAN:
            with self.subTest(pull=number):
                rules = self.w.rules(number)
                self.assertEqual([r.number for r in rules], list(range(1, len(lockrules.NAMES) + 1)))
                self.assertEqual(tuple(r.name for r in rules), lockrules.NAMES)
                self.assertEqual([r for r in rules if not r.ok], [])
                self.assertTrue(all(isinstance(r, lockrules.Rule) and r.reason == "" for r in rules))
                self.assertTrue(lockrules.lockable(rules))

    def test_report_says_lockable(self):
        code, out, err = self.w.lock()
        self.assertEqual((code, err), (0, ""))
        lines = out.splitlines()
        self.assertEqual([line.split()[1] for line in lines if line.startswith("PR ")],
                         [f"#{n}" for n in OPEN])
        self.assertIn("PR #8 #7 story/script ep01: LOCKABLE", lines)
        self.assertIn("PR #6 #5 production/frames ep01: LOCKABLE", lines)
        self.assertIn("PR #10 #9 story/bible A-1: LOCKABLE", lines)
        for number in CLEAN:
            self.assertEqual(len(report_block(out, number)), 1)
            self.assertTrue(report_block(out, number)[0].endswith(": LOCKABLE"))
        self.assertEqual(report_block(out, 17), ["PR #17 #15 story/premise ep02: NO 1 rule(s)",
                                                 "  rule 5 selected: ticket #15 selected PR #16"])

    def test_rules_are_not_trivially_true(self):
        p, state = self.w.load()
        entry = lambda n: production.stage(p, state.tickets[TICKET[n]]["room"], state.tickets[TICKET[n]]["stage"])
        # Rule 1: two lints, both listed. Rule 8: a REPEAT note answered with a change.
        self.assertEqual(entry(8)["lint"], ["media", "structure"])
        self.assertIn("REPEAT B1:", state.pulls[8]["comments"][4]["body"])
        # Rule 3: a key, three judges, 2 of 3 for the new version.
        self.assertTrue(blind.old_exists(p, state, state.pulls[19]))
        self.assertEqual(lockrules.judge_sides(p, state, state.pulls[19]),
                         {"critic": "new", "elder": "new", "viewer": "old"})
        # Rule 5: a pitched ticket, and this pull is the selected one.
        self.assertEqual(entry(16)["pitches"], 2)
        self.assertEqual(tickets.selected(state, 15), 16)
        # Rule 6: a charter amendment with a 5-2 vote; a canon amendment with no vote.
        for number, added in ((10, "bible/amendments/A-1.json"), (12, "bible/amendments/A-2.json")):
            self.assertTrue(lockrules.needs_amendment(entry(number), state.pulls[number]["git"]["changed"]))
            self.assertIn(added, state.pulls[number]["git"]["changed"])
        self.assertTrue(any(c["body"].startswith("[BRAINTRUST] VOTE 5-2") for c in state.pulls[10]["comments"]))
        self.assertFalse(any("VOTE" in c["body"] for c in state.pulls[12]["comments"]))
        # Rule 9: the last stage of the room, covered by a gate PASS.
        self.assertTrue(lockrules.gate_needed(p, state, state.tickets[20], entry(21), "cutter"))
        self.assertTrue(ledger.covers(state.pulls[21]["git"], state.pulls[21]["comments"], entry(21)))
        self.assertFalse(lockrules.gate_needed(p, state, state.tickets[7], entry(8), "writer"))
        # Rule 1 on a stage that names no lint.
        self.assertEqual(entry(6)["lint"], [])


class LintRuleTest(OneRule):
    def test_lint_fail(self):
        w = World(self, (8,))
        w.replace(8, 807, "[LINT] PASS", "[LINT] FAIL")
        self.assertOnly(w, 8, {1}, "lint #807 is FAIL")

    def test_checks_required_line_omits_a_lint(self):
        w = World(self, (8,))
        w.replace(8, 807, "Checks required by stage script: media, structure", "Checks required by stage script: structure")
        self.assertOnly(w, 8, {1}, "lint #807 does not list media for stage script")


class CheckersRuleTest(OneRule):
    def test_checker_with_no_verdict(self):
        w = World(self, (8,))
        w.drop(8, 808)
        self.assertOnly(w, 8, {2}, "checker editor has no counting verdict")

    def test_open_blocker(self):
        w = World(self, (8,))
        w.edit(8, 808, body="[EDITOR] NOTES\nB3 (Blocker): the last line is missing.\n"
                            "reviewed head {head:8} manifest none\n")
        self.assertOnly(w, 8, {2}, "checker editor NOTES #808")


class JudgesRuleTest(OneRule):
    def test_judge_with_no_verdict(self):
        w = World(self, (12,))
        w.drop(12, 1204)
        self.assertOnly(w, 12, {3}, "judge braintrust has no counting verdict")

    def test_blind_one_of_three_for_new(self):
        w = World(self, (19,))
        w.replace(19, 1905, "[ELDER] PREFERS A", "[ELDER] PREFERS B")
        self.assertOnly(w, 19, {3}, "1 of 3 judges prefer the new version")

    def test_blind_without_a_key(self):
        w = World(self, (19,), blind_key=False)
        rules = self.assertOnly(w, 19, {3})
        self.assertEqual(rules[2].reason, "no blinding key")


class UpstreamRuleTest(OneRule):
    def test_upstream_not_locked(self):
        w = World(self, (8,))
        w.set_record("pulls", 2, state="closed", merged_at=None)
        self.assertOnly(w, 8, {4}, "upstream stage premise is not locked for ep01")

    def test_cheaper_stages_of_a_story_ticket(self):
        w = World(self, ())
        p, state = w.load()
        self.assertEqual(lockrules.cheaper_stages(p, state, state.tickets[7], w.root), "")


class SelectedRuleTest(OneRule):
    def test_another_pull_selected(self):
        w = World(self, (16,))
        w.replace(15, 1503, "SELECTED #16", "SELECTED #17")
        self.assertOnly(w, 16, {5}, "ticket #15 selected PR #17")


class AmendmentRuleTest(OneRule):
    def test_canon_change_without_an_amendment_file(self):
        w = World(self, (12,), edits={12: {"bible/amendments/A-2.json": None}})
        self.assertOnly(w, 12, {6}, "adds 0 amendment file(s)")

    def test_amendment_with_no_class(self):
        w = World(self, (10,), edits={10: {"bible/amendments/A-1.json": '{"summary": "Add rule 2."}\n'}})
        self.assertOnly(w, 10, {6}, "bible/amendments/A-1.json has no class")

    def test_charter_amendment_with_a_four_three_vote(self):
        w = World(self, (10,))
        w.replace(10, 1007, "VOTE 5-2", "VOTE 4-3")
        self.assertOnly(w, 10, {6}, "class charter needs a [BRAINTRUST] VOTE")

    def test_vote_older_than_the_last_addressed(self):
        w = World(self, (10,))
        w.edit(10, 1007, created_at=at(10, 15))  # after READY (10:00), before ADDRESSED (10:20)
        self.assertOnly(w, 10, {6}, "class charter needs a [BRAINTRUST] VOTE")

    VOTE = "[BRAINTRUST] VOTE 5-2\nFive seats for, two against.\n"
    CANNOT = "bible/amendments/A-1.json: the [BRAINTRUST] VOTE does not count: "

    def test_vote_without_the_reviewed_line(self):
        w = World(self, (10,))
        w.edit(10, 1007, body=self.VOTE)
        rules = self.assertOnly(w, 10, {6})
        self.assertEqual(rules[5].reason, self.CANNOT + "reviewed: no reviewed head line")

    def test_vote_for_an_earlier_head(self):
        w = World(self, (10,))
        voted = w.head(10)
        w.push(10, "bible/canon/lamps.md", "# Lamps\n\nA lamp stays lit.\n")
        head = w.head(10)
        for verdict in (1004, 1005, 1006):  # the lint, checker and judge review the new head
            w.replace(10, verdict, voted, head)
        w.edit(10, 1007, body=self.VOTE + "reviewed head {head:10~1} manifest none\n")
        rules = self.assertOnly(w, 10, {6})
        self.assertEqual(rules[5].reason,
                         self.CANNOT + f"head: commit {head} after {voted} is not a merge of the base")

    def test_vote_for_another_manifest(self):
        w = World(self, (10,))
        w.replace(10, 1007, "manifest none", "manifest " + "0" * 64)
        rules = self.assertOnly(w, 10, {6})
        self.assertEqual(rules[5].reason,
                         self.CANNOT + "manifest: %s is not the current manifest none" % ("0" * 64))

    def test_vote_followed_only_by_a_merge_of_the_base(self):
        w = World(self, (10,))
        voted = w.head(10)
        w.move_base()
        w.merge_base_into(10)
        self.assertNotEqual(w.head(10), voted)
        self.assertTrue(w.comments(10)[-1]["body"].endswith(f"reviewed head {voted} manifest none\n"))
        self.assertEqual(w.failed(10), set())

    def test_canon_amendment_that_changes_the_charter_needs_a_vote(self):
        w = World(self, (12,), edits={12: {"bible/charter.md": "# Charter\n\n1. Kites may land.\n"}})
        self.assertOnly(w, 12, {6}, "a change to bible/charter.md needs a [BRAINTRUST] VOTE")

    def test_canon_reviewer_must_approve(self):
        w = World(self, (10,))
        w.drop(10, 1005)
        # canon-reviewer is also the stage's checker, so rule 2 fails with it.
        self.assertOnly(w, 10, {2, 6}, "canon-reviewer has no counting APPROVED")


class AmendmentScopeTest(OneRule):
    """Criterion 3: which pulls need an amendment."""

    def test_style_pull_changing_only_bible_style_needs_none(self):
        w = World(self, (14,))
        p, state = w.load()
        entry = production.stage(p, "story", "style")
        self.assertEqual(state.pulls[14]["git"]["changed"], ["bible/style/palette.md"])
        self.assertFalse(lockrules.needs_amendment(entry, state.pulls[14]["git"]["changed"]))
        self.assertTrue(w.rules(14)[5].ok)

    def test_another_stage_changing_bible_canon_fails(self):
        w = World(self, (14,), edits={14: {"bible/canon/kites.md": "# Kites\n\nNo kite has an owner.\n"}})
        # bible/canon/kites.md is also outside the designer's writes, so rule 7 fails with it.
        rules = self.assertOnly(w, 14, {6, 7}, "adds 0 amendment file(s)")
        self.assertEqual(rules[6].reason, "outside the writes of designer: bible/canon/kites.md")

    def test_needs_amendment(self):
        bible = {"unit": "amendment", "paths": ["bible/charter.md", "bible/canon/**", "bible/amendments/**"]}
        style = {"unit": "look", "paths": ["bible/style/**"]}
        self.assertTrue(lockrules.needs_amendment(bible, []))
        self.assertFalse(lockrules.needs_amendment(style, ["bible/style/a.md"]))
        self.assertTrue(lockrules.needs_amendment(style, ["bible/style/a.md", "bible/canon/x.md"]))
        self.assertFalse(lockrules.needs_amendment({"unit": "episode", "paths": ["episodes/*/a.md"]},
                                                   ["episodes/ep01/a.md"]))


class WritesRuleTest(OneRule):
    def test_file_outside_writes(self):
        w = World(self, (8,), edits={8: {"episodes/ep01/board/panel-9.md": "A panel the writer may not draw.\n"}})
        rules = self.assertOnly(w, 8, {7})
        self.assertEqual(rules[6].reason, "outside the writes of writer: episodes/ep01/board/panel-9.md")

    def test_file_outside_the_room(self):
        w = World(self, (8,), edits={8: {"episodes/ep01/notes.md": "Notes no stage of the room holds.\n"}})
        rules = self.assertOnly(w, 8, {7})
        self.assertEqual(rules[6].reason, "outside room story: episodes/ep01/notes.md")

    def test_no_maker_ready_fails_rules_seven_and_nine(self):
        w = World(self, (6,))
        w.edit(6, 601, role="inspector")
        rules = self.assertOnly(w, 6, {7, 9})
        self.assertEqual(rules[6].reason, "no maker of stage frames has posted READY")
        self.assertEqual(rules[8].reason, "no maker of stage frames has posted READY")


class RepeatNotesRuleTest(OneRule):
    REBUTTED = ("- B2 by editor: changed. The scene is one line now.",
                "- B2 by editor: rebutted. The scene carries the ending.")

    def test_repeat_note_rebutted_with_no_ruling(self):
        w = World(self, (8,))
        w.replace(8, 806, *self.REBUTTED)
        self.assertOnly(w, 8, {8}, "editor B2 (#805)")

    def test_holds_when_addressed_says_changed(self):
        w = World(self, (8,))
        self.assertTrue(w.rules(8)[7].ok)

    def test_holds_when_a_later_ruling_on_the_ticket_names_the_note(self):
        w = World(self, (8,))
        w.replace(8, 806, *self.REBUTTED)
        w.add(7, 702, at(10, 55), "chief", "[CHIEF] RULING\neditor B2: the scene stays. Charter rule 1.\n")
        self.assertTrue(w.rules(8)[7].ok)

    def test_holds_when_a_later_ruling_on_the_pull_names_the_note(self):
        w = World(self, (8,))
        w.replace(8, 806, *self.REBUTTED)
        w.add(8, 809, at(10, 55), "chief", "[CHIEF] RULING\neditor B2: the scene stays.\n")
        self.assertTrue(w.rules(8)[7].ok)

    def test_ruling_before_the_note_or_on_another_note_does_not_count(self):
        for created_at, body in ((at(10, 35), "[CHIEF] RULING\neditor B2: the scene stays.\n"),
                                 (at(10, 55), "[CHIEF] RULING\neditor B21: the scene stays.\n"),
                                 (at(10, 55), "[FOREMAN] RULING\neditor B2: the scene stays.\n")):
            with self.subTest(body=body, created_at=created_at):
                w = World(self, (8,))
                w.replace(8, 806, *self.REBUTTED)
                w.add(7, 702, created_at, "chief", body)
                self.assertFalse(w.rules(8)[7].ok)


class GateRuleTest(OneRule):
    def test_last_stage_without_a_gate_pass(self):
        w = World(self, (21,))
        w.drop(21, 2104)
        self.assertOnly(w, 21, {9}, "stage cut is the last of room story")

    def test_past_the_iteration_cap_without_a_gate_pass(self):
        w = World(self, (8,))
        for i in (2, 3, 4):
            w.add(7, 700 + i + 10, at(9, i), "chief", f"[CHIEF] LAUNCH writer {i}\nprompt sha256 recorded.\n")
        self.assertOnly(w, 8, {9}, "ticket #7 passed CAP_TICKET_ITERATIONS for writer")

    def test_at_the_iteration_cap_needs_no_gate(self):
        w = World(self, (8,))
        for i in (2, 3):
            w.add(7, 700 + i + 10, at(9, i), "chief", f"[CHIEF] LAUNCH writer {i}\nprompt sha256 recorded.\n")
        self.assertTrue(w.rules(8)[8].ok)


class BaseRuleTest(OneRule):
    def test_base_moved(self):
        w = World(self, (8,))
        w.move_base()
        self.assertOnly(w, 8, {10}, "head does not contain the base trunk")

    def test_hold_label_on_the_ticket(self):
        w = World(self, (8,))
        w.add_label("issues", 7, "hold")
        self.assertOnly(w, 8, {10}, "ticket #7 has the label hold")

    def test_hold_label_on_the_pull(self):
        w = World(self, (8,))
        w.add_label("pulls", 8, "hold")
        self.assertOnly(w, 8, {10}, "PR #8 has the label hold")


class ReleasedRuleTest(OneRule):
    def test_released_episode(self):
        w = World(self, (8, 10))
        w.release("ep01")
        self.assertOnly(w, 8, {11}, "changes files of a released episode: episodes/ep01/script.md")
        self.assertTrue(w.rules(10)[10].ok)  # the bible is no episode's file

    def test_another_episode_released(self):
        w = World(self, (8, 16))
        w.release("ep02")
        self.assertTrue(lockrules.lockable(w.rules(8)))
        self.assertOnly(w, 16, {11}, "episodes/ep02/premise.md")


EP01 = "episodes/ep01/reel/edit-list.json"
EP02 = "episodes/ep02/reel/edit-list.json"


def edit_list(bible):
    return json.dumps({"fps": 24, "bible": bible, "events": []}) + "\n"


class BibleVersionTest(OneRule):
    """Rule 12: every edit list the pull request changes names the newest amendment of its head."""

    def world(self, pull=21, edits=None, amendments=("A-1", "A-2"), base=None):
        """``edits`` on ``pull``; ``amendments`` (names under bible/amendments/, ``.json`` added when there is
        no extension) and ``base`` ({path: text}) written on trunk, then the base merged into ``pull``."""
        w = World(self, (pull,), edits={pull: edits or {}})
        for name in amendments:
            w.move_base(f"bible/amendments/{name if '.' in name else name + '.json'}", '{"class": "canon"}\n')
        for rel, text in sorted((base or {}).items()):
            w.move_base(rel, text)
        w.merge_base_into(pull)
        return w

    def test_older_amendment_fails(self):
        w = self.world(edits={EP01: edit_list("A-1")})
        self.assertOnly(w, 21, {12}, f"{EP01}: bible is A-1, the newest amendment is A-2")
        self.assertEqual(w.rules(21)[11].reason, f"{EP01}: bible is A-1, the newest amendment is A-2")
        code, out, err = w.lock()
        self.assertEqual((code, err), (0, ""))
        self.assertIn(f"  rule 12 bible-version: {EP01}: bible is A-1, the newest amendment is A-2",
                      out.splitlines())

    def test_newest_amendment_locks(self):
        w = self.world(edits={EP01: edit_list("A-2")})
        self.assertTrue(lockrules.lockable(w.rules(21)))
        code, out, err = w.lock("--apply")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("locked PR #21 at ", out)
        self.assertEqual(w.record("pulls", 21)["state"], "merged")

    def test_edit_list_on_the_base_only_reads_the_pulls_that_change_it(self):
        w = self.world(pull=8, base={EP01: edit_list("A-1")})
        self.assertTrue(lockrules.lockable(w.rules(8)))
        w = self.world(base={EP01: edit_list("A-1")})
        self.assertTrue(w.rules(21)[11].ok)
        w.push(21, EP01, json.dumps({"fps": 25, "bible": "A-1", "events": []}) + "\n")
        rule = w.rules(21)[11]
        self.assertEqual((rule.number, rule.name, rule.ok), (12, "bible-version", False))
        self.assertEqual(rule.reason, f"{EP01}: bible is A-1, the newest amendment is A-2")
        w.push(21, EP01, json.dumps({"fps": 25, "bible": "A-2", "events": []}) + "\n")
        self.assertTrue(w.rules(21)[11].ok)

    def test_numbers_compare_as_integers(self):
        tens = [f"A-{n}" for n in range(1, 11)]
        w = self.world(edits={EP01: edit_list("A-9")}, amendments=tens)
        self.assertOnly(w, 21, {12}, f"{EP01}: bible is A-9, the newest amendment is A-10")
        w = self.world(edits={EP01: edit_list("A-10")}, amendments=tens)
        self.assertTrue(lockrules.lockable(w.rules(21)))

    def test_equal_numbers_the_id_that_sorts_last_wins(self):
        w = self.world(edits={EP01: edit_list("A-02")}, amendments=("A-1", "A-02", "A-2"))
        self.assertOnly(w, 21, {12}, f"{EP01}: bible is A-02, the newest amendment is A-2")
        w = self.world(edits={EP01: edit_list("A-2")}, amendments=("A-1", "A-02", "A-2"))
        self.assertTrue(lockrules.lockable(w.rules(21)))

    def test_other_files_under_amendments_are_ignored(self):
        others = ("A-1", "A-2", "A-9.md", "A3-founding.md", "B-5.json", "notes/A-7.json")
        w = self.world(edits={EP01: edit_list("A-1")}, amendments=others)
        self.assertOnly(w, 21, {12}, f"{EP01}: bible is A-1, the newest amendment is A-2")
        w = self.world(edits={EP01: edit_list("A-2")}, amendments=others)
        self.assertTrue(lockrules.lockable(w.rules(21)))

    def test_unreadable_edit_list(self):
        texts = ["{fps", "[]\n", json.dumps({"fps": 24, "events": []}), json.dumps({"fps": 24, "bible": 2}),
                 edit_list("A2"), edit_list("A-2.json")]
        for text in texts:
            with self.subTest(text=text):
                w = self.world(edits={EP01: text})
                self.assertOnly(w, 21, {12})
                self.assertEqual(w.rules(21)[11].reason, f"{EP01}: bible is not the id A-<n> of an amendment")

    def test_head_with_no_amendment(self):
        w = self.world(edits={EP01: edit_list("A-1")}, amendments=("A-1.md", "B-1.json"))
        self.assertOnly(w, 21, {12})
        self.assertEqual(w.rules(21)[11].reason,
                         f"{EP01}: bible is A-1, the head has no bible/amendments/A-<n>.json")

    def test_two_edit_lists(self):
        w = self.world(edits={EP01: edit_list("A-2"), EP02: edit_list("A-1")})
        self.assertOnly(w, 21, {12})
        self.assertEqual(w.rules(21)[11].reason, f"{EP02}: bible is A-1, the newest amendment is A-2")
        w = self.world(edits={EP02: edit_list("A-1"), EP01: "{fps"})
        self.assertOnly(w, 21, {12})
        self.assertEqual(w.rules(21)[11].reason, f"{EP01}: bible is not the id A-<n> of an amendment; "
                                                 f"{EP02}: bible is A-1, the newest amendment is A-2")
        w = self.world(edits={EP01: edit_list("A-2"), EP02: edit_list("A-2")})
        self.assertTrue(lockrules.lockable(w.rules(21)))

    def test_deleted_edit_list_is_skipped(self):
        w = self.world(base={EP01: edit_list("A-1")})
        w.push(21, EP01, None)
        _, state = w.load()
        self.assertIn(EP01, state.pulls[21]["git"]["changed"])
        self.assertTrue(w.rules(21)[11].ok)

    def test_stale_head_fails_the_base_then_the_bible_version(self):
        w = self.world(edits={EP01: edit_list("A-1")}, amendments=("A-1",))
        self.assertTrue(lockrules.lockable(w.rules(21)))
        w.move_base("bible/amendments/A-2.json", '{"class": "canon"}\n')
        self.assertOnly(w, 21, {10}, "head does not contain the base trunk")
        w.merge_base_into(21)
        self.assertOnly(w, 21, {12}, f"{EP01}: bible is A-1, the newest amendment is A-2")

    def test_twelfth_rule_in_the_json_report(self):
        w = self.world(edits={EP01: edit_list("A-1")})
        rules = w.rules(21)
        self.assertEqual([r.number for r in rules], list(range(1, len(lockrules.NAMES) + 1)))
        self.assertEqual(rules[11].name, "bible-version")
        code, out, err = w.lock("--json")
        self.assertEqual((code, err), (0, ""))
        last = json.loads(out)[0]["rules"][11]
        self.assertEqual(last, {"name": "bible-version", "number": 12, "ok": False,
                                "reason": f"{EP01}: bible is A-1, the newest amendment is A-2"})


class FreshnessTest(OneRule):
    """Criterion 5: a stale or misplaced APPROVED fails rule 2 alone."""

    def test_approved_older_than_the_last_addressed(self):
        w = World(self, (8,))
        w.edit(8, 808, created_at=at(10, 45))  # the last ADDRESSED is at 10:50
        self.assertOnly(w, 8, {2}, "checker editor has no counting verdict")

    def test_approved_on_an_earlier_head_with_a_maker_commit_since(self):
        w = World(self, (8,))
        w.replace(8, 808, "reviewed head {head:8}".replace("{head:8}", w.head(8)), "reviewed head {head:8~1}")
        p, state = w.load()
        self.assertIn(gitstate.head(w.root, "feat/SC-1-script~1"), [c["sha"] for c in state.pulls[8]["git"]["commits"]])
        self.assertOnly(w, 8, {2}, "checker editor has no counting verdict")

    def test_approved_from_a_checker_the_stage_does_not_name(self):
        w = World(self, (14,))
        w.edit(14, 1403, role="canon-reviewer",
               body="[CANON-REVIEWER] APPROVED\nEvery criterion holds.\nreviewed head {head:14} manifest none\n")
        self.assertOnly(w, 14, {2}, "checker editor has no counting verdict")



class CommentAuthorTest(OneRule):
    """ISSUE-225 behaviour 4: a comment by a login ``GITHUB_LOGINS`` does not name counts for no rule.

    Each case takes a counting comment away and stores it again as written by ``stranger``. The adapter
    comes from ``github.connect`` on the production's settings, as ``factory lock`` gets it.
    """

    def forged(self, pull, dropped, again, named):
        w = World(self, (pull,))
        comment = next(c for c in w._comments(pull) if c["id"] == again)
        for comment_id in dropped:
            w.drop(pull, comment_id)
        w.add_by("stranger", pull, comment["id"], comment["created_at"], comment["role"], comment["body"])
        if named:
            with open(w.root / "production.local.env", "a") as f:
                f.write("GITHUB_LOGINS=stranger\n")
        w.gh = github.connect(production.load(w.root).settings)
        return w

    def assertForgedFails(self, pull, dropped, again, rule, reason):
        w = self.forged(pull, dropped, again, named=False)
        self.assertNotIn(again, [c["id"] for c in w.comments(pull)])
        self.assertOnly(w, pull, {rule}, reason)
        code, out, err = w.lock()
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(report_block(out, pull)[0].endswith(": NO 1 rule(s)"), out)

    def assertNamedLocks(self, pull, dropped, again):
        w = self.forged(pull, dropped, again, named=True)
        self.assertIn(again, [c["id"] for c in w.comments(pull)])
        self.assertEqual(w.failed(pull), set())
        code, out, err = w.lock()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(len(report_block(out, pull)), 1, out)
        self.assertTrue(report_block(out, pull)[0].endswith(": LOCKABLE"), out)

    def test_forged_approval(self):
        self.assertForgedFails(8, (808,), 808, 2, "checker editor has no counting verdict")

    def test_forged_approval_by_a_named_login(self):
        self.assertNamedLocks(8, (808,), 808)

    def test_forged_lint_pass(self):
        self.assertForgedFails(8, (802, 807), 807, 1, "no counting [LINT] verdict")

    def test_forged_lint_pass_by_a_named_login(self):
        self.assertNamedLocks(8, (802, 807), 807)

    def test_forged_gate_record(self):
        self.assertForgedFails(21, (2104,), 2104, 9, "stage cut is the last of room story")

    def test_forged_gate_record_by_a_named_login(self):
        self.assertNamedLocks(21, (2104,), 2104)


if __name__ == "__main__":
    unittest.main()
