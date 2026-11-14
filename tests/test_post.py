"""Tests for the distribution post stage: the upload job's gate and run, its recorded answer, the lock of the
post and its row of `registry/posts.tsv` (kit/lib/posts.py, cmd_gate, cmd_run, lockrules, cmd_lock).

The world is a production made by `factory init --rooms story,distribution` (the shipped pipeline and roles)
on branch `main` in a temporary directory, with the files of tests/fixtures/post/production committed on
`main`: the upload request of `ep01/youtube`, the media manifest, the YouTube rulebook and the episode's edit
list. GitHub is a copy of tests/fixtures/post/github: ticket #3 (`distribution/post`, `ep01/youtube`) has the
open pull #4, whose branch is made after the request and holds the job. The tool is
tests/fixtures/post/tools/upload.py and the register tests/fixtures/post/resources.json; the setting
`FAKE_ANSWER` names the answer the fake platform gives, under tests/fixtures/post/answers/. `POSTED` and
`VERIFIED` are recorded comments of `channel-youtube`. Nothing reaches the network.
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import (cmd_gate, cmd_init, cmd_lint, cmd_lock, cmd_run, gitstate, github, ledger,  # noqa: E402
                     posts, resources)

FIX = ROOT / "tests" / "fixtures" / "post"
NOW = "2026-11-02T09:00:00Z"
TICKET, PULL, BRANCH = 3, 4, "feat/PO-1-ep01-youtube"
UNIT = "ep01/youtube"
REQUEST = "distribution/ep01/youtube/requests/upload.json"
JOB = "jobs/" + REQUEST
ANSWER = "distribution/ep01/youtube/post.json"
BEATS = "B1=0-7.7,B2=7.7-17.7"
BROKE = "run: tool upload failed: tool broke the interface\n"
IDENTITY = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")
HEADER = "time\tpost\tepisode\tplatform\tpost_id\tvisibility\tsha256\tjob\tpull\thead\tbeats\n"


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


def video_sha():
    return json.loads((FIX / "answers" / "clean.json").read_text())["sha256"]


class World:
    """The production on `main`, its recorded GitHub, a state directory and a key, all temporary."""

    def __init__(self, test, answer="clean.json", maker=None, room=None):
        self.test = test
        tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, tmp)
        self.root, self.dir, self.state, self.secrets = tmp / "show", tmp / "github", tmp / "state", tmp / "secrets"
        env = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                                           "FACTORY_GITHUB": str(self.dir), "FACTORY_NOW": NOW})
        for patch in (env, mock.patch.object(cmd_run, "TOOLS_DIR", FIX / "tools"),
                      mock.patch.object(resources, "REGISTER", FIX / "resources.json")):
            patch.start()
            test.addCleanup(patch.stop)
        for name in IDENTITY:
            os.environ.pop(name, None)
        self.root.mkdir()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            test.assertEqual(cmd_init.main([str(self.root), "--rooms", "story,distribution"]), 0)
        shutil.copytree(FIX / "production", self.root, dirs_exist_ok=True)
        if maker is not None or room is not None:
            self.change_stage(maker, room)
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "the production and the upload request")
        shutil.copytree(FIX / "github", self.dir)
        if room is not None:
            self.edit_issue(labels=[f"room:{room}", "stage:upload", "tracked", f"unit:{UNIT}"])
        self.request = (self.root / REQUEST).read_text()
        self.job = {"caller": "channel-youtube", "expected": {"compute_min": 0, "usd": 0},
                    "inputs": [{"path": REQUEST, "sha256": cmd_gate.sha256(self.request)}],
                    "ticket": TICKET, "tool": "upload"}
        self.digest = hashlib.sha256(json.dumps(self.job, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.git("branch", BRANCH, "main")
        self.on_branch({JOB: json.dumps(self.job, indent=1) + "\n"})
        self.secrets.mkdir()
        key = self.secrets / "platform.fake"
        key.write_text("seeded-key-of-the-fake-platform")
        key.chmod(0o600)
        self.answer(answer)
        self.gh = github.Recorded(self.dir)
        self.clock = 0

    # -- the world

    def change_stage(self, maker, room):
        path = self.root / ".factory" / "pipeline.json"
        entries = json.loads(path.read_text())
        for entry in entries:
            if (entry["room"], entry["stage"]) == ("distribution", "post"):
                if maker is not None:
                    entry["maker"] = maker
                if room is not None:
                    entries.append({**entry, "room": room, "stage": "upload", "paths": ["**"]})
                break
        path.write_text(json.dumps(entries, indent=1) + "\n")

    def answer(self, name):
        (self.root / "production.local.env").write_text(
            f"STATE_DIR={self.state}\nSECRETS_DIR={self.secrets}\nFAKE_ANSWER={FIX / 'answers' / name}\n")

    def edit_issue(self, **changes):
        path = self.dir / "issues" / f"{TICKET}.json"
        path.write_text(json.dumps({**json.loads(path.read_text()), **changes}, indent=1) + "\n")

    def git(self, *args):
        return gitstate.git(self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", *args)

    def head(self, ref=BRANCH):
        return gitstate.head(self.root, ref)

    def on_branch(self, files):
        self.git("checkout", "-q", BRANCH)
        for rel, text in files.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "files on the pull")
        self.git("checkout", "-q", "main")

    def merge_base(self):
        """Merge `main` into the pull's branch, as the maker does before the lock."""
        self.git("checkout", "-q", BRANCH)
        self.git("merge", "-q", "--no-edit", "main")
        self.git("checkout", "-q", "main")

    def tick(self):
        """The next minute after NOW, as a comment time."""
        self.clock += 1
        return "2026-11-02T10:%02d:00Z" % self.clock

    def record(self, body, role="channel-youtube"):
        path = self.dir / "comments" / f"{PULL}.json"
        found = json.loads(path.read_text()) if path.is_file() else []
        ids = [c["id"] for p in (self.dir / "comments").glob("*.json") for c in json.loads(p.read_text())] \
            if (self.dir / "comments").is_dir() else []
        comment = {"body": body, "created_at": self.tick(), "id": max(ids, default=0) + 1, "role": role}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(found + [comment], indent=1) + "\n")
        return comment

    def posted(self):
        return self.record(f"[CHANNEL:YOUTUBE] POSTED\npost yt123\nreviewed head {self.head()} manifest none\n")

    def verified(self, actor="CHANNEL:YOUTUBE", role="channel-youtube"):
        return self.record(f"[{actor}] VERIFIED\npost yt123 private\n", role)

    # -- the commands

    def main(self, main, argv, gh=None, now=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(github, "connect", return_value=gh or self.gh))
            if now is not None:
                stack.enter_context(mock.patch.dict(os.environ, {"FACTORY_NOW": now}))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def gate(self, gh=None):
        return self.main(cmd_gate.main, [JOB, "--pull", str(PULL), "--root", str(self.root), "--apply"], gh)

    def run(self, gh=None):
        return self.main(cmd_run.main, [JOB, "--pull", str(PULL), "--root", str(self.root), "--apply"], gh)

    def lint(self):
        return self.main(cmd_lint.main, ["--root", str(self.root), "--post", str(PULL)], now=self.tick())

    def lock(self, apply=True, gh=None):
        return self.main(cmd_lock.main, ["--root", str(self.root)] + (["--apply"] if apply else []), gh,
                         now=self.tick())

    # -- what is there

    def rows(self, kind):
        text = gitstate.show(self.root, "main", ledger.PATH)
        return [r for r in (ledger.read(text) if text is not None else []) if r["kind"] == kind]

    def registry(self):
        return gitstate.show(self.root, "main", posts.PATH)

    def subject(self, ref):
        return self.git("log", "-1", "--format=%an|%s", ref).strip()

    def branch_files(self, ref=BRANCH, since=None):
        return sorted(self.git("diff", "--name-only", since or "main", ref).split())


def printed(digest, verdict):
    return f"run job {digest}: {verdict} usd 0 compute_min 0\n"


class Chain(unittest.TestCase):
    """Behaviours 1 and 2: the clean chain, and the gate of the upload job."""

    def gated_and_run(self, w):
        code, out, err = w.gate()
        self.assertEqual((code, out, err), (0, f"gate job {w.digest}: PASS cap - reason within caps\n", ""))
        before = w.head()
        self.assertEqual(w.run(), (0, printed(w.digest, "DONE"), ""))
        self.assertEqual(w.subject(BRANCH), f"factory|manifest: job {w.digest[:12]}")
        self.assertEqual(w.branch_files(BRANCH, before), [ANSWER])
        return before

    def test_last_stage_of_distribution_is_window(self):
        entries = json.loads((ROOT / "kit" / "pipeline.json").read_text())
        self.assertEqual([e["stage"] for e in entries if e["room"] == "distribution"], ["post", "window"])

    def test_clean_post(self):
        w = World(self)
        self.gated_and_run(w)
        answer = json.loads(gitstate.show(w.root, BRANCH, ANSWER))
        self.assertEqual((answer["job"], answer["post_id"]), (w.digest, "yt123"))
        w.posted()
        code, out, err = w.lint()
        self.assertEqual(code, 0, out + err)
        [lint] = [c for c in w.gh.comments(PULL) if c["role"] == "program:lint"]
        self.assertTrue(lint["body"].startswith("[LINT] PASS\n"), lint["body"])
        self.assertIn("Checks required by stage post: publish, media\n", lint["body"])
        w.verified()
        w.merge_base()
        head = w.head()
        code, out, err = w.lock()
        self.assertEqual((code, err), (0, ""), out)
        self.assertIn(f"locked PR #{PULL} at ", out)
        self.assertTrue(out.endswith(f"registry/posts.tsv: {UNIT} yt123\n"), out)
        lock_time = json.loads((w.dir / "issues" / f"{TICKET}.json").read_text())
        self.assertEqual(lock_time["state"], "closed")
        [row] = w.registry().splitlines()[1:]
        self.assertEqual(w.registry().splitlines()[0] + "\n", HEADER)
        cells = row.split("\t")
        self.assertEqual(cells[1:], [UNIT, "ep01", "youtube", "yt123", "private", video_sha(), w.digest, str(PULL),
                                     head, BEATS])
        self.assertRegex(cells[0], r"^2026-11-02T10:\d\d:00Z$")
        self.assertEqual(w.subject("main"), "factory|registry: post ep01/youtube")
        self.assertIn({"number": None, "op": "push", "ref": "main"},
                      [json.loads(line) for line in (w.dir / "log.jsonl").read_text().splitlines()][-1:])

    def test_no_disclosure_fails_the_lint_and_the_lock(self):
        w = World(self, answer="no-disclosure.json")
        self.gated_and_run(w)
        w.posted()
        code, out, err = w.lint()
        self.assertEqual(code, 1, out + err)
        [lint] = [c for c in w.gh.comments(PULL) if c["role"] == "program:lint"]
        self.assertTrue(lint["body"].startswith("[LINT] FAIL\n"), lint["body"])
        self.assertIn("post yt123 has no disclosure field status.containsSyntheticMedia", lint["body"])
        w.verified()
        w.merge_base()
        code, out, err = w.lock(apply=False)
        self.assertIn(f"  rule 1 lint: lint #{lint['id']} is FAIL\n", out)
        self.assertIsNone(w.registry())

    def test_the_gate_asks_no_lint_at_an_operator_only_stage(self):
        w = World(self)
        self.assertEqual([c for c in w.gh.comments(PULL) if c["role"] == "program:lint"], [])
        self.assertEqual(w.gate()[:2], (0, f"gate job {w.digest}: PASS cap - reason within caps\n"))

    def test_a_maker_then_an_operator_needs_the_lint(self):
        w = World(self, maker=["editorial", "channel-youtube"])
        self.assertEqual(w.gate(), (1, f"gate job {w.digest}: STOP cap - reason verdicts\n", ""))


class Record(unittest.TestCase):
    """Behaviour 3: what a tool may leave in ``record/``."""

    def assert_broke(self, w):
        self.assertEqual(w.gate()[0], 0)
        before = w.head()
        self.assertEqual(w.run(), (1, printed(w.digest, "FAILED"), BROKE))
        self.assertEqual(w.head(), before)
        [row] = w.rows("run")
        self.assertEqual((row["verdict"], row["usd"]), ("FAILED", "0"))
        result = json.loads((w.state / "jobs" / w.digest / "result.json").read_text())
        self.assertNotIn("record", result)

    def test_a_record_named_other_json(self):
        self.assert_broke(World(self, answer="elsewhere"))

    def test_an_answer_with_an_unknown_key(self):
        self.assert_broke(World(self, answer="unknown-key.json"))

    def test_an_answer_of_another_job(self):
        self.assert_broke(World(self, answer="stale"))

    def test_a_ticket_of_room_story(self):
        self.assert_broke(World(self, room="story"))

    def test_a_kept_record_is_listed_and_checked_again(self):
        w = World(self)
        self.assertEqual(w.gate()[0], 0)
        self.assertEqual(w.run(gh=FlakyPush(w.dir, {1}))[0], 2)
        job_dir = w.state / "jobs" / w.digest
        result = json.loads((job_dir / "result.json").read_text())
        text = (job_dir / "record" / "post.json").read_bytes()
        self.assertEqual(result["record"], [{"bytes": len(text), "name": "post.json",
                                             "sha256": hashlib.sha256(text).hexdigest()}])
        self.assertEqual(result["outputs"], [])
        (job_dir / "record" / "post.json").write_bytes(text + b" ")
        self.assertEqual(w.run(), (2, "", f"run: the outputs of job {w.digest} changed after the call\n"))

    def test_a_failed_push_then_a_second_run(self):
        w = World(self)
        self.assertEqual(w.gate()[0], 0)
        before = w.head()
        self.assertEqual(w.run(gh=FlakyPush(w.dir, {1})), (2, "", "run: push: exit 1\n"))
        self.assertEqual(w.run(), (0, printed(w.digest, "DONE"), ""))
        self.assertEqual(w.git("rev-list", "--count", f"{before}..{BRANCH}").strip(), "1")
        self.assertEqual(w.branch_files(BRANCH, before), [ANSWER])
        self.assertEqual([r["verdict"] for r in w.rows("run")], ["DONE"])

    def test_an_empty_record_is_removed(self):
        w = World(self)
        with mock.patch.object(cmd_run, "_call", return_value=("DONE", 0, 0, None, [])):
            self.assertEqual(w.gate()[0], 0)
            before = w.head()
            self.assertEqual(w.run()[0], 0)
        self.assertFalse((w.state / "jobs" / w.digest / "record").exists())
        self.assertEqual(w.head(), before)


class Lock(unittest.TestCase):
    """Behaviour 4: rules 2 and 7 at ``distribution/post``."""

    def ready_world(self):
        w = World(self)
        self.assertEqual(w.gate()[0], 0)
        self.assertEqual(w.run()[0], 0)
        w.posted()
        self.assertEqual(w.lint()[0], 0)
        return w

    def refused(self, w, line):
        w.merge_base()
        code, out, err = w.lock()
        self.assertEqual((code, err), (0, ""))
        self.assertIn(line, out)
        self.assertTrue(out.endswith("nothing to lock\n"), out)
        self.assertIsNone(w.registry())

    def test_before_verified(self):
        self.refused(self.ready_world(),
                     "  rule 2 checkers: no [CHANNEL:YOUTUBE] VERIFIED newer than the counting [LINT] verdict\n")

    def test_verified_older_than_the_lint(self):
        w = World(self)
        self.assertEqual(w.gate()[0], 0)
        self.assertEqual(w.run()[0], 0)
        w.posted()
        w.verified()
        self.assertEqual(w.lint()[0], 0)
        self.refused(w, "  rule 2 checkers: no [CHANNEL:YOUTUBE] VERIFIED newer than the counting [LINT] verdict\n")

    def test_verified_by_another_channel(self):
        w = self.ready_world()
        w.verified("CHANNEL:TIKTOK", "channel-tiktok")
        self.refused(w, "  rule 2 checkers: no [CHANNEL:YOUTUBE] VERIFIED newer than the counting [LINT] verdict\n")

    def test_a_post_json_with_no_run_row(self):
        w = World(self)
        answer = json.loads((FIX / "answers" / "clean.json").read_text())
        answer["job"] = "e" * 64
        w.on_branch({ANSWER: json.dumps(answer, indent=1, sort_keys=True) + "\n"})
        w.posted()
        self.assertEqual(w.lint()[0], 1)
        w.verified()
        self.refused(w, "  rule 7 writes: outside the writes of channel-youtube: distribution/ep01/youtube/post.json\n")


class Row(unittest.TestCase):
    """Behaviours 5 and 7: a row that is not written, and the beat map."""

    def test_a_push_that_fails_then_the_next_locks(self):
        w = World(self)
        self.assertEqual(w.gate()[0], 0)
        self.assertEqual(w.run()[0], 0)
        w.posted()
        self.assertEqual(w.lint()[0], 0)
        w.verified()
        w.merge_base()
        code, out, err = w.lock(gh=FlakyPush(w.dir, {1}))
        self.assertEqual(code, 5, out + err)
        merged = w.head("main")
        self.assertEqual(err, f"lock: PR #{PULL} is merged at {merged[:12]}, registry/posts.tsv not written: "
                              "push: exit 1\n")
        self.assertIsNone(w.registry())
        self.assertEqual(w.git("status", "--porcelain").strip(), "")
        code, out, err = w.lock()
        self.assertEqual((code, err), (0, ""), out)
        self.assertTrue(out.startswith(f"registry/posts.tsv: {UNIT} yt123\n"), out)
        self.assertEqual(len(w.registry().splitlines()), 2)
        self.assertEqual(w.subject("main"), "factory|registry: post ep01/youtube")
        self.assertEqual(w.registry().splitlines()[1].split("\t")[8:10], [str(PULL), w.head()])
        code, out, err = w.lock()
        self.assertEqual((code, err, out), (0, "", "nothing to lock\n"))
        self.assertEqual(len(w.registry().splitlines()), 2)

    def test_beat_map(self):
        events = [{"shot": "s1", "beat": "B1", "start": 0, "duration": 4.5},
                  {"shot": "s2", "beat": "B1", "start": 4.5, "duration": 3.2},
                  {"shot": "s3", "beat": "B2", "start": 7.7, "duration": 10}]
        self.assertEqual(posts.beats(json.dumps({"events": events})), BEATS)
        self.assertEqual(posts.beats((FIX / "production" / "episodes" / "ep01" / "reel" / "edit-list.json").read_text()),
                         BEATS)
        self.assertEqual(posts.beats(json.dumps({"events": [{"beat": "B1", "start": 0.1, "duration": 0.2}]})),
                         "B1=0.1-0.3")

    def test_no_beat_map(self):
        for text in (None, "{", "[]", '{"events": []}', '{"events": [{"beat": "B1", "start": true, "duration": 1}]}',
                     '{"events": [{"beat": 1, "start": 0, "duration": 1}]}', '{"events": [{"beat": "B1", "start": 0}]}'):
            with self.subTest(text=text):
                self.assertEqual(posts.beats(text), "-")

    def test_no_edit_list_gives_a_dash_in_the_row(self):
        w = World(self)
        w.git("rm", "-q", "episodes/ep01/reel/edit-list.json")
        w.git("commit", "-q", "-m", "no edit list")
        self.assertEqual(w.gate()[0], 0)
        self.assertEqual(w.run()[0], 0)
        w.posted()
        self.assertEqual(w.lint()[0], 0)
        w.verified()
        w.merge_base()
        self.assertEqual(w.lock()[0], 0)
        self.assertEqual(w.registry().splitlines()[1].split("\t")[-1], "-")


if __name__ == "__main__":
    unittest.main()
