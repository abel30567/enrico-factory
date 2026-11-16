"""Tests for kit/lib/tools/grok-imagine.py: the shipped tool and register, through `factory run`.

The world is the one of tests/test_run.py, with the shipped tools and register in place of the fakes. It
commits `media/manifest.tsv` on `trunk` and merges `trunk` into the pull's branch, commits the request of
tests/fixtures/tool_grok_imagine/request.json on `trunk`, writes the job, passes it with the real gate and runs
it with the real `factory run`. The proxy is one `ThreadingHTTPServer` on 127.0.0.1, port 0, in a thread: it
answers every `POST` with the status and body of one answer fixture and records the path, the `Authorization`
header and the JSON body; it is also the media store's public link, `MEDIA_PUBLIC_BASE`, answering
`GET /media/<sha256>` with the bytes it holds under that sha256 and 404 otherwise, and records each `GET` path.
Nothing reaches a host other than 127.0.0.1.
"""

import base64
import hashlib
import http.server
import importlib.util
import json
import socket
import struct
import sys
import threading
import unittest
import zlib
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from kit.lib import cmd_run, down, gitstate, ledger, lint, resources  # noqa: E402
from test_media import KEY_ID, REGION, SECRET, FakeStore  # noqa: E402
from test_run import BRANCH, TICKET, World, line  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "tool_grok_imagine"
TOOL, RESOURCE = "grok-imagine", "gen.image.login"
TOKEN = "invented-proxy-client-token-7a1f"
REQUEST = "episodes/ep01/sequences/q1/shots/requests/sh010.json"
MANIFEST = "media/manifest.tsv"
PROMPT = "A lantern on a pier in fog at dawn."
HEADER = "id\tsha256\tbytes\tsource\n"
DOWN = f"run: resource {RESOURCE} is down (#5)\n"
BODY = json.dumps({"model": "grok-imagine-image-2.0", "n": 1, "prompt": PROMPT, "response_format": "b64_json"},
                  sort_keys=True)
COSTED = {"usd": 0.1, "compute_min": 1}


def answer(name):
    """``(status, body bytes)`` of an answer fixture."""
    data = json.loads((FIX / f"{name}.json").read_text())
    return data["status"], json.dumps(data["body"]).encode()


def with_usage(usage):
    """``(status, body bytes)`` of refs-ok.json with its ``usage`` replaced, or dropped when ``usage`` is None."""
    data = json.loads((FIX / "refs-ok.json").read_text())["body"]
    data.pop("usage")
    return 200, json.dumps(data if usage is None else {**data, "usage": usage}).encode()


def png(rgb):
    """A 1-pixel PNG of the colour ``rgb``."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00" + bytes(rgb))) + chunk(b"IEND", b""))


def sha(data):
    return hashlib.sha256(data).hexdigest()


AMA = base64.b64decode(json.loads((FIX / "ok.json").read_text())["body"]["data"][0]["b64_json"])
TIDE = png((20, 90, 160))
PICTURES = {"ama-ref": AMA, "tide-ref": TIDE}
# The public-link GET of the preview comment's picture (``kit/lib/handoff.py``): its ``media.put`` of the image.
PREVIEW_GET = f"/media/{sha(AMA)}"


def row(name, data):
    return f"{name}\t{sha(data)}\t{len(data)}\tsources/{name}.png\n"


MANIFEST_TEXT = HEADER + "".join(row(name, data) for name, data in PICTURES.items())
REEF = png((200, 120, 40))
MOSS = png((60, 140, 70))
MANY = {**PICTURES, "reef-ref": REEF, "ama-two": AMA, "moss-ref": MOSS}
MANY_TEXT = HEADER + "".join(row(name, data) for name, data in MANY.items())
MANY_HELD = {sha(data): data for data in MANY.values()}


def refs_request(*refs):
    return {"for": "sh010", "prompt": PROMPT, "refs": list(refs)}


def form(key, pictures):
    """The ``key`` body of refs-form.json, ``<ref n>`` and ``<prompt>`` filled in; as the tool sends it."""
    def fill(value):
        if isinstance(value, dict):
            return {k: fill(v) for k, v in value.items()}
        if isinstance(value, list):
            return [fill(v) for v in value]
        if value == "<prompt>":
            return PROMPT
        if isinstance(value, str) and value.startswith("<ref "):
            return "data:image/png;base64," + base64.b64encode(pictures[int(value[5:-1]) - 1]).decode()
        return value
    return json.dumps(fill(json.loads((FIX / "refs-form.json").read_text())[key]), sort_keys=True)


def free_port():
    """A loopback port nothing listens on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Proxy:
    """The proxy on 127.0.0.1: ``answer`` is the ``(status, body)`` of every POST; ``requests`` is every
    POST seen, ``{path, authorization, body}`` with ``body`` the request's text. ``held`` is the bytes the
    media link serves by sha256; ``gets`` is every GET path seen."""

    def __init__(self, test, answer, held):
        self.answer, self.requests, self.held, self.gets, self.lock = answer, [], held, [], threading.Lock()
        proxy = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, data, kind):
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                with proxy.lock:
                    proxy.gets.append(self.path)
                    data = proxy.held.get(self.path[len("/media/"):]) if self.path.startswith("/media/") else None
                if data is None:
                    self.reply(404, b"", "text/plain")
                else:
                    self.reply(200, data, "application/octet-stream")

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode("utf-8", "replace")
                with proxy.lock:
                    proxy.requests.append({"path": self.path, "authorization": self.headers.get("Authorization"),
                                           "body": body})
                    status, data = proxy.answer
                self.reply(status, data, "application/json")

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        test.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class ImageTest(unittest.TestCase):
    def world(self, answer_of, request=None, url=None, media_base=None, extra=None, manifest=MANIFEST_TEXT,
              held=None, expected=None):
        """The world, the proxy answering ``answer_of`` and the passed job; ``(world, proxy, job, hash)``.
        ``extra`` is ``[(path, text)]``, the inputs after the request: by default the manifest when the
        request has refs. ``manifest`` is the text committed and given as the manifest input."""
        w = World(self)
        for patch in (mock.patch.object(cmd_run, "TOOLS_DIR", ROOT / "kit" / "lib" / "tools"),
                      mock.patch.object(resources, "REGISTER", ROOT / "kit" / "resources.json")):
            patch.start()
            self.addCleanup(patch.stop)
        proxy = Proxy(self, answer_of, {sha(data): data for data in PICTURES.values()} if held is None else held)
        secret = w.secrets / "proxy.client"
        secret.write_text(TOKEN + "\n")
        secret.chmod(0o600)
        self.store = FakeStore(self)
        key = w.secrets / "store.media"
        key.write_text(f"{KEY_ID}\n{SECRET}\n{REGION}\n")
        key.chmod(0o600)
        w.local(STATE_DIR=w.state, SECRETS_DIR=w.secrets, PROXY_URL=url or proxy.url,
                MEDIA_PUBLIC_BASE=media_base or proxy.url + "media", MEDIA_STORE=self.store.url("/b"))
        self.manifest_text = manifest
        w.commit(MANIFEST, manifest)
        self.merge_trunk(w)
        text = (FIX / "request.json").read_text() if request is None else json.dumps(request) + "\n"
        w.commit(REQUEST, text)
        if extra is None:
            extra = [(MANIFEST, manifest)] if request is not None and request.get("refs") else []
        inputs = [{"path": REQUEST, "sha256": sha(text.encode())}]
        for path, other in extra:
            if path != MANIFEST:
                w.commit(path, other)
            inputs.append({"path": path, "sha256": sha(other.encode())})
        job = w.job("image.json", tool=TOOL, inputs=inputs, expected=expected or {"usd": 0, "compute_min": 1})
        return w, proxy, job, w.passed(job)

    def merge_trunk(self, w):
        """Merge `trunk` into the pull's branch; the recorded comments that named its old head name the new one."""
        old = w.head(BRANCH)
        w.git("checkout", "-q", BRANCH)
        w.git("merge", "-q", "--no-edit", "trunk")
        w.git("checkout", "-q", "trunk")
        for path in sorted((w.dir / "comments").glob("*.json")):
            path.write_text(path.read_text().replace(old, w.head(BRANCH)))

    def assert_failed(self, w, job, digest, reason):
        code, out, err = w.run(job)
        self.assertEqual((code, out, err), (1, line(digest, "FAILED", "0", "0") + "\n",
                                            f"run: tool {TOOL} failed: {reason}\n"))
        [row_] = w.rows("run")
        self.assertEqual((row_["tool"], row_["verdict"], row_["usd"], row_["compute_min"]),
                         (TOOL, "FAILED", "0", "0"))
        self.assertEqual(w.manifest(), self.manifest_text)

    def assert_down(self, w, job, digest):
        """The first run files the issue and the second is refused by it; the GitHub log entries added."""
        before = w.log()
        self.assertEqual(w.run(job), (2, "", DOWN))
        self.assertEqual(w.rows("run"), [])
        self.assertEqual(w.manifest(), self.manifest_text)
        self.assertFalse(w.job_dir(digest).exists())
        added = w.log()[len(before):]
        self.assertEqual(w.run(job), (2, "", DOWN))
        self.assertEqual(w.log()[len(before):], added)
        return added

    def assert_done(self, w, job, digest, usd):
        """The run is DONE at ``usd``; its one image has its manifest row on the branch."""
        self.assertEqual(w.run(job), (0, line(digest, "DONE", usd, "0") + "\n", ""))
        image = AMA
        self.assertEqual(w.manifest(), self.manifest_text + f"{digest[:16]}-image.png\t{sha(image)}\t"
                                                            f"{len(image)}\tjob {digest}\n")
        self.assertEqual(self.store.objects, {sha(AMA): AMA})
        [row_] = w.rows("run")
        self.assertEqual(ledger.line(row_).split("\t")[5:10], [TOOL, usd, "0", "renderer", "DONE"])

    def test_clean(self):
        w, proxy, job, digest = self.world(answer("ok"))
        self.assertEqual(w.run(job), (0, line(digest, "DONE", "0", "0") + "\n", ""))
        self.assertEqual(proxy.requests, [{"path": "/v1/images/generations", "authorization": f"Bearer {TOKEN}",
                                           "body": BODY}])
        self.assertEqual(proxy.gets, [f"/media/{sha(AMA)}", PREVIEW_GET])
        self.assertEqual(self.store.objects, {sha(AMA): AMA})
        self.assertTrue(AMA.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(w.manifest(), MANIFEST_TEXT + f"{digest[:16]}-image.png\t{sha(AMA)}\t"
                                                       f"{len(AMA)}\tjob {digest}\n")
        tree = w.tmp / "exported"
        gitstate.export(w.root, BRANCH, tree)
        result = lint.run(tree, ["media"])["media"]
        self.assertEqual((result.status, result.findings), ("PASS", []))
        [row_] = w.rows("run")
        self.assertEqual(ledger.line(row_).split("\t")[5:10], [TOOL, "0", "0", "renderer", "DONE"])

    def test_one_ref(self):
        w, proxy, job, digest = self.world(answer("refs-ok"), refs_request("media:ama-ref"), expected=COSTED)
        self.assert_done(w, job, digest, "0.07")
        self.assertEqual(proxy.gets, [f"/media/{sha(AMA)}", f"/media/{sha(AMA)}", PREVIEW_GET])
        self.assertEqual(proxy.requests, [{"path": "/v1/images/edits", "authorization": f"Bearer {TOKEN}",
                                           "body": form("one", [AMA])}])

    def test_two_refs_and_over_the_limit(self):
        w, proxy, job, digest = self.world(with_usage({"cost_in_usd_ticks": 800000000}),
                                           refs_request("media:ama-ref", "media:tide-ref"), expected=COSTED)
        self.assert_done(w, job, digest, "0.08")
        self.assertEqual(proxy.gets, [f"/media/{sha(AMA)}", f"/media/{sha(TIDE)}", f"/media/{sha(AMA)}",
                                      PREVIEW_GET])
        self.assertEqual([(r["path"], r["body"]) for r in proxy.requests],
                         [("/v1/images/edits", form("two", [AMA, TIDE]))])
        w, proxy, job, digest = self.world(answer("refs-ok"), refs_request("media:tide-ref", "media:ama-ref"),
                                           expected=COSTED)
        self.assert_done(w, job, digest, "0.07")
        self.assertEqual([r["body"] for r in proxy.requests], [form("two", [TIDE, AMA])])

    def test_three_refs(self):
        w, proxy, job, digest = self.world(answer("refs-ok"), refs_request(
            "media:ama-ref", "media:tide-ref", "media:reef-ref"), manifest=MANY_TEXT, held=MANY_HELD,
            expected=COSTED)
        self.assert_done(w, job, digest, "0.07")
        self.assertEqual(proxy.gets, [f"/media/{sha(AMA)}", f"/media/{sha(TIDE)}", f"/media/{sha(REEF)}",
                                      f"/media/{sha(AMA)}", PREVIEW_GET])
        four = json.loads(form("four", [AMA, TIDE, REEF, AMA]))
        four["images"].pop()
        self.assertEqual([(r["path"], r["body"]) for r in proxy.requests],
                         [("/v1/images/edits", json.dumps(four, sort_keys=True))])

    def test_four_refs(self):
        w, proxy, job, digest = self.world(with_usage({"cost_in_usd_ticks": 1000000000}), refs_request(
            "media:tide-ref", "media:ama-ref", "media:reef-ref", "media:ama-two"), manifest=MANY_TEXT,
            held=MANY_HELD, expected=COSTED)
        self.assert_done(w, job, digest, "0.1")
        self.assertEqual(proxy.gets, [f"/media/{sha(TIDE)}", f"/media/{sha(AMA)}", f"/media/{sha(REEF)}",
                                      f"/media/{sha(AMA)}", f"/media/{sha(AMA)}", PREVIEW_GET])
        self.assertEqual([(r["path"], r["authorization"], r["body"]) for r in proxy.requests],
                         [("/v1/images/edits", f"Bearer {TOKEN}", form("four", [TIDE, AMA, REEF, AMA]))])

    def test_five_refs_over_the_limit(self):
        w, proxy, job, digest = self.world(answer("refs-ok"), refs_request(
            "media:ama-ref", "media:tide-ref", "media:reef-ref", "media:ama-two", "media:moss-ref"),
            manifest=MANY_TEXT, held=MANY_HELD)
        self.assert_failed(w, job, digest, "refs over the limit of 4")
        self.assertEqual((proxy.gets, proxy.requests), ([], []))

    def test_request_and_manifest_failures(self):
        cases = [("not a request", refs_request("ama-ref"), None, MANIFEST_TEXT),
                 ("not a request", refs_request("media:"), None, MANIFEST_TEXT),
                 ("not a request", refs_request("media:ama-ref", "media:ama-ref"), None, MANIFEST_TEXT),
                 ("not a request", refs_request(7), None, MANIFEST_TEXT),
                 ("ref not in the manifest", refs_request("media:ama-ref"), [], MANIFEST_TEXT),
                 ("ref not in the manifest", refs_request("media:ama-ref"), [("media/other.tsv", MANIFEST_TEXT)],
                  MANIFEST_TEXT),
                 ("ref not in the manifest", refs_request("media:ama-ref", "media:sea-ref"), None, MANIFEST_TEXT),
                 ("ref not in the manifest", refs_request("media:ama-ref"), None,
                  HEADER + f"ama-ref\t{sha(AMA).upper()}\t{len(AMA)}\tsources/ama-ref.png\n"
                  + f"ama-ref\t{sha(AMA)}\t1.5\tsources/ama-ref.png\n" + f"ama-ref\t{sha(AMA)}\t{len(AMA)}\n")]
        for reason, request, extra, manifest in cases:
            with self.subTest(reason=reason, request=request, extra=extra):
                w, proxy, job, digest = self.world(answer("refs-ok"), request, extra=extra, manifest=manifest)
                self.assert_failed(w, job, digest, reason)
                self.assertEqual((proxy.gets, proxy.requests), ([], []))

    def test_malformed_row_before_the_good_one(self):
        manifest = (HEADER + f"ama-ref\t{sha(TIDE)}\tmany\tsources/ama-ref.png\n" + row("ama-ref", AMA)
                    + row("ama-ref", TIDE))
        w, proxy, job, digest = self.world(answer("refs-ok"), refs_request("media:ama-ref"), manifest=manifest,
                                           expected=COSTED)
        self.assert_done(w, job, digest, "0.07")
        self.assertEqual(proxy.gets, [f"/media/{sha(AMA)}", f"/media/{sha(AMA)}", PREVIEW_GET])
        self.assertEqual([r["body"] for r in proxy.requests], [form("one", [AMA])])

    def test_ref_fetch_failures(self):
        jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00invented jpeg bytes\xff\xd9"
        gif = b"GIF89a\x01\x00\x01\x00invented gif bytes;"
        other = bytes(len(AMA))
        cases = [("ref not fetched", MANIFEST_TEXT, {}),
                 ("ref not fetched", MANIFEST_TEXT, {sha(AMA): AMA + b"\x00"}),
                 ("ref not fetched", MANIFEST_TEXT, {sha(AMA): AMA[:-1]}),
                 ("ref not fetched", MANIFEST_TEXT, {sha(AMA): other}),
                 ("ref is not a PNG", HEADER + row("ama-ref", jpeg), {sha(jpeg): jpeg}),
                 ("ref is not a PNG", HEADER + row("ama-ref", gif), {sha(gif): gif})]
        for reason, manifest, held in cases:
            with self.subTest(reason=reason, held=sorted(held)):
                w, proxy, job, digest = self.world(answer("refs-ok"), refs_request("media:ama-ref"),
                                                   manifest=manifest, held=held)
                self.assert_failed(w, job, digest, reason)
                self.assertEqual(len(proxy.gets), 1)
                self.assertEqual(proxy.requests, [])

    def test_media_link_unreachable(self):
        for media_base in (f"http://127.0.0.1:{free_port()}/media", "http://media.invalid/media"):
            with self.subTest(media_base):
                w, proxy, job, digest = self.world(answer("refs-ok"), refs_request("media:ama-ref"),
                                                   media_base=media_base)
                added = self.assert_down(w, job, digest)
                self.assertEqual([(e["op"], e["number"], e["title"]) for e in added],
                                 [("create_issue", 5, f"[DOWN] {RESOURCE}")])
                self.assertEqual((proxy.gets, proxy.requests), ([], []))

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

    def test_max_refs(self):
        spec = importlib.util.spec_from_file_location("grok_imagine", ROOT / "kit" / "lib" / "tools" / f"{TOOL}.py")
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.MAX_REFS, json.loads((FIX / "refs-form.json").read_text())["max_refs"])
        self.assertEqual(tool.MAX_REFS, 4)
        self.assertEqual(tool.TOO_MANY, "refs over the limit of 4")
        self.assertIs(tool.REASONS[1], tool.TOO_MANY)

    def test_cost(self):
        cases = [("0.08", with_usage({"cost_in_usd_ticks": 800000000})), ("0.07", answer("refs-ok")),
                 ("0", with_usage(None)), ("0", with_usage({"cost_in_usd_ticks": "700000000"})),
                 ("0", with_usage({"cost_in_usd_ticks": -5})), ("0", with_usage({"cost_in_usd_ticks": 1.5})),
                 ("0", with_usage({"cost_in_usd_ticks": True}))]
        for usd, answer_of in cases:
            with self.subTest(answer_of[1]):
                w, proxy, job, digest = self.world(answer_of, refs_request("media:ama-ref"), expected=COSTED)
                self.assert_done(w, job, digest, usd)


if __name__ == "__main__":
    unittest.main()
