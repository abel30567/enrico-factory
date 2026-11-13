"""Tests for `factory run --read <topic> tiktok`: kit/lib/tools/reads/tiktok.py on the read path of kit/lib/reads.py.

The world copies tests/fixtures/read_tiktok/: `production/` is committed on `trunk`, and `answers/` is what a
fake TikTok Discovery API answers. Each answer file names the API reference page it was written from and the
day it was read: `{"source", "read_on", "status", "body"}`, and may name extra `headers` of the answer. The
fake API is one `ThreadingHTTPServer` on 127.0.0.1, port 0: a `GET /discovery/<endpoint>/?<query>` gets
`answers/<endpoint>.json`, a `video_list` answer holding only the ids the request asks for; any other path
gets a 404. Every request is logged with its path, query and `Access-Token`. `production.local.env` sets
`STATE_DIR`, `SECRETS_DIR`, `TIKTOK_API_URL`, `TIKTOK_ADVERTISER_ID` and `TIKTOK_COUNTRY`; the key file holds a
seeded token, mode 0600; `FACTORY_NOW` is 2026-11-12T09:30:00Z. Nothing reaches a host other than 127.0.0.1.

Recorded: `trending_list` gives skarrowfen (7003, rank 1), harbourtales (7001, rank 3) and lighthousekeeper
(7002, rank 40), each with views and posts; `video_list` gives 7001 the videos …001, …002, …003 and 7002 the
videos …004, …001. The topic watches harbourtales, #LighthouseKeeper and fogbell, which is not rising. Each
seeded defect is one change to the world.
"""

import contextlib
import http.server
import importlib.util
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

from kit.lib import cmd_run, cmd_trends, reads, trends  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "read_tiktok"
TOOL = ROOT / "kit" / "lib" / "tools" / "reads" / "tiktok.py"
NOW = "2026-11-12T09:30:00Z"
TOPIC = "harbour-tales"
SEED = "seeded-token-tiktok"
STORED = "reads/harbour-tales/tiktok/20261112T093000Z.json"
BUDGET = "read-budget/tiktok.json"
TOPIC_FILE = "research/topics/harbour-tales.json"
WROTE = "wrote reads/harbour-tales/tiktok/20261112T093000Z.json\n"
QUERY = {"advertiser_id": "1234567890", "country_code": "GB", "date_range": "7DAY", "discovery_type": "HASHTAG"}
VIDEO = "73110000000000000{:02d}"


def read_line(videos, search=1):
    return f"read harbour-tales tiktok at {NOW}: {videos} videos, search {search} of 100 on 2026-11-12\n"


def free_port():
    """A loopback port nothing listens on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class FakeApi:
    """The TikTok Discovery API on 127.0.0.1, answering from the answer files in ``answers``."""

    def __init__(self, test, answers):
        self.answers, self.requests, self.only_asked = answers, [], True
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
        self.requests.append({"path": path, "query": query, "token": h.headers.get("Access-Token")})
        parts = path.split("/")
        file = self.answers / f"{parts[2]}.json" if len(parts) == 4 and parts[1] == "discovery" else None
        if file is None or parts[3] != "" or not file.is_file():
            status, headers, body = 404, {}, {"code": 40400, "message": "no such path"}
        else:
            answer = json.loads(file.read_text())
            status, headers, body = answer["status"], answer.get("headers", {}), answer["body"]
            items = body.get("data", {}).get("list") if isinstance(body, dict) else None
            if parts[2] == "video_list" and self.only_asked and isinstance(items, list):
                asked = json.loads(params.get("hashtag_ids", "[]"))
                body["data"]["list"] = [i for i in items if i.get("hashtag_id") in asked]
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        h.send_response(status)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(data)))
        for key, value in headers.items():
            h.send_header(key, value.format(api=self.url))
        h.end_headers()
        h.wfile.write(data)


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def video(n):
    vid = VIDEO.format(n)
    return {"video_id": vid, "share_url": f"https://www.tiktok.com/video/{vid}",
            "embed_url": f"https://www.tiktok.com/embed/v2/{vid}"}


def stored_video(n, order):
    return {"id": VIDEO.format(n), "link": f"https://www.tiktok.com/video/{VIDEO.format(n)}", "order": order}


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
        self.key = self.secrets / "external.tiktok"
        self.write_key(SEED + "\n")
        self.api = FakeApi(test, self.answers)
        self.settings = {"STATE_DIR": str(self.state), "SECRETS_DIR": str(self.secrets),
                         "TIKTOK_API_URL": self.api.url, "TIKTOK_ADVERTISER_ID": "1234567890",
                         "TIKTOK_COUNTRY": "GB"}
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

    def answer(self, endpoint, change):
        path = self.answers / f"{endpoint}.json"
        doc = json.loads(path.read_text())
        change(doc)
        path.write_text(json.dumps(doc, indent=1) + "\n")

    def items(self, endpoint, items):
        def change(doc):
            doc["body"]["data"]["list"] = items
        self.answer(endpoint, change)

    def commit_topic(self, doc):
        path = self.root / TOPIC_FILE
        path.write_text(doc if isinstance(doc, str) else json.dumps(doc) + "\n")
        git(self.root, "add", TOPIC_FILE)
        git(self.root, "commit", "-q", "-m", "topic")

    def hashtags(self, value):
        self.commit_topic({"topic": TOPIC, "tiktok": {"hashtags": value}})

    def put_read(self, rel, text="{}\n"):
        path = self.state / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    # -- the command and what is there

    def run(self, apply=True):
        args = ["--read", TOPIC, "tiktok", "--root", str(self.root)] + (["--apply"] if apply else [])
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_run.main(args)
        return code, out.getvalue(), err.getvalue()

    def budget(self):
        path = self.state / BUDGET
        return json.loads(path.read_text()) if path.exists() else None

    def stored(self):
        return json.loads((self.state / STORED).read_text())

    def files(self):
        """Every file under STATE_DIR with its bytes."""
        if not self.state.exists():
            return {}
        return {str(p.relative_to(self.state)): p.read_bytes() for p in sorted(self.state.rglob("*")) if p.is_file()}

    def paths(self):
        return [r["path"] for r in self.api.requests]

    def asked(self):
        """The ``hashtag_ids`` of each `video_list` request."""
        return [json.loads(dict(urllib.parse.parse_qsl(r["query"]))["hashtag_ids"])
                for r in self.api.requests if r["path"] == "/discovery/video_list/"]


def tool_copy(**names):
    """A fresh copy of the TikTok read tool as a module, with ``names`` replaced."""
    spec = importlib.util.spec_from_file_location("tiktok_copy", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for key, value in names.items():
        setattr(module, key, value)
    return module


class Base(unittest.TestCase):
    def setUp(self):
        self.w = World(self)

    def refused(self, line):
        """``factory run`` refuses with ``line`` on stderr and exit 2, makes no request and stores no read."""
        self.assertEqual(self.w.run(), (2, "", line + "\n"))
        self.assertEqual(self.w.api.requests, [])
        self.assertFalse((self.w.state / STORED).exists())

    def failed(self, reason):
        """The read fails with ``reason``, exit 1: nothing is stored and its search is spent."""
        self.assertEqual(self.w.run(), (1, "", f"run: read harbour-tales tiktok failed: {reason}\n"))
        self.assertFalse((self.w.state / STORED).exists())
        self.assertEqual(self.w.budget(), {"day": "2026-11-12", "searches": 1})

    def down(self):
        self.assertEqual(self.w.run(), (2, "", "run: resource external.tiktok is down\n"))
        self.assertFalse((self.w.state / STORED).exists())


class B01ReadIsStored(Base):
    def test_without_apply(self):
        self.assertEqual(self.w.run(apply=False),
                         (0, "read harbour-tales tiktok: would search, 0 of 100 searches spent on 2026-11-12\n", ""))
        self.assertEqual(self.w.api.requests, [])
        self.assertEqual(self.w.files(), {})

    def test_with_apply(self):
        self.assertEqual(self.w.run(), (0, read_line(4) + WROTE, ""))
        self.assertEqual(sorted(self.w.files()), [BUDGET, STORED])
        self.assertEqual((self.w.state / STORED).read_bytes(), (FIX / "expected" / "20261112T093000Z.json").read_bytes())
        doc = self.w.stored()
        self.assertEqual(doc["videos"], [stored_video(1, 1), stored_video(4, 2), stored_video(2, 3), stored_video(3, 4)])
        self.assertEqual(doc["hashtags"], [{"hashtag_id": "7001", "name": "harbourtales", "rank_position": 3},
                                           {"hashtag_id": "7002", "name": "lighthousekeeper", "rank_position": 40},
                                           {"hashtag_id": None, "name": "fogbell", "rank_position": None}])
        self.assertEqual(self.w.budget(), {"day": "2026-11-12", "searches": 1})

    def test_a_repeated_hashtag_is_kept_once(self):
        self.w.hashtags(["harbourtales", "#LighthouseKeeper", "#HarbourTales", "fogbell", "lighthousekeeper"])
        self.assertEqual(self.w.run()[0], 0)
        self.assertEqual([h["name"] for h in self.w.stored()["hashtags"]], ["harbourtales", "lighthousekeeper", "fogbell"])
        self.assertEqual(self.w.asked(), [["7001", "7002"]])

    def test_the_link_falls_back_to_the_embed_url(self):
        items = json.loads((FIX / "answers" / "video_list.json").read_text())["body"]["data"]["list"]
        del items[0]["top_video_list"][1]["share_url"]
        items[0]["top_video_list"][2]["share_url"] = "a link with spaces"
        items[1]["top_video_list"][0] = {"video_id": "no-link"}
        items[1]["top_video_list"].insert(0, "not a video")
        self.w.items("video_list", items)
        self.assertEqual(self.w.run()[0], 0)
        self.assertEqual(self.w.stored()["videos"], [
            stored_video(1, 1),
            {"id": VIDEO.format(2), "link": f"https://www.tiktok.com/embed/v2/{VIDEO.format(2)}", "order": 2},
            {"id": VIDEO.format(3), "link": f"https://www.tiktok.com/embed/v2/{VIDEO.format(3)}", "order": 3}])

    def test_a_rank_that_is_not_whole_is_null(self):
        items = json.loads((FIX / "answers" / "trending_list.json").read_text())["body"]["data"]["list"]
        items[1]["rank_position"] = "3"
        items[2]["rank_position"] = True
        self.w.items("trending_list", items)
        self.assertEqual(self.w.run()[0], 0)
        self.assertEqual([h["rank_position"] for h in self.w.stored()["hashtags"]], [None, None, None])
        self.assertEqual([h["hashtag_id"] for h in self.w.stored()["hashtags"]], ["7001", "7002", None])


class B02TrendsRanking(Base):
    def test_platform_order_ranks_the_read(self):
        self.assertEqual(self.w.run()[0], 0)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_trends.main([TOPIC, "tiktok", "--root", str(self.w.root)])
        lines = [f"trends harbour-tales tiktok: read {NOW}, 4 videos, 0 under the view floor 1000"]
        lines += [f"platform-order {rank} {VIDEO.format(n)} {rank}" for rank, n in enumerate((1, 4, 2, 3), 1)]
        lines += [f"skip {name}:unknown: 4 of 4 videos lack {what}" for name, what in trends.RANKINGS[:6]]
        self.assertEqual((code, out.getvalue(), err.getvalue()), (0, "\n".join(lines) + "\n", ""))


class B03Calls(Base):
    def test_two_gets(self):
        code, out, err = self.w.run()
        self.assertEqual(code, 0)
        trending = urllib.parse.urlencode(sorted(QUERY.items()))
        self.assertEqual(self.w.api.requests, [
            {"path": "/discovery/trending_list/", "query": trending, "token": SEED},
            {"path": "/discovery/video_list/", "query": trending + "&hashtag_ids=%5B%227001%22%2C%227002%22%5D",
             "token": SEED}])
        self.assertNotIn("category_name", trending)
        self.assertNotIn(SEED, out + err)
        for rel, data in self.w.files().items():
            self.assertNotIn(SEED.encode(), data, rel)

    def test_the_settings_are_sent(self):
        self.w.write_settings(TIKTOK_ADVERTISER_ID="77", TIKTOK_COUNTRY="NO")
        self.assertEqual(self.w.run()[0], 0)
        for request in self.w.api.requests:
            params = dict(urllib.parse.parse_qsl(request["query"]))
            self.assertEqual((params["advertiser_id"], params["country_code"]), ("77", "NO"))

    def test_the_token_is_the_first_line_stripped(self):
        self.w.write_key("  other-token \nsecond line\n")
        self.assertEqual(self.w.run()[0], 0)
        self.assertEqual([r["token"] for r in self.w.api.requests], ["other-token", "other-token"])


class B04NoCountIsKept(Base):
    def test_no_count_of_an_answer_is_stored(self):
        self.assertEqual(self.w.run()[0], 0)
        text = (self.w.state / STORED).read_text()
        items = json.loads((FIX / "answers" / "trending_list.json").read_text())["body"]["data"]["list"]
        for item in items:
            for key in ("views", "posts"):
                self.assertNotIn(str(item[key]), text)
        self.assertNotIn("views", text)
        self.assertNotIn("posts", text)
        self.assertNotIn("skarrowfen", text)
        self.assertNotIn("7003", text)
        self.assertIn('"rank_position": 3', text)
        for stored in self.w.stored()["videos"]:
            self.assertEqual(sorted(stored), ["id", "link", "order"])


class B05Order(Base):
    def test_swapped_hashtags(self):
        self.w.hashtags(["#LighthouseKeeper", "harbourtales", "fogbell"])
        self.assertEqual(self.w.run()[0], 0)
        self.assertEqual(self.w.stored()["videos"],
                         [stored_video(4, 1), stored_video(1, 2), stored_video(2, 3), stored_video(3, 4)])
        self.assertEqual(self.w.asked(), [["7002", "7001"]])


class B06NotRising(Base):
    def test_one_get_and_no_videos(self):
        self.w.hashtags(["fogbell"])
        self.assertEqual(self.w.run(), (0, read_line(0) + WROTE, ""))
        self.assertEqual(self.w.paths(), ["/discovery/trending_list/"])
        doc = self.w.stored()
        self.assertEqual(doc["videos"], [])
        self.assertEqual(doc["hashtags"], [{"hashtag_id": None, "name": "fogbell", "rank_position": None}])


class B07Chunks(Base):
    def test_eleven_hashtags_make_two_calls(self):
        names = [f"tide{n:02d}" for n in range(1, 12)]
        ids = [str(8000 + n) for n in range(1, 12)]
        self.w.items("trending_list", [{"hashtag_id": i, "hashtag_name": n, "rank_position": k}
                                       for k, (i, n) in enumerate(zip(ids, names), 1)])
        self.w.items("video_list", [{"hashtag_id": i, "top_video_list": [video(k)]} for k, i in enumerate(ids, 1)])
        self.w.hashtags(names)
        self.assertEqual(self.w.run(), (0, read_line(11) + WROTE, ""))
        self.assertEqual(self.w.asked(), [ids[:10], ids[10:]])
        self.assertEqual([v["id"] for v in self.w.stored()["videos"]], [VIDEO.format(k) for k in range(1, 12)])


class B08Reasons(Base):
    def test_the_reasons_are_the_rulings(self):
        self.assertEqual(reads.load_tool("tiktok").REASONS, (
            "a setting of external.tiktok is not valid", "tiktok answered with an error code",
            "tiktok answered something that is not a discovery list"))

    def test_a_country_that_is_not_two_capitals(self):
        self.w.write_settings(TIKTOK_COUNTRY="gb")
        self.failed("a setting of external.tiktok is not valid")
        self.assertEqual(self.w.api.requests, [])

    def test_an_advertiser_that_is_not_digits(self):
        self.w.write_settings(TIKTOK_ADVERTISER_ID="12a")
        self.failed("a setting of external.tiktok is not valid")
        self.assertEqual(self.w.api.requests, [])

    def test_http_400(self):
        self.w.answer("trending_list", lambda doc: doc.update(status=400))
        self.failed("tiktok answered with an error code")

    def test_an_error_code(self):
        self.w.answer("video_list", lambda doc: doc["body"].update(code=40001, message="refused"))
        self.failed("tiktok answered with an error code")

    def test_a_body_that_is_not_json(self):
        self.w.answer("trending_list", lambda doc: doc.update(body="<html>not json</html>"))
        self.failed("tiktok answered something that is not a discovery list")

    def test_no_data_list(self):
        self.w.answer("trending_list", lambda doc: doc["body"]["data"].pop("list"))
        self.failed("tiktok answered something that is not a discovery list")

    def test_a_video_list_item_not_asked(self):
        def change(doc):
            doc["body"]["data"]["list"].append({"hashtag_id": "7003", "top_video_list": [video(5)]})
        self.w.answer("video_list", change)
        self.w.api.only_asked = False
        self.failed("tiktok answered something that is not a discovery list")

    def test_items_that_are_not_discovery_items(self):
        cases = (("trending_list", ["7001"]), ("trending_list", [{"hashtag_id": 7001, "hashtag_name": "harbourtales"}]),
                 ("trending_list", [{"hashtag_id": "7001"}]), ("video_list", [{"hashtag_id": "7001"}]),
                 ("video_list", [{"hashtag_id": "7001", "top_video_list": {"video_id": "x"}}]))
        for endpoint, items in cases:
            with self.subTest(endpoint=endpoint, items=items):
                shutil.rmtree(self.w.answers)
                shutil.copytree(FIX / "answers", self.w.answers)
                shutil.rmtree(self.w.state, ignore_errors=True)
                self.w.items(endpoint, items)
                self.failed("tiktok answered something that is not a discovery list")


class B09Down(Base):
    def test_http_401(self):
        self.w.answer("trending_list", lambda doc: doc.update(status=401))
        self.down()

    def test_http_500(self):
        self.w.answer("video_list", lambda doc: doc.update(status=500))
        self.down()

    def test_a_key_file_of_only_a_newline(self):
        self.w.write_key("\n")
        self.down()
        self.assertEqual(self.w.api.requests, [])

    def test_a_closed_port(self):
        self.w.write_settings(TIKTOK_API_URL=f"http://127.0.0.1:{free_port()}")
        self.down()


class B10NoRedirect(Base):
    def test_a_302_is_not_followed(self):
        self.w.answer("trending_list", lambda doc: doc.update(
            status=302, headers={"Location": "{api}/discovery/video_list/"}))
        self.failed("tiktok answered with an error code")
        self.assertEqual(self.w.paths(), ["/discovery/trending_list/"])


class B11PathRefusals(Base):
    WATCH = "run: research/topics/harbour-tales.json: tiktok is not {hashtags}"

    def test_bad_hashtags(self):
        for value in ("harbourtales", ["two words"], [], ["#"], ["##fog"], ["harbourtales", 7]):
            with self.subTest(value=value):
                self.w.hashtags(value)
                self.refused(self.WATCH)

    def test_another_key_beside_hashtags(self):
        self.w.commit_topic({"tiktok": {"hashtags": ["harbourtales"], "words": ["fog"]}})
        self.refused(self.WATCH)

    def test_a_topic_that_does_not_watch_tiktok(self):
        self.w.commit_topic({"topic": TOPIC, "youtube": {"words": ["fog"], "region": "GB", "days": 7}})
        self.refused("run: research/topics/harbour-tales.json does not watch tiktok")

    def test_an_api_url_that_is_not_an_api(self):
        self.w.write_settings(TIKTOK_API_URL="http://example.invalid")
        self.refused("run: setting TIKTOK_API_URL is not an API URL")

    def test_no_key_file(self):
        self.w.key.unlink()
        self.refused("run: resource external.tiktok is not usable: secret external.tiktok: missing")

    def test_an_empty_key_file(self):
        self.w.write_key("")
        self.refused("run: resource external.tiktok is not usable: secret external.tiktok: empty")

    def test_no_country(self):
        self.w.write_settings(TIKTOK_COUNTRY=None)
        self.refused("run: resource external.tiktok is not usable: setting TIKTOK_COUNTRY is empty")

    def test_a_tool_without_platform(self):
        tools = self.w.tmp / "tools"
        tools.mkdir()
        text = TOOL.read_text()
        self.assertIn('PLATFORM = "tiktok"\n', text)
        (tools / "tiktok.py").write_text(text.replace('PLATFORM = "tiktok"\n', ""))
        with mock.patch.object(reads, "TOOLS_DIR", tools):
            self.refused("run: read tool tiktok does not follow the read interface")

    def test_the_read_is_already_stored(self):
        self.w.put_read(STORED)
        self.assertEqual(self.w.run(), (2, "", f"run: {STORED} is already stored\n"))
        self.assertEqual(self.w.api.requests, [])
        self.assertEqual((self.w.state / STORED).read_text(), "{}\n")


class B12Interface(Base):
    def test_returns_that_break_the_interface(self):
        for returned in ({"videos": [{"id": "a"}], "hashtags": []}, {"videos": [], "hashtags": {1, 2}}, {"videos": []}):
            with self.subTest(returned=returned):
                tool = tool_copy(read=lambda call, r=returned: r)
                self.assertIsInstance(tool, types.ModuleType)
                with mock.patch.object(reads, "load_tool", return_value=tool):
                    self.assertEqual(self.w.run(),
                                     (1, "", "run: read harbour-tales tiktok failed: tool broke the interface\n"))
                self.assertFalse((self.w.state / STORED).exists())

    def test_the_tool_follows_the_interface(self):
        tool = reads.load_tool("tiktok")
        self.assertTrue(reads._follows(tool, "tiktok"))
        self.assertEqual((tool.PLATFORM, tool.RESOURCE, tool.API, tool.BUDGET, tool.STORED, tool.DATE_RANGE,
                          tool.PER_CALL), ("tiktok", "external.tiktok", "https://business-api.tiktok.com/open_api/v1.3",
                                           ("TIKTOK_SEARCHES_PER_DAY", 100), ("hashtags",), "7DAY", 10))
        self.assertEqual(sorted(tool.WATCH), ["hashtags"])
        self.assertIs(tool.WATCH["hashtags"](["#Fog", "bell"]), True)


if __name__ == "__main__":
    unittest.main()
