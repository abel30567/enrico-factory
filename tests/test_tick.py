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
            "NEED #15 selection every pitch of #15 is lockable and none is selected"])
        self.assertEqual([e["op"] for e in w.log()], ["add_label"])
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
    """Criteria 6 and 10: a LAUNCH older than RUN_TIMEOUT_MIN with no marker of its role."""

    def setUp(self):
        self.w = self.world((5, 6))
        p = self.w.p()
        launch.prepare(p, self.w.gh, p.root, "writer", 6)

    def at(self, minutes):
        os.environ["FACTORY_NOW"] = later(minutes)

    def test_timed_out_run_is_blocked(self):
        w = self.w
        self.at(121)
        self.assertEqual(lines(self.ticked(w, "--apply")), ["STEP #5 timeout writer 6-1"])
        posted = w.ops("post")[1:]
        self.assertEqual(len(posted), 1)
        [blocked] = [c for c in w.comments(6) if c["created_at"] == later(121)]
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
        self.at(121)
        out = self.ticked(self.w, "--apply", code=1)
        self.assertEqual(out, "FAIL #5 timeout: no run writer-6-1\ntick: 0 steps, 0 need, 0 wait, 1 failed\n")
        self.assertEqual(len(self.w.ops("post")), 1)


class Wake(Case):
    """Criterion 7: the label is added once for a judgment and removed once it is answered."""

    def test_ruling(self):
        w = self.world((22, 23))
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
            "NEED #22 ruling three rounds of notes on #23 and no ruling",
            "STEP #3 blind PR #4",
            "STEP #5 lint PR #6",
            "STEP #7 gate PR #8",
            "STEP #13 lock PR #14",
            "WAIT #15 lockable PR #16 is not the ticket's next step",
            "WAIT #15 lockable PR #17 is not the ticket's next step",
            "NEED #15 selection every pitch of #15 is lockable and none is selected",
        ])
        self.assertEqual(first[1].splitlines()[-1], "tick: 7 steps, 2 need, 4 wait, 0 failed")

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


if __name__ == "__main__":
    unittest.main()
