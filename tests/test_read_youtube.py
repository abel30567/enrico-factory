"""Tests for `factory run --read <topic> youtube`: kit/lib/reads.py and kit/lib/tools/reads/youtube.py.

The world copies tests/fixtures/read_youtube/: `production/` is committed on `trunk`, and `answers/` is what a
fake YouTube Data API answers. Each answer file names the API reference page it was written from and the
day it was read: `{"reference", "read_on", "answers": [{"query", "status", "body"}]}`. The fake API is one
`ThreadingHTTPServer` on 127.0.0.1, port 0: a `GET /<endpoint>?<query>` gets the entry of
`answers/<endpoint>.json` whose `query` equals the request's less `key`, else a 404, and every request is
logged. `production.local.env` sets `STATE_DIR`, `SECRETS_DIR` and `YOUTUBE_API_URL`; the key file
holds a seeded string, mode 0600; `FACTORY_NOW` is 2026-11-13T09:00:00Z. Nothing reaches a host other
than 127.0.0.1.

Recorded: search gives a channel (no video id), then yt-a (ch-1), yt-b and yt-c (ch-2); yt-a has its three
counts and lasts PT45S; yt-b has no likeCount, lasts PT4M10S, was published at a time ending `.5Z` and has
its comments off; yt-c is not in the `videos` answer; ch-1 shows 12000 followers and has 11 other uploads;
ch-2 hides its count and has one other upload, seen 0 times. Each seeded defect is one change to the world.
"""

import contextlib
import http.server
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kit.lib import cmd_run, reads, resources, trends  # noqa: E402
from kit.lib.tools import Failed  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "read_youtube"
NOW = "2026-11-13T09:00:00Z"
TOPIC = "harbour-tales"
SEED = "seeded-key-Wickabel-5d81c3"
STORED = "reads/harbour-tales/youtube/20261113T090000Z.json"
BUDGET = "read-budget/youtube.json"
READ_LINE = "read harbour-tales youtube at 2026-11-13T09:00:00Z: 3 videos, search {} of {} on 2026-11-13\n"
WROTE = "wrote reads/harbour-tales/youtube/20261113T090000Z.json\n"
TOPIC_FILE = "research/topics/harbour-tales.json"
# The endpoint and the index in its answer file of each request of a clean read, in order (ruling 11).
CLEAN = (("search", 0), ("videos", 0), ("channels", 0), ("playlistItems", 0), ("playlistItems", 1),
         ("videos", 1), ("commentThreads", 0), ("commentThreads", 1), ("commentThreads", 2))
ABSENT = object()


def free_port():
    """A loopback port nothing listens on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class FakeApi:
    """The YouTube Data API on 127.0.0.1, answering from the answer files in ``answers``."""

    def __init__(self, test, answers):
        self.answers, self.requests = answers, []
        api = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                api.handle(self)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()
        test.addCleanup(self.server.server_close)
        test.addCleanup(self.server.shutdown)

    def handle(self, h):
        path, _, query = h.path.partition("?")
        params = dict(urllib.parse.parse_qsl(query, keep_blank_values=True))
        self.requests.append({"path": h.path, "params": dict(params),
                              "headers": {k.lower(): v for k, v in h.headers.items()}})
        params.pop("key", None)
        file = self.answers / f"{path.lstrip('/')}.json"
        found = None
        if "/" not in path.lstrip("/") and file.is_file():
            found = next((a for a in json.loads(file.read_text())["answers"] if a["query"] == params), None)
        status, body = (404, {"error": {"code": 404}}) if found is None else (found["status"], found["body"])
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        h.send_response(status)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


class World:
    def __init__(self, test):
        tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, tmp)
        self.test, self.tmp = test, tmp
        self.root, self.answers = tmp / "production", tmp / "answers"
        shutil.copytree(FIX / "production", self.root)
        shutil.copytree(FIX / "answers", self.answers)
        git(self.root, "init", "-q", "-b", "trunk")
        git(self.root, "config", "user.name", "test")
        git(self.root, "config", "user.email", "test@example.invalid")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "the production")
        self.state, self.secrets = tmp / "state", tmp / "secrets"
        self.secrets.mkdir(mode=0o700)
        self.key = self.secrets / "external.youtube"
        self.write_key(SEED + "\n")
        self.api = FakeApi(test, self.answers)
        self.settings = {"STATE_DIR": str(self.state), "SECRETS_DIR": str(self.secrets),
                         "YOUTUBE_API_URL": self.api.url}
        self.write_settings()
        patcher = mock.patch.dict(os.environ, {"FACTORY_NOW": NOW})
        patcher.start()
        test.addCleanup(patcher.stop)

    # -- seeded defects

    def write_key(self, text, mode=0o600):
        self.key.write_text(text)
        os.chmod(self.key, mode)

    def write_settings(self, **changes):
        self.settings.update(changes)
        lines = [f"{k}={v}\n" for k, v in self.settings.items() if v is not None]
        (self.root / "production.local.env").write_text("".join(lines))

    def answer(self, endpoint, index, change):
        path = self.answers / f"{endpoint}.json"
        doc = json.loads(path.read_text())
        change(doc["answers"][index])
        path.write_text(json.dumps(doc, indent=1) + "\n")

    def error(self, endpoint, status, reason=None, index=0):
        def change(entry):
            entry["status"] = status
            entry["body"] = {"error": {"code": status, "message": "refused",
                                       "errors": [] if reason is None else [{"reason": reason, "message": "x"}]}}
        self.answer(endpoint, index, change)

    def stats(self, vid, **fields):
        """Change the statistics of ``vid`` in the first `videos` answer."""
        def change(entry):
            video = next(v for v in entry["body"]["items"] if v["id"] == vid)
            for key, value in fields.items():
                if value is ABSENT:
                    video["statistics"].pop(key, None)
                else:
                    video["statistics"][key] = value
        self.answer("videos", 0, change)

    def commit_topic(self, doc, rel=TOPIC_FILE):
        path = self.root / rel
        path.write_text(doc if isinstance(doc, str) else json.dumps(doc) + "\n")
        git(self.root, "add", rel)
        git(self.root, "commit", "-q", "-m", "topic")

    def watch(self, **fields):
        watch = {"words": ["Skarrowfen", "Wickabel"], "region": "GB", "days": 7}
        for key, value in fields.items():
            if value is ABSENT:
                watch.pop(key)
            else:
                watch[key] = value
        self.commit_topic({"youtube": watch})

    def put_read(self, rel, text="{}\n"):
        path = self.state / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def put_budget(self, day, searches):
        self.put_read(BUDGET, json.dumps({"day": day, "searches": searches}, sort_keys=True) + "\n")

    # -- the command and what is there

    def run(self, *argv, apply=True, read=(TOPIC, "youtube")):
        args = (["--read", *read] if read else []) + list(argv) + ["--root", str(self.root)]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_run.main(args + (["--apply"] if apply else []))
        return code, out.getvalue(), err.getvalue()

    def budget(self):
        path = self.state / BUDGET
        return json.loads(path.read_text()) if path.exists() else None

    def stored(self):
        return json.loads((self.state / STORED).read_text())

    def video(self, vid):
        return next(v for v in self.stored()["videos"] if v["id"] == vid)

    def files(self):
        """Every file under STATE_DIR with its bytes."""
        if not self.state.exists():
            return {}
        return {str(p.relative_to(self.state)): p.read_bytes() for p in sorted(self.state.rglob("*")) if p.is_file()}

    def expected_paths(self):
        """The request paths of a clean read, with the key, from the answer files."""
        found = []
        for endpoint, index in CLEAN:
            query = json.loads((self.answers / f"{endpoint}.json").read_text())["answers"][index]["query"]
            found.append(f"/{endpoint}?" + urllib.parse.urlencode(sorted({**query, "key": SEED}.items())))
        return found


class Base(unittest.TestCase):
    def setUp(self):
        self.w = World(self)

    def refused(self, line, *argv, out="", stored=False, **kwargs):
        """``factory run`` refuses with ``line`` on stderr and exit 2, prints ``out`` and makes no request."""
        got = self.w.run(*argv, **kwargs)
        self.assertEqual(got, (2, out, line + "\n"))
        self.assertEqual(self.w.api.requests, [])
        self.assertEqual((self.w.state / STORED).exists(), stored)

    def failed(self, reason, code=1, line=None):
        """The read fails with ``reason`` (exit 1), or with ``line`` and exit ``code``; nothing is stored."""
        got = self.w.run()
        self.assertEqual(got, (code, "", line or f"run: read harbour-tales youtube failed: {reason}\n"))
        self.assertFalse((self.w.state / STORED).exists())

    def down(self):
        self.failed(None, 2, "run: resource external.youtube is down\n")


class B01CleanRead(Base):
    def test_clean_read(self):
        code, out, err = self.w.run()
        self.assertEqual((code, out, err), (0, READ_LINE.format(1, 100) + WROTE, ""))
        self.assertEqual([r["path"] for r in self.w.api.requests], self.w.expected_paths())
        self.assertEqual((self.w.state / STORED).read_bytes(), (FIX / "expected" / "20261113T090000Z.json").read_bytes())
        reads_ = trends.load_reads(self.w.state / "reads" / TOPIC / "youtube", TOPIC, "youtube")
        self.assertEqual([v["id"] for v in reads_[0]["videos"]], ["yt-a", "yt-b", "yt-c"])
        self.assertEqual(self.w.budget(), {"day": "2026-11-13", "searches": 1})
        self.assertEqual(sorted(self.w.files()), [BUDGET, STORED])

    def test_the_counts_of_yt_a(self):
        self.w.run()
        video = self.w.video("yt-a")
        self.assertEqual((video["views"], video["likes"], video["comments"], video["followers"]), (5400, 320, 41, 12000))
        self.assertEqual((video["duration_s"], video["order"], video["author"]), (45, 1, "ch-1"))
        self.assertEqual(video["author_views"], [2100, 2000, 1900, 1800, 1700, 1600, 1500, 1400, 1300, 1200, 1100])
        self.assertEqual(video["top_comments"], ["Wickabel!", "Skarrowfen, Skarrowfen."])
        self.assertEqual(video["link"], "https://www.youtube.com/watch?v=yt-a")
        self.assertNotIn("shares", video)
        self.assertNotIn("saves", video)


class B02MissingIsNotZero(Base):
    def test_missing_counts_are_null(self):
        self.assertEqual(self.w.run()[0], 0)
        b, c = self.w.video("yt-b"), self.w.video("yt-c")
        self.assertIsNone(b["likes"])
        self.assertIsNone(b["followers"])
        self.assertEqual(b["author_views"], [0])
        self.assertIsNone(b["top_comments"])
        self.assertEqual(b["duration_s"], 250)
        self.assertEqual(b["published_at"], "2026-11-11T09:00:00Z")
        self.assertEqual(b["comments"], 0)
        self.assertEqual([c[k] for k in ("author", "title", "views", "likes", "comments", "followers", "author_views",
                                         "duration_s", "published_at")], [None] * 9)
        self.assertEqual((c["order"], c["top_comments"]), (3, []))

    def test_a_zero_count_is_zero(self):
        self.w.stats("yt-b", likeCount="0")
        self.assertEqual(self.w.run()[0], 0)
        self.assertEqual(self.w.video("yt-b")["likes"], 0)

    def test_a_hidden_count_is_null_even_when_given(self):
        def change(entry):
            entry["body"]["items"][1]["statistics"]["subscriberCount"] = "300"
        self.w.answer("channels", 0, change)
        self.assertEqual(self.w.run()[0], 0)
        self.assertIsNone(self.w.video("yt-b")["followers"])


class B03Expiry(Base):
    OLD = ("reads/harbour-tales/youtube/20261014T090000Z.json", "reads/quay-songs/youtube/20261001T000000Z.json")
    KEPT = ("reads/harbour-tales/youtube/20261014T090001Z.json", "reads/harbour-tales/tiktok/20261001T000000Z.json",
            "reads/harbour-tales/youtube/notes.txt")

    def setUp(self):
        super().setUp()
        for rel in self.OLD + self.KEPT:
            self.w.put_read(rel)

    def lines(self, verb):
        return "".join(f"{verb} {rel}: older than 30 days\n" for rel in self.OLD)

    def test_old_reads_are_deleted(self):
        code, out, err = self.w.run()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, self.lines("deleted") + READ_LINE.format(1, 100) + WROTE)
        for rel in self.OLD:
            self.assertFalse((self.w.state / rel).exists(), rel)
        for rel in self.KEPT:
            self.assertTrue((self.w.state / rel).exists(), rel)

    def test_expiry_needs_no_key(self):
        self.w.key.unlink()
        self.refused("run: resource external.youtube is not usable: secret external.youtube: missing",
                     out=self.lines("deleted"))
        for rel in self.OLD:
            self.assertFalse((self.w.state / rel).exists(), rel)

    def test_without_apply_nothing_is_deleted(self):
        code, out, err = self.w.run(apply=False)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, self.lines("would delete")
                         + "read harbour-tales youtube: would search, 0 of 100 searches spent on 2026-11-13\n")
        for rel in self.OLD + self.KEPT:
            self.assertTrue((self.w.state / rel).exists(), rel)


class B04Budget(Base):
    def test_at_99_reads(self):
        self.w.put_budget("2026-11-13", 99)
        self.assertEqual(self.w.run()[:2], (0, READ_LINE.format(100, 100) + WROTE))
        self.assertEqual(self.w.budget(), {"day": "2026-11-13", "searches": 100})

    def test_at_100_is_refused(self):
        self.w.put_budget("2026-11-13", 100)
        self.refused("run: read harbour-tales youtube: 100 of 100 searches of 2026-11-13 are spent")
        self.assertEqual(self.w.budget(), {"day": "2026-11-13", "searches": 100})

    def test_another_day_counts_zero(self):
        self.w.put_budget("2026-11-12", 100)
        self.assertEqual(self.w.run()[:2], (0, READ_LINE.format(1, 100) + WROTE))
        self.assertEqual(self.w.budget(), {"day": "2026-11-13", "searches": 1})
        self.assertEqual((self.w.state / BUDGET).read_text(), '{"day": "2026-11-13", "searches": 1}\n')
        self.assertFalse((self.w.state / (BUDGET + ".tmp")).exists())

    def test_a_limit_of_3(self):
        self.w.write_settings(YOUTUBE_SEARCHES_PER_DAY="3")
        for n in (1, 2, 3):
            (self.w.state / STORED).unlink(missing_ok=True)
            self.assertEqual(self.w.run()[:2], (0, READ_LINE.format(n, 3) + WROTE))
        (self.w.state / STORED).unlink()
        self.w.api.requests.clear()
        self.refused("run: read harbour-tales youtube: 3 of 3 searches of 2026-11-13 are spent")

    def test_a_bad_limit(self):
        for value in ("101", "-1", "x"):
            with self.subTest(value=value):
                self.w.write_settings(YOUTUBE_SEARCHES_PER_DAY=value)
                self.refused("run: setting YOUTUBE_SEARCHES_PER_DAY must be a whole number from 0 to 100")

    def test_a_malformed_budget_file(self):
        for text in ("not json\n", '{"day": "2026-11-13"}\n', '{"day": "2026-11-13", "searches": -1}\n',
                     '{"day": "today", "searches": 1}\n', '{"day": "2026-11-13", "searches": true}\n'):
            with self.subTest(text=text):
                self.w.put_read(BUDGET, text)
                self.refused("run: read-budget/youtube.json is not a budget file")

    def test_a_failed_read_keeps_its_search(self):
        self.w.error("search", 500)
        self.failed("the platform answered with an error")
        self.assertEqual(self.w.budget(), {"day": "2026-11-13", "searches": 1})


class B05WithoutApply(Base):
    def test_nothing_is_called_or_written(self):
        self.w.put_budget("2026-11-13", 4)
        before = self.w.files()
        code, out, err = self.w.run(apply=False)
        self.assertEqual((code, out, err),
                         (0, "read harbour-tales youtube: would search, 4 of 100 searches spent on 2026-11-13\n", ""))
        self.assertEqual(self.w.api.requests, [])
        self.assertEqual(self.w.files(), before)


class B06Key(Base):
    def test_a_plain_key_is_the_key_parameter(self):
        code, out, err = self.w.run()
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.w.api.requests), len(CLEAN))
        for request in self.w.api.requests:
            self.assertEqual(request["params"].get("key"), SEED, request["path"])
            self.assertNotIn("authorization", request["headers"])
        self.assertNotIn(SEED, out + err)
        for rel, data in self.w.files().items():
            self.assertNotIn(SEED.encode(), data, rel)

    def test_a_grant_is_the_bearer_header(self):
        self.w.write_key(json.dumps({"access_token": SEED, "token_type": "Bearer"}) + "\n")
        code, out, err = self.w.run()
        self.assertEqual(code, 0, err)
        for request in self.w.api.requests:
            self.assertNotIn("key", request["params"])
            self.assertEqual(request["headers"].get("authorization"), f"Bearer {SEED}")
        self.assertEqual((self.w.state / STORED).read_bytes(), (FIX / "expected" / "20261113T090000Z.json").read_bytes())
        self.assertNotIn(SEED, out + err)
        for rel, data in self.w.files().items():
            self.assertNotIn(SEED.encode(), data, rel)

    def test_a_key_readable_by_others(self):
        os.chmod(self.w.key, 0o644)
        self.refused("run: resource external.youtube is not usable: secret external.youtube: readable by others")

    def test_no_secrets_dir(self):
        self.w.write_settings(SECRETS_DIR=None)
        self.refused("run: resource external.youtube is not usable: setting SECRETS_DIR is empty")

    def test_two_words_are_not_a_key(self):
        self.w.write_key(f"{SEED} {SEED}\n")
        self.failed("the key file is not a key or a grant")
        self.assertEqual(self.w.api.requests, [])
        self.assertEqual(self.w.budget(), {"day": "2026-11-13", "searches": 1})


class B07PlatformAnswers(Base):
    def test_quota_is_down(self):
        self.w.error("search", 403, "quotaExceeded")
        self.down()

    def test_a_closed_port_is_down(self):
        self.w.write_settings(YOUTUBE_API_URL=f"http://127.0.0.1:{free_port()}")
        self.down()

    def test_forbidden_is_refused(self):
        self.w.error("search", 403, "forbidden")
        self.failed("refused by the platform")

    def test_500_is_an_error(self):
        self.w.error("search", 500)
        self.failed("the platform answered with an error")

    def test_not_json_is_a_bad_answer(self):
        self.w.answer("search", 0, lambda entry: entry.update(body="not json"))
        self.failed("bad answer")

    def test_a_count_that_is_not_a_number_is_a_bad_answer(self):
        self.w.stats("yt-a", viewCount="12k")
        self.failed("bad answer")

    def test_a_proxy_changes_nothing(self):
        with mock.patch.dict(os.environ, {"http_proxy": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1"}):
            code, out, err = self.w.run()
        self.assertEqual((code, out, err), (0, READ_LINE.format(1, 100) + WROTE, ""))


class B08ArgumentsAndTopic(Base):
    def test_neither_a_job_nor_read(self):
        self.refused("run: give a job file or --read <topic> <platform>", read=None)

    def test_read_with_a_pull(self):
        self.refused("run: --read takes no job file and no --pull", "--pull", "3")

    def test_read_with_a_job(self):
        self.refused("run: --read takes no job file and no --pull", "job.json")

    def test_topic_and_platform(self):
        for read, line in ((("Harbour", "youtube"), "run: topic must match [a-z0-9][a-z0-9-]*"),
                           ((TOPIC, "vimeo"), "run: platform must be one of youtube, tiktok, instagram"),
                           ((TOPIC, "tiktok"), "run: no read tool for tiktok")):
            with self.subTest(read=read):
                self.refused(line, read=read)

    def test_the_topic_file_only_in_the_working_tree(self):
        (self.w.root / "research/topics/quay-songs.json").write_text(
            '{"youtube": {"words": ["Wickabel"], "region": "GB", "days": 7}}\n')
        self.refused("run: research/topics/quay-songs.json is not on trunk", read=("quay-songs", "youtube"))

    def test_no_youtube_key(self):
        for doc in ({"tiktok": {}}, [1], "not json\n"):
            with self.subTest(doc=doc):
                self.w.commit_topic(doc)
                self.refused("run: research/topics/harbour-tales.json does not watch youtube")

    def test_a_bad_watch(self):
        line = "run: research/topics/harbour-tales.json: youtube is not {words, region, days}"
        for fields in ({"region": "gb"}, {"days": 0}, {"days": True}, {"days": 366}, {"words": []},
                       {"words": [""]}, {"words": "Wickabel"}, {"region": ABSENT}, {"extra": 1}):
            with self.subTest(fields=fields):
                self.w.watch(**fields)
                self.refused(line)


def fake_tool(**names):
    fields = {"PLATFORM": "youtube", "RESOURCE": "external.youtube", "REASONS": ("bad answer",),
              "API": "https://www.googleapis.com/youtube/v3", "BUDGET": ("YOUTUBE_SEARCHES_PER_DAY", 100),
              "read": lambda call: {"videos": []}}
    fields.update(names)
    return types.SimpleNamespace(**{k: v for k, v in fields.items() if v is not ABSENT})


class B09Interface(Base):
    def run_with(self, tool):
        with mock.patch.object(reads, "load_tool", return_value=tool):
            return self.w.run()

    def raises(self, error):
        def read(call):
            print(SEED)
            print(SEED, file=sys.stderr)
            raise error
        return read

    def test_a_crash(self):
        code, out, err = self.run_with(fake_tool(read=self.raises(RuntimeError(SEED))))
        self.assertEqual((code, out, err), (1, "", "run: read harbour-tales youtube failed: tool crashed\n"))
        self.assertFalse((self.w.state / STORED).exists())

    def test_a_video_without_a_link(self):
        code, out, err = self.run_with(fake_tool(read=lambda call: {"videos": [{"id": "yt-a"}]}))
        self.assertEqual((code, out, err), (1, "", "run: read harbour-tales youtube failed: tool broke the interface\n"))
        self.assertFalse((self.w.state / STORED).exists())

    def test_returns_that_break_the_interface(self):
        for returned in ({"videos": [], "more": 1}, {"videos": {}}, [], None,
                         {"videos": [{"id": "yt-a", "link": "l", "views": -1}]}):
            with self.subTest(returned=returned):
                code, out, err = self.run_with(fake_tool(read=lambda call: returned))
                self.assertEqual((code, err), (1, "run: read harbour-tales youtube failed: tool broke the interface\n"))

    def test_a_reason_outside_reasons(self):
        code, out, err = self.run_with(fake_tool(read=self.raises(Failed("other"))))
        self.assertEqual((code, out, err), (1, "", "run: read harbour-tales youtube failed: tool failed\n"))

    def test_no_budget(self):
        code, out, err = self.run_with(fake_tool(BUDGET=ABSENT))
        self.assertEqual((code, out, err), (2, "", "run: read tool youtube does not follow the read interface\n"))
        self.assertFalse(self.w.state.exists())

    def test_the_call(self):
        calls = []
        self.assertEqual(self.run_with(fake_tool(read=lambda call: calls.append(call) or {"videos": []}))[0], 0)
        self.assertEqual(calls, [{"api": self.w.api.url, "read_at": NOW,
                                  "secrets": {"external.youtube": str(self.w.key)},
                                  "topic": {"words": ["Skarrowfen", "Wickabel"], "region": "GB", "days": 7}}])


class B10Url(Base):
    def test_a_url_that_is_not_an_api(self):
        for url in ("http://example.com/v3", "ftp://127.0.0.1/v3"):
            with self.subTest(url=url):
                self.w.write_settings(YOUTUBE_API_URL=url)
                self.refused("run: setting YOUTUBE_API_URL is not an API URL")
                self.assertIsNone(self.w.budget())


class B11SameSecond(Base):
    def test_the_stamp_is_stored(self):
        self.w.put_read(STORED)
        self.w.put_budget("2026-11-13", 7)
        self.refused("run: reads/harbour-tales/youtube/20261113T090000Z.json is already stored", stored=True)
        self.assertEqual(self.w.budget(), {"day": "2026-11-13", "searches": 7})
        self.assertEqual((self.w.state / STORED).read_text(), "{}\n")


class B12JobPath(unittest.TestCase):
    def test_help_names_read(self):
        proc = subprocess.run([sys.executable, str(ROOT / "kit/lib/cmd_run.py"), "--help"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("--read TOPIC PLATFORM", proc.stdout)

    def test_reads_is_not_a_job_tool(self):
        with self.assertRaises(cmd_run.Refused) as caught:
            cmd_run._load_tool("reads")
        self.assertEqual(str(caught.exception), "tool reads is not installed")


class B13Register(unittest.TestCase):
    def test_the_row(self):
        register, problems = resources.load(resources.REGISTER)
        self.assertEqual(problems, [])
        row = next(r for r in register["resources"] if r["id"] == "external.youtube")
        self.assertEqual(row["checks"], [{"kind": "secret", "name": "external.youtube"}])


if __name__ == "__main__":
    unittest.main()
