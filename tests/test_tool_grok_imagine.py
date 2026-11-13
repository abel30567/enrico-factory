"""Tests for kit/lib/tools/grok-imagine.py: the shipped tool and register, through `factory run`.

The world is the one of tests/test_run.py, with the shipped tools and register in place of the fakes. It
commits the request of tests/fixtures/tool_grok_imagine/request.json on `trunk`, writes the job, passes it with
the real gate and runs it with the real `factory run`. The proxy is one `ThreadingHTTPServer` on 127.0.0.1,
port 0, in a thread: it answers every `POST` with the status and body of one answer fixture and records the
path, the `Authorization` header and the JSON body. Nothing reaches a host other than 127.0.0.1.
"""

import base64
import hashlib
import http.server
import json
import socket
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from kit.lib import cmd_run, down, gitstate, ledger, lint, resources  # noqa: E402
from test_run import BRANCH, TICKET, World, line  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "tool_grok_imagine"
TOOL, RESOURCE = "grok-imagine", "gen.image.login"
TOKEN = "invented-proxy-client-token-7a1f"
REQUEST = "episodes/ep01/sequences/q1/shots/requests/sh010.json"
PROMPT = "A lantern on a pier in fog at dawn."
HEADER = "id\tsha256\tbytes\tsource\n"
DOWN = f"run: resource {RESOURCE} is down (#5)\n"
BODY = json.dumps({"model": "grok-imagine-image-2.0", "n": 1, "prompt": PROMPT, "response_format": "b64_json"},
                  sort_keys=True)


def answer(name):
    """``(status, body bytes)`` of an answer fixture."""
    data = json.loads((FIX / f"{name}.json").read_text())
    return data["status"], json.dumps(data["body"]).encode()


def free_port():
    """A loopback port nothing listens on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Proxy:
    """The proxy on 127.0.0.1: ``answer`` is the ``(status, body)`` of every POST; ``requests`` is every
    request seen, ``{path, authorization, body}`` with ``body`` the request's text."""

    def __init__(self, test, answer):
        self.answer, self.requests, self.lock = answer, [], threading.Lock()
        proxy = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode("utf-8", "replace")
                with proxy.lock:
                    proxy.requests.append({"path": self.path, "authorization": self.headers.get("Authorization"),
                                           "body": body})
                    status, data = proxy.answer
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        test.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class ImageTest(unittest.TestCase):
    def world(self, answer_of, request=None, url=None):
        """The world, the proxy answering ``answer_of`` and the passed job; ``(world, proxy, job, hash)``."""
        w = World(self)
        for patch in (mock.patch.object(cmd_run, "TOOLS_DIR", ROOT / "kit" / "lib" / "tools"),
                      mock.patch.object(resources, "REGISTER", ROOT / "kit" / "resources.json")):
            patch.start()
            self.addCleanup(patch.stop)
        proxy = Proxy(self, answer_of)
        secret = w.secrets / "proxy.client"
        secret.write_text(TOKEN + "\n")
        secret.chmod(0o600)
        w.local(STATE_DIR=w.state, SECRETS_DIR=w.secrets, PROXY_URL=url or proxy.url)
        text = (FIX / "request.json").read_text() if request is None else json.dumps(request) + "\n"
        w.commit(REQUEST, text)
        inputs = [{"path": REQUEST, "sha256": hashlib.sha256(text.encode()).hexdigest()}]
        job = w.job("image.json", tool=TOOL, inputs=inputs, expected={"usd": 0, "compute_min": 1})
        return w, proxy, job, w.passed(job)

    def assert_failed(self, w, job, digest, reason):
        code, out, err = w.run(job)
        self.assertEqual((code, out, err), (1, line(digest, "FAILED", "0", "0") + "\n",
                                            f"run: tool {TOOL} failed: {reason}\n"))
        [row] = w.rows("run")
        self.assertEqual((row["tool"], row["verdict"]), (TOOL, "FAILED"))
        self.assertIsNone(w.manifest())

    def assert_down(self, w, job, digest):
        """The first run files the issue and the second is refused by it; the GitHub log entries added."""
        before = w.log()
        self.assertEqual(w.run(job), (2, "", DOWN))
        self.assertEqual(w.rows("run"), [])
        self.assertIsNone(w.manifest())
        self.assertFalse(w.job_dir(digest).exists())
        added = w.log()[len(before):]
        self.assertEqual(w.run(job), (2, "", DOWN))
        self.assertEqual(w.log()[len(before):], added)
        return added

    def test_clean(self):
        w, proxy, job, digest = self.world(answer("ok"))
        self.assertEqual(w.run(job), (0, line(digest, "DONE", "0", "0") + "\n", ""))
        self.assertEqual(proxy.requests, [{"path": "/v1/images/generations", "authorization": f"Bearer {TOKEN}",
                                           "body": BODY}])
        png = base64.b64decode(json.loads((FIX / "ok.json").read_text())["body"]["data"][0]["b64_json"])
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(w.manifest(), HEADER + f"{digest[:16]}-image.png\t{hashlib.sha256(png).hexdigest()}\t"
                                                f"{len(png)}\tjob {digest}\n")
        tree = w.tmp / "exported"
        gitstate.export(w.root, BRANCH, tree)
        result = lint.run(tree, ["media"])["media"]
        self.assertEqual((result.status, result.findings), ("PASS", []))
        [row] = w.rows("run")
        self.assertEqual(ledger.line(row).split("\t")[5:10], [TOOL, "0", "0", "renderer", "DONE"])

    def test_login_ended(self):
        w, proxy, job, digest = self.world(answer("login-ended"))
        added = self.assert_down(w, job, digest)
        self.assertEqual([(e["op"], e["number"], e["title"], e["labels"]) for e in added],
                         [("create_issue", 5, f"[DOWN] {RESOURCE}", [down.LABEL])])
        self.assertEqual(added[0]["body"].splitlines()[:3], [
            f"Resource: {RESOURCE}", f"Cause: tool {TOOL} could not use it; nothing was spent",
            f"Seen by: run job {digest[:12]} on #{TICKET}"])
        self.assertEqual(len(proxy.requests), 1)

    def test_the_pause_without_apply(self):
        w, proxy, job, digest = self.world(answer("ok"))
        number = w.gh.create_issue(down.title(RESOURCE), "Resource: gen.image.login\n", [down.LABEL])
        self.assertEqual(number, 5)
        self.assertEqual(w.run(job, apply=False), (2, "", DOWN))
        self.assertEqual(w.run(job), (2, "", DOWN))
        self.assertEqual(proxy.requests, [])
        self.assertEqual(w.rows("run"), [])
        w.edit("issues", 5, state="closed")
        self.assertEqual(w.run(job, apply=False), (0, f"run job {digest}: would call {TOOL}\n", ""))
        self.assertEqual(proxy.requests, [])

    def test_proxy_unreachable(self):
        w, proxy, job, digest = self.world(answer("ok"), url=f"http://127.0.0.1:{free_port()}")
        added = self.assert_down(w, job, digest)
        self.assertEqual([(e["op"], e["number"], e["title"]) for e in added],
                         [("create_issue", 5, f"[DOWN] {RESOURCE}")])
        self.assertEqual(proxy.requests, [])

    def test_failures(self):
        cases = [("refused", answer("refused"), None, 1),
                 ("no image", answer("no-image"), None, 1),
                 ("proxy error", (500, b"{}"), None, 1),
                 ("refs not supported", answer("ok"), {"for": "sh010", "prompt": PROMPT, "refs": ["media:x"]}, 0),
                 ("not a request", answer("ok"), {"prompt": PROMPT, "refs": []}, 0)]
        for reason, answer_of, request, calls in cases:
            with self.subTest(reason):
                w, proxy, job, digest = self.world(answer_of, request)
                self.assert_failed(w, job, digest, reason)
                self.assertEqual(len(proxy.requests), calls)

    def test_forbidden_and_out_of_usage_are_down(self):
        for status in (403, 429):
            with self.subTest(status):
                w, proxy, job, digest = self.world((status, b"{}"))
                self.assertEqual(w.run(job), (2, "", DOWN))
                self.assertEqual(w.rows("run"), [])
                self.assertEqual(len(proxy.requests), 1)


if __name__ == "__main__":
    unittest.main()
