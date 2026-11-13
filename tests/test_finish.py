"""Tests for kit/lib/finish.py, through `factory launch ... --wait`.

The production is tests/fixtures/finish/production/, made a git repository on branch `trunk` in a temporary
directory with one branch per recorded pull, each changing episodes/ep01/outline.md. GitHub is a copy of
tests/fixtures/finish/github/: ticket #1 (ep01) is locked by the merged pull #2; ticket #3 (ep02) waits for
its writer's first launch; ticket #4 (ep01 again) has the open pull #5 with the writer's READY.
The harness is a copy of tests/fixtures/harness/fake-harness, its steps written per test.
"""

import contextlib
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

from kit.lib import cmd_launch, finish, gitstate, github, harness, launch, markers, production  # noqa: E402
from kit.lib import sandbox, tickets  # noqa: E402
from kit.lib import steps  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "finish"
FAKE = ROOT / "tests" / "fixtures" / "harness" / "fake-harness"
NOW = "2026-11-11T12:00:00Z"
SECRETS_NAME = "vault-ocelot-3b9e"
TOKEN = "tok-wombat-58c1-do-not-print"
ACCOUNTS = ("acct-one", "acct-two")
SHA = re.compile(r"[0-9a-f]{40}")
WRITER, CRITIC, WEIGHER = "You are the writer", "You are the critic", "You are the weigher"
EVIDENCE = [{"criterion": "outline written", "result": "pass", "command": "cat episodes/ep02/outline.md"}]


def result_step(match, result, files=None, **extra):
    return {"match": match, "result": result, "files": files or {}, **extra}


def ready(summary="The heron leaves at dusk.", files=None, **extra):
    files = {"episodes/ep02/outline.md": "# Outline of episode two\n\nThe heron leaves.\n"} if files is None else files
    return result_step(WRITER, {"marker": "READY", "summary": summary, "evidence": EVIDENCE}, files, **extra)


def notes(summary="One thing blocks this.", blocker="The dam is never shown."):
    return result_step(CRITIC, {"marker": "NOTES", "summary": summary, "notes": [
        {"id": "B1", "severity": "Blocker", "text": blocker, "evidence": "episodes/ep01/outline.md line 3"}]},
        {"scratch.txt": "the critic's own scratch\n"})


def approved(summary="The flood has a cause now."):
    return result_step(CRITIC, {"marker": "APPROVED", "summary": summary,
                                "evidence": [{"criterion": "cause shown", "result": "pass", "command": "read"}]},
                       {"scratch.txt": "the critic's own scratch\n"})


class World:
    """A production on branch `trunk`, a recorded GitHub, a fake harness, secrets and a state directory."""

    def __init__(self, test, harness_name="fake-harness"):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        test.addCleanup(shutil.rmtree, self.tmp)
        self.github = self.tmp / "github"
        env = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "FACTORY_NOW": NOW,
               "FACTORY_GITHUB": str(self.github)}
        patch = mock.patch.dict(os.environ, env)
        patch.start()
        test.addCleanup(patch.stop)
        os.environ.pop("FACTORY_LAUNCH_WAIT", None)
        self.root = self.tmp / "show"
        shutil.copytree(FIX / "production", self.root)
        self.git("init", "-q", "-b", "trunk")
        self.commit("the production")
        shutil.copytree(FIX / "github", self.github)
        for path in sorted((self.github / "pulls").glob("*.json")):
            self.branch(json.loads(path.read_text())["head_ref"])
        self.harness = self.tmp / "harness"
        self.harness.mkdir()
        for path in (FAKE, FIX / "committing-harness", FIX / "sandboxed-harness"):
            shutil.copy2(path, self.harness / path.name)
        self.secrets = self.tmp / SECRETS_NAME
        (self.secrets / "inference.primary").mkdir(parents=True)
        for name in ACCOUNTS:
            path = self.secrets / "inference.primary" / name
            path.write_text(TOKEN + "\n")
            path.chmod(0o600)
        self.state = self.tmp / "state"
        self.gh = github.Recorded(self.github)
        self.local(HARNESS=str(self.harness / harness_name))

    def local(self, **extra):
        values = {"STATE_DIR": str(self.state), "SECRETS_DIR": str(self.secrets),
                  "HARNESS": str(self.harness / "fake-harness"), "MODEL_MAKER": "maker-model-1",
                  "MODEL_CHECKER": "checker-model-1", "ACCOUNTS_MAKER": ", ".join(ACCOUNTS),
                  "ACCOUNTS_CHECKER": ", ".join(ACCOUNTS)}
        values.update(getattr(self, "settings", {}))
        values.update(extra)
        self.settings = values
        (self.root / "production.local.env").write_text("".join(f"{k}={v}\n" for k, v in values.items()))
        self.p = production.load(self.root)

    def git(self, *args):
        return gitstate.git(self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", *args)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def branch(self, ref):
        """A branch `ref` on top of `trunk` that changes episodes/ep01/outline.md."""
        self.git("checkout", "-q", "-b", ref, "trunk")
        path = self.root / "episodes" / "ep01" / "outline.md"
        path.write_text(path.read_text() + f"\nThe dam breaks ({ref}).\n")
        self.commit("write " + ref)
        self.git("checkout", "-q", "trunk")

    def head(self, ref):
        return gitstate.head(self.root, ref)

    def steps(self, *steps):
        (self.harness / "fake-harness.json").write_text(json.dumps(list(steps), indent=1) + "\n")
        for name in ("fake-harness.used", "fake-harness.calls.jsonl"):
            (self.harness / name).unlink(missing_ok=True)

    def calls(self):
        path = self.harness / "fake-harness.calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def launch(self, *args, wait=True):
        """``(exit code, stdout, stderr)`` of ``factory launch *args --root <root> [--wait]`` in this process."""
        return self.main([*args, "--root", str(self.root)] + (["--wait"] if wait else []))

    def main(self, argv):
        """``(exit code, stdout, stderr)`` of ``cmd_launch.main(argv)``."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_launch.main(argv)
        return code, out.getvalue(), err.getvalue()

    def comments(self, number):
        return self.gh.comments(number)

    def by(self, number, role):
        """The comments on ``number`` posted with ``role`` that are not LAUNCH records."""
        return [c for c in self.comments(number) if c["role"] == role]

    def posted(self, role):
        """Every comment posted with ``role`` during the test (at ``NOW``), on any issue or pull."""
        return [c for path in sorted((self.github / "comments").glob("*.json"))
                for c in json.loads(path.read_text()) if c["role"] == role and c["created_at"] == NOW]

    def log(self):
        path = self.github / "log.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def state_now(self):
        return tickets.load(self.p, self.gh, self.root)

    def run_dir(self, name):
        return self.state / "runs" / name


def last_line(body):
    return body.rstrip("\n").split("\n")[-1]


class MakerFirstLaunch(unittest.TestCase):
    """Criterion 1: one file inside `writes`, one outside, result READY; then ADDRESSED on the same pull."""

    def setUp(self):
        self.w = World(self)
        self.w.steps(ready(files={"episodes/ep02/outline.md": "# Two\n\nThe heron leaves.\n",
                                  "notes/scratch.md": "outside writes\n"}))
        self.code, self.out, self.err = self.w.launch("writer", "3")
        self.branch = "outline/OL-2-writer-1"

    def test_exit_and_output(self):
        self.assertEqual((self.code, self.err), (0, ""))
        self.assertEqual(self.out, "launched writer 3-1\nposted [WRITER] READY for launch 3-1\n")

    def test_one_commit_by_the_role_with_the_inside_file(self):
        w = self.w
        self.assertEqual(w.git("log", "--format=%an %ae", f"trunk..{self.branch}").splitlines(),
                         ["writer writer@factory.invalid"])
        self.assertEqual(w.git("show", "--name-only", "--format=", self.branch).split(),
                         ["episodes/ep02/outline.md"])
        self.assertEqual(w.git("log", "-1", "--format=%s", self.branch).strip(),
                         "[OL-2] outline: The heron leaves at dusk.")

    def test_outside_file_is_gone(self):
        worktree = self.w.state / "worktrees" / "writer-3-1"
        self.assertTrue((worktree / "episodes" / "ep02" / "outline.md").is_file())
        self.assertFalse((worktree / "notes" / "scratch.md").exists())
        self.assertEqual(gitstate.status(worktree), [])

    def test_pull_and_push(self):
        pulls = [p for p in self.w.gh.pulls("open") if p["head_ref"] == self.branch]
        self.assertEqual(len(pulls), 1)
        pull = pulls[0]
        self.assertEqual((pull["number"], pull["body"], pull["title"], pull["base_ref"], pull["ticket"]),
                         (6, "Ticket: #3\n", "[OL-2] Outline for ep02", "trunk", 3))
        ops = [(e["op"], e.get("ref")) for e in self.w.log() if e["op"] in ("push", "open_pull")]
        self.assertEqual(ops, [("push", self.branch), ("open_pull", None)])

    def test_comment_on_the_new_pull(self):
        w = self.w
        self.assertEqual(w.by(3, "writer"), [])
        posted = w.by(6, "writer")
        self.assertEqual(len(posted), 1)
        body = posted[0]["body"]
        head = w.head(self.branch)
        self.assertEqual(body, "\n".join([
            "[WRITER] READY", "", "The heron leaves at dusk.", "",
            "| Criterion | Result | Command |", "|---|---|---|",
            "| outline written | pass | cat episodes/ep02/outline.md |",
            "Dropped (outside writes): notes/scratch.md",
            "Launch: 3-1",
            f"head {head} manifest none"]) + "\n")
        self.assertEqual(markers.parse(body), markers.Marker("WRITER", "READY", ()))

    def test_second_launch_addresses_on_the_same_pull(self):
        w = self.w
        w.steps(result_step(WRITER, {"marker": "ADDRESSED", "summary": "The heron leaves at dawn now.",
                                     "evidence": EVIDENCE,
                                     "addressed": [{"note": "B1", "by": "critic", "action": "changed",
                                                    "text": "the time\nof day | fixed"}]},
                            {"episodes/ep02/outline.md": "# Two\n\nThe heron leaves at dawn.\n"}))
        first_head = w.head(self.branch)
        code, out, err = w.launch("writer", "6")
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(len(w.gh.pulls("all")), 3)
        self.assertEqual(w.git("log", "--format=%an", f"trunk..{self.branch}").split(), ["writer", "writer"])
        self.assertEqual(w.git("rev-parse", f"{self.branch}~1").strip(), first_head)
        body = w.by(6, "writer")[-1]["body"]
        lines = body.rstrip("\n").split("\n")
        self.assertEqual(lines[0], "[WRITER] ADDRESSED")
        self.assertIn("- B1 by critic: changed. the time of day | fixed", lines)
        self.assertEqual(lines[-3:], ["Dropped (outside writes): none", f"Launch: {json.loads((w.run_dir('writer-6-2') / 'run.json').read_text())['id']}",
                                      f"head {w.head(self.branch)} manifest none"])
        self.assertEqual([e["ref"] for e in w.log() if e["op"] == "push"], [self.branch, self.branch])


class CheckerVerdicts(unittest.TestCase):
    """Criteria 2 and 5: a checker's NOTES and APPROVED on pull #5, never committed, worktree removed."""

    def setUp(self):
        self.w = World(self)
        self.head = self.w.head("outline/OL-3-writer-1")
        self.refs = self.w.git("for-each-ref", "--format=%(refname) %(objectname)")

    def verdict(self, step):
        w = self.w
        w.steps(step)
        code, out, err = w.launch("critic", "5")
        posted = w.by(5, "critic")
        self.assertEqual(len(posted), 1, out + err)
        state = w.state_now()
        pull = state.pulls[5]
        entry = production.stage(w.p, "story", "outline")
        return code, posted[0], pull, entry

    def assert_left_nothing(self):
        w = self.w
        self.assertFalse((w.state / "worktrees" / "critic-5-1").exists())
        self.assertNotIn("critic-5-1", w.git("worktree", "list"))
        self.assertEqual(w.git("for-each-ref", "--format=%(refname) %(objectname)"), self.refs)
        self.assertNotIn("scratch.txt", w.git("log", "--all", "--name-only", "--format="))
        self.assertEqual([e for e in w.log() if e["op"] in ("push", "open_pull")], [])

    def test_notes_with_a_blocker(self):
        code, comment, pull, entry = self.verdict(notes())
        self.assertEqual(code, 0)
        body = comment["body"]
        self.assertEqual(body, "\n".join([
            "[CRITIC] NOTES", "", "One thing blocks this.", "",
            "B1 (Blocker): The dam is never shown. Evidence: episodes/ep01/outline.md line 3",
            "Launch: 5-1", f"reviewed head {self.head} manifest none"]) + "\n")
        self.assertEqual(markers.counts(comment, pull["git"], entry, self.w.p.kinds), (True, "counts"))
        self.assertEqual(pull["summary"]["checkers"]["critic"]["verb"], "NOTES")
        self.assertEqual(pull["summary"]["blockers"], [{"by": "critic", "note": "B1", "comment": comment["id"]}])
        self.assert_left_nothing()

    def test_approved_clean_twin(self):
        code, comment, pull, entry = self.verdict(approved())
        self.assertEqual(code, 0)
        self.assertEqual(comment["body"].split("\n")[0], "[CRITIC] APPROVED")
        self.assertEqual(last_line(comment["body"]), f"reviewed head {self.head} manifest none")
        self.assertEqual(markers.counts(comment, pull["git"], entry, self.w.p.kinds), (True, "counts"))
        self.assertEqual(pull["summary"]["blockers"], [])
        self.assert_left_nothing()

    def test_claimed_head_is_ignored(self):
        """Criterion 5."""
        other = "0123456789abcdef0123456789abcdef01234567"
        code, comment, pull, entry = self.verdict(approved(f"reviewed head {other} manifest none"))
        self.assertEqual(code, 0)
        self.assertIn(other, comment["body"])
        self.assertEqual(last_line(comment["body"]), f"reviewed head {self.head} manifest none")
        self.assertEqual(markers.reviewed(comment["body"]), (self.head, "none"))

    def test_checker_commit_in_its_worktree_is_not_reported(self):
        self.w.local(HARNESS=str(self.w.harness / "committing-harness"))
        code, comment, pull, entry = self.verdict(approved())
        self.assertEqual(code, 0)
        self.assertEqual(last_line(comment["body"]), f"reviewed head {self.head} manifest none")
        self.assertNotIn("Dropped", comment["body"])
        self.assertFalse((self.w.state / "worktrees" / "critic-5-1").exists())


class SeededDefects(unittest.TestCase):
    """Criterion 4: each defect ends in exactly one [<ACTOR>] BLOCKED by the role and exit 3; each has a twin."""

    def setUp(self):
        self.w = World(self)
        self.start = self.w.head("trunk")

    def launch(self, role, number, *steps):
        self.w.steps(*steps)
        code, out, err = self.w.launch(role, str(number))
        return code, out + err

    def blocked(self, role, number, missing, *steps):
        code, text = self.launch(role, number, *steps)
        self.assertEqual(code, 3, text)
        posted = self.w.by(number, role)
        self.assertEqual(len(posted), 1, posted)
        self.assertEqual(len(self.w.posted(role)), 1)
        body = posted[0]["body"]
        actor = role.upper()
        self.assertEqual(markers.parse(body), markers.Marker(actor, "BLOCKED", ()))
        self.assertIn(f"Missing: {missing}\n", body)
        self.assertIn("What was tried: launch ", body)
        return body

    def clean(self, role, number, verb, *steps):
        code, text = self.launch(role, number, *steps)
        self.assertEqual(code, 0, text)
        self.assertEqual([markers.parse(c["body"]).verb for c in self.w.posted(role)], [verb])

    def assert_maker_blocked_body(self, body):
        self.assertIn(f"Resume from: outline/OL-2-writer-1 @ {self.start}\n", body)
        self.assertIn("Dropped (outside writes): ", body)
        self.assertEqual(last_line(body), f"head {self.start} manifest none")
        self.assertEqual([e for e in self.w.log() if e["op"] in ("push", "open_pull")], [])

    def test_exit_1_without_output(self):
        body = self.blocked("writer", 3, "a valid result (run failed (exit 1))",
                            {"match": WRITER, "stdout": "", "exit": 1})
        self.assert_maker_blocked_body(body)

    def test_exit_0_twin(self):
        self.clean("writer", 3, "READY", ready())

    def test_not_json(self):
        body = self.blocked("writer", 3, "a valid result (run failed (exit 0))",
                            {"match": WRITER, "stdout": "this is not JSON\n"})
        self.assert_maker_blocked_body(body)

    def test_json_twin(self):
        self.clean("writer", 3, "READY", ready())

    def test_marker_the_kind_may_not_post(self):
        body = self.blocked("writer", 3, "a valid result (result.marker: a maker posting as WRITER may not "
                                         "post 'APPROVED')",
                            result_step(WRITER, {"marker": "APPROVED", "summary": "fine"},
                                        {"episodes/ep02/outline.md": "x\n"}))
        self.assert_maker_blocked_body(body)
        self.assertEqual(self.w.head("outline/OL-2-writer-1"), self.start)

    def test_notes_without_a_note(self):
        body = self.blocked("critic", 5, "a valid result (result.notes: NOTES needs at least one note)",
                            result_step(CRITIC, {"marker": "NOTES", "summary": "something"}))
        head = self.w.head("outline/OL-3-writer-1")
        self.assertIn(f"Resume from: PR #5 @ {head}\n", body)
        self.assertNotIn("Dropped", body)
        self.assertEqual(last_line(body), f"head {head} manifest none")
        self.assertFalse((self.w.state / "worktrees" / "critic-5-1").exists())

    def test_notes_with_a_note_twin(self):
        self.clean("critic", 5, "NOTES", notes())

    def test_ready_writing_nothing(self):
        body = self.blocked("writer", 3, "any change inside writes", ready(files={}))
        self.assert_maker_blocked_body(body)
        self.assertIn("Dropped (outside writes): none\n", body)

    def test_ready_writing_only_outside(self):
        body = self.blocked("writer", 3, "any change inside writes", ready(files={"notes/x.md": "x\n"}))
        self.assert_maker_blocked_body(body)
        self.assertIn("Dropped (outside writes): notes/x.md\n", body)

    def test_timeout(self):
        self.w.local(RUN_TIMEOUT_MIN="0.02")
        started = []
        real = harness.start

        def spy(*args, **kw):
            started.append(real(*args, **kw))
            return started[-1]

        with mock.patch.object(harness, "start", spy):
            body = self.blocked("writer", 3, "a valid result (timed out)", ready(sleep=30))
        self.assert_maker_blocked_body(body)
        self.assertEqual(len(self.w.calls()), 1)
        self.assertEqual(len(started), 1)
        self.assertIsNotNone(started[0].poll())
        with self.assertRaises(ProcessLookupError):
            os.killpg(started[0].pid, 0)

    def test_inside_the_timeout_twin(self):
        self.w.local(RUN_TIMEOUT_MIN="0.02")
        self.clean("writer", 3, "READY", ready(sleep=0))

    def test_worker_commits_outside_writes(self):
        self.w.local(HARNESS=str(self.w.harness / "committing-harness"))
        body = self.blocked("writer", 3, "commits only inside writes",
                            ready(files={"episodes/ep02/outline.md": "x\n", "notes/own.md": "mine\n"}))
        self.assert_maker_blocked_body(body)
        self.assertIn("Dropped (outside writes): notes/own.md\n", body)
        self.assertEqual(self.w.head("outline/OL-2-writer-1"), self.start)
        self.assertEqual(gitstate.status(self.w.state / "worktrees" / "writer-3-1"), [])

    def test_worker_commits_inside_writes_twin(self):
        self.w.local(HARNESS=str(self.w.harness / "committing-harness"))
        self.clean("writer", 3, "READY", ready())
        self.assertEqual(self.w.git("log", "--format=%an", "trunk..outline/OL-2-writer-1").split(), ["worker"])
        self.assertEqual([e["op"] for e in self.w.log() if e["op"] in ("push", "open_pull")],
                         ["push", "open_pull"])

    def exhaust(self, *names):
        self.w.state.mkdir(mode=0o700)
        (self.w.state / "accounts.json").write_text(json.dumps({n: "2026-11-12T00:00:00Z" for n in names}))

    def test_every_account_exhausted(self):
        self.exhaust(*ACCOUNTS)
        before = self.w.comments(3)
        code, text = self.launch("writer", 3, ready())
        self.assertEqual((code, text), (1, "launch: slot MAKER is out of usage until 2026-11-12T00:00:00Z\n"))
        self.assertEqual(self.w.comments(3), before)
        self.assertFalse((self.w.harness / "fake-harness.calls.jsonl").exists())

    def test_every_account_exhausted_after_the_launch(self):
        self.exhaust(*ACCOUNTS)
        with mock.patch("kit.lib.down.waiting", return_value=None):
            body = self.blocked("writer", 3, "a valid result (every account is out of usage)", ready())
        self.assert_maker_blocked_body(body)
        self.assertFalse((self.w.harness / "fake-harness.calls.jsonl").exists())

    def test_one_account_left_twin(self):
        self.exhaust(ACCOUNTS[0])
        self.clean("writer", 3, "READY", ready())
        self.assertEqual(len(self.w.calls()), 1)

    def test_role_blocked_result_commits_nothing(self):
        code, text = self.launch("writer", 3, result_step(
            WRITER, {"marker": "BLOCKED", "summary": "Cannot place the heron.",
                     "blocked": {"missing": "the heron's route", "tried": "the map", "resume_from": "nothing yet"}},
            {"episodes/ep02/outline.md": "half\n"}))
        self.assertEqual(code, 3, text)
        posted = self.w.by(3, "writer")
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0]["body"], "\n".join([
            "[WRITER] BLOCKED", "", "Cannot place the heron.", "", "Missing: the heron's route",
            "What was tried: the map", "Resume from: nothing yet", "Dropped (outside writes): none", "Launch: 3-1",
            f"head {self.start} manifest none"]) + "\n")
        self.assertEqual(self.w.head("outline/OL-2-writer-1"), self.start)
        self.assertEqual([e for e in self.w.log() if e["op"] in ("push", "open_pull")], [])


class Accounts(unittest.TestCase):
    """Criteria 6 and 7."""

    def setUp(self):
        self.w = World(self)
        self.tokens, self.envs = [], []
        real = harness.start

        def spy(argv, env, cwd, prompt_path, token_path, *rest, **kw):
            self.tokens.append(Path(token_path).name)
            self.envs.append(set(env))
            return real(argv, env, cwd, prompt_path, token_path, *rest, **kw)

        patch = mock.patch.object(harness, "start", spy)
        patch.start()
        self.addCleanup(patch.stop)

    def test_out_of_usage_moves_to_the_next_account(self):
        w = self.w
        usage = {"type": "result", "subtype": "error_during_execution", "is_error": True,
                 "result": "Claude AI usage limit reached", "api_error_status": 429}
        w.steps({"match": WRITER, "envelope": usage, "exit": 1}, ready())
        code, out, err = w.launch("writer", "3")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.tokens, ["acct-one", "acct-two"])
        self.assertEqual(json.loads((w.state / "accounts.json").read_text()), {"acct-one": "2026-11-11T17:00:00Z"})
        self.assertEqual([markers.parse(c["body"]).verb for c in w.by(6, "writer")], ["READY"])
        self.assertEqual(w.by(3, "writer"), [])
        self.assert_no_secret(w.run_dir("writer-3-1"))

    def test_out_of_usage_twice_is_blocked(self):
        w = self.w
        usage = {"type": "result", "subtype": "error_during_execution", "is_error": True,
                 "result": "usage limit reached", "api_error_status": 429}
        w.steps({"match": WRITER, "envelope": usage, "exit": 1}, {"match": WRITER, "envelope": usage, "exit": 1},
                ready())
        code, out, err = w.launch("writer", "3")
        self.assertEqual(code, 3, out + err)
        self.assertEqual(self.tokens, ["acct-one", "acct-two"])
        self.assertEqual(sorted(json.loads((w.state / "accounts.json").read_text())), list(ACCOUNTS))
        self.assertIn("Missing: a valid result (account out of usage (exit 1))\n", w.by(3, "writer")[0]["body"])

    def assert_no_secret(self, run_dir):
        w = self.w
        under = str(w.secrets) + os.sep
        texts = {str(path): path.read_text(errors="replace") for path in sorted(run_dir.rglob("*")) if path.is_file()}
        texts.update({str(path): path.read_text() for path in sorted(w.github.rglob("*.json*"))})
        for name, text in texts.items():
            self.assertNotIn(TOKEN, text, name)
            self.assertNotIn(under, text, name)
            if not name.endswith("sandbox.sb"):  # the profile names SECRETS_DIR to deny reading it
                self.assertNotIn(SECRETS_NAME, text, name)

    def test_calls_have_the_token_descriptor_and_only_allowed_environment(self):
        """Criterion 7."""
        w = self.w
        w.steps(ready())
        self.assertEqual(w.launch("writer", "3")[0], 0)
        calls = w.calls()
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0]["fd3"], True)
        allowed = harness.environment(os.environ, w.p.settings, "any", w.run_dir("writer-3-1") / "config", 3,
                                      tmp_dir=w.run_dir("writer-3-1") / "tmp")
        self.assertEqual(self.envs, [set(allowed)])
        # The fake's own interpreter adds a few keys at start-up (locale coercion, and on macOS the text
        # encoding); they are read from the same interpreter started with PATH alone.
        own = subprocess.run(["/usr/bin/env", "python3", "-c", "import os; print(' '.join(os.environ))"],
                             env={"PATH": os.environ["PATH"]}, capture_output=True, text=True, check=True)
        added = set(own.stdout.split()) - {"PATH"}
        self.assertLessEqual(set(calls[0]["env_keys"]), set(allowed) | added)
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR", calls[0]["env_keys"])
        self.assertEqual(calls[0]["cwd"], str(w.state / "worktrees" / "writer-3-1"))


class Sandbox(unittest.TestCase):
    """Criterion 9."""

    def test_profile_names_the_run(self):
        w = World(self)
        w.steps(approved())
        self.assertEqual(w.launch("critic", "5")[0], 0)
        run_dir = w.run_dir("critic-5-1")
        text = (run_dir / "sandbox.sb").read_text()
        self.assertIn(f'(subpath "{os.path.realpath(run_dir)}")', text)
        self.assertIn(f'(subpath "{os.path.realpath(w.state / "worktrees" / "critic-5-1")}")', text)
        self.assertIn('(remote ip "*:443")', text)
        self.assertEqual(oct(os.stat(run_dir / "sandbox.sb").st_mode & 0o777), "0o600")

    def test_family_other_reaches_the_proxy(self):
        w = World(self)
        w.local(PROXY_URL="http://localhost:8317", MODEL_OTHER="other-model-1")
        role = w.root / ".factory" / "roles" / "critic.md"
        role.write_text(role.read_text().replace("family: any", "family: other"))
        w.local()
        (w.secrets / "proxy.client").write_text(TOKEN)
        (w.secrets / "proxy.client").chmod(0o600)
        w.steps(approved())
        code, out, err = w.launch("critic", "5")
        self.assertEqual(code, 0, out + err)
        text = (w.run_dir("critic-5-1") / "sandbox.sb").read_text()
        self.assertIn('(remote ip "localhost:8317")', text)
        self.assertIn("ANTHROPIC_BASE_URL", w.calls()[0]["env_keys"])

    def probe(self):
        if not sandbox.available():
            return "sandbox-exec is not available"
        try:
            done = subprocess.run([sandbox.SANDBOX_EXEC, "-p", "(version 1)(allow default)", "/usr/bin/true"],
                                  capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return "the sandbox-exec probe did not run"
        return "" if done.returncode == 0 else "the sandbox-exec probe failed"

    def test_sandbox_on(self):
        reason = self.probe()
        if reason:
            self.skipTest(reason)
        w = World(self, "sandboxed-harness")
        w.local(SANDBOX="on")
        w.steps(approved())
        code, out, err = w.launch("critic", "5")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(markers.parse(w.by(5, "critic")[-1]["body"]).verb, "APPROVED")

    def test_sandbox_on_denies_a_write_outside_the_worktree(self):
        reason = self.probe()
        if reason:
            self.skipTest(reason)
        w = World(self, "sandboxed-harness")
        w.local(SANDBOX="on")
        w.steps(result_step(CRITIC, approved()["result"], {"../escaped.txt": "out\n"}))
        code, out, err = w.launch("critic", "5")
        # The fake stops at the denied write, so the run ends without a result.
        self.assertEqual(code, 3, out + err)
        self.assertIn("Missing: a valid result (run failed (exit 1))\n", w.by(5, "critic")[0]["body"])
        self.assertEqual(list(w.tmp.rglob("escaped.txt")), [])

    def test_sandbox_off_twin_lets_the_write_through(self):
        w = World(self, "sandboxed-harness")
        w.steps(result_step(CRITIC, approved()["result"], {"../escaped.txt": "out\n"}))
        code, out, err = w.launch("critic", "5")
        self.assertEqual(code, 0, out + err)
        self.assertTrue((w.state / "worktrees" / "escaped.txt").is_file())

    def home(self, w):
        """A fixture home directory under the test's own temporary directory, set as HOME."""
        home = w.tmp / "home"
        (home / "notes").mkdir(parents=True)
        (home / "notes" / "plain.txt").write_text("plain\n")
        patch = mock.patch.dict(os.environ, {"HOME": str(home)})
        patch.start()
        self.addCleanup(patch.stop)
        return home

    def home_rule(self, w):
        """The one line of the run's profile that names `.ssh`."""
        text = (w.run_dir("critic-5-1") / "sandbox.sb").read_text()
        rule = [r for r in text.splitlines() if "/.ssh" in r]
        self.assertEqual(len(rule), 1)
        return rule[0]

    def test_sandbox_deny_reaches_the_profile(self):
        """Behaviour 6."""
        w = World(self)
        self.home(w)
        store = w.tmp / "store"
        w.local(SANDBOX_DENY=f"{store}, {w.tmp}/nowhere ,,")
        w.steps(approved())
        code, out, err = w.launch("critic", "5")
        self.assertEqual(code, 0, out + err)
        rule = self.home_rule(w)
        self.assertIn(f'(subpath "{store}")', rule)
        self.assertIn(f'(subpath "{w.tmp}/nowhere")', rule)

    def test_sandbox_deny_expands_home(self):
        """Behaviour 6: `~/` is the home directory, and an entry named twice appears once."""
        w = World(self)
        home = self.home(w)
        w.local(SANDBOX_DENY="~/.store,~/.store, ~/absent/x")
        w.steps(approved())
        code, out, err = w.launch("critic", "5")
        self.assertEqual(code, 0, out + err)
        rule = self.home_rule(w)
        self.assertTrue(rule.endswith(f'(subpath "{home}/.store") (subpath "{home}/absent/x"))'), rule)
        self.assertEqual(rule.count(f'"{home}/.store"'), 1)

    def test_without_sandbox_deny_twin(self):
        """Behaviour 6, clean twin."""
        w = World(self)
        home = self.home(w)
        w.steps(approved())
        code, out, err = w.launch("critic", "5")
        self.assertEqual(code, 0, out + err)
        text = (w.run_dir("critic-5-1") / "sandbox.sb").read_text()
        self.assertNotIn(str(w.tmp / "store"), text)
        self.assertNotIn(f"{w.tmp}/nowhere", text)
        self.assertNotIn(f"{home}/.store", text)
        self.assertIn(f'(subpath "{home}/.claude")', self.home_rule(w))

    def test_unusable_sandbox_deny_is_blocked(self):
        """Behaviour 7."""
        absolute = "setting SANDBOX_DENY holds a path that is not absolute"
        contains = "setting SANDBOX_DENY holds a path that contains the production, the worktree or the run directory"
        for value, message in (("relative/x", absolute), ("~other/x", absolute), (None, contains)):
            with self.subTest(value=value):
                w = World(self)
                self.home(w)
                w.local(SANDBOX_DENY=str(w.state) if value is None else value)
                w.steps(approved())
                code, out, err = w.launch("critic", "5")
                self.assertEqual(code, 3, out + err)
                body = w.by(5, "critic")[-1]["body"]
                self.assertTrue(body.startswith("[CRITIC] BLOCKED"), body)
                self.assertIn(f"Missing: a valid result ({message})\n", body)
                self.assertTrue(w.run_dir("critic-5-1").is_dir())
                self.assertFalse((w.run_dir("critic-5-1") / "sandbox.sb").exists())
                self.assertEqual(w.calls(), [])

    def probe_launch(self, **settings):
        """`probe.json` of a sandboxed launch of `critic` on 5 by `probing-harness`, and the paths it read."""
        reason = self.probe()
        if reason:
            self.skipTest(reason)
        w = World(self, "probing-harness")
        shutil.copy2(FIX / "probing-harness", w.harness / "probing-harness")
        home = self.home(w)
        store = home / ".store"
        store.mkdir()
        (store / "login.env").write_text("login\n")
        paths = {"store": str(store / "login.env"), "notes": str(home / "notes" / "plain.txt"),
                 "token": str(w.secrets / "inference.primary" / ACCOUNTS[0])}
        (w.harness / "probe-paths.json").write_text(json.dumps(sorted(paths.values())))
        w.local(SANDBOX="on", **{k: v.format(store=store) for k, v in settings.items()})
        w.steps(approved())
        code, out, err = w.launch("critic", "5")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(markers.parse(w.by(5, "critic")[-1]["body"]).verb, "APPROVED")
        tmp = w.run_dir("critic-5-1") / "tmp"
        found = json.loads((w.run_dir("critic-5-1") / "probe.json").read_text())
        self.assertEqual([found["TMPDIR"], found["gettempdir"], found["mkstemp"]], [os.path.realpath(tmp)] * 3)
        self.assertEqual(oct(os.stat(tmp).st_mode & 0o777), "0o700")
        return {name: found["paths"][path] for name, path in paths.items()}

    def test_worker_tmp_and_denied_store(self):
        """Behaviour 11."""
        self.assertEqual(self.probe_launch(SANDBOX_DENY="{store}"),
                         {"store": "PermissionError", "token": "PermissionError", "notes": "read"})

    def test_worker_tmp_without_sandbox_deny_twin(self):
        """Behaviour 11, clean twin."""
        self.assertEqual(self.probe_launch(), {"store": "read", "token": "PermissionError", "notes": "read"})


class Post(unittest.TestCase):
    """``finish.post`` called with nothing but ``p, gh, root, run, outcome`` (as w2-15 will), and the body rules."""

    def setUp(self):
        self.w = World(self)
        self.run_ = launch.prepare(self.w.p, self.w.gh, self.w.root, "critic", 5, now=NOW)

    def test_timeout_outcome(self):
        w = self.w
        comment = finish.post(w.p, w.gh, w.root, self.run_, harness.Outcome("failed", None, "timed out"))
        head = w.head("outline/OL-3-writer-1")
        self.assertEqual(comment["body"], "\n".join([
            "[CRITIC] BLOCKED", "", finish.LAUNCHER_SUMMARY, "",
            "Missing: a valid result (timed out)", "What was tried: launch 5-1",
            f"Resume from: PR #5 @ {head}", "Launch: 5-1", f"head {head} manifest none"]) + "\n")
        self.assertEqual(comment["role"], "critic")

    def test_values_are_one_line_and_cells_have_no_pipe(self):
        w = self.w
        result = {"marker": "APPROVED", "summary": "first\nsecond\r\nthird",
                  "evidence": [{"criterion": "a|b", "result": "ok\nfine", "command": "x | y"}],
                  "notes": [{"id": "I1", "severity": "Issue", "text": "minor\nthing", "evidence": "e"}]}
        comment = finish.post(w.p, w.gh, w.root, self.run_, harness.Outcome("ok", result, "result read"))
        lines = comment["body"].rstrip("\n").split("\n")
        self.assertEqual(lines[2], "first second third")
        self.assertIn("| a/b | ok fine | x / y |", lines)
        self.assertIn("I1 (Issue): minor thing Evidence: e", lines)

    def test_notes_blockers_first(self):
        w = self.w
        result = {"marker": "NOTES", "summary": "s", "notes": [
            {"id": "I1", "severity": "Issue", "text": "i", "evidence": "e"},
            {"id": "B2", "severity": "Blocker", "text": "b", "evidence": "e"}]}
        body = finish.post(w.p, w.gh, w.root, self.run_, harness.Outcome("ok", result, ""))["body"]
        lines = body.split("\n")
        self.assertLess(lines.index("B2 (Blocker): b Evidence: e"), lines.index("I1 (Issue): i Evidence: e"))


class Supervisor(unittest.TestCase):
    """Exit 2 when nothing can be posted; the error in supervisor.log holds no secret."""

    def test_nothing_posted(self):
        w = World(self)
        w.steps(approved())
        run = launch.prepare(w.p, w.gh, w.root, "critic", 5, now=NOW)
        with mock.patch.object(w.gh, "post", side_effect=github.GitHubError("post #5: exit 1")), \
                contextlib.redirect_stdout(io.StringIO()):
            code = finish.supervise(w.p, w.gh, w.root, run)
        self.assertEqual(code, 2)
        log = (run.dir / "supervisor.log").read_text()
        self.assertEqual(log, "launch 5-1: nothing posted: GitHubError: post #5: exit 1\n")
        self.assertFalse(run.worktree.exists())

    def test_harness_error_before_start_is_blocked(self):
        w = World(self)
        w.local(ACCOUNTS_CHECKER="")
        w.steps(approved())
        code, out, err = w.launch("critic", "5")
        self.assertEqual(code, 3, out + err)
        self.assertIn("Missing: a valid result (setting ACCOUNTS_CHECKER is empty)\n", w.by(5, "critic")[0]["body"])
        self.assertEqual(w.calls(), [])


class DeadRun(unittest.TestCase):
    """ISSUE-81: the supervisor's attempts follow steps.RUN_ATTEMPTS; a removal that fails is logged."""

    def test_removal_that_fails_is_logged(self):
        w = World(self)
        w.steps(approved())
        run = launch.prepare(w.p, w.gh, w.root, "critic", 5, now=NOW)
        out = io.StringIO()
        seeded = gitstate.GitError("worktree", 128, "seeded")
        with mock.patch.object(gitstate, "remove_worktree", side_effect=seeded), contextlib.redirect_stdout(out):
            code = finish.supervise(w.p, w.gh, w.root, run)
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue(), "posted [CRITIC] APPROVED for launch 5-1\n")
        self.assertEqual((run.dir / "supervisor.log").read_text(),
                         "launch 5-1: worktree not removed: git worktree: exit 128\n")

    def test_one_attempt_starts_one_worker(self):
        w = World(self)
        tokens = []
        real = harness.start

        def spy(argv, env, cwd, prompt_path, token_path, *rest, **kw):
            tokens.append(Path(token_path).name)
            return real(argv, env, cwd, prompt_path, token_path, *rest, **kw)

        usage = {"type": "result", "subtype": "error_during_execution", "is_error": True,
                 "result": "usage limit reached", "api_error_status": 429}
        w.steps({"match": WRITER, "envelope": usage, "exit": 1}, ready())
        with mock.patch.object(harness, "start", spy), mock.patch.object(steps, "RUN_ATTEMPTS", 1):
            code, out, err = w.launch("writer", "3")
        self.assertEqual(code, 3, out + err)
        self.assertEqual(tokens, ["acct-one"])
        self.assertEqual(sorted(json.loads((w.state / "accounts.json").read_text())), ["acct-one"])
        self.assertIn("Missing: a valid result (account out of usage (exit 1))\n", w.by(3, "writer")[0]["body"])


# -- the orchestrator role (ISSUE-89)

KEEPER = "You are the keeper"
PITCH_PULLS = (6, 7, 8)


def add_comment(w, number, id, created_at, role, body):
    path = w.github / "comments" / f"{number}.json"
    found = json.loads(path.read_text()) if path.is_file() else []
    path.write_text(json.dumps(found + [{"id": id, "created_at": created_at, "role": role, "body": body}],
                               indent=1, sort_keys=True) + "\n")


def three_rounds(w):
    """Pull #5 after the writer's READY: the critic's NOTES, then twice the writer's ADDRESSED and NOTES."""
    head = w.head("outline/OL-3-writer-1")
    for r in range(3):
        if r:
            add_comment(w, 5, 10 * r + 10, f"2026-11-10T1{r}:00:00Z", "writer", f"[WRITER] ADDRESSED\nRound {r}.\n")
        add_comment(w, 5, 10 * r + 11, f"2026-11-10T1{r}:30:00Z", "critic",
                    f"[CRITIC] NOTES\nB1 the dam is never shown, round {r + 1}\nreviewed head {head} manifest none\n")


def three_pitches(w):
    """Stage outline with `pitches` 3; ticket #3 with the open pulls 6, 7 and 8, each with the writer's READY,
    the critic's APPROVED and the weigher's verdict."""
    for n in PITCH_PULLS:
        ref = f"outline/OL-2-pitch-{n}"
        w.branch(ref)
        (w.github / "pulls" / f"{n}.json").write_text(json.dumps({
            "base_ref": "trunk", "body": "Ticket: #3\n", "created_at": "2026-11-04T09:00:00Z", "head_ref": ref,
            "labels": [], "merged_at": None, "number": n, "state": "open", "ticket": 3, "title": "Work for #3"},
            indent=1, sort_keys=True) + "\n")
        reviewed = f"reviewed head {w.head(ref)} manifest none\n"
        add_comment(w, n, 10 * n, "2026-11-05T09:00:00Z", "writer", f"[WRITER] READY\nPitch {n}.\n")
        add_comment(w, n, 10 * n + 1, "2026-11-05T09:10:00Z", "critic", f"[CRITIC] APPROVED\nFine.\n{reviewed}")
        add_comment(w, n, 10 * n + 2, "2026-11-05T09:20:00Z", "weigher", f"[WEIGHER] NO PREFERENCE\nEven.\n{reviewed}")
    path = w.root / ".factory" / "pipeline.json"
    assert '"pitches": 1' in path.read_text()
    path.write_text(path.read_text().replace('"pitches": 1', '"pitches": 3'))
    w.local()


class Judgment:
    """A keeper run through `launch.prepare` and `finish.supervise`."""

    def supervise(self, number, *steps):
        w = self.w
        self.refs = w.git("for-each-ref", "--format=%(refname) %(objectname)")
        self.pulls = len(w.gh.pulls("all"))
        w.steps(*steps)
        self.run_ = launch.prepare(w.p, w.gh, w.root, "keeper", number, now=NOW)
        self.assertTrue(self.run_.worktree.is_dir())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = finish.supervise(w.p, w.gh, w.root, self.run_)
        posted = [c for c in w.by(number, "keeper") if markers.parse(c["body"]).verb != "LAUNCH"]
        self.assertEqual(len(posted), 1, posted)
        self.assertEqual(len([c for c in w.posted("keeper") if " LAUNCH " not in c["body"].split("\n")[0]]), 1)
        return code, posted[0]["body"]

    def assert_left_nothing(self):
        w = self.w
        self.assertFalse(self.run_.worktree.exists())
        self.assertEqual(w.git("for-each-ref", "--format=%(refname) %(objectname)"), self.refs)
        self.assertEqual(len(w.gh.pulls("all")), self.pulls)
        self.assertEqual([e for e in w.log() if e["op"] in ("push", "open_pull")], [])

    def assert_blocked(self, body, missing, where, last):
        lines = body.rstrip("\n").split("\n")
        self.assertEqual(lines[0], "[KEEPER] BLOCKED")
        self.assertIn(f"Missing: {missing}", lines)
        self.assertIn(f"Resume from: {where}", lines)
        self.assertNotIn("Dropped", body)
        self.assertEqual(lines[-1], last)


class RulingPosted(Judgment, unittest.TestCase):
    """Behaviour 6: a ruling is posted on the pull request, by the role, and nothing is committed."""

    EVIDENCE = [{"criterion": "critic B1", "result": "acted on", "command": "bible/charter.md:3"}]

    def setUp(self):
        self.w = World(self)
        three_rounds(self.w)
        self.head = self.w.head("outline/OL-3-writer-1")
        self.manifest = gitstate.manifest(self.w.root, "trunk", self.head)

    def test_ruling(self):
        code, body = self.supervise(5, result_step(KEEPER, {"marker": "RULING", "summary": "The dam stays off screen.",
                                                            "evidence": self.EVIDENCE}))
        self.assertEqual(code, 0)
        self.assertEqual(body, "\n".join([
            "[KEEPER] RULING", "", "The dam stays off screen.", "",
            "| Criterion | Result | Command |", "|---|---|---|", "| critic B1 | acted on | bible/charter.md:3 |",
            "Launch: 5-1", f"head {self.head} manifest {self.manifest}"]) + "\n")
        self.assertEqual(markers.parse(body), markers.Marker("KEEPER", "RULING", ()))
        self.assertEqual(self.w.by(4, "keeper"), [])
        self.assert_left_nothing()

    def test_selected_on_a_pull_twin(self):
        code, body = self.supervise(5, result_step(KEEPER, {"marker": "SELECTED", "summary": "Pick this.",
                                                            "selected": 5, "evidence": self.EVIDENCE}))
        self.assertEqual(code, 3)
        self.assert_blocked(body, "a valid result (result.marker: SELECTED does not answer a launch on a pull request)",
                            f"PR #5 @ {self.head}", f"head {self.head} manifest {self.manifest}")
        self.assert_left_nothing()


class SelectionPosted(Judgment, unittest.TestCase):
    """Behaviour 7: a selection is posted on the ticket, and `tickets.selected` reads it."""

    def setUp(self):
        self.w = World(self)
        three_pitches(self.w)

    def selected(self, chosen=None, marker="SELECTED"):
        result = {"marker": marker, "summary": "Pitch seven holds best.",
                  "evidence": [{"criterion": f"#{n}", "result": "approved", "command": f"pr-{n}/"} for n in PITCH_PULLS]}
        if chosen is not None:
            result["selected"] = chosen
        return self.supervise(3, result_step(KEEPER, result))

    def test_selected(self):
        code, body = self.selected(7)
        self.assertEqual(code, 0)
        self.assertEqual(self.run_.meta["pulls"], [6, 7, 8])
        lines = body.rstrip("\n").split("\n")
        self.assertEqual(lines[0], "[KEEPER] SELECTED #7")
        self.assertEqual(lines[-2:], ["Launch: 3-1", "head none manifest none"])
        self.assertEqual(tickets.selected(self.w.state_now(), 3), 7)
        for n in PITCH_PULLS:
            self.assertEqual(self.w.by(n, "keeper"), [])
        self.assert_left_nothing()

    def test_selected_outside_the_pitches(self):
        code, body = self.selected(99)
        self.assertEqual(code, 3)
        self.assert_blocked(body, "a valid result (result.selected: #99 is not a pitch of this launch)",
                            "ticket #3", "head none manifest none")
        self.assertIsNone(tickets.selected(self.w.state_now(), 3))
        self.assert_left_nothing()

    def test_ruling_on_a_ticket(self):
        code, body = self.selected(marker="RULING")
        self.assertEqual(code, 3)
        self.assert_blocked(body, "a valid result (result.marker: RULING does not answer a launch on a ticket)",
                            "ticket #3", "head none manifest none")
        self.assertIsNone(tickets.selected(self.w.state_now(), 3))
        self.assert_left_nothing()


class RulingOnATicket(Judgment, unittest.TestCase):
    """ISSUE-242 behaviour 6: a ruling for a blocked maker on a ticket, a launch with no pitch exported, is
    posted on the ticket."""

    RULING = {"marker": "RULING", "summary": "Write the outline from the charter alone.",
              "evidence": [{"criterion": "writer BLOCKED", "result": "ruled", "command": "bible/charter.md:3"}]}

    def setUp(self):
        self.w = World(self)
        add_comment(self.w, 3, 30, "2026-11-10T09:00:00Z", "keeper", "[KEEPER] LAUNCH writer 3-1\n")
        add_comment(self.w, 3, 31, "2026-11-10T09:30:00Z", "writer", "[WRITER] BLOCKED\nMissing: the bible\n")

    def test_ruling(self):
        code, body = self.supervise(3, result_step(KEEPER, self.RULING))
        self.assertEqual(code, 0)
        self.assertEqual(self.run_.meta["pulls"], [])
        lines = body.rstrip("\n").split("\n")
        self.assertEqual(lines[0], "[KEEPER] RULING")
        self.assertEqual(lines[-2:], ["Launch: 3-1", "head none manifest none"])
        self.assertEqual(markers.parse(body), markers.Marker("KEEPER", "RULING", ()))
        self.assert_left_nothing()

    def test_ruling_on_a_run_with_pitches(self):
        # Seeded defect: the same result on a run that exported two pitches.
        w = self.w
        w.steps(result_step(KEEPER, self.RULING))
        run = launch.prepare(w.p, w.gh, w.root, "keeper", 3, now=NOW)
        run = run._replace(meta={**run.meta, "pulls": [6, 7]})
        with contextlib.redirect_stdout(io.StringIO()):
            code = finish.supervise(w.p, w.gh, w.root, run)
        self.assertEqual(code, 3)
        [body] = [c["body"] for c in w.by(3, "keeper") if markers.parse(c["body"]).verb != "LAUNCH"]
        self.assert_blocked(body, "a valid result (result.marker: RULING does not answer a launch on a ticket)",
                            "ticket #3", "head none manifest none")


class NoJudgment(Judgment, unittest.TestCase):
    """Behaviour 8: a harness that exits without a result posts the launcher's BLOCKED on the number given.
    Clean twins: RulingPosted.test_ruling and SelectionPosted.test_selected."""

    def setUp(self):
        self.w = World(self)

    def test_on_a_pull(self):
        three_rounds(self.w)
        head = self.w.head("outline/OL-3-writer-1")
        code, body = self.supervise(5)
        self.assertEqual(code, 3)
        self.assert_blocked(body, "a valid result (run failed (exit 3))", f"PR #5 @ {head}",
                            f"head {head} manifest {gitstate.manifest(self.w.root, 'trunk', head)}")
        self.assertIn("\n\n" + finish.LAUNCHER_SUMMARY + "\n\n", body)
        self.assert_left_nothing()

    def test_on_a_ticket(self):
        three_pitches(self.w)
        code, body = self.supervise(3)
        self.assertEqual(code, 3)
        self.assert_blocked(body, "a valid result (run failed (exit 3))", "ticket #3", "head none manifest none")
        self.assertIn("\n\n" + finish.LAUNCHER_SUMMARY + "\n\n", body)
        self.assert_left_nothing()



# -- a resource that is down (ISSUE-94)

from kit.lib import down  # noqa: E402

ENVELOPES = ROOT / "tests" / "fixtures" / "harness" / "envelopes"
LOGIN_ENDED = {"match": CRITIC, "stdout": (ENVELOPES / "login-ended-status.stdout").read_text(), "exit": 1}
PROXY_DOWN = {"match": CRITIC, "stdout": "", "stderr": (ENVELOPES / "proxy-down.stderr").read_text(), "exit": 1}
DOWN_TEXT = ["Launches that need this resource skip a stopped login and wait when none is left.",
             "To clear: renew the login or restart the proxy, then close this issue."]
STOPPED_LINE = ("The stopped account is named in stopped.json in the state directory of the machine that ran "
                "the launch.")


class ResourceDown(unittest.TestCase):
    """ISSUE-94, behaviours 1 to 5, 8 and 9: a dead login or proxy files one `resource:down` issue; a stopped
    account is skipped, and a launch waits when none is left."""

    def setUp(self):
        self.w = World(self)
        self.tokens = []
        real = harness.start

        def spy(argv, env, cwd, prompt_path, token_path, *rest, **kw):
            self.tokens.append(Path(token_path).name)
            return real(argv, env, cwd, prompt_path, token_path, *rest, **kw)

        patch = mock.patch.object(harness, "start", spy)
        patch.start()
        self.addCleanup(patch.stop)
        self.outputs = []

    def launch(self, *steps, role="critic", number="5"):
        self.w.steps(*steps)
        code, out, err = self.w.launch(role, number)
        self.outputs.append(err)
        return code, out, err

    def down_issues(self):
        return [i for i in self.w.gh.issues("open") if "resource:down" in i["labels"]]

    def one_down_issue(self):
        found = self.down_issues()
        self.assertEqual(len(found), 1, "open resource:down issues")
        return found[0]

    def creates(self):
        return [e for e in self.w.log() if e["op"] == "create_issue"]

    def stopped(self):
        path = self.w.state / "stopped.json"
        return json.loads(path.read_text()) if path.exists() else None

    def verbs(self):
        return [markers.parse(c["body"]).verb for c in self.w.by(5, "critic")]

    def other(self):
        """`critic` as `family: other`, set up as in Sandbox.test_family_other_reaches_the_proxy."""
        w = self.w
        w.local(PROXY_URL="http://localhost:8317", MODEL_OTHER="other-model-1")
        role = w.root / ".factory" / "roles" / "critic.md"
        role.write_text(role.read_text().replace("family: any", "family: other"))
        w.local()
        (w.secrets / "proxy.client").write_text(TOKEN)
        (w.secrets / "proxy.client").chmod(0o600)

    def snapshot(self):
        w = self.w
        files = {path.relative_to(w.github).as_posix(): path.read_bytes()
                 for path in sorted(w.github.rglob("*")) if path.is_file()}
        runs = sorted(path.relative_to(w.state).as_posix() for path in (w.state / "runs").rglob("*"))
        return files, w.git("for-each-ref", "--format=%(refname) %(objectname)"), runs

    def assert_refused(self, resource, number):
        before = self.snapshot()
        started = len(self.tokens)
        code, out, err = self.launch(approved())
        self.assertEqual((code, out, err), (1, "", f"launch: resource {resource} is down (#{number})\n"))
        self.assertEqual((len(self.tokens), self.w.calls()), (started, []))
        self.assertEqual(self.snapshot(), before)

    def assert_private(self):
        """Behaviour 9. The orchestrator's LAUNCH records name the worktree by design and are left out."""
        w = self.w
        texts = {"issue " + str(i["number"]): i["title"] + "\n" + i["body"] for i in w.gh.issues("all")
                 if "resource:down" in i["labels"]}
        texts["log.jsonl"] = "\n".join(json.dumps(e) for e in w.log()
                                       if not (e["op"] == "post" and " LAUNCH " in e["body"].split("\n")[0]))
        texts["stderr"] = "".join(self.outputs)
        for log in sorted(w.state.glob("runs/*/supervisor.log")):
            texts[str(log)] = log.read_text()
        for name, text in texts.items():
            for secret in (*ACCOUNTS, TOKEN, SECRETS_NAME, str(w.state), str(w.root), str(Path.home())):
                self.assertNotIn(secret, text, name)

    # -- behaviour 1

    def test_rotate_and_file(self):
        w = self.w
        code, out, err = self.launch(LOGIN_ENDED, approved())
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.verbs(), ["APPROVED"])
        self.assertEqual(self.tokens, ["acct-one", "acct-two"])
        issue = self.one_down_issue()
        self.assertEqual((issue["title"], issue["labels"]), ("[DOWN] inference.primary", ["resource:down"]))
        self.assertEqual(issue["body"], "\n".join([
            "Resource: inference.primary", "Cause: login expired or revoked", "Seen by: launch critic 5-1 on #5", "",
            *DOWN_TEXT, STOPPED_LINE]) + "\n")
        self.assertEqual(self.stopped(), {"acct-one": issue["number"]})
        self.assertFalse((w.state / "accounts.json").exists())
        code, out, err = self.launch(approved())
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.tokens, ["acct-one", "acct-two", "acct-two"])
        self.assertEqual(len(self.creates()), 1)
        self.assert_private()

    def test_rotate_and_file_clean_twin(self):
        code, out, err = self.launch(approved())
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.tokens, ["acct-one"])
        self.assertEqual((self.creates(), self.stopped()), ([], None))

    # -- behaviour 2

    def every_account_ended(self):
        code, out, err = self.launch(LOGIN_ENDED, dict(LOGIN_ENDED))
        self.assertEqual(code, 3, out + err)
        self.assertEqual(self.verbs(), ["BLOCKED"])
        self.assertIn("Missing: a valid result (login expired or revoked (exit 1))\n", self.w.by(5, "critic")[0]["body"])
        self.assertEqual(self.tokens, ["acct-one", "acct-two"])
        issue = self.one_down_issue()
        self.assertEqual(len(self.creates()), 1)
        self.assertEqual(self.stopped(), {"acct-one": issue["number"], "acct-two": issue["number"]})
        return issue["number"]

    def test_every_account_ended(self):
        number = self.every_account_ended()
        self.assert_refused("inference.primary", number)
        self.assert_private()

    def test_every_account_stopped_after_prepare(self):
        """Ruling 7: no account can be picked and every one is stopped; nothing more is filed."""
        w = self.w
        w.steps(approved())
        run = launch.prepare(w.p, w.gh, w.root, "critic", 5, now=NOW)
        for name in ACCOUNTS:
            down.report(w.p, w.gh, down.PRIMARY, "login expired or revoked", "a test", name)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(finish.supervise(w.p, w.gh, w.root, run), 3)
        self.assertIn("Missing: a valid result (every account's login has ended)\n", w.by(5, "critic")[-1]["body"])
        self.assertEqual((w.calls(), len(self.creates())), ([], 1))

    # -- behaviour 3

    def test_the_proxy(self):
        self.other()
        code, out, err = self.launch(PROXY_DOWN)
        self.assertEqual(code, 3, out + err)
        self.assertIn("Missing: a valid result (model proxy unreachable (exit 1))\n", self.w.by(5, "critic")[0]["body"])
        issue = self.one_down_issue()
        self.assertEqual((issue["title"], issue["labels"]), ("[DOWN] inference.other", ["resource:down"]))
        self.assertEqual(issue["body"], "\n".join([
            "Resource: inference.other", "Cause: model proxy unreachable", "Seen by: launch critic 5-1 on #5", "",
            *DOWN_TEXT]) + "\n")
        self.assertIsNone(self.stopped())
        self.assert_refused("inference.other", issue["number"])
        code, out, err = self.launch(ready(), role="writer", number="3")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(self.creates()), 1)
        self.assert_private()

    def test_login_ended_in_slot_other(self):
        self.other()
        code, out, err = self.launch(LOGIN_ENDED)
        self.assertEqual(code, 3, out + err)
        issue = self.one_down_issue()
        self.assertEqual(issue["title"], "[DOWN] inference.other")
        self.assertIn("\nCause: login expired or revoked\n", issue["body"])
        self.assertIsNone(self.stopped())

    def test_login_ended_in_slot_other_while_its_issue_is_open(self):
        w = self.w
        self.other()
        w.steps(LOGIN_ENDED)
        run = launch.prepare(w.p, w.gh, w.root, "critic", 5, now=NOW)
        number = down.report(w.p, w.gh, down.OTHER, "model proxy unreachable", "a test")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(finish.supervise(w.p, w.gh, w.root, run), 3)
        self.assertEqual([i["number"] for i in self.down_issues()], [number])
        self.assertEqual(len(self.creates()), 1)

    # -- behaviour 4

    def test_nothing_filed(self):
        usage = {"match": CRITIC, "stdout": (ENVELOPES / "out-of-usage-status.stdout").read_text(), "exit": 1}
        failed = {"match": CRITIC, "stdout": (ENVELOPES / "failed-error.stdout").read_text(), "exit": 1}
        for steps_, code in (([PROXY_DOWN], 3), ([usage, approved()], 0), ([failed], 3)):
            with self.subTest(step=steps_[0]["stdout"][:40]):
                w = self.w = World(self)
                found, out, err = self.launch(*steps_)
                self.assertEqual(found, code, out + err)
                self.assertEqual((self.creates(), self.stopped()), ([], None))
                self.assertFalse((w.state / "down.lock").exists())
        self.assertIn("Missing: a valid result (run failed (exit 1))\n", self.w.by(5, "critic")[0]["body"])

    def test_proxy_down_in_slot_checker_is_blocked_as_on_main(self):
        code, out, err = self.launch(PROXY_DOWN)
        self.assertEqual(code, 3, out + err)
        self.assertIn("Missing: a valid result (model proxy unreachable (exit 1))\n", self.w.by(5, "critic")[0]["body"])
        self.assertEqual(self.tokens, ["acct-one"])

    # -- behaviour 5

    def test_closed(self):
        w = self.w
        old = self.every_account_ended()
        w.gh.close_issue(old)
        code, out, err = self.launch(LOGIN_ENDED, approved())
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.tokens, ["acct-one", "acct-two", "acct-one", "acct-two"])
        issue = self.one_down_issue()
        self.assertNotEqual(issue["number"], old)
        self.assertEqual(len(self.creates()), 2)
        self.assertEqual(self.stopped(), {"acct-one": issue["number"]})

    # -- behaviour 8

    def test_a_filing_that_fails(self):
        w = self.w
        seeded = github.GitHubError("create_issue: exit 1")
        with mock.patch.object(github.Recorded, "create_issue", side_effect=seeded):
            code, out, err = self.launch(LOGIN_ENDED, dict(LOGIN_ENDED))
        self.assertEqual(code, 3, out + err)
        self.assertEqual(self.verbs(), ["BLOCKED"])
        self.assertIn("Missing: a valid result (login expired or revoked (exit 1))\n", w.by(5, "critic")[0]["body"])
        self.assertEqual((w.run_dir("critic-5-1") / "supervisor.log").read_text(),
                         "launch 5-1: resource inference.primary not reported: create_issue: exit 1\n")
        self.assertEqual(self.tokens, ["acct-one"])
        self.assertIsNone(self.stopped())
        self.assertEqual(self.down_issues(), [])
        self.assert_private()


if __name__ == "__main__":
    unittest.main()
