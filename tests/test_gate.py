"""Tests for kit/lib/cmd_gate.py: `factory gate` on a lock and on a job, end to end.

The production is tests/fixtures/gate/production, made into a git repository on branch `trunk` in a
temporary directory, with one branch per recorded pull. GitHub is a copy of tests/fixtures/gate/github.
A comment body's ``{head:N}`` stands for the head commit of pull N's branch, filled in when it is copied.
Every test sets ``FACTORY_GITHUB`` and ``FACTORY_NOW``: nothing reaches the network or ``gh``.
"""

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import cmd_gate, gitstate, github, ledger, lint, production, tickets  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "gate"
HEAD = re.compile(r"\{head:(\d+)\}")
NOW = "2026-11-02T09:00:00Z"
LOCK_PULL, JOB_TICKET, JOB_PULL = 2, 3, 4
CLEAN_JOB = "jobs/render-ep01.json"
LINE = re.compile(r"^gate (job|lock) ([0-9a-f]{64}): (PASS|STOP) cap (\S+) reason (.+)$")


class World:
    """A production repository on branch `trunk` and a recorded GitHub directory, both temporary."""

    def __init__(self, test):
        self.test = test
        tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, tmp)
        self.root = tmp / "show"
        self.dir = tmp / "github"
        patch = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                                             "FACTORY_GITHUB": str(self.dir), "FACTORY_NOW": NOW})
        patch.start()
        test.addCleanup(patch.stop)
        shutil.copytree(FIX / "production", self.root)
        self.git("init", "-q", "-b", "trunk")
        self.commit("the production")
        shutil.copytree(FIX / "github", self.dir)
        for path in sorted((self.dir / "pulls").glob("*.json")):
            self.branch(json.loads(path.read_text())["head_ref"])
        for path in sorted((self.dir / "comments").glob("*.json")):
            path.write_text(self.heads(path.read_text()))
        self.gh = github.Recorded(self.dir)

    def git(self, *args):
        return gitstate.git(self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", *args)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def branch(self, ref):
        """A branch `ref` with one commit on top of `trunk`."""
        self.git("checkout", "-q", "-b", ref, "trunk")
        self.write("work/" + ref.replace("/", "-") + ".md", ref + "\n")
        self.commit("work on " + ref)
        self.git("checkout", "-q", "trunk")

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def heads(self, text):
        def head(m):
            return gitstate.head(self.root, self.pull_record(int(m.group(1)))["head_ref"])
        return HEAD.sub(head, text)

    def pull_record(self, number):
        return json.loads((self.dir / "pulls" / f"{number}.json").read_text())

    def record(self, number, comment):
        """Add one recorded comment, as GitHub would hold it."""
        path = self.dir / "comments" / f"{number}.json"
        found = json.loads(path.read_text()) if path.is_file() else []
        path.write_text(json.dumps(found + [{**comment, "body": self.heads(comment["body"])}], indent=1) + "\n")

    def comments(self, number):
        return self.gh.comments(number)

    def gates(self, number):
        return [c for c in self.comments(number) if c["role"] == "program:gate"]

    def ledger_rows(self):
        text = gitstate.show(self.root, "trunk", ledger.PATH)
        return [] if text is None else ledger.read(text)

    def log(self):
        path = self.dir / "log.jsonl"
        return [json.loads(l) for l in path.read_text().splitlines()] if path.is_file() else []

    def gate(self, target, apply=True):
        """``(exit, stdout, stderr)`` of ``factory gate`` run in-process."""
        out, err = io.StringIO(), io.StringIO()
        argv = [target, "--root", str(self.root)] + (["--apply"] if apply else [])
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_gate.main(argv)
        return code, out.getvalue(), err.getvalue()

    def job(self, name="job.json", **changes):
        """A copy of the clean job file with ``changes``, written untracked; its absolute path."""
        job = json.loads((self.root / CLEAN_JOB).read_text())
        job.update(changes)
        return str(self.write("jobs/" + name, json.dumps(job, indent=1)))

    def state(self):
        return tickets.load(production.load(self.root), self.gh, self.root)

    def covers(self):
        p = production.load(self.root)
        pull = self.state().pulls[LOCK_PULL]
        return ledger.covers(pull["git"], pull["comments"], production.stage(p, "production", "frames"))

    def set_env(self, name, value):
        path = self.root / "production.env"
        lines = [l for l in path.read_text().splitlines() if not l.startswith(name + "=")]
        path.write_text("\n".join(lines + [f"{name}={value}"]) + "\n")

    def origin(self, behind):
        """A bare `origin` whose `trunk` has one more commit, adding a ledger row; with ``behind`` the local
        `trunk` stays at the commit before it. ``(origin's row line, the local trunk's tip)``."""
        remote = self.root.parent / "origin.git"
        gitstate.git(self.root.parent, "init", "-q", "--bare", str(remote))
        self.git("remote", "add", "origin", str(remote))
        start = gitstate.head(self.root, "trunk")
        row = ledger.line({"time": "2026-11-02T08:00:00Z", "kind": "job", "hash": "e" * 64, "ticket": JOB_TICKET,
                           "episode": "ep01", "tool": "renderbox", "usd": "1", "compute_min": "5",
                           "caller": "renderer", "verdict": "PASS", "cap": "-"})
        self.write(ledger.PATH, ledger.HEADER + "\n" + row + "\n")
        self.commit("ledger: a row from another clone")
        self.git("push", "-q", "origin", "trunk")
        if behind:
            self.git("reset", "-q", "--hard", start)
        return row, gitstate.head(self.root, "trunk")


class LockGateTest(unittest.TestCase):
    def setUp(self):
        self.w = World(self)

    def test_apply_then_base_merge_then_new_work(self):
        before = gitstate.head(self.w.root, "trunk")
        head = gitstate.head(self.w.root, "feat/FR-1-ep01")
        code, out, err = self.w.gate(str(LOCK_PULL))
        self.assertEqual((code, err), (0, ""))
        m = LINE.match(out.rstrip("\n"))
        self.assertIsNotNone(m, out)
        self.assertEqual(m.groups()[0:1] + m.groups()[2:], ("lock", "PASS", "-", "within caps"))
        # One ledger line, committed on the base by `factory`, and pushed.
        [row] = self.w.ledger_rows()
        self.assertEqual({k: row[k] for k in ("kind", "hash", "ticket", "episode", "tool", "usd", "compute_min",
                                               "caller", "verdict", "cap", "time")},
                         {"kind": "lock", "hash": m.group(2), "ticket": "1", "episode": "ep01", "tool": "-",
                          "usd": "0", "compute_min": "0", "caller": "lead", "verdict": "PASS", "cap": "-",
                          "time": NOW})
        self.assertEqual(self.w.git("log", "-1", "--format=%an %P", "trunk").split(), ["factory", before])
        self.assertEqual(self.w.git("diff", "--name-only", before, "trunk").split(), [ledger.PATH])
        self.assertIn({"op": "push", "number": None, "ref": "trunk"}, self.w.log())
        # One gate comment on the pull, ending with the reviewed head line.
        [gate] = self.w.gates(LOCK_PULL)
        lines = gate["body"].rstrip("\n").split("\n")
        self.assertEqual(lines, ["[PRODUCER] GATE PASS", f"covers: lock {m.group(2)}", "cap: -  reason: within caps",
                                 "ledger: " + ledger.line(row).replace("\t", " | "),
                                 f"reviewed head {head} manifest none"])
        self.assertEqual(m.group(2), hashlib.sha256(
            f"lock\n{LOCK_PULL}\n{head}\nnone\n".encode()).hexdigest())
        self.assertTrue(self.w.covers())

        # The base merged into the branch: still covered, and the gate repeats nothing.
        self.w.git("checkout", "-q", "feat/FR-1-ep01")
        self.w.git("merge", "-q", "--no-ff", "--no-edit", "trunk")
        self.w.git("checkout", "-q", "trunk")
        self.assertNotEqual(gitstate.head(self.w.root, "feat/FR-1-ep01"), head)
        self.assertTrue(self.w.covers())
        code, again, err = self.w.gate(str(LOCK_PULL))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(again, out.rstrip("\n") + " (already recorded)\n")
        self.assertEqual(len(self.w.ledger_rows()), 1)
        self.assertEqual(len(self.w.gates(LOCK_PULL)), 1)

        # A new maker commit and a recorded ADDRESSED: no longer covered.
        self.w.git("checkout", "-q", "feat/FR-1-ep01")
        self.w.write("frames/ep01/shot-2.md", "A second lantern.\n")
        self.w.commit("another frame")
        self.w.git("checkout", "-q", "trunk")
        self.w.record(LOCK_PULL, {"id": 50, "created_at": "2026-11-02T10:00:00Z", "role": "painter",
                                  "body": "[PAINTER] ADDRESSED\nadded a frame\n"})
        self.assertFalse(self.w.covers())

    def test_twice_on_the_same_head(self):
        first = self.w.gate(str(LOCK_PULL))
        second = self.w.gate(str(LOCK_PULL))
        self.assertEqual(first[0], 0)
        self.assertEqual(second, (0, first[1].rstrip("\n") + " (already recorded)\n", ""))
        self.assertEqual(len(self.w.ledger_rows()), 1)
        self.assertEqual(len(self.w.gates(LOCK_PULL)), 1)
        self.assertEqual([e["op"] for e in self.w.log()], ["push", "post"])

    def test_without_apply_writes_nothing(self):
        before = gitstate.head(self.w.root, "trunk")
        comments = self.w.comments(LOCK_PULL)
        dry = self.w.gate(str(LOCK_PULL), apply=False)
        self.assertEqual(dry[0], 0)
        self.assertEqual(gitstate.head(self.w.root, "trunk"), before)
        self.assertEqual(self.w.comments(LOCK_PULL), comments)
        self.assertFalse((self.w.root / ledger.PATH).exists())
        self.assertEqual(gitstate.status(self.w.root), [])
        self.assertEqual(self.w.log(), [])
        self.assertEqual(self.w.gate(str(LOCK_PULL)), dry)

    def test_gate_pass_from_a_maker_role_does_not_cover(self):
        body = ("[PRODUCER] GATE PASS\ncovers: lock " + "ab" * 32 + "\ncap: -  reason: within caps\n"
                "ledger: x\nreviewed head {head:2} manifest none\n")
        self.w.record(LOCK_PULL, {"id": 60, "created_at": "2026-11-02T08:00:00Z", "role": "painter", "body": body})
        self.assertFalse(self.w.covers())
        self.w.record(LOCK_PULL, {"id": 61, "created_at": "2026-11-02T08:00:00Z", "role": "program:gate",
                                  "body": body})
        self.assertTrue(self.w.covers())

    def test_lock_stop_ends_with_the_reviewed_head(self):
        self.w.set_env("CAP_EPISODE_HOURS", "1")  # ep01 began at 2026-11-01T09:00:00Z
        head = gitstate.head(self.w.root, "feat/FR-1-ep01")
        code, out, err = self.w.gate(str(LOCK_PULL))
        self.assertEqual((code, err), (1, ""))
        self.assertRegex(out, r"^gate lock [0-9a-f]{64}: STOP cap CAP_EPISODE_HOURS reason over cap\n$")
        [gate] = self.w.gates(LOCK_PULL)
        lines = gate["body"].rstrip("\n").split("\n")
        self.assertEqual((lines[0], lines[-1]), ("[PRODUCER] GATE STOP", f"reviewed head {head} manifest none"))
        self.assertFalse(self.w.covers())
        self.assertEqual(self.w.gate(str(LOCK_PULL)), (1, out.rstrip("\n") + " (already recorded)\n", ""))

    def test_cap_that_is_not_a_number(self):
        self.w.set_env("CAP_SEASON_USD", "plenty")
        self.assertEqual(self.w.gate(str(LOCK_PULL)), (2, "", "gate: setting CAP_SEASON_USD is not a number >= 0\n"))
        self.assertEqual(self.w.log(), [])

    def test_refused_pull(self):
        for target in ("99", "4x"):
            with self.subTest(target=target):
                code, out, err = self.w.gate(target)
                self.assertEqual((code, out), (2, ""))
                self.assertRegex(err, r"^gate: ")
        code, out, err = self.w.gate("99")
        self.assertEqual(err, "gate: #99 is not an open pull\n")
        self.assertEqual(self.w.ledger_rows(), [])
        self.assertEqual(self.w.log(), [])


class FailingPost(github.Recorded):
    def post(self, number, body, role):
        raise github.GitHubError(f"post #{number}: exit 1")


class FlakyPush(github.Recorded):
    """``Recorded`` whose first ``fails`` pushes raise; with ``remote`` a push also runs `git push origin`."""

    def __init__(self, directory, fails=1, remote=False):
        super().__init__(directory)
        self.fails, self.remote = fails, remote

    def push(self, root, ref):
        if self.fails:
            self.fails -= 1
            raise github.GitHubError("push: exit 1")
        if self.remote:
            gitstate.git(root, "push", "-q", "origin", ref)
        super().push(root, ref)


class PushRetryTest(unittest.TestCase):
    """A run whose push failed left the ledger line committed on the local base only: a rerun pushes it."""

    def setUp(self):
        self.w = World(self)
        self.target = str(self.w.root / CLEAN_JOB)

    def run_gate(self, gh, apply=True):
        with mock.patch.object(cmd_gate.github, "connect", return_value=gh):
            return self.w.gate(self.target, apply)

    def rows_of(self, digest):
        return [r for r in self.w.ledger_rows() if r["hash"] == digest]

    def half_done(self, gh):
        """The first run, its push failing: the line committed on the base, nothing posted; the row."""
        code, out, err = self.run_gate(gh)
        self.assertEqual((code, out, err), (2, "", "gate: push: exit 1\n"))
        [row] = self.w.ledger_rows()
        self.assertEqual(self.w.git("log", "-1", "--format=%an", "trunk").strip(), "factory")
        self.assertEqual(self.w.git("show", "--name-only", "--format=", "trunk").split(), [ledger.PATH])
        self.assertNotIn("post", [e["op"] for e in self.w.log()])
        self.assertEqual(self.w.gates(JOB_TICKET), [])
        return row

    def assert_posted_once(self, row, before):
        """The log gained exactly ``push`` then ``post``, the comment quotes ``row``, one row has its hash."""
        log = self.w.log()
        self.assertEqual(log[:len(before)], before)
        self.assertEqual([e["op"] for e in log[len(before):]], ["push", "post"])
        [gate] = self.w.gates(JOB_TICKET)
        self.assertIn("ledger: " + ledger.line(row).replace("\t", " | ") + "\n", gate["body"])
        self.assertEqual(self.rows_of(row["hash"]), [row])

    def test_failed_push_then_rerun_pushes_then_posts(self):
        row = self.half_done(FlakyPush(self.w.dir, fails=1))
        before = self.w.log()
        code, out, err = self.run_gate(FlakyPush(self.w.dir, fails=0))
        self.assertEqual((code, out, err), (0, f"gate job {row['hash']}: PASS cap - reason within caps\n", ""))
        self.assert_posted_once(row, before)

    def test_push_fails_on_the_rerun_too(self):
        gh = FlakyPush(self.w.dir, fails=2)
        row = self.half_done(gh)
        self.assertEqual(self.run_gate(gh), (2, "", "gate: push: exit 1\n"))
        self.assertNotIn("post", [e["op"] for e in self.w.log()])
        self.assertEqual(self.rows_of(row["hash"]), [row])
        self.assertEqual(self.w.gates(JOB_TICKET), [])
        before = self.w.log()
        self.assertEqual(self.run_gate(gh)[0], 0)
        self.assert_posted_once(row, before)

    def test_clean_twin_does_not_push_again(self):
        code, out, err = self.run_gate(FlakyPush(self.w.dir, fails=0))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual([e["op"] for e in self.w.log()], ["push", "post"])
        before = self.w.log()
        self.assertEqual(self.run_gate(FlakyPush(self.w.dir, fails=0)),
                         (0, out.rstrip("\n") + " (already recorded)\n", ""))
        self.assertEqual(self.w.log(), before)
        self.assertEqual(len(self.w.ledger_rows()), 1)

    def test_without_apply_on_the_half_done_state(self):
        self.half_done(FlakyPush(self.w.dir, fails=1))
        log, refs = self.w.log(), self.w.git("for-each-ref", "--format=%(refname) %(objectname)")
        tree = (gitstate.status(self.w.root), (self.w.root / ledger.PATH).read_text())
        code, out, err = self.run_gate(FlakyPush(self.w.dir, fails=0), apply=False)
        self.assertEqual((code, err), (0, ""))
        self.assertRegex(out, r"^gate job [0-9a-f]{64}: PASS cap - reason within caps\n$")
        self.assertEqual(self.w.log(), log)
        self.assertEqual(self.w.git("for-each-ref", "--format=%(refname) %(objectname)"), refs)
        self.assertEqual((gitstate.status(self.w.root), (self.w.root / ledger.PATH).read_text()), tree)
        self.assertEqual(self.w.gates(JOB_TICKET), [])

    def test_rerun_reaches_a_real_remote(self):
        remote = self.w.root.parent / "remote.git"
        gitstate.git(self.w.root.parent, "init", "-q", "--bare", str(remote))
        self.w.git("remote", "add", "origin", str(remote))
        self.w.git("push", "-q", "origin", "trunk")
        start = gitstate.head(self.w.root, "trunk")
        gh = FlakyPush(self.w.dir, fails=1, remote=True)
        self.half_done(gh)
        self.assertNotEqual(gitstate.head(self.w.root, "trunk"), start)
        self.assertEqual(gitstate.head(self.w.root, "origin/trunk"), start)
        self.assertEqual(self.run_gate(gh)[0], 0)
        self.assertEqual(self.w.git("rev-parse", "origin/trunk").strip(), gitstate.head(self.w.root, "trunk"))
        self.assertEqual(gitstate.head(remote, "trunk"), gitstate.head(self.w.root, "trunk"))


class JobGateTest(unittest.TestCase):
    def setUp(self):
        self.w = World(self)

    def assert_refused(self, target, message):
        code, out, err = self.w.gate(target)
        self.assertEqual((code, out, err), (2, "", f"gate: {message}\n"))
        self.assertEqual(self.w.ledger_rows(), [])
        self.assertFalse((self.w.root / ledger.PATH).exists())
        self.assertEqual(self.w.gates(JOB_TICKET), [])
        self.assertEqual(self.w.log(), [])

    def test_clean_job(self):
        code, out, err = self.w.gate(str(self.w.root / CLEAN_JOB))
        self.assertEqual((code, err), (0, ""))
        job = json.loads((self.w.root / CLEAN_JOB).read_text())
        digest = hashlib.sha256(
            json.dumps(job, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(out, f"gate job {digest}: PASS cap - reason within caps\n")
        [row] = self.w.ledger_rows()
        self.assertEqual(ledger.line(row), "\t".join(
            [NOW, "job", digest, "3", "ep01", "renderbox", "2.5", "30", "renderer", "PASS", "-"]))
        [gate] = self.w.gates(JOB_TICKET)
        self.assertEqual(gate["body"], f"[PRODUCER] GATE PASS\ncovers: job {digest}\ncap: -  reason: within caps\n"
                                       f"ledger: {ledger.line(row).replace(chr(9), ' | ')}\n")
        self.assertEqual(self.w.gates(JOB_PULL), [])
        self.assertEqual(ledger.spent(self.w.ledger_rows()), {"usd": 2.5, "compute_min": 30})

    def test_relative_path_from_the_production(self):
        here = os.getcwd()
        os.chdir(self.w.root)
        self.addCleanup(os.chdir, here)
        self.assertEqual(self.w.gate(CLEAN_JOB, apply=False)[0], 0)

    def test_input_hash_does_not_match(self):
        path = self.w.job(inputs=[{"path": "frames/ep01/shot-1.md", "sha256": "0" * 64}])
        self.assert_refused(path, "input frames/ep01/shot-1.md does not match its hash on the base")
        path = self.w.job(inputs=[{"path": "frames/ep01/missing.md", "sha256": "0" * 64}])
        self.assert_refused(path, "input frames/ep01/missing.md does not match its hash on the base")

    def test_input_changed_only_in_the_work_tree(self):
        self.w.write("frames/ep01/shot-1.md", "Changed but not committed.\n")
        self.assertEqual(self.w.gate(str(self.w.root / CLEAN_JOB), apply=False)[0], 0)

    def test_extra_key(self):
        path = self.w.job(extra="x")
        self.assert_refused(path, f"job file {path} is not {{ticket, tool, inputs, expected, caller}}")

    def test_bad_shapes(self):
        for name, changes in (("missing", None), ("negative", {"expected": {"usd": -1, "compute_min": 0}}),
                              ("bool", {"ticket": True}), ("string", {"expected": {"usd": "1", "compute_min": 0}}),
                              ("input-key", {"inputs": [{"path": "a", "sha256": "b", "size": 1}]})):
            with self.subTest(name):
                if changes is None:
                    path = self.w.write("jobs/broken.json", "{not json").as_posix()
                    self.assert_refused(path, f"job file {path} is not JSON")
                else:
                    path = self.w.job(**changes)
                    self.assert_refused(path, f"job file {path} is not {{ticket, tool, inputs, expected, caller}}")

    def test_outside_the_production(self):
        outside = self.w.root.parent / "job.json"
        outside.write_text((self.w.root / CLEAN_JOB).read_text())
        self.assert_refused(str(outside), f"job file {outside} is outside the production")
        self.assert_refused(str(self.w.root / "jobs" / "none.json"),
                            f"job file {self.w.root / 'jobs' / 'none.json'} cannot be read")

    def test_caller_is_a_maker(self):
        self.assert_refused(self.w.job(caller="painter"),
                            "caller painter is not a gated operator of stage production/render")

    def test_ticket_is_not_open(self):
        self.assert_refused(self.w.job(ticket=1 + 100), "ticket #101 is not an open ticket")

    def test_missing_checker_verdict(self):
        path = self.w.dir / "comments" / f"{JOB_PULL}.json"
        path.write_text(json.dumps([c for c in json.loads(path.read_text()) if c["role"] != "inspector"]))
        code, out, err = self.w.gate(self.w.job())
        self.assertEqual((code, err), (1, ""))
        self.assertRegex(out, r"^gate job [0-9a-f]{64}: STOP cap - reason verdicts\n$")
        [row] = self.w.ledger_rows()
        self.assertEqual((row["verdict"], row["cap"]), ("STOP", "-"))
        [gate] = self.w.gates(JOB_TICKET)
        lines = gate["body"].split("\n")
        self.assertEqual((lines[0], lines[2]), ("[PRODUCER] GATE STOP", "cap: -  reason: verdicts"))

    def test_over_episode_usd_then_cap_raised(self):
        path = self.w.job(expected={"usd": 12, "compute_min": 30})
        code, out, err = self.w.gate(path)
        self.assertEqual((code, err), (1, ""))
        m = LINE.match(out.rstrip("\n"))
        self.assertEqual(m.groups()[2:], ("STOP", "CAP_EPISODE_USD", "over cap"))
        [row] = self.w.ledger_rows()
        self.assertEqual((row["verdict"], row["cap"], row["usd"]), ("STOP", "CAP_EPISODE_USD", "12"))
        self.assertEqual(ledger.spent(self.w.ledger_rows()), {"usd": 0, "compute_min": 0})
        [stop] = self.w.gates(JOB_TICKET)
        self.assertTrue(stop["body"].startswith("[PRODUCER] GATE STOP\n"))
        self.assertIn("cap: CAP_EPISODE_USD  reason: over cap\n", stop["body"])

        # The owner raises the cap: the decision changes, and is appended and posted.
        self.w.set_env("CAP_EPISODE_USD", "20")
        code, out, err = self.w.gate(path)
        self.assertEqual((code, out, err), (0, f"gate job {m.group(2)}: PASS cap - reason within caps\n", ""))
        self.assertEqual([r["verdict"] for r in self.w.ledger_rows()], ["STOP", "PASS"])
        self.assertEqual([g["body"].split("\n")[0] for g in self.w.gates(JOB_TICKET)],
                         ["[PRODUCER] GATE STOP", "[PRODUCER] GATE PASS"])
        self.assertEqual(ledger.spent(self.w.ledger_rows()), {"usd": 12, "compute_min": 30})

        # A third run: nothing new.
        code, out, err = self.w.gate(path)
        self.assertEqual((code, out, err),
                         (0, f"gate job {m.group(2)}: PASS cap - reason within caps (already recorded)\n", ""))
        self.assertEqual(len(self.w.ledger_rows()), 2)
        self.assertEqual(len(self.w.gates(JOB_TICKET)), 2)

    def test_failing_post_then_retry(self):
        target = str(self.w.root / CLEAN_JOB)
        with mock.patch.object(cmd_gate.github, "connect", return_value=FailingPost(self.w.dir)):
            code, out, err = self.w.gate(target)
        self.assertEqual((code, out, err), (2, "", "gate: post #3: exit 1\n"))
        [row] = self.w.ledger_rows()
        self.assertEqual(self.w.gates(JOB_TICKET), [])
        code, out, err = self.w.gate(target)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.w.ledger_rows(), [row])
        [gate] = self.w.gates(JOB_TICKET)
        self.assertIn("ledger: " + ledger.line(row).replace("\t", " | ") + "\n", gate["body"])
        # The rerun pushes the line again before it posts: a push the first run may not have made.
        self.assertEqual([e["op"] for e in self.w.log()], ["push", "push", "post"])

    def test_frozen(self):
        (self.w.dir / "issues" / "9.json").write_text(json.dumps({
            "number": 9, "title": "Stop everything", "body": "", "state": "open", "labels": ["factory:freeze"],
            "created_at": "2026-11-02T08:00:00Z"}))
        for target in (str(self.w.root / CLEAN_JOB), str(LOCK_PULL)):
            with self.subTest(target=target):
                self.assertEqual(self.w.gate(target), (2, "", "gate: frozen\n"))
        self.assertEqual(self.w.ledger_rows(), [])
        self.assertEqual(self.w.gates(JOB_TICKET) + self.w.gates(LOCK_PULL), [])
        self.assertEqual(self.w.log(), [])

    def test_output_holds_no_setting_value(self):
        self.w.set_env("CAP_EPISODE_USD", "7.3125")
        code, out, _ = self.w.gate(self.w.job(expected={"usd": 8, "compute_min": 30}))
        self.assertEqual(code, 1)
        text = out + "".join(g["body"] for g in self.w.gates(JOB_TICKET))
        text += (self.w.root / ledger.PATH).read_text()
        for value in ("7.3125", "example/lantern-show"):
            self.assertNotIn(value, text)


class JobOnPullTest(unittest.TestCase):
    """ISSUE-96: `--pull N` reads the job file and its inputs at the head of pull request N."""

    PULL_JOB = "jobs/renders/requests/r1.json"

    def setUp(self):
        self.w = World(self)
        self.request = '{"for": "shot-1", "prompt": "a lantern"}\n'
        job = json.loads((self.w.root / CLEAN_JOB).read_text())
        job["inputs"] = [{"path": "renders/requests/r1.json",
                          "sha256": hashlib.sha256(self.request.encode()).hexdigest()}]
        self.job = job
        self.on_branch(self.w.pull_record(JOB_PULL)["head_ref"],
                       {"renders/requests/r1.json": self.request, self.PULL_JOB: json.dumps(job, indent=1)})

    def on_branch(self, ref, files):
        """Commit ``files`` on ``ref`` and record a fresh lint and checker verdict on its pull."""
        self.w.git("checkout", "-q", ref)
        for rel, text in files.items():
            self.w.write(rel, text)
        self.w.commit("files on " + ref)
        self.w.git("checkout", "-q", "trunk")
        number = next(n for n in (LOCK_PULL, JOB_PULL) if self.w.pull_record(n)["head_ref"] == ref)
        self.w.record(number, {"body": f"[LINT] PASS\nstructure: PASS\nreviewed head {{head:{number}}} manifest none\n",
                               "created_at": "2026-11-01T12:10:00Z", "id": 100 + number, "role": "program:lint"})
        self.w.record(number, {"body": f"[INSPECTOR] APPROVED\nfine\nreviewed head {{head:{number}}} manifest none\n",
                               "created_at": "2026-11-01T12:20:00Z", "id": 200 + number, "role": "inspector"})

    def gate(self, target, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_gate.main([target, *extra, "--root", str(self.w.root), "--apply"])
        return code, out.getvalue(), err.getvalue()

    def assert_refused(self, result, message):
        self.assertEqual(result, (2, "", f"gate: {message}\n"))
        self.assertEqual(self.w.ledger_rows(), [])
        self.assertEqual(self.w.gates(JOB_TICKET), [])

    def test_pass_on_the_pull(self):
        code, out, err = self.gate(self.PULL_JOB, "--pull", str(JOB_PULL))
        self.assertEqual((code, err), (0, ""))
        digest = hashlib.sha256(json.dumps(self.job, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(out, f"gate job {digest}: PASS cap - reason within caps\n")
        [gate] = self.w.gates(JOB_TICKET)
        self.assertEqual(gate["body"].split("\n")[:2], ["[PRODUCER] GATE PASS", f"covers: job {digest}"])
        self.assertNotIn("reviewed head", gate["body"])
        [row] = self.w.ledger_rows()
        self.assertEqual((row["hash"], row["verdict"]), (digest, "PASS"))

    def test_without_pull_cannot_be_read(self):
        self.assertFalse((self.w.root / self.PULL_JOB).exists())
        here = os.getcwd()
        os.chdir(self.w.root)
        self.addCleanup(os.chdir, here)
        self.assert_refused(self.gate(self.PULL_JOB), f"job file {self.PULL_JOB} cannot be read")

    def test_missing_file_on_the_pull(self):
        self.assert_refused(self.gate("jobs/none.json", "--pull", str(JOB_PULL)), "job file jobs/none.json cannot be read")
        self.assert_refused(self.gate("../" + self.PULL_JOB, "--pull", str(JOB_PULL)),
                            f"job file ../{self.PULL_JOB} cannot be read")
        self.assert_refused(self.gate(self.PULL_JOB, "--pull", "99"), f"job file {self.PULL_JOB} cannot be read")

    def test_pull_of_another_ticket(self):
        self.on_branch(self.w.pull_record(LOCK_PULL)["head_ref"],
                       {"renders/requests/r1.json": self.request, self.PULL_JOB: json.dumps(self.job, indent=1)})
        self.assert_refused(self.gate(self.PULL_JOB, "--pull", str(LOCK_PULL)),
                            f"pull #{LOCK_PULL} is not the open pull of ticket #{JOB_TICKET}")

    def test_input_changed_on_the_pull(self):
        self.on_branch(self.w.pull_record(JOB_PULL)["head_ref"], {"renders/requests/r1.json": "changed\n"})
        self.assert_refused(self.gate(self.PULL_JOB, "--pull", str(JOB_PULL)),
                            f"input renders/requests/r1.json does not match its hash on pull #{JOB_PULL}")


class LintAcceptsGateCommentsTest(unittest.TestCase):
    def test_markers_lint(self):
        w = World(self)
        self.assertEqual(w.gate(str(LOCK_PULL))[0], 0)
        self.assertEqual(w.gate(str(w.root / CLEAN_JOB))[0], 0)
        self.assertEqual(w.gate(w.job(expected={"usd": 12, "compute_min": 30}))[0], 1)
        comments = w.gates(LOCK_PULL) + w.gates(JOB_TICKET)
        self.assertEqual(len(comments), 3)
        tree = w.root.parent / "tree"
        shutil.copytree(FIX / "production" / ".factory", tree / ".factory")
        (tree / ".factory-state").mkdir()
        (tree / ".factory-state" / "comments.json").write_text(json.dumps(comments, indent=1) + "\n")
        result = lint.run(tree, only=["markers"])["markers"]
        self.assertEqual((result.status, result.findings, result.reason), ("PASS", [], ""))


class CountingSync(github.Recorded):
    """A recorded GitHub that records each base ``sync_base`` is called with; with ``fail`` it raises."""

    def __init__(self, directory, fail=False):
        super().__init__(directory)
        self.fail = fail
        self.synced = []

    def sync_base(self, root, base_ref):
        self.synced.append(base_ref)
        if self.fail:
            raise github.GitHubError(f"sync_base {base_ref}: git fetch failed (exit 128)")
        return super().sync_base(root, base_ref)


class GitSync(github.Recorded):
    """A recorded GitHub whose ``sync_base`` is ``Gh.sync_base``: it starts only `git`, on the clone's `origin`."""

    def sync_base(self, root, base_ref):
        return github.Gh("example/lantern-show").sync_base(root, base_ref)


class SyncBaseTest(unittest.TestCase):
    """ISSUE-228: with ``--apply`` the gate first brings the local base up to GitHub's."""

    def setUp(self):
        self.w = World(self)
        self.target = str(self.w.root / CLEAN_JOB)

    def run_gate(self, gh, apply=True):
        with mock.patch.object(github, "connect", return_value=gh):
            return self.w.gate(self.target, apply)

    def snapshot(self):
        """The recorded GitHub's bytes, the production's refs and every file name of the world."""
        tmp = self.w.root.parent
        return ({str(path.relative_to(self.w.dir)): path.read_bytes()
                 for path in sorted(self.w.dir.rglob("*")) if path.is_file()},
                self.w.git("for-each-ref", "--format=%(refname) %(objectname)"),
                sorted(str(path.relative_to(tmp)) for path in tmp.rglob("*")))

    def test_apply_syncs_once(self):
        gh = CountingSync(self.w.dir)
        self.assertEqual(self.run_gate(gh)[0], 0)
        self.assertEqual(gh.synced, ["trunk"])

    def test_without_apply_does_not_sync(self):
        gh = CountingSync(self.w.dir, fail=True)
        code, out, err = self.run_gate(gh, apply=False)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(gh.synced, [])

    def test_a_failed_sync_writes_nothing(self):
        gh = CountingSync(self.w.dir, fail=True)
        before = self.snapshot()
        self.assertEqual(self.run_gate(gh), (2, "", "gate: sync_base trunk: git fetch failed (exit 128)\n"))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(gh.synced, ["trunk"])

    def test_stale_clone_appends_on_the_synced_ledger(self):
        row, start = self.w.origin(behind=True)
        remote = gitstate.head(self.w.root.parent / "origin.git", "trunk")
        self.assertNotEqual(start, remote)
        self.assertEqual(self.w.ledger_rows(), [])
        code, out, err = self.run_gate(GitSync(self.w.dir))
        self.assertEqual((code, err), (0, ""))
        digest = LINE.match(out.rstrip("\n")).group(2)
        rows = self.w.ledger_rows()
        self.assertEqual([ledger.line(r) for r in rows[:1]], [row])
        self.assertEqual([r["hash"] for r in rows], ["e" * 64, digest])
        self.w.git("merge-base", "--is-ancestor", remote, "trunk")
        self.assertEqual(self.w.git("log", "-1", "--format=%P", "trunk").split(), [remote])

    def test_clean_twin_appends_one_row_on_the_local_tip(self):
        row, start = self.w.origin(behind=False)
        self.assertEqual(start, gitstate.head(self.w.root.parent / "origin.git", "trunk"))
        self.assertEqual([ledger.line(r) for r in self.w.ledger_rows()], [row])
        code, out, err = self.run_gate(GitSync(self.w.dir))
        self.assertEqual((code, err), (0, ""))
        digest = LINE.match(out.rstrip("\n")).group(2)
        self.assertEqual([r["hash"] for r in self.w.ledger_rows()], ["e" * 64, digest])
        self.assertEqual(self.w.git("log", "-1", "--format=%P", "trunk").split(), [start])


class EntryPointTest(unittest.TestCase):
    def test_factory_gate_runs(self):
        w = World(self)
        for argv in (["kit/factory", "gate"], ["kit/lib/cmd_gate.py"]):
            with self.subTest(argv=argv):
                done = subprocess.run([sys.executable, *argv, str(LOCK_PULL), "--root", str(w.root)],
                                      cwd=ROOT, capture_output=True, text=True)
                self.assertEqual((done.returncode, done.stderr), (0, ""))
                self.assertRegex(done.stdout, r"^gate lock [0-9a-f]{64}: PASS cap - reason within caps\n$")
        self.assertEqual(w.log(), [])


class RightsRefusalTest(unittest.TestCase):
    """ISSUE-274: a job whose input names an entry of research/not-ours.tsv on the base is refused."""

    LIST = "name\tholder\tlink\nWickabel\tthe makers of Skarrowfen\thttps://example.invalid/1\n"
    TEXT = "Draw Wickabel on the pier.\n"

    def setUp(self):
        self.w = World(self)

    def job_naming(self, rel, name):
        """``rel`` holding TEXT and the not-ours list, committed on `trunk`; a job whose input is ``rel``."""
        self.w.write(rel, self.TEXT)
        self.w.write("research/not-ours.tsv", self.LIST)
        self.w.commit("the list and " + rel)
        return self.w.job(name, inputs=[{"path": rel, "sha256": hashlib.sha256(self.TEXT.encode()).hexdigest()}])

    def test_an_input_names_a_listed_name(self):
        path = self.job_naming("bible/style/requests/pier.json", "pier.json")
        code, out, err = self.w.gate(path)
        self.assertEqual((code, out, err), (2, "", "gate: input bible/style/requests/pier.json names 'Wickabel' "
                                                   "(research/not-ours.tsv:2)\n"))
        self.assertEqual(self.w.ledger_rows(), [])
        self.assertEqual(self.w.gates(JOB_TICKET), [])
        self.assertEqual(self.w.log(), [])

    def test_clean_twin_an_input_under_research(self):
        path = self.job_naming("research/requests/pier.json", "research.json")
        code, out, err = self.w.gate(path)
        self.assertEqual((code, err), (0, ""))
        self.assertRegex(out, r"^gate job [0-9a-f]{64}: PASS cap - reason within caps\n$")
        code, out, err = self.w.gate(str(self.w.root / CLEAN_JOB))
        self.assertEqual((code, err), (0, ""))
        self.assertRegex(out, r"^gate job [0-9a-f]{64}: PASS cap - reason within caps\n$")
        self.assertEqual(len(self.w.ledger_rows()), 2)


if __name__ == "__main__":
    unittest.main()
