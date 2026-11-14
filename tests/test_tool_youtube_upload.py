"""Tests for kit/lib/tools/youtube-upload.py: one private upload through `factory run`, end to end.

The production is a temporary copy of tests/fixtures/run/production on branch `trunk`, with one branch per
recorded pull, and GitHub a copy of tests/fixtures/run/github (ticket 3, pull 4, caller `renderer`). The
copies are edited only to make ticket 3 the post `ep01/youtube` of stage `distribution/post`, made by
`renderer`. The tools directory and the resources register are the shipped ones. The world writes a key file
`platform.youtube` of mode 0600, commits `YOUTUBE_OAUTH_APP=production`, `media/manifest.tsv` (rows
`reel-ep01` and `captions-ep01`) and the upload request at `distribution/ep01/youtube/requests/upload.json`,
and has the real gate pass the job `jobs/<request>` of the tool `youtube-upload`. During `factory run`
`urllib.request.build_opener` returns a fake opener that logs each request and answers it from the answer
file the scenario names for its method and URL (tests/fixtures/youtube_upload/answers/). Nothing reaches
the network. ``publish_findings`` runs the publish check on a record in a new temporary directory.
"""

import contextlib
import email.message
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import cmd_gate, cmd_run, gitstate, github, ledger, media, posts  # noqa: E402,F401
from kit.lib.lints import publish  # noqa: E402

RUN = ROOT / "tests" / "fixtures" / "run"
ANSWERS = ROOT / "tests" / "fixtures" / "youtube_upload" / "answers"
NOW = "2026-11-02T09:00:00Z"
TICKET, PULL, BRANCH = 3, 4, "feat/RN-1-ep01"
TOOL, RESOURCE = "youtube-upload", "platform.youtube"
REQUEST_PATH = "distribution/ep01/youtube/requests/upload.json"
JOB_PATH = "jobs/" + REQUEST_PATH
RECORD_PATH = "distribution/ep01/youtube/post.json"
MANIFEST_PATH = "media/manifest.tsv"
KEY_LINES = ["invented-client-id", "invented-client-secret", "invented-refresh-token"]
ACCESS_TOKEN = "invented-access-token"
VIDEO = (b"an invented video, sixty-four bytes long, for the upload tests.." * 2)[:64]
SHA = hashlib.sha256(VIDEO).hexdigest()
LINK = f"https://media.example.invalid/pub/{SHA}"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
LOCATION = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=invented-upload-1"
READ_URL = "https://www.googleapis.com/youtube/v3/videos?part=status,processingDetails&id=inventedVid01"
REASONS = ("request is not an upload request", "public link unreachable", "public link serves other bytes",
           "upload refused", "upload failed")
GIT_IDENTITY = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_DATE", "GIT_COMMITTER_NAME",
                "GIT_COMMITTER_EMAIL", "GIT_COMMITTER_DATE")
MANIFEST_HEADER = "id\tsha256\tbytes\tsource\n"
CAPTIONS_SHA = hashlib.sha256(b"captions").hexdigest()
VIDEO_ROW = f"reel-ep01\t{SHA}\t64\tconform-editor\n"
CAPTIONS_ROW = f"captions-ep01\t{CAPTIONS_SHA}\t8\tconform-editor\n"
MANIFEST = MANIFEST_HEADER + VIDEO_ROW + CAPTIONS_ROW
OTHER_SHA = hashlib.sha256(b"other").hexdigest()
RULEBOOK = {"platform": "youtube", "kinds": {}, "policy": [],
            "disclosure": {"field": "status.containsSyntheticMedia", "value": True, "source": None, "read_at": None}}


def answer(name):
    return json.loads((ANSWERS / f"{name}.json").read_text())


def video_answer(status=200, body=VIDEO):
    return {"status": status, "headers": {}, "body": body}


def clean_request():
    return {"captions": "media:captions-ep01", "description": "An invented episode.", "link": LINK,
            "title": "Episode one", "video": "media:reel-ep01"}


def clean_scenario():
    return {("GET", LINK): video_answer(), ("POST", TOKEN_URL): answer("token-ok"),
            ("POST", UPLOAD_URL): answer("init-ok"), ("PUT", LOCATION): answer("put-ok"),
            ("GET", READ_URL): answer("read-ok")}


def digest_of(path):
    job = json.loads(Path(path).read_text())
    return hashlib.sha256(json.dumps(job, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def clean_record(digest, **changes):
    """The record of a clean upload of the job ``digest``, with ``changes``."""
    return {"captions": True, "fields": {"status.containsSyntheticMedia": True}, "job": digest,
            "platform": "youtube", "post_id": "inventedVid01", "processed": False, "read_at": NOW, "sha256": SHA,
            "visibility": "private", **changes}


def as_post(stage_files):
    """The copied pipeline's `render` entry made the stage `distribution/post`, unit `post`, no `after`."""
    entries = json.loads(stage_files.read_text())
    for entry in entries:
        if entry["stage"] == "render":
            entry.update({"room": "distribution", "stage": "post", "unit": "post", "after": ""})
    stage_files.write_text(json.dumps(entries) + "\n")


def as_post_ticket(issue, platform):
    data = json.loads(issue.read_text())
    data["labels"] = ["room:distribution", "stage:post", "tracked", f"unit:ep01/{platform}"]
    issue.write_text(json.dumps(data, indent=1) + "\n")


class FakeResponse:
    def __init__(self, status, headers, body):
        self.status, self.headers, self._body = status, headers, io.BytesIO(body)

    def read(self, size=-1):
        return self._body.read(size)

    def close(self):
        pass


class FakeOpener:
    """Logs ``(method, URL, headers, body)`` of each request and answers it from the scenario."""

    def __init__(self, scenario):
        self.scenario, self.requests, self.unnamed = scenario, [], []

    def open(self, request, timeout=None):
        method, url = request.get_method(), request.full_url
        body = request.data
        if hasattr(body, "read"):
            body = body.read()
        headers = {name.lower(): value for name, value in request.header_items()}
        self.requests.append((method, url, headers, body))
        if (method, url) not in self.scenario:
            self.unnamed.append((method, url))
            raise AssertionError(f"no answer for {method} {url}")
        found = self.scenario[(method, url)]
        payload = found["body"]
        if not isinstance(payload, bytes):
            payload = b"" if payload == "" else json.dumps(payload).encode()
        message = email.message.Message()
        for name, value in found["headers"].items():
            message[name] = value
        if not 200 <= found["status"] < 300:
            raise urllib.error.HTTPError(url, found["status"], "error", message, io.BytesIO(payload))
        return FakeResponse(found["status"], message, payload)


class World:
    """A production on `trunk`, a recorded GitHub, a state directory and the key file, all temporary.

    ``inputs`` are the job's inputs, ``(path, text)``, each committed on `trunk` when ``text`` is not None;
    by default the request and the manifest."""

    def __init__(self, test, request=None, key="\n".join(KEY_LINES) + "\n", manifest=MANIFEST, inputs=None):
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
        as_post(self.root / ".factory" / "pipeline.json")
        self.git("init", "-q", "-b", "trunk")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "the production")
        shutil.copytree(RUN / "github", self.dir)
        as_post_ticket(self.dir / "issues" / f"{TICKET}.json", "youtube")
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
        self.request_text = json.dumps(clean_request() if request is None else request, indent=1, sort_keys=True) + "\n"
        self.manifest_text = manifest
        found = []
        for path, text in inputs or [(REQUEST_PATH, self.request_text), (MANIFEST_PATH, manifest)]:
            if text is not None:
                self.commit(path, text)
            found.append({"path": path, "sha256": hashlib.sha256((self.root / path).read_bytes()).hexdigest()})
        job = {"caller": "renderer", "expected": {"compute_min": 0, "usd": 0}, "inputs": found,
               "ticket": TICKET, "tool": TOOL}
        self.job_text = json.dumps(job, indent=1)
        self.job = str(self.write(JOB_PATH, self.job_text))
        self.digest = digest_of(self.job)
        code, out, err = self._main(cmd_gate.main, [self.job, "--root", str(self.root), "--apply"])
        test.assertEqual(code, 0, out + err)
        self.branch_head = gitstate.head(self.root, BRANCH)
        self.opener = None

    def git(self, *args):
        return gitstate.git(self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", *args)

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

    def run(self, scenario=None, apply=True):
        """``(exit, stdout, stderr)`` of ``factory run`` with the fake opener answering ``scenario``."""
        self.opener = FakeOpener(clean_scenario() if scenario is None else scenario)
        with mock.patch.object(urllib.request, "build_opener", return_value=self.opener) as built:
            result = self._main(cmd_run.main, [self.job, "--root", str(self.root)] + (["--apply"] if apply else []))
        self.built = built
        self.test.assertEqual(self.opener.unnamed, [])
        return result

    def sent(self):
        return [(method, url) for method, url, _, _ in self.opener.requests]

    def job_dir(self):
        return self.state / "jobs" / self.digest

    def record_text(self):
        path = self.job_dir() / "record" / "post.json"
        return path.read_text() if path.is_file() else None

    def post(self):
        return json.loads(self.record_text())

    def ledger_text(self):
        return gitstate.show(self.root, "trunk", ledger.PATH) or ""

    def runs(self):
        return [r for r in ledger.read(self.ledger_text()) if r["kind"] == "run"] if self.ledger_text() else []

    def manifest(self):
        return gitstate.show(self.root, BRANCH, gitstate.MANIFEST)


def publish_findings(test, w, record):
    """``(rule, message)`` of the publish check on ``record``, the world's manifest, request and job, sorted."""
    tmp = Path(tempfile.mkdtemp())
    test.addCleanup(shutil.rmtree, tmp)
    for rel, text in ((MANIFEST_PATH, w.manifest_text), (REQUEST_PATH, w.request_text), (JOB_PATH, w.job_text),
                      (RECORD_PATH, json.dumps(record, indent=1, sort_keys=True) + "\n"),
                      ("rulebook/youtube.json", json.dumps(RULEBOOK, indent=1) + "\n")):
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp / rel).write_text(text)
    return sorted((f.rule, f.message) for f in publish.check(tmp))


def succeeded(scenario):
    """``scenario`` with the read's processing ``succeeded``."""
    scenario[("GET", READ_URL)]["body"]["items"][0]["processingDetails"]["processingStatus"] = "succeeded"
    return scenario


def failed(world, reason):
    return f"run: tool {TOOL} failed: {reason}\n"


def line(world, verdict):
    return f"run job {world.digest}: {verdict} usd 0 compute_min 0\n"


class UploadTest(unittest.TestCase):
    def assert_failed(self, w, scenario, reason):
        code, out, err = w.run(scenario)
        self.assertEqual((code, out, err), (1, line(w, "FAILED"), failed(w, reason)))
        self.assertEqual([r["verdict"] for r in w.runs()], ["FAILED"])
        self.assertIsNone(w.record_text())
        self.assertEqual(gitstate.head(w.root, BRANCH), w.branch_head)

    def assert_done(self, w, scenario):
        """The run is DONE and its record is on the branch; the record."""
        code, out, err = w.run(scenario)
        self.assertEqual((code, out, err), (0, line(w, "DONE"), ""))
        self.assertEqual(posts.answer_problems(w.post()), [])
        self.assertEqual(gitstate.show(w.root, BRANCH, RECORD_PATH), w.record_text())
        return w.post()

    def test_clean_upload(self):
        w = World(self)
        code, out, err = w.run()
        self.assertEqual((code, out, err), (0, line(w, "DONE"), ""))
        self.assertEqual(w.sent(), [("GET", LINK), ("POST", TOKEN_URL), ("POST", UPLOAD_URL), ("PUT", LOCATION),
                                    ("GET", READ_URL)])
        # One opener, with no proxy and no redirect.
        self.assertEqual(w.built.call_count, 1)
        handlers = w.built.call_args.args
        self.assertEqual([type(h) for h in handlers][0], urllib.request.ProxyHandler)
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsNone(handlers[1].redirect_request(None, None, 302, "", {}, "https://elsewhere.invalid/"))
        requests = w.opener.requests
        self.assertEqual(sorted(urllib.parse.parse_qsl(requests[1][3].decode())),
                         [("client_id", KEY_LINES[0]), ("client_secret", KEY_LINES[1]),
                          ("grant_type", "refresh_token"), ("refresh_token", KEY_LINES[2])])
        auth = "Bearer " + ACCESS_TOKEN
        init = requests[2]
        self.assertEqual({k: init[2][k] for k in ("authorization", "content-type", "x-upload-content-length",
                                                  "x-upload-content-type")},
                         {"authorization": auth, "content-type": "application/json; charset=UTF-8",
                          "x-upload-content-length": "64", "x-upload-content-type": "video/*"})
        body = json.loads(init[3])
        self.assertEqual(init[3].decode(), json.dumps(body, sort_keys=True))
        self.assertEqual(body["status"], {"containsSyntheticMedia": True, "privacyStatus": "private"})
        self.assertEqual(body["snippet"], {"description": "An invented episode.", "title": "Episode one"})
        put = requests[3]
        self.assertEqual((put[2]["authorization"], put[2]["content-type"], put[2]["content-length"], put[3]),
                         (auth, "video/*", "64", VIDEO))
        self.assertEqual(requests[4][2]["authorization"], auth)
        self.assertEqual(list((w.job_dir() / "out").iterdir()), [])
        self.assertEqual(sorted(p.name for p in (w.job_dir() / "record").iterdir()), ["post.json"])
        record = clean_record(w.digest)
        text = json.dumps(record, indent=1, sort_keys=True) + "\n"
        self.assertEqual(w.record_text(), text)
        self.assertEqual(posts.answer_problems(w.post()), [])
        # The branch's new commit adds only the record, at the post's path.
        self.assertEqual(w.git("log", "--format=%s", f"{w.branch_head}..{BRANCH}").splitlines(),
                         [f"manifest: job {w.digest[:12]}"])
        self.assertEqual(w.git("diff", "--name-status", w.branch_head, BRANCH).splitlines(), ["A\t" + RECORD_PATH])
        self.assertEqual(gitstate.show(w.root, BRANCH, RECORD_PATH), text)
        self.assertIsNone(w.manifest())
        self.assertEqual([r["verdict"] for r in w.runs()], ["DONE"])
        self.assertEqual(publish_findings(self, w, w.post()),
                         [("publish-processed", "post inventedVid01 is not processed")])

    def test_a_processed_upload_passes_the_publish_check(self):
        w = World(self)
        post = self.assert_done(w, succeeded(clean_scenario()))
        self.assertEqual(post, clean_record(w.digest, processed=True))
        self.assertEqual(publish_findings(self, w, post), [])

    def test_seeded_answers(self):
        """The platform's answer is recorded as it is, and the publish check judges it."""
        def dropped(status):
            del status["containsSyntheticMedia"]

        cases = (
            ("disclosure dropped", dropped, {"fields": {}},
             [("publish-disclosure", "post inventedVid01 has no disclosure field status.containsSyntheticMedia")]),
            ("disclosure false", lambda status: status.update(containsSyntheticMedia=False),
             {"fields": {"status.containsSyntheticMedia": False}},
             [("publish-disclosure", "post inventedVid01 sets status.containsSyntheticMedia to false, not true")]),
            ("public", lambda status: status.update(privacyStatus="public"), {"visibility": "public"},
             [("publish-visibility", "post inventedVid01 is public, not private")]),
        )
        for name, change, changes, findings in cases:
            with self.subTest(name):
                w = World(self)
                scenario = succeeded(clean_scenario())
                change(scenario[("PUT", LOCATION)]["body"]["status"])
                post = self.assert_done(w, scenario)
                self.assertEqual(post, clean_record(w.digest, processed=True, **changes))
                self.assertEqual(publish_findings(self, w, post), findings)
        with self.subTest("privacyStatus none"):
            w = World(self)
            scenario = succeeded(clean_scenario())
            scenario[("PUT", LOCATION)]["body"]["status"]["privacyStatus"] = "none"
            self.assert_failed(w, scenario, "upload failed")

    def test_wrong_hash(self):
        w = World(self)
        post = self.assert_done(w, succeeded(clean_scenario()))
        post["sha256"] = OTHER_SHA
        self.assertEqual(posts.answer_problems(post), [])
        self.assertIn(("publish-hash", f"post inventedVid01 has sha256 {OTHER_SHA[:12]}, not the {SHA[:12]} of "
                                       "media:reel-ep01"), publish_findings(self, w, post))

    def test_bad_request(self):
        missing = clean_request()
        del missing["description"]
        cases = {
            "a key missing": {"request": missing},
            "a sixth key": {"request": {**clean_request(), "privacyStatus": "public"}},
            "the old request": {"request": {"bytes": 64, "description": "An invented episode.", "link": LINK,
                                            "sha256": SHA, "title": "Episode one"}},
            "video not media:<id>": {"request": {**clean_request(), "video": "reel-ep01"}},
            "video media:": {"request": {**clean_request(), "video": "media:"}},
            "captions not media:<id>": {"request": {**clean_request(), "captions": "captions-ep01"}},
            "captions with no row": {"manifest": MANIFEST_HEADER + VIDEO_ROW},
            "captions with two rows": {"manifest": MANIFEST + CAPTIONS_ROW},
            "video with no row": {"manifest": MANIFEST_HEADER + CAPTIONS_ROW},
            "one input only": {"inputs": [(REQUEST_PATH, json.dumps(clean_request()) + "\n")]},
            "a second input elsewhere": {"inputs": [(REQUEST_PATH, json.dumps(clean_request()) + "\n"),
                                                    ("media/other.tsv", MANIFEST)]},
            "link ending with another sha256": {"request": {**clean_request(),
                                                            "link": f"https://media.example.invalid/pub/{OTHER_SHA}"}},
            "link not ending in the sha256": {"request": {**clean_request(),
                                                          "link": f"https://media.example.invalid/pub/{SHA}x"}},
            "http off loopback": {"request": {**clean_request(), "link": f"http://media.example.invalid/pub/{SHA}"}},
        }
        for name, options in cases.items():
            with self.subTest(name):
                w = World(self, **options)
                self.assert_failed(w, clean_scenario(), "request is not an upload request")
                self.assertEqual(w.sent(), [])

    def test_the_file(self):
        other = bytes(reversed(VIDEO))
        self.assertEqual(len(other), len(VIDEO))
        for name, served, reason in (("404", video_answer(404, b"not found"), "public link unreachable"),
                                     ("other bytes", video_answer(200, other), "public link serves other bytes")):
            with self.subTest(name):
                w = World(self)
                scenario = clean_scenario()
                scenario[("GET", LINK)] = served
                self.assert_failed(w, scenario, reason)
                self.assertEqual(w.sent(), [("GET", LINK)])

    def assert_down(self, w, scenario):
        code, out, err = w.run(scenario)
        self.assertEqual((code, out, err), (2, "", f"run: resource {RESOURCE} is down (#5)\n"))
        self.assertEqual(w.runs(), [])
        self.assertFalse(os.path.lexists(w.job_dir()))
        return out, err

    def test_down(self):
        with self.subTest("a key file of two lines"):
            w = World(self, key="\n".join(KEY_LINES[:2]) + "\n")
            self.assert_down(w, clean_scenario())
            self.assertEqual(w.sent(), [])
        with self.subTest("invalid grant"):
            w = World(self)
            scenario = clean_scenario()
            scenario[("POST", TOKEN_URL)] = answer("token-invalid-grant")
            self.assert_down(w, scenario)
            self.assertEqual(w.sent(), [("GET", LINK), ("POST", TOKEN_URL)])

    def test_upload_errors(self):
        put_error = answer("put-ok")
        put_error["status"] = 500
        for name, key, found, reason in (("init-quota", ("POST", UPLOAD_URL), answer("init-quota"), "upload refused"),
                                         ("put-error", ("PUT", LOCATION), put_error, "upload failed")):
            with self.subTest(name):
                w = World(self)
                scenario = clean_scenario()
                scenario[key] = found
                self.assert_failed(w, scenario, reason)

    def test_a_failed_read_is_still_done(self):
        w = World(self)
        scenario = clean_scenario()
        scenario[("GET", READ_URL)]["body"] = {"items": []}
        self.assertEqual(self.assert_done(w, scenario), clean_record(w.digest))
        self.assertEqual([r["verdict"] for r in w.runs()], ["DONE"])

    def test_the_resource_checks(self):
        with self.subTest("YOUTUBE_OAUTH_APP=testing"):
            w = World(self)
            w.set_env("YOUTUBE_OAUTH_APP", "testing")
            code, out, err = w.run()
            self.assertEqual((code, out, err), (2, "", f"run: resource {RESOURCE} is not usable: setting "
                                                         "YOUTUBE_OAUTH_APP is not one of: production\n"))
            self.assertEqual(w.sent(), [])
        with self.subTest("no key file"):
            w = World(self)
            w.key_file.unlink()
            code, out, err = w.run()
            self.assertEqual((code, out, err), (2, "", f"run: resource {RESOURCE} is not usable: secret "
                                                         f"{RESOURCE}: missing\n"))
            self.assertEqual(w.sent(), [])

    def test_no_secret_leaks(self):
        put_public = clean_scenario()
        put_public[("PUT", LOCATION)]["body"]["status"]["privacyStatus"] = "public"
        invalid_grant = clean_scenario()
        invalid_grant[("POST", TOKEN_URL)] = answer("token-invalid-grant")
        two_lines = "\n".join(KEY_LINES[:2]) + "\n"
        for name, key, scenario, exit_code in (("clean", None, clean_scenario(), 0),
                                               ("not private", None, put_public, 0),
                                               ("two lines", two_lines, clean_scenario(), 2),
                                               ("invalid grant", None, invalid_grant, 2)):
            with self.subTest(name):
                w = World(self) if key is None else World(self, key=key)
                code, out, err = w.run(scenario)
                self.assertEqual(code, exit_code, out + err)
                self.assertEqual(w.record_text() is not None, exit_code < 2)
                texts = [out, err, w.ledger_text(), w.git("log", "-p", "--all")]
                for rel in ("record/post.json", "result.json"):
                    if (w.job_dir() / rel).is_file():
                        texts.append((w.job_dir() / rel).read_text())
                for secret in KEY_LINES + [ACCESS_TOKEN]:
                    for text in texts:
                        self.assertNotIn(secret, text)

    def test_shape_and_interface(self):
        names = sorted(path.stem for path in ANSWERS.glob("*.json"))
        self.assertEqual(names, ["init-ok", "init-quota", "put-ok", "read-ok", "token-invalid-grant", "token-ok"])
        for name in names:
            with self.subTest(name):
                found = answer(name)
                self.assertEqual(sorted(found), ["body", "headers", "read_at", "reference", "status"])
                self.assertTrue(found["reference"].startswith("https://developers.google.com/"))
                self.assertEqual(found["read_at"], "2026-11-13")
        tool = cmd_run._load_tool(TOOL)
        self.assertEqual(tool.RESOURCE, RESOURCE)
        self.assertEqual(tool.REASONS, REASONS)
        self.assertEqual(tool.REASONS[1:3], (media.LINK_UNREACHABLE, media.OTHER_BYTES_AT_LINK))
        w = World(self)
        code, out, err = w.run(apply=False)
        self.assertEqual((code, out, err), (0, f"run job {w.digest}: would call {TOOL}\n", ""))
        self.assertEqual(w.sent(), [])


if __name__ == "__main__":
    unittest.main()
