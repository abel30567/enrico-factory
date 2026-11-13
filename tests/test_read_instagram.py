"""Tests for `factory run --read <topic> instagram`: kit/lib/tools/reads/instagram.py on the read path.

The world copies tests/fixtures/read_instagram/: `production/` is committed on `trunk`, and `answers/` is what a
fake Graph API answers. Each answer file is `{"reference", "read_on", "status", "answer"}`, written by hand from
the reference page it names on the day it names. The fake API is one `ThreadingHTTPServer` on 127.0.0.1, port 0:
a `GET /v27.0/<user>?...` gets the answer of `answers/<account>.json`, the account taken from `fields`, with
its status, else a 404, and every request is logged. `production.local.env` sets `STATE_DIR`, `SECRETS_DIR`,
`INSTAGRAM_API_URL` and `INSTAGRAM_USER_ID`; the key file holds a fixture token, mode 0600; `FACTORY_NOW` is
2026-11-13T09:00:00Z. Nothing reaches a host other than 127.0.0.1.

Recorded: skarrowfen has 12000 followers and four media, newest first: Reels …001 (48000, 2400, 300) and
…002 (9000, likes hidden, 90), the FEED post …003, and the Reel …004 (500, 100, 10); wickabel has 800
followers and the Reel …011 (2000, 100, 20). The topic lists wickabel before skarrowfen. Each seeded defect
is one change to the world.
"""

import contextlib
import http.server
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kit.lib import cmd_run, reads, resources, trends  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "read_instagram"
NOW = "2026-11-13T09:00:00Z"
TOPIC = "harbour-tales"
TOKEN = "fixture-token-not-real"
USER = "17841400000000001"
STORED = "reads/harbour-tales/instagram/20261113T090000Z.json"
BUDGET = "read-budget/instagram.json"
TOPIC_FILE = "research/topics/harbour-tales.json"
READ_LINE = "read harbour-tales instagram at 2026-11-13T09:00:00Z: {} videos, search 1 of 24 on 2026-11-13\n"
WROTE = "wrote reads/harbour-tales/instagram/20261113T090000Z.json\n"
WATCH_LINE = "run: research/topics/harbour-tales.json: instagram is not {accounts, hashtags}\n"
FIELDS = ("business_discovery.username({}){{username,followers_count,media.limit(20)"
          "{{id,media_product_type,permalink,timestamp,view_count,like_count,comments_count}}}}")
ACCOUNT = re.compile(r"business_discovery\.username\(([^)]*)\)")
REASONS = ("INSTAGRAM_USER_ID is not a number", "the key file is not a token", "token rejected", "rate limited",
           "unexpected answer")
ID = "1800000000000000{}".format
ABSENT = object()


def free_port():
    """A loopback port nothing listens on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class FakeApi:
    """The Graph API on 127.0.0.1, answering from the answer files in ``answers``."""

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
        self.requests.append({"path": path, "query": query})
        found = ACCOUNT.search(params.get("fields", ""))
        file = self.answers / f"{found.group(1)}.json" if found and "/" not in found.group(1) else None
        if path.startswith("/v27.0/") and file is not None and file.is_file():
            doc = json.loads(file.read_text())
            status, body = doc["status"], doc["answer"]
        else:
            status, body = 404, {"error": {"code": 803, "message": "not found"}}
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
        self.key = self.secrets / "external.instagram"
        self.write_key(TOKEN + "\n")
        self.api = FakeApi(test, self.answers)
        self.settings = {"STATE_DIR": str(self.state), "SECRETS_DIR": str(self.secrets),
                         "INSTAGRAM_API_URL": self.api.url + "/v27.0", "INSTAGRAM_USER_ID": USER}
        self.write_settings()
        patcher = mock.patch.dict(os.environ, {"FACTORY_NOW": NOW})
        patcher.start()
        test.addCleanup(patcher.stop)

    # -- seeded defects

    def write_key(self, text):
        self.key.write_text(text)
        os.chmod(self.key, 0o600)

    def write_settings(self, **changes):
        self.settings.update(changes)
        lines = [f"{k}={v}\n" for k, v in self.settings.items()]
        (self.root / "production.local.env").write_text("".join(lines))

    def answer(self, account, status=None, answer=ABSENT, change=None):
        """Change the answer file of ``account``: its status, its whole answer, or ``change(answer)``."""
        path = self.answers / f"{account}.json"
        doc = json.loads(path.read_text())
        if status is not None:
            doc["status"] = status
        if answer is not ABSENT:
            doc["answer"] = answer
        if change is not None:
            change(doc["answer"])
        path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")

    def error(self, account, code, subcode=None):
        error = {"message": "refused", "type": "OAuthException", "code": code, "fbtrace_id": "AbCdEf0123"}
        if subcode is not None:
            error["error_subcode"] = subcode
        self.answer(account, 400, {"error": error})

    def media(self, account, vid, **fields):
        """Change the fields of the media ``vid`` in the answer of ``account``."""
        def change(answer):
            item = next(m for m in answer["business_discovery"]["media"]["data"] if m.get("id") == vid)
            for key, value in fields.items():
                if value is ABSENT:
                    item.pop(key, None)
                else:
                    item[key] = value
        self.answer(account, change=change)

    def watch(self, **fields):
        watch = {"accounts": ["wickabel", "skarrowfen"], "hashtags": ["harbourtales"]}
        for key, value in fields.items():
            if value is ABSENT:
                watch.pop(key)
            else:
                watch[key] = value
        (self.root / TOPIC_FILE).write_text(json.dumps({"topic": TOPIC, "instagram": watch}) + "\n")
        git(self.root, "add", TOPIC_FILE)
        git(self.root, "commit", "-q", "-m", "topic")

    # -- the command and what is there

    def run(self, apply=True):
        args = ["--read", TOPIC, "instagram", "--root", str(self.root)] + (["--apply"] if apply else [])
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_run.main(args)
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


class Base(unittest.TestCase):
    def setUp(self):
        self.w = World(self)

    def read(self, videos):
        """The read stores ``videos`` videos, exit 0 and an empty stderr; the stored file."""
        self.assertEqual(self.w.run(), (0, READ_LINE.format(videos) + WROTE, ""))
        return self.w.stored()

    def failed(self, reason, w=None):
        """The read fails with ``reason``, exit 1; nothing is stored and the search stays spent."""
        w = w or self.w
        self.assertEqual(w.run(), (1, "", f"run: read harbour-tales instagram failed: {reason}\n"))
        self.assertFalse((w.state / STORED).exists())
        self.assertEqual(w.budget(), {"day": "2026-11-13", "searches": 1})


class B01WatchListBecomesAStoredRead(Base):
    def test_without_apply_nothing_is_done(self):
        self.assertEqual(self.w.run(apply=False),
                         (0, "read harbour-tales instagram: would search, 0 of 24 searches spent on 2026-11-13\n", ""))
        self.assertEqual(self.w.api.requests, [])
        self.assertFalse(self.w.state.exists())

    def test_the_stored_read(self):
        doc = self.read(4)
        self.assertEqual((self.w.state / STORED).read_bytes(), (FIX / "expected" / "20261113T090000Z.json").read_bytes())
        self.assertEqual(doc["unreadable"], [])
        loaded = trends.load_reads(self.w.state / "reads" / TOPIC / "instagram", TOPIC, "instagram")
        self.assertEqual(loaded, [doc])
        self.assertEqual(sorted(self.w.files()), [BUDGET, STORED])

    def test_the_fields_of_a_video(self):
        self.read(4)
        self.assertEqual(self.w.video(ID(1)), {
            "account": "skarrowfen", "author_views": [9000, 500], "comments": 300, "followers": 12000,
            "id": ID(1), "likes": 2400, "link": f"https://www.instagram.com/reel/{ID(1)}/",
            "published_at": "2026-11-11T09:00:00Z", "views": 48000})
        self.assertEqual(self.w.video("18000000000000011")["published_at"], "2026-11-12T06:30:00Z")

    def test_a_timestamp_in_another_zone_is_converted_and_a_bad_one_is_null(self):
        self.w.media("skarrowfen", ID(1), timestamp="2026-11-11T11:00:00+0200")
        self.w.media("skarrowfen", ID(2), timestamp="yesterday")
        self.read(4)
        self.assertEqual(self.w.video(ID(1))["published_at"], "2026-11-11T09:00:00Z")
        self.assertIsNone(self.w.video(ID(2))["published_at"])


class B02NotProfessionalIsUnreadable(Base):
    def test_a_personal_account(self):
        shutil.copy(self.w.answers / "personal.json", self.w.answers / "wickabel.json")
        doc = self.read(3)
        self.assertEqual(doc["unreadable"], [{"account": "wickabel", "why": "not a professional account, or no such account"}])
        self.assertNotIn("wickabel", [v["account"] for v in doc["videos"]])

    def test_the_same_answer_with_status_200(self):
        shutil.copy(self.w.answers / "personal.json", self.w.answers / "wickabel.json")
        self.w.answer("wickabel", 200)
        self.assertEqual(self.read(3)["unreadable"][0]["account"], "wickabel")

    def test_twin_the_clean_read(self):
        doc = self.read(4)
        self.assertEqual(doc["unreadable"], [])
        self.assertIn("wickabel", [v["account"] for v in doc["videos"]])


class B03AgeGatedIsUnreadable(Base):
    WHY = [{"account": "wickabel", "why": "no data returned, as for an age-gated account"}]

    def test_no_business_discovery(self):
        self.w.answer("wickabel", 200, {"id": USER})
        self.assertEqual(self.read(3)["unreadable"], self.WHY)

    def test_business_discovery_null(self):
        self.w.answer("wickabel", 200, {"id": USER, "business_discovery": None})
        self.assertEqual(self.read(3)["unreadable"], self.WHY)

    def test_both_unreadable_sorted_by_account(self):
        self.w.answer("wickabel", 200, {"id": USER})
        shutil.copy(self.w.answers / "personal.json", self.w.answers / "skarrowfen.json")
        doc = self.w.run()
        self.assertEqual(doc, (0, READ_LINE.format(0) + WROTE, ""))
        self.assertEqual([u["account"] for u in self.w.stored()["unreadable"]], ["skarrowfen", "wickabel"])


class B04MissingIsNeverZero(Base):
    def test_hidden_likes_are_null(self):
        self.read(4)
        self.assertIsNone(self.w.video(ID(2))["likes"])
        self.assertEqual(self.w.video(ID(1))["likes"], 2400)

    def test_no_followers_count_is_null(self):
        self.w.answer("skarrowfen", change=lambda a: a["business_discovery"].pop("followers_count"))
        doc = self.read(4)
        self.assertEqual([v["followers"] for v in doc["videos"] if v["account"] == "skarrowfen"], [None] * 3)
        self.assertEqual(self.w.video("18000000000000011")["followers"], 800)

    def test_twin_followers(self):
        doc = self.read(4)
        self.assertEqual([v["followers"] for v in doc["videos"] if v["account"] == "skarrowfen"], [12000] * 3)

    def test_a_negative_view_count_is_null_and_left_out(self):
        self.w.media("skarrowfen", ID(4), view_count=-1)
        self.read(4)
        self.assertIsNone(self.w.video(ID(4))["views"])
        self.assertEqual(self.w.video(ID(1))["author_views"], [9000])
        self.assertEqual(self.w.video(ID(2))["author_views"], [48000])

    def test_twin_views(self):
        self.read(4)
        self.assertEqual(self.w.video(ID(4))["views"], 500)
        self.assertEqual(self.w.video(ID(2))["author_views"], [48000, 500])

    def test_other_counts_that_are_not_whole_numbers_are_null(self):
        self.w.media("skarrowfen", ID(1), comments_count=True, like_count="2400", view_count=4.5)
        self.read(4)
        video = self.w.video(ID(1))
        self.assertEqual((video["views"], video["likes"], video["comments"]), (None, None, None))
        self.assertEqual(self.w.video(ID(2))["author_views"], [500])


class B05OnlyReelsSortedNoRepeats(Base):
    def test_only_reels_in_order(self):
        doc = self.read(4)
        self.assertEqual([v["id"] for v in doc["videos"]], [ID(1), ID(2), ID(4), "18000000000000011"])
        self.assertNotIn(7000, [n for v in doc["videos"] for n in v["author_views"]])
        self.assertEqual(self.w.video(ID(1))["author_views"], [9000, 500])
        self.assertEqual(self.w.video("18000000000000011")["author_views"], [])

    def test_a_collaboration_is_stored_once(self):
        def change(answer):
            answer["business_discovery"]["media"]["data"].append({
                "id": ID(1), "media_product_type": "REELS", "permalink": f"https://www.instagram.com/reel/{ID(1)}/",
                "timestamp": "2026-11-11T09:00:00+0000", "view_count": 48100, "like_count": 2410,
                "comments_count": 301})
        self.w.answer("wickabel", change=change)
        doc = self.read(4)
        self.assertEqual([(v["id"], v["account"]) for v in doc["videos"]],
                         [(ID(1), "skarrowfen"), (ID(2), "skarrowfen"), (ID(4), "skarrowfen"),
                          ("18000000000000011", "wickabel")])
        self.assertEqual(self.w.video(ID(1))["views"], 48000)
        self.assertEqual(self.w.video("18000000000000011")["author_views"], [48100])

    def test_two_reads_are_byte_identical(self):
        other = World(self)
        self.assertEqual(self.w.run()[0], 0)
        self.assertEqual(other.run()[0], 0)
        self.assertEqual((self.w.state / STORED).read_bytes(), (other.state / STORED).read_bytes())


class B06TheRequest(Base):
    def test_one_request_per_account_sorted(self):
        self.read(4)
        self.assertEqual(self.w.api.requests, [
            {"path": f"/v27.0/{USER}",
             "query": urllib.parse.urlencode(sorted({"access_token": TOKEN, "fields": FIELDS.format(account)}.items()))}
            for account in ("skarrowfen", "wickabel")])
        params = dict(urllib.parse.parse_qsl(self.w.api.requests[0]["query"]))
        self.assertEqual(params, {"access_token": TOKEN, "fields": FIELDS.format("skarrowfen")})
        self.assertEqual(params["fields"], "business_discovery.username(skarrowfen){username,followers_count,"
                         "media.limit(20){id,media_product_type,permalink,timestamp,view_count,like_count,comments_count}}")


class B07EachFailure(Base):
    def defect(self, reason, seed):
        with self.subTest(reason=reason, seed=seed.__name__ if callable(seed) else seed):
            w = World(self)
            seed(w)
            self.failed(reason, w)

    def test_error_codes(self):
        for code, subcode, reason in ((190, None, "token rejected"), (102, None, "token rejected"),
                                      (4, None, "rate limited"), (17, None, "rate limited"),
                                      (100, None, "unexpected answer"), (110, 33, "unexpected answer")):
            self.defect(reason, lambda w: w.error("skarrowfen", code, subcode))

    def test_unexpected_answers(self):
        seeds = (
            lambda w: w.answer("skarrowfen", answer="not json"),
            lambda w: w.answer("skarrowfen", answer=[]),
            lambda w: w.answer("skarrowfen", 500, {}),
            lambda w: w.answer("skarrowfen", answer={"id": USER, "business_discovery": "x"}),
            lambda w: w.answer("skarrowfen", change=lambda a: a["business_discovery"].update(media=[])),
            lambda w: w.media("skarrowfen", ID(1), id=ABSENT),
            lambda w: w.media("skarrowfen", ID(2), permalink="https://www.instagram.com/reel/ x/"),
        )
        for seed in seeds:
            self.defect("unexpected answer", seed)

    def test_no_media_is_no_reels(self):
        self.w.answer("wickabel", change=lambda a: a["business_discovery"].pop("media"))
        self.assertEqual([v["account"] for v in self.read(3)["videos"]], ["skarrowfen"] * 3)

    def test_a_user_id_that_is_not_a_number(self):
        self.w.write_settings(INSTAGRAM_USER_ID="me")
        self.failed("INSTAGRAM_USER_ID is not a number")
        self.assertEqual(self.w.api.requests, [])

    def test_a_key_file_that_is_not_a_token(self):
        self.w.write_key("two words\n")
        self.failed("the key file is not a token")
        self.assertEqual(self.w.api.requests, [])

    def test_a_failure_on_the_second_account_stores_nothing(self):
        self.w.error("wickabel", 190)
        self.failed("token rejected")
        self.assertEqual(len(self.w.api.requests), 2)
        self.assertEqual(sorted(self.w.files()), [BUDGET])


class B08Down(Base):
    def test_nothing_listens(self):
        self.w.write_settings(INSTAGRAM_API_URL=f"http://127.0.0.1:{free_port()}/v27.0")
        self.assertEqual(self.w.run(), (2, "", "run: resource external.instagram is down\n"))
        self.assertFalse((self.w.state / STORED).exists())


class B09NoTokenAnywhere(Base):
    def check(self, got):
        self.assertNotIn(TOKEN, got[1] + got[2])
        for rel, data in self.w.files().items():
            self.assertNotIn(TOKEN.encode(), data, rel)

    def test_the_clean_read(self):
        got = self.w.run()
        self.assertEqual(got[0], 0)
        self.check(got)

    def test_a_rejected_token(self):
        self.w.error("skarrowfen", 190)
        got = self.w.run()
        self.assertEqual(got[0], 1)
        self.check(got)


class B10TheWatch(Base):
    def test_each_bad_watch_is_refused(self):
        seeds = ({"accounts": []}, {"accounts": ABSENT}, {"accounts": ["@wickabel"]},
                 {"accounts": ["wickabel", "wickabel"]}, {"accounts": ["w" * 31]},
                 {"accounts": [f"account{n}" for n in range(26)]},
                 {"hashtags": ABSENT}, {"hashtags": "harbourtales"}, {"days": 7})
        for fields in seeds:
            with self.subTest(fields=repr(fields)):
                w = World(self)
                w.watch(**fields)
                self.assertEqual(w.run(), (2, "", WATCH_LINE))
                self.assertEqual(w.api.requests, [])
                self.assertIsNone(w.budget())

    def test_edges_that_are_valid(self):
        self.w.watch(accounts=["skarrowfen", "w" * 30] + [f"a.b_{n}" for n in range(23)], hashtags=[])
        self.assertEqual(self.w.run(apply=False),
                         (0, "read harbour-tales instagram: would search, 0 of 24 searches spent on 2026-11-13\n", ""))

    def test_twin_the_clean_read(self):
        self.read(4)


class B11InterfaceAndRegister(unittest.TestCase):
    def test_the_read_tool(self):
        tool = reads._tool("instagram")
        self.assertEqual(tool.REASONS, REASONS)
        self.assertEqual(tool.STORED, ("unreadable",))
        self.assertEqual(list(tool.WATCH), ["accounts", "hashtags"])
        self.assertFalse(hasattr(tool, "run"))

    def test_not_a_job_tool(self):
        with self.assertRaises(cmd_run.Refused) as caught:
            cmd_run._load_tool("instagram")
        self.assertEqual(str(caught.exception), "tool instagram is not installed")

    def test_the_register_row(self):
        register, problems = resources.load(resources.REGISTER)
        self.assertEqual(problems, [])
        tool = reads._tool("instagram")
        row = next(r for r in register["resources"] if r["id"] == tool.RESOURCE)
        self.assertEqual(row["checks"], [{"kind": "secret", "name": "external.instagram"},
                                         {"kind": "setting", "name": "INSTAGRAM_USER_ID"}])


if __name__ == "__main__":
    unittest.main()
