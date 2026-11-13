"""Tests for kit/lib/audioapi.py and the tools `voice`, `music` and `sfx`, through `factory run`.

The world is the one of tests/test_run.py, with the kit's own tools (kit/lib/tools) and register
(kit/resources.json), the three key files gen.voice, gen.music and gen.sfx holding one key, and the vendor a
loopback HTTP server answering each path with a fixture of tests/fixtures/audioapi. The fixtures are written
by hand from the vendor's public references, each naming its page. Nothing reaches the network.
"""

import base64
import hashlib
import http.server
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from kit.lib import audioapi, cmd_run, gitstate, ledger, lint, resources  # noqa: E402
from test_run import NOW, TICKET, World, line  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "audioapi"
KEY = "xi-seeded-key-0c4f7a2e91"
VOICE_ID = "q7Ama0voice0id0x1234"
QUERY = "?output_format=mp3_44100_128"
MANIFEST_HEADER = "id\tsha256\tbytes\tsource\n"
TTS, MUSIC, SFX = f"/v1/text-to-speech/{VOICE_ID}", "/v1/music", "/v1/sound-generation"


def fixture(name):
    return json.loads((FIX / name).read_text())


def body_of(name):
    data = fixture(name)
    if "body_base64" in data:
        return base64.b64decode(data["body_base64"])
    return json.dumps(data["body_json"]).encode()


class FakeVendor:
    """The vendor on 127.0.0.1: ``answers[path]`` is the fixture it answers a POST to ``path`` with, or a
    ``(status, body)`` pair for a seeded answer no fixture holds."""

    def __init__(self, test):
        self.answers, self.requests = {}, []
        vendor = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                vendor.requests.append({"path": self.path, "key": self.headers.get("xi-api-key"),
                                        "type": self.headers.get("Content-Type"),
                                        "accept": self.headers.get("Accept"),
                                        "body": self.rfile.read(length).decode()})
                name = vendor.answers.get(self.path.split("?", 1)[0])
                if name is None:
                    status, payload = 404, b""
                elif isinstance(name, tuple):
                    status, payload = name
                else:
                    status, payload = fixture(name)["status"], body_of(name)
                self.send_response(status)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()
        test.addCleanup(self.server.server_close)
        test.addCleanup(self.server.shutdown)


def stopped_url():
    """The URL of a loopback server that was started and stopped: nothing answers there."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    server.server_close()
    return url


class AudioTest(unittest.TestCase):
    def setUp(self):
        self.w = World(self)
        self.vendor = FakeVendor(self)
        for patch in (mock.patch.object(cmd_run, "TOOLS_DIR", ROOT / "kit" / "lib" / "tools"),
                      mock.patch.object(resources, "REGISTER", ROOT / "kit" / "resources.json"),
                      mock.patch.object(audioapi, "BASE", self.vendor.url)):
            patch.start()
            self.addCleanup(patch.stop)
        for name in ("gen.voice", "gen.music", "gen.sfx"):
            self.key(name, KEY + "\n")
        self.printed = []

    # -- helpers

    def key(self, name, text):
        path = self.w.secrets / name
        path.write_text(text)
        path.chmod(0o600)

    def committed(self, rel, text):
        """``rel`` committed on `trunk` with ``text``; its job input."""
        self.w.commit(rel, text)
        return {"path": rel, "sha256": hashlib.sha256(text.encode()).hexdigest()}

    def request(self, name, prompt, **extra):
        data = {"for": "sh010", "prompt": prompt, "refs": [], **extra}
        return self.committed(f"requests/{name}.json", json.dumps(data, indent=1) + "\n")

    def voice(self, character, text=None):
        return self.committed(f"bible/style/voices/{character}.json",
                              text or json.dumps({"voice_id": VOICE_ID}) + "\n")

    def job(self, name, tool, inputs):
        return self.w.job(f"{name}.json", tool=tool, inputs=inputs)

    def factory_run(self, job):
        code, out, err = self.w.run(job)
        self.printed += [out, err]
        return code, out, err

    def assert_done(self, tool, prompt, path, body, inputs, answer):
        w = self.w
        self.vendor.answers[path] = answer
        job = self.job(tool, tool, inputs)
        digest = w.passed(job)
        self.assertEqual(self.factory_run(job), (0, line(digest, "DONE", "2.5", "30") + "\n", ""))
        self.assertEqual(self.vendor.requests, [{
            "path": path + QUERY, "key": KEY, "type": "application/json", "accept": "audio/mpeg",
            "body": json.dumps(body, sort_keys=True)}])
        data = body_of(answer)
        self.assertEqual((w.job_dir(digest) / "out" / f"{tool}.mp3").read_bytes(), data)
        self.assertEqual(w.manifest(), MANIFEST_HEADER + "\t".join(
            [f"{digest[:16]}-{tool}.mp3", hashlib.sha256(data).hexdigest(), str(len(data)), f"job {digest}"]) + "\n")
        [row] = w.rows("run")
        self.assertEqual(ledger.line(row), "\t".join(
            [NOW, "run", digest, str(TICKET), "ep01", tool, "2.5", "30", "renderer", "DONE", "-"]))
        tree = w.tmp / "exported"
        gitstate.export(w.root, "feat/RN-1-ep01", tree)
        result = lint.run(tree, ["media"])["media"]
        self.assertEqual((result.status, result.findings), ("PASS", []))
        return digest

    def assert_failed(self, job, tool, reason, usd, compute_min):
        digest = self.w.passed(job)
        self.assertEqual(self.factory_run(job), (1, line(digest, "FAILED", usd, compute_min) + "\n",
                                         f"run: tool {tool} failed: {reason}\n"))
        self.assertEqual(self.w.files(self.w.job_dir(digest) / "out"), [])
        self.assertEqual([(r["hash"], r["verdict"]) for r in self.w.rows("run")][-1], (digest, "FAILED"))
        self.assertIsNone(self.w.manifest())
        return digest

    def assert_down(self, job, digest):
        self.assertEqual(self.factory_run(job), (2, "", "run: resource gen.sfx is down\n"))
        self.assertEqual(self.w.rows("run"), [])
        self.assertFalse(self.w.job_dir(digest).exists())

    def sfx_job(self):
        job = self.job("door", "sfx", [self.request("door", "A wooden door creaks open.")])
        return job, self.w.passed(job)

    # -- Behaviour 1 to 10

    def test_voice_clean(self):
        prompt = "Ama says the tide is late."
        inputs = [self.request("ama-line", prompt), self.voice("ama")]
        self.assert_done("voice", prompt, TTS, {"model_id": "eleven_multilingual_v2", "text": prompt}, inputs,
                         "voice-ok.json")

    def test_music_clean(self):
        prompt = "A slow harbour waltz on fiddle, 30 seconds."
        self.assert_done("music", prompt, MUSIC,
                         {"force_instrumental": True, "model_id": "music_v1", "prompt": prompt},
                         [self.request("waltz", prompt)], "music-ok.json")

    def test_sfx_clean(self):
        prompt = "A wooden door creaks open."
        self.assert_done("sfx", prompt, SFX, {"model_id": "eleven_text_to_sound_v2", "text": prompt},
                         [self.request("door", prompt)], "sfx-ok.json")

    def test_music_names_a_work(self):
        self.vendor.answers[MUSIC] = "music-bad-prompt.json"
        job = self.job("named", "music", [self.request("named", "A song that sounds like Skarrowfen.")])
        digest = self.assert_failed(job, "music", "the vendor found an artist or a song in the prompt", "2.5", "30")
        self.assertEqual(len(self.vendor.requests), 1)
        suggestion = fixture("music-bad-prompt.json")["body_json"]["detail"]["data"]["prompt_suggestion"]
        texts = self.printed + [p.read_bytes().decode("utf-8", "replace") for p in self.w.state.rglob("*") if p.is_file()]
        texts += [p.read_text() for p in self.w.dir.rglob("*") if p.is_file()]
        texts.append(self.w.git("log", "--all", "-p", "--format=%H %an %s%n%b"))
        self.assertTrue(self.w.job_dir(digest).is_dir())
        for text in texts:
            self.assertNotIn(suggestion, text)

    def test_no_stored_voice(self):
        self.vendor.answers[TTS] = "voice-ok.json"
        request = self.request("ama-line", "Ama says the tide is late.")
        ama, tobi = self.voice("ama"), self.voice("tobi")
        bad = self.voice("bad", json.dumps({"voice": VOICE_ID}) + "\n")
        jobs = [(self.job("none", "voice", [request]), "the inputs hold no one stored voice"),
                (self.job("two", "voice", [request, ama, tobi]), "the inputs hold no one stored voice"),
                (self.job("bad", "voice", [request, bad]), 'the stored voice file is not {"voice_id": <id>}')]
        digests = [self.w.passed(job) for job, _ in jobs]
        for (job, reason), digest in zip(jobs, digests):
            with self.subTest(job):
                self.assertEqual(self.factory_run(job), (1, line(digest, "FAILED", "0", "0") + "\n",
                                                 f"run: tool voice failed: {reason}\n"))
        self.assertEqual(self.vendor.requests, [])
        self.assertIsNone(self.w.manifest())

    def test_unknown_voice(self):
        self.vendor.answers[TTS] = "voice-not-found.json"
        ama = self.voice("ama")
        job = self.job("lost", "voice", [self.request("ama-line", "Ama says the tide is late."), ama])
        self.assert_failed(job, "voice", "the vendor has no such voice", "2.5", "30")
        self.assertEqual(len(self.vendor.requests), 1)
        # A refusal whose detail.status and detail.code are not strings names no word of the tool.
        self.vendor.answers[TTS] = (404, json.dumps(
            {"detail": {"status": ["voice_not_found"], "code": {"voice_not_found": 1}}}).encode())
        job = self.job("odd", "voice", [self.request("odd-line", "Ama says the sea is late."), ama])
        self.assert_failed(job, "voice", "the vendor refused the request", "2.5", "30")
        self.assertEqual(len(self.vendor.requests), 2)

    def test_not_a_request(self):
        self.vendor.answers[SFX] = "sfx-ok.json"
        jobs = [self.job("blank", "sfx", [self.request("blank", "   ")]),
                self.job("extra", "sfx", [self.request("extra", "A wooden door creaks open.", length=3)])]
        digests = [self.w.passed(job) for job in jobs]
        for job, digest in zip(jobs, digests):
            with self.subTest(job):
                self.assertEqual(self.factory_run(job), (1, line(digest, "FAILED", "0", "0") + "\n",
                                                 "run: tool sfx failed: the first input is not a request\n"))
        self.assertEqual(self.vendor.requests, [])
        self.assertIsNone(self.w.manifest())

    def test_not_audio(self):
        self.vendor.answers[SFX] = "not-audio.json"
        job, _ = self.sfx_job()
        self.assert_failed(job, "sfx", "the vendor answer is not audio", "2.5", "30")
        self.assertEqual(len(self.vendor.requests), 1)

    def test_down(self):
        job, digest = self.sfx_job()
        for answer in ("invalid-key.json", "rate-limited.json"):
            with self.subTest(answer):
                self.vendor.answers[SFX] = answer
                self.assert_down(job, digest)
        self.assertEqual(len(self.vendor.requests), 2)
        with self.subTest("stopped server"), mock.patch.object(audioapi, "BASE", stopped_url()):
            self.assert_down(job, digest)
        with self.subTest("a key of two words"):
            self.key("gen.sfx", "two words\n")
            self.assert_down(job, digest)
            self.key("gen.sfx", KEY + "\n")
        self.assertEqual(len(self.vendor.requests), 2)
        self.vendor.answers[SFX] = "sfx-ok.json"
        self.assertEqual(self.factory_run(job), (0, line(digest, "DONE", "2.5", "30") + "\n", ""))
        self.assertEqual([r["verdict"] for r in self.w.rows("run")], ["DONE"])

    def test_the_key_stays_out(self):
        w = self.w
        self.vendor.answers.update({TTS: "voice-ok.json", MUSIC: "music-bad-prompt.json", SFX: "rate-limited.json"})
        jobs = [self.job("spoken", "voice", [self.request("ama-line", "Ama says the tide is late."), self.voice("ama")]),
                self.job("named", "music", [self.request("named", "A song that sounds like Skarrowfen.")]),
                self.job("door", "sfx", [self.request("door", "A wooden door creaks open.")]),
                self.job("blank", "sfx", [self.request("blank", "   ")]),
                self.job("none", "voice", [self.request("tobi-line", "Tobi laughs.")])]
        for job in jobs:
            w.passed(job)
        self.assertEqual([self.factory_run(job)[0] for job in jobs], [0, 1, 2, 1, 1])
        self.vendor.answers[SFX] = "sfx-ok.json"
        self.assertEqual(self.factory_run(jobs[2])[0], 0)
        self.assertEqual([r["key"] for r in self.vendor.requests], [KEY] * 4)
        texts = self.printed + [p.read_bytes().decode("utf-8", "replace") for p in w.state.rglob("*") if p.is_file()]
        texts += [p.read_text() for p in w.dir.rglob("*") if p.is_file()]
        texts.append(w.git("log", "--all", "-p", "--format=%H %an %s%n%b"))
        for text in texts:
            self.assertNotIn(KEY, text)


if __name__ == "__main__":
    unittest.main()
