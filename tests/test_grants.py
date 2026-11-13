"""Tests for kit/lib/grants.py: `factory tick` refreshes the TikTok grant, `factory doctor` reports a lapse.

The production is tests/fixtures/tick/production, made a git repository on branch `trunk` in a temporary
directory, with an empty recorded GitHub and `STATE_DIR` and `SECRETS_DIR` in `production.local.env`.
TikTok's token endpoint is a fake opener (``urllib.request.build_opener`` patched) that logs each request
and answers from tests/fixtures/grants. Doctor runs on the shipped register.
"""

import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import cmd_doctor, cmd_tick, gitstate, grants, production  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "grants"
TICK = ROOT / "tests" / "fixtures" / "tick"
NOW = "2026-11-13T12:00:00Z"
URL = "https://open.tiktokapis.com/v2/oauth/token/"
SOURCE = "https://developers.tiktok.com/docs/en/oauth-refresh-a-user-access-token"
KEY = "platform.tiktok"
CLEAN = {"access_token": "act.seeded-old", "client_key": "invented-client-key",
         "client_secret": "invented-client-secret", "expires_at": "2026-11-13T12:20:00Z",
         "refresh_expires_at": "2027-05-12T12:00:00Z", "refresh_token": "rft.seeded-old"}
REFRESHED = {**CLEAN, "access_token": "act.seeded-new", "expires_at": "2026-11-14T12:00:00Z",
             "refresh_expires_at": "2027-11-13T12:00:00Z", "refresh_token": "rft.seeded-new"}
BODY = ("client_key=invented-client-key&client_secret=invented-client-secret&grant_type=refresh_token"
        "&refresh_token=rft.seeded-old")
STEP = "STEP - refresh platform.tiktok\n"
SECRETS = ("act.seeded-old", "rft.seeded-old", "act.seeded-new", "rft.seeded-new", "invented-client-key",
           "invented-client-secret")
TIKTOK_TITLE = "TikTok: posting and counts, one OAuth grant"
INSTAGRAM_TITLE = "Instagram: Reels publishing and insights, one OAuth grant"


def tally(steps, failed):
    return f"tick: {steps} steps, 0 need, 0 wait, {failed} failed\n"


def fixture(name):
    return json.loads((FIX / name).read_text())


def answer(status, body):
    return {"status": status, "body": body}


def grant_text(grant):
    return json.dumps(grant, indent=1, sort_keys=True) + "\n"


class Response:
    def __init__(self, status, data):
        self.status, self.data = status, data

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Opener:
    """Logs ``(method, URL, headers, body)`` per request; answers ``answer`` or raises it."""

    def __init__(self, log, reply):
        self.log, self.reply = log, reply

    def open(self, request, timeout=None):
        self.log.append((request.get_method(), request.full_url, dict(request.header_items()),
                         request.data.decode("ascii"), timeout))
        if isinstance(self.reply, Exception):
            raise self.reply
        body = self.reply["body"]
        data = (body if isinstance(body, str) else json.dumps(body)).encode("utf-8")
        if self.reply["status"] >= 400:
            raise urllib.error.HTTPError(request.full_url, self.reply["status"], "refused", {}, io.BytesIO(data))
        return Response(self.reply["status"], data)


class World:
    """The tick fixture production on `trunk`, an empty recorded GitHub and a secrets directory."""

    def __init__(self, test):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        test.addCleanup(shutil.rmtree, self.tmp)
        self.github = self.tmp / "github"
        for name in ("issues", "pulls", "comments"):
            (self.github / name).mkdir(parents=True)
        env = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "FACTORY_NOW": NOW,
               "FACTORY_GITHUB": str(self.github)}
        patch = mock.patch.dict(os.environ, env)
        patch.start()
        test.addCleanup(patch.stop)
        self.root = self.tmp / "show"
        shutil.copytree(TICK / "production", self.root)
        git = ("-c", "user.name=tester", "-c", "user.email=tester@example.invalid")
        gitstate.git(self.root, "init", "-q", "-b", "trunk")
        gitstate.git(self.root, "add", "-A")
        gitstate.git(self.root, *git, "commit", "-q", "-m", "the production")
        self.state = self.tmp / "state"
        self.secrets = self.tmp / "secrets"
        self.secrets.mkdir()
        self.key = self.secrets / KEY
        self.settings(STATE_DIR=self.state, SECRETS_DIR=self.secrets)
        self.log = []
        self.reply = answer(**{k: v for k, v in fixture("refresh-ok.json").items() if k in ("status", "body")})

    def settings(self, **values):
        (self.root / "production.local.env").write_text("".join(f"{k}={v}\n" for k, v in values.items()))

    def write(self, content):
        self.key.write_text(content if isinstance(content, str) else grant_text(content))
        self.key.chmod(0o600)

    def tick(self, apply=True):
        """``(exit, stdout, stderr)`` of ``factory tick``, with the fake opener."""
        out, err = io.StringIO(), io.StringIO()
        build = mock.patch("urllib.request.build_opener", lambda *handlers: Opener(self.log, self.reply))
        with build, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_tick.main(["--root", str(self.root)] + (["--apply"] if apply else []))
        return code, out.getvalue(), err.getvalue()

    def leaks(self):
        """The files under the test's directory, other than the key file, that hold a secret."""
        found = []
        for path in sorted(self.tmp.rglob("*")):
            if path.is_file() and not path.is_symlink() and path != self.key:
                data = path.read_bytes()
                if any(secret.encode() in data for secret in SECRETS):
                    found.append(path)
        return found


class Doctor:
    """A production directory for ``factory doctor`` with the distribution room's settings."""

    def __init__(self, test):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        test.addCleanup(shutil.rmtree, self.tmp)
        self.secrets = self.tmp / "secrets"
        self.secrets.mkdir()
        settings = {"TIKTOK_POSTING": "inbox", "INSTAGRAM_TEST_ACCOUNT": "17841400000000001",
                    "MEDIA_PUBLIC_BASE": "https://media.example.invalid/pub", "SECRETS_DIR": self.secrets}
        (self.tmp / "production.local.env").write_text("".join(f"{k}={v}\n" for k, v in settings.items()))
        patch = mock.patch.dict(os.environ, {"FACTORY_NOW": NOW})
        patch.start()
        test.addCleanup(patch.stop)

    def write(self, rid, content, mtime=None):
        path = self.secrets / rid
        path.write_text(content if isinstance(content, str) else grant_text(content))
        path.chmod(0o600)
        if mtime is not None:
            stamp = datetime.strptime(mtime, grants.TIME_FORMAT).replace(tzinfo=timezone.utc).timestamp()
            os.utime(path, (stamp, stamp))

    def run(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_doctor.main(["--root", str(self.tmp), "--rooms", "distribution", "--wave", "5", *extra])
        return code, out.getvalue(), err.getvalue()

    @staticmethod
    def row(out, rid):
        """The row of ``rid`` with the reason lines under it."""
        lines = out.splitlines()
        start = next(i for i, line in enumerate(lines) if line.split("\t")[1:2] == [rid])
        end = start + 1
        while end < len(lines) and lines[end].startswith("  "):
            end += 1
        return lines[start:end]


class GrantsTest(unittest.TestCase):
    def assertFailed(self, world, reason, requests=1, before=None):
        before = world.key.read_bytes() if before is None else before
        code, out, err = world.tick()
        self.assertEqual(out, f"FAIL - refresh platform.tiktok: {reason}\n" + tally(0, 1))
        self.assertEqual((code, err), (1, ""))
        self.assertEqual(len(world.log), requests)
        self.assertEqual(world.key.read_bytes(), before)
        self.assertEqual(sorted(os.listdir(world.secrets)), [KEY])
        return out + err

    def test_1_clean_refresh(self):
        world = World(self)
        world.write(CLEAN)
        code, out, err = world.tick()
        self.assertEqual((code, out, err), (0, STEP + tally(1, 0), ""))
        self.assertEqual(len(world.log), 1)
        method, url, headers, body, timeout = world.log[0]
        self.assertEqual((method, url, body, timeout), ("POST", URL, BODY, 60))
        self.assertEqual(headers, {"Content-type": "application/x-www-form-urlencoded"})
        self.assertEqual(world.key.read_text(), grant_text(REFRESHED))
        self.assertEqual(world.key.stat().st_mode & 0o777, 0o600)
        self.assertEqual(sorted(os.listdir(world.secrets)), [KEY])
        self.assertEqual(world.tick(), (0, tally(0, 0), ""))
        self.assertEqual(len(world.log), 1)

    def test_2_dry_run(self):
        world = World(self)
        world.write(CLEAN)
        before = world.key.read_bytes()
        self.assertEqual(world.tick(apply=False), (0, STEP + tally(1, 0), ""))
        self.assertEqual(world.log, [])
        self.assertEqual(world.key.read_bytes(), before)

    def test_3_when_it_is_due(self):
        world = World(self)
        world.write({**CLEAN, "expires_at": "2026-11-13T12:31:00Z"})
        self.assertEqual(world.tick(), (0, tally(0, 0), ""))
        self.assertEqual(world.log, [])
        due = [{**CLEAN, "expires_at": "2026-11-13T12:30:00Z"},
               {k: v for k, v in CLEAN.items() if k != "expires_at"},
               {**CLEAN, "refresh_expires_at": "soon"}]
        for number, grant in enumerate(due, 1):
            with self.subTest(grant=grant):
                world.write(grant)
                self.assertEqual(world.tick(), (0, STEP + tally(1, 0), ""))
                self.assertEqual(len(world.log), number)
                self.assertEqual(json.loads(world.key.read_text())["refresh_token"], "rft.seeded-new")

    def test_4_nothing_to_refresh(self):
        world = World(self)
        self.assertEqual(world.tick(), (0, tally(0, 0), ""))
        world.write(CLEAN)
        world.settings(STATE_DIR=world.state)
        self.assertEqual(world.tick(), (0, tally(0, 0), ""))
        self.assertEqual(world.log, [])

    def test_5_refused(self):
        for reply in (answer(**{k: v for k, v in fixture("refresh-invalid-grant.json").items()
                                if k in ("status", "body")}),
                      answer(200, {"error": "invalid_request"})):
            with self.subTest(reply=reply):
                world = World(self)
                world.write(CLEAN)
                world.reply = reply
                self.assertFailed(world, "refresh refused")

    def test_6_malformed(self):
        ok = fixture("refresh-ok.json")["body"]
        for body in ({k: v for k, v in ok.items() if k != "refresh_token"}, {**ok, "expires_in": True}, "<html>"):
            with self.subTest(body=body):
                world = World(self)
                world.write(CLEAN)
                world.reply = answer(200, body)
                self.assertFailed(world, "refresh answer malformed")

    def test_7_unreachable(self):
        world = World(self)
        world.write(CLEAN)
        world.reply = urllib.error.URLError("down")
        self.assertFailed(world, "token endpoint unreachable")

    def test_8_not_refreshable(self):
        for content in ("act.seeded-old", {"access_token": "act.seeded-old"}, {**CLEAN, "client_secret": ""}):
            with self.subTest(content=content):
                world = World(self)
                world.write(content)
                self.assertFailed(world, "key file is not a refreshable grant", requests=0)

    def test_9_lapsed(self):
        world = World(self)
        world.write({**CLEAN, "refresh_expires_at": NOW})
        self.assertFailed(world, "grant has lapsed", requests=0)

    def test_10_cannot_replace(self):
        world = World(self)
        world.write(CLEAN)
        with mock.patch("tempfile.mkstemp", side_effect=PermissionError("read-only")):
            self.assertFailed(world, "key file cannot be replaced", requests=0)

    def test_11_frozen(self):
        world = World(self)
        world.write(CLEAN)
        shutil.copy(TICK / "github" / "issues" / "24.json", world.github / "issues" / "24.json")
        before = world.key.read_bytes()
        self.assertEqual(world.tick(), (0, "FROZEN\n", ""))
        self.assertEqual(world.log, [])
        self.assertEqual(world.key.read_bytes(), before)

    def test_12_doctor_tiktok(self):
        cases = [("2026-11-27T12:00:01Z", "ok", []),
                 ("2026-11-27T12:00:00Z", "ok", ["  secret platform.tiktok: lapses within 14 days"]),
                 (NOW, "missing", ["  secret platform.tiktok: lapsed"])]
        for lapses, state, reasons in cases:
            with self.subTest(lapses=lapses):
                doctor = Doctor(self)
                doctor.write(KEY, {**CLEAN, "refresh_expires_at": lapses})
                code, out, err = doctor.run()
                self.assertEqual(Doctor.row(out, KEY), [f"{state}\t{KEY}\t{TIKTOK_TITLE}"] + reasons)
                self.assertEqual(err, "")
                if state == "missing":
                    self.assertEqual(code, 1)
        doctor = Doctor(self)
        doctor.write(KEY, {**CLEAN, "refresh_expires_at": "2026-11-27T12:00:00Z"})
        code, out, err = doctor.run("--json")
        rows = {row["id"]: row for row in json.loads(out)["resources"]}
        self.assertEqual(rows[KEY]["state"], "ok")
        self.assertEqual(rows[KEY]["reasons"], ["secret platform.tiktok: lapses within 14 days"])
        doctor = Doctor(self)
        doctor.write(KEY, "act.seeded-old\n")
        code, out, err = doctor.run()
        self.assertEqual(Doctor.row(out, KEY), [f"ok\t{KEY}\t{TIKTOK_TITLE}"])

    def test_13_doctor_instagram(self):
        rid = "platform.instagram"
        cases = [("2026-09-29T12:00:00Z", "ok", []),
                 ("2026-09-28T12:00:00Z", "ok", [f"  secret {rid}: lapses within 14 days"]),
                 ("2026-09-14T12:00:00Z", "missing", [f"  secret {rid}: lapsed"])]
        for mtime, state, reasons in cases:
            with self.subTest(mtime=mtime):
                doctor = Doctor(self)
                doctor.write(rid, "invented-ig-token\n", mtime)
                doctor.write("platform.youtube", "invented-client-id\ninvented-secret\ninvented-refresh\n", mtime)
                code, out, err = doctor.run()
                self.assertEqual(Doctor.row(out, rid), [f"{state}\t{rid}\t{INSTAGRAM_TITLE}"] + reasons)
                self.assertFalse([line for line in Doctor.row(out, "platform.youtube") if "lapse" in line])
                if state == "missing":
                    self.assertEqual(code, 1)

    def test_14_time(self):
        for value in (NOW, "2026-11-13 12:00:00", "2026-11-13T12:00:00+00:00", "2026-13-13T12:00:00Z"):
            with self.subTest(value=value):
                env = {"FACTORY_NOW": value}
                try:
                    expected = production.now(env)
                except production.ProductionError as error:
                    with self.assertRaises(ValueError) as caught:
                        grants.now(env)
                    self.assertEqual(str(caught.exception), str(error))
                else:
                    self.assertEqual(grants.now(env), expected)
        doctor = Doctor(self)
        with mock.patch.dict(os.environ, {"FACTORY_NOW": "2026-11-13 12:00:00"}):
            code, out, err = doctor.run()
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(err, "doctor: FACTORY_NOW is not a UTC time YYYY-MM-DDTHH:MM:SSZ\n")

    def test_15_no_secret_leaks(self):
        streams = []
        world = World(self)
        world.write(CLEAN)
        streams.append(world.tick())
        self.assertEqual(world.leaks(), [])
        for reply in (answer(400, fixture("refresh-invalid-grant.json")["body"]), urllib.error.URLError("down")):
            world = World(self)
            world.write(CLEAN)
            world.reply = reply
            streams.append(world.tick())
            self.assertEqual(world.leaks(), [])
        for lapses in ("2026-11-27T12:00:00Z", NOW):
            doctor = Doctor(self)
            doctor.write(KEY, {**CLEAN, "refresh_expires_at": lapses})
            streams.append(doctor.run())
            streams.append(doctor.run("--json"))
        self.assertEqual(len(streams), 7)
        for code, out, err in streams:
            for secret in SECRETS:
                self.assertNotIn(secret, out + err)

    def test_16_fixtures_and_surface(self):
        expected = {
            "refresh-ok.json": (200, {"access_token": "act.seeded-new", "expires_in": 86400,
                                      "open_id": "invented-open-id", "refresh_expires_in": 31536000,
                                      "refresh_token": "rft.seeded-new", "scope": "video.upload",
                                      "token_type": "Bearer"}),
            "refresh-invalid-grant.json": (400, {"error": "invalid_grant",
                                                 "error_description": "Refresh token is invalid or expired.",
                                                 "log_id": "invented-log-1"})}
        for name, (status, body) in expected.items():
            with self.subTest(name=name):
                text = (FIX / name).read_text()
                found = json.loads(text)
                self.assertEqual(sorted(found), ["body", "read_at", "source", "status"])
                self.assertEqual(found, {"source": SOURCE, "read_at": "2026-11-13", "status": status, "body": body})
                self.assertEqual(text, json.dumps(found, indent=1, sort_keys=True) + "\n")
        self.assertEqual(grants.TIME_FORMAT, production.TIME_FORMAT)
        source = (ROOT / "kit" / "lib" / "grants.py").read_text()
        self.assertIsNone(re.search(r"^\s*(from|import)\s+kit\b", source, re.M))
        self.assertNotIn("urlopen", source)


if __name__ == "__main__":
    unittest.main()
