"""Tests for kit/lib/tools/youtube-public.py and kit/lib/release.py: the public switch through `factory run`.

The production is a temporary copy of tests/fixtures/run/production on branch `trunk`, with one branch per
recorded pull, and GitHub a copy of tests/fixtures/run/github (ticket 3, unit `ep01`, pull 4, caller
`renderer`). The tools directory and the resources register are the shipped ones. The world writes a key
file `platform.youtube` of mode 0600, commits `YOUTUBE_OAUTH_APP=production` and the switch request at
`distribution/ep01/youtube/requests/public.json`, and has the real gate pass a job of the tool
`youtube-public`. During `factory run` `urllib.request.build_opener` returns the fake opener of
tests/test_tool_youtube_upload.py, answering from tests/fixtures/youtube_upload/answers/token-ok.json and
tests/fixtures/youtube_public/answers/. Nothing reaches the network.
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
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_tool_youtube_upload import FakeOpener, answer  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import cmd_gate, cmd_lock, cmd_run, down, gitstate, github, ledger, release  # noqa: E402

RUN = ROOT / "tests" / "fixtures" / "run"
ANSWERS = ROOT / "tests" / "fixtures" / "youtube_public" / "answers"
NOW = "2026-11-02T09:00:00Z"
TICKET, PULL, BRANCH = 3, 4, "feat/RN-1-ep01"
TOOL, RESOURCE = "youtube-public", "platform.youtube"
REQUEST_PATH = "distribution/ep01/youtube/requests/public.json"
RELEASE_PATH = "distribution/ep01/release.json"
KEY_LINES = ["invented-client-id", "invented-client-secret", "invented-refresh-token"]
ACCESS_TOKEN = "invented-access-token"
VIDEO_ID = "inventedVid01"
SHA = hashlib.sha256(b"an invented video").hexdigest()
TOKEN_URL = "https://oauth2.googleapis.com/token"
READ_URL = f"https://www.googleapis.com/youtube/v3/videos?part=status,processingDetails&id={VIDEO_ID}"
UPDATE_URL = "https://www.googleapis.com/youtube/v3/videos?part=status"
GIT_IDENTITY = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_DATE", "GIT_COMMITTER_NAME",
                "GIT_COMMITTER_EMAIL", "GIT_COMMITTER_DATE")
MANIFEST_HEADER = "id\tsha256\tbytes\tsource\n"
RULE_11 = "  rule 11 released: changes files of a released episode: episodes/ep01/script.md"


def public(name):
    return json.loads((ANSWERS / f"{name}.json").read_text())


def clean_request():
    return {"post_id": VIDEO_ID, "sha256": SHA}


def clean_scenario():
    return {("POST", TOKEN_URL): answer("token-ok"), ("GET", READ_URL): public("read-private"),
            ("PUT", UPDATE_URL): public("update-ok")}


def read_status(scenario):
    return scenario[("GET", READ_URL)]["body"]["items"][0]["status"]


def digest_of(path):
    job = json.loads(Path(path).read_text())
    return hashlib.sha256(json.dumps(job, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class World:
    """A production on `trunk`, a recorded GitHub, a state directory and the key file, all temporary."""

    def __init__(self, test, request=None, text=None, key="\n".join(KEY_LINES) + "\n"):
        self.test = test
        self.tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, self.tmp)
        self.root = self.tmp / "show"
        self.dir = self.tmp / "github"
        self.state = self.tmp / "state"
        self.secrets = self.tmp / "secrets"
        patch = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                                             "FACTORY_GITHUB": str(self.dir), "FACTORY_NOW": NOW})
        patch.start()
        test.addCleanup(patch.stop)
        for name in GIT_IDENTITY:
            os.environ.pop(name, None)
        shutil.copytree(RUN / "production", self.root)
        self.git("init", "-q", "-b", "trunk")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "the production")
        shutil.copytree(RUN / "github", self.dir)
        for path in sorted((self.dir / "pulls").glob("*.json")):
            self.branch(json.loads(path.read_text())["head_ref"])
        for path in sorted((self.dir / "comments").glob("*.json")):
            path.write_text(path.read_text().replace(f"{{head:{PULL}}}", gitstate.head(self.root, BRANCH)))
        self.secrets.mkdir()
        self.key_file = self.secrets / RESOURCE
        self.key_file.write_text(key)
        self.key_file.chmod(0o600)
        (self.root / "production.local.env").write_text(f"STATE_DIR={self.state}\nSECRETS_DIR={self.secrets}\n")
        self.set_env("YOUTUBE_OAUTH_APP", "production")
        if text is None:
            text = json.dumps(clean_request() if request is None else request, indent=1, sort_keys=True) + "\n"
        self.commit(REQUEST_PATH, text)
        job = {"caller": "renderer", "expected": {"compute_min": 0, "usd": 0},
               "inputs": [{"path": REQUEST_PATH, "sha256": hashlib.sha256(text.encode()).hexdigest()}],
               "ticket": TICKET, "tool": TOOL}
        self.job = str(self.write("jobs/public.json", json.dumps(job, indent=1)))
        self.digest = digest_of(self.job)
        code, out, err = self._main(cmd_gate.main, [self.job, "--root", str(self.root), "--apply"])
        test.assertEqual(code, 0, out + err)
        self.opener = None

    def git(self, *args, cwd=None):
        return gitstate.git(cwd or self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid",
                            *args)

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

    def set_env(self, name, value):
        path = self.root / "production.env"
        lines = [line for line in path.read_text().splitlines() if not line.startswith(name + "=")]
        self.commit("production.env", "\n".join(lines + [f"{name}={value}"]) + "\n")

    def _main(self, main, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(github, "connect", return_value=github.Recorded(self.dir)))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def run(self, scenario=None):
        """``(exit, stdout, stderr)`` of ``factory run --apply`` with the fake opener answering ``scenario``."""
        self.opener = FakeOpener(clean_scenario() if scenario is None else scenario)
        with mock.patch.object(urllib.request, "build_opener", return_value=self.opener) as built:
            result = self._main(cmd_run.main, [self.job, "--root", str(self.root), "--apply"])
        self.built = built
        self.test.assertEqual(self.opener.unnamed, [])
        return result

    def lock(self):
        return self._main(cmd_lock.main, ["--root", str(self.root)])

    def script_on_pull(self):
        """Add `episodes/ep01/script.md` and a merge of `trunk` on the pull's branch."""
        where = self.tmp / "pull-tree"
        gitstate.add_worktree(self.root, where, "refs/heads/" + BRANCH)
        try:
            old = gitstate.head(where)
            (where / "episodes" / "ep01").mkdir(parents=True)
            (where / "episodes" / "ep01" / "script.md").write_text("An invented script.\n")
            self.git("add", "-A", cwd=where)
            self.git("commit", "-q", "-m", "the script of ep01", cwd=where)
            self.git("merge", "-q", "--no-ff", "--no-edit", "trunk", cwd=where)
            new = gitstate.head(where)
            self.git("update-ref", "refs/heads/" + BRANCH, new, old)
        finally:
            gitstate.remove_worktree(self.root, where)

    def sent(self):
        return [(method, url) for method, url, _, _ in self.opener.requests]

    def job_dir(self):
        return self.state / "jobs" / self.digest

    def out_text(self, name):
        return (self.job_dir() / "out" / name).read_text()

    def post(self):
        return json.loads(self.out_text("post.json"))

    def ledger_text(self):
        return gitstate.show(self.root, "trunk", ledger.PATH) or ""

    def runs(self):
        return [r for r in ledger.read(self.ledger_text()) if r["kind"] == "run"] if self.ledger_text() else []

    def manifest(self):
        return gitstate.show(self.root, BRANCH, gitstate.MANIFEST)

    def released(self):
        return gitstate.show(self.root, "trunk", RELEASE_PATH)

    def base_log(self):
        return self.git("log", "--format=%an\t%s", "trunk").splitlines()


def failed(reason):
    return f"run: tool {TOOL} failed: {reason}\n"


def line(world, verdict):
    return f"run job {world.digest}: {verdict} usd 0 compute_min 0\n"


def row(world, name, text):
    data = text.encode()
    return f"{world.digest[:16]}-{name}\t{hashlib.sha256(data).hexdigest()}\t{len(data)}\tjob {world.digest}\n"


class PublicTest(unittest.TestCase):
    def assert_failed(self, w, scenario, reason):
        code, out, err = w.run(scenario)
        self.assertEqual((code, out, err), (1, line(w, "FAILED"), failed(reason)))
        self.assertEqual([r["verdict"] for r in w.runs()], ["FAILED"])
        self.assertIsNone(w.manifest())
        self.assertIsNone(w.released())
        self.assertFalse(any(subject.startswith("factory\trelease:") for subject in w.base_log()))

    def assert_down(self, w, scenario):
        code, out, err = w.run(scenario)
        self.assertEqual((code, out, err), (2, "", f"run: resource {RESOURCE} is down (#5)\n"))
        issue = json.loads((w.dir / "issues" / "5.json").read_text())
        self.assertEqual((issue["state"], issue["title"], issue["labels"]),
                         ("open", down.title(RESOURCE), [down.LABEL]))
        self.assertEqual(w.runs(), [])
        self.assertFalse(os.path.lexists(w.job_dir()))

    def test_1_done_when(self):
        twin = World(self)
        twin.script_on_pull()
        code, out, err = twin.lock()
        self.assertEqual((code, err), (0, ""), out + err)
        self.assertNotIn("rule 11", out)

        w = World(self)
        code, out, err = w.run()
        self.assertEqual((code, out, err), (0, line(w, "DONE"), ""))
        self.assertEqual(w.sent(), [("POST", TOKEN_URL), ("GET", READ_URL), ("PUT", UPDATE_URL)])
        self.assertEqual(w.built.call_count, 1)
        handlers = w.built.call_args.args
        self.assertEqual(type(handlers[0]), urllib.request.ProxyHandler)
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsNone(handlers[1].redirect_request(None, None, 302, "", {}, "https://elsewhere.invalid/"))
        token, read, put = w.opener.requests
        self.assertEqual(sorted(urllib.parse.parse_qsl(token[3].decode())),
                         [("client_id", KEY_LINES[0]), ("client_secret", KEY_LINES[1]),
                          ("grant_type", "refresh_token"), ("refresh_token", KEY_LINES[2])])
        auth = "Bearer " + ACCESS_TOKEN
        self.assertEqual(read[2]["authorization"], auth)
        self.assertEqual((put[2]["authorization"], put[2]["content-type"]),
                         (auth, "application/json; charset=UTF-8"))
        status = {"containsSyntheticMedia": True, "embeddable": True, "license": "youtube",
                  "privacyStatus": "public", "publicStatsViewable": True, "selfDeclaredMadeForKids": False}
        self.assertEqual(put[3].decode(), json.dumps({"id": VIDEO_ID, "status": status}, sort_keys=True))

        post_text, release_text = w.out_text("post.json"), w.out_text("release.json")
        self.assertEqual(json.loads(post_text), {"disclosure": True, "platform": "youtube", "post_id": VIDEO_ID,
                                                 "processing": "succeeded", "sha256": SHA, "visibility": "public"})
        self.assertEqual(post_text, json.dumps(json.loads(post_text), indent=1, sort_keys=True) + "\n")
        self.assertEqual(json.loads(release_text), {"job": w.digest, "platform": "youtube", "post_id": VIDEO_ID,
                                                    "sha256": SHA, "ticket": TICKET})
        self.assertEqual(release_text, json.dumps(json.loads(release_text), indent=1, sort_keys=True) + "\n")
        self.assertEqual(w.released(), release_text)
        log = w.base_log()
        self.assertEqual(log[:2], [f"factory\tledger: run {w.digest[:12]} DONE",
                                   f"factory\trelease: ep01 job {w.digest[:12]}"])
        self.assertEqual(w.manifest(), MANIFEST_HEADER + row(w, "post.json", post_text)
                         + row(w, "release.json", release_text))
        self.assertEqual([r["verdict"] for r in w.runs()], ["DONE"])

        w.script_on_pull()
        code, out, err = w.lock()
        self.assertEqual((code, err), (0, ""), out + err)
        self.assertIn(RULE_11, out.splitlines())

    def test_2_not_ready_to_switch(self):
        for name, edit, reason in (
                ("public", lambda s: s.update(privacyStatus="public"), "video is not private"),
                ("processing", None, "video is not processed"),
                ("no disclosure", lambda s: s.pop("containsSyntheticMedia"), "disclosure not set")):
            with self.subTest(name):
                w = World(self)
                scenario = clean_scenario()
                if edit is None:
                    scenario[("GET", READ_URL)]["body"]["items"][0]["processingDetails"]["processingStatus"] = \
                        "processing"
                else:
                    edit(read_status(scenario))
                self.assert_failed(w, scenario, reason)
                self.assertEqual(w.sent(), [("POST", TOKEN_URL), ("GET", READ_URL)])

    def test_3_no_such_video(self):
        not_found = public("read-private")
        not_found["status"] = 404
        other = public("read-private")
        other["body"]["items"][0]["id"] = "inventedVid02"
        for name, found in (("read-missing", public("read-missing")), ("404", not_found), ("another id", other)):
            with self.subTest(name):
                w = World(self)
                scenario = clean_scenario()
                scenario[("GET", READ_URL)] = found
                self.assert_failed(w, scenario, "video not found")
                self.assertEqual(w.sent(), [("POST", TOKEN_URL), ("GET", READ_URL)])

    def test_4_the_switch_refused(self):
        no_status = public("update-ok")
        del no_status["body"]["status"]
        for name, found in (("update-forbidden", public("update-forbidden")), ("no status", no_status)):
            with self.subTest(name):
                w = World(self)
                scenario = clean_scenario()
                scenario[("PUT", UPDATE_URL)] = found
                self.assert_failed(w, scenario, "switch refused")
                self.assertEqual(w.sent(), [("POST", TOKEN_URL), ("GET", READ_URL), ("PUT", UPDATE_URL)])

    def test_5_the_switch_not_kept(self):
        for name, edit, reason, visibility, disclosure in (
                ("private", lambda s: s.update(privacyStatus="private"), "video is not public", "private", True),
                ("no disclosure", lambda s: s.pop("containsSyntheticMedia"), "disclosure not set", "public", None)):
            with self.subTest(name):
                w = World(self)
                scenario = clean_scenario()
                edit(scenario[("PUT", UPDATE_URL)]["body"]["status"])
                self.assert_failed(w, scenario, reason)
                self.assertEqual((w.post()["visibility"], w.post()["disclosure"]), (visibility, disclosure))
                self.assertFalse((w.job_dir() / "out" / "release.json").exists())

    def test_6_bad_request(self):
        missing = clean_request()
        del missing["sha256"]
        cases = {
            "a key missing": {"request": missing},
            "an extra key": {"request": {**clean_request(), "privacyStatus": "public"}},
            "an empty post_id": {"request": {**clean_request(), "post_id": ""}},
            "a sha256 of 63 digits": {"request": {**clean_request(), "sha256": SHA[:63]}},
            "a sha256 in upper case": {"request": {**clean_request(), "sha256": SHA.upper()}},
            "not JSON": {"text": "post_id: inventedVid01\n"},
        }
        for name, kwargs in cases.items():
            with self.subTest(name):
                w = World(self, **kwargs)
                self.assert_failed(w, clean_scenario(), "request is not a switch request")
                self.assertEqual(w.sent(), [])

    def test_7_down(self):
        with self.subTest("a key file of two lines"):
            w = World(self, key="\n".join(KEY_LINES[:2]) + "\n")
            self.assert_down(w, clean_scenario())
            self.assertEqual(w.sent(), [])
        with self.subTest("a token answer of 400"):
            w = World(self)
            scenario = clean_scenario()
            scenario[("POST", TOKEN_URL)]["status"] = 400
            self.assert_down(w, scenario)
            self.assertEqual(w.sent(), [("POST", TOKEN_URL)])

    def test_8_released_once(self):
        w = World(self)
        w.commit(RELEASE_PATH, "{}\n")
        code, out, err = w.run()
        self.assertEqual((code, out, err), (0, line(w, "DONE"), ""))
        self.assertEqual(w.released(), "{}\n")
        self.assertFalse(any("\trelease:" in subject for subject in w.base_log()))
        code, out, err = w.run()
        self.assertEqual((code, out, err), (0, line(w, "DONE").rstrip("\n") + cmd_gate.ALREADY + "\n", ""))
        self.assertEqual(w.sent(), [])
        self.assertEqual(w.released(), "{}\n")

    def test_9_replay(self):
        w = World(self)
        # A side effect of DEFAULT calls the wrapped function: the real one.
        with mock.patch.object(release, "write", wraps=release.write,
                               side_effect=[release.ReleaseError("boom"), mock.DEFAULT]) as patched:
            code, out, err = w.run()
            self.assertEqual((code, out, err), (2, "", "run: boom\n"))
            self.assertEqual(w.runs(), [])
            self.assertIsNone(w.released())
            code, out, err = w.run()
        self.assertEqual(patched.call_count, 2)
        self.assertEqual((code, out, err), (0, line(w, "DONE"), ""))
        self.assertEqual(w.sent(), [])
        self.assertEqual(w.released(), w.out_text("release.json"))
        self.assertEqual([r["verdict"] for r in w.runs()], ["DONE"])
        self.assertEqual(w.base_log()[:2], [f"factory\tledger: run {w.digest[:12]} DONE",
                                            f"factory\trelease: ep01 job {w.digest[:12]}"])

    def test_10_nothing_secret(self):
        def edited(key, change):
            scenario = clean_scenario()
            change(scenario[key])
            return scenario

        two_lines = "\n".join(KEY_LINES[:2]) + "\n"
        cases = (
            ("done", {}, clean_scenario()),
            ("not private", {}, edited(("GET", READ_URL),
                                       lambda a: a["body"]["items"][0]["status"].update(privacyStatus="public"))),
            ("not found", {}, edited(("GET", READ_URL), lambda a: a.update(status=404))),
            ("refused", {}, edited(("PUT", UPDATE_URL), lambda a: a.update(status=403))),
            ("not kept", {}, edited(("PUT", UPDATE_URL),
                                    lambda a: a["body"]["status"].update(privacyStatus="private"))),
            ("bad request", {"text": "not json\n"}, clean_scenario()),
            ("two lines", {"key": two_lines}, clean_scenario()),
            ("token 400", {}, edited(("POST", TOKEN_URL), lambda a: a.update(status=400))),
        )
        for name, kwargs, scenario in cases:
            with self.subTest(name):
                w = World(self, **kwargs)
                code, out, err = w.run(scenario)
                texts = [out, err, w.ledger_text(), w.manifest() or "", w.released() or ""]
                if w.job_dir().is_dir():
                    texts += [path.read_text() for path in sorted(w.job_dir().rglob("*")) if path.is_file()]
                texts += [path.read_text() for path in sorted((w.dir / "issues").glob("*.json"))]
                for secret in KEY_LINES + [ACCESS_TOKEN]:
                    for text in texts:
                        self.assertNotIn(secret, text)


if __name__ == "__main__":
    unittest.main()
