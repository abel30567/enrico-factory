"""Tests for the 3D tools, kit/lib/tools/model3d.py and kit/lib/tools/rig.py, through `factory run`.

The production, the recorded GitHub and the gate PASS are `World` of tests/test_run.py, with the kit's own
tools and register. The asset store is `AssetStore` of tests/test_assets.py. The vendors are one fake on
127.0.0.1 that answers by method and path from tests/fixtures/tools_3d, each fixture an answer written by
hand from the vendor's public API reference, its `{base}` replaced by the fake's URL. The asset is the
invented `lamp-keeper` and its image `media:lamp-keeper-ref`. Nothing reaches a host other than 127.0.0.1,
and every poll is 0 seconds apart.
"""

import base64
import contextlib
import hashlib
import http.server
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from kit.lib import cmd_doctor, cmd_gate, cmd_run, gitstate, resources, vendor  # noqa: E402
from test_assets import KEY_ID, REGION, SECRET, AssetStore  # noqa: E402
from test_media import free_port  # noqa: E402
from test_run import BRANCH, World, digest_of  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "tools_3d"
RUN_FIX = ROOT / "tests" / "fixtures" / "run"
MODEL_KEY, RIG_KEY = "model-key-6b2e91d4", "rig-key-0c7a53f8"
STORE_KEY = f"{KEY_ID}\n{SECRET}\n{REGION}\n"
PUBLIC = "https://media.invalid/pub"
IMAGE = b"invented reference image of the lamp keeper\x89\x00"
MODEL = b"glTF invented lamp keeper model\x00\x01\x02"
RIG = b"glTF invented lamp keeper rig\x00\x03\x04\x05"
MODEL_EXPECTED, RIG_EXPECTED = {"usd": 0.3, "compute_min": 5}, {"usd": 0.5, "compute_min": 5}
SUBMIT = json.loads((FIX / "model" / "submit.json").read_text())["body"]
REQUEST = "/requests/" + SUBMIT["request_id"]
TASK = json.loads((FIX / "rig" / "create.json").read_text())["body"]["result"]
RIGGING = "/openapi/v1/rigging"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def line(digest, verdict, usd, compute_min):
    return f"run job {digest}: {verdict} usd {usd} compute_min {compute_min}\n"


class Vendor:
    """Both vendors on 127.0.0.1. ``answers[(method, path)]`` is a list of ``(status, body)``, sent in turn,
    the last one again and again; a path is matched without its query. Every request is recorded."""

    def __init__(self, test):
        self.requests, self.answers = [], {}
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                fake.handle(self, "GET")

            def do_POST(self):
                fake.handle(self, "POST")

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()
        test.addCleanup(self.server.server_close)
        test.addCleanup(self.server.shutdown)
        self.answer("POST", "/" + vendor.MODEL_PATH, self.fixture("model/submit"))
        self.answer("GET", REQUEST + "/status", self.fixture("model/status-in-progress"),
                    self.fixture("model/status-completed"))
        self.answer("GET", REQUEST, self.fixture("model/result"))
        self.answer("GET", "/files/model.glb", (200, MODEL))
        self.answer("POST", RIGGING, self.fixture("rig/create"))
        self.answer("GET", f"{RIGGING}/{TASK}", self.fixture("rig/task-in-progress"),
                    self.fixture("rig/task-succeeded"))
        self.answer("GET", "/files/rig.glb", (200, RIG))

    def fixture(self, name, **changes):
        data = json.loads((FIX / f"{name}.json").read_text())
        body = json.loads(json.dumps(data["body"]).replace("{base}", self.base))
        return data["status"], json.dumps({**body, **changes}).encode()

    def answer(self, method, path, *answers):
        self.answers[(method, path)] = list(answers)

    def handle(self, h, method):
        length = int(h.headers.get("Content-Length") or 0)
        body = h.rfile.read(length) if length else b""
        self.requests.append({"method": method, "path": h.path, "body": body,
                              "headers": {k.lower(): v for k, v in h.headers.items()}})
        found = self.answers.get((method, urllib.parse.urlsplit(h.path).path)) or [(404, b"")]
        status, payload = found.pop(0) if len(found) > 1 else found[0]
        h.send_response(status)
        h.send_header("Content-Length", str(len(payload)))
        h.end_headers()
        h.wfile.write(payload)

    def made(self, method=None):
        return [r for r in self.requests if method is None or r["method"] == method]


class Tools3dTest(unittest.TestCase):
    def setUp(self):
        self.w = World(self)
        self.vendor = Vendor(self)
        self.store = AssetStore(self)
        self.tmp = self.w.tmp / "tmp"
        self.tmp.mkdir()
        for patch in (mock.patch.object(cmd_run, "TOOLS_DIR", ROOT / "kit" / "lib" / "tools"),
                      mock.patch.object(resources, "REGISTER", ROOT / "kit" / "resources.json"),
                      mock.patch.object(vendor, "MODEL_API", self.vendor.base),
                      mock.patch.object(vendor, "RIG_API", self.vendor.base),
                      mock.patch.object(vendor, "POLL_SECONDS", 0),
                      mock.patch.object(tempfile, "tempdir", str(self.tmp))):
            patch.start()
            self.addCleanup(patch.stop)
        for name, text in (("gen.model3d", MODEL_KEY), ("gen.rig", RIG_KEY), ("store.assets", STORE_KEY)):
            path = self.w.secrets / name
            path.write_text(text)
            path.chmod(0o600)
        self.w.local(STATE_DIR=self.w.state, SECRETS_DIR=self.w.secrets, FAKE_LOG=self.w.fake_log,
                     ASSET_STORE=self.store.url("/b"), MEDIA_PUBLIC_BASE=PUBLIC)
        self.w.set_env("RIG_VENDOR", vendor.RIG_ID)
        self.w.commit(vendor.MANIFEST, "id\tsha256\tbytes\tsource\n"
                      f"lamp-keeper-ref\t{sha(IMAGE)}\t{len(IMAGE)}\tupload\n")
        self.printed = []

    # -- jobs

    def job(self, tool, ref, expected, prompt="the lamp keeper, as drawn", **changes):
        """A request for ``ref`` committed on `trunk`, a job of ``tool`` on it and the manifest, passed by the
        gate; ``(job file, hash)``."""
        request = {"for": "lamp-keeper", "prompt": prompt, "refs": [ref], **changes}
        text = json.dumps(request, indent=1) + "\n"
        rel = f"requests/lamp-keeper-{sha(text.encode())[:12]}.json"
        self.w.commit(rel, text)
        manifest = gitstate.show(self.w.root, "trunk", vendor.MANIFEST)
        inputs = [{"path": rel, "sha256": cmd_gate.sha256(text)},
                  {"path": vendor.MANIFEST, "sha256": cmd_gate.sha256(manifest)}]
        path = self.w.job(f"{tool}-{sha(text.encode())[:12]}.json", tool=tool, expected=expected, inputs=inputs)
        digest = self.w.passed(path)
        self.assertEqual(digest, digest_of(path))
        return path, digest

    def model_job(self, prompt="the lamp keeper, as drawn", **changes):
        return self.job("model3d", "media:lamp-keeper-ref", MODEL_EXPECTED, prompt, **changes)

    def rig_job(self, model_digest, prompt="the lamp keeper, rigged", **changes):
        """The model job's manifest committed on `trunk`, the pull's verdicts at its new head, and a rig job."""
        self.w.commit(vendor.MANIFEST, self.w.manifest())
        reviewed = f"{self.w.head(BRANCH)} manifest {gitstate.manifest(self.w.root, 'trunk', BRANCH)}"
        text = (RUN_FIX / "github" / "comments" / "4.json").read_text().replace("{head:4} manifest none", reviewed)
        (self.w.dir / "comments" / "4.json").write_text(text)
        return self.job("rig", f"media:{model_digest[:16]}-model.glb", RIG_EXPECTED, prompt, **changes)

    def factory_run(self, job):
        found = self.w.run(job)
        self.printed.append(found)
        return found

    def clean_model(self):
        job, digest = self.model_job()
        self.assertEqual(self.factory_run(job), (0, line(digest, "DONE", "0.3", "5"), ""))
        return digest

    def assert_failed(self, job, digest, tool, reason, usd, compute_min="5"):
        self.assertEqual(self.factory_run(job), (1, line(digest, "FAILED", usd, compute_min),
                                         f"run: tool {tool} failed: {reason}\n"))

    def assert_no_leak(self):
        secrets = [MODEL_KEY, RIG_KEY, KEY_ID, SECRET]
        texts = [out + err for _, out, err in self.printed]
        texts.append(gitstate.show(self.w.root, "trunk", "ledger.tsv") or "")
        texts += [p.read_text() for p in self.w.state.rglob("result.json")]
        log = self.w.dir / "log.jsonl"
        texts.append(log.read_text() if log.is_file() else "")
        for text in texts:
            for secret in secrets:
                self.assertNotIn(secret, text)

    # -- the behaviours

    def test_model_job_clean(self):
        digest = self.clean_model()
        self.assertEqual(self.store.objects[sha(MODEL)], MODEL)
        self.assertIn(f"{digest[:16]}-model.glb\t{sha(MODEL)}\t{len(MODEL)}\tjob {digest}",
                      self.w.manifest().splitlines())
        self.assertEqual([r["tool"] for r in self.w.rows("run")], ["model3d"])
        [post] = self.vendor.made("POST")
        self.assertEqual(post["path"], "/" + vendor.MODEL_PATH)
        self.assertEqual(json.loads(post["body"]), {"image_url": f"{PUBLIC}/{sha(IMAGE)}"})
        self.assertEqual(post["headers"]["authorization"], f"Key {MODEL_KEY}")
        self.assertEqual([r["path"] for r in self.vendor.made()], [
            "/" + vendor.MODEL_PATH, REQUEST + "/status", REQUEST + "/status", REQUEST, "/files/model.glb"])
        self.assertNotIn("authorization", self.vendor.made()[-1]["headers"])
        self.assertEqual(os.listdir(self.tmp), [])

    def test_rig_job_clean(self):
        model = self.clean_model()
        self.vendor.requests.clear()
        job, digest = self.rig_job(model)
        self.assertEqual(self.factory_run(job), (0, line(digest, "DONE", "0.5", "5"), ""))
        self.assertEqual(self.store.objects[sha(RIG)], RIG)
        self.assertIn(f"{digest[:16]}-rig.glb\t{sha(RIG)}\t{len(RIG)}\tjob {digest}", self.w.manifest().splitlines())
        [post] = self.vendor.made("POST")
        self.assertEqual(post["path"], RIGGING)
        model_url = json.loads(post["body"])["model_url"]
        self.assertTrue(model_url.startswith("data:application/octet-stream;base64,"))
        self.assertEqual(base64.b64decode(model_url.split(",", 1)[1]), MODEL)
        self.assertEqual(sorted(json.loads(post["body"])), ["model_url"])
        self.assertEqual(post["headers"]["authorization"], f"Bearer {RIG_KEY}")
        self.assertEqual([r["path"] for r in self.vendor.made()][1:], [
            f"{RIGGING}/{TASK}", f"{RIGGING}/{TASK}", "/files/rig.glb?Expires=1747291618"])
        self.assertEqual([r["tool"] for r in self.w.rows("run")], ["model3d", "rig"])
        self.assertEqual(os.listdir(self.tmp), [])

    def test_request_defects(self):
        for tool, ref, expected in (("model3d", "media:lamp-keeper-ref", MODEL_EXPECTED),
                                    ("rig", "media:lamp-keeper-ref", RIG_EXPECTED)):
            for reason, changes in ((vendor.NOT_A_REQUEST, {"size": "tall"}),
                                    (vendor.NO_REFERENCE, {"refs": []}),
                                    (vendor.NOT_IN_MANIFEST, {"refs": ["media:lamp-keeper-sketch"]})):
                with self.subTest(tool=tool, reason=reason):
                    job, digest = self.job(tool, ref, expected, f"{tool}: {reason}", **changes)
                    self.assert_failed(job, digest, tool, reason, "0", "0")
                    self.assertEqual(self.vendor.requests, [])
        self.assertEqual(len(self.w.rows("run")), 6)

    def test_key_refused_is_down(self):
        self.vendor.answer("POST", "/" + vendor.MODEL_PATH, (401, b'{"detail": "unauthorized"}'))
        job, digest = self.model_job()
        for api in (self.vendor.base, f"http://127.0.0.1:{free_port()}"):
            with self.subTest(api=api), mock.patch.object(vendor, "MODEL_API", api):
                self.assertEqual(self.factory_run(job), (2, "", "run: resource gen.model3d is down\n"))
                self.assertEqual(self.w.rows("run"), [])
                self.assertFalse(self.w.job_dir(digest).exists())
        self.assertEqual(len(self.vendor.made("POST")), 1)

    def test_vendor_fails_after_the_start(self):
        self.vendor.answer("GET", REQUEST + "/status", (500, b"{}"))
        job, digest = self.model_job("status answers 500")
        self.assert_failed(job, digest, "model3d", vendor.VENDOR_FAILED, "0.3")
        self.vendor.answer("GET", REQUEST + "/status", self.vendor.fixture("model/status-completed"))
        self.vendor.answer("POST", "/" + vendor.MODEL_PATH, self.vendor.fixture(
            "model/submit", status_url="https://elsewhere.invalid/requests/x/status"))
        self.vendor.requests.clear()
        job, digest = self.model_job("status_url outside the API")
        self.assert_failed(job, digest, "model3d", vendor.VENDOR_FAILED, "0.3")
        self.assertNotIn(REQUEST + "/status", [r["path"] for r in self.vendor.made()])
        self.vendor.answer("POST", "/" + vendor.MODEL_PATH, self.vendor.fixture("model/submit"))
        model = self.clean_model()
        self.vendor.answer("GET", f"{RIGGING}/{TASK}", self.vendor.fixture("rig/task-succeeded", status="FAILED"))
        job, digest = self.rig_job(model)
        self.assert_failed(job, digest, "rig", vendor.VENDOR_FAILED, "0.5")
        self.assertEqual([r["tool"] for r in self.w.rows("run")], ["model3d"] * 3 + ["rig"])

    def test_too_slow(self):
        self.vendor.answer("GET", REQUEST + "/status", self.vendor.fixture("model/status-in-progress"))
        job, digest = self.model_job()
        with mock.patch.object(vendor, "POLLS", 2):
            self.assert_failed(job, digest, "model3d", vendor.TOO_SLOW, "0.3")
        self.assertEqual([r["path"] for r in self.vendor.made()].count(REQUEST + "/status"), 2)

    def test_download(self):
        self.vendor.answer("GET", "/files/model.glb", (404, b""))
        job, digest = self.model_job("the file is gone")
        self.assert_failed(job, digest, "model3d", vendor.DOWNLOAD_FAILED, "0.3")
        self.assertEqual(os.listdir(self.w.job_dir(digest) / "out"), [])
        result = self.vendor.fixture("model/result")[1].replace(
            f"{self.vendor.base}/files/model.glb".encode(), b"ftp://files.invalid/model.glb")
        self.vendor.answer("GET", REQUEST, (200, result))
        job, digest = self.model_job("the file is on ftp")
        self.assert_failed(job, digest, "model3d", vendor.VENDOR_FAILED, "0.3")
        self.assertEqual(self.store.objects, {})

    def test_store_refuses_the_output(self):
        self.store.status["PUT"] = (403, b"")
        job, digest = self.model_job()
        self.assert_failed(job, digest, "model3d", vendor.NOT_STORED, "0.3")
        self.assertIsNone(self.w.manifest())
        self.assertEqual(self.store.objects, {})

    def test_rig_input(self):
        model = self.clean_model()
        self.vendor.requests.clear()
        with mock.patch.object(vendor, "DATA_URI_BYTES", len(MODEL) - 1):
            job, digest = self.rig_job(model, "too large")
            self.assert_failed(job, digest, "rig", vendor.TOO_LARGE, "0", "0")
        self.store.objects.clear()
        job, digest = self.job("rig", f"media:{model[:16]}-model.glb", RIG_EXPECTED, "not in the store")
        self.assert_failed(job, digest, "rig", vendor.NOT_IN_STORE, "0", "0")
        self.assertEqual(self.vendor.requests, [])
        self.assertEqual(os.listdir(self.tmp), [])

    def test_nothing_leaks(self):
        self.vendor.answer("POST", "/" + vendor.MODEL_PATH, (401, b""))
        job, model = self.model_job()
        self.assertEqual(self.factory_run(job)[0], 2)
        self.vendor.answer("POST", "/" + vendor.MODEL_PATH, self.vendor.fixture("model/submit"))
        self.assertEqual(self.factory_run(job), (0, line(model, "DONE", "0.3", "5"), ""))
        self.vendor.answer("GET", f"{RIGGING}/{TASK}", self.vendor.fixture("rig/task-succeeded", status="FAILED"))
        job, digest = self.rig_job(model)
        self.assert_failed(job, digest, "rig", vendor.VENDOR_FAILED, "0.5")
        self.assertEqual(len(self.printed), 3)
        self.assert_no_leak()


class DoctorTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        (self.root / "sec").mkdir()
        for name in ("gen.model3d", "gen.rig", "store.assets"):
            (self.root / "sec" / name).write_text("k\n")
            (self.root / "sec" / name).chmod(0o600)
        (self.root / "production.local.env").write_text(
            f"SECRETS_DIR={self.root / 'sec'}\nASSET_STORE=https://store.invalid/b\nMEDIA_PUBLIC_BASE={PUBLIC}\n")
        patch = mock.patch.object(resources, "REGISTER", ROOT / "kit" / "resources.json")
        patch.start()
        self.addCleanup(patch.stop)

    def doctor(self, rig_vendor, wave):
        (self.root / "production.env").write_text(f"RIG_VENDOR={rig_vendor}\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            cmd_doctor.main(["--root", str(self.root), "--wave", str(wave)])
        return out.getvalue().splitlines()

    def test_doctor(self):
        lines = self.doctor(vendor.RIG_ID, 5)
        self.assertIn("ok\tgen.model3d\tText or image to 3D model", lines)
        self.assertIn("ok\tgen.rig\tAutomatic rigging", lines)
        lines = self.doctor("other", 5)
        self.assertIn("ok\tgen.model3d\tText or image to 3D model", lines)
        at = lines.index("missing\tgen.rig\tAutomatic rigging")
        self.assertEqual(lines[at + 1], "  setting RIG_VENDOR is not one of: meshy")
        lines = self.doctor(vendor.RIG_ID, 4)
        self.assertIn("skipped\tgen.model3d\tText or image to 3D model", lines)
        self.assertIn("skipped\tgen.rig\tAutomatic rigging", lines)


if __name__ == "__main__":
    unittest.main()
