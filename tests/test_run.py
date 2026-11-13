"""Tests for kit/lib/cmd_run.py: `factory run` on a job the gate passed, end to end.

The production is tests/fixtures/run/production, made into a git repository on branch `trunk` in a
temporary directory, with one branch per recorded pull. GitHub is a copy of tests/fixtures/run/github.
A comment body's ``{head:N}`` stands for the head commit of pull N's branch, filled in when it is copied.
The tools are tests/fixtures/run/tools and the register tests/fixtures/run/resources.json. The world
writes ``production.local.env`` with ``STATE_DIR``, ``SECRETS_DIR`` and ``FAKE_LOG`` in temporary
directories and a key file of mode 0600. A PASS is made by the real gate. Nothing reaches the network.
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import cmd_gate, cmd_run, gitstate, github, ledger, lint, resources  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "run"
NOW = "2026-11-02T09:00:00Z"
TICKET, PULL, BRANCH = 3, 4, "feat/RN-1-ep01"
CLEAN_JOB = "jobs/clean.json"
KEY = "seeded-key-5d1c0b7e"
MANIFEST_HEADER = "id\tsha256\tbytes\tsource\n"


def digest_of(path):
    job = json.loads(Path(path).read_text())
    return hashlib.sha256(json.dumps(job, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class FlakyPush(github.Recorded):
    """``Recorded`` whose pushes numbered in ``fails`` (counting from 1) raise."""

    def __init__(self, directory, fails):
        super().__init__(directory)
        self.fails, self.count = set(fails), 0

    def push(self, root, ref):
        self.count += 1
        if self.count in self.fails:
            raise github.GitHubError("push: exit 1")
        super().push(root, ref)


class FailingPost(github.Recorded):
    def post(self, number, body, role):
        raise github.GitHubError(f"post #{number}: exit 1")


class World:
    """A production repository on `trunk`, a recorded GitHub, a state directory and a key, all temporary."""

    def __init__(self, test):
        self.test = test
        self.tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, self.tmp)
        self.root = self.tmp / "show"
        self.dir = self.tmp / "github"
        self.state = self.tmp / "state"
        self.secrets = self.tmp / "secrets"
        self.fake_log = self.tmp / "fake.log"
        for patch in (mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                                                   "FACTORY_GITHUB": str(self.dir), "FACTORY_NOW": NOW}),
                      mock.patch.object(cmd_run, "TOOLS_DIR", FIX / "tools"),
                      mock.patch.object(resources, "REGISTER", FIX / "resources.json")):
            patch.start()
            test.addCleanup(patch.stop)
        shutil.copytree(FIX / "production", self.root)
        self.git("init", "-q", "-b", "trunk")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "the production")
        shutil.copytree(FIX / "github", self.dir)
        for path in sorted((self.dir / "pulls").glob("*.json")):
            self.branch(json.loads(path.read_text())["head_ref"])
        for path in sorted((self.dir / "comments").glob("*.json")):
            path.write_text(path.read_text().replace(f"{{head:{PULL}}}", self.head(BRANCH)))
        self.secrets.mkdir()
        self.key(KEY)
        self.local(STATE_DIR=self.state, SECRETS_DIR=self.secrets, FAKE_LOG=self.fake_log)
        self.gh = github.Recorded(self.dir)

    # -- the world

    def git(self, *args):
        return gitstate.git(self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", *args)

    def head(self, ref="trunk"):
        return gitstate.head(self.root, ref)

    def branch(self, ref):
        """A branch `ref` with one commit on top of `trunk`."""
        self.git("checkout", "-q", "-b", ref, "trunk")
        self.write("work/" + ref.replace("/", "-") + ".md", ref + "\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "work on " + ref)
        self.git("checkout", "-q", "trunk")

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def commit(self, rel, text):
        """Write ``rel`` and commit it alone on `trunk`."""
        self.write(rel, text)
        self.git("add", "--", rel)
        self.git("commit", "-q", "-m", "change " + rel)

    def key(self, text, mode=0o600):
        path = self.secrets / "gen.fake"
        path.write_text(text)
        path.chmod(mode)
        return path

    def local(self, **values):
        (self.root / "production.local.env").write_text("".join(f"{k}={v}\n" for k, v in values.items()))

    def set_env(self, name, value):
        path = self.root / "production.env"
        lines = [l for l in path.read_text().splitlines() if not l.startswith(name + "=")]
        self.commit("production.env", "\n".join(lines + [f"{name}={value}"]) + "\n")

    def record(self, number, comment):
        path = self.dir / "comments" / f"{number}.json"
        found = json.loads(path.read_text()) if path.is_file() else []
        path.write_text(json.dumps(found + [comment], indent=1) + "\n")

    def edit(self, kind, number, /, **changes):
        path = self.dir / kind / f"{number}.json"
        record = json.loads(path.read_text()) if path.is_file() else {}
        path.write_text(json.dumps({**record, **changes}, indent=1) + "\n")

    def job(self, name, **changes):
        """A copy of the clean job file with ``changes``, written untracked; its absolute path."""
        job = json.loads((self.root / CLEAN_JOB).read_text())
        job.update(changes)
        return str(self.write("jobs/" + name, json.dumps(job, indent=1)))

    def script(self, name, **script):
        """A fake's script committed on `trunk` as ``scripts/<name>.json``, and a job file that names it."""
        text = json.dumps(script, indent=1) + "\n"
        self.commit(f"scripts/{name}.json", text)
        sha = hashlib.sha256(text.encode()).hexdigest()
        return self.job(f"{name}.json", inputs=[{"path": f"scripts/{name}.json", "sha256": sha}])

    # -- the commands

    def _main(self, main, argv, gh):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            if gh is not None:
                stack.enter_context(mock.patch.object(github, "connect", return_value=gh))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def gate(self, job, gh=None):
        return self._main(cmd_gate.main, [job, "--root", str(self.root), "--apply"], gh)

    def passed(self, job=None, gh=None):
        """``job`` (default the clean one) passed by the real gate; its hash."""
        job = job or str(self.root / CLEAN_JOB)
        code, out, err = self.gate(job, gh)
        self.test.assertEqual(code, 0 if gh is None else 2, out + err)
        return digest_of(job)

    def run(self, job=None, apply=True, gh=None):
        """``(exit, stdout, stderr)`` of ``factory run`` in-process."""
        job = job or str(self.root / CLEAN_JOB)
        return self._main(cmd_run.main, [job, "--root", str(self.root)] + (["--apply"] if apply else []), gh)

    # -- what is there

    def rows(self, kind=None):
        text = gitstate.show(self.root, "trunk", ledger.PATH)
        rows = [] if text is None else ledger.read(text)
        return [r for r in rows if kind is None or r["kind"] == kind]

    def log(self):
        path = self.dir / "log.jsonl"
        return [json.loads(l) for l in path.read_text().splitlines()] if path.is_file() else []

    def fake_calls(self):
        return self.fake_log.read_text().split() if self.fake_log.exists() else []

    def job_dir(self, digest):
        return self.state / "jobs" / digest

    def manifest(self, ref=BRANCH):
        return gitstate.show(self.root, ref, gitstate.MANIFEST)

    def refs(self):
        return self.git("for-each-ref", "--format=%(refname) %(objectname)")

    def files(self, top):
        return sorted(str(p.relative_to(top)) for p in Path(top).rglob("*")) if Path(top).exists() else []

    def snapshot(self):
        """Every ref, the recorded GitHub's log and every name under ``STATE_DIR``."""
        return self.refs(), self.log(), self.files(self.state)

    def worktrees(self):
        return [os.path.realpath(line.split(" ", 1)[1]) for line in self.git("worktree", "list", "--porcelain").splitlines()
                if line.startswith("worktree ")]


def line(digest, verdict, usd, compute_min):
    return f"run job {digest}: {verdict} usd {usd} compute_min {compute_min}"


def clean_outputs():
    script = json.loads((FIX / "production" / "scripts" / "clean.json").read_text())
    return sorted(({"bytes": len(text.encode()), "name": name, "sha256": hashlib.sha256(text.encode()).hexdigest()}
                   for name, text in script["outputs"].items()), key=lambda o: o["name"])


def manifest_rows(digest, outputs):
    return [f"{digest[:16]}-{o['name']}\t{o['sha256']}\t{o['bytes']}\tjob {digest}" for o in outputs]


class EntryPointTest(unittest.TestCase):
    def test_run_is_a_command(self):
        done = subprocess.run([sys.executable, "kit/factory", "--help"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0)
        self.assertIn("  run", done.stdout.splitlines())
        done = subprocess.run([sys.executable, "kit/lib/cmd_run.py", "--help"], cwd=ROOT, capture_output=True,
                              text=True)
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertTrue(done.stdout.startswith("usage: factory run"))

    def test_the_kit_ships_no_tool(self):
        self.assertEqual(sorted(p.name for p in (ROOT / "kit" / "lib" / "tools").glob("*.py")), ["__init__.py"])


class CleanRunTest(unittest.TestCase):
    def setUp(self):
        self.w = World(self)
        self.digest = self.w.passed()

    def assert_clean_run(self):
        w, digest = self.w, self.digest
        trunk, branch, log = w.head(), w.head(BRANCH), w.log()
        code, out, err = w.run()
        self.assertEqual((code, out, err), (0, line(digest, "DONE", "1.5", "20") + "\n", ""))
        self.assertEqual(w.fake_calls(), [digest])
        # The outputs stay in the job's directory, named by result.json.
        outputs = clean_outputs()
        job_dir = w.job_dir(digest)
        self.assertEqual(w.files(job_dir), ["out", "out/shot-1.png", "out/shot-2.png", "result.json"])
        self.assertEqual(json.loads((job_dir / "result.json").read_text()),
                         {"compute_min": "20", "outputs": outputs, "reason": None, "usd": "1.5", "verdict": "DONE"})
        # One commit by `factory` on the pull's branch, touching only the manifest.
        self.assertEqual(w.git("rev-list", f"{branch}..{BRANCH}").split(), [w.head(BRANCH)])
        self.assertEqual(w.git("log", "-1", "--format=%an|%s|%P", BRANCH).strip(),
                         f"factory|manifest: job {digest[:12]}|{branch}")
        self.assertEqual(w.git("diff", "--name-only", branch, BRANCH).split(), [gitstate.MANIFEST])
        self.assertEqual(w.manifest(), MANIFEST_HEADER + "".join(r + "\n" for r in manifest_rows(digest, outputs)))
        tree = w.tmp / "exported"
        gitstate.export(w.root, BRANCH, tree)
        result = lint.run(tree, ["media"])["media"]
        self.assertEqual((result.status, result.findings), ("PASS", []))
        # One commit on the base, adding one run row.
        self.assertEqual(w.git("rev-list", f"{trunk}..trunk").split(), [w.head()])
        self.assertEqual(w.git("diff", "--name-only", trunk, "trunk").split(), [ledger.PATH])
        [row] = w.rows("run")
        self.assertEqual(ledger.line(row), "\t".join(
            [NOW, "run", digest, str(TICKET), "ep01", "fake", "1.5", "20", "renderer", "DONE", "-"]))
        self.assertEqual(ledger.spent(w.rows()), {"usd": Decimal("1.5"), "compute_min": Decimal("20")})
        # Two pushes, the branch then the base, and no post.
        self.assertEqual(w.log()[len(log):], [{"op": "push", "number": None, "ref": BRANCH},
                                              {"op": "push", "number": None, "ref": "trunk"}])
        # The root checkout is still on the base, with no change to a tracked file.
        self.assertEqual(w.git("symbolic-ref", "--short", "HEAD").strip(), "trunk")
        self.assertEqual(w.git("status", "--porcelain", "--untracked-files=no"), "")

    def test_clean_run(self):
        self.assert_clean_run()
        self.assertEqual(self.w.worktrees(), [os.path.realpath(self.w.root)])

    def test_branch_held_by_a_finished_makers_worktree(self):
        held = self.w.tmp / "maker"
        gitstate.add_worktree(self.w.root, held, BRANCH, branch=BRANCH)
        before = {name: (held / name).read_bytes() for name in self.w.files(held) if (held / name).is_file()}
        self.assert_clean_run()
        self.assertEqual({name: (held / name).read_bytes() for name in self.w.files(held) if (held / name).is_file()},
                         before)
        self.assertEqual(self.w.worktrees(), [os.path.realpath(self.w.root), os.path.realpath(held)])

    def test_branch_moved_before_the_recording(self):
        real = cmd_run._add_rows

        def moving(tree, digest, outputs):
            tree_id = self.w.git("rev-parse", BRANCH + "^{tree}").strip()
            moved = self.w.git("commit-tree", tree_id, "-p", BRANCH, "-m", "a maker's commit").strip()
            self.w.git("update-ref", "refs/heads/" + BRANCH, moved)
            self.moved = moved
            return real(tree, digest, outputs)

        log = self.w.log()
        with mock.patch.object(cmd_run, "_add_rows", moving):
            code, out, err = self.w.run()
        self.assertEqual((code, out), (2, ""))
        self.assertRegex(err, r"^run: git update-ref: exit \d+: .*cannot lock ref.*\n$")
        self.assertTrue((self.w.job_dir(self.digest) / "result.json").is_file())
        self.assertEqual(self.w.rows("run"), [])
        self.assertEqual(self.w.head(BRANCH), self.moved)
        self.assertEqual(self.w.log(), log)
        self.assertEqual(self.w.worktrees(), [os.path.realpath(self.w.root)])
        # A second run records the call it already made, on the moved branch.
        code, out, err = self.w.run()
        self.assertEqual((code, out, err), (0, line(self.digest, "DONE", "1.5", "20") + "\n", ""))
        self.assertEqual(self.w.fake_calls(), [self.digest])
        self.assertEqual(self.w.git("log", "-1", "--format=%P", BRANCH).strip(), self.moved)
        self.assertEqual(len(self.w.rows("run")), 1)


class NoCountingPassTest(unittest.TestCase):
    def setUp(self):
        self.w = World(self)
        self.clean = str(self.w.root / CLEAN_JOB)

    def assert_no_pass(self, job):
        before = self.w.snapshot()
        digest = digest_of(job)
        self.assertEqual(self.w.run(job), (2, "", f"run: job {digest} has no counting GATE PASS\n"))
        self.assertEqual(self.w.fake_calls(), [])
        self.assertFalse(self.w.job_dir(digest).exists())
        self.assertEqual(self.w.snapshot(), before)

    def gate_comment(self, digest, **fields):
        return {"id": 90, "created_at": "2026-11-02T08:00:00Z", "role": "program:gate", **fields,
                "body": f"[PRODUCER] GATE PASS\ncovers: job {digest}\ncap: -  reason: within caps\nledger: x\n"}

    def test_no_gate_comment(self):
        self.assert_no_pass(self.clean)

    def test_a_newer_gate_stop(self):
        self.w.passed()
        self.w.set_env("CAP_EPISODE_USD", "1")
        code, out, _ = self.w.gate(self.clean)
        self.assertEqual(code, 1)
        self.assertIn("STOP cap CAP_EPISODE_USD", out)
        self.assert_no_pass(self.clean)

    def test_expected_changed_after_the_pass(self):
        self.w.passed()
        self.assert_no_pass(self.w.job("changed.json", expected={"compute_min": 31, "usd": 2.5}))

    def test_pass_by_an_unnamed_author(self):
        digest = self.w.passed(gh=FailingPost(self.w.dir))
        self.assertEqual([r["verdict"] for r in self.w.rows("job")], ["PASS"])
        self.w.record(TICKET, self.gate_comment(digest, author="stranger"))
        self.assert_no_pass(self.clean)

    def test_pass_with_the_operators_role(self):
        digest = self.w.passed(gh=FailingPost(self.w.dir))
        self.assertEqual([r["verdict"] for r in self.w.rows("job")], ["PASS"])
        self.w.record(TICKET, self.gate_comment(digest, role="renderer"))
        self.assert_no_pass(self.clean)

    def test_pass_by_hand_without_a_ledger_row(self):
        self.w.record(TICKET, self.gate_comment(digest_of(self.clean)))
        self.assertEqual(self.w.rows(), [])
        self.assert_no_pass(self.clean)


class JobChecksTest(unittest.TestCase):
    def setUp(self):
        self.w = World(self)

    def assert_refused(self, job, message):
        before = self.w.snapshot()
        self.assertEqual(self.w.run(job), (2, "", f"run: {message}\n"))
        self.assertEqual(self.w.fake_calls(), [])
        self.assertEqual(self.w.snapshot(), before)

    def test_outside_the_production(self):
        outside = self.w.tmp / "job.json"
        outside.write_text((self.w.root / CLEAN_JOB).read_text())
        self.assert_refused(str(outside), f"job file {outside} is outside the production")

    def test_sixth_key(self):
        path = self.w.job("extra.json", extra="x")
        self.assert_refused(path, f"job file {path} is not {{ticket, tool, inputs, expected, caller}}")

    def test_input_changed_on_the_base(self):
        self.w.passed()
        self.w.commit("scripts/clean.json", '{"outputs": {}, "usd": 0, "compute_min": 0}\n')
        self.assert_refused(str(self.w.root / CLEAN_JOB), "input scripts/clean.json does not match its hash on the base")

    def test_closed_ticket(self):
        self.w.passed()
        self.w.edit("issues", TICKET, state="closed")
        self.assert_refused(str(self.w.root / CLEAN_JOB), f"ticket #{TICKET} is not an open ticket")

    def test_caller_is_not_a_gated_operator(self):
        self.assert_refused(self.w.job("painter.json", caller="painter"),
                            "caller painter is not a gated operator of stage production/render")

    def test_frozen(self):
        self.w.passed()
        self.w.edit("issues", 9, number=9, title="Stop everything", body="", state="open",
                    labels=["factory:freeze"], created_at="2026-11-02T08:00:00Z")
        self.assert_refused(str(self.w.root / CLEAN_JOB), "frozen")


class DryRunTest(unittest.TestCase):
    def setUp(self):
        self.w = World(self)

    def test_without_apply(self):
        digest = self.w.passed()
        (self.w.dir / "log.jsonl").unlink()
        refs = self.w.refs()
        self.assertEqual(self.w.run(apply=False), (0, f"run job {digest}: would call fake\n", ""))
        self.assertEqual(self.w.fake_calls(), [])
        self.assertFalse(self.w.state.exists())
        self.assertEqual(self.w.refs(), refs)
        self.assertEqual(self.w.log(), [])

    def test_without_apply_and_without_a_pass(self):
        digest = digest_of(self.w.root / CLEAN_JOB)
        self.assertEqual(self.w.run(apply=False), (2, "", f"run: job {digest} has no counting GATE PASS\n"))
        self.assertFalse(self.w.state.exists())
        self.assertEqual(self.w.log(), [])


class TwiceTest(unittest.TestCase):
    def test_second_run_is_already_recorded(self):
        w = World(self)
        digest = w.passed()
        first = w.run()
        self.assertEqual(first[0], 0)
        before = w.snapshot()
        again = (0, line(digest, "DONE", "1.5", "20") + " (already recorded)\n", "")
        self.assertEqual(w.run(), again)
        self.assertEqual(w.run(apply=False), again)
        self.assertEqual(w.fake_calls(), [digest])
        self.assertEqual(w.snapshot(), before)
        # The gate on the same job now says STOP (the manifest commit moved the head): still recorded.
        code, out, _ = w.gate(str(w.root / CLEAN_JOB))
        self.assertEqual((code, out), (1, f"gate job {digest}: STOP cap - reason verdicts\n"))
        self.assertEqual(w.run(), again)
        self.assertEqual(w.fake_calls(), [digest])
        self.assertEqual(len(w.rows("run")), 1)


class ToolTest(unittest.TestCase):
    def test_not_installed_or_not_a_tool(self):
        w = World(self)
        cases = (("nothing", "tool nothing is not installed"), ("../fake", "tool ../fake is not installed"),
                 ("lacks-reasons", "tool lacks-reasons does not follow the tool interface"),
                 ("elsewhere", "tool elsewhere names the unknown resource gen.other"))
        for n, (tool, message) in enumerate(cases):
            with self.subTest(tool=tool):
                job = w.job(f"tool-{n}.json", tool=tool)
                w.passed(job)
                before = w.snapshot()
                self.assertEqual(w.run(job), (2, "", f"run: {message}\n"))
                self.assertEqual(w.snapshot(), before)
        self.assertEqual(w.fake_calls(), [])
        self.assertEqual(w.rows("run"), [])


class KeyTest(unittest.TestCase):
    def setUp(self):
        self.w = World(self)
        self.w.passed()

    def assert_unusable(self, reason):
        before = self.w.snapshot()
        self.assertEqual(self.w.run(), (2, "", f"run: resource gen.fake is not usable: {reason}\n"))
        self.assertEqual(self.w.fake_calls(), [])
        self.assertEqual(self.w.snapshot(), before)

    def test_key_missing(self):
        (self.w.secrets / "gen.fake").unlink()
        self.assert_unusable("secret gen.fake: missing")

    def test_key_readable_by_others(self):
        self.w.key(KEY, 0o644)
        self.assert_unusable("secret gen.fake: readable by others")

    def test_secrets_dir_unset(self):
        self.w.local(STATE_DIR=self.w.state, FAKE_LOG=self.w.fake_log)
        self.assert_unusable("setting SECRETS_DIR is empty")


class NoValueTest(unittest.TestCase):
    def test_the_key_is_nowhere(self):
        w = World(self)
        printed = []
        jobs = (str(w.root / CLEAN_JOB), w.script("crash", crash=True, outputs={}, usd=1, compute_min=1),
                w.script("shout", shout=True, fail="rejected", outputs={"a.png": "a\n"}, usd=1, compute_min=1))
        results = []
        for job in jobs:
            # Every PASS first: the clean run's manifest commit moves the pull's head, so a later gate stops.
            w.passed(job)
        for job in jobs:
            code, out, err = w.run(job)
            results.append((code, err))
            printed += [out, err]
        self.assertEqual(results, [(0, ""), (1, "run: tool fake failed: tool crashed\n"),
                                   (1, "run: tool fake failed: rejected\n")])
        self.assertEqual(len(w.fake_calls()), 3)
        texts = printed + [p.read_bytes().decode("utf-8", "replace") for p in w.state.rglob("*") if p.is_file()]
        texts += [p.read_text() for p in w.dir.rglob("*") if p.is_file()]
        texts.append(w.git("log", "--all", "-p", "--format=%H %an %s%n%b"))
        for text in texts:
            self.assertNotIn(KEY, text)

    def test_a_tool_that_ends_the_process(self):
        w = World(self)
        job = w.job("exits.json", tool="exits")
        digest = w.passed(job)
        self.assertEqual(w.run(job), (1, line(digest, "FAILED", "2.5", "30") + "\n",
                                      "run: tool exits failed: tool crashed\n"))
        self.assertEqual([r["verdict"] for r in w.rows("run")], ["FAILED"])


class FailureTest(unittest.TestCase):
    def setUp(self):
        self.w = World(self)

    def test_failure_half_way(self):
        w = self.w
        job = w.script("half", outputs={"part-1.png": "half a frame\n"}, fail="rejected", usd=0.4, compute_min=5)
        digest = w.passed(job)
        branch = w.head(BRANCH)
        failed = line(digest, "FAILED", "0.4", "5")
        self.assertEqual(w.run(job), (1, failed + "\n", "run: tool fake failed: rejected\n"))
        [row] = w.rows("run")
        self.assertEqual((row["verdict"], row["usd"], row["compute_min"], row["cap"]), ("FAILED", "0.4", "5", "-"))
        self.assertEqual(w.head(BRANCH), branch)
        self.assertEqual((w.job_dir(digest) / "out" / "part-1.png").read_text(), "half a frame\n")
        self.assertEqual(json.loads((w.job_dir(digest) / "result.json").read_text()),
                         {"compute_min": "5", "outputs": [], "reason": "rejected", "usd": "0.4", "verdict": "FAILED"})
        self.assertEqual(w.run(job), (1, failed + " (already recorded)\n", ""))
        self.assertEqual(w.fake_calls(), [digest])

    def test_a_reason_the_tool_does_not_list(self):
        job = self.w.script("other", outputs={}, fail="anything else", usd=0.4, compute_min=5)
        digest = self.w.passed(job)
        self.assertEqual(self.w.run(job), (1, line(digest, "FAILED", "0.4", "5") + "\n",
                                           "run: tool fake failed: tool failed\n"))

    def test_a_failure_with_no_amounts(self):
        job = self.w.script("unknown", outputs={}, fail="rejected")
        digest = self.w.passed(job)
        self.assertEqual(self.w.run(job), (1, line(digest, "FAILED", "2.5", "30") + "\n",
                                           "run: tool fake failed: rejected\n"))
        [row] = self.w.rows("run")
        self.assertEqual((row["usd"], row["compute_min"]), ("2.5", "30"))


class InterfaceTest(unittest.TestCase):
    def setUp(self):
        self.w = World(self)

    def test_a_tool_that_breaks_the_interface(self):
        frames = {"shot-1.png": "a frame\n"}
        cases = {"negative": {"outputs": frames, "returns": {"usd": -1, "compute_min": 1}},
                 "one-amount": {"outputs": frames, "returns": {"usd": 1}},
                 "five-places": {"outputs": frames, "returns": {"usd": 0.00001, "compute_min": 1}},
                 "empty-output": {"outputs": {"shot-1.png": ""}, "usd": 1, "compute_min": 1},
                 "bad-name": {"outputs": {"Frame 1.png": "a frame\n"}, "usd": 1, "compute_min": 1}}
        branch = self.w.head(BRANCH)
        for name, script in cases.items():
            with self.subTest(name):
                job = self.w.script(name, **script)
                digest = self.w.passed(job)
                self.assertEqual(self.w.run(job), (1, line(digest, "FAILED", "2.5", "30") + "\n",
                                                   "run: tool fake failed: tool broke the interface\n"))
                self.assertEqual([r["verdict"] for r in self.w.rows("run") if r["hash"] == digest], ["FAILED"])
        self.assertEqual(self.w.head(BRANCH), branch)

    def test_clean_twin_with_no_outputs(self):
        job = self.w.script("none", outputs={}, usd=0.5, compute_min=3)
        digest = self.w.passed(job)
        branch, log = self.w.head(BRANCH), self.w.log()
        self.assertEqual(self.w.run(job), (0, line(digest, "DONE", "0.5", "3") + "\n", ""))
        self.assertEqual(len(self.w.rows("run")), 1)
        self.assertEqual(self.w.head(BRANCH), branch)
        self.assertEqual(self.w.log()[len(log):], [{"op": "push", "number": None, "ref": "trunk"}])


class OverExpectedTest(unittest.TestCase):
    def test_cost_more_than_expected(self):
        w = World(self)
        job = w.script("dear", outputs={"shot-1.png": "a frame\n"}, usd=3, compute_min=20)
        digest = w.passed(job)
        self.assertEqual(w.run(job), (0, line(digest, "DONE", "3", "20") + "\n",
                                      f"run: job {digest} cost more than expected\n"))
        [row] = w.rows("run")
        self.assertEqual((row["verdict"], row["usd"]), ("DONE", "3"))


class DownTest(unittest.TestCase):
    def test_resource_down_then_up(self):
        w = World(self)
        job = w.script("flaky", down=True, outputs={"shot-1.png": "a frame\n"}, usd=1.5, compute_min=20)
        digest = w.passed(job)
        before = w.snapshot()
        self.assertEqual(w.run(job), (2, "", "run: resource gen.fake is down\n"))
        self.assertEqual(w.rows("run"), [])
        self.assertFalse(w.job_dir(digest).exists())
        self.assertEqual(w.refs(), before[0])
        self.assertEqual(w.log(), before[1])
        self.assertEqual(w.run(job), (0, line(digest, "DONE", "1.5", "20") + "\n", ""))
        self.assertEqual(w.fake_calls(), [digest, digest])


class NeverTwiceTest(unittest.TestCase):
    def setUp(self):
        self.w = World(self)
        self.digest = self.w.passed()
        self.done = line(self.digest, "DONE", "1.5", "20")

    def assert_recorded_once(self):
        self.assertEqual(self.w.fake_calls(), [self.digest])
        rows = self.w.manifest().splitlines()[1:]
        self.assertEqual(rows, manifest_rows(self.digest, clean_outputs()))
        self.assertEqual(len(self.w.rows("run")), 1)

    def test_died_after_the_call(self):
        code, out, err = self.w.run(gh=FlakyPush(self.w.dir, fails={1}))
        self.assertEqual((code, out, err), (2, "", "run: push: exit 1\n"))
        self.assertTrue((self.w.job_dir(self.digest) / "result.json").is_file())
        self.assertEqual(self.w.rows("run"), [])
        self.assertEqual(self.w.run(apply=False), (0, f"run job {self.digest}: would record the earlier call\n", ""))
        log = self.w.log()
        self.assertEqual(self.w.run(), (0, self.done + "\n", ""))
        self.assert_recorded_once()
        self.assertEqual(self.w.log()[len(log):], [{"op": "push", "number": None, "ref": BRANCH},
                                                   {"op": "push", "number": None, "ref": "trunk"}])

    def test_died_at_the_second_push(self):
        # The ledger row is committed on the local base before its push fails: the base pushes it later.
        code, out, err = self.w.run(gh=FlakyPush(self.w.dir, fails={2}))
        self.assertEqual((code, out, err), (2, "", "run: push: exit 1\n"))
        self.assertEqual(self.w.run(), (0, self.done + " (already recorded)\n", ""))
        self.assert_recorded_once()

    def test_died_during_the_call(self):
        (self.w.job_dir(self.digest) / "out").mkdir(parents=True)
        self.assertEqual(self.w.run(apply=False), (0, f"run job {self.digest}: would record the earlier call\n", ""))
        self.assertEqual(self.w.run(), (1, line(self.digest, "FAILED", "2.5", "30") + "\n",
                                        "run: tool fake failed: no result\n"))
        self.assertEqual(self.w.fake_calls(), [])
        [row] = self.w.rows("run")
        self.assertEqual((row["verdict"], row["usd"], row["compute_min"]), ("FAILED", "2.5", "30"))

    def test_outputs_changed_after_the_call(self):
        self.assertEqual(self.w.run(gh=FlakyPush(self.w.dir, fails={1}))[0], 2)
        shot = self.w.job_dir(self.digest) / "out" / "shot-1.png"
        message = f"run: the outputs of job {self.digest} changed after the call\n"
        shot.write_text("a different frame\n")
        self.assertEqual(self.w.run(), (2, "", message))
        shot.unlink()
        self.assertEqual(self.w.run(), (2, "", message))
        self.assertEqual(self.w.rows("run"), [])


class OnePullTest(unittest.TestCase):
    def setUp(self):
        self.w = World(self)
        self.w.passed()

    def assert_refused(self):
        before = self.w.snapshot()
        self.assertEqual(self.w.run(), (2, "", f"run: ticket #{TICKET} does not have exactly one open pull\n"))
        self.assertEqual(self.w.fake_calls(), [])
        self.assertEqual(self.w.snapshot(), before)

    def test_pull_closed(self):
        self.w.edit("pulls", PULL, state="closed")
        self.assert_refused()

    def test_two_open_pulls(self):
        self.w.branch("feat/RN-1-again")
        self.w.edit("pulls", 5, base_ref="trunk", body=f"Ticket: #{TICKET}\n", created_at="2026-11-01T12:00:00Z",
                    head_ref="feat/RN-1-again", labels=[], merged_at=None, number=5, state="open", ticket=TICKET,
                    title="More work for #3")
        self.assert_refused()



class RunOnPullTest(unittest.TestCase):
    """ISSUE-96: `--pull N` reads the job file and its script at the head of pull request N."""

    JOB = "jobs/renders/requests/r1.json"
    SCRIPT = "renders/requests/r1.json"

    def setUp(self):
        self.w = World(self)
        self.script = json.dumps({"outputs": {"r1.png": "from the pull\n"}, "usd": 1, "compute_min": 5}) + "\n"
        job = json.loads((self.w.root / CLEAN_JOB).read_text())
        job["inputs"] = [{"path": self.SCRIPT, "sha256": hashlib.sha256(self.script.encode()).hexdigest()}]
        self.job = job
        self.digest = hashlib.sha256(json.dumps(job, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.on_branch({self.SCRIPT: self.script, self.JOB: json.dumps(job, indent=1)})

    def on_branch(self, files):
        """Commit ``files`` on the pull's branch and record a fresh lint and checker verdict on it."""
        w = self.w
        w.git("checkout", "-q", BRANCH)
        for rel, text in files.items():
            w.write(rel, text)
        w.git("add", "-A")
        w.git("commit", "-q", "-m", "files on the pull")
        w.git("checkout", "-q", "trunk")
        head = w.head(BRANCH)
        w.record(PULL, {"body": f"[LINT] PASS\nstructure: PASS\nreviewed head {head} manifest none\n",
                        "created_at": "2026-11-01T12:10:00Z", "id": 100, "role": "program:lint"})
        w.record(PULL, {"body": f"[INSPECTOR] APPROVED\nfine\nreviewed head {head} manifest none\n",
                        "created_at": "2026-11-01T12:20:00Z", "id": 101, "role": "inspector"})

    def command(self, main, *argv):
        return self.w._main(main, [*argv, "--root", str(self.w.root), "--apply"], None)

    def test_gate_then_run_on_the_pull(self):
        w = self.w
        self.assertEqual(self.command(cmd_run.main, self.JOB, "--pull", str(PULL)),
                         (2, "", f"run: job {self.digest} has no counting GATE PASS\n"))
        code, out, err = self.command(cmd_gate.main, self.JOB, "--pull", str(PULL))
        self.assertEqual((code, err), (0, ""), out)
        branch = w.head(BRANCH)
        code, out, err = self.command(cmd_run.main, self.JOB, "--pull", str(PULL))
        self.assertEqual((code, out, err), (0, line(self.digest, "DONE", "1", "5") + "\n", ""))
        self.assertEqual(w.fake_calls(), [self.digest])
        self.assertEqual((w.job_dir(self.digest) / "out" / "r1.png").read_text(), "from the pull\n")
        self.assertEqual(w.git("log", "-1", "--format=%an|%s|%P", BRANCH).strip(),
                         f"factory|manifest: job {self.digest[:12]}|{branch}")
        self.assertIn(f"\tjob {self.digest}\n", w.manifest())
        [row] = w.rows("run")
        self.assertEqual((row["hash"], row["verdict"]), (self.digest, "DONE"))
        # A second run is already recorded, after the manifest commit moved the head.
        code, out, err = self.command(cmd_run.main, self.JOB, "--pull", str(PULL))
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.endswith(" (already recorded)\n"))
        self.assertEqual(w.fake_calls(), [self.digest])

    def test_without_pull_cannot_be_read(self):
        here = os.getcwd()
        os.chdir(self.w.root)
        self.addCleanup(os.chdir, here)
        self.assertEqual(self.command(cmd_run.main, self.JOB), (2, "", f"run: job file {self.JOB} cannot be read\n"))

    def test_script_changed_on_the_pull_after_the_pass(self):
        self.assertEqual(self.command(cmd_gate.main, self.JOB, "--pull", str(PULL))[0], 0)
        self.on_branch({self.SCRIPT: "{}\n"})
        self.assertEqual(self.command(cmd_run.main, self.JOB, "--pull", str(PULL)),
                         (2, "", f"run: input {self.SCRIPT} does not match its hash on pull #{PULL}\n"))
        self.assertEqual(self.w.fake_calls(), [])

if __name__ == "__main__":
    unittest.main()
