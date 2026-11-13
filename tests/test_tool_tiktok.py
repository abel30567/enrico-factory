"""Tests for kit/lib/tools/tiktok-upload.py: `factory run` on an upload job, in each posting mode.

The production is tests/fixtures/run/production on branch `trunk` in a temporary directory, with one branch
per recorded pull, and GitHub is a copy of tests/fixtures/run/github, as in tests/test_run.py. The tools are
the shipped kit/lib/tools and the register the shipped kit/resources.json. The upload request is
tests/fixtures/tool_tiktok/request.json, committed on `trunk`. TikTok's answers are
tests/fixtures/tool_tiktok/answers, written by hand from the public API reference page each one names.
``urllib.request.build_opener`` is replaced by a fake that serves them in order and records each request.
Nothing reaches the network.
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
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import cmd_gate, cmd_run, gitstate, github, ledger, resources  # noqa: E402

RUN = ROOT / "tests" / "fixtures" / "run"
FIX = ROOT / "tests" / "fixtures" / "tool_tiktok"
NOW = "2026-11-02T09:00:00Z"
TICKET, PULL, BRANCH = 3, 4, "feat/RN-1-ep01"
TOKEN = "act.seeded-7f3a"
KEY_TEXT = json.dumps({"access_token": TOKEN}) + "\n"
REQUEST = "requests/up-1.json"
JOB = "jobs/up-1.json"
INBOX_URL = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
POST_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
MANIFEST_HEADER = "id\tsha256\tbytes\tsource\n"
REASONS = ("request is not {video_url, title}", "account is not private", "privacy level not offered",
           "too many posts", "video url not verified", "platform unreachable", "upload refused")
BAD_REQUEST = "run: tool tiktok-upload failed: request is not {video_url, title}\n"
DOWN = "run: resource platform.tiktok is down\n"


def request_fixture():
    return json.loads((FIX / "request.json").read_text())


def digest_of(path):
    job = json.loads(Path(path).read_text())
    return hashlib.sha256(json.dumps(job, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def line(digest, verdict):
    return f"run job {digest}: {verdict} usd 0 compute_min 0"


class FakeTikTok:
    """Stands in for ``urllib.request.build_opener``: serves ``answers`` in order, records every request."""

    def __init__(self, answers=(), raises=None):
        self.answers = [json.loads((FIX / "answers" / f"{name}.json").read_text()) for name in answers]
        self.raises = raises
        self.sent = []
        self.handlers = []

    def __call__(self, *handlers):
        self.handlers.append([type(h) for h in handlers])
        return self

    def open(self, req, timeout=None):
        self.sent.append({"url": req.full_url, "method": req.get_method(), "timeout": timeout,
                          "authorization": req.get_header("Authorization"),
                          "content_type": req.get_header("Content-type"), "body": json.loads(req.data)})
        if self.raises is not None:
            raise self.raises
        answer = self.answers.pop(0)
        body = json.dumps(answer["body"]).encode("utf-8")
        if answer["status"] >= 400:
            raise urllib.error.HTTPError(req.full_url, answer["status"], "error", {}, io.BytesIO(body))
        return contextlib.closing(io.BytesIO(body))


class World:
    """A production on `trunk` with an upload request and its job, a recorded GitHub, a state and a key."""

    def __init__(self, test, mode="direct", request=None, key=KEY_TEXT):
        self.test = test
        self.tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, self.tmp)
        self.root = self.tmp / "show"
        self.dir = self.tmp / "github"
        self.state = self.tmp / "state"
        self.secrets = self.tmp / "secrets"
        for patch in (mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                                                   "FACTORY_GITHUB": str(self.dir), "FACTORY_NOW": NOW}),
                      mock.patch.object(cmd_run, "TOOLS_DIR", ROOT / "kit" / "lib" / "tools"),
                      mock.patch.object(resources, "REGISTER", ROOT / "kit" / "resources.json")):
            patch.start()
            test.addCleanup(patch.stop)
        shutil.copytree(RUN / "production", self.root)
        self.git("init", "-q", "-b", "trunk")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "the production")
        shutil.copytree(RUN / "github", self.dir)
        for path in sorted((self.dir / "pulls").glob("*.json")):
            self.branch(json.loads(path.read_text())["head_ref"])
        for path in sorted((self.dir / "comments").glob("*.json")):
            path.write_text(path.read_text().replace(f"{{head:{PULL}}}", self.head(BRANCH)))
        self.secrets.mkdir()
        if key is not None:
            self.key(key)
        (self.root / "production.local.env").write_text(f"STATE_DIR={self.state}\nSECRETS_DIR={self.secrets}\n")
        self.set_env("TIKTOK_POSTING", mode)
        text = (FIX / "request.json").read_text() if request is None else request
        self.commit(REQUEST, text)
        self.job = str(self.write(JOB, json.dumps({
            "caller": "renderer", "expected": {"compute_min": 0, "usd": 0},
            "inputs": [{"path": REQUEST, "sha256": hashlib.sha256(text.encode()).hexdigest()}],
            "ticket": TICKET, "tool": "tiktok-upload"}, indent=1)))
        self.digest = digest_of(self.job)

    def git(self, *args):
        return gitstate.git(self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", *args)

    def head(self, ref="trunk"):
        return gitstate.head(self.root, ref)

    def branch(self, ref):
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
        self.write(rel, text)
        self.git("add", "--", rel)
        self.git("commit", "-q", "-m", "change " + rel)

    def key(self, text, mode=0o600):
        path = self.secrets / "platform.tiktok"
        path.write_text(text)
        path.chmod(mode)

    def set_env(self, name, value):
        path = self.root / "production.env"
        lines = [l for l in path.read_text().splitlines() if not l.startswith(name + "=")]
        self.commit("production.env", "\n".join(lines + [f"{name}={value}"]) + "\n")

    def _main(self, main, argv, fake):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(urllib.request, "build_opener", fake))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def passed(self):
        code, out, err = self._main(cmd_gate.main, [self.job, "--root", str(self.root), "--apply"], FakeTikTok())
        self.test.assertEqual(code, 0, out + err)
        return self

    def run(self, fake):
        return self._main(cmd_run.main, [self.job, "--root", str(self.root), "--apply"], fake)

    def rows(self, kind):
        text = gitstate.show(self.root, "trunk", ledger.PATH)
        return [r for r in ([] if text is None else ledger.read(text)) if r["kind"] == kind]

    def job_dir(self):
        return self.state / "jobs" / self.digest

    def post(self):
        path = self.job_dir() / "out" / "post.json"
        return path.read_text() if path.is_file() else None

    def recorded_text(self):
        """Every byte under ``STATE_DIR`` and the recorded GitHub, the ledger and the manifest, as text."""
        texts = [p.read_bytes().decode("utf-8", "replace") for top in (self.state, self.dir)
                 for p in sorted(Path(top).rglob("*")) if p.is_file()]
        texts += [gitstate.show(self.root, "trunk", ledger.PATH) or "", gitstate.show(self.root, BRANCH, gitstate.MANIFEST) or ""]
        return "\n".join(texts)


def sent_body(mode):
    request = request_fixture()
    source = {"source": "PULL_FROM_URL", "video_url": request["video_url"]}
    if mode == "inbox":
        return {"source_info": source}
    privacy = "SELF_ONLY" if mode == "private" else "PUBLIC_TO_EVERYONE"
    return {"post_info": {"title": request["title"], "privacy_level": privacy, "is_aigc": True}, "source_info": source}


def post_text(mode, publish_id):
    privacy = {"inbox": None, "private": "SELF_ONLY", "direct": "PUBLIC_TO_EVERYONE"}[mode]
    return json.dumps({"is_aigc": None if mode == "inbox" else True, "mode": mode, "privacy_level": privacy,
                       "publish_id": publish_id}, indent=1, sort_keys=True) + "\n"


class CleanModesTest(unittest.TestCase):
    """Behaviours 1, 2 and 7: one request per mode, ``post.json``, one manifest row, one DONE row."""

    ANSWERS = {"inbox": "inbox-ok", "private": "direct-ok", "direct": "direct-ok"}

    def check_mode(self, mode):
        w = World(self, mode).passed()
        answer = json.loads((FIX / "answers" / f"{self.ANSWERS[mode]}.json").read_text())
        publish_id = answer["body"]["data"]["publish_id"]
        fake = FakeTikTok([self.ANSWERS[mode]])
        code, out, err = w.run(fake)
        self.assertEqual((code, out, err), (0, line(w.digest, "DONE") + "\n", ""))
        [sent] = fake.sent
        self.assertEqual(sent, {"url": INBOX_URL if mode == "inbox" else POST_URL, "method": "POST", "timeout": 60,
                                "authorization": f"Bearer {TOKEN}",
                                "content_type": "application/json; charset=UTF-8", "body": sent_body(mode)})
        [handlers] = fake.handlers
        self.assertEqual(len(handlers), 2)
        self.assertIs(handlers[0], urllib.request.ProxyHandler)
        self.assertTrue(issubclass(handlers[1], urllib.request.HTTPRedirectHandler))
        text = post_text(mode, publish_id)
        self.assertEqual(w.post(), text)
        self.assertEqual(sorted(p.name for p in (w.job_dir() / "out").iterdir()), ["post.json"])
        sha = hashlib.sha256(text.encode()).hexdigest()
        self.assertEqual(gitstate.show(w.root, BRANCH, gitstate.MANIFEST),
                         MANIFEST_HEADER + f"{w.digest[:16]}-post.json\t{sha}\t{len(text.encode())}\tjob {w.digest}\n")
        [row] = w.rows("run")
        self.assertEqual(ledger.line(row), "\t".join(
            [NOW, "run", w.digest, str(TICKET), "ep01", "tiktok-upload", "0", "0", "renderer", "DONE", "-"]))
        self.assertNotIn(TOKEN, out + err + w.recorded_text())
        return sent, json.loads(w.post())

    def test_inbox(self):
        sent, post = self.check_mode("inbox")
        self.assertEqual(sorted(sent["body"]), ["source_info"])
        self.assertIsNone(post["is_aigc"])

    def test_private(self):
        sent, post = self.check_mode("private")
        self.assertIs(sent["body"]["post_info"]["is_aigc"], True)
        self.assertIs(post["is_aigc"], True)
        self.assertEqual(sent["body"]["post_info"]["privacy_level"], "SELF_ONLY")

    def test_direct(self):
        sent, post = self.check_mode("direct")
        self.assertIs(sent["body"]["post_info"]["is_aigc"], True)
        self.assertIs(post["is_aigc"], True)
        self.assertEqual(sent["body"]["post_info"]["privacy_level"], "PUBLIC_TO_EVERYONE")


class RequestTest(unittest.TestCase):
    """Behaviour 3: a first input that is not the upload request is refused before any request."""

    def refused(self, text):
        w = World(self, "direct", request=text).passed()
        fake = FakeTikTok(["direct-ok"])
        code, out, err = w.run(fake)
        self.assertEqual((code, out, err), (1, line(w.digest, "FAILED") + "\n", BAD_REQUEST))
        self.assertEqual(fake.sent, [])
        [row] = w.rows("run")
        self.assertEqual((row["verdict"], ledger.line(row).split("\t")[6]), ("FAILED", "0"))
        self.assertIsNone(w.post())

    def with_changes(self, **changes):
        request = request_fixture()
        request.update(changes)
        return json.dumps({k: v for k, v in request.items() if v is not None}, indent=1, sort_keys=True) + "\n"

    def test_each_defect(self):
        url = request_fixture()["video_url"]
        defects = {
            "a third key": self.with_changes(caption="x"),
            "no title": self.with_changes(title=None),
            "a title of spaces": self.with_changes(title="   "),
            "a title of 2201 a": self.with_changes(title="a" * 2201),
            "http": self.with_changes(video_url=url.replace("https://", "http://")),
            "63 hex digits": self.with_changes(video_url=url[:-1]),
            "a question mark": self.with_changes(video_url=url + "?x=1"),
            "a list": json.dumps([request_fixture()]) + "\n",
            "not JSON": "a lantern in the fog\n",
        }
        for name, text in defects.items():
            with self.subTest(name):
                self.refused(text)

    def test_a_title_of_2200_passes(self):
        w = World(self, "direct", request=self.with_changes(title="a" * 2200)).passed()
        fake = FakeTikTok(["direct-ok"])
        code, out, err = w.run(fake)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(fake.sent[0]["body"]["post_info"]["title"], "a" * 2200)


class AnswerTest(unittest.TestCase):
    """Behaviours 4 and 7: an answer that is not a post is a FAILED job, recorded once."""

    def failed(self, fake, reason):
        w = World(self, "direct").passed()
        code, out, err = w.run(fake)
        self.assertEqual((code, out, err), (1, line(w.digest, "FAILED") + "\n",
                                            f"run: tool tiktok-upload failed: {reason}\n"))
        self.assertEqual(len(fake.sent), 1)
        [row] = w.rows("run")
        self.assertEqual(row["verdict"], "FAILED")
        self.assertIsNone(w.post())
        again = FakeTikTok(["direct-ok"])
        code, out, err = w.run(again)
        self.assertEqual((code, out), (1, line(w.digest, "FAILED") + " (already recorded)\n"))
        self.assertEqual(again.sent, [])
        self.assertIsNone(w.post())
        self.assertNotIn(TOKEN, out + err + w.recorded_text())

    def test_each_answer(self):
        for answer, reason in (("unaudited", "account is not private"), ("privacy-mismatch", "privacy level not offered"),
                               ("too-many", "too many posts"), ("url-unverified", "video url not verified"),
                               ("invalid-param", "upload refused"), ("no-publish-id", "upload refused")):
            with self.subTest(answer):
                self.failed(FakeTikTok([answer]), reason)

    def test_no_answer(self):
        self.failed(FakeTikTok(raises=urllib.error.URLError("x")), "platform unreachable")


class DownTest(unittest.TestCase):
    """Behaviours 5 and 7: a token TikTok refuses, or no token, is a resource down: nothing is recorded."""

    def down(self, w, fake):
        code, out, err = w.run(fake)
        self.assertEqual((code, out, err), (2, "", DOWN))
        self.assertEqual(w.rows("run"), [])
        self.assertFalse(os.path.lexists(w.job_dir()))
        self.assertNotIn(TOKEN, out + err + w.recorded_text())

    def test_refused_token(self):
        for answer in ("token-invalid", "scope"):
            with self.subTest(answer):
                w = World(self, "private").passed()
                fake = FakeTikTok([answer])
                self.down(w, fake)
                self.assertEqual(len(fake.sent), 1)

    def test_each_key_defect(self):
        for name, text in (("plain text", TOKEN + "\n"), ("no access token", '{"refresh_token": "x"}\n'),
                           ("empty access token", '{"access_token": ""}\n')):
            with self.subTest(name):
                w = World(self, "private", key=text).passed()
                fake = FakeTikTok(["direct-ok"])
                self.down(w, fake)
                self.assertEqual(fake.sent, [])

    def test_a_key_with_other_keys_passes(self):
        w = World(self, "private", key=json.dumps({"access_token": TOKEN, "refresh_token": "rft.other"})).passed()
        fake = FakeTikTok(["direct-ok"])
        code, out, err = w.run(fake)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(fake.sent[0]["authorization"], f"Bearer {TOKEN}")


class RegisterTest(unittest.TestCase):
    """Behaviour 6: the register refuses an unknown mode or a missing key before the tool runs."""

    def refused(self, w, message):
        fake = FakeTikTok(["direct-ok"])
        code, out, err = w.run(fake)
        self.assertEqual((code, out, err), (2, "", f"run: resource platform.tiktok is not usable: {message}\n"))
        self.assertEqual(fake.sent, [])
        self.assertEqual(w.rows("run"), [])

    def test_public_is_not_a_mode(self):
        self.refused(World(self, "public").passed(),
                     "setting TIKTOK_POSTING is not one of: inbox, private, direct")

    def test_no_key_file(self):
        self.refused(World(self, "direct", key=None).passed(), "secret platform.tiktok: missing")


class InterfaceTest(unittest.TestCase):
    """Behaviour 8: the tool loads through `factory run` and names its resource and reasons."""

    def test_load(self):
        tool = cmd_run._load_tool("tiktok-upload")
        self.assertEqual(tool.RESOURCE, "platform.tiktok")
        self.assertEqual(tool.REASONS, REASONS)

    def test_no_redirect_is_followed(self):
        tool = cmd_run._load_tool("tiktok-upload")
        handler = tool._NoRedirect()
        self.assertIsInstance(handler, urllib.request.HTTPRedirectHandler)
        self.assertIsNone(handler.redirect_request(None, None, 302, "Found", {}, "https://elsewhere.invalid/"))

    def test_fixtures_name_their_page(self):
        for path in sorted((FIX / "answers").glob("*.json")):
            with self.subTest(path.name):
                answer = json.loads(path.read_text())
                self.assertEqual(sorted(answer), ["body", "read_at", "source", "status"])
                self.assertEqual(answer["read_at"], "2026-11-13")
                self.assertTrue(answer["source"].startswith("https://developers.tiktok.com/doc/"))
                self.assertEqual(path.read_text(), json.dumps(answer, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    unittest.main()
