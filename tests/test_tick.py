"""Tests for kit/lib/cmd_tick.py: `factory tick` carries out the next step of every open ticket.

The production is tests/fixtures/tick/production (its own `.factory/` kit, invented roles, rooms `story`
and `production`), made a git repository on branch `trunk` in a temporary directory with a clean index.
Each kept open pull's branch has one commit writing the files of tests/fixtures/tick/branches/<pull>/.
GitHub is a copy of tests/fixtures/tick/github with only the issues and pulls a test reads; a comment
body's ``{head:N}`` is the head of pull N's branch. The harness is a copy of the fake harness.

Recorded (ticket -> pull, stage):
- #1 -> #2 story/draft ep01, merged (the locked version); #3 -> #4 draft ep01 again: blind;
- #5 -> #6 story/draft ep02, READY only: lint;
- #7 -> #8 story/final ep01, the room's last stage, READY and APPROVED: gate, sync, lock;
- #9 -> #10, #11 -> #12 production/frames ep01 and ep02: lockable, no gate;
- #13 -> #14 story/draft ep03: lockable, no gate;
- #15 -> #16, #17 story/pitch ep01, two pitches, both lockable;
- #18 production/frames ep03, no pull: a maker launch;
- #19 -> #20 production/frames ep04, READY only: a checker launch;
- #21 production/render ep05: waits for frames ep05;
- #22 -> #23 production/frames ep06: three rounds of notes;
- #24 an open issue with the label `factory:freeze`.
"""

import contextlib
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

from kit.lib import blind, cmd_tick, gitstate, github, launch, lint, production  # noqa: E402
from kit.lib import cmd_lock  # noqa: E402
from kit.lib import steps  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "tick"
FAKE = ROOT / "tests" / "fixtures" / "harness" / "fake-harness"
NOW = "2026-11-11T12:00:00Z"
HEAD = re.compile(r"\{head:(\d+)\}")
ACCOUNTS = ("acct-one", "acct-two")
EVERY = tuple(range(1, 24))
LAST = re.compile(r"tick: (\d+) steps, (\d+) need, (\d+) wait, (\d+) failed\n\Z")
LABEL = "needs:orchestrator"
GATE_PASS = ("[PRODUCER] GATE PASS\ncovers: lock seeded\ncap: -  reason: within caps\n"
             "reviewed head {head:8} manifest none\n")
GATE_STOP = ("[PRODUCER] GATE STOP\ncovers: lock seeded\ncap: CAP_EPISODE_USD  reason: over cap\n"
             "reviewed head {head:8} manifest none\n")


def step(match, result, files=None):
    return {"match": match, "result": result, "files": files or {}}


EVIDENCE = [{"criterion": "done", "result": "pass", "command": "cat"}]
PAINTER_READY = step("You are the painter", {"marker": "READY", "summary": "Frames drawn.", "evidence": EVIDENCE},
                     {"production/ep03/frames.md": "# Frames of episode three\n\nThe pier at night.\n"})
INSPECTOR_APPROVED = step("You are the inspector",
                          {"marker": "APPROVED", "summary": "The frames hold.", "evidence": EVIDENCE})


def later(minutes):
    return f"2026-11-11T{12 + minutes // 60:02d}:{minutes % 60:02d}:00Z"


class World:
    """A production on `trunk`, a recorded GitHub with the issues and pulls ``keep``, a fake harness."""

    def __init__(self, cleanup, keep=EVERY, edits=None):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        cleanup(shutil.rmtree, self.tmp)
        self.github = self.tmp / "github"
        self.state = self.tmp / "state"
        env = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "FACTORY_NOW": NOW,
               "FACTORY_GITHUB": str(self.github), "FACTORY_LAUNCH_WAIT": "1"}
        patch = mock.patch.dict(os.environ, env)
        patch.start()
        cleanup(patch.stop)
        self.root = self.tmp / "show"
        shutil.copytree(FIX / "production", self.root)
        self.git("init", "-q", "-b", "trunk")
        self.commit("the production")
        shutil.copytree(FIX / "github", self.github)
        for path in sorted(self.github.glob("*/*.json")):
            if int(path.stem) not in keep:
                path.unlink()
        for path in sorted((self.github / "pulls").glob("*.json")):
            pull = json.loads(path.read_text())
            if pull["state"] == "open":
                self.branch(pull["number"], pull["head_ref"], (edits or {}).get(pull["number"], {}))
        for path in sorted((self.github / "comments").glob("*.json")):
            path.write_text(self.heads(path.read_text()))
        self.harness = self.tmp / "harness"
        self.harness.mkdir()
        shutil.copy2(FAKE, self.harness / "fake-harness")
        self.steps()
        secrets = self.tmp / "secrets"
        (secrets / "inference.primary").mkdir(parents=True)
        for name in ACCOUNTS:
            token = secrets / "inference.primary" / name
            token.write_text("token-for-tests\n")
            token.chmod(0o600)
        settings = {"STATE_DIR": self.state, "SECRETS_DIR": secrets, "HARNESS": self.harness / "fake-harness",
                    "MODEL_MAKER": "maker-model-1", "MODEL_CHECKER": "checker-model-1",
                    "ACCOUNTS_MAKER": ", ".join(ACCOUNTS), "ACCOUNTS_CHECKER": ", ".join(ACCOUNTS)}
        (self.root / "production.local.env").write_text("".join(f"{k}={v}\n" for k, v in settings.items()))
        self.gh = github.Recorded(self.github)

    # -- git

    def git(self, *args):
        return gitstate.git(self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", *args)

    def commit(self, message):
        # Empty the index first, so `add` hashes every file instead of trusting a copied fixture's mtime.
        self.git("rm", "-r", "-q", "--cached", "--ignore-unmatch", ".")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def branch(self, number, ref, edits):
        self.git("checkout", "-q", "-b", ref, "trunk")
        shutil.copytree(FIX / "branches" / str(number), self.root, dirs_exist_ok=True)
        for rel, text in sorted(edits.items()):
            self.write(rel, text)
        self.commit(f"work on {ref}")
        self.git("checkout", "-q", "trunk")

    def head(self, ref):
        return gitstate.head(self.root, ref)

    def ref(self, number):
        return self.record("pulls", number)["head_ref"]

    def heads(self, text):
        return HEAD.sub(lambda m: self.head(self.ref(int(m.group(1)))), text)

    # -- the recorded GitHub

    def record(self, kind, number):
        return json.loads((self.github / kind / f"{number}.json").read_text())

    def set_record(self, kind, number, **fields):
        path = self.github / kind / f"{number}.json"
        path.write_text(json.dumps({**self.record(kind, number), **fields}, indent=1, sort_keys=True) + "\n")

    def add(self, number, created_at, role, body):
        """A comment written straight into the recorded GitHub, as an earlier run would have left it."""
        path = self.github / "comments" / f"{number}.json"
        found = json.loads(path.read_text()) if path.is_file() else []
        ids = [c["id"] for p in (self.github / "comments").glob("*.json") for c in json.loads(p.read_text())]
        found.append({"id": max(ids, default=0) + 1, "created_at": created_at, "role": role,
                      "body": self.heads(body)})
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(found, indent=1, sort_keys=True) + "\n")

    def comments(self, number):
        return self.gh.comments(number)

    def log(self):
        path = self.github / "log.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []

    def ops(self, op):
        return [entry for entry in self.log() if entry["op"] == op]

    # -- the rest

    def steps(self, *steps):
        (self.harness / "fake-harness.json").write_text(json.dumps(list(steps), indent=1) + "\n")

    def p(self):
        return production.load(self.root)

    def snapshot(self):
        files = {p.relative_to(self.github).as_posix(): p.read_bytes()
                 for p in sorted(self.github.rglob("*")) if p.is_file()}
        listing = sorted(p.relative_to(self.state).as_posix() for p in self.state.rglob("*")) \
            if self.state.exists() else []
        return files, self.git("rev-parse", "--all"), listing

    def tick(self, *args):
        """``(exit code, stdout, stderr)`` of ``factory tick *args --root <root>``."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_tick.main([*args, "--root", str(self.root)])
        return code, out.getvalue(), err.getvalue()

    def markers_lint(self, comments):
        """``lint.run(tree, only=["markers"])`` on a tree holding the kit and ``comments``."""
        tree = self.tmp / "tree"
        shutil.rmtree(tree, ignore_errors=True)
        shutil.copytree(self.root / ".factory", tree / ".factory")
        (tree / ".factory-state").mkdir()
        data = [{"id": c["id"], "role": c["role"], "body": c["body"]} for c in comments]
        (tree / ".factory-state" / "comments.json").write_text(json.dumps(data, indent=1) + "\n")
        return lint.run(tree, only=["markers"])["markers"]


def lines(out):
    """The output without its last line."""
    return out.splitlines()[:-1]


class Case(unittest.TestCase):
    def world(self, keep, edits=None):
        return World(self.addCleanup, keep, edits)

    def ticked(self, w, *args, code=0):
        found, out, err = w.tick(*args)
        self.assertEqual((found, err), (code, ""), out)
        self.assertRegex(out, LAST)
        return out

    def dry_then_apply(self, w, expected):
        """The line without ``--apply`` with the snapshot unchanged, then the same line with it."""
        before = w.snapshot()
        out = self.ticked(w)
        self.assertEqual(lines(out), expected)
        self.assertEqual(w.snapshot(), before)
        out = self.ticked(w, "--apply")
        self.assertEqual(lines(out), expected)
        return before


class Rows(Case):
    """Criterion 1: the rows none, launch, lint and blind."""

    def test_none(self):
        w = self.world((21,))
        before = self.dry_then_apply(w, ["WAIT #21 waiting upstream stage frames is not locked for ep05"])
        self.assertEqual(w.snapshot(), before)

    def test_launch_a_maker_on_a_ready_ticket(self):
        w = self.world((18,))
        w.steps(PAINTER_READY)
        self.dry_then_apply(w, ["STEP #18 launch painter on #18"])
        [record] = w.comments(18)
        self.assertEqual((record["role"], record["body"].split("\n")[0]), ("foreman", "[FOREMAN] LAUNCH painter 18-1"))
        [ready] = w.comments(19)
        self.assertEqual((ready["role"], ready["body"].split("\n")[0]), ("painter", "[PAINTER] READY"))
        self.assertEqual(w.record("pulls", 19)["ticket"], 18)

    def test_launch_a_checker_on_a_pull(self):
        w = self.world((19, 20))
        w.steps(INSPECTOR_APPROVED)
        self.dry_then_apply(w, ["STEP #19 launch inspector on PR #20"])
        found = [(c["role"], c["body"].split("\n")[0]) for c in w.comments(20)][1:]
        self.assertEqual(found, [("foreman", "[FOREMAN] LAUNCH inspector 20-1"), ("inspector", "[INSPECTOR] APPROVED")])

    def test_lint(self):
        w = self.world((5, 6))
        self.dry_then_apply(w, ["STEP #5 lint PR #6"])
        posted = [entry for entry in w.ops("post")]
        self.assertEqual([(e["number"], e["role"], e["body"].split("\n")[0]) for e in posted],
                         [(6, "program:lint", "[LINT] PASS")])

    def test_blind(self):
        w = self.world((1, 2, 3, 4))
        p = w.p()
        self.assertFalse(blind.has_key(p, 4, w.head(w.ref(4))))
        self.dry_then_apply(w, ["STEP #3 blind PR #4"])
        self.assertTrue(blind.has_key(p, 4, w.head(w.ref(4))))
        self.assertEqual(w.log(), [])


class Lock(Case):
    """Criteria 2 and 10: gate, sync and lock on the last stage, and the seeded refusals."""

    def test_gate_then_sync_then_lock(self):
        w = self.world((7, 8))
        ref = w.ref(8)
        base_before = w.head("trunk")
        out = self.ticked(w, "--apply")
        self.assertEqual(lines(out), ["STEP #7 gate PR #8"])
        gates = [c for c in w.comments(8) if c["role"] == "program:gate"]
        self.assertEqual([c["body"].split("\n")[0] for c in gates], ["[PRODUCER] GATE PASS"])
        self.assertEqual(w.git("rev-list", "--count", f"{base_before}..trunk").strip(), "1")

        old, base = w.head(ref), w.head("trunk")
        out = self.ticked(w, "--apply")
        self.assertEqual(lines(out), ["STEP #7 sync PR #8"])
        new = w.head(ref)
        self.assertEqual(w.git("rev-list", "--parents", "-n", "1", new).split(), [new, old, base])
        self.assertIn({"op": "push", "number": None, "ref": ref}, w.log())
        self.assertEqual(len(w.git("worktree", "list").splitlines()), 1)

        out = self.ticked(w, "--apply")
        self.assertEqual(lines(out), ["STEP #7 lock PR #8"])
        self.assertEqual(w.record("pulls", 8)["state"], "merged")
        self.assertEqual(w.record("issues", 7)["state"], "closed")
        self.assertEqual(w.ops("add_label"), [])

        # Criterion 10: the markers lint accepts every comment the ticks caused.
        comments = w.comments(8)
        self.assertEqual([c["role"] for c in comments], ["writer", "editor", "program:gate", "program:lock"])
        self.assertEqual(w.markers_lint(comments).status, lint.PASS)

    def refused(self, w, line):
        out = self.ticked(w, "--apply")
        self.assertEqual(lines(out), [line])
        self.assertEqual(w.ops("post"), [])
        self.assertEqual([(e["op"], e["number"], e["label"]) for e in w.log()], [("add_label", 7, LABEL)])
        self.assertIn(LABEL, w.record("issues", 7)["labels"])
        self.assertEqual(w.record("pulls", 8)["state"], "open")

    def test_a_gate_stop_for_this_head_is_a_need(self):
        w = self.world((7, 8))
        w.add(8, "2026-11-10T10:00:00Z", "program:gate", GATE_STOP)
        self.refused(w, "NEED #7 lock refused: rule 9 gate")

    def test_a_file_outside_writes_is_a_need(self):
        w = self.world((7, 8), edits={8: {"notes/stray.md": "Not the writer's.\n"}})
        self.refused(w, "NEED #7 lock refused: rule 7 writes")

    def test_hold_on_the_pull_is_a_need(self):
        w = self.world((7, 8))
        w.add(8, "2026-11-10T10:00:00Z", "program:gate", GATE_PASS)
        w.set_record("pulls", 8, labels=["hold"])
        w.write("notes/later.md", "The base moves.\n")
        w.commit("move the base")
        self.refused(w, "NEED #7 lock refused: rule 10 base")

    def test_a_conflict_with_the_base_moves_nothing(self):
        w = self.world((7, 8))
        w.add(8, "2026-11-10T10:00:00Z", "program:gate", GATE_PASS)
        w.write("episodes/ep01/final.md", "# Final of episode one\n\nThe ferry crosses at dawn.\n")
        w.commit("change the same line on the base")
        ref, head = w.ref(8), w.head(w.ref(8))
        self.refused(w, "NEED #7 conflict with base")
        self.assertEqual(w.head(ref), head)
        self.assertEqual(w.ops("push"), [])
        self.assertEqual(len(w.git("worktree", "list").splitlines()), 1)

    def test_a_push_that_fails_puts_the_branch_back(self):
        w = self.world((7, 8))
        w.add(8, "2026-11-10T10:00:00Z", "program:gate", GATE_PASS)
        w.write("notes/later.md", "The base moves.\n")
        w.commit("move the base")
        ref, head = w.ref(8), w.head(w.ref(8))

        class Refusing(github.Recorded):
            def push(self, root, ref):
                raise github.GitHubError(f"push {ref}: exit 1")

        with mock.patch.object(github, "connect", lambda settings, env=None: Refusing(w.github)):
            out = self.ticked(w, "--apply", code=1)
        self.assertEqual(lines(out), [f"FAIL #7 sync: push {ref}: exit 1"])
        self.assertEqual(w.head(ref), head)
        self.assertEqual(len(w.git("worktree", "list").splitlines()), 1)

    def test_without_apply_the_sync_is_printed_and_nothing_moves(self):
        w = self.world((7, 8))
        w.add(8, "2026-11-10T10:00:00Z", "program:gate", GATE_PASS)
        w.write("episodes/ep01/final.md", "# Final of episode one\n\nThe ferry crosses at dawn.\n")
        w.commit("change the same line on the base")
        before = w.snapshot()
        self.assertEqual(lines(self.ticked(w)), ["STEP #7 sync PR #8"])
        self.assertEqual(w.snapshot(), before)


class Rooms(Case):
    """Criterion 3: one lock per room per tick, and a lock moves the base for every room."""

    def merged(self, w):
        return sorted(int(p.stem) for p in (w.github / "pulls").glob("*.json")
                      if json.loads(p.read_text())["state"] == "merged")

    def test_two_lockable_pulls_in_one_room(self):
        w = self.world((9, 10, 11, 12))
        self.assertEqual(lines(self.ticked(w)),
                         ["STEP #9 lock PR #10", "WAIT #11 lockable (one lock per room per tick)"])
        self.assertEqual(lines(self.ticked(w, "--apply")), ["STEP #9 lock PR #10", "STEP #11 sync PR #12"])
        self.assertEqual(self.merged(w), [10])
        self.assertEqual(lines(self.ticked(w, "--apply")), ["STEP #11 lock PR #12"])
        self.assertEqual(self.merged(w), [10, 12])

    def test_one_lockable_pull_in_each_of_two_rooms(self):
        w = self.world((9, 10, 13, 14))
        self.assertEqual(lines(self.ticked(w, "--apply")), ["STEP #9 lock PR #10", "STEP #13 sync PR #14"])
        self.assertEqual(self.merged(w), [10])
        self.assertEqual(lines(self.ticked(w, "--apply")), ["STEP #13 lock PR #14"])
        self.assertEqual(self.merged(w), [10, 14])


class Pitches(Case):
    """Criterion 4: the lock of a pitched ticket waits for the selection."""

    def test_none_selected(self):
        w = self.world((15, 16, 17))
        out = self.ticked(w, "--apply")
        self.assertEqual(lines(out), [
            "WAIT #15 lockable PR #16 is not the ticket's next step",
            "WAIT #15 lockable PR #17 is not the ticket's next step",
            "STEP #15 launch keeper on #15"])
        out = self.ticked(w, "--apply")
        self.assertEqual(lines(out), [
            "WAIT #15 lockable PR #16 is not the ticket's next step",
            "WAIT #15 lockable PR #17 is not the ticket's next step",
            "NEED #15 selection every pitch of #15 is lockable and none is selected"])
        self.assertEqual([e["op"] for e in w.log()], ["post", "post", "add_label"])
        self.assertEqual({w.record("pulls", n)["state"] for n in (16, 17)}, {"open"})

    def test_only_the_selected_pitch_gets_a_lock_line(self):
        w = self.world((15, 16, 17))
        w.add(15, "2026-11-10T10:00:00Z", "keeper", "[KEEPER] SELECTED #17\n\nThe ferry sinks.\n")
        self.assertEqual(lines(self.ticked(w)), [
            "WAIT #15 lockable PR #16 is not the ticket's next step", "STEP #15 lock PR #17"])


class Idempotence(Case):
    """Criterion 5: a second tick repeats nothing of the first."""

    def test_a_launch_with_no_worker_waits(self):
        w = self.world((18,))
        p = w.p()
        launch.prepare(p, w.gh, p.root, "painter", 18)
        for _ in range(2):
            out = self.ticked(w, "--apply")
            self.assertEqual(lines(out), [f"WAIT #18 running painter launched at {NOW}"])
        self.assertEqual([e["body"].split("\n")[0] for e in w.ops("post")], ["[FOREMAN] LAUNCH painter 18-1"])

    def test_one_lint_comment(self):
        w = self.world((5, 6))
        self.assertEqual(lines(self.ticked(w, "--apply")), ["STEP #5 lint PR #6"])
        self.assertEqual(lines(self.ticked(w, "--apply")), ["STEP #5 launch editor on PR #6"])
        self.assertEqual([e["role"] for e in w.ops("post")].count("program:lint"), 1)


class Timeout(Case):
    """Criteria 6 and 10: a LAUNCH with no marker of its role, older than RUN_ATTEMPTS times
    RUN_TIMEOUT_MIN plus RUN_MARGIN_MIN."""

    def setUp(self):
        self.w = self.world((5, 6))
        p = self.w.p()
        launch.prepare(p, self.w.gh, p.root, "writer", 6)

    def at(self, minutes):
        os.environ["FACTORY_NOW"] = later(minutes)

    def test_timed_out_run_is_blocked(self):
        w = self.w
        self.at(251)
        self.assertEqual(lines(self.ticked(w, "--apply")), ["STEP #5 timeout writer 6-1"])
        posted = w.ops("post")[1:]
        self.assertEqual(len(posted), 1)
        [blocked] = [c for c in w.comments(6) if c["created_at"] == later(251)]
        self.assertEqual((blocked["role"], blocked["body"].split("\n")[0]), ("writer", "[WRITER] BLOCKED"))
        self.assertIn("Missing: a valid result (timed out)", blocked["body"].splitlines())
        self.assertEqual(lines(self.ticked(w, "--apply")), ["NEED #5 blocked the maker is BLOCKED on #6"])

        # Criterion 10: the markers lint accepts it, and refuses it under a program's role.
        comments = w.comments(6)
        self.assertEqual(w.markers_lint(comments).status, lint.PASS)
        seeded = [{**c, "role": "program:lint"} if c["id"] == blocked["id"] else c for c in comments]
        self.assertEqual(w.markers_lint(seeded).status, lint.FAIL)

    def test_before_the_timeout_it_waits(self):
        self.at(119)
        self.assertEqual(lines(self.ticked(self.w, "--apply")), [f"WAIT #5 running writer launched at {NOW}"])
        self.assertEqual(len(self.w.ops("post")), 1)

    def test_no_run_directory_fails(self):
        shutil.rmtree(self.w.state / "runs" / "writer-6-1")
        self.at(251)
        out = self.ticked(self.w, "--apply", code=1)
        self.assertEqual(out, "FAIL #5 timeout: no run writer-6-1\ntick: 0 steps, 0 need, 0 wait, 1 failed\n")
        self.assertEqual(len(self.w.ops("post")), 1)


class Wake(Case):
    """Criterion 7: the label is added once for a judgment and removed once it is answered."""

    def test_ruling(self):
        w = self.world((22, 23))
        self.assertEqual(lines(self.ticked(w, "--apply")), ["STEP #22 launch foreman on PR #23"])
        for _ in range(2):
            out = self.ticked(w, "--apply")
            self.assertEqual(lines(out), ["NEED #22 ruling three rounds of notes on #23 and no ruling"])
        self.assertEqual([e["number"] for e in w.ops("add_label")], [22])
        w.add(23, "2026-11-10T10:00:00Z", "foreman", "[FOREMAN] RULING\n\nThe sky may wait.\n")
        out = self.ticked(w, "--apply")
        self.assertEqual(lines(out), ["STEP #22 launch painter on PR #23"])
        self.assertEqual([e["number"] for e in w.ops("remove_label")], [22])
        self.assertNotIn(LABEL, w.record("issues", 22)["labels"])


class Failure(Case):
    """Criterion 8: a step that fails is a FAIL line; the tick goes on."""

    def test_post_refused_for_one_pull(self):
        w = self.world((1, 2, 3, 4, 5, 6))

        class Refusing(github.Recorded):
            def post(self, number, body, role):
                if number == 6:
                    raise github.GitHubError(f"post #{number}: exit 1")
                return super().post(number, body, role)

        with mock.patch.object(github, "connect", lambda settings, env=None: Refusing(w.github)):
            out = self.ticked(w, "--apply", code=1)
        self.assertEqual(out, "STEP #3 blind PR #4\nFAIL #5 lint: post #6: exit 1\n"
                              "tick: 1 steps, 0 need, 0 wait, 1 failed\n")
        self.assertTrue(blind.has_key(w.p(), 4, w.head(w.ref(4))))


class Whole(Case):
    """Criteria 3, 9 and 11 on every ticket at once: read only."""

    @classmethod
    def setUpClass(cls):
        cls.w = World(cls.addClassCleanup)

    def test_dry_run_is_byte_identical_and_writes_nothing(self):
        before = self.w.snapshot()
        first, second = self.w.tick(), self.w.tick()
        self.assertEqual(first, second)
        self.assertEqual(self.w.snapshot(), before)
        # Rooms in sorted order (production, story), tickets by number within a room.
        self.assertEqual(lines(first[1]), [
            "STEP #9 lock PR #10",
            "WAIT #11 lockable (one lock per room per tick)",
            "STEP #18 launch painter on #18",
            "STEP #19 launch inspector on PR #20",
            "WAIT #21 waiting upstream stage frames is not locked for ep05",
            "STEP #22 launch foreman on PR #23",
            "STEP #3 blind PR #4",
            "STEP #5 lint PR #6",
            "STEP #7 gate PR #8",
            "STEP #13 lock PR #14",
            "WAIT #15 lockable PR #16 is not the ticket's next step",
            "WAIT #15 lockable PR #17 is not the ticket's next step",
            "STEP #15 launch keeper on #15",
        ])
        self.assertEqual(first[1].splitlines()[-1], "tick: 9 steps, 0 need, 4 wait, 0 failed")

    def test_room(self):
        code, out, err = self.w.tick("--room", "production")
        self.assertEqual((code, err), (0, ""))
        numbers = {int(m) for m in re.findall(r"^\w+ #(\d+) ", out, re.M)}
        self.assertEqual(numbers, {9, 11, 18, 19, 21, 22})
        self.assertEqual(self.w.tick("--room", "nowhere"), (2, "", "tick: unknown room nowhere\n"))


class Frozen(Case):
    """Criterion 9: the freeze and the tick lock."""

    def test_frozen_reads_and_writes_nothing(self):
        w = self.world(EVERY + (24,))
        before = w.snapshot()
        for args in ((), ("--apply",)):
            self.assertEqual(w.tick(*args), (0, "FROZEN\n", ""))
            self.assertEqual(w.snapshot(), before)

    def test_tick_lock(self):
        w = self.world((5, 6))
        held = w.state / "tick.lock"
        held.mkdir(parents=True)
        before = w.snapshot()
        self.assertEqual(w.tick("--apply"), (0, "tick: another tick is running\n", ""))
        self.assertTrue(held.is_dir())
        self.assertEqual(w.snapshot(), before)
        stale = held.stat().st_mtime - 31 * 60
        os.utime(held, (stale, stale))
        self.assertEqual(lines(self.ticked(w, "--apply")), ["STEP #5 lint PR #6"])
        self.assertFalse(held.exists())


def failing_merge(w, fails):
    """``github.connect`` giving a recorded GitHub whose merge of pull ``n`` fails the next ``fails[n]`` times
    (every time when ``None``)."""

    class Failing(github.Recorded):
        def merge(self, root, number, head_sha):
            left = fails.get(number, 0)
            if left is None or left > 0:
                if left is not None:
                    fails[number] = left - 1
                raise github.GitHubError(f"merge #{number}: refused (exit 1)")
            return super().merge(root, number, head_sha)

    return mock.patch.object(github, "connect", lambda settings, env=None: Failing(w.github))


class VoidLock(Case):
    """ISSUE-77: a head whose merge failed is a NEED for the orchestrator, not a lock tried on every tick."""

    def void(self):
        """``World((7, 8))`` after ticks 1 to 3 with a merge of pull 8 that always fails, and its NEED line."""
        w = self.world((7, 8))
        with failing_merge(w, {8: None}):
            self.assertEqual(lines(self.ticked(w, "--apply")), ["STEP #7 gate PR #8"])
            self.assertEqual(lines(self.ticked(w, "--apply")), ["STEP #7 sync PR #8"])
            self.assertEqual(lines(self.ticked(w, "--apply", code=1)), ["FAIL #7 lock: merge failed"])
        return w, f"NEED #7 lock refused: merge failed at {w.head(w.ref(8))[:12]}"

    def lock_comment(self, w, number, role="program:lock"):
        w.add(number, "2026-11-10T10:00:00Z", role,
              f"[FOREMAN] LOCK\n\nlocked head {{head:{number}}} manifest none\nrelied on: none\n")

    def test_a_failing_merge_is_tried_once_then_asks_the_orchestrator(self):
        w, need = self.void()
        with failing_merge(w, {8: None}):
            for _ in range(2):
                self.assertEqual(lines(self.ticked(w, "--apply")), [need])
        found = [(c["role"], c["body"].split("\n")[0]) for c in w.comments(8)]
        self.assertEqual(found, [("writer", "[WRITER] READY"), ("editor", "[EDITOR] APPROVED"),
                                 ("program:gate", "[PRODUCER] GATE PASS"), ("program:lock", "[KEEPER] LOCK"),
                                 ("program:lock", cmd_lock.VOID)])
        self.assertEqual([(e["number"], e["label"]) for e in w.ops("add_label")], [(7, LABEL)])
        self.assertEqual((w.record("pulls", 8)["state"], w.record("issues", 7)["state"]), ("open", "open"))

    def test_without_apply_the_need_is_printed_and_nothing_is_written(self):
        w, need = self.void()
        before = w.snapshot()
        self.assertEqual(lines(self.ticked(w)), [need])
        self.assertEqual(w.snapshot(), before)

    def test_a_void_note_for_an_earlier_head_voids_nothing(self):
        w = self.world((9, 10, 11, 12))
        with failing_merge(w, {10: 1}):
            self.assertEqual(lines(self.ticked(w, "--apply", code=1)),
                             ["FAIL #9 lock: merge failed", "STEP #11 lock PR #12"])
            self.assertEqual(lines(self.ticked(w, "--apply")), ["STEP #9 sync PR #10"])
            self.assertEqual(lines(self.ticked(w, "--apply")), ["STEP #9 lock PR #10"])
        self.assertEqual(w.record("pulls", 10)["state"], "merged")

    def test_the_void_text_by_another_role_voids_nothing(self):
        for with_lock in (True, False):
            with self.subTest(with_lock=with_lock):
                w = self.world((9, 10))
                if with_lock:
                    self.lock_comment(w, 10)
                w.add(10, "2026-11-10T10:01:00Z", "foreman", cmd_lock.VOID + "\n")
                self.assertEqual(lines(self.ticked(w, "--apply")), ["STEP #9 lock PR #10"])
                self.assertEqual(w.record("pulls", 10)["state"], "merged")

    def test_a_void_pull_leaves_the_room_s_lock_to_the_next(self):
        w = self.world((9, 10, 11, 12))
        self.lock_comment(w, 10)
        w.add(10, "2026-11-10T10:01:00Z", "program:lock", cmd_lock.VOID + "\n")
        need = f"NEED #9 lock refused: merge failed at {w.head(w.ref(10))[:12]}"
        self.assertEqual(lines(self.ticked(w, "--apply")), [need, "STEP #11 lock PR #12"])
        self.assertEqual([w.record("pulls", n)["state"] for n in (10, 12)], ["open", "merged"])

    def test_the_orchestrator_s_retry_locks_the_void_head(self):
        w, _ = self.void()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = cmd_lock.main(["--apply", "--root", str(w.root)])
        self.assertEqual(code, 0)
        self.assertEqual((w.record("pulls", 8)["state"], w.record("issues", 7)["state"]), ("merged", "closed"))

    def test_the_markers_lint_passes_on_the_void_lock(self):
        w, need = self.void()
        with failing_merge(w, {8: None}):
            for _ in range(2):
                self.assertEqual(lines(self.ticked(w, "--apply")), [need])
        self.assertEqual(w.markers_lint(w.comments(8)).status, lint.PASS)


class DeadRun(Case):
    """ISSUE-81: a run is dead only after RUN_ATTEMPTS * RUN_TIMEOUT_MIN + RUN_MARGIN_MIN minutes (250 by
    default); the tick's BLOCKED for a checker's or judge's run removes its worktree as the supervisor does."""

    def at(self, minutes):
        os.environ["FACTORY_NOW"] = later(minutes)

    def maker(self):
        w = self.world((5, 6))
        p = w.p()
        return w, launch.prepare(p, w.gh, p.root, "writer", 6)

    def checker(self):
        w = self.world((19, 20))
        p = w.p()
        return w, launch.prepare(p, w.gh, p.root, "inspector", 20)

    def listed(self, w, path):
        return f"worktree {path}" in w.git("worktree", "list", "--porcelain").splitlines()

    def after_launch(self, w, number, role):
        """The comments by ``role`` on ``number`` after its newest LAUNCH."""
        found = []
        for c in sorted(w.comments(number), key=lambda c: (c["created_at"], c["id"])):
            if c["body"].startswith("[") and " LAUNCH " in c["body"].split("\n")[0]:
                found = []
            elif c["role"] == role:
                found.append(c)
        return found

    def blocked(self, w, number, role):
        """The first lines of the comments by ``role`` after the LAUNCH on ``number``."""
        return [c["body"].split("\n")[0] for c in self.after_launch(w, number, role)]

    # -- criteria 1, 2, 4, 6 and 11: the line

    def test_alive_past_the_setting_waits(self):
        w, _ = self.maker()
        for minutes in (121, 250):
            self.at(minutes)
            self.assertEqual(lines(self.ticked(w, "--apply")), [f"WAIT #5 running writer launched at {NOW}"])
        self.assertEqual(len(w.ops("post")), 1)
        self.assertNotIn("[WRITER] BLOCKED", self.blocked(w, 6, "writer"))

    def test_dead_past_the_line_is_blocked_once(self):
        w, run = self.maker()
        self.at(251)
        self.assertEqual(lines(self.ticked(w, "--apply")), ["STEP #5 timeout writer 6-1"])
        [blocked] = self.after_launch(w, 6, "writer")
        self.assertEqual(blocked["body"].split("\n")[0], "[WRITER] BLOCKED")
        self.assertIn("Missing: a valid result (timed out)", blocked["body"].splitlines())
        self.assertEqual(lines(self.ticked(w, "--apply")), ["NEED #5 blocked the maker is BLOCKED on #6"])
        self.assertEqual(len(w.ops("post")), 2)
        # Criterion 6: a maker's worktree is kept.
        self.assertEqual(run.worktree, w.state / "worktrees" / "writer-6-1")
        self.assertTrue(run.worktree.is_dir())
        self.assertTrue(self.listed(w, run.worktree))

    def test_the_setting_scales_the_line(self):
        w, _ = self.maker()
        with open(w.root / "production.local.env", "a") as handle:
            handle.write("RUN_TIMEOUT_MIN=30\n")
        for minutes in (61, 70):
            self.at(minutes)
            self.assertEqual(lines(self.ticked(w, "--apply")), [f"WAIT #5 running writer launched at {NOW}"])
        self.at(71)
        self.assertEqual(lines(self.ticked(w, "--apply")), ["STEP #5 timeout writer 6-1"])

    def test_the_line_follows_the_attempts(self):
        w, _ = self.maker()
        with mock.patch.object(steps, "RUN_ATTEMPTS", 1):
            self.at(130)
            self.assertEqual(lines(self.ticked(w)), [f"WAIT #5 running writer launched at {NOW}"])
            self.at(131)
            self.assertEqual(lines(self.ticked(w)), ["STEP #5 timeout writer 6-1"])

    # -- criteria 5, 7, 8, 9 and 10: a checker's worktree

    def test_checker_worktree_is_removed(self):
        w, run = self.checker()
        self.assertTrue(self.listed(w, run.worktree))
        self.at(251)
        self.assertEqual(lines(self.ticked(w, "--apply")), ["STEP #19 timeout inspector 20-1"])
        self.assertEqual(self.blocked(w, 20, "inspector"), ["[INSPECTOR] BLOCKED"])
        self.assertFalse(run.worktree.exists())
        self.assertFalse(self.listed(w, run.worktree))
        self.assertFalse((run.dir / "supervisor.log").exists())

    def test_removal_that_fails_is_logged(self):
        w, run = self.checker()
        self.at(251)
        seeded = gitstate.GitError("worktree", 128, "seeded")
        with mock.patch.object(gitstate, "remove_worktree", side_effect=seeded):
            self.assertEqual(lines(self.ticked(w, "--apply")), ["STEP #19 timeout inspector 20-1"])
        self.assertEqual(self.blocked(w, 20, "inspector"), ["[INSPECTOR] BLOCKED"])
        self.assertTrue(run.worktree.is_dir())
        self.assertEqual((run.dir / "supervisor.log").read_text(),
                         "launch 20-1: worktree not removed: git worktree: exit 128\n")

    def test_worktree_already_removed_through_git(self):
        w, run = self.checker()
        gitstate.remove_worktree(w.root, run.worktree)
        self.at(251)
        self.assertEqual(lines(self.ticked(w, "--apply")), ["STEP #19 timeout inspector 20-1"])
        self.assertEqual((run.dir / "supervisor.log").read_text(),
                         "launch 20-1: worktree not removed: git worktree: exit 128\n")

    def test_worktree_directory_deleted_by_hand(self):
        w, run = self.checker()
        shutil.rmtree(run.worktree)
        self.assertTrue(self.listed(w, run.worktree))
        self.at(251)
        self.assertEqual(lines(self.ticked(w, "--apply")), ["STEP #19 timeout inspector 20-1"])
        self.assertFalse(self.listed(w, run.worktree))
        self.assertFalse((run.dir / "supervisor.log").exists())

    def test_dry_run_writes_nothing(self):
        w, run = self.checker()
        self.at(251)
        before = w.snapshot()
        self.assertEqual(lines(self.ticked(w)), ["STEP #19 timeout inspector 20-1"])
        self.assertEqual(w.snapshot(), before)
        self.assertTrue(run.worktree.is_dir())
        self.assertTrue(self.listed(w, run.worktree))

    def test_post_refused_keeps_the_worktree_for_the_next_tick(self):
        w, run = self.checker()
        self.at(251)

        class Refusing(github.Recorded):
            def post(self, number, body, role):
                if number == 20:
                    raise github.GitHubError(f"post #{number}: exit 1")
                return super().post(number, body, role)

        with mock.patch.object(github, "connect", lambda settings, env=None: Refusing(w.github)):
            out = self.ticked(w, "--apply", code=1)
        self.assertEqual(lines(out), ["FAIL #19 timeout: post #20: exit 1"])
        self.assertTrue(run.worktree.is_dir())
        self.assertEqual(lines(self.ticked(w, "--apply")), ["STEP #19 timeout inspector 20-1"])
        self.assertFalse(run.worktree.exists())
        self.assertFalse(self.listed(w, run.worktree))



class CountingSync(github.Recorded):
    """A recorded GitHub whose ``sync_base`` records each base it is called with, and fails from call
    ``fail_from`` on (never when ``None``)."""

    def __init__(self, directory, fail_from=None):
        super().__init__(directory)
        self.fail_from = fail_from
        self.synced = []

    def sync_base(self, root, base_ref):
        self.synced.append(base_ref)
        if self.fail_from is not None and len(self.synced) >= self.fail_from:
            raise github.GitHubError(f"sync_base {base_ref}: git fetch failed (exit 128)")
        return super().sync_base(root, base_ref)


class SyncBase(Case):
    """ISSUE-227: a tick with ``--apply`` syncs the local base first; a sync that fails after a merge ends it."""

    KEEP = (9, 10, 11, 12, 13, 14)

    def tick_with(self, w, gh, *args):
        with mock.patch.object(github, "connect", lambda settings, env=None: gh):
            return w.tick(*args)

    def test_a_sync_that_fails_after_a_lock_ends_the_tick(self):
        w = self.world(self.KEEP)
        gh = CountingSync(w.github, fail_from=2)
        code, out, err = self.tick_with(w, gh, "--apply")
        trunk = w.head("trunk")[:12]
        self.assertEqual((code, err), (1, ""))
        self.assertEqual(out, f"FAIL - PR #10 is merged at {trunk}, base not synced: "
                              "sync_base trunk: git fetch failed (exit 128)\n"
                              "tick: 0 steps, 0 need, 0 wait, 1 failed\n")
        self.assertEqual([w.record("pulls", n)["state"] for n in (10, 12, 14)], ["merged", "open", "open"])
        self.assertEqual(w.record("issues", 9)["state"], "closed")
        self.assertFalse([c for c in w.comments(10) if c["body"].rstrip() == cmd_lock.VOID])
        self.assertEqual(gh.synced, ["trunk", "trunk"])

    def test_a_tick_whose_syncs_succeed(self):
        w = self.world(self.KEEP)
        gh = CountingSync(w.github)
        found, out, err = self.tick_with(w, gh, "--apply")
        self.assertEqual((found, err), (0, ""))
        self.assertRegex(out, LAST)
        self.assertEqual(lines(out), ["STEP #9 lock PR #10", "STEP #11 sync PR #12", "STEP #13 sync PR #14"])
        self.assertEqual(gh.synced, ["trunk", "trunk"])

    def test_a_first_sync_that_fails_writes_nothing(self):
        w = self.world(self.KEEP)
        gh = CountingSync(w.github, fail_from=1)
        before = w.snapshot()
        self.assertEqual(self.tick_with(w, gh, "--apply"),
                         (2, "", "tick: sync_base trunk: git fetch failed (exit 128)\n"))
        self.assertEqual(w.snapshot(), before)
        self.assertEqual(gh.synced, ["trunk"])

    def test_without_apply_nothing_is_synced(self):
        w = self.world(self.KEEP)
        gh = CountingSync(w.github, fail_from=1)
        before = w.snapshot()
        found, out, err = self.tick_with(w, gh)
        self.assertEqual((found, err), (0, ""))
        self.assertEqual(lines(out), ["STEP #9 lock PR #10", "WAIT #11 lockable (one lock per room per tick)",
                                      "STEP #13 lock PR #14"])
        self.assertEqual(w.snapshot(), before)
        self.assertEqual(gh.synced, [])


FOREMAN_RULING = step("You are the foreman", {
    "marker": "RULING", "summary": "The sky may wait for the next episode.",
    "evidence": [{"criterion": "inspector B1", "result": "rejected", "command": "production/ep06/frames.md:1"}]})
PITCHES_WAIT = ["WAIT #15 lockable PR #16 is not the ticket's next step",
                "WAIT #15 lockable PR #17 is not the ticket's next step"]


def keeper_selected(number):
    return step("You are the keeper", {
        "marker": "SELECTED", "summary": "The second pitch sinks the ferry.", "selected": number,
        "evidence": [{"criterion": f"PR #{n}", "result": "selected" if n == number else "approved",
                      "command": "episodes/ep01/pitch.md"} for n in (16, 17)]})


def firsts(comments):
    return [(c["role"], c["body"].split("\n")[0]) for c in comments]


class Judgment(Case):
    """ISSUE-89 behaviours 12 to 14: tick starts the room's orchestrator for a ruling and a selection."""

    def at(self, minutes):
        os.environ["FACTORY_NOW"] = later(minutes)

    # -- behaviour 12: a ruling through tick

    def test_ruling_through_tick(self):
        w = self.world((22, 23))
        w.steps(FOREMAN_RULING)
        self.assertEqual(lines(self.ticked(w, "--apply")), ["STEP #22 launch foreman on PR #23"])
        self.assertEqual(firsts(w.comments(23))[4:], [("foreman", "[FOREMAN] LAUNCH foreman 23-1"),
                                                      ("foreman", "[FOREMAN] RULING")])
        self.assertEqual(lines(self.ticked(w, "--apply")), ["STEP #22 launch painter on PR #23"])
        self.assertEqual(w.ops("add_label"), [])

    def test_ruling_with_no_answer(self):
        w = self.world((22, 23))
        self.assertEqual(lines(self.ticked(w, "--apply")), ["STEP #22 launch foreman on PR #23"])
        self.assertEqual(firsts(w.comments(23))[4:], [("foreman", "[FOREMAN] LAUNCH foreman 23-1"),
                                                      ("foreman", "[FOREMAN] BLOCKED")])
        for _ in range(2):
            self.assertEqual(lines(self.ticked(w, "--apply")),
                             ["NEED #22 ruling three rounds of notes on #23 and no ruling"])
        self.assertEqual([(e["number"], e["label"]) for e in w.ops("add_label")], [(22, LABEL)])
        self.assertEqual([b for _, b in firsts(w.comments(23)) if " LAUNCH foreman " in b],
                         ["[FOREMAN] LAUNCH foreman 23-1"])

    # -- behaviour 13: a selection through tick

    def test_selection_through_tick(self):
        w = self.world((15, 16, 17))
        w.steps(keeper_selected(17))
        self.assertEqual(lines(self.ticked(w, "--apply")), PITCHES_WAIT + ["STEP #15 launch keeper on #15"])
        self.assertEqual(firsts(w.comments(15)), [("keeper", "[KEEPER] LAUNCH keeper 15-1"),
                                                  ("keeper", "[KEEPER] SELECTED #17")])
        self.assertEqual(lines(self.ticked(w, "--apply")), PITCHES_WAIT[:1] + ["STEP #15 lock PR #17"])
        self.assertEqual(w.ops("add_label"), [])
        self.assertEqual([w.record("pulls", n)["state"] for n in (16, 17)], ["open", "merged"])

    def test_selection_outside_the_pitches(self):
        w = self.world((15, 16, 17))
        w.steps(keeper_selected(99))
        self.assertEqual(lines(self.ticked(w, "--apply")), PITCHES_WAIT + ["STEP #15 launch keeper on #15"])
        found = w.comments(15)
        self.assertEqual(firsts(found), [("keeper", "[KEEPER] LAUNCH keeper 15-1"), ("keeper", "[KEEPER] BLOCKED")])
        self.assertIn("Missing: a valid result (result.selected: #99 is not a pitch of this launch)",
                      found[1]["body"].splitlines())
        self.assertEqual(lines(self.ticked(w, "--apply")), PITCHES_WAIT + [
            "NEED #15 selection every pitch of #15 is lockable and none is selected"])
        self.assertEqual([(e["number"], e["label"]) for e in w.ops("add_label")], [(15, LABEL)])
        self.assertIn(LABEL, w.record("issues", 15)["labels"])
        self.assertEqual(w.ops("merge"), [])
        self.assertEqual([w.record("pulls", n)["state"] for n in (16, 17)], ["open", "open"])

    # -- behaviour 14: a dead selection run

    def selection_run(self):
        w = self.world((15, 16, 17))
        p = w.p()
        run = launch.prepare(p, w.gh, p.root, "keeper", 15)
        self.assertTrue(run.dir.is_dir())
        return w, run

    def test_dead_selection_run(self):
        w, run = self.selection_run()
        self.at(251)
        self.assertEqual(lines(self.ticked(w, "--apply")), PITCHES_WAIT + ["STEP #15 timeout keeper 15-1"])
        found = w.comments(15)
        self.assertEqual(firsts(found), [("keeper", "[KEEPER] LAUNCH keeper 15-1"), ("keeper", "[KEEPER] BLOCKED")])
        self.assertEqual(found[1]["created_at"], later(251))
        body = found[1]["body"].splitlines()
        self.assertIn("Missing: a valid result (timed out)", body)
        self.assertIn("Resume from: ticket #15", body)
        self.assertEqual(len(w.ops("post")), 2)

    def test_live_selection_run(self):
        w, _ = self.selection_run()
        self.at(1)
        before = w.snapshot()
        self.assertEqual(lines(self.ticked(w, "--apply")),
                         PITCHES_WAIT + [f"WAIT #15 running keeper launched at {NOW}"])
        self.assertEqual(w.snapshot(), before)
        self.assertEqual(len(w.ops("post")), 1)


if __name__ == "__main__":
    unittest.main()
