"""Tests for kit/lib/cmd_run.py: `factory run` on a job the gate passed, end to end.

The production is tests/fixtures/run/production, made into a git repository on branch `trunk` in a
temporary directory, with one branch per recorded pull. GitHub is a copy of tests/fixtures/run/github.
A comment body's ``{head:N}`` stands for the head commit of pull N's branch, filled in when it is copied.
The tools are tests/fixtures/run/tools and the register tests/fixtures/run/resources.json. The world
writes ``production.local.env`` with ``STATE_DIR``, ``SECRETS_DIR`` and ``FAKE_LOG`` in temporary
directories and a key file of mode 0600. A PASS is made by the real gate. Nothing reaches the network. The previewer
of the preview comment is the stand-in ``test_handoff.StubPreviews``.
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
sys.path.insert(0, str(ROOT / "tests"))

from kit.lib import cmd_gate, cmd_media, cmd_run, gitstate, github, ledger, lint, resources  # noqa: E402
from test_assets import AssetStore  # noqa: E402
from test_media import KEY_ID, REGION, SECRET, FakeStore, free_port  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "run"
NOW = "2026-11-02T09:00:00Z"
TICKET, PULL, BRANCH = 3, 4, "feat/RN-1-ep01"
CLEAN_JOB = "jobs/clean.json"
KEY = "seeded-key-5d1c0b7e"
MANIFEST_HEADER = "id\tsha256\tbytes\tsource\n"
BIN = hashlib.sha256(b"wren glb").hexdigest()
WREN_PATH = "production/assets/wren/assets/state.json"
WREN_TEXT = json.dumps({"asset": "wren", "kind": "character", "sha256": BIN}, indent=1) + "\n"


def digest_of(path):
    job = json.loads(Path(path).read_text())
    return hashlib.sha256(json.dumps(job, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def stub_previews():
    """The previewer the preview comment uses (``kit/lib/handoff.py``): a stand-in, so a run is the same
    whether or not ``kit.lib.preview`` is installed."""
    from test_handoff import StubPreviews
    return StubPreviews()


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
                      mock.patch.object(resources, "REGISTER", FIX / "resources.json"),
                      mock.patch.dict(sys.modules, {"kit.lib.preview": stub_previews()})):
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

    def attempt(self, path, attempt):
        """The job file at ``path`` rewritten with one more key, ``attempt``; its path."""
        job = json.loads(Path(path).read_text())
        job["attempt"] = attempt
        Path(path).write_text(json.dumps(job, indent=1))
        return path

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

    def test_every_shipped_tool_has_the_tool_shape(self):
        register, _ = resources.load(resources.REGISTER)
        ids = {resource["id"] for resource in register["resources"]}
        for path in sorted(cmd_run.TOOLS_DIR.glob("*.py")):
            if path.name == "__init__.py":
                continue
            with self.subTest(path.name):
                self.assertIsNotNone(cmd_run.TOOL_NAME.fullmatch(path.stem))
                module = cmd_run._load_tool(path.stem)
                self.assertIsInstance(module.RESOURCE, str)
                self.assertNotEqual(module.RESOURCE, "")
                self.assertIn(module.RESOURCE, ids)
                self.assertIsInstance(module.REASONS, tuple)
                self.assertNotEqual(module.REASONS, ())
                for reason in module.REASONS:
                    self.assertIsInstance(reason, str)
                self.assertTrue(callable(module.run))


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
        self.assertEqual(w.run(job), (2, "", "run: resource gen.fake is down (#5)\n"))
        self.assertEqual(w.rows("run"), [])
        self.assertFalse(w.job_dir(digest).exists())
        self.assertEqual(w.refs(), before[0])
        [entry] = w.log()[len(before[1]):]
        self.assertEqual(w.log()[:len(before[1])], before[1])
        self.assertEqual((entry["op"], entry["number"], entry["title"], entry["labels"]),
                         ("create_issue", 5, "[DOWN] gen.fake", ["resource:down"]))
        self.assertEqual(w.run(job), (2, "", "run: resource gen.fake is down (#5)\n"))
        self.assertEqual(w.fake_calls(), [digest])
        w.edit("issues", 5, state="closed")
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

    def test_an_asset_input_is_read_on_the_base(self):
        self.w.commit(WREN_PATH, WREN_TEXT)
        job = {**self.job, "inputs": self.job["inputs"] + [{"path": "asset:wren", "sha256": BIN}]}
        pulled = json.dumps({"asset": "wren", "kind": "character", "sha256": BIN, "triangles": 1}, indent=1) + "\n"
        self.on_branch({self.JOB: json.dumps(job, indent=1), WREN_PATH: pulled})
        self.assertEqual(self.command(cmd_gate.main, self.JOB, "--pull", str(PULL))[0], 0)
        calls, real = [], cmd_run._call

        def spy(tool, call, expected):
            calls.append([(item["path"], item["text"]) for item in call["inputs"]])
            return real(tool, call, expected)

        with mock.patch.object(cmd_run, "_call", spy):
            code, out, err = self.command(cmd_run.main, self.JOB, "--pull", str(PULL))
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(calls, [[(self.SCRIPT, self.script), ("asset:wren", WREN_TEXT)]])


class AssetInputTest(unittest.TestCase):
    """ISSUE-536: an input `asset:<id>` reaches the tool as the asset's locked state on the base."""

    def setUp(self):
        self.calls, real = [], cmd_run._call

        def spy(tool, call, expected):
            self.calls.append([(item["path"], item["sha256"], item["text"]) for item in call["inputs"]])
            return real(tool, call, expected)

        patch = mock.patch.object(cmd_run, "_call", spy)
        patch.start()
        self.addCleanup(patch.stop)

    def world(self, state=WREN_TEXT, digest=BIN):
        """A fresh world with ``state`` committed at the asset's state path (none when None); the job's path."""
        self.w = World(self)
        if state is not None:
            self.w.commit(WREN_PATH, state)
        inputs = json.loads((self.w.root / CLEAN_JOB).read_text())["inputs"]
        return self.w.job("wren.json", inputs=inputs + [{"path": "asset:wren", "sha256": digest}])

    def test_the_tool_gets_the_locked_state(self):
        job = self.world()
        digest = self.w.passed(job)
        self.assertEqual(self.w.run(job), (0, line(digest, "DONE", "1.5", "20") + "\n", ""))
        [inputs] = self.calls
        self.assertEqual(inputs[1], ("asset:wren", BIN, WREN_TEXT))
        self.assertEqual(self.w.fake_calls(), [digest])

    def test_no_state_or_another_sha256_calls_no_tool(self):
        for args, reason in (((None,), "has no locked state on the base"),
                             ((WREN_TEXT, "0" * 64), "does not match the sha256 of its locked state")):
            with self.subTest(reason=reason):
                job = self.world(*args)
                self.calls.clear()
                before = self.w.snapshot()
                self.assertEqual(self.w.run(job), (2, "", f"run: input asset:wren {reason}\n"))
                self.assertEqual(self.calls, [])
                self.assertEqual(self.w.fake_calls(), [])
                self.assertEqual(self.w.snapshot(), before)


class ManifestInputTest(unittest.TestCase):
    """ISSUE-480: a job that names the manifest after its request runs after another job of the pull added rows."""

    ROWS = {"ama-ref": f"ama-ref\t{'a' * 64}\t2048\tsources/ama.png",
            "tide-ref": f"tide-ref\t{'b' * 64}\t4096\tsources/tide.png",
            "kite-ref": f"kite-ref\t{'d' * 64}\t512\tsources/kite.png",
            "wick-ref": f"wick-ref\t{'e' * 64}\t1\tsources/wickabel.png"}
    NOT_OURS = "name\tholder\tlink\nWickabel\tthe makers of Skarrowfen\thttps://example.invalid/1\n"
    REFS = {"r1": ["media:ama-ref"], "r2": ["media:ama-ref", "media:tide-ref"]}
    NAMES = "'Wickabel' (research/not-ours.tsv:2)"

    def world(self, rows=("ama-ref", "tide-ref"), not_ours=False, jobs=("r1", "r2")):
        """A fresh world whose branch holds the manifest with ``rows`` and the jobs ``jobs`` of ``REFS``."""
        self.w, self.count = World(self), 0
        if not_ours:
            self.w.commit("research/not-ours.tsv", self.NOT_OURS)
        manifest = MANIFEST_HEADER + "".join(self.ROWS[r] + "\n" for r in rows)
        files = {gitstate.MANIFEST: manifest}
        for name in jobs:
            files.update(self.request(name, self.REFS[name], manifest))
        self.on_branch(files)

    def on_branch(self, files):
        """Commit ``files`` on the pull's branch and record a fresh lint and checker verdict on its head and manifest."""
        w = self.w
        w.git("checkout", "-q", BRANCH)
        for rel, text in files.items():
            w.write(rel, text)
        w.git("add", "-A")
        w.git("commit", "-q", "-m", "files on the pull")
        w.git("checkout", "-q", "trunk")
        head, manifest = w.head(BRANCH), gitstate.manifest(w.root, "trunk", BRANCH)
        for role, body in (("program:lint", "[LINT] PASS\nstructure: PASS\n"), ("inspector", "[INSPECTOR] APPROVED\nfine\n")):
            self.count += 1
            w.record(PULL, {"body": f"{body}reviewed head {head} manifest {manifest}\n",
                            "created_at": f"2026-11-01T13:{self.count:02d}:00Z", "id": 200 + self.count, "role": role})

    def request(self, name, refs, manifest, second=gitstate.MANIFEST, only=False):
        """The files of request ``name`` (a fake script with ``refs``) and of its job, whose inputs are the request
        and ``second`` with ``manifest`` as its text (only ``second`` when ``only``)."""
        script = json.dumps({"outputs": {f"{name}.png": f"{name}\n"}, "usd": 1, "compute_min": 1, "refs": refs}) + "\n"
        rel = f"shots/requests/{name}.json"
        inputs = [{"path": rel, "sha256": hashlib.sha256(script.encode()).hexdigest()}] * (not only)
        inputs.append({"path": second, "sha256": hashlib.sha256(manifest.encode()).hexdigest()})
        job = json.loads((self.w.root / CLEAN_JOB).read_text())
        job["inputs"] = inputs
        return {rel: script, self.job(name): json.dumps(job, indent=1)}

    def job(self, name):
        return f"jobs/shots/requests/{name}.json"

    def digest(self, name):
        text = gitstate.show(self.w.root, BRANCH, self.job(name))
        return hashlib.sha256(json.dumps(json.loads(text), sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def command(self, main, name):
        return self.w._main(main, [self.job(name), "--pull", str(PULL), "--root", str(self.w.root), "--apply"], None)

    def gate(self, name):
        code, out, err = self.command(cmd_gate.main, name)
        self.assertEqual((code, err), (0, ""), out)

    def assert_done(self, name):
        self.assertEqual(self.command(cmd_run.main, name), (0, line(self.digest(name), "DONE", "1", "1") + "\n", ""))

    def change_manifest(self, change):
        self.on_branch({gitstate.MANIFEST: change(self.w.manifest())})

    def gate_both_run_r1(self):
        self.gate("r1")
        self.gate("r2")
        self.assert_done("r1")

    def test_both_jobs_run_in_either_order(self):
        for order in (("r1", "r2"), ("r2", "r1")):
            with self.subTest(order=order):
                self.world()
                self.gate("r1")
                self.gate("r2")
                for name in order:
                    self.assert_done(name)
                self.assertEqual(self.w.fake_calls(), [self.digest(n) for n in order])

    def test_a_named_row_changed(self):
        tide, ama = self.ROWS["tide-ref"], self.ROWS["ama-ref"]
        defects = {"tide sha256": ("tide-ref", lambda t: t.replace("b" * 64, "c" * 64)),
                   "tide bytes": ("tide-ref", lambda t: t.replace("\t4096\t", "\t4097\t")),
                   "tide removed": ("tide-ref", lambda t: t.replace(tide + "\n", "")),
                   "tide twice": ("tide-ref", lambda t: t + tide + "\n"),
                   "ama bytes": ("ama-ref", lambda t: t.replace(ama, ama.replace("\t2048\t", "\t2049\t")))}
        for defect, (name, change) in defects.items():
            with self.subTest(defect=defect):
                self.world()
                self.gate_both_run_r1()
                self.change_manifest(change)
                self.assertEqual(self.command(cmd_run.main, "r2"),
                                 (2, "", f"run: input {gitstate.MANIFEST} changed the rows of media:{name} on pull #{PULL}\n"))
                self.assertEqual(self.w.fake_calls(), [self.digest("r1")])

    def test_clean_twin_other_rows_added(self):
        self.world()
        self.gate_both_run_r1()
        self.change_manifest(lambda t: t + self.ROWS["kite-ref"] + "\n")
        self.assert_done("r2")
        comments = json.loads((self.w.dir / "comments" / f"{TICKET}.json").read_text())
        self.assertEqual(sum(c["body"].startswith("[PRODUCER] GATE PASS\n") for c in comments), 2)
        one, two = self.digest("r1"), self.digest("r2")
        self.assertEqual(sorted((r["kind"], r["hash"], r["verdict"]) for r in self.w.rows()),
                         sorted([("job", one, "PASS"), ("job", two, "PASS"), ("run", one, "DONE"), ("run", two, "DONE")]))

    def test_whole_file_for_every_other_input(self):
        def header(t):
            return t.replace(MANIFEST_HEADER, MANIFEST_HEADER.replace("source", "origin"), 1)

        def kite(t):
            return {"shots/notes.tsv": t + self.ROWS["kite-ref"] + "\n"}

        cases = {"header changed": (dict(refs=["media:ama-ref"]), gitstate.MANIFEST, header),
                 "manifest first and only": (dict(refs=["media:ama-ref"], only=True), gitstate.MANIFEST, None),
                 "refs empty": (dict(refs=[]), gitstate.MANIFEST, None),
                 "ref without media:": (dict(refs=["ama-ref"]), gitstate.MANIFEST, None),
                 "another input": (dict(refs=["media:ama-ref"], second="shots/notes.tsv"), "shots/notes.tsv", kite)}
        for case, (args, path, after) in cases.items():
            with self.subTest(case=case):
                self.world(jobs=("r1",))
                self.gate("r1")
                manifest = self.w.manifest()
                files = self.request("r2", manifest=manifest, **args)
                if path != gitstate.MANIFEST:
                    files[path] = manifest
                self.on_branch(files)
                self.gate("r2")
                self.assert_done("r1")
                if after is header:
                    self.change_manifest(header)
                elif after is not None:
                    self.on_branch(after(manifest))
                self.assertEqual(self.command(cmd_run.main, "r2"),
                                 (2, "", f"run: input {path} does not match its hash on pull #{PULL}\n"))
                self.assertEqual(self.w.fake_calls(), [self.digest("r1")])

    def test_gated_version_not_found(self):
        self.world()
        self.gate_both_run_r1()
        with mock.patch.object(cmd_gate, "MANIFEST_VERSIONS", 1):
            self.assertEqual(self.command(cmd_run.main, "r2"),
                             (2, "", f"run: input {gitstate.MANIFEST} does not match its hash on pull #{PULL}\n"))
        with mock.patch.object(cmd_gate, "MANIFEST_VERSIONS", 2):
            self.assert_done("r2")
        files = self.request("r3", self.REFS["r1"], self.w.manifest() + "never committed\n")
        self.on_branch(files)
        self.assertEqual(self.command(cmd_gate.main, "r3"),
                         (2, "", f"gate: input {gitstate.MANIFEST} does not match its hash on pull #{PULL}\n"))

    def test_not_ours_name_in_a_row_the_job_does_not_use(self):
        with self.subTest(case="before the gate"):
            self.world(rows=("ama-ref", "tide-ref", "wick-ref"), not_ours=True)
            self.gate_both_run_r1()
            self.assert_done("r2")
        with self.subTest(case="after the first job ran"):
            self.world(not_ours=True)
            self.gate_both_run_r1()
            self.change_manifest(lambda t: t + self.ROWS["wick-ref"] + "\n")
            self.assert_done("r2")

    def test_not_ours_name_in_a_row_the_job_uses(self):
        self.world(rows=("ama-ref", "tide-ref", "wick-ref"), not_ours=True, jobs=("r1",))
        self.on_branch(self.request("r2", ["media:ama-ref", "media:wick-ref"], self.w.manifest()))
        self.gate("r1")
        self.assertEqual(self.command(cmd_gate.main, "r2"),
                         (2, "", f"gate: input {gitstate.MANIFEST} names {self.NAMES}\n"))

    def test_not_ours_name_when_the_whole_file_is_read(self):
        for refs in ([], ["ama-ref"]):
            with self.subTest(refs=refs):
                self.world(rows=("ama-ref", "tide-ref", "wick-ref"), not_ours=True, jobs=())
                self.on_branch(self.request("r2", refs, self.w.manifest()))
                self.assertEqual(self.command(cmd_gate.main, "r2"),
                                 (2, "", f"gate: input {gitstate.MANIFEST} names {self.NAMES}\n"))


class AttemptShapeTest(unittest.TestCase):
    def setUp(self):
        self.w = World(self)

    def assert_refused(self, job, message):
        before = self.w.snapshot()
        self.assertEqual(self.w.gate(job), (2, "", f"gate: {message}\n"))
        self.assertEqual(self.w.run(job), (2, "", f"run: {message}\n"))
        self.assertEqual(self.w.fake_calls(), [])
        self.assertEqual(self.w.snapshot(), before)

    def test_an_attempt_that_is_not_a_whole_number_of_at_least_2(self):
        for value in (1, 0, -1, True, 2.0, "2", None):
            with self.subTest(value=value):
                path = self.w.job("bad-attempt.json", attempt=value)
                self.assert_refused(path, f"job file {path} has an attempt that is not a whole number of at least 2")

    def test_an_attempt_and_a_sixth_key(self):
        path = self.w.job("extra-attempt.json", attempt=2, extra="x")
        self.assert_refused(path, f"job file {path} is not {{ticket, tool, inputs, expected, caller}}")

    def test_clean_twin_without_an_attempt_keeps_its_hash(self):
        self.assertEqual(digest_of(self.w.root / CLEAN_JOB),
                         "cf8408ba831dcee10426143e731b0733c0bd062d82cd7988f9b3ef92c9ab5f6c")

    def test_clean_twin_with_attempt_2_passes(self):
        path = self.w.job("two.json", attempt=2)
        digest = digest_of(path)
        self.assertNotEqual(digest, digest_of(self.w.root / CLEAN_JOB))
        self.assertEqual(self.w.gate(path), (0, f"gate job {digest}: PASS cap - reason within caps\n", ""))


class RetryTest(unittest.TestCase):
    def setUp(self):
        self.w = World(self)

    def failed_once(self):
        """The failing job passed by the real gate and run once; its path and hash."""
        job = self.w.script("half", outputs={}, fail="rejected", usd=0.4, compute_min=5)
        digest = self.w.passed(job)
        self.assertEqual(self.w.run(job), (1, line(digest, "FAILED", "0.4", "5") + "\n",
                                           "run: tool fake failed: rejected\n"))
        return job, digest

    def test_a_failed_job_asked_for_again(self):
        w = self.w
        job, h1 = self.failed_once()
        first = Path(job).read_text()
        w.attempt(job, 2)
        self.assertEqual(json.loads(Path(job).read_text()), {**json.loads(first), "attempt": 2})
        h2 = digest_of(job)
        self.assertNotEqual(h2, h1)
        # No run before the new hash has its own PASS.
        self.assertEqual(w.run(job), (2, "", f"run: job {h2} has no counting GATE PASS\n"))
        self.assertEqual(w.fake_calls(), [h1])
        self.assertEqual(w.gate(job), (0, f"gate job {h2}: PASS cap - reason within caps\n", ""))
        passes = [c for c in json.loads((w.dir / "comments" / f"{TICKET}.json").read_text())
                  if c["role"] == "program:gate" and c["body"].startswith("[PRODUCER] GATE PASS\n")]
        self.assertEqual([c["body"].splitlines()[1] for c in passes], [f"covers: job {h1}", f"covers: job {h2}"])
        failed = line(h2, "FAILED", "0.4", "5")
        self.assertEqual(w.run(job), (1, failed + "\n", "run: tool fake failed: rejected\n"))
        self.assertEqual(w.fake_calls(), [h1, h2])
        self.assertEqual([(r["kind"], r["hash"], r["verdict"]) for r in w.rows()],
                         [("job", h1, "PASS"), ("run", h1, "FAILED"), ("job", h2, "PASS"), ("run", h2, "FAILED")])
        self.assertEqual(ledger.spent(w.rows()), {"usd": Decimal("0.8"), "compute_min": Decimal("10")})
        self.assertEqual(w.run(job), (1, failed + " (already recorded)\n", ""))
        self.assertEqual(w.fake_calls(), [h1, h2])
        Path(job).write_text(first)
        self.assertEqual(w.run(job), (1, line(h1, "FAILED", "0.4", "5") + " (already recorded)\n", ""))
        self.assertEqual(w.fake_calls(), [h1, h2])

    def test_the_caps_count_the_failed_attempt(self):
        self.w.set_env("CAP_EPISODE_USD", "2.6")
        job, _ = self.failed_once()
        h2 = digest_of(self.w.attempt(job, 2))
        self.assertEqual(self.w.gate(job), (1, f"gate job {h2}: STOP cap CAP_EPISODE_USD reason over cap\n", ""))
        self.assertEqual(self.w.run(job), (2, "", f"run: job {h2} has no counting GATE PASS\n"))
        self.assertEqual(len(self.w.fake_calls()), 1)

    def test_clean_twin_within_the_caps(self):
        self.w.set_env("CAP_EPISODE_USD", "2.9")
        job, _ = self.failed_once()
        h2 = digest_of(self.w.attempt(job, 2))
        self.assertEqual(self.w.gate(job), (0, f"gate job {h2}: PASS cap - reason within caps\n", ""))


class MediaStoreTest(unittest.TestCase):
    """ISSUE-481: a DONE job's pictures go to the media store before their manifest rows are committed."""

    PANEL = "GIF89a an invented panel\n"
    NOTES = "not a picture\n"

    def setUp(self):
        self.w = w = World(self)
        self.store = FakeStore(self)
        register = json.loads((FIX / "resources.json").read_text())
        shipped = {r["id"]: r for r in json.loads((ROOT / "kit" / "resources.json").read_text())["resources"]}
        register["resources"] += [shipped["store.media"], shipped["store.assets"]]
        self.register = w.tmp / "resources.json"
        self.register.write_text(json.dumps(register, indent=2) + "\n")
        self.patch(resources, "REGISTER", self.register)
        self.media_key()
        self.settings()

    # -- the world

    def patch(self, target, name, value):
        patch = mock.patch.object(target, name, value)
        patch.start()
        self.addCleanup(patch.stop)

    def media_key(self, name="store.media"):
        path = self.w.secrets / name
        path.write_text(f"{KEY_ID}\n{SECRET}\n{REGION}\n")
        path.chmod(0o600)

    def settings(self, **changes):
        w = self.w
        w.local(**{"STATE_DIR": w.state, "SECRETS_DIR": w.secrets, "FAKE_LOG": w.fake_log,
                   "MEDIA_STORE": self.store.url("/b"), "MEDIA_PUBLIC_BASE": self.store.url("/pub"), **changes})

    def panel_job(self):
        job = self.w.script("panel", outputs={"panel.gif": self.PANEL, "notes.txt": self.NOTES}, usd=1.5,
                            compute_min=20)
        return job, self.w.passed(job)

    def outputs(self, texts):
        return sorted(({"bytes": len(t.encode()), "name": n, "sha256": hashlib.sha256(t.encode()).hexdigest()}
                       for n, t in texts.items()), key=lambda o: o["name"])

    def check(self, tree, mid):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_media.main(["check", mid, "--root", str(tree)])
        return code, out.getvalue(), err.getvalue()

    # -- the checks

    def assert_done(self, job, digest):
        """The run of ``job`` is DONE: the panel is in the store after one PUT, then its rows are committed."""
        w = self.w
        branch, trunk = w.head(BRANCH), w.head()
        self.assertEqual(w.run(job), (0, line(digest, "DONE", "1.5", "20") + "\n", ""))
        panel = self.PANEL.encode()
        panel_sha = hashlib.sha256(panel).hexdigest()
        self.assertEqual(self.store.objects, {panel_sha: panel})
        self.assertEqual([r["path"] for r in self.store.requests if r["method"] == "PUT"], [f"/b/{panel_sha}"])
        self.assertEqual(w.git("diff", "--name-only", branch, BRANCH).split(), [gitstate.MANIFEST])
        outputs = self.outputs({"panel.gif": self.PANEL, "notes.txt": self.NOTES})
        self.assertEqual(w.manifest(), MANIFEST_HEADER + "".join(r + "\n" for r in manifest_rows(digest, outputs)))
        self.assertEqual(w.git("diff", "--name-only", trunk, "trunk").split(), [ledger.PATH])
        [row] = w.rows("run")
        self.assertEqual((row["hash"], row["verdict"], row["usd"]), (digest, "DONE", "1.5"))
        self.assertEqual(w.fake_calls(), [digest])
        tree = w.tmp / "exported"
        gitstate.export(w.root, BRANCH, tree)
        shutil.copyfile(w.root / "production.local.env", tree / "production.local.env")
        self.assertEqual(self.check(tree, f"{digest[:16]}-panel.gif"), (0, f"media {digest[:16]}-panel.gif: OK\n", ""))
        self.assertEqual(self.check(tree, f"{digest[:16]}-notes.txt"),
                         (1, f"media {digest[:16]}-notes.txt: FAILED public link answered 404\n", ""))

    def assert_refused(self, job, digest, message):
        """The run of ``job`` is refused after the call with ``message``: nothing is recorded."""
        w = self.w
        branch, log = w.head(BRANCH), w.log()
        self.assertEqual(w.run(job), (2, "", f"run: {message}\n"))
        self.assertEqual(w.fake_calls(), [digest])
        self.assertTrue((w.job_dir(digest) / "result.json").is_file())
        self.assertEqual(w.head(BRANCH), branch)
        self.assertEqual(w.rows("run"), [])
        self.assertEqual(w.log(), log)
        self.assertEqual(self.store.objects, {})

    # -- Behaviour 1 to 7

    def test_picture_put_before_its_row(self):
        job, digest = self.panel_job()
        self.assert_done(job, digest)

    def test_store_refuses_then_twin_rerun(self):
        job, digest = self.panel_job()
        self.store.status["PUT"] = (500, b"")
        self.assert_refused(job, digest, "the media store did not take panel.gif: store answered 500")
        del self.store.status["PUT"]
        self.store.requests.clear()
        self.assert_done(job, digest)

    def test_store_unreachable_then_twin_rerun(self):
        job, digest = self.panel_job()
        self.settings(MEDIA_STORE=f"http://127.0.0.1:{free_port()}/b")
        self.assert_refused(job, digest, "the media store did not take panel.gif: store unreachable")
        self.assertEqual(self.store.requests, [])
        self.settings()
        self.assert_done(job, digest)

    def test_store_not_usable_then_twin_rerun(self):
        job, digest = self.panel_job()
        (self.w.secrets / "store.media").unlink()
        self.assert_refused(job, digest, "resource store.media is not usable: secret store.media: missing")
        self.assertEqual(self.store.requests, [])
        self.media_key()
        self.assert_done(job, digest)

    def test_register_without_the_media_store(self):
        job, digest = self.panel_job()
        self.patch(resources, "REGISTER", FIX / "resources.json")
        self.assert_refused(job, digest, "the register has no resource store.media")
        self.assertEqual(self.store.requests, [])

    def test_twin_no_picture_sends_nothing(self):
        w = self.w
        self.settings(MEDIA_STORE="", MEDIA_PUBLIC_BASE="")
        job = w.script("notes", outputs={"notes.txt": self.NOTES}, usd=1.5, compute_min=20)
        digest = w.passed(job)
        self.assertEqual(w.run(job), (0, line(digest, "DONE", "1.5", "20") + "\n", ""))
        self.assertEqual(w.manifest(), MANIFEST_HEADER + "".join(
            r + "\n" for r in manifest_rows(digest, self.outputs({"notes.txt": self.NOTES}))))
        self.assertEqual(self.store.requests, [])

    def test_twin_stage_files_tool_puts_nothing_in_the_media_store(self):
        w = self.w
        tools = w.tmp / "tools"
        tools.mkdir()
        (tools / "fake.py").write_text((FIX / "tools" / "fake.py").read_text() + '\nSTAGE_FILES = ("notes.txt",)\n')
        self.patch(cmd_run, "TOOLS_DIR", tools)
        assets = AssetStore(self)
        self.media_key("store.assets")
        self.settings(ASSET_STORE=assets.url("/b"))
        job, digest = self.panel_job()
        self.assertEqual(w.run(job), (0, line(digest, "DONE", "1.5", "20") + "\n", ""))
        panel = self.PANEL.encode()
        # The preview comment (kit/lib/handoff.py) attaches the picture output on GitHub. No output is uploaded.
        self.assertEqual([r["path"] for r in self.store.requests if r["method"] == "PUT"], [])
        self.assertEqual(self.store.objects, {})
        self.assertEqual(assets.objects, {hashlib.sha256(panel).hexdigest(): panel})
        self.assertIsNone(w.manifest())


class RightsRefusalTest(unittest.TestCase):
    """ISSUE-274: a run reads research/not-ours.tsv on the base again, after the gate's PASS."""

    def test_a_name_listed_after_the_gate_pass(self):
        w = World(self)
        job = w.script("pier", outputs={"pier.png": "Draw Wickabel on the pier.\n"}, usd=1, compute_min=2)
        digest = w.passed(job)
        w.commit("research/not-ours.tsv", "name\tholder\tlink\nWickabel\tthe makers of Skarrowfen\t"
                                          "https://example.invalid/1\n")
        self.assertEqual(w.run(job), (2, "", "run: input scripts/pier.json names 'Wickabel' "
                                             "(research/not-ours.tsv:2)\n"))
        self.assertEqual(w.fake_calls(), [])
        self.assertEqual([(r["kind"], r["hash"]) for r in w.rows()], [("job", digest)])


if __name__ == "__main__":
    unittest.main()
