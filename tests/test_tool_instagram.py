"""Tests for kit/lib/tools/instagram-upload.py: the shipped tool and register, through `factory run`.

The production is tests/fixtures/run/production, made into a git repository on branch `trunk` in a
temporary directory, with one branch per recorded pull, and GitHub a copy of tests/fixtures/run/github.
The world commits `media/manifest.tsv` (row `reel-ep01`) and the request of
tests/fixtures/tool_instagram/request.json on `trunk`, writes the job, passes it with the real gate and
runs it with the real `factory run`, the shipped tool and the shipped register.

The platform is one `ThreadingHTTPServer` on 127.0.0.1, port 0, in a thread. It serves the Reel's
invented bytes under `/pub/<sha256>` and the queued answers of tests/fixtures/tool_instagram/answers
under `/v27.0/`, records every request and answers 404 to anything not queued. `time.sleep` is patched.
Nothing reaches a host other than 127.0.0.1.
"""

import contextlib
import hashlib
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
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import cmd_gate, cmd_run, gitstate, ledger, resources  # noqa: E402

RUN = ROOT / "tests" / "fixtures" / "run"
FIX = ROOT / "tests" / "fixtures" / "tool_instagram"
NOW = "2026-11-02T09:00:00Z"
TICKET, PULL, BRANCH = 3, 4, "feat/RN-1-ep01"
TOOL = "instagram-upload"
ACCOUNT, CONTAINER, POST = "17841400000000001", "17890000000000010", "17920000000000020"
TOKEN = "invented-instagram-token-0b1c2d3e"
REEL = b"invented reel bytes, not a video"
REEL_SHA = hashlib.sha256(REEL).hexdigest()
REQUEST = "distribution/ep01/requests/reel.json"
MANIFEST = "media/manifest.tsv"
HEADER = "id\tsha256\tbytes\tsource\n"
ROW = f"reel-ep01\t{REEL_SHA}\t{len(REEL)}\tupload\n"
REASONS = ("bad request", "public link does not serve the reel", "platform unreachable", "platform refused",
           "container failed", "container not ready")
CHECKS = [{"kind": "secret", "name": "platform.instagram"}, {"kind": "setting", "name": "INSTAGRAM_TEST_ACCOUNT"},
          {"kind": "setting", "name": "MEDIA_PUBLIC_BASE"}]
DOWN = "run: resource platform.instagram is down (#5)\n"
MEDIA = ("POST", f"/v27.0/{ACCOUNT}/media")
STATUS = ("GET", f"/v27.0/{CONTAINER}?fields=status_code")
PUBLISH = ("POST", f"/v27.0/{ACCOUNT}/media_publish")
LINK = ("GET", f"/pub/{REEL_SHA}")


def answer(name):
    """``(status, body bytes)`` of a fixture answer."""
    data = json.loads((FIX / "answers" / f"{name}.json").read_text())
    return data["status"], json.dumps(data["body"]).encode()


def free_port():
    """A loopback port nothing listens on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def line(digest, verdict):
    return f"run job {digest}: {verdict} usd 0 compute_min 0\n"


class Platform:
    """The public link and the platform's API on 127.0.0.1. ``queue(method, path, *answers)`` adds answers,
    each served once in order; ``requests`` is every request, ``{method, path, body, authorization}``."""

    def __init__(self, test):
        self.answers, self.requests, self.lock = {}, [], threading.Lock()
        platform = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                platform.handle(self, "GET")

            def do_POST(self):
                platform.handle(self, "POST")

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        test.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def queue(self, method, path, *answers):
        with self.lock:
            self.answers.setdefault((method, path), []).extend(answers)

    def clean(self):
        self.queue(*MEDIA, answer("container"))
        self.queue(*STATUS, answer("status-in-progress"), answer("status-finished"))
        self.queue(*PUBLISH, answer("publish"))

    def handle(self, handler, method):
        length = int(handler.headers.get("Content-Length") or 0)
        body = handler.rfile.read(length).decode("utf-8", "replace")
        with self.lock:
            self.requests.append({"method": method, "path": handler.path, "body": body,
                                  "authorization": handler.headers.get("Authorization")})
            if (method, handler.path) == LINK:
                status, data = 200, REEL
            else:
                queued = self.answers.get((method, handler.path))
                status, data = queued.pop(0) if queued else (404, b"")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def sent(self):
        return [(r["method"], r["path"]) for r in self.requests]

    def api(self):
        return [r for r in self.requests if r["path"].startswith("/v27.0/")]


class World:
    """A production on `trunk` holding the manifest and the request, a recorded GitHub, a state directory,
    the token file and the platform, all temporary."""

    def __init__(self, test, account=ACCOUNT, token=TOKEN + "\n"):
        self.test = test
        self.platform = Platform(test)
        self.tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, self.tmp)
        self.root = self.tmp / "show"
        self.dir = self.tmp / "github"
        self.state = self.tmp / "state"
        self.secrets = self.tmp / "secrets"
        self.api = f"http://127.0.0.1:{self.platform.port}/v27.0"
        env = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                                           "FACTORY_GITHUB": str(self.dir), "FACTORY_NOW": NOW,
                                           "FACTORY_INSTAGRAM_API": self.api})
        sleep = mock.patch("time.sleep")
        env.start()
        test.addCleanup(env.stop)
        self.sleep = sleep.start()
        test.addCleanup(sleep.stop)
        shutil.copytree(RUN / "production", self.root)
        self.write(MANIFEST, HEADER + ROW)
        self.write(REQUEST, (FIX / "request.json").read_text())
        self.git("init", "-q", "-b", "trunk")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "the production")
        shutil.copytree(RUN / "github", self.dir)
        for path in sorted((self.dir / "pulls").glob("*.json")):
            self.branch(json.loads(path.read_text())["head_ref"])
        for path in sorted((self.dir / "comments").glob("*.json")):
            path.write_text(path.read_text().replace(f"{{head:{PULL}}}", gitstate.head(self.root, BRANCH)))
        self.secrets.mkdir()
        self.token(token)
        settings = {"STATE_DIR": self.state, "SECRETS_DIR": self.secrets,
                    "MEDIA_PUBLIC_BASE": f"http://127.0.0.1:{self.platform.port}/pub"}
        if account is not None:
            settings["INSTAGRAM_TEST_ACCOUNT"] = account
        (self.root / "production.local.env").write_text("".join(f"{k}={v}\n" for k, v in settings.items()))

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

    def token(self, text):
        path = self.secrets / "platform.instagram"
        path.write_text(text)
        path.chmod(0o600)

    def job(self, inputs=None, name="reel.json"):
        """A job file, untracked, whose inputs are ``inputs`` (default the request and the manifest), each
        ``(path, text)`` committed on `trunk` when its text is not None; its absolute path."""
        found = []
        for path, text in inputs or [(REQUEST, None), (MANIFEST, None)]:
            if text is not None:
                self.commit(path, text)
            found.append({"path": path, "sha256": sha((self.root / path).read_text())})
        job = {"caller": "renderer", "expected": {"compute_min": 0, "usd": 0}, "inputs": found,
               "ticket": TICKET, "tool": TOOL}
        return str(self.write("jobs/" + name, json.dumps(job, indent=1) + "\n"))

    def _main(self, main, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def passed(self, job):
        """``job`` passed by the real gate; its hash."""
        code, out, err = self._main(cmd_gate.main, [job, "--root", str(self.root), "--apply"])
        self.test.assertEqual(code, 0, out + err)
        self.test.assertIn(": PASS ", out)
        text = json.loads(Path(job).read_text())
        return hashlib.sha256(json.dumps(text, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def run(self, job):
        return self._main(cmd_run.main, [job, "--root", str(self.root), "--apply"])

    def rows(self, kind=None):
        text = gitstate.show(self.root, "trunk", ledger.PATH)
        rows = [] if text is None else ledger.read(text)
        return [r for r in rows if kind is None or r["kind"] == kind]

    def job_dir(self, digest):
        return self.state / "jobs" / digest


class Case(unittest.TestCase):
    def world(self, **options):
        self.w = World(self, **options)
        return self.w

    def clean_run(self):
        """The clean job, gated and run with the clean answers; ``(digest, exit, out, err)``."""
        w = self.w
        w.platform.clean()
        job = w.job()
        digest = w.passed(job)
        return (digest,) + w.run(job)

    def assert_refused(self, result, err, api=None):
        """``(exit, out, err)`` is exit 2 with ``err``, no run row, and ``api`` requests (default none)."""
        code, out, got = result
        self.assertEqual((code, out, got), (2, "", err))
        self.assertEqual(self.w.rows("run"), [])
        self.assertEqual(self.w.platform.sent(), api or [])

    def assert_failed(self, result, digest, reason):
        """``(exit, out, err)`` is FAILED ``reason`` at no cost, recorded."""
        code, out, err = result
        self.assertEqual((code, out, err), (1, line(digest, "FAILED"), f"run: tool {TOOL} failed: {reason}\n"))
        [row] = self.w.rows("run")
        self.assertEqual(ledger.line(row).split("\t")[5:], [TOOL, "0", "0", "renderer", "FAILED", "-"])

    def failing(self, inputs=None, *queued):
        """Gate and run a job with ``inputs``, the platform answering ``queued`` ``(method, path, answer)``."""
        for method, path, found in queued:
            self.w.platform.queue(method, path, found)
        job = self.w.job(inputs)
        digest = self.w.passed(job)
        return digest, self.w.run(job)


class CleanTest(Case):
    """Behaviour 1: one Reel through `factory run`, five requests, `post.json`, one manifest row, one run row."""

    def test_clean_run(self):
        w = self.world()
        branch = gitstate.head(w.root, BRANCH)
        digest, code, out, err = self.clean_run()
        self.assertEqual((code, out, err), (0, line(digest, "DONE"), ""))
        self.assertEqual(w.platform.sent(), [LINK, MEDIA, STATUS, STATUS, PUBLISH])
        post = w.job_dir(digest) / "out" / "post.json"
        text = json.dumps({"account": ACCOUNT, "container": CONTAINER, "post": POST, "video": "media:reel-ep01"},
                          indent=1, sort_keys=True) + "\n"
        self.assertEqual(post.read_text(), text)
        self.assertEqual(sorted(p.name for p in post.parent.iterdir()), ["post.json"])
        self.assertEqual(w.git("diff", "--name-only", branch, BRANCH).split(), [MANIFEST])
        self.assertEqual(gitstate.show(w.root, BRANCH, MANIFEST), HEADER + ROW + "\t".join(
            [f"{digest[:16]}-post.json", sha(text), str(len(text)), f"job {digest}"]) + "\n")
        last = w.rows()[-1]
        self.assertEqual(ledger.line(last).split("\t"), [NOW, "run", digest, str(TICKET), "ep01", TOOL, "0", "0",
                                                         "renderer", "DONE", "-"])
        self.assertEqual(w.sleep.call_args_list, [mock.call(60)])

    def test_a_second_run_sends_nothing(self):
        w = self.world()
        digest, code, _, _ = self.clean_run()
        self.assertEqual(code, 0)
        sent = w.platform.sent()
        w.platform.clean()
        self.assertEqual(w.run(str(w.root / "jobs" / "reel.json")),
                         (0, line(digest, "DONE").rstrip("\n") + cmd_gate.ALREADY + "\n", ""))
        self.assertEqual(w.platform.sent(), sent)
        self.assertEqual(len(w.rows("run")), 1)


class RequestsTest(Case):
    """Behaviour 2: the container's body, the token only in the `Authorization` header of the API requests."""

    def assert_token_kept(self, out, err):
        w = self.w
        for request in w.platform.requests:
            self.assertNotIn(TOKEN, request["path"] + request["body"])
        for path in sorted(w.state.rglob("*")):
            if path.is_file():
                self.assertNotIn(TOKEN.encode(), path.read_bytes())
        self.assertNotIn(TOKEN, out + err)
        self.assertNotIn(TOKEN, w.git("log", "-p", "--all"))
        self.assertNotIn(TOKEN, "".join(p.read_text() for p in sorted(w.dir.rglob("*")) if p.is_file()))

    def test_the_container_and_the_header(self):
        w = self.world()
        _, code, out, err = self.clean_run()
        self.assertEqual(code, 0)
        link = f"http://127.0.0.1:{w.platform.port}/pub/{REEL_SHA}"
        [media] = [r for r in w.platform.requests if (r["method"], r["path"]) == MEDIA]
        self.assertEqual(media["body"], "caption=A+lantern+goes+out.&is_ai_generated=true&media_type=REELS"
                                        "&video_url=" + urllib.parse.quote_plus(link))
        [publish] = [r for r in w.platform.requests if (r["method"], r["path"]) == PUBLISH]
        self.assertEqual(publish["body"], "creation_id=" + CONTAINER)
        self.assertEqual([r["authorization"] for r in w.platform.api()], ["Bearer " + TOKEN] * 4)
        [public] = [r for r in w.platform.requests if (r["method"], r["path"]) == LINK]
        self.assertIsNone(public["authorization"])
        self.assert_token_kept(out, err)

    def test_a_token_with_a_carriage_return_is_down(self):
        w = self.world(token=TOKEN + "\r\n")
        job = w.job()
        w.passed(job)
        result = w.run(job)
        self.assert_refused(result, DOWN)
        self.assert_token_kept(result[1], result[2])


class AccountTest(Case):
    """Behaviour 3: the test account is a setting of digits."""

    def test_no_account(self):
        w = self.world(account=None)
        job = w.job()
        w.passed(job)
        self.assert_refused(w.run(job), "run: resource platform.instagram is not usable: "
                                        "setting INSTAGRAM_TEST_ACCOUNT is empty\n")

    def test_an_account_that_is_not_digits(self):
        w = self.world(account="test-account")
        job = w.job()
        digest = w.passed(job)
        self.assert_refused(w.run(job), DOWN)
        self.assertFalse(os.path.lexists(w.job_dir(digest)))

    def test_the_test_account(self):
        w = self.world()
        _, code, _, _ = self.clean_run()
        self.assertEqual(code, 0)
        self.assertEqual({r["path"].split("/")[2] for r in w.platform.requests if r["method"] == "POST"}, {ACCOUNT})


class TokenTest(Case):
    """Behaviour 4: an invalid token is `down`, and the job runs again under the same PASS."""

    def test_an_expired_token_then_a_good_one(self):
        w = self.world()
        w.platform.queue(*MEDIA, answer("error-token"))
        job = w.job()
        digest = w.passed(job)
        self.assert_refused(w.run(job), DOWN, [LINK, MEDIA])
        self.assertFalse(os.path.lexists(w.job_dir(digest)))
        w.platform.clean()
        issue = w.dir / "issues" / "5.json"
        issue.write_text(json.dumps({**json.loads(issue.read_text()), "state": "closed"}, indent=1) + "\n")
        self.assertEqual(w.run(job), (0, line(digest, "DONE"), ""))
        self.assertEqual(len(w.rows("job")), 1)
        self.assertEqual([r["verdict"] for r in w.rows("run")], ["DONE"])

    def test_a_token_of_two_lines(self):
        w = self.world(token=TOKEN + "\nmore\n")
        job = w.job()
        w.passed(job)
        self.assert_refused(w.run(job), DOWN)

    def test_a_token_with_no_final_newline(self):
        w = self.world(token=TOKEN)
        _, code, _, _ = self.clean_run()
        self.assertEqual(code, 0)
        self.assertEqual({r["authorization"] for r in w.platform.api()}, {"Bearer " + TOKEN})


class LinkTest(Case):
    """Behaviour 5: the public link must serve the Reel's bytes before the platform is called."""

    def test_a_size_one_byte_larger(self):
        w = self.world()
        row = f"reel-ep01\t{REEL_SHA}\t{len(REEL) + 1}\tupload\n"
        digest, result = self.failing([(REQUEST, None), (MANIFEST, HEADER + row)])
        self.assert_failed(result, digest, "public link does not serve the reel")
        self.assertEqual(w.platform.sent(), [LINK])

    def test_the_right_size(self):
        w = self.world()
        _, code, _, _ = self.clean_run()
        self.assertEqual(code, 0)
        self.assertEqual(w.platform.sent()[0], LINK)


class BadRequestTest(Case):
    """Behaviour 6: a request or a manifest the tool cannot read is `bad request`, and nothing is sent."""

    def assert_bad(self, inputs):
        digest, result = self.failing(inputs)
        self.assert_failed(result, digest, "bad request")
        self.assertEqual(self.w.platform.sent(), [])

    def request(self, **changes):
        data = json.loads((FIX / "request.json").read_text())
        data.update(changes)
        return json.dumps(data, sort_keys=True) + "\n"

    def test_a_third_key(self):
        self.world()
        self.assert_bad([(REQUEST, self.request(title="out")), (MANIFEST, None)])

    def test_a_video_without_media(self):
        self.world()
        self.assert_bad([(REQUEST, self.request(video="reel-ep01")), (MANIFEST, None)])

    def test_an_id_with_no_row(self):
        self.world()
        self.assert_bad([(REQUEST, self.request(video="media:reel-ep02")), (MANIFEST, None)])

    def test_two_rows_with_the_id(self):
        self.world()
        self.assert_bad([(REQUEST, None), (MANIFEST, HEADER + ROW + ROW)])

    def test_one_input_only(self):
        self.world()
        self.assert_bad([(REQUEST, None)])

    def test_a_second_input_elsewhere(self):
        self.world()
        self.assert_bad([(REQUEST, None), ("media/other.tsv", HEADER + ROW)])

    def test_the_request(self):
        w = self.world()
        _, code, _, _ = self.clean_run()
        self.assertEqual(code, 0)
        self.assertEqual(len(w.platform.api()), 4)


class ContainerTest(Case):
    """Behaviour 7: the container's status decides whether it is published."""

    def test_error_and_expired(self):
        for name in ("status-error", "status-expired"):
            with self.subTest(name):
                w = self.world()
                digest, result = self.failing(None, (*MEDIA, answer("container")), (*STATUS, answer(name)))
                self.assert_failed(result, digest, "container failed")
                self.assertEqual(w.platform.sent(), [LINK, MEDIA, STATUS])
                self.assertNotIn(PUBLISH, w.platform.sent())

    def test_never_ready(self):
        w = self.world()
        w.platform.queue(*STATUS, *[answer("status-in-progress")] * 5)
        digest, result = self.failing(None, (*MEDIA, answer("container")))
        self.assert_failed(result, digest, "container not ready")
        self.assertEqual(w.platform.sent(), [LINK, MEDIA] + [STATUS] * 5)
        self.assertEqual(w.sleep.call_args_list, [mock.call(60)] * 4)

    def test_published_already(self):
        w = self.world()
        published = (200, json.dumps({"status_code": "PUBLISHED", "id": CONTAINER}).encode())
        digest, result = self.failing(None, (*MEDIA, answer("container")), (*STATUS, published))
        self.assert_failed(result, digest, "platform refused")
        self.assertEqual(w.platform.sent(), [LINK, MEDIA, STATUS])

    def test_finished(self):
        w = self.world()
        _, code, _, _ = self.clean_run()
        self.assertEqual(code, 0)
        self.assertEqual(w.platform.sent()[-1], PUBLISH)


class RefusalTest(Case):
    """Behaviour 8: a refusal or no answer is a recorded failure at no cost."""

    def test_a_limit_on_the_publish(self):
        w = self.world()
        digest, result = self.failing(None, (*MEDIA, answer("container")), (*STATUS, answer("status-finished")),
                                      (*PUBLISH, answer("error-limit")))
        self.assert_failed(result, digest, "platform refused")
        self.assertEqual(w.platform.sent(), [LINK, MEDIA, STATUS, PUBLISH])

    def test_a_container_answer_that_is_not_json(self):
        w = self.world()
        digest, result = self.failing(None, (*MEDIA, (200, b"not json")))
        self.assert_failed(result, digest, "platform refused")
        self.assertEqual(w.platform.sent(), [LINK, MEDIA])

    def test_no_answer(self):
        w = self.world()
        os.environ["FACTORY_INSTAGRAM_API"] = f"http://127.0.0.1:{free_port()}/v27.0"
        digest, result = self.failing(None)
        self.assert_failed(result, digest, "platform unreachable")
        self.assertEqual(w.platform.sent(), [LINK])

    def test_the_answers(self):
        w = self.world()
        digest, code, out, _ = self.clean_run()
        self.assertEqual((code, out), (0, line(digest, "DONE")))
        self.assertEqual(w.rows("run")[-1]["verdict"], "DONE")


class SeamTest(Case):
    """Behaviour 9: `FACTORY_INSTAGRAM_API` replaces the API with a loopback URL, and only with one."""

    def test_a_seam_off_the_loopback(self):
        w = self.world()
        os.environ["FACTORY_INSTAGRAM_API"] = "https://graph.example.invalid/v27.0"
        w.platform.clean()
        job = w.job()
        w.passed(job)
        self.assert_refused(w.run(job), DOWN)

    def test_the_shipped_names(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("FACTORY_INSTAGRAM_API", None)
            tool = cmd_run._load_tool(TOOL)
            self.assertEqual(tool.API, "https://graph.facebook.com/v27.0")
            self.assertEqual(tool._api(), "https://graph.facebook.com/v27.0")
        self.assertEqual(tool.RESOURCE, "platform.instagram")
        self.assertEqual(tool.REASONS, REASONS)
        self.assertEqual((tool.POLLS, tool.WAIT), (5, 60))


class RegisterTest(unittest.TestCase):
    """Behaviour 10: the shipped register's checks of `platform.instagram`, as `factory doctor` prints them."""

    def doctor(self, settings, token=None):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        (tmp / "production.local.env").write_text(f"SECRETS_DIR={tmp}\n" + settings)
        if token is not None:
            (tmp / "platform.instagram").write_text(token)
            (tmp / "platform.instagram").chmod(0o600)
        done = subprocess.run([sys.executable, "kit/factory", "doctor", "--root", str(tmp), "--rooms", "distribution"],
                              cwd=ROOT, capture_output=True, text=True)
        lines = done.stdout.splitlines()
        start = next(i for i, l in enumerate(lines) if "\tplatform.instagram\t" in l)
        found = [lines[start]]
        for rest in lines[start + 1:]:
            if not rest.startswith("  "):
                break
            found.append(rest)
        return found

    def test_the_checks(self):
        register, problems = resources.load(resources.REGISTER)
        self.assertEqual(problems, [])
        [resource] = [r for r in register["resources"] if r["id"] == "platform.instagram"]
        self.assertEqual(resource["checks"], CHECKS)

    def test_doctor_with_nothing_set(self):
        self.assertEqual(self.doctor(""), [
            "missing\tplatform.instagram\tInstagram: Reels publishing and insights, one OAuth grant",
            "  secret platform.instagram: missing",
            "  setting INSTAGRAM_TEST_ACCOUNT is empty",
            "  setting MEDIA_PUBLIC_BASE is empty"])

    def test_doctor_with_everything_set(self):
        settings = f"INSTAGRAM_TEST_ACCOUNT={ACCOUNT}\nMEDIA_PUBLIC_BASE=http://127.0.0.1:9/pub\n"
        self.assertEqual(self.doctor(settings, TOKEN + "\n"), [
            "ok\tplatform.instagram\tInstagram: Reels publishing and insights, one OAuth grant"])


if __name__ == "__main__":
    unittest.main()
