"""Tests for kit/lib/launch.py.

The production is tests/fixtures/launch/production/, made a git repository on branch `trunk` in a
temporary directory with one branch per recorded pull. GitHub is a copy of tests/fixtures/launch/github/:
ticket #5 (beats, ep01) is ready for its maker's first launch; ticket #6 (beats, ep02) carries that
maker's first LAUNCH and has the open pull #7 with a READY, a [LINT] FAIL and a reviewer's NOTES.
A comment body's ``{head:N}`` stands for the head commit of pull N's branch, filled in when it is copied.
"""

import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import gitstate, github, launch, ledger, lint, production  # noqa: E402
from kit.lib.launch import LaunchError  # noqa: E402
from kit.lib import prompt  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "launch"
PRODUCTION = FIX / "production"
GITHUB = FIX / "github"
FAKE_HARNESS = ROOT / "tests" / "fixtures" / "harness" / "fake-harness"
HEAD = re.compile(r"\{head:(\d+)\}")
NOW = "2026-11-11T12:00:00Z"
SEED = "quillfish-seed-5c1e"
RUN_KEYS = {"id", "role", "kind", "actor", "number", "ticket", "pull", "room", "stage", "unit", "branch",
            "worktree", "blind", "base_ref", "base_sha", "head_sha", "model", "family", "harness_version",
            "prompt_sha256", "started_at", "launch_comment"}
PLACEHOLDER = re.compile(r"\{\{[A-Z][A-Z0-9_]*\}\}")


class World:
    """A production repository on branch `trunk`, a recorded GitHub directory and a state directory."""

    def __init__(self, test):
        patch = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                                             "FACTORY_NOW": NOW})
        patch.start()
        test.addCleanup(patch.stop)
        self.tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, self.tmp)
        self.root = self.tmp / "show"
        shutil.copytree(PRODUCTION, self.root)
        self.git("init", "-q", "-b", "trunk")
        self.commit("the production")
        self.state = self.tmp / "state"
        self.dir = self.tmp / "github"
        shutil.copytree(GITHUB, self.dir)
        for path in sorted((self.dir / "pulls").glob("*.json")):
            self.branch(json.loads(path.read_text())["head_ref"])
        for path in sorted((self.dir / "comments").glob("*.json")):
            path.write_text(self.heads(path.read_text()))
        self.gh = github.Recorded(self.dir)
        self.local()

    def local(self, **extra):
        """Write production.local.env with the test settings plus `extra`, and load the production."""
        values = {"STATE_DIR": str(self.state), "HARNESS": str(FAKE_HARNESS), "MODEL_MAKER": "maker-model-1",
                  "MODEL_CHECKER": "checker-model-1", "SECRETS_DIR": "/nowhere/" + SEED,
                  "ACCOUNTS_MAKER": SEED, **extra}
        (self.root / "production.local.env").write_text("".join(f"{k}={v}\n" for k, v in values.items()))
        self.p = production.load(self.root)

    def git(self, *args):
        return gitstate.git(self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", *args)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def branch(self, ref):
        """A branch `ref` with one commit on top of `trunk`."""
        self.git("checkout", "-q", "-b", ref, "trunk")
        path = self.root / "work" / (ref.replace("/", "-") + ".md")
        path.parent.mkdir(exist_ok=True)
        path.write_text(ref + "\n")
        self.commit("write " + ref)
        self.git("checkout", "-q", "trunk")

    def head(self, ref):
        return gitstate.head(self.root, ref)

    def heads(self, text):
        def head(m):
            return self.head(json.loads((self.dir / "pulls" / f"{m.group(1)}.json").read_text())["head_ref"])
        return HEAD.sub(head, text)

    def _read(self, rel):
        return json.loads((self.dir / rel).read_text())

    def _write(self, rel, data):
        path = self.dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")

    def comment(self, number, id, created_at, role, body):
        rel = f"comments/{number}.json"
        found = self._read(rel) if (self.dir / rel).is_file() else []
        self._write(rel, found + [{"id": id, "created_at": created_at, "role": role, "body": self.heads(body)}])

    def set_issue(self, number, **fields):
        rel = f"issues/{number}.json"
        self._write(rel, {**(self._read(rel) if (self.dir / rel).is_file() else {}), **fields, "number": number})

    def set_pull(self, number, **fields):
        rel = f"pulls/{number}.json"
        record = {**(self._read(rel) if (self.dir / rel).is_file() else {}), **fields, "number": number}
        if not self.git("branch", "--list", record["head_ref"]).strip():
            self.branch(record["head_ref"])
        self._write(rel, record)

    def worktrees(self):
        return gitstate.git(self.root, "worktree", "list", "--porcelain")

    def snapshot(self):
        """What a refused launch must leave as it was."""
        comments = {p.name: p.read_text() for p in sorted((self.dir / "comments").glob("*.json"))}
        return {"comments": comments, "worktrees": self.worktrees(),
                "branches": self.git("for-each-ref", "--format=%(refname) %(objectname)", "refs/heads/")}

    def prepare(self, role, number, **kw):
        return launch.prepare(self.p, self.gh, self.root, role, number, now=NOW, **kw)


def fenced(body):
    """The text inside the comment's fence, and the fence."""
    lines = body.split("\n")
    start = next(i for i, line in enumerate(lines) if line.startswith("````"))
    fence = lines[start]
    end = len(lines) - 1 - lines[::-1].index(fence)
    return "\n".join(lines[start + 1:end]) + "\n", fence


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class CleanMakerLaunch(unittest.TestCase):
    """Criterion 1."""

    def setUp(self):
        self.w = World(self)
        self.before = self.w.gh.comments(5)
        self.run_ = self.w.prepare("drafter", 5)

    def test_branch_and_worktree(self):
        w, run = self.w, self.run_
        self.assertEqual(run.worktree, w.state / "worktrees" / "drafter-5-1")
        self.assertEqual(run.dir, w.state / "runs" / "drafter-5-1")
        self.assertEqual(w.head("beats/BT-1-drafter-1"), w.head("trunk"))
        self.assertEqual(gitstate.git(run.worktree, "symbolic-ref", "HEAD").strip(), "refs/heads/beats/BT-1-drafter-1")

    def test_run_json(self):
        w, meta = self.w, self.run_.meta
        self.assertEqual(set(meta), RUN_KEYS)
        self.assertEqual(launch.load_run(self.run_.dir), self.run_)
        text = (self.run_.dir / "run.json").read_text()
        self.assertEqual(text, json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n")
        comment = w.gh.comments(5)[-1]
        self.assertEqual(meta, {
            "id": "5-1", "role": "drafter", "kind": "maker", "actor": "DRAFTER", "number": 5, "ticket": 5,
            "pull": None, "room": "story", "stage": "beats", "unit": "ep01", "branch": "beats/BT-1-drafter-1",
            "worktree": str(w.state / "worktrees" / "drafter-5-1"), "blind": False, "base_ref": "trunk",
            "base_sha": w.head("trunk"), "head_sha": None, "model": "maker-model-1", "family": "any",
            "harness_version": "0.0.0 (fake)", "prompt_sha256": meta["prompt_sha256"], "started_at": NOW,
            "launch_comment": comment["id"]})

    def test_files_and_modes(self):
        run_dir = self.run_.dir
        self.assertEqual(sorted(p.name for p in run_dir.iterdir()),
                         ["config", "mcp.json", "prompt.md", "prompt.sha256", "run.json"])
        self.assertEqual(list((run_dir / "config").iterdir()), [])
        self.assertEqual(json.loads((run_dir / "mcp.json").read_text()), {"mcpServers": {}})
        for path in (self.w.state, self.w.state / "runs", self.w.state / "worktrees", run_dir, run_dir / "config"):
            self.assertEqual(mode(path), 0o700, path)
        for name in ("prompt.md", "prompt.sha256", "mcp.json", "run.json"):
            self.assertEqual(mode(run_dir / name), 0o600, name)

    def test_hash_and_comment(self):
        run_dir = self.run_.dir
        data = (run_dir / "prompt.md").read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        self.assertEqual((run_dir / "prompt.sha256").read_text(), digest + "\n")
        self.assertEqual(self.run_.meta["prompt_sha256"], digest)
        comments = self.w.gh.comments(5)
        self.assertEqual(len(comments), len(self.before) + 1)
        comment = comments[-1]
        self.assertEqual(comment["role"], "showlead")
        lines = comment["body"].split("\n")
        self.assertEqual(lines[:8], [
            "[SHOWLEAD] LAUNCH drafter 5-1", f"prompt sha256: {digest}", "model: maker-model-1", "family: any",
            "harness: 0.0.0 (fake)", f"base: {self.w.head('trunk')}", "head: none", ""])
        text, fence = fenced(comment["body"])
        self.assertEqual(fence, "````")
        self.assertEqual(text.encode("utf-8"), data)

    def test_prompt(self):
        text = (self.run_.dir / "prompt.md").read_text()
        self.assertIsNone(PLACEHOLDER.search(text))
        self.assertIn("You are the drafter for example/tide-show.\n", text)
        self.assertIn("Ticket: #5 [BT-1] Beats for ep01\n\nLay out the beats of episode one", text)
        self.assertIn(f"Worktree: {self.w.state / 'worktrees' / 'drafter-5-1'}\n", text)
        self.assertIn("Branch: beats/BT-1-drafter-1\n", text)
        self.assertIn("Task: Do the stage beats for unit ep01.\n", text)
        self.assertIn("The orchestrator rules", text)
        self.assertIn("Every scene ends on the tide.", text)

    def test_cleanup_removes_the_worktree(self):
        launch.cleanup(self.w.root, self.run_)
        self.assertFalse(self.run_.worktree.exists())
        self.assertNotIn("drafter-5-1", self.w.worktrees())

    def test_second_maker_launch_is_refused(self):
        # Ticket #5 now has a launch and no pull: its one pitch is in use.
        with self.assertRaisesRegex(LaunchError, r"^ticket #5 has no free pitch; give the pull request number$"):
            self.w.prepare("drafter", 5)


class Refusals(unittest.TestCase):
    """Criterion 2: each check of Behaviour 2 on the clean fixture with exactly one defect."""

    def setUp(self):
        self.w = World(self)

    def refused(self, message, role, number, **kw):
        before = self.w.snapshot()
        with self.assertRaises(LaunchError) as caught:
            self.w.prepare(role, number, **kw)
        self.assertEqual(str(caught.exception), message)
        self.assertEqual(self.w.snapshot(), before)
        self.assertFalse(self.w.state.exists())

    def test_clean_twin(self):
        self.w.prepare("drafter", 5)
        self.w.prepare("reviewer", 7)

    def test_frozen(self):
        self.w.set_issue(20, title="Freeze the show", body="", state="open",
                         labels=["factory:freeze"], created_at="2026-11-10T00:00:00Z")
        self.refused("production is frozen", "drafter", 5)

    def test_no_role(self):
        self.refused("no role ghost", "ghost", 5)

    def test_orchestrator_with_no_judgment_due(self):
        self.refused("no judgment is due on #5", "showlead", 5)

    def test_operator(self):
        self.refused("stage beats does not name runner", "runner", 5)

    def test_analyst_on_a_pull(self):
        self.refused("an analyst is launched on a ticket; #7 is a pull request", "reader", 7)

    def test_analyst_not_posting_as_analyst(self):
        path = self.w.root / ".factory" / "roles" / "reader.md"
        path.write_text(path.read_text().replace("actor: ANALYST\n", ""))
        self.w.local()
        self.refused("reader posts as READER; an analyst posts as ANALYST", "reader", 5)

    def test_analyst_of_another_room(self):
        path = self.w.root / ".factory" / "roles" / "reader.md"
        path.write_text(path.read_text().replace("room: story\n", "room: distribution\n"))
        self.w.local()
        self.refused("reader is not a role of room story", "reader", 5)

    def test_analyst_already_running(self):
        self.w.comment(5, 30, "2026-11-10T10:00:00Z", "showlead", "[SHOWLEAD] LAUNCH reader 5-1\n")
        self.refused("reader is already running on #5", "reader", 5)

    def test_not_tracked(self):
        self.refused("#99 is not a tracked ticket or a pull request of one", "drafter", 99)

    def test_closed_ticket(self):
        self.w.set_issue(5, state="closed")
        self.refused("ticket #5 is closed", "drafter", 5)

    def test_locked_ticket(self):
        self.w.set_pull(8, title="Work for #5", body="Ticket: #5\n", state="merged", labels=[],
                        head_ref="beats/BT-1-elsewhere", base_ref="trunk", ticket=5,
                        created_at="2026-11-02T09:00:00Z", merged_at="2026-11-03T09:00:00Z")
        self.refused("ticket #5 is locked", "drafter", 5)

    def test_hold(self):
        self.w.set_issue(5, labels=["hold", "room:story", "stage:beats", "tracked", "unit:ep01"])
        self.refused("ticket #5 is on hold", "drafter", 5)

    def test_pull_not_open(self):
        self.w.set_pull(8, title="Work for #6", body="Ticket: #6\n", state="closed", labels=[],
                        head_ref="beats/BT-2-dropped", base_ref="trunk", ticket=6,
                        created_at="2026-11-02T09:00:00Z", merged_at=None)
        self.refused("pull request #8 is not open", "reviewer", 8)

    def test_stage_does_not_name_role(self):
        path = self.w.root / ".factory" / "pipeline.json"
        path.write_text(path.read_text().replace('"judge": "assessor"', '"judge": ""'))
        self.w.local()
        self.refused("stage beats does not name assessor", "assessor", 7)

    def test_upstream_not_locked(self):
        self.w.set_pull(2, state="closed", merged_at=None)
        self.refused("upstream stage premise is not locked for ep01", "drafter", 5)

    def test_checker_given_a_ticket(self):
        self.refused("give the pull request number", "reviewer", 6)

    def test_judge_given_a_ticket(self):
        self.refused("give the pull request number", "assessor", 6)

    def test_maker_given_a_ticket_with_no_free_pitch(self):
        self.refused("ticket #6 has no free pitch; give the pull request number", "drafter", 6)

    def gateway_key(self):
        secrets = self.w.tmp / "secrets"
        secrets.mkdir()
        (secrets / "knowledge.fermi").write_text("fermi-key-" + SEED + "\n")
        (secrets / "knowledge.fermi").chmod(0o600)
        return {"GATEWAY": "on", "SECRETS_DIR": str(secrets), "FERMI_URL": "https://fermi.invalid/mcp"}

    def test_gateway_on(self):
        """Gateway behaviour 11: the knowledge gateway's checks refuse a launch with nothing created."""
        prefix = "the knowledge gateway is not usable: "
        self.w.local(GATEWAY="on")
        self.refused(prefix + "setting FERMI_URL is empty; secret knowledge.fermi: missing", "drafter", 5)
        good = self.gateway_key()
        self.w.local(**{**good, "FERMI_URL": "http://example.invalid/mcp"})
        self.refused(prefix + "setting FERMI_URL is not an https URL", "drafter", 5)
        self.w.local(**{**good, "REPO": ""})
        self.refused(prefix + "setting REPO is empty", "drafter", 5)

    def test_gateway_on_twin(self):
        self.w.local(**self.gateway_key())
        run = self.w.prepare("drafter", 5)
        self.assertEqual(json.loads((run.dir / "mcp.json").read_text()), {"mcpServers": {}})

    def test_already_running(self):
        self.w.comment(7, 30, "2026-11-10T10:00:00Z", "showlead", "[SHOWLEAD] LAUNCH reviewer 7-1\n")
        self.refused("reviewer is already running on #7", "reviewer", 7)
        # The twin: a later marker of the reviewer's actor ends that run.
        self.w.comment(7, 31, "2026-11-10T11:00:00Z", "reviewer", "[REVIEWER] BLOCKED\nno access\n")
        self.assertEqual(self.w.prepare("reviewer", 7).meta["id"], "7-2")

    def test_run_already_exists(self):
        (self.w.state / "runs" / "drafter-5-1").mkdir(parents=True)
        before = self.w.snapshot()
        with self.assertRaisesRegex(LaunchError, r"^run drafter-5-1 already exists$"):
            self.w.prepare("drafter", 5)
        self.assertEqual(self.w.snapshot(), before)
        self.assertFalse((self.w.state / "worktrees").exists())

    def test_branch_already_exists(self):
        self.w.git("branch", "beats/BT-1-drafter-1", "trunk")
        self.refused("branch beats/BT-1-drafter-1 already exists", "drafter", 5)

    def test_pair_dir_only_for_a_judge(self):
        self.refused("a pair directory is only for a judge", "reviewer", 7, pair_dir=self.w.tmp)

    def test_pair_dir_must_exist(self):
        self.refused("the pair directory is not an existing directory", "assessor", 7,
                     pair_dir=self.w.tmp / "missing")

    def test_harness_error(self):
        self.w.local(MODEL_MAKER="")
        self.refused("setting MODEL_MAKER is empty", "drafter", 5)


class AnalystLaunch(unittest.TestCase):
    """Behaviour 1 and 2: an analyst is launched on a ticket, in a detached checkout of the base."""

    def setUp(self):
        self.w = World(self)

    def test_analyst_launch(self):
        w = self.w
        run = w.prepare("reader", 5)
        meta = run.meta
        self.assertEqual(meta["id"], "5-1")
        self.assertEqual(meta["kind"], "analyst")
        self.assertEqual(meta["actor"], "ANALYST")
        self.assertIsNone(meta["pull"])
        self.assertIsNone(meta["branch"])
        self.assertIsNone(meta["head_sha"])
        self.assertEqual(json.loads((run.dir / "run.json").read_text()), meta)
        self.assertEqual(run.worktree, w.state / "worktrees" / "reader-5-1")
        self.assertEqual(gitstate.head(run.worktree), w.head("trunk"))
        self.assertEqual(gitstate.git(run.worktree, "rev-parse", "--abbrev-ref", "HEAD").strip(), "HEAD")
        body = w.gh.comments(5)[-1]["body"]
        lines = body.split("\n")
        self.assertEqual(lines[0], "[SHOWLEAD] LAUNCH reader 5-1")
        self.assertIn("head: none", lines[:8])
        text = (run.dir / "prompt.md").read_text()
        self.assertIn("Task: Report the symptoms ticket #5 asks for, for unit ep01. Write no file.\n", text)
        self.assertIsNone(PLACEHOLDER.search(text))

    def test_a_maker_still_has_its_pitch(self):
        self.w.prepare("reader", 5)
        self.assertEqual(self.w.prepare("drafter", 5).meta["id"], "5-1")

    def test_a_ticket_with_no_free_pitch(self):
        self.assertEqual(self.w.prepare("reader", 6).meta["id"], "6-1")


class CheckerLaunch(unittest.TestCase):
    """Criterion 3."""

    def test_checker(self):
        w = World(self)
        run = w.prepare("reviewer", 7)
        head = w.head("beats/BT-2-drafter-1")
        self.assertEqual(gitstate.head(run.worktree), head)
        self.assertEqual(gitstate.git(run.worktree, "rev-parse", "--abbrev-ref", "HEAD").strip(), "HEAD")
        text = (run.dir / "prompt.md").read_text()
        self.assertIn("Pull request: 7\n", text)
        self.assertIn(f"Task: Review pull request #7 at head {head}.\n", text)
        self.assertTrue(text.endswith("\n\n## Maker's report\n\n[DRAFTER] READY\nMaker report: kelp-lantern-report, "
                                      "the beats are in episodes/ep02/beats.md.\n"), text)
        comment = w.gh.comments(7)[-1]
        self.assertEqual(comment["role"], "showlead")
        self.assertTrue(comment["body"].startswith("[SHOWLEAD] LAUNCH reviewer 7-1\n"))
        self.assertIn(f"\nhead: {head}\n", comment["body"])
        self.assertIn("\nfamily: same\n", comment["body"])
        self.assertEqual(fenced(comment["body"])[0], text)
        self.assertEqual((run.meta["pull"], run.meta["branch"], run.meta["head_sha"]), (7, None, head))


class JudgeLaunch(unittest.TestCase):
    """Criterion 4."""

    def setUp(self):
        self.w = World(self)
        self.bodies = [c["body"] for n in (5, 6, 7) for c in self.w.gh.comments(n)]

    def assert_no_comment(self, text):
        self.assertNotIn("kelp-lantern-report", text)
        for body in self.bodies:
            for line in body.splitlines():
                self.assertNotIn(line, text)

    def test_judge(self):
        run = self.w.prepare("assessor", 7)
        text = (run.dir / "prompt.md").read_text()
        self.assert_no_comment(text)
        self.assertIn("Task: Judge pull request #7.\n", text)
        self.assertIn("Pull request: 7\n", text)
        self.assertFalse(run.meta["blind"])
        launch.cleanup(self.w.root, run)
        self.assertFalse(run.worktree.exists())

    def test_blind_judge(self):
        pair = self.w.tmp / "pair"
        pair.mkdir()
        (pair / "A.md").write_text("one\n")
        before = self.w.worktrees()
        run = self.w.prepare("assessor", 7, pair_dir=pair)
        text = (run.dir / "prompt.md").read_text()
        self.assert_no_comment(text)
        self.assertNotIn(self.w.head("beats/BT-2-drafter-1"), text)
        self.assertNotIn("beats/BT-2-drafter-1", text)
        self.assertNotIn("Work for #6", text)
        self.assertIn(f"Worktree: {pair}\n", text)
        self.assertEqual(self.w.worktrees(), before)
        self.assertFalse((self.w.state / "worktrees" / "assessor-7-1").exists())
        self.assertTrue(run.meta["blind"])
        self.assertEqual(launch.load_run(run.dir).meta["blind"], True)
        launch.cleanup(self.w.root, run)
        self.assertEqual((pair / "A.md").read_text(), "one\n")


class MakerRelaunch(unittest.TestCase):
    """Criterion 5."""

    BRANCH = "beats/BT-2-drafter-1"

    def setUp(self):
        self.w = World(self)

    def test_relaunch(self):
        w = self.w
        earlier = w.state / "worktrees" / "drafter-6-1"
        earlier.parent.mkdir(parents=True)
        gitstate.add_worktree(w.root, earlier, self.BRANCH, branch=self.BRANCH)
        run = w.prepare("drafter", 7)
        self.assertEqual((run.meta["id"], run.meta["branch"]), ("7-2", self.BRANCH))
        self.assertFalse(earlier.exists())
        self.assertNotIn("drafter-6-1", w.worktrees())
        self.assertEqual(gitstate.git(run.worktree, "symbolic-ref", "HEAD").strip(), "refs/heads/" + self.BRANCH)
        self.assertEqual(gitstate.head(run.worktree), w.head(self.BRANCH))
        text = (run.dir / "prompt.md").read_text()
        self.assertIn(f"Branch: {self.BRANCH}\n", text)
        lint_body = w.gh.comments(7)[1]["body"].rstrip("\n")
        notes_body = w.gh.comments(7)[2]["body"].rstrip("\n")
        self.assertIn(f"Task: Address every open note on pull request #7.\n\n{lint_body}\n\n{notes_body}\n", text)
        comment = w.gh.comments(7)[-1]
        self.assertTrue(comment["body"].startswith("[SHOWLEAD] LAUNCH drafter 7-2\n"))
        self.assertEqual(run.meta["launch_comment"], comment["id"])

    def test_branch_checked_out_elsewhere(self):
        w = self.w
        elsewhere = w.tmp / "elsewhere"
        gitstate.add_worktree(w.root, elsewhere, self.BRANCH, branch=self.BRANCH)
        before = w.snapshot()
        with self.assertRaisesRegex(LaunchError, r"^worktree: branch beats/BT-2-drafter-1 is checked out outside the run "
                                                 r"worktrees$"):
            w.prepare("drafter", 7)
        self.assertEqual(w.snapshot(), before)
        self.assertEqual(gitstate.git(elsewhere, "symbolic-ref", "HEAD").strip(), "refs/heads/" + self.BRANCH)
        self.assertFalse(w.state.exists())


class OperatorLaunch(unittest.TestCase):
    """ISSUE-96: the operator's two launches on a pull request with a request, told apart by its jobs.

    The stage `beats` is made ``["drafter", "fetcher"]`` with the operator tests/fixtures/jobs/fetcher.md;
    the pull #7 of ticket #6 gets the request episodes/ep02/requests/r1.json."""

    BRANCH = "beats/BT-2-drafter-1"
    REQUEST = "episodes/ep02/requests/r1.json"
    JOB = "jobs/episodes/ep02/requests/r1.json"
    REQUEST_TEXT = '{"for": "beat 3", "prompt": "the harbour at flood", "refs": []}\n'

    def setUp(self):
        w = self.w = World(self)
        shutil.copy(ROOT / "tests" / "fixtures" / "jobs" / "fetcher.md", w.root / ".factory" / "roles" / "fetcher.md")
        path = w.root / ".factory" / "pipeline.json"
        entries = json.loads(path.read_text())
        for entry in entries:
            if entry["stage"] == "beats":
                entry["maker"] = ["drafter", "fetcher"]
        path.write_text(json.dumps(entries, indent=1) + "\n")
        w.commit("an operator at beats")
        w.local()

    def on_branch(self, files):
        w = self.w
        w.git("checkout", "-q", self.BRANCH)
        for rel, text in files.items():
            (w.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (w.root / rel).write_text(text)
        w.commit("files on the pull")
        w.git("checkout", "-q", "trunk")

    def job(self, **changes):
        job = {"caller": "fetcher", "expected": {"compute_min": 5, "usd": 1},
               "inputs": [{"path": self.REQUEST, "sha256": hashlib.sha256(self.REQUEST_TEXT.encode()).hexdigest()}],
               "ticket": 6, "tool": "renderbox", **changes}
        self.on_branch({self.JOB: json.dumps(job, indent=1)})
        return hashlib.sha256(json.dumps(job, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def ledger_row(self, kind, digest, verdict, usd="1", compute_min="5"):
        w = self.w
        path = w.root / ledger.PATH
        text = path.read_text() if path.is_file() else ledger.HEADER + "\n"
        path.parent.mkdir(exist_ok=True)
        path.write_text(text + ledger.line({
            "time": NOW, "kind": kind, "hash": digest, "ticket": 6, "episode": "ep02", "tool": "renderbox",
            "usd": usd, "compute_min": compute_min, "caller": "fetcher", "verdict": verdict, "cap": "-"}) + "\n")
        w.commit(f"ledger: {kind} {digest[:12]}")

    def passed(self, digest, id=50):
        self.w.comment(6, id, "2026-11-10T10:00:00Z", "program:gate",
                       f"[PRODUCER] GATE PASS\ncovers: job {digest}\ncap: -  reason: within caps\n")
        self.ledger_row("job", digest, "PASS")

    def refused(self, message):
        w = self.w
        before = w.snapshot()
        with self.assertRaises(LaunchError) as caught:
            w.prepare("fetcher", 7)
        self.assertEqual(str(caught.exception), message)
        self.assertEqual(w.snapshot(), before)
        self.assertFalse(w.state.exists())

    def prompt(self, run):
        return (run.dir / "prompt.md").read_text()

    def test_the_writing_launch(self):
        w = self.w
        self.on_branch({self.REQUEST: self.REQUEST_TEXT})
        run = w.prepare("fetcher", 7)
        self.assertEqual((run.meta["kind"], run.meta["pull"], run.meta["branch"]), ("operator", 7, self.BRANCH))
        self.assertEqual(gitstate.git(run.worktree, "symbolic-ref", "HEAD").strip(), "refs/heads/" + self.BRANCH)
        text = self.prompt(run)
        self.assertIn(f"Branch: {self.BRANCH}\n", text)
        self.assertIn("Task: Turn each request on pull request #7 into a job file.\n"
                      f"- {self.REQUEST} -> {self.JOB}\n"
                      "Each job names ticket 6 and caller fetcher. The ticket names the tool and the expected cost.\n",
                      text)
        comment = w.gh.comments(7)[-1]
        self.assertTrue(comment["body"].startswith("[SHOWLEAD] LAUNCH fetcher 7-1\n"))

    def test_the_ticket_number(self):
        self.on_branch({self.REQUEST: self.REQUEST_TEXT})
        before = self.w.snapshot()
        with self.assertRaisesRegex(LaunchError, r"^give the pull request number$"):
            self.w.prepare("fetcher", 6)
        self.assertEqual(self.w.snapshot(), before)

    def test_no_request(self):
        self.refused("#7 has no request for fetcher")

    def test_only_a_job_path(self):
        self.on_branch({self.JOB: "{}\n"})
        self.refused("#7 has no request for fetcher")

    def test_a_job_with_no_pass(self):
        self.on_branch({self.REQUEST: self.REQUEST_TEXT})
        digest = self.job()
        self.refused(f"job {digest} has no counting GATE PASS")

    def test_a_job_changed_after_its_pass(self):
        self.on_branch({self.REQUEST: self.REQUEST_TEXT})
        self.passed(self.job())
        changed = self.job(expected={"compute_min": 5, "usd": 2})
        self.refused(f"job {changed} has no counting GATE PASS")

    def test_a_pass_and_no_run(self):
        self.on_branch({self.REQUEST: self.REQUEST_TEXT})
        digest = self.job()
        self.passed(digest)
        self.refused(f"job {digest} has not run")

    def test_the_reporting_launch(self):
        w = self.w
        self.on_branch({self.REQUEST: self.REQUEST_TEXT})
        digest = self.job()
        self.passed(digest)
        self.ledger_row("run", digest, "DONE", usd="0.75", compute_min="4")
        media = f"{digest[:16]}-r1.png"
        self.on_branch({"media/manifest.tsv": "id\tsha256\tbytes\tsource\n"
                                              f"{media}\t{'a' * 64}\t9\tjob {digest}\n"
                                              f"other-1\t{'b' * 64}\t9\tjob {'c' * 64}\n"})
        # One more commit that leaves the job file alone: the PASS still counts, by its hash.
        self.on_branch({"episodes/ep02/beats.md": "# Beats\n"})
        run = w.prepare("fetcher", 7)
        self.assertIn("Task: Report on the jobs of pull request #7. Write no file.\n"
                      f"- {self.JOB}: job {digest} DONE usd 0.75 compute_min 4; media: {media}\n", self.prompt(run))

    def test_the_maker_cites_the_media(self):
        # ISSUE-285 behaviour 8: every job DONE, the maker's task asks it to cite the media.
        self.on_branch({self.REQUEST: self.REQUEST_TEXT})
        digest = self.job()
        self.passed(digest)
        self.ledger_row("run", digest, "DONE")
        media = f"{digest[:16]}-r1.png"
        self.on_branch({"media/manifest.tsv": f"id\tsha256\tbytes\tsource\n{media}\t{'a' * 64}\t9\tjob {digest}\n"})
        run = self.w.prepare("drafter", 7)
        self.assertIn("Task: Cite the media of the jobs on pull request #7.\n"
                      f"- {self.REQUEST}: media: {media}\n"
                      "Write the files that wait for these images. "
                      "Do not change or remove a request: a changed request is a new job.\n", self.prompt(run))

    def test_a_job_not_run_is_not_cited(self):
        # Seeded: a PASS and no run.
        self.on_branch({self.REQUEST: self.REQUEST_TEXT})
        self.passed(self.job())
        run = self.w.prepare("drafter", 7)
        text = self.prompt(run)
        self.assertIn("Task: Address every open note on pull request #7.", text)
        self.assertNotIn("Cite the media", text)


class Fence(unittest.TestCase):
    """Criterion 6."""

    def test_five_backticks(self):
        w = World(self)
        task = w.tmp / "task.md"
        task.write_text("Show this:\n`````\ncode\n`````\n")
        run = w.prepare("drafter", 5, task_file=task)
        text = (run.dir / "prompt.md").read_text()
        self.assertIn("Task: Show this:\n`````\n", text)
        body = w.gh.comments(5)[-1]["body"]
        inner, fence = fenced(body)
        self.assertEqual(fence, "``````")
        self.assertEqual(inner, text)


class FailingPost(github.Recorded):
    def post(self, number, body, role):
        raise github.GitHubError(f"post #{number}: exit 1")


class PostFails(unittest.TestCase):
    """Criterion 7."""

    def test_first_launch(self):
        w = World(self)
        w.gh = FailingPost(w.dir)
        before = w.snapshot()
        with self.assertRaisesRegex(LaunchError, r"^could not post LAUNCH on #5$"):
            w.prepare("drafter", 5)
        self.assertEqual(w.snapshot(), before)
        self.assertEqual(list((w.state / "runs").iterdir()), [])
        self.assertEqual(list((w.state / "worktrees").iterdir()), [])

    def test_relaunch_keeps_the_pull_branch(self):
        w = World(self)
        w.gh = FailingPost(w.dir)
        before = w.snapshot()
        with self.assertRaisesRegex(LaunchError, r"^could not post LAUNCH on #7$"):
            w.prepare("drafter", 7)
        self.assertEqual(w.snapshot(), before)
        self.assertEqual(list((w.state / "runs").iterdir()), [])


class NoSecrets(unittest.TestCase):
    """Criterion 8."""

    def test_seeded_settings_are_nowhere(self):
        w = World(self)
        w.prepare("drafter", 5)
        w.prepare("reviewer", 7)
        w.prepare("assessor", 7)
        files = [p for p in (w.state / "runs").rglob("*") if p.is_file()]
        self.assertEqual(len(files), 12)
        for path in files:
            self.assertNotIn(SEED, path.read_text(), path)
        for path in (w.dir / "comments").glob("*.json"):
            self.assertNotIn(SEED, path.read_text(), path)

    def test_error_message_holds_no_setting(self):
        w = World(self)
        w.local(MODEL_CHECKER="")
        with self.assertRaises(LaunchError) as caught:
            w.prepare("reviewer", 7)
        self.assertNotIn(SEED, str(caught.exception))


class MarkersLint(unittest.TestCase):
    """Criterion 9."""

    def lint_status(self, comments):
        tree = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tree)
        shutil.copytree(PRODUCTION / ".factory", tree / ".factory")
        (tree / ".factory-state").mkdir()
        (tree / ".factory-state" / "comments.json").write_text(json.dumps(comments, indent=1) + "\n")
        return lint.run(tree, only=["markers"])["markers"].status

    def test_launch_comments_pass(self):
        w = World(self)
        w.prepare("drafter", 5)
        w.prepare("reviewer", 7)
        w.prepare("drafter", 7)
        comments = [c for n in (5, 6, 7) for c in w.gh.comments(n)]
        launches = [c for c in comments if " LAUNCH " in c["body"].split("\n", 1)[0]]
        self.assertEqual(len(launches), 4)
        self.assertEqual(self.lint_status(comments), lint.PASS)
        seeded = [{**c, "role": "program:launch"} if c in launches else c for c in comments]
        self.assertEqual(self.lint_status(seeded), lint.FAIL)


class UnfilledPlaceholder(unittest.TestCase):
    """Criterion 10."""

    def test_no_value(self):
        w = World(self)
        doc = w.p.roles["reviewer"]
        w.p.roles["reviewer"] = {**doc, "body": doc["body"] + "Branch: {{BRANCH}}\n"}
        before = w.snapshot()
        with self.assertRaises(LaunchError) as caught:
            w.prepare("reviewer", 7)
        self.assertEqual(str(caught.exception), "no value for {{BRANCH}}")
        self.assertEqual(w.snapshot(), before)
        self.assertFalse(w.state.exists())


class Source(unittest.TestCase):
    """Criterion 11."""

    def test_no_flag_no_placeholder_one_renderer(self):
        source = (ROOT / "kit" / "lib" / "launch.py").read_text()
        self.assertIsNone(re.search(r"--(model|tools|mcp-config|json-schema|permission-mode)", source))
        self.assertNotIn("{{", source)
        self.assertGreaterEqual(source.count("prompt.render"), 1)


# -- the orchestrator role (ISSUE-89)

PULL_REF = "beats/BT-2-drafter-1"
PITCHES = (8, 9, 10)


def rounds(w, count=3):
    """Pull #7 with `count` rounds of the reviewer's NOTES: the fixture's one, then for each more round the
    drafter's LAUNCH and ADDRESSED and the reviewer's LAUNCH and NOTES. Returns the ids added."""
    ids = []
    for r in range(2, count + 1):
        at = f"2026-11-10T1{r - 2}"
        for i, (minute, role, body) in enumerate([
                ("00", "showlead", f"[SHOWLEAD] LAUNCH drafter 7-{r}\n"),
                ("10", "drafter", f"[DRAFTER] ADDRESSED\nB1 is answered, round {r}.\n"),
                ("20", "showlead", f"[SHOWLEAD] LAUNCH reviewer 7-{r}\n"),
                ("30", "reviewer", f"[REVIEWER] NOTES\nB1 driftwood-note the flood still has no cause, round {r}\n"
                                   "reviewed head {head:7} manifest none\n")]):
            ids.append(10 * r + i)
            w.comment(7, ids[-1], f"{at}:{minute}:00Z", role, body)
    return ids


def pitches(w, judged=PITCHES):
    """Stage beats with `pitches` 3; ticket #5 with the open pulls 8, 9 and 10, each with a READY, a [LINT]
    PASS and an APPROVED, and the assessor's verdict on the pulls in `judged`; and the closed pull 11."""
    with open(w.root / ".git" / "info" / "exclude", "a") as handle:
        handle.write("production.local.env\n")
    for n in PITCHES + (11,):
        w.set_pull(n, title="Work for #5", body="Ticket: #5\n", state="open" if n != 11 else "closed",
                   labels=[], head_ref=f"beats/BT-1-pitch-{n}", base_ref="trunk", ticket=5,
                   created_at="2026-11-04T09:00:00Z", merged_at=None)
    for n in PITCHES:
        w.comment(n, 100 + 10 * n, "2026-11-05T09:00:00Z", "drafter", f"[DRAFTER] READY\nMaker report: pitch {n}.\n")
        w.comment(n, 101 + 10 * n, "2026-11-05T09:05:00Z", "program:lint",
                  f"[LINT] PASS\nreviewed head {{head:{n}}} manifest none\n")
        w.comment(n, 102 + 10 * n, "2026-11-05T09:10:00Z", "reviewer",
                  f"[REVIEWER] APPROVED\nPitch {n} holds.\nreviewed head {{head:{n}}} manifest none\n")
        if n in judged:
            w.comment(n, 103 + 10 * n, "2026-11-05T09:15:00Z", "assessor",
                      f"[ASSESSOR] NO PREFERENCE\nPitch {n} assessed.\nreviewed head {{head:{n}}} manifest none\n")
    path = w.root / ".factory" / "pipeline.json"
    old = '"pitches": 1, "paths": ["episodes/*/beats.md"]'
    assert old in path.read_text()
    path.write_text(path.read_text().replace(old, '"pitches": 3, "paths": ["episodes/*/beats.md"]'))
    w.local()


def other_room(w):
    """A second orchestrator, `cutlead`, of the room `post`."""
    role = (w.root / ".factory" / "roles" / "showlead.md").read_text()
    role = role.replace("role: showlead", "role: cutlead").replace("room: story", "room: post")
    (w.root / ".factory" / "roles" / "cutlead.md").write_text(role.replace("the showlead", "the cutlead"))
    w.local()


def body_of(w, number, id):
    return next(c["body"] for c in w.gh.comments(number) if c["id"] == id)


def task_of(text):
    """The rendered TASK of a prompt whose role file has `Task: {{TASK}}` and then `Rules:`."""
    return text.split("\nTask: ", 1)[1].split("\n\nRules:\n", 1)[0]


def stated_sha(body):
    return next(line.split(": ", 1)[1] for line in body.split("\n") if line.startswith("prompt sha256: "))


def tree(path):
    """Every file under `path`, relative, with its content."""
    return {p.relative_to(path).as_posix(): p.read_text() for p in sorted(path.rglob("*")) if p.is_file()}


def files_at(w, ref):
    names = w.git("ls-tree", "-r", "--name-only", ref).split("\n")
    return {name: w.git("show", f"{ref}:{name}") for name in names if name}


class OrchestratorRefusal:
    """A refused launch creates nothing and posts nothing."""

    def refused(self, w, message, role, number):
        before = w.snapshot()
        with self.assertRaises(LaunchError) as caught:
            w.prepare(role, number)
        self.assertEqual(str(caught.exception), message)
        self.assertEqual(w.snapshot(), before)
        self.assertFalse(w.state.exists())


class RulingLaunch(OrchestratorRefusal, unittest.TestCase):
    """Behaviour 1: a ruling launch on a pull request with three rounds of notes and no ruling."""

    def setUp(self):
        self.w = World(self)
        self.added = rounds(self.w)

    def test_ruling_launch(self):
        w = self.w
        before = len(w.gh.comments(7))
        run = w.prepare("showlead", 7)
        head = w.head(PULL_REF)
        comments = w.gh.comments(7)
        self.assertEqual(len(comments), before + 1)
        record = comments[-1]
        self.assertEqual(record["role"], "showlead")
        self.assertTrue(record["body"].startswith("[SHOWLEAD] LAUNCH showlead 7-1\n"), record["body"])
        self.assertIn(f"\nhead: {head}\n", record["body"])
        text = (run.dir / "prompt.md").read_text()
        self.assertEqual(fenced(record["body"])[0], text)
        self.assertEqual(stated_sha(record["body"]), hashlib.sha256(text.encode("utf-8")).hexdigest())

    def test_working_directory(self):
        w = self.w
        run = w.prepare("showlead", 7)
        workdir = w.state / "worktrees" / "showlead-7-1"
        self.assertEqual(run.worktree, workdir)
        self.assertTrue(workdir.is_dir())
        self.assertFalse(os.path.lexists(workdir / ".git"))
        self.assertEqual(mode(workdir), 0o700)
        self.assertEqual(sorted(p.name for p in workdir.iterdir()), ["pr-7"])
        self.assertEqual(tree(workdir / "pr-7"), files_at(w, PULL_REF))
        self.assertEqual((workdir / "pr-7" / "work" / "beats-BT-2-drafter-1.md").read_text(), PULL_REF + "\n")
        self.assertNotIn("showlead-7-1", w.worktrees())
        launch.cleanup(w.root, run)
        self.assertFalse(workdir.exists())

    def test_run_json(self):
        w = self.w
        run = w.prepare("showlead", 7)
        meta = run.meta
        self.assertEqual(set(meta), RUN_KEYS | {"pulls"})
        self.assertEqual(launch.load_run(run.dir), run)
        self.assertEqual(meta, {
            "id": "7-1", "role": "showlead", "kind": "orchestrator", "actor": "SHOWLEAD", "number": 7, "ticket": 6,
            "pull": 7, "room": "story", "stage": "beats", "unit": "ep02", "branch": None,
            "worktree": str(w.state / "worktrees" / "showlead-7-1"), "blind": False, "base_ref": "trunk",
            "base_sha": w.head("trunk"), "head_sha": w.head(PULL_REF), "model": "maker-model-1", "family": "any",
            "harness_version": "0.0.0 (fake)", "prompt_sha256": meta["prompt_sha256"], "started_at": NOW,
            "launch_comment": w.gh.comments(7)[-1]["id"], "pulls": [7]})

    def test_prompt_and_task(self):
        w = self.w
        run = w.prepare("showlead", 7)
        text = (run.dir / "prompt.md").read_text()
        self.assertIsNone(re.search(r"\{\{", text))
        head = w.head(PULL_REF)
        quoted = [2, 4] + [i for i in self.added if body_of(w, 7, i).startswith(("[DRAFTER]", "[REVIEWER]"))]
        self.assertEqual(len(quoted), 6)
        parts = [f"Judgment 1, a ruling after three rounds of notes, on pull request #7 at head {head}. "
                 "Its files are in pr-7/."] + [body_of(w, 7, i).rstrip("\n") for i in quoted]
        self.assertEqual(task_of(text), "\n\n".join(parts))
        self.assertNotIn("[LINT]", text)
        self.assertNotIn("] LAUNCH ", text)
        self.assertNotIn("Pull request:", text)
        self.assertNotIn("Branch:", text)

    def test_two_rounds_twin(self):
        w = World(self)
        rounds(w, 2)
        self.refused(w, "no judgment is due on #7", "showlead", 7)
        self.assertFalse((w.state / "runs").exists())
        self.assertFalse((w.state / "worktrees").exists())


class SelectionLaunch(OrchestratorRefusal, unittest.TestCase):
    """Behaviour 2: a selection launch on a ticket whose three pitches are lockable."""

    def test_selection_launch(self):
        w = World(self)
        pitches(w)
        before = len(w.gh.comments(5))
        run = w.prepare("showlead", 5)
        comments = w.gh.comments(5)
        self.assertEqual(len(comments), before + 1)
        record = comments[-1]
        self.assertTrue(record["body"].startswith("[SHOWLEAD] LAUNCH showlead 5-1\n"), record["body"])
        self.assertIn("\nhead: none\n", record["body"])
        for n in PITCHES:
            self.assertFalse(any(" LAUNCH " in c["body"].split("\n")[0] for c in w.gh.comments(n)))
        workdir = w.state / "worktrees" / "showlead-5-1"
        self.assertEqual(run.worktree, workdir)
        self.assertFalse(os.path.lexists(workdir / ".git"))
        self.assertEqual(sorted(p.name for p in workdir.iterdir()), ["pr-10", "pr-8", "pr-9"])
        for n in PITCHES:
            self.assertEqual(tree(workdir / f"pr-{n}"), files_at(w, f"beats/BT-1-pitch-{n}"))
        meta = json.loads((run.dir / "run.json").read_text())
        self.assertEqual((meta["kind"], meta["number"], meta["ticket"], meta["pull"], meta["head_sha"],
                          meta["branch"], meta["blind"], meta["pulls"]),
                         ("orchestrator", 5, 5, None, None, None, False, [8, 9, 10]))
        text = (run.dir / "prompt.md").read_text()
        self.assertEqual(fenced(record["body"])[0], text)
        self.assertIsNone(re.search(r"\{\{", text))
        parts = ["Judgment 2, a pitch selection, on ticket #5. The pitches are the pull requests #8, #9, #10."]
        for n in PITCHES:
            parts += [f"Pull request #{n} at head {w.head(f'beats/BT-1-pitch-{n}')}. Its files are in pr-{n}/."]
            parts += [body_of(w, n, 100 + 10 * n + k).rstrip("\n") for k in (0, 2, 3)]
        self.assertEqual(task_of(text), "\n\n".join(parts))
        self.assertNotIn("#11", text)
        self.assertNotIn("pr-11", text)
        self.assertNotIn("[LINT]", text)

    def test_a_pitch_without_a_counting_verdict_twin(self):
        w = World(self)
        pitches(w, judged=(8, 10))
        self.refused(w, "no judgment is due on #5", "showlead", 5)


class OrchestratorWhoAndWhen(OrchestratorRefusal, unittest.TestCase):
    """Behaviour 3: only the room's orchestrator, and not while its LAUNCH is unanswered on the number."""

    def test_orchestrator_of_another_room(self):
        w = World(self)
        rounds(w)
        other_room(w)
        self.refused(w, "cutlead is not the orchestrator of room story", "cutlead", 7)

    def test_running_on_a_pull(self):
        w = World(self)
        rounds(w)
        w.comment(7, 90, "2026-11-10T15:00:00Z", "showlead", "[SHOWLEAD] LAUNCH showlead 7-1\n")
        self.refused(w, "showlead is already running on #7", "showlead", 7)

    def test_running_on_a_ticket(self):
        w = World(self)
        pitches(w)
        w.comment(5, 90, "2026-11-06T15:00:00Z", "showlead", "[SHOWLEAD] LAUNCH showlead 5-1\n")
        self.refused(w, "showlead is already running on #5", "showlead", 5)

    def test_a_later_launch_of_a_maker_does_not_answer(self):
        w = World(self)
        rounds(w)
        w.comment(7, 90, "2026-11-10T15:00:00Z", "showlead", "[SHOWLEAD] LAUNCH showlead 7-1\n")
        w.comment(7, 91, "2026-11-10T16:00:00Z", "showlead", "[SHOWLEAD] LAUNCH drafter 7-4\n")
        self.refused(w, "showlead is already running on #7", "showlead", 7)

    def test_a_later_launch_of_a_maker_on_the_ticket_does_not_answer(self):
        w = World(self)
        pitches(w)
        w.comment(5, 90, "2026-11-06T15:00:00Z", "showlead", "[SHOWLEAD] LAUNCH showlead 5-1\n")
        w.comment(5, 91, "2026-11-06T16:00:00Z", "showlead", "[SHOWLEAD] LAUNCH drafter 5-1\n")
        self.refused(w, "showlead is already running on #5", "showlead", 5)

    def test_blocked_ends_the_run_on_a_pull_twin(self):
        w = World(self)
        rounds(w)
        w.comment(7, 90, "2026-11-10T15:00:00Z", "showlead", "[SHOWLEAD] LAUNCH showlead 7-1\n")
        w.comment(7, 91, "2026-11-10T16:00:00Z", "showlead", "[SHOWLEAD] BLOCKED\nno charter\n")
        self.assertEqual(w.prepare("showlead", 7).meta["id"], "7-2")

    def test_blocked_ends_the_run_on_a_ticket_twin(self):
        w = World(self)
        pitches(w)
        w.comment(5, 90, "2026-11-06T15:00:00Z", "showlead", "[SHOWLEAD] LAUNCH showlead 5-1\n")
        w.comment(5, 91, "2026-11-06T16:00:00Z", "showlead", "[SHOWLEAD] BLOCKED\nno charter\n")
        self.assertEqual(w.prepare("showlead", 5).meta["id"], "5-2")


class OrchestratorPostFails(unittest.TestCase):
    """Behaviour 4: a LAUNCH that cannot be posted leaves no run directory and no working directory."""

    def failed(self, w, number):
        w.gh = FailingPost(w.dir)
        before = w.snapshot()
        with self.assertRaisesRegex(LaunchError, rf"^could not post LAUNCH on #{number}$"):
            w.prepare("showlead", number)
        self.assertEqual(w.snapshot(), before)
        self.assertEqual(list((w.state / "runs").iterdir()), [])
        self.assertEqual(list((w.state / "worktrees").iterdir()), [])

    def test_ruling(self):
        w = World(self)
        rounds(w)
        self.failed(w, 7)

    def test_selection(self):
        w = World(self)
        pitches(w)
        self.failed(w, 5)


class RulingPassedOn(unittest.TestCase):
    """Behaviour 5: the newest RULING of the room's orchestrator reaches the maker's task and the checker's
    prompt; without one both prompts are those `main` builds."""

    RULING = "[SHOWLEAD] RULING\n\nThe flood keeps no cause; B1 is set aside.\n\nLaunch: 7-1\n"

    def setUp(self):
        self.w = World(self)

    def main_prompt(self, role, run):
        """The prompt `main` builds for the drafter or the reviewer on pull #7, as `main`'s launch.py does."""
        w = self.w
        values = {"REPO": "example/tide-show", "WORKTREE": str(run.worktree), "RULES": production.rules_text(w.p),
                  "TICKET": "#6 [BT-2] Beats for ep02\n\nLay out the beats of episode two: the harbour floods.\n"}
        if role == "drafter":
            values.update(BRANCH=PULL_REF, TASK="\n\n".join(
                ["Address every open note on pull request #7."] + [body_of(w, 7, i).rstrip("\n") for i in (3, 4)]))
        else:
            values.update(PR="7", TASK=f"Review pull request #7 at head {w.head(PULL_REF)}.")
        text = prompt.render(w.p.roles[role]["body"], values)
        if not text.endswith("\n"):
            text += "\n"
        if role == "reviewer":
            text += "\n## Maker's report\n\n" + body_of(w, 7, 2)
        return text

    def launched(self, role):
        run = self.w.prepare(role, 7)
        record = self.w.gh.comments(7)[-1]["body"]
        self.assertTrue(record.startswith(f"[SHOWLEAD] LAUNCH {role} "), record)
        text = (run.dir / "prompt.md").read_text()
        self.assertEqual(stated_sha(record), hashlib.sha256(text.encode("utf-8")).hexdigest())
        return run, text, stated_sha(record)

    def test_maker_task_ends_with_the_ruling(self):
        self.w.comment(7, 50, "2026-11-10T12:00:00Z", "showlead", self.RULING)
        run, text, sha = self.launched("drafter")
        self.assertTrue(task_of(text).endswith("\n\n" + self.RULING.rstrip("\n")), text)
        self.assertNotEqual(sha, hashlib.sha256(self.main_prompt("drafter", run).encode("utf-8")).hexdigest())

    def test_checker_prompt_ends_with_the_ruling(self):
        self.w.comment(7, 50, "2026-11-10T12:00:00Z", "showlead", self.RULING)
        run, text, sha = self.launched("reviewer")
        self.assertTrue(text.endswith("\n## Maker's report\n\n" + body_of(self.w, 7, 2) + "\n## Ruling\n\n" + self.RULING),
                        text)
        self.assertEqual(text, self.main_prompt("reviewer", run) + "\n## Ruling\n\n" + self.RULING)

    def test_the_newer_of_two_rulings(self):
        older = "[SHOWLEAD] RULING\n\nAn older ruling, overruled.\n"
        self.w.comment(7, 50, "2026-11-10T12:00:00Z", "showlead", older)
        self.w.comment(7, 51, "2026-11-10T13:00:00Z", "showlead", self.RULING)
        run, text, _ = self.launched("drafter")
        self.assertTrue(task_of(text).endswith("\n\n" + self.RULING.rstrip("\n")), text)
        self.assertNotIn("An older ruling", text)
        run, text, _ = self.launched("reviewer")
        self.assertTrue(text.endswith("\n## Ruling\n\n" + self.RULING), text)
        self.assertNotIn("An older ruling", text)

    def test_a_ruling_on_the_ticket(self):
        self.w.comment(6, 50, "2026-11-10T12:00:00Z", "showlead", self.RULING)
        run, text, _ = self.launched("drafter")
        self.assertTrue(task_of(text).endswith("\n\n" + self.RULING.rstrip("\n")), text)
        run, text, _ = self.launched("reviewer")
        self.assertTrue(text.endswith("\n## Ruling\n\n" + self.RULING), text)

    def test_no_ruling_twin(self):
        for role in ("drafter", "reviewer"):
            with self.subTest(role=role):
                run, text, sha = self.launched(role)
                self.assertEqual(sha, hashlib.sha256(self.main_prompt(role, run).encode("utf-8")).hexdigest())
                self.assertNotIn("## Ruling", text)

    def test_ruling_of_another_actor_twin(self):
        other_room(self.w)
        self.w.comment(7, 50, "2026-11-10T12:00:00Z", "cutlead", self.RULING.replace("SHOWLEAD", "CUTLEAD"))
        for role in ("drafter", "reviewer"):
            with self.subTest(role=role):
                run, text, sha = self.launched(role)
                self.assertEqual(sha, hashlib.sha256(self.main_prompt(role, run).encode("utf-8")).hexdigest())
                self.assertNotIn("RULING", text)



class BlockedMakerLaunch(OrchestratorRefusal, unittest.TestCase):
    """ISSUE-242 behaviour 4: the orchestrator's launch for a maker whose latest marker is BLOCKED."""

    BLOCKED = "[DRAFTER] BLOCKED\n\nMissing: the tide table\nWhat was tried: the bible\nResume from: here\n"

    def setUp(self):
        self.w = World(self)

    def test_on_a_pull(self):
        w = self.w
        w.comment(7, 50, "2026-11-10T12:00:00Z", "drafter", self.BLOCKED)
        before = len(w.gh.comments(7))
        run = w.prepare("showlead", 7)
        comments = w.gh.comments(7)
        self.assertEqual(len(comments), before + 1)
        record = comments[-1]
        self.assertTrue(record["body"].startswith("[SHOWLEAD] LAUNCH showlead 7-1\n"), record["body"])
        head = w.head(PULL_REF)
        self.assertIn(f"\nhead: {head}\n", record["body"])
        workdir = w.state / "worktrees" / "showlead-7-1"
        self.assertEqual(sorted(p.name for p in workdir.iterdir()), ["pr-7"])
        self.assertEqual(tree(workdir / "pr-7"), files_at(w, PULL_REF))
        self.assertEqual(set(run.meta), RUN_KEYS | {"pulls"})
        self.assertEqual((run.meta["pull"], run.meta["pulls"]), (7, [7]))
        text = (run.dir / "prompt.md").read_text()
        self.assertEqual(task_of(text), f"Judgment 4, a blocked worker, on pull request #7 at head {head}. "
                                        "Its files are in pr-7/.\n\n" + self.BLOCKED.rstrip("\n"))

    def test_on_a_ticket(self):
        w = self.w
        w.comment(5, 50, "2026-11-10T12:00:00Z", "showlead", "[SHOWLEAD] LAUNCH drafter 5-1\n")
        w.comment(5, 51, "2026-11-10T12:30:00Z", "drafter", self.BLOCKED)
        run = w.prepare("showlead", 5)
        record = w.gh.comments(5)[-1]
        self.assertTrue(record["body"].startswith("[SHOWLEAD] LAUNCH showlead 5-1\n"), record["body"])
        self.assertIn("\nhead: none\n", record["body"])
        workdir = w.state / "worktrees" / "showlead-5-1"
        self.assertEqual(run.worktree, workdir)
        self.assertEqual(list(workdir.iterdir()), [])
        self.assertEqual(set(run.meta), RUN_KEYS | {"pulls"})
        self.assertEqual((run.meta["pull"], run.meta["head_sha"], run.meta["pulls"]), (None, None, []))
        text = (run.dir / "prompt.md").read_text()
        self.assertEqual(task_of(text), "Judgment 4, a blocked worker, on ticket #5. The launch it answers has no "
                                        "pull request.\n\n" + self.BLOCKED.rstrip("\n"))

    def test_after_a_ruling_on_the_pull(self):
        # Seeded defect: the BLOCKED has its ruling; the maker is due, not the orchestrator.
        w = self.w
        w.comment(7, 50, "2026-11-10T12:00:00Z", "drafter", self.BLOCKED)
        w.comment(7, 51, "2026-11-10T13:00:00Z", "showlead", "[SHOWLEAD] RULING\n\nUse last year's table.\n")
        self.refused(w, "no judgment is due on #7", "showlead", 7)


class RulingOnTheTicketPassedOn(unittest.TestCase):
    """ISSUE-242 behaviour 5: a maker relaunched on a ticket after a RULING on its BLOCKED gets the
    ruling as the last part of its task; with no such ruling its prompt is the one `main` builds."""

    RULING = "[SHOWLEAD] RULING\n\nUse last year's tide table.\n\nLaunch: 5-1\n"

    def setUp(self):
        self.w = World(self)

    def main_prompt(self, run):
        """The prompt `main` builds for the drafter's launch on ticket #5, as `main`'s launch.py does."""
        w = self.w
        values = {"REPO": "example/tide-show", "WORKTREE": str(run.worktree), "RULES": production.rules_text(w.p),
                  "TICKET": f"#5 [BT-1] Beats for ep01\n\n{w.gh.issue(5)['body']}",
                  "BRANCH": run.meta["branch"], "TASK": "Do the stage beats for unit ep01."}
        text = prompt.render(w.p.roles["drafter"]["body"], values)
        return text if text.endswith("\n") else text + "\n"

    def launched(self):
        run = self.w.prepare("drafter", 5)
        text = (run.dir / "prompt.md").read_text()
        record = self.w.gh.comments(5)[-1]["body"]
        self.assertTrue(record.startswith("[SHOWLEAD] LAUNCH drafter 5-"), record)
        return run, text, stated_sha(record)

    def test_the_ruling_ends_the_task(self):
        w = self.w
        w.comment(5, 50, "2026-11-10T12:00:00Z", "showlead", "[SHOWLEAD] LAUNCH drafter 5-1\n")
        w.comment(5, 51, "2026-11-10T12:30:00Z", "drafter", "[DRAFTER] BLOCKED\nMissing: the tide table\n")
        w.comment(5, 52, "2026-11-10T13:00:00Z", "showlead", "[SHOWLEAD] LAUNCH showlead 5-1\n")
        w.comment(5, 53, "2026-11-10T13:30:00Z", "showlead", self.RULING)
        run, text, sha = self.launched()
        self.assertEqual(run.meta["id"], "5-2")
        self.assertEqual(task_of(text), "Do the stage beats for unit ep01.\n\n" + self.RULING.rstrip("\n"))
        self.assertNotEqual(sha, hashlib.sha256(self.main_prompt(run).encode("utf-8")).hexdigest())

    def test_no_ruling_twin(self):
        run, text, sha = self.launched()
        self.assertEqual(sha, hashlib.sha256(self.main_prompt(run).encode("utf-8")).hexdigest())

    def test_a_ruling_with_no_blocked_twin(self):
        self.w.comment(5, 53, "2026-11-10T13:30:00Z", "showlead", self.RULING)
        run, text, sha = self.launched()
        self.assertEqual(sha, hashlib.sha256(self.main_prompt(run).encode("utf-8")).hexdigest())
        self.assertNotIn("RULING", text)


class ResourceDown(unittest.TestCase):
    """ISSUE-94: a launch whose resource is down is refused before anything is created or posted."""

    def setUp(self):
        self.w = World(self)

    def down_issue(self, resource, state="open"):
        self.w.set_issue(20, title=f"[DOWN] {resource}", body="", state=state, labels=["resource:down"],
                         created_at="2026-11-10T00:00:00Z")

    def refused(self, message, role, number):
        before = self.w.snapshot()
        listing = sorted(os.listdir(self.w.state)) if self.w.state.exists() else None
        with self.assertRaises(LaunchError) as caught:
            self.w.prepare(role, number)
        self.assertEqual(str(caught.exception), message)
        self.assertEqual(self.w.snapshot(), before)
        return listing

    def test_the_proxy(self):
        """Behaviour 3: a role of family `other` waits while `inference.other` is down."""
        self.w.local(PROXY_URL="http://localhost:8317", MODEL_OTHER="other-model-1")
        self.down_issue("inference.other")
        self.assertIsNone(self.refused("resource inference.other is down (#20)", "reviewer", 7))
        self.assertFalse(self.w.state.exists())

    def test_the_proxy_twins(self):
        """A closed issue, and the same role when its family falls back to `same`, are not blocks."""
        self.w.local(PROXY_URL="http://localhost:8317", MODEL_OTHER="other-model-1")
        self.down_issue("inference.other", state="closed")
        self.w.prepare("reviewer", 7)
        w = World(self)
        w.set_issue(20, title="[DOWN] inference.other", body="", state="open", labels=["resource:down"],
                    created_at="2026-11-10T00:00:00Z")
        w.prepare("reviewer", 7)

    def stop_every_account(self):
        self.w.state.mkdir(mode=0o700)
        (self.w.state / "stopped.json").write_text(json.dumps({SEED: 20}))

    def test_every_account_stopped(self):
        """Behaviour 2: every account of the slot is stopped under an open issue."""
        self.stop_every_account()
        self.down_issue("inference.primary")
        listing = self.refused("resource inference.primary is down (#20)", "drafter", 5)
        self.assertEqual(listing, ["stopped.json"])
        self.assertEqual(sorted(os.listdir(self.w.state)), listing)

    def test_every_account_stopped_under_a_closed_issue_twin(self):
        self.stop_every_account()
        self.down_issue("inference.primary", state="closed")
        self.w.prepare("drafter", 5)


class OutOfUsage(unittest.TestCase):
    """ISSUE-249: a launch whose slot has no account with usage left is refused before anything is created."""

    def setUp(self):
        self.w = World(self)

    def accounts(self, data):
        self.w.state.mkdir(mode=0o700, exist_ok=True)
        (self.w.state / "accounts.json").write_text(json.dumps(data))

    def stopped(self, data):
        self.w.state.mkdir(mode=0o700, exist_ok=True)
        (self.w.state / "stopped.json").write_text(json.dumps(data))
        self.w.set_issue(20, title="[DOWN] inference.primary", body="", state="open", labels=["resource:down"],
                         created_at="2026-11-10T00:00:00Z")

    def refused(self, message, role, number):
        before = self.w.snapshot()
        listing = sorted(os.listdir(self.w.state))
        with self.assertRaises(LaunchError) as caught:
            self.w.prepare(role, number)
        self.assertEqual(str(caught.exception), message)
        self.assertEqual(self.w.snapshot(), before)
        self.assertEqual(sorted(os.listdir(self.w.state)), listing)

    def test_the_launch_is_refused(self):
        """Behaviour 3."""
        self.accounts({SEED: "2026-11-11T17:00:00Z"})
        self.refused("slot MAKER is out of usage until 2026-11-11T17:00:00Z", "drafter", 5)

    def test_the_time_written_is_usable_twin(self):
        self.accounts({SEED: NOW})
        self.assertEqual(self.w.prepare("drafter", 5).meta["id"], "5-1")

    def test_the_earliest_time(self):
        """Behaviour 4: the earliest time of the accounts of the slot."""
        self.w.local(ACCOUNTS_MAKER=f"{SEED}, acct-two")
        self.accounts({SEED: "2026-11-11T18:00:00Z", "acct-two": "2026-11-11T16:30:00Z"})
        self.refused("slot MAKER is out of usage until 2026-11-11T16:30:00Z", "drafter", 5)

    def test_a_stopped_account_is_left_out(self):
        """Behaviour 4: the earliest time among the accounts not stopped."""
        self.w.local(ACCOUNTS_MAKER=f"{SEED}, acct-two")
        self.accounts({SEED: "2026-11-11T18:00:00Z", "acct-two": "2026-11-11T16:30:00Z"})
        self.stopped({"acct-two": 20})
        self.refused("slot MAKER is out of usage until 2026-11-11T18:00:00Z", "drafter", 5)

    def test_every_account_stopped_is_the_resource(self):
        """Behaviour 4, ruling 5: a role that is blocked gets the resource refusal, not the usage one."""
        self.w.local(ACCOUNTS_MAKER=f"{SEED}, acct-two")
        self.accounts({SEED: "2026-11-11T18:00:00Z", "acct-two": "2026-11-11T16:30:00Z"})
        self.stopped({SEED: 20, "acct-two": 20})
        self.refused("resource inference.primary is down (#20)", "drafter", 5)

    def test_the_family_other_is_not_a_wait(self):
        """Behaviour 5: a role on the proxy uses no account of its slot."""
        self.accounts({SEED: "2026-11-11T18:00:00Z"})
        self.w.local(ACCOUNTS_CHECKER=SEED, PROXY_URL="http://localhost:8317", MODEL_OTHER="other-model-1")
        self.w.prepare("reviewer", 7)

    def test_the_family_other_falls_back_twin(self):
        self.accounts({SEED: "2026-11-11T18:00:00Z"})
        self.w.local(ACCOUNTS_CHECKER=SEED, MODEL_OTHER="other-model-1")
        self.refused("slot CHECKER is out of usage until 2026-11-11T18:00:00Z", "reviewer", 7)


CUT = re.compile(r"^prompt cut: the first (\d+) of (\d+) lines are shown; "
                 r"the whole prompt is runs/(\S+)/prompt\.md under STATE_DIR$")


def notes_task(w, end="\n", lines=1000):
    task = w.tmp / "task.md"
    task.write_text("".join(f"Note {i:04d}: " + "x" * 88 + end for i in range(1, lines + 1)))
    return task


def budget(role):
    """The longest record, in characters, that `role` can post."""
    return next(n for n in range(github.BODY_MAX, 0, -1) if github.fits("a" * (n - 1) + "\n", role))


class LongMakerTask(unittest.TestCase):
    """ISSUE-243 behaviours 3 and 6: a record that would not fit is cut after its last whole line that fits."""

    def launched(self, end="\n"):
        w = World(self)
        before = len(w.gh.comments(5))
        run = w.prepare("drafter", 5, task_file=notes_task(w, end))
        comments = w.gh.comments(5)
        self.assertEqual(len(comments), before + 1)
        return run, comments[-1], (run.dir / "prompt.md").read_text()

    def test_the_record_is_cut(self):
        run, comment, text = self.launched()
        body = comment["body"]
        lines = body.split("\n")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        self.assertEqual(lines[:2], ["[SHOWLEAD] LAUNCH drafter 5-1", f"prompt sha256: {digest}"])
        m = CUT.match(lines[2])
        self.assertIsNotNone(m, lines[2])
        k, n = int(m.group(1)), int(m.group(2))
        self.assertEqual(m.group(3), "drafter-5-1")
        self.assertEqual(n, text.count("\n"))
        self.assertTrue(0 < k < n, (k, n))
        self.assertEqual(lines[3:9], ["model: maker-model-1", "family: any", "harness: 0.0.0 (fake)",
                                      f"base: {run.meta['base_sha']}", "head: none", ""])
        inner, fence = fenced(body)
        prompt_lines = text.split("\n")
        self.assertEqual(inner, "".join(x + "\n" for x in prompt_lines[:k]))
        self.assertTrue(github.fits(body, "showlead"))
        start, closing, end = body.rpartition("\n" + fence + "\n")
        longer = start + "\n" + prompt_lines[k] + closing + end
        self.assertEqual(len(longer), len(body) + len(prompt_lines[k]) + 1)
        self.assertFalse(github.fits(longer, "showlead"))
        self.assertEqual(run.meta["prompt_sha256"], digest)
        self.assertEqual(run.meta["launch_comment"], comment["id"])
        self.assertEqual(json.loads((run.dir / "run.json").read_text())["launch_comment"], comment["id"])

    def test_lines_end_only_at_a_newline(self):
        run, comment, text = self.launched(end="\f\n")
        self.assertIn("\f\n", text)
        m = CUT.match(comment["body"].split("\n")[2])
        self.assertIsNotNone(m)
        self.assertEqual(int(m.group(2)), text.count("\n"))
        inner, _ = fenced(comment["body"])
        self.assertTrue(text.startswith(inner) and inner.endswith("\n"), inner[-20:])
        self.assertEqual(inner.count("\n"), int(m.group(1)))


class RecordAtTheLimit(unittest.TestCase):
    """ISSUE-243 behaviour 4: a record of exactly the limit is posted whole; one character more is cut."""

    def record(self, extra):
        w = World(self)
        task = w.tmp / "task.md"
        task.write_text("Pad: " + "y" * extra + "\n")
        run = w.prepare("drafter", 5, task_file=task)
        return w.gh.comments(5)[-1]["body"], (run.dir / "prompt.md").read_text()

    def extra(self):
        """The task padding for which the whole record is exactly the longest that fits."""
        small, _ = self.record(0)
        return budget("showlead") - len(small)

    def test_at_the_limit_whole_twin(self):
        body, text = self.record(self.extra())
        self.assertTrue(github.fits(body, "showlead"))
        self.assertFalse(github.fits(body + "a", "showlead"))
        self.assertFalse(any(line.startswith("prompt cut:") for line in body.split("\n")))
        self.assertEqual(fenced(body)[0], text)

    def test_one_character_more_is_cut(self):
        body, text = self.record(self.extra() + 1)
        lines = body.split("\n")
        self.assertIsNotNone(CUT.match(lines[2]), lines[2])
        self.assertTrue(github.fits(body, "showlead"))
        self.assertEqual(stated_sha(body), hashlib.sha256(text.encode("utf-8")).hexdigest())


class LongRuling(unittest.TestCase):
    """ISSUE-243 behaviour 5: a ruling over long rounds is launched with its record cut."""

    def test_long_ruling(self):
        w = World(self)
        added = rounds(w)
        pad = "".join("p" * 99 + "\n" for _ in range(300))
        path = w.dir / "comments" / "7.json"
        comments = json.loads(path.read_text())
        for c in comments:
            if c["id"] in added and c["body"].startswith(("[DRAFTER] ADDRESSED\n", "[REVIEWER] NOTES\n")):
                first, rest = c["body"].split("\n", 1)
                c["body"] = first + "\n" + pad + rest
        path.write_text(json.dumps(comments, indent=1, sort_keys=True) + "\n")
        run = w.prepare("showlead", 7)
        body = w.gh.comments(7)[-1]["body"]
        text = (run.dir / "prompt.md").read_text()
        self.assertTrue(body.startswith("[SHOWLEAD] LAUNCH showlead 7-1\n"), body[:80])
        m = CUT.match(body.split("\n")[2])
        self.assertIsNotNone(m, body.split("\n")[2])
        self.assertEqual(m.group(3), "showlead-7-1")
        self.assertEqual(stated_sha(body), hashlib.sha256(text.encode("utf-8")).hexdigest())
        self.assertTrue(github.fits(body, "showlead"))


if __name__ == "__main__":
    unittest.main()
