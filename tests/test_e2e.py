"""End to end, offline: a fixture ticket goes from LAUNCH to LOCK (system brief section 17, wave 2).

Every world is a production built by ``factory init --rooms story`` in a temporary directory, with the
two-stage pipeline of tests/fixtures/e2e/pipeline.json (premise, beats), a locked premise of ep01
(issue 1, merged pull 2) and the open beats ticket, issue 3. GitHub is ``github.Recorded`` and the model
is the fake harness. The real commands run in process; the clock moves one minute before each of them.

The base world is the happy path stopped just before its lock (eight ticks). It is built once, kept as a
snapshot and put back at the same path for each test that uses it. Rules 3, 5 and 6 and the refusal
tests build a world of their own.
"""

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import (blind, cmd_freeze, cmd_init, cmd_launch, cmd_lock, cmd_roles, cmd_run, cmd_tick,  # noqa: E402
                     cmd_unfreeze, github, gitstate, ledger, lint, production, resources)

FIX = ROOT / "tests" / "fixtures" / "e2e"
FAKE = ROOT / "tests" / "fixtures" / "harness" / "fake-harness"
START = datetime(2026, 11, 11, 12, 0, 0, tzinfo=timezone.utc)
SECRETS = "secrets-e2e-quokka-4711"
TOKEN = "token-e2e-wombat-0815-never-printed\n"
IDENTITY = ("-c", "user.name=e2e tester", "-c", "user.email=e2e@example.invalid")
LAST = re.compile(r"tick: (\d+) steps, (\d+) need, (\d+) wait, (\d+) failed\n\Z")
LABEL = "needs:orchestrator"
REF = "beats/EP01-story-writer-1"
BEATS = "episodes/ep01/beats.md"
SETTINGS = {"REPO": "fixture/show", "MODEL_MAKER": "fake-maker", "MODEL_CHECKER": "fake-checker",
            "MODEL_OTHER": "fake-other"}
HAPPY_LINES = ["STEP #3 launch story-writer on #3", "STEP #3 lint PR #4", "STEP #3 launch story-editor on PR #4",
               "STEP #3 launch story-writer on PR #4", "STEP #3 lint PR #4", "STEP #3 launch story-editor on PR #4",
               "STEP #3 gate PR #4", "STEP #3 sync PR #4", "STEP #3 lock PR #4"]
BASE_HEADER = "PR #4 #3 story/beats ep01: NO 1 rule(s)"
HEX64 = re.compile(r"\b[0-9a-f]{64}\b")
HEX40 = re.compile(r"\b[0-9a-f]{40}\b")


def fixture(name):
    return (FIX / name).read_text(encoding="utf-8")


def pipeline():
    return json.loads(fixture("pipeline.json"))


EVIDENCE = [{"criterion": "the beats follow the format", "result": "eight beats, 240 s",
             "command": "factory lint --only structure"}]


def step(match, marker, files=None, **extra):
    return {"match": match, "result": {"marker": marker, "summary": f"{marker.lower()} from the fake.", **extra},
            "files": files or {}}


def writer_ready(text):
    return step("You are the story writer", "READY", {BEATS: text}, evidence=EVIDENCE)


def writer_addressed(text, note="B1"):
    return step("You are the story writer", "ADDRESSED", {BEATS: text}, evidence=EVIDENCE,
                addressed=[{"note": note, "by": "story-editor", "action": "changed",
                            "text": "The find beat now costs something."}])


def editor_notes(note="B1"):
    return step("You are the story editor", "NOTES",
                notes=[{"id": note, "severity": "Blocker", "text": "The find beat gives Wren the wick for free.",
                        "evidence": "episodes/ep01/beats.md B5"}])


def editor_approved():
    return step("You are the story editor", "APPROVED", evidence=EVIDENCE)


def happy_script():
    return [writer_ready(fixture("beats-1.md")), editor_notes(), writer_addressed(fixture("beats-2.md")),
            editor_approved()]


def first(comment):
    return comment["body"].split("\n", 1)[0]


def last_line(comment):
    return comment["body"].rstrip("\n").split("\n")[-1]


class World:
    """A production in ``<tmp>/show`` with a recorded GitHub, a state directory and the fake harness."""

    def __init__(self, entries=None):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="e2e-")))
        self.backup = None
        self.minutes = 0
        self.show = self.tmp / "show"
        self.github = self.tmp / "github"
        self.state = self.tmp / "state"
        self.harness = self.tmp / "harness"
        self.secrets = self.tmp / SECRETS
        for directory in (self.show, self.state, self.harness, *(self.github / k for k in ("issues", "pulls",
                                                                                          "comments"))):
            directory.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")
        code, _, err = self.run(cmd_init.main, str(self.show), "--rooms", "story")
        assert code == 0, err
        kit = self.show / ".factory"
        (kit / "pipeline.json").write_text(json.dumps(entries or pipeline(), indent=1) + "\n")
        for path in sorted((kit / "formats").iterdir()):
            if path.name != "episode-circle.md":
                path.unlink()
        env = self.show / "production.env"
        env.write_text("".join(f"{line.split('=', 1)[0]}={SETTINGS[line.split('=', 1)[0]]}\n"
                               if line.split("=", 1)[0] in SETTINGS else line + "\n"
                               for line in env.read_text().splitlines()))
        shutil.copy2(FAKE, self.harness / "fake-harness")
        (self.secrets / "inference.primary").mkdir(parents=True)
        token = self.secrets / "inference.primary" / "acct-one"
        token.write_text(TOKEN)
        token.chmod(0o600)
        local = {"STATE_DIR": self.state, "SECRETS_DIR": self.secrets, "HARNESS": self.harness / "fake-harness",
                 "ACCOUNTS_MAKER": "acct-one", "ACCOUNTS_CHECKER": "acct-one", "ACCOUNTS_OTHER": "acct-one",
                 "SANDBOX": "off", "GATEWAY": "off"}
        (self.show / "production.local.env").write_text("".join(f"{k}={v}\n" for k, v in local.items()))
        self.write("episodes/ep01/premise.md", fixture("premise.md"))
        self.commit("the production")
        for path in sorted((FIX / "github").glob("*/*.json")):
            shutil.copyfile(path, self.github / path.parent.name / path.name)
        self.gh = github.Recorded(self.github)
        self.script()

    def remove(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        if self.backup is not None:
            shutil.rmtree(self.backup.parent, ignore_errors=True)

    # -- the snapshot

    def save(self):
        self.backup = Path(tempfile.mkdtemp(prefix="e2e-backup-")) / "world"
        shutil.copytree(self.tmp, self.backup, symlinks=True)
        self.saved_minutes = self.minutes

    def restore(self):
        shutil.rmtree(self.tmp)
        shutil.copytree(self.backup, self.tmp, symlinks=True)
        self.minutes = self.saved_minutes

    # -- the clock and the commands

    def now(self):
        return (START + timedelta(minutes=self.minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def advance(self):
        self.minutes += 1
        os.environ["FACTORY_NOW"] = self.now()
        os.environ["FACTORY_GITHUB"] = str(self.github)

    def run(self, main, *argv):
        """``(exit code, stdout, stderr)`` of a command's ``main``, one minute later than the last."""
        self.advance()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def tick(self, apply=False):
        return self.run(cmd_tick.main, *(["--apply"] if apply else []), "--root", str(self.show))

    def lock(self, apply=False):
        return self.run(cmd_lock.main, *(["--apply"] if apply else []), "--root", str(self.show))

    def post(self, number, body, role):
        self.advance()
        return self.gh.post(number, body, role)

    # -- git

    def git(self, *args, cwd=None):
        return gitstate.git(cwd or self.show, *IDENTITY, *args)

    def write(self, rel, text):
        path = self.show / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def commit(self, message, cwd=None):
        # Empty the index first, so `add` hashes every file instead of trusting a file's time.
        self.git("rm", "-r", "-q", "--cached", "--ignore-unmatch", ".", cwd=cwd)
        self.git("add", "-A", cwd=cwd)
        self.git("commit", "-q", "-m", message, cwd=cwd)

    def head(self, ref):
        return gitstate.head(self.show, ref)

    @contextlib.contextmanager
    def detached(self, at):
        """A temporary detached worktree at ``at``, removed afterwards."""
        where = self.tmp / "detached"
        self.git("worktree", "add", "-q", "--detach", str(where), at)
        try:
            yield where
        finally:
            self.git("worktree", "remove", "--force", str(where))

    def move_branch(self, ref, new, old):
        self.git("update-ref", f"refs/heads/{ref}", new, old)

    # -- the recorded GitHub

    def record(self, kind, number):
        return json.loads((self.github / kind / f"{number}.json").read_text())

    def set_record(self, kind, number, /, **fields):
        path = self.github / kind / f"{number}.json"
        found = json.loads(path.read_text()) if path.is_file() else {}
        path.write_text(json.dumps({**found, **fields}, indent=1, sort_keys=True) + "\n")

    def raw_comments(self, number):
        path = self.github / "comments" / f"{number}.json"
        return json.loads(path.read_text()) if path.is_file() else []

    def set_comments(self, number, comments):
        (self.github / "comments" / f"{number}.json").write_text(json.dumps(comments, indent=1, sort_keys=True)
                                                                 + "\n")

    def edit_comment(self, number, comment_id, **fields):
        self.set_comments(number, [{**c, **fields} if c["id"] == comment_id else c
                                   for c in self.raw_comments(number)])

    def comment(self, number, comment_id):
        return next(c for c in self.raw_comments(number) if c["id"] == comment_id)

    def comments(self, number):
        return self.gh.comments(number)

    def log(self):
        path = self.github / "log.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []

    # -- the harness

    def script(self, *steps):
        (self.harness / "fake-harness.json").write_text(json.dumps(list(steps), indent=1) + "\n")

    def append(self, extra):
        steps = json.loads((self.harness / "fake-harness.json").read_text())
        self.script(*steps, extra)

    def p(self):
        return production.load(self.show)

    def markers_lint(self, comments):
        tree = self.tmp / "tree"
        shutil.rmtree(tree, ignore_errors=True)
        shutil.copytree(self.show / ".factory", tree / ".factory")
        (tree / ".factory-state").mkdir()
        data = [{"id": c["id"], "role": c["role"], "body": c["body"]} for c in comments]
        (tree / ".factory-state" / "comments.json").write_text(json.dumps(data, indent=1) + "\n")
        return lint.run(tree, only=["markers"])["markers"]


# -- the module: offline, with a `gh` that would tell, and the base world built once

_patches = []
_module = {}


class _NoSocket:
    def __init__(self, *args, **kwargs):
        raise OSError("tests/test_e2e.py: no network")


def setUpModule():
    bin_dir = Path(os.path.realpath(tempfile.mkdtemp(prefix="e2e-bin-")))
    _module["bin"] = bin_dir
    _module["gh_called"] = bin_dir / "gh-was-called"
    gh = bin_dir / "gh"
    gh.write_text(f"#!/bin/sh\ntouch '{_module['gh_called']}'\nexit 97\n")
    gh.chmod(0o755)
    env = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "FACTORY_LAUNCH_WAIT": "1",
           "FACTORY_NOW": START.strftime("%Y-%m-%dT%H:%M:%SZ"), "FACTORY_GITHUB": "",
           "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
    _patches[:] = [mock.patch.dict(os.environ, env), mock.patch.object(socket, "socket", _NoSocket)]
    for patch in _patches:
        patch.start()


def tearDownModule():
    for patch in reversed(_patches):
        patch.stop()
    if "base" in _module:
        _module["base"].remove()
    shutil.rmtree(_module["bin"], ignore_errors=True)


def to_lock(case, w, ticks=12):
    """``tick --apply`` until issue 3 is closed; the tick lines, one list per tick."""
    found = []
    for _ in range(ticks):
        if w.record("issues", 3)["state"] == "closed":
            break
        found.append(case.ticked(w, apply=True))
    return found


def base_world(case):
    """The base world before its lock, put back as it was after its eighth tick."""
    if "base" not in _module:
        w = World()
        w.script(*happy_script())
        ticks, times = [], []
        for _ in range(8):
            ticks.append(case.ticked(w, apply=True))
            times.append(w.now())
        _module["base_ticks"], _module["base_gate_now"] = ticks, times[6]
        w.save()
        _module["base"] = w
    w = _module["base"]
    w.restore()
    return w


class Case(unittest.TestCase):
    maxDiff = None

    def world(self, entries=None):
        w = World(entries)
        self.addCleanup(w.remove)
        return w

    def ticked(self, w, apply=False):
        code, out, err = w.tick(apply)
        self.assertEqual((code, err), (0, ""), out)
        self.assertRegex(out, LAST)
        return out.splitlines()[:-1]

    # -- the four parts of a rule test

    def lockable(self, w, pull, ticket, stage, unit):
        """(a) the report says the pull may be locked."""
        self.assertEqual(w.lock(), (0, f"PR #{pull} #{ticket} story/{stage} {unit}: LOCKABLE\n", ""))

    def refused(self, w, pull, report):
        """(b) the report names the broken rule, and ``--apply`` locks nothing."""
        text = "".join(line + "\n" for line in report)
        self.assertEqual(w.lock(), (0, text, ""))
        self.assertEqual(w.lock(apply=True), (0, text + "nothing to lock\n", ""))
        self.assertEqual(w.record("pulls", pull)["state"], "open")
        self.assertFalse([c for c in w.comments(pull) if first(c).endswith(" LOCK")])

    def tick_line(self, w, expected, apply=False):
        """(c) what tick prints."""
        self.assertEqual(self.ticked(w, apply), expected)

    def locks(self, w, pull, ticket, ticks):
        """(d) after the undo, ``ticks`` ticks lock the pull and close the ticket."""
        for _ in range(ticks):
            self.assertEqual(w.record("pulls", pull)["state"], "open")
            out = self.ticked(w, apply=True)
        self.assertEqual(out[-1], f"STEP #{ticket} lock PR #{pull}")
        self.assertEqual(w.record("pulls", pull)["state"], "merged")
        self.assertEqual(w.record("issues", ticket)["state"], "closed")


class HappyPath(Case):
    """Behaviour 2, 3, 4 and 7: from LAUNCH to LOCK, the kit's check, determinism, offline and secrets."""

    def happy(self):
        w = base_world(self)
        ticks = _module["base_ticks"] + to_lock(self, w)
        return w, ticks

    def test_launch_to_lock(self):
        w, ticks = self.happy()
        self.assertEqual(ticks, [[line] for line in HAPPY_LINES])

        [launch] = w.comments(3)
        self.assertEqual((launch["role"], first(launch)), ("showrunner", "[SHOWRUNNER] LAUNCH story-writer 3-1"))
        comments = w.comments(4)
        self.assertEqual([(c["role"], first(c)) for c in comments], [
            ("story-writer", "[STORY-WRITER] READY"), ("program:lint", "[LINT] PASS"),
            ("showrunner", "[SHOWRUNNER] LAUNCH story-editor 4-1"), ("story-editor", "[STORY-EDITOR] NOTES"),
            ("showrunner", "[SHOWRUNNER] LAUNCH story-writer 4-2"), ("story-writer", "[STORY-WRITER] ADDRESSED"),
            ("program:lint", "[LINT] PASS"), ("showrunner", "[SHOWRUNNER] LAUNCH story-editor 4-2"),
            ("story-editor", "[STORY-EDITOR] APPROVED"), ("program:gate", "[PRODUCER] GATE PASS"),
            ("program:lock", "[SHOWRUNNER] LOCK")])
        self.assertEqual([c["id"] for c in comments], list(range(2, 13)))
        by_id = {c["id"]: c for c in comments}

        # Heads: h1 the branch's first commit, h2 the writer's second, h3 the sync's merge.
        h1, h2 = last_line(by_id[2]).split()[1], last_line(by_id[7]).split()[1]
        [production_commit] = w.git("rev-list", "--max-parents=0", "main").split()
        self.assertEqual(w.git("rev-list", "--parents", "-n", "1", h1).split(), [h1, production_commit])
        self.assertEqual(w.git("rev-list", "--parents", "-n", "1", h2).split(), [h2, h1])
        self.assertEqual(last_line(by_id[2]), f"head {h1} manifest none")
        self.assertEqual(last_line(by_id[5]), f"reviewed head {h1} manifest none")
        self.assertEqual(last_line(by_id[7]), f"head {h2} manifest none")
        for cid in (8, 10, 11):
            self.assertEqual(last_line(by_id[cid]), f"reviewed head {h2} manifest none", cid)
        h3 = by_id[12]["body"].split("\n")[2].split()[2]
        self.assertEqual(by_id[12]["body"], f"[SHOWRUNNER] LOCK\n\nlocked head {h3} manifest none\n"
                                            "relied on: lint #8; story-editor APPROVED #10; gate #11\n")
        gate_commit = w.git("log", "-1", "--format=%H", "main^1", "--", "ledger/spend.tsv").strip()
        self.assertEqual(w.git("rev-list", "--parents", "-n", "1", h3).split(), [h3, h2, gate_commit])
        self.assertEqual(w.head(REF), h3)

        pull = w.record("pulls", 4)
        self.assertEqual((pull["state"], pull["ticket"], pull["head_ref"], pull["body"]),
                         ("merged", 3, REF, "Ticket: #3\n"))
        self.assertEqual(w.record("issues", 3)["state"], "closed")

        self.assertEqual(w.git("show", f"main:{BEATS}"), fixture("beats-2.md"))
        spend = w.git("show", "main:ledger/spend.tsv").splitlines()
        self.assertEqual(len(spend), 2)
        row = spend[1].split("\t")
        self.assertEqual(len(row), 11, row)
        self.assertRegex(row[2], r"\A[0-9a-f]{64}\Z")
        self.assertEqual(row[:2] + row[3:], [_module["base_gate_now"], "lock", "3", "ep01", "-", "0", "0",
                                             "showrunner", "PASS", "-"])

        ops = [(e["op"], e.get("ref")) for e in w.log()]
        gate_push = ops.index(("push", "main"))
        sync_push = ops.index(("push", REF), gate_push)
        merge = [i for i, (op, _) in enumerate(ops) if op == "merge"]
        close = [i for i, (op, _) in enumerate(ops) if op == "close_issue"]
        self.assertEqual((len(merge), len(close)), (1, 1))
        self.assertLess(gate_push, sync_push)
        self.assertLess(sync_push, merge[0])
        self.assertLess(merge[0], close[0])

        recorded = w.comments(3) + comments
        self.assertEqual(w.markers_lint(recorded).status, lint.PASS)
        seeded = [{**c, "role": "program:lint"} if c["id"] == 10 else c for c in recorded]
        self.assertEqual(w.markers_lint(seeded).status, lint.FAIL)

        self.assertEqual(w.git("status", "--porcelain"), "")
        self.assertEqual(w.lock(), (0, "", ""))
        before = w.log()
        self.assertEqual(w.tick(apply=True), (0, "tick: 0 steps, 0 need, 0 wait, 0 failed\n", ""))
        self.assertEqual(w.log(), before)

    def test_fixture_kit_is_valid(self):
        w = self.world()
        kit = str(w.show / ".factory")
        self.assertEqual(w.run(cmd_roles.main, "--kit", kit, "--check"), (0, "OK\n", ""))
        shutil.copyfile(ROOT / "kit" / "formats" / "serial-microdrama.md",
                        w.show / ".factory" / "formats" / "serial-microdrama.md")
        code, out, err = w.run(cmd_roles.main, "--kit", kit, "--check")
        self.assertEqual(code, 1)
        self.assertIn("formats/serial-microdrama.md: skips 'outline' is not a stage in the pipeline",
                      (out + err).splitlines())

    def test_two_runs_record_the_same(self):
        one, _ = self.happy()
        two = self.world()
        two.script(*happy_script())
        self.assertEqual(to_lock(self, two), [[line] for line in HAPPY_LINES])
        for rel in ("pulls/4.json", "issues/3.json"):
            self.assertEqual((one.github / rel).read_bytes(), (two.github / rel).read_bytes(), rel)

        def same(text, tmp):
            text = text.replace(str(tmp), "<TMP>")
            return HEX40.sub("<SHA>", HEX64.sub("<HASH>", text))

        for rel in ("comments/3.json", "comments/4.json", "log.jsonl"):
            a, b = (one.github / rel).read_text(), (two.github / rel).read_text()
            self.assertNotEqual(a, b, rel)
            self.assertEqual(same(a, one.tmp), same(b, two.tmp), rel)

    def test_no_network_and_no_gh(self):
        w, ticks = self.happy()
        self.assertEqual(ticks[-1], ["STEP #3 lock PR #4"])
        with self.assertRaises(OSError):
            socket.socket()
        self.assertFalse(_module["gh_called"].exists())

    def test_secrets_stay_out(self):
        w, ticks = self.happy()
        self.assertEqual(ticks[-1], ["STEP #3 lock PR #4"])
        token = w.secrets / "inference.primary" / "acct-one"
        holding_token, holding_name = [], []
        for path in sorted(w.tmp.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            data = path.read_bytes()
            rel = path.relative_to(w.tmp).as_posix()
            if TOKEN.strip().encode() in data:
                holding_token.append(rel)
            if SECRETS.encode() in data:
                holding_name.append(rel)
        self.assertEqual(holding_token, [token.relative_to(w.tmp).as_posix()])
        sandboxes = [rel for rel in holding_name if re.fullmatch(r"state/runs/[^/]+/sandbox\.sb", rel)]
        self.assertTrue(sandboxes)
        self.assertEqual(sorted(set(holding_name) - set(sandboxes)), ["show/production.local.env"])
        self.assertFalse([rel for rel in holding_name if rel.startswith("github/")])
        commits = w.git("rev-list", "--all").split()
        for rev in ["main", REF] + commits:
            done = subprocess.run(["git", "-C", str(w.show), "grep", "-q", "-F", "-e", SECRETS, rev, "--"],
                                  capture_output=True, text=True)
            self.assertEqual(done.returncode, 1, (rev, done.stderr))


class BaseRules(Case):
    """Behaviour 5 on the base world before its lock: rules 1, 2, 4, 7, 8, 9, 10 and 11."""

    def setUp(self):
        self.w = base_world(self)
        self.lockable(self.w, 4, 3, "beats", "ep01")

    def refused_base(self, line):
        self.refused(self.w, 4, [BASE_HEADER, line])

    def test_rule_1_lint_missing(self):
        w = self.w
        kept = w.raw_comments(4)
        self.assertEqual(first(w.comment(4, 8)), "[LINT] PASS")
        w.set_comments(4, [c for c in kept if c["id"] != 8])
        self.refused_base("  rule 1 lint: no counting [LINT] verdict")
        self.tick_line(w, ["STEP #3 lint PR #4"])
        self.assertEqual(self.ticked(w, apply=True), ["STEP #3 lint PR #4"])
        self.assertEqual(first(w.comments(4)[-1]), "[LINT] PASS")
        self.locks(w, 4, 3, 1)

    def test_rule_1_lint_incomplete(self):
        w = self.w
        kept = w.comment(4, 8)
        line = "Checks required by stage beats: structure, media"
        self.assertIn(line, kept["body"].splitlines())
        w.edit_comment(4, 8, body=kept["body"].replace(line, "Checks required by stage beats: structure"))
        self.refused_base("  rule 1 lint: lint #8 does not list media for stage beats")
        self.tick_line(w, ["NEED #3 lock refused: rule 1 lint"])
        w.edit_comment(4, 8, body=kept["body"])
        self.locks(w, 4, 3, 1)

    def test_rule_2_checkers_no_verdict(self):
        w = self.w
        kept = w.raw_comments(4)
        self.assertEqual(first(w.comment(4, 10)), "[STORY-EDITOR] APPROVED")
        launched = w.comment(4, 9)["created_at"]
        w.set_comments(4, [c for c in kept if c["id"] != 10])
        self.refused_base("  rule 2 checkers: checker story-editor has no counting verdict")
        self.tick_line(w, [f"WAIT #3 running story-editor launched at {launched}"])
        w.set_comments(4, kept)
        self.locks(w, 4, 3, 1)

    def test_rule_2_checkers_notes(self):
        w = self.w
        kept = w.raw_comments(4)
        body = ("[STORY-EDITOR] NOTES\n\nThe return beat is told, not shown.\n\n"
                "B2 (Blocker): Wren's walk home happens off screen. Evidence: episodes/ep01/beats.md B7\n"
                f"Launch: 4-2\nreviewed head {w.head(REF)} manifest none\n")
        notes = w.post(4, body, "story-editor")
        self.refused_base(f"  rule 2 checkers: checker story-editor NOTES #{notes['id']}")
        self.tick_line(w, ["STEP #3 launch story-writer on PR #4"])
        w.set_comments(4, kept)
        self.locks(w, 4, 3, 1)

    def test_rule_4_upstream(self):
        w = self.w
        w.set_record("pulls", 2, state="closed")
        self.refused_base("  rule 4 upstream: upstream stage premise is not locked for ep01")
        self.tick_line(w, ["WAIT #3 waiting upstream stage premise is not locked for ep01"])
        w.set_record("pulls", 2, state="merged")
        self.locks(w, 4, 3, 1)

    def test_rule_7_writes(self):
        w = self.w
        old, main = w.head(REF), w.head("main")
        with w.detached(old) as where:
            (where / "notes").mkdir()
            (where / "notes" / "stray.md").write_text("Not the writer's to write.\n")
            w.git("add", "-A", cwd=where)
            tree = w.git("write-tree", cwd=where).strip()
        new = w.git("commit-tree", tree, "-p", old, "-p", main, "-m", "a stray file").strip()
        w.move_branch(REF, new, old)
        self.refused_base("  rule 7 writes: outside the writes of story-writer: notes/stray.md; "
                          "outside room story: notes/stray.md")
        self.tick_line(w, ["NEED #3 lock refused: rule 7 writes"])
        w.move_branch(REF, old, new)
        self.locks(w, 4, 3, 1)

    def test_rule_8_repeat_notes(self):
        w = self.w
        notes, addressed = w.comment(4, 5)["body"], w.comment(4, 7)["body"]
        self.assertIn("B1 (Blocker): ", notes)
        self.assertIn("- B1 by story-editor: changed.", addressed)
        w.edit_comment(4, 5, body=notes.replace("B1 (Blocker): ", "B1 (Blocker): REPEAT B1: "))
        w.edit_comment(4, 7, body=addressed.replace("- B1 by story-editor: changed.",
                                                    "- B1 by story-editor: rebutted."))
        self.refused_base("  rule 8 repeat-notes: repeated note(s) with no change or ruling: story-editor B1 (#5)")
        self.tick_line(w, ["NEED #3 lock refused: rule 8 repeat-notes"])
        w.post(4, "[SHOWRUNNER] RULING\n\nstory-editor B1 is answered: the rebuttal stands.\n", "showrunner")
        self.locks(w, 4, 3, 1)

    def test_rule_9_gate(self):
        w = self.w
        self.assertEqual(w.comment(4, 11)["role"], "program:gate")
        w.edit_comment(4, 11, role="story-writer")
        self.refused_base("  rule 9 gate: stage beats is the last of room story and no counting "
                          "[PRODUCER] GATE PASS covers the head")
        self.tick_line(w, ["STEP #3 gate PR #4"])
        w.edit_comment(4, 11, role="program:gate")
        self.locks(w, 4, 3, 1)

    def test_rule_10_base_moved(self):
        w = self.w
        w.write("notes/later.md", "The base moves on.\n")
        w.commit("move the base")
        self.refused_base("  rule 10 base: head does not contain the base main")
        self.tick_line(w, ["STEP #3 sync PR #4"])
        self.locks(w, 4, 3, 2)

    def test_rule_10_base_hold_on_pull(self):
        w = self.w
        w.set_record("pulls", 4, labels=["hold"])
        self.refused_base("  rule 10 base: PR #4 has the label hold")
        self.tick_line(w, ["NEED #3 lock refused: rule 10 base"])
        w.set_record("pulls", 4, labels=[])
        self.locks(w, 4, 3, 1)

    def test_rule_10_base_hold_on_ticket(self):
        w = self.w
        labels = w.record("issues", 3)["labels"]
        w.set_record("issues", 3, labels=sorted(labels + ["hold"]))
        self.refused_base("  rule 10 base: ticket #3 has the label hold")
        self.tick_line(w, ["WAIT #3 held ticket #3 has the label hold"])
        w.set_record("issues", 3, labels=labels)
        self.locks(w, 4, 3, 1)

    def test_rule_11_released(self):
        w = self.w
        w.write("distribution/ep01/release.json", "{}\n")
        w.commit("release ep01")
        old = w.head(REF)
        with w.detached(old) as where:
            w.git("merge", "-q", "--no-ff", "--no-edit", "main", cwd=where)
            new = w.git("rev-parse", "HEAD", cwd=where).strip()
        w.move_branch(REF, new, old)
        self.refused_base("  rule 11 released: changes files of a released episode: episodes/ep01/beats.md")
        self.tick_line(w, ["NEED #3 lock refused: rule 11 released"], apply=True)
        self.assertIn(LABEL, w.record("issues", 3)["labels"])
        w.git("rm", "-q", "distribution/ep01/release.json")
        w.commit("withdraw the release of ep01")
        self.assertEqual(self.ticked(w, apply=True), ["STEP #3 sync PR #4"])
        self.locks(w, 4, 3, 1)
        self.assertNotIn(LABEL, w.record("issues", 3)["labels"])


class OwnWorldRules(Case):
    """Behaviour 5, rules 3, 5 and 6, each on a world of its own."""

    def test_rule_3_judges(self):
        entries = pipeline()
        entries[1].update(judge="pitch-judge", blind=True)
        w = self.world(entries)
        w.write(BEATS, fixture("beats-1.md"))
        w.commit("the locked beats of ep01")
        w.set_record("issues", 3, state="closed")
        w.set_record("pulls", 4, base_ref="main", body="Ticket: #3\n", created_at="2026-11-10T11:30:00Z",
                     head_ref=REF, labels=[], merged_at="2026-11-10T11:45:00Z", number=4, state="merged",
                     ticket=3, title="[EP01] Beats of ep01")
        w.set_record("issues", 5, **{**w.record("issues", 3), "number": 5, "state": "open",
                                     "title": "[EP01-R] Rewrite the beats of ep01",
                                     "created_at": "2026-11-10T11:50:00Z"})
        w.script(writer_ready(fixture("beats-2.md")), editor_approved())
        for line in ("STEP #5 launch story-writer on #5", "STEP #5 lint PR #6", "STEP #5 launch story-editor on PR #6",
                     "STEP #5 blind PR #6"):
            self.assertEqual(self.ticked(w, apply=True), [line])
        ref = w.record("pulls", 6)["head_ref"]
        key = json.loads(blind.key_path(w.p(), 6, w.head(ref)).read_text())
        new = next(letter for letter in ("A", "B") if key[letter] == "new")
        old = "B" if new == "A" else "A"
        w.append(step("You are the pitch judge", f"PREFERS {new}",
                      prefers={"choice": new, "cited": ["the price in the find beat"]}, evidence=EVIDENCE))
        for line in ("STEP #5 launch pitch-judge on PR #6", "STEP #5 gate PR #6", "STEP #5 sync PR #6"):
            self.assertEqual(self.ticked(w, apply=True), [line])
        self.lockable(w, 6, 5, "beats", "ep01")

        [judged] = [c for c in w.raw_comments(6) if c["role"] == "pitch-judge"]
        self.assertIn(f"Prefers: {new}.", judged["body"])
        w.edit_comment(6, judged["id"], body=judged["body"].replace(f"PREFERS {new}", f"PREFERS {old}")
                       .replace(f"Prefers: {new}.", f"Prefers: {old}."))
        self.refused(w, 6, ["PR #6 #5 story/beats ep01: NO 1 rule(s)",
                            "  rule 3 judges: 0 of 1 judges prefer the new version"])
        self.tick_line(w, ["NEED #5 lock refused: rule 3 judges"])
        w.edit_comment(6, judged["id"], body=judged["body"])
        self.locks(w, 6, 5, 1)

    def test_rule_5_selected(self):
        entries = pipeline()
        entries[1]["pitches"] = 2
        w = self.world(entries)
        w.script(writer_ready(fixture("beats-1.md")), writer_ready(fixture("beats-2.md")), editor_approved(),
                 editor_approved())
        waits = ["WAIT #3 lockable PR #4 is not the ticket's next step",
                 "WAIT #3 lockable PR #5 is not the ticket's next step",
                 "NEED #3 selection every pitch of #3 is lockable and none is selected"]
        for _ in range(4):
            self.ticked(w, apply=True)
        self.assertEqual(self.ticked(w, apply=True), waits[:2] + ["STEP #3 launch showrunner on #3"])
        selected = w.post(3, "[SHOWRUNNER] SELECTED #5\n\nThe second pitch makes Wren pay.\n", "showrunner")
        self.assertEqual(self.ticked(w, apply=True), [waits[0], "STEP #3 gate PR #5"])
        self.assertEqual(self.ticked(w, apply=True), [waits[0], "STEP #3 sync PR #5"])
        pull_4 = ["PR #4 #3 story/beats ep01: NO 3 rule(s)", "  rule 5 selected: ticket #3 selected PR #5",
                  "  rule 9 gate: stage beats is the last of room story and no counting [PRODUCER] GATE PASS "
                  "covers the head", "  rule 10 base: head does not contain the base main"]
        code, out, err = w.lock()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines(), pull_4 + ["PR #5 #3 story/beats ep01: LOCKABLE"])

        kept = w.raw_comments(3)
        w.set_comments(3, [c for c in kept if c["id"] != selected["id"]])
        pull_4[1] = "  rule 5 selected: ticket #3 has no SELECTED"
        self.refused(w, 5, pull_4 + ["PR #5 #3 story/beats ep01: NO 1 rule(s)",
                                     "  rule 5 selected: ticket #3 has no SELECTED"])
        self.tick_line(w, waits)
        w.set_comments(3, kept)
        self.assertEqual(self.ticked(w, apply=True), [waits[0], "STEP #3 lock PR #5"])
        self.assertEqual(w.record("pulls", 5)["state"], "merged")
        self.assertEqual(w.record("issues", 3)["state"], "closed")
        self.assertEqual(w.record("pulls", 4)["state"], "open")

    def test_rule_6_amendment(self):
        w = self.world([json.loads(fixture("bible.json"))] + pipeline()[:1])
        w.set_record("issues", 3, labels=["room:story", "stage:bible", "tracked", "unit:A-1"],
                     title="[A-1] The harbour lamp enters the canon")
        w.script(step("You are the canon keeper", "READY",
                      {"bible/canon/lamp.md": fixture("lamp.md"), "bible/amendments/A-1.json": fixture("A-1.json")},
                      evidence=EVIDENCE),
                 step("You are the canon reviewer", "APPROVED", evidence=EVIDENCE))
        self.assertEqual(self.ticked(w, apply=True), ["STEP #3 launch canon-keeper on #3"])
        self.assertEqual(self.ticked(w, apply=True), ["STEP #3 launch canon-reviewer on PR #4"])
        self.refused(w, 4, ["PR #4 #3 story/bible A-1: NO 1 rule(s)",
                            "  rule 6 amendment: bible/amendments/A-1.json: class arc needs a [BRAINTRUST] VOTE "
                            "with 5 or more for, later than the maker's last READY or ADDRESSED"])
        self.tick_line(w, ["NEED #3 lock refused: rule 6 amendment"])
        w.post(4, "[BRAINTRUST] VOTE 6-1\n\nThe lamp belongs in the canon.\n"
                  f"reviewed head {w.head(w.record('pulls', 4)['head_ref'])} manifest "
                  f"{gitstate.manifest(w.show, w.record('pulls', 4)['base_ref'], w.record('pulls', 4)['head_ref'])}\n",
               "braintrust")
        self.lockable(w, 4, 3, "bible", "A-1")
        self.locks(w, 4, 3, 1)


class Refusals(Case):
    """Behaviour 6: refusals outside the lock, each on a fresh base world."""

    def test_frozen(self):
        w = self.world()
        w.script(*happy_script())
        self.assertEqual(w.run(cmd_freeze.main, "--root", str(w.show), "--reason", "a test freeze"),
                         (0, "frozen (#4)\n", ""))
        log, comments = w.log(), sorted((w.github / "comments").glob("*.json"))
        self.assertEqual(w.tick(apply=True), (0, "FROZEN\n", ""))
        self.assertEqual(w.log(), log)
        self.assertEqual(sorted((w.github / "comments").glob("*.json")), comments)
        self.assertEqual(w.lock(apply=True), (2, "", "lock: frozen\n"))
        self.assertEqual(w.run(cmd_unfreeze.main, "--root", str(w.show)), (0, "unfrozen (#4)\n", ""))
        self.tick_line(w, ["STEP #3 launch story-writer on #3"])

    def blocked_maker(self, w, missing, extra=()):
        self.assertEqual(self.ticked(w, apply=True), ["STEP #3 launch story-writer on #3"])
        found = w.comments(3)
        self.assertEqual([(c["role"], first(c)) for c in found],
                         [("showrunner", "[SHOWRUNNER] LAUNCH story-writer 3-1"),
                          ("story-writer", "[STORY-WRITER] BLOCKED")])
        body = found[1]["body"].splitlines()
        for line in (f"Missing: {missing}", *extra):
            self.assertIn(line, body)
        self.assertEqual(sorted(p.name for p in (w.github / "pulls").glob("*.json")), ["2.json"])
        self.assertEqual(self.ticked(w, apply=True), ["STEP #3 launch showrunner on #3"])
        for _ in range(2):
            self.assertEqual(self.ticked(w, apply=True), ["NEED #3 blocked the maker is BLOCKED on #3"])
        self.assertIn(LABEL, w.record("issues", 3)["labels"])
        self.assertEqual([first(c) for c in w.comments(3)].count("[SHOWRUNNER] LAUNCH story-writer 3-1"), 1)
        self.assertEqual([first(c) for c in w.comments(3) if " LAUNCH " in first(c)],
                         ["[SHOWRUNNER] LAUNCH story-writer 3-1", "[SHOWRUNNER] LAUNCH showrunner 3-1"])

    def test_maker_returns_a_checkers_marker(self):
        w = self.world()
        w.script(step("You are the story writer", "APPROVED", {BEATS: fixture("beats-1.md")}, evidence=EVIDENCE))
        self.blocked_maker(w, "a valid result (result.marker: a maker posting as STORY-WRITER may not post "
                              "'APPROVED')")

    def test_maker_writes_only_outside_writes(self):
        w = self.world()
        w.script(step("You are the story writer", "READY", {"notes/stray.md": "Not the writer's.\n"},
                      evidence=EVIDENCE))
        self.blocked_maker(w, "any change inside writes", ["Dropped (outside writes): notes/stray.md"])

    def test_three_rounds_need_a_ruling(self):
        w = self.world()
        v1, v2 = fixture("beats-1.md"), fixture("beats-2.md")
        v3 = v2.replace("Wren gives up the boat for it", "Wren gives up her father's boat for it")
        v4 = v3.replace("She climbs the lamp stair soaked", "She climbs the lamp stair soaked and shaking")
        self.assertEqual(len({v1, v2, v3, v4}), 4)
        w.script(writer_ready(v1), editor_notes(), writer_addressed(v2), editor_notes(), writer_addressed(v3),
                 editor_notes(), writer_addressed(v4))
        rounds = ["STEP #3 launch story-writer on #3", "STEP #3 lint PR #4", "STEP #3 launch story-editor on PR #4",
                  "STEP #3 launch story-writer on PR #4", "STEP #3 lint PR #4", "STEP #3 launch story-editor on PR #4",
                  "STEP #3 launch story-writer on PR #4", "STEP #3 lint PR #4", "STEP #3 launch story-editor on PR #4"]
        self.assertEqual([self.ticked(w, apply=True) for _ in range(9)], [[line] for line in rounds])
        self.assertEqual(self.ticked(w, apply=True), ["STEP #3 launch showrunner on PR #4"])
        for _ in range(2):
            self.assertEqual(self.ticked(w, apply=True), ["NEED #3 ruling three rounds of notes on #4 and no ruling"])
        launches = [first(c) for n in (3, 4) for c in w.comments(n)
                    if first(c).startswith("[SHOWRUNNER] LAUNCH story-writer ")]
        self.assertEqual(launches, ["[SHOWRUNNER] LAUNCH story-writer 3-1", "[SHOWRUNNER] LAUNCH story-writer 4-2",
                                    "[SHOWRUNNER] LAUNCH story-writer 4-3"])
        self.assertIn(LABEL, w.record("issues", 3)["labels"])
        w.post(4, "[SHOWRUNNER] RULING\n\nOne more round: the find beat must cost Wren the boat.\n", "showrunner")
        self.assertEqual(self.ticked(w, apply=True), ["STEP #3 launch story-writer on PR #4"])
        self.assertNotIn(LABEL, w.record("issues", 3)["labels"])


class Judgments(Case):
    """ISSUE-89 behaviours 15 and 16: tick runs the showrunner for a selection and a ruling, and the
    ticket goes on to its lock; no comment is posted by the test."""

    @staticmethod
    def versions():
        """Four texts of the beats that pass the lint, each different."""
        v1, v2 = fixture("beats-1.md"), fixture("beats-2.md")
        v3 = v2.replace("Wren gives up the boat for it", "Wren gives up her father's boat for it")
        v4 = v3.replace("She climbs the lamp stair soaked", "She climbs the lamp stair soaked and shaking")
        return v1, v2, v3, v4

    @staticmethod
    def prompt(comment):
        """The prompt inside a LAUNCH record's fence."""
        lines = comment["body"].split("\n")
        start = next(i for i, line in enumerate(lines) if line.startswith("````"))
        end = len(lines) - 1 - lines[::-1].index(lines[start])
        return "\n".join(lines[start + 1:end]) + "\n"

    def labels_added(self, w):
        return [(e["number"], e["label"]) for e in w.log() if e["op"] == "add_label"]

    @staticmethod
    def waits(*pulls):
        return [f"WAIT #3 lockable PR #{n} is not the ticket's next step" for n in pulls]

    @staticmethod
    def launches(w):
        """The first lines of every LAUNCH record on the issue and its pulls."""
        return [first(c) for n in (3, 4, 5, 6) for c in w.comments(n) if " LAUNCH " in first(c)]

    # -- behaviour 15: a pitch selection

    def selection_world(self, selected):
        entries = pipeline()
        entries[1]["pitches"] = 3
        w = self.world(entries)
        v1, v2, v3, _ = self.versions()
        w.script(writer_ready(v1), writer_ready(v2), writer_ready(v3), editor_approved(), editor_approved(),
                 editor_approved(),
                 step("You are the showrunner", "SELECTED", selected=selected,
                      evidence=[{"criterion": f"PR #{n}", "result": "selected" if n == selected else "approved",
                                 "command": "episodes/ep01/beats.md"} for n in (4, 5, 6)]))
        return w

    def test_selection_to_lock(self):
        w = self.selection_world(5)
        ticks = to_lock(self, w)
        self.assertEqual(ticks, [
            ["STEP #3 launch story-writer on #3"], ["STEP #3 launch story-writer on #3"],
            ["STEP #3 launch story-writer on #3"],
            ["STEP #3 lint PR #4", "STEP #3 lint PR #5", "STEP #3 lint PR #6"],
            ["STEP #3 launch story-editor on PR #4", "STEP #3 launch story-editor on PR #5",
             "STEP #3 launch story-editor on PR #6"],
            self.waits(4, 5, 6) + ["STEP #3 launch showrunner on #3"],
            [self.waits(4)[0], "STEP #3 gate PR #5", self.waits(6)[0]],
            [self.waits(4)[0], "STEP #3 sync PR #5", self.waits(6)[0]],
            [self.waits(4)[0], "STEP #3 lock PR #5", self.waits(6)[0]]])

        found = [(c["role"], first(c)) for c in w.comments(3)]
        self.assertEqual(found.count(("showrunner", "[SHOWRUNNER] LAUNCH showrunner 3-1")), 1)
        self.assertEqual(found.count(("showrunner", "[SHOWRUNNER] SELECTED #5")), 1)
        self.assertEqual([b for _, b in found if " LAUNCH showrunner " in b or " SELECTED " in b],
                         ["[SHOWRUNNER] LAUNCH showrunner 3-1", "[SHOWRUNNER] SELECTED #5"])
        [record] = [c for c in w.comments(3) if first(c) == "[SHOWRUNNER] LAUNCH showrunner 3-1"]
        prompt = self.prompt(record)
        self.assertIn("The pitches are the pull requests #4, #5, #6.", prompt)
        for n in (4, 5, 6):
            self.assertIn(f"Pull request #{n} at head ", prompt)
            self.assertIn(f"Its files are in pr-{n}/.", prompt)

        self.assertEqual([w.record("pulls", n)["state"] for n in (4, 5, 6)], ["open", "merged", "open"])
        self.assertEqual(w.record("issues", 3)["state"], "closed")
        self.assertEqual(self.labels_added(w), [])
        self.assertNotIn(LABEL, w.record("issues", 3)["labels"])
        meta = json.loads((w.state / "runs" / "showrunner-3-1" / "run.json").read_text())
        self.assertEqual(Path(meta["worktree"]), w.state / "worktrees" / "showrunner-3-1")
        self.assertFalse(Path(meta["worktree"]).exists())

    def test_selection_outside_the_pitches(self):
        w = self.selection_world(9)
        ticks = [self.ticked(w, apply=True) for _ in range(6)]
        self.assertEqual(ticks, [["STEP #3 launch story-writer on #3"]] * 3 + [
            ["STEP #3 lint PR #4", "STEP #3 lint PR #5", "STEP #3 lint PR #6"],
            ["STEP #3 launch story-editor on PR #4", "STEP #3 launch story-editor on PR #5",
             "STEP #3 launch story-editor on PR #6"],
            self.waits(4, 5, 6) + ["STEP #3 launch showrunner on #3"]])
        [blocked] = [c for c in w.comments(3) if c["role"] == "showrunner" and first(c) == "[SHOWRUNNER] BLOCKED"]
        body = blocked["body"].splitlines()
        self.assertIn("Missing: a valid result (result.selected: #9 is not a pitch of this launch)", body)
        self.assertIn("Resume from: ticket #3", body)

        launches = self.launches(w)
        need = "NEED #3 selection every pitch of #3 is lockable and none is selected"
        for _ in range(3):
            self.assertEqual(self.ticked(w, apply=True), self.waits(4, 5, 6) + [need])
        self.assertEqual(self.launches(w), launches)
        self.assertEqual(self.labels_added(w), [(3, LABEL)])
        self.assertIn(LABEL, w.record("issues", 3)["labels"])
        self.assertFalse([e for e in w.log() if e["op"] == "merge"])
        self.assertEqual([w.record("pulls", n)["state"] for n in (4, 5, 6)], ["open", "open", "open"])
        self.assertEqual(w.record("issues", 3)["state"], "open")

    # -- behaviour 16: a ruling after three rounds of notes

    def ruling_world(self, *rest):
        w = self.world()
        v1, v2, v3, v4 = self.versions()
        w.script(writer_ready(v1), editor_notes(), writer_addressed(v2), editor_notes(), writer_addressed(v3),
                 editor_notes(),
                 step("You are the showrunner", "RULING",
                      evidence=[{"criterion": "story-editor B1", "result": "acted on",
                                 "command": "episodes/ep01/beats.md B5"}]),
                 writer_addressed(v4), *rest)
        return w

    ROUNDS = ["STEP #3 launch story-writer on #3", "STEP #3 lint PR #4", "STEP #3 launch story-editor on PR #4",
              "STEP #3 launch story-writer on PR #4", "STEP #3 lint PR #4", "STEP #3 launch story-editor on PR #4",
              "STEP #3 launch story-writer on PR #4", "STEP #3 lint PR #4", "STEP #3 launch story-editor on PR #4",
              "STEP #3 launch showrunner on PR #4",
              "STEP #3 launch story-writer on PR #4", "STEP #3 lint PR #4", "STEP #3 launch story-editor on PR #4"]

    def test_ruling_to_lock(self):
        w = self.ruling_world(editor_approved())
        ticks = to_lock(self, w, ticks=20)
        self.assertEqual(ticks, [[line] for line in self.ROUNDS + [
            "STEP #3 gate PR #4", "STEP #3 sync PR #4", "STEP #3 lock PR #4"]])

        found = [(c["role"], first(c)) for c in w.comments(4)]
        self.assertEqual(found.count(("showrunner", "[SHOWRUNNER] LAUNCH showrunner 4-1")), 1)
        self.assertEqual([b for _, b in found if " LAUNCH showrunner " in b or " RULING" in b],
                         ["[SHOWRUNNER] LAUNCH showrunner 4-1", "[SHOWRUNNER] RULING"])
        [ruling] = [c for c in w.comments(4) if first(c) == "[SHOWRUNNER] RULING"]
        self.assertEqual(ruling["role"], "showrunner")
        self.assertIn("| story-editor B1 | acted on | episodes/ep01/beats.md B5 |", ruling["body"].splitlines())
        records = {first(c): c for n in (3, 4) for c in w.comments(n)}
        for name in ("[SHOWRUNNER] LAUNCH story-writer 4-4", "[SHOWRUNNER] LAUNCH story-editor 4-4"):
            self.assertIn(ruling["body"].rstrip("\n"), self.prompt(records[name]), name)

        self.assertEqual(w.record("pulls", 4)["state"], "merged")
        self.assertEqual(w.record("issues", 3)["state"], "closed")
        self.assertEqual(self.labels_added(w), [])

    def test_notes_after_the_ruling(self):
        w = self.ruling_world(editor_notes())
        ticks = [self.ticked(w, apply=True) for _ in range(len(self.ROUNDS))]
        self.assertEqual(ticks, [[line] for line in self.ROUNDS])
        for _ in range(2):
            self.assertEqual(self.ticked(w, apply=True), ["NEED #3 ruling three rounds of notes on #4 and no ruling"])
        self.assertIn(LABEL, w.record("issues", 3)["labels"])
        self.assertEqual(self.labels_added(w), [(3, LABEL)])
        records = [first(c) for n in (3, 4) for c in w.comments(n)]
        self.assertEqual([r for r in records if " LAUNCH showrunner " in r], ["[SHOWRUNNER] LAUNCH showrunner 4-1"])
        self.assertNotIn("[SHOWRUNNER] LAUNCH story-writer 4-5", records)
        self.assertEqual(w.record("pulls", 4)["state"], "open")



class Filing(Case):
    """ISSUE-90: the tick files the next stage's ticket after a lock, one ``board`` per sequence."""

    SEQUENCES = {"episodes/ep01/sequences/Q1/sequence.json": '{"id": "Q1"}\n',
                 "episodes/ep01/sequences/Q2/sequence.json": '{"id": "Q2"}\n'}

    def created(self, w):
        return [e["number"] for e in w.log() if e["op"] == "create_issue"]

    def test_the_whole_line(self):
        w = self.world(pipeline() + json.loads(fixture("sequences-board.json")))
        w.script(*happy_script(), step("You are the head of story", "READY", self.SEQUENCES, evidence=EVIDENCE),
                 step("You are the continuity supervisor", "APPROVED", evidence=EVIDENCE))
        ticks = to_lock(self, w)
        self.assertEqual(ticks[-1], ["STEP #3 lock PR #4"])
        self.assertEqual(self.created(w), [])

        self.assertEqual(self.ticked(w, apply=True), ["STEP #3 file sequences ep01"])
        [n] = self.created(w)
        issue = w.record("issues", n)
        self.assertEqual((issue["state"], issue["labels"]),
                         ("open", ["room:story", "stage:sequences", "tracked", "unit:ep01"]))

        for _ in range(6):
            if w.record("issues", n)["state"] == "closed":
                break
            out = self.ticked(w, apply=True)
        self.assertEqual(w.record("issues", n)["state"], "closed")
        self.assertRegex(out[-1], rf"\ASTEP #{n} lock PR #\d+\Z")
        for rel, text in sorted(self.SEQUENCES.items()):
            self.assertEqual(w.git("show", f"main:{rel}"), text)

        self.assertEqual(self.ticked(w, apply=True), [f"STEP #{n} file board ep01/Q1", f"STEP #{n} file board ep01/Q2"])
        boards = self.created(w)[1:]
        self.assertEqual([w.record("issues", b)["labels"] for b in boards],
                         [["room:story", "stage:board", "tracked", "unit:ep01/Q1"],
                          ["room:story", "stage:board", "tracked", "unit:ep01/Q2"]])
        self.assertEqual(len(self.created(w)), 3)
        self.ticked(w, apply=True)
        self.assertEqual(len(self.created(w)), 3)

    def test_the_whole_line_to_the_reel(self):
        w = self.world(pipeline() + json.loads(fixture("sequences-board.json")) + json.loads(fixture("reel.json")))
        boards = [step(rf"Do the stage board for unit ep01/{q}\.", "READY",
                       {f"episodes/ep01/sequences/{q}/shots/{q}-1.json": '{"id": "%s-1"}\n' % q},
                       evidence=EVIDENCE) for q in ("Q1", "Q2")]
        w.script(*happy_script(), step("You are the head of story", "READY", self.SEQUENCES, evidence=EVIDENCE),
                 step("You are the continuity supervisor", "APPROVED", evidence=EVIDENCE),
                 boards[0], step("You are the continuity supervisor", "APPROVED", evidence=EVIDENCE),
                 boards[1], step("You are the continuity supervisor", "APPROVED", evidence=EVIDENCE))
        ticks = []
        for _ in range(30):
            ticks.append(self.ticked(w, apply=True))
            if len(self.created(w)) == 3 and all(w.record("issues", b)["state"] == "closed"
                                                 for b in self.created(w)[1:]):
                break
        b, c = self.created(w)[1:]
        self.assertEqual(w.record("issues", c)["state"], "closed")
        self.assertEqual([t for t in ticks if any(re.fullmatch(rf"STEP #({b}|{c}) lock PR #\d+", x) for x in t)],
                         ticks[-2:])
        self.assertTrue(re.fullmatch(rf"STEP #{b} lock PR #\d+", ticks[-2][0]), ticks[-2])
        self.assertTrue(re.fullmatch(rf"STEP #{c} lock PR #\d+", ticks[-1][-1]), ticks[-1])
        self.assertFalse([x for t in ticks for x in t if " file reel " in x])

        self.assertEqual(self.ticked(w, apply=True), [f"STEP #{b} file reel ep01"])
        r = self.created(w)[-1]
        issue = w.record("issues", r)
        self.assertEqual((issue["state"], issue["labels"]),
                         ("open", ["room:story", "stage:reel", "tracked", "unit:ep01"]))
        pulls = [json.loads(path.read_text()) for path in (w.github / "pulls").glob("*.json")]
        merged = {x["ticket"]: x["number"] for x in pulls if x["state"] == "merged"}
        self.assertEqual([x for x in issue["body"].splitlines() if x.startswith("Upstream: ")],
                         [f"Upstream: #{b} board ep01/Q1, locked by PR #{merged[b]}.",
                          f"Upstream: #{c} board ep01/Q2, locked by PR #{merged[c]}."])
        self.assertEqual(self.ticked(w, apply=True), [f"STEP #{r} launch editorial on #{r}"])
        self.assertEqual(len(self.created(w)), 4)

        old = w.head("main")
        with w.detached(old) as where:
            target = where / "episodes/ep01/sequences/Q3/sequence.json"
            target.parent.mkdir(parents=True)
            target.write_text('{"id": "Q3"}\n')
            w.commit("a third sequence", cwd=where)
            new = w.git("rev-parse", "HEAD", cwd=where).strip()
        w.move_branch("main", new, old)
        self.assertEqual(self.ticked(w, apply=True),
                         [f"STEP #{self.created(w)[0]} file board ep01/Q3",
                          f"WAIT #{r} waiting upstream stage board is not locked for ep01/Q3"])


class OperatorJob(Case):
    """ISSUE-96: one request offline from the maker's READY through its job, the gate, the run and the
    operator's report. The beats stage is given to editorial and the operator media-wrangler; the request,
    tests/fixtures/e2e/jobs/request.json, is also the script of the fake tool tests/fixtures/run/tools/fake.py,
    reached through the shipped register plus the fake tool's entry, written in the world's temporary
    directory."""

    REQUEST = "episodes/ep01/reel/requests/r1.json"
    JOB = "jobs/" + REQUEST
    CAPS = {"CAP_EPISODE_USD": 100, "CAP_SEASON_USD": 500, "CAP_EPISODE_COMPUTE_MIN": 1000,
            "CAP_SEASON_COMPUTE_MIN": 5000, "CAP_TICKET_ITERATIONS": 10, "CAP_EPISODE_HOURS": 10000,
            "CAP_SEASON_HOURS": 100000}

    def setUp(self):
        entries = pipeline()
        entries[1] = {**entries[1], "maker": ["editorial", "media-wrangler"], "lint": ["media"],
                      "paths": ["episodes/*/reel/**"]}
        w = self.w = self.world(entries)
        env = w.show / "production.env"
        names = [line.split("=", 1)[0] for line in env.read_text().splitlines()]
        env.write_text("".join(f"{n}={self.CAPS[n]}\n" if n in self.CAPS else line + "\n"
                               for n, line in zip(names, env.read_text().splitlines())))
        w.commit("the caps")
        register = json.loads((ROOT / "kit" / "resources.json").read_text())
        fake = json.loads((ROOT / "tests" / "fixtures" / "run" / "resources.json").read_text())
        register["path_settings"] = sorted(set(register["path_settings"]) | {"FAKE_LOG"})
        register["resources"] = register["resources"] + fake["resources"]
        (w.tmp / "resources.json").write_text(json.dumps(register, indent=1) + "\n")
        for patch in (mock.patch.object(cmd_run, "TOOLS_DIR", ROOT / "tests" / "fixtures" / "run" / "tools"),
                      mock.patch.object(resources, "REGISTER", w.tmp / "resources.json")):
            patch.start()
            self.addCleanup(patch.stop)
        key = w.secrets / "gen.fake"
        key.write_text("a-key-for-e2e\n")
        key.chmod(0o600)
        self.fake_log = w.tmp / "fake.log"
        with open(w.show / "production.local.env", "a") as handle:
            handle.write(f"FAKE_LOG={self.fake_log}\n")
        self.request = fixture("jobs/request.json")
        self.job = {"caller": "media-wrangler", "expected": {"compute_min": 2, "usd": 1},
                    "inputs": [{"path": self.REQUEST, "sha256": hashlib.sha256(self.request.encode()).hexdigest()}],
                    "ticket": 3, "tool": "fake"}
        self.digest = hashlib.sha256(json.dumps(self.job, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        w.script(step("You are editorial", "READY", {self.REQUEST: self.request}, evidence=EVIDENCE),
                 step("You are the media wrangler", "READY", {self.JOB: json.dumps(self.job, indent=1) + "\n"},
                      evidence=EVIDENCE),
                 editor_approved(),
                 step("You are the media wrangler", "READY", evidence=EVIDENCE),
                 editor_approved())

    def launched(self, role):
        return [c for c in self.w.comments(4) if first(c).startswith("[SHOWRUNNER] LAUNCH " + role)]

    def ref(self):
        return self.w.record("pulls", 4)["head_ref"]

    def launch(self, role, number):
        return self.w.run(cmd_launch.main, role, str(number), "--root", str(self.w.show))

    def test_one_request_through_its_job(self):
        w = self.w
        self.tick_line(w, ["STEP #3 launch editorial on #3"], apply=True)
        self.tick_line(w, ["STEP #3 launch media-wrangler on PR #4"], apply=True)
        [writing] = self.launched("media-wrangler")
        self.assertIn(f"Turn each request on pull request #4 into a job file.\n- {self.REQUEST} -> {self.JOB}\n"
                      "Each job names ticket 3 and caller media-wrangler. "
                      "The ticket names the tool and the expected cost.\n", writing["body"])
        self.assertEqual(w.git("show", f"{self.ref()}:{self.JOB}"), json.dumps(self.job, indent=1) + "\n")
        self.assertEqual(first(w.comments(4)[-1]), "[MEDIA-WRANGLER] READY")
        self.tick_line(w, ["STEP #3 lint PR #4"], apply=True)
        self.assertEqual(first(w.comments(4)[-1]), "[LINT] PASS")
        self.tick_line(w, ["STEP #3 launch story-editor on PR #4"], apply=True)
        self.assertEqual(first(w.comments(4)[-1]), "[STORY-EDITOR] APPROVED")
        self.tick_line(w, [f"STEP #3 gate {self.JOB} PR #4"], apply=True)
        gate = w.comments(3)[-1]
        self.assertEqual(gate["body"].split("\n")[:2], ["[PRODUCER] GATE PASS", f"covers: job {self.digest}"])
        self.tick_line(w, [f"STEP #3 run {self.JOB} PR #4"], apply=True)
        rows = ledger.read(w.git("show", "main:" + ledger.PATH))
        self.assertEqual([(r["kind"], r["hash"], r["verdict"]) for r in rows],
                         [("job", self.digest, "PASS"), ("run", self.digest, "DONE")])
        self.assertEqual(w.git("log", "-1", "--format=%s", self.ref()).strip(), f"manifest: job {self.digest[:12]}")
        self.assertEqual(self.fake_log.read_text().split(), [self.digest])
        media_id = f"{self.digest[:16]}-frame.txt"
        self.assertIn(f"\t{'job ' + self.digest}", w.git("show", f"{self.ref()}:media/manifest.tsv"))
        self.tick_line(w, ["STEP #3 launch media-wrangler on PR #4"], apply=True)
        reporting = self.launched("media-wrangler")[-1]
        self.assertIn(f"Report on the jobs of pull request #4. Write no file.\n- {self.JOB}: job {self.digest} DONE "
                      f"usd 1 compute_min 2; media: {media_id}\n", reporting["body"])
        self.assertEqual(first(w.comments(4)[-1]), "[MEDIA-WRANGLER] READY")
        self.tick_line(w, ["STEP #3 lint PR #4"], apply=True)
        self.tick_line(w, ["STEP #3 launch story-editor on PR #4"], apply=True)
        self.assertEqual(len(self.launched("media-wrangler")), 2)
        self.assertEqual(self.fake_log.read_text().split(), [self.digest])

    def test_the_report_needs_a_counting_pass(self):
        w = self.w
        self.tick_line(w, ["STEP #3 launch editorial on #3"], apply=True)
        self.tick_line(w, ["STEP #3 launch media-wrangler on PR #4"], apply=True)
        posts = len(w.log())
        runs = sorted(p.name for p in (w.state / "runs").iterdir())
        self.assertEqual(self.launch("media-wrangler", 4),
                         (1, "", f"launch: job {self.digest} has no counting GATE PASS\n"))
        self.assertEqual(len(w.log()), posts)
        self.assertEqual(sorted(p.name for p in (w.state / "runs").iterdir()), runs)


class BlockedJudgments(Case):
    """ISSUE-242 behaviours 8 and 9: tick runs the showrunner once for a BLOCKED maker, on the ticket and
    on a pull, then the maker again with the ruling, and the ticket goes on to its lock; no comment is
    posted by the test."""

    REFUSED = "a valid result (result.marker: a maker posting as STORY-WRITER may not post 'APPROVED')"
    BLOCKED = "NEED #3 blocked the maker is BLOCKED on #{}"

    @staticmethod
    def refused():
        return step("You are the story writer", "APPROVED", {BEATS: fixture("beats-1.md")}, evidence=EVIDENCE)

    @staticmethod
    def writer_blocked():
        return step("You are the story writer", "BLOCKED",
                    blocked={"missing": "which beat the note means", "tried": "the beats and the note",
                             "resume_from": "the find beat"})

    @staticmethod
    def ruling():
        return step("You are the showrunner", "RULING",
                    evidence=[{"criterion": "story-writer BLOCKED", "result": "ruled",
                               "command": "episodes/ep01/beats.md B5"}])

    def labels_added(self, w):
        return [(e["number"], e["label"]) for e in w.log() if e["op"] == "add_label"]

    @staticmethod
    def firsts(w, *numbers):
        return [(c["role"], first(c)) for n in numbers for c in w.comments(n)]

    def prompt_of(self, w, record):
        """The prompt of a LAUNCH record, read from its run directory."""
        role, run_id = first(record).split(" ")[2:4]
        return (w.state / "runs" / f"{role}-{run_id}" / "prompt.md").read_text()

    # -- behaviour 8: a maker BLOCKED on the ticket

    def test_blocked_on_the_ticket_to_lock(self):
        w = self.world()
        w.script(self.refused(), self.ruling(), writer_ready(fixture("beats-1.md")), editor_approved())
        ticks = to_lock(self, w)
        self.assertEqual(ticks, [[line] for line in [
            "STEP #3 launch story-writer on #3", "STEP #3 launch showrunner on #3", "STEP #3 launch story-writer on #3",
            "STEP #3 lint PR #4", "STEP #3 launch story-editor on PR #4", "STEP #3 gate PR #4", "STEP #3 sync PR #4",
            "STEP #3 lock PR #4"]])
        [blocked] = [c for c in w.comments(3) if first(c) == "[STORY-WRITER] BLOCKED"]
        self.assertIn(f"Missing: {self.REFUSED}", blocked["body"].splitlines())
        found = self.firsts(w, 3)
        self.assertEqual(found.count(("showrunner", "[SHOWRUNNER] LAUNCH showrunner 3-1")), 1)
        self.assertEqual(found.count(("showrunner", "[SHOWRUNNER] RULING")), 1)
        self.assertEqual([b for _, b in found if " LAUNCH showrunner " in b], ["[SHOWRUNNER] LAUNCH showrunner 3-1"])
        [ruling] = [c for c in w.comments(3) if first(c) == "[SHOWRUNNER] RULING"]
        self.assertIn(ruling["body"].rstrip("\n"), (w.state / "runs" / "story-writer-3-2" / "prompt.md").read_text())
        self.assertEqual(w.record("pulls", 4)["state"], "merged")
        self.assertEqual(w.record("issues", 3)["state"], "closed")
        self.assertEqual(self.labels_added(w), [])

    def test_blocked_twice_on_the_ticket(self):
        w = self.world()
        w.script(self.refused(), self.ruling(), self.refused())
        ticks = [self.ticked(w, apply=True) for _ in range(5)]
        self.assertEqual(ticks, [["STEP #3 launch story-writer on #3"], ["STEP #3 launch showrunner on #3"],
                                 ["STEP #3 launch story-writer on #3"], [self.BLOCKED.format(3)],
                                 [self.BLOCKED.format(3)]])
        self.assertIn(LABEL, w.record("issues", 3)["labels"])
        self.assertEqual(self.labels_added(w), [(3, LABEL)])
        self.assertEqual([b for _, b in self.firsts(w, 3) if " LAUNCH showrunner " in b],
                         ["[SHOWRUNNER] LAUNCH showrunner 3-1"])
        self.assertEqual(sorted(p.name for p in (w.github / "pulls").glob("*.json")), ["2.json"])

    # -- behaviour 9: a maker BLOCKED on a pull

    PULL_TICKS = ["STEP #3 launch story-writer on #3", "STEP #3 lint PR #4", "STEP #3 launch story-editor on PR #4",
                  "STEP #3 launch story-writer on PR #4", "STEP #3 launch showrunner on PR #4",
                  "STEP #3 launch story-writer on PR #4"]

    def test_blocked_on_a_pull_to_lock(self):
        w = self.world()
        w.script(writer_ready(fixture("beats-1.md")), editor_notes(), self.writer_blocked(), self.ruling(),
                 writer_addressed(fixture("beats-2.md")), editor_approved())
        ticks = to_lock(self, w, ticks=20)
        self.assertEqual(ticks, [[line] for line in self.PULL_TICKS + [
            "STEP #3 lint PR #4", "STEP #3 launch story-editor on PR #4", "STEP #3 gate PR #4", "STEP #3 sync PR #4",
            "STEP #3 lock PR #4"]])
        found = self.firsts(w, 3, 4)
        self.assertEqual([b for _, b in found if " LAUNCH showrunner " in b or " RULING" in b],
                         ["[SHOWRUNNER] LAUNCH showrunner 4-1", "[SHOWRUNNER] RULING"])
        comments = sorted(w.comments(4), key=lambda c: (c["created_at"], c["id"]))
        i = next(i for i, c in enumerate(comments) if first(c) == "[SHOWRUNNER] RULING")
        self.assertEqual(comments[i]["role"], "showrunner")
        record = next(c for c in comments[i + 1:] if first(c).startswith("[SHOWRUNNER] LAUNCH story-writer "))
        self.assertIn(comments[i]["body"].rstrip("\n"), self.prompt_of(w, record))
        self.assertEqual(w.record("pulls", 4)["state"], "merged")
        self.assertEqual(w.record("issues", 3)["state"], "closed")
        self.assertEqual(self.labels_added(w), [])

    def test_blocked_twice_on_a_pull(self):
        w = self.world()
        w.script(writer_ready(fixture("beats-1.md")), editor_notes(), self.writer_blocked(), self.ruling(),
                 self.writer_blocked())
        ticks = [self.ticked(w, apply=True) for _ in range(len(self.PULL_TICKS) + 2)]
        self.assertEqual(ticks, [[line] for line in self.PULL_TICKS + [self.BLOCKED.format(4)] * 2])
        self.assertIn(LABEL, w.record("issues", 3)["labels"])
        self.assertEqual(self.labels_added(w), [(3, LABEL)])
        self.assertEqual([b for _, b in self.firsts(w, 3, 4) if " LAUNCH showrunner " in b],
                         ["[SHOWRUNNER] LAUNCH showrunner 4-1"])
        self.assertEqual(w.record("pulls", 4)["state"], "open")


if __name__ == "__main__":
    unittest.main()
