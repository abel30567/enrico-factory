"""Tests for the 3D tools, kit/lib/tools/model3d.py and kit/lib/tools/rig.py, through `factory run`.

The production, the recorded GitHub and the gate PASS are `World` of tests/test_run.py, with the kit's own
tools and register. The asset store is `AssetStore` of tests/test_assets.py. The vendors are one fake on
127.0.0.1 that answers by method and path from tests/fixtures/tools_3d, each fixture an answer written by
hand from the vendor's public API reference (`model/`) or recorded from a real session (`rig/`), its
`{base}` replaced by the fake's URL. The asset is the invented `lamp-keeper` and its image
`media:lamp-keeper-ref`; its canon height, a row of `bible/canon/ledger.tsv`, is 1.57 m, and the model vendor
serves `RAW`, 1.7 m high, which the model job stores scaled as `MODEL`. The World's pipeline gains the asset stages
`assets` and `rigging`, and its ticket 3 is at `stage:assets` of `unit:lamp-keeper`. The GLBs the vendors
serve are built by `make_glb` of tests/test_glb.py. A second fake serves the image's public link, and
`vendor.RUN` is a stand-in for `ffmpeg` that answers the pixels of a picture of tests/test_cardscan.py.
Nothing reaches a host other than 127.0.0.1, and every poll is 0 seconds apart.
"""

import base64
import contextlib
import hashlib
import http.server
import io
import json
import os
import shutil
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
sys.path.insert(0, str(ROOT / "tests"))

from kit.lib import cardscan, cmd_doctor, cmd_gate, cmd_run, glb, gitstate, resources, vendor  # noqa: E402
from kit.lib.lints.assets import TRIANGLE_BUDGETS  # noqa: E402
from kit.lib.tools import model3d  # noqa: E402
from test_assets import KEY_ID, REGION, SECRET, AssetStore  # noqa: E402
from test_cardscan import card, figure  # noqa: E402
from test_glb import Builder, make_glb, png  # noqa: E402
from test_media import free_port  # noqa: E402
from test_run import BRANCH, World, digest_of  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "tools_3d"
RUN_FIX = ROOT / "tests" / "fixtures" / "run"
MODEL_KEY, RIG_KEY = "model-key-6b2e91d4", "rig-key-0c7a53f8"
STORE_KEY = f"{KEY_ID}\n{SECRET}\n{REGION}\n"
IMAGE = b"invented reference image of the lamp keeper\x89\x00"


def lamp_keeper(skin):
    """One triangle 1.7 m high, one material and one 2048 by 1024 PNG; with ``skin``, the joints `root`,
    `spine` and `head`, each the parent of the next, and float weights that sum to 1."""
    b = Builder()
    attributes = {"POSITION": b.position(3)}
    if skin:
        attributes["JOINTS_0"] = b.accessor(bufferView=b.view(bytes([0, 1, 2, 0] * 3)), componentType=5121,
                                            count=3, type="VEC4")
        attributes["WEIGHTS_0"] = b.weights([(1, 0, 0, 0), (0.5, 0.5, 0, 0), (0.25, 0.25, 0.5, 0)])
        b.skin("root", "spine", "head")
    b.mesh({"attributes": attributes, "material": 0})
    b.doc["materials"] = [{"name": "lamp_keeper_body"}]
    b.image(png(2048, 1024), "lamp_keeper_body")
    return b


def scaled(data, height):
    """``data`` scaled by `glb.scale` to ``height`` metres."""
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "model.glb")
        Path(path).write_bytes(data)
        glb.scale(path, height / glb.height(data))
        return Path(path).read_bytes()


RAW = lamp_keeper(skin=False).glb()
MODEL = scaled(RAW, 1.57)
SPARSE = lamp_keeper(skin=False)
SPARSE.doc["accessors"][0]["sparse"] = {"count": 1, "indices": {"bufferView": 0, "componentType": 5121},
                                        "values": {"bufferView": 0}}
SPARSE = SPARSE.glb()
RIG = lamp_keeper(skin=True).glb()
UNSKINNED = lamp_keeper(skin=True)
UNSKINNED.doc.pop("skins")
UNSKINNED = UNSKINNED.glb()
OLD_MODEL = b"glTF invented lamp keeper model\x00\x01\x02"
BROKEN = {"a JSON byte changed": RAW[:20] + b"x" + RAW[21:],
          "no meshes": make_glb({"asset": {"version": "2.0"}}),
          "not a GLB": OLD_MODEL}
ASSET_DIR = "production/assets/lamp-keeper"
STATE = f"{ASSET_DIR}/assets/state.json"
MODEL_MANIFEST = f"{ASSET_DIR}/assets/manifest.json"
RIG_MANIFEST = f"{ASSET_DIR}/rigging/manifest.json"
SHEET = "bible/canon/characters/lamp-keeper.md"
PLACE = "bible/canon/places/lamp-keeper.md"
LEDGER = "bible/canon/ledger.tsv"
LEDGER_HEADER = "subject\tattribute\tvalue\n"
LABELS = ["room:production", "stage:assets", "tracked", "unit:lamp-keeper"]
STAGES = [{"room": "production", "stage": stage, "unit": "asset", "after": "", "maker": "renderer",
           "lint": ["structure"], "checker": "inspector", "judge": "", "blind": False, "pitches": 1,
           "paths": [f"production/assets/*/{stage}/**"]} for stage in ("assets", "rigging")]
SKELETON = {"bones": [{"name": "root", "parent": None}, {"name": "spine", "parent": "root"},
                      {"name": "head", "parent": "spine"}], "weights_normalised": True}
MODEL_EXPECTED, RIG_EXPECTED = {"usd": 0.3, "compute_min": 5}, {"usd": 0.5, "compute_min": 5}
SUBMIT = json.loads((FIX / "model" / "submit.json").read_text())["body"]
REQUEST = "/requests/" + SUBMIT["request_id"]
RIG_SUBMIT = json.loads((FIX / "rig" / "submit.json").read_text())["body"]
RIG_REQUEST = "/fal-ai/meshy/requests/" + RIG_SUBMIT["request_id"]
RIG_FILE = json.loads((FIX / "rig" / "result.json").read_text())["body"]["rigged_character_glb"]["url"].replace(
    "{base}", "")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def line(digest, verdict, usd, compute_min):
    return f"run job {digest}: {verdict} usd {usd} compute_min {compute_min}\n"


def stage_json(data):
    return json.dumps(data, indent=1, sort_keys=True) + "\n"


def output(name, data):
    return {"bytes": len(data), "name": name, "sha256": sha(data)}


class Ffmpeg:
    """A stand-in for `vendor.RUN`: it answers ``rgb`` and exits ``code``; every input is recorded."""

    def __init__(self, rgb, code=0):
        self.rgb, self.code, self.inputs = rgb, code, []

    def __call__(self, argv, input=None, **options):
        self.inputs.append((argv, input, options))
        return subprocess.CompletedProcess(argv, self.code, self.rgb, b"")


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
        self.answer("GET", "/files/model.glb", (200, RAW))
        self.answer("POST", "/" + vendor.RIG_PATH, self.fixture("rig/submit"))
        self.answer("GET", RIG_REQUEST + "/status", self.fixture("rig/status-in-progress"),
                    self.fixture("rig/status-completed"))
        self.answer("GET", RIG_REQUEST, self.fixture("rig/result"))
        self.answer("GET", RIG_FILE, (200, RIG))

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
        self.public = Vendor(self)
        self.public.answer("GET", f"/pub/{sha(IMAGE)}", (200, IMAGE))
        self.ffmpeg = Ffmpeg(figure().rgb())
        self.store = AssetStore(self)
        self.tmp = self.w.tmp / "tmp"
        self.tmp.mkdir()
        for patch in (mock.patch.object(cmd_run, "TOOLS_DIR", ROOT / "kit" / "lib" / "tools"),
                      mock.patch.object(resources, "REGISTER", ROOT / "kit" / "resources.json"),
                      mock.patch.object(vendor, "MODEL_API", self.vendor.base),
                      mock.patch.object(vendor, "RIG_API", self.vendor.base),
                      mock.patch.object(vendor, "POLL_SECONDS", 0),
                      mock.patch.object(vendor, "RUN", self.ffmpeg),
                      mock.patch.object(tempfile, "tempdir", str(self.tmp))):
            patch.start()
            self.addCleanup(patch.stop)
        for name, text in (("gen.model3d", MODEL_KEY), ("gen.rig", RIG_KEY), ("store.assets", STORE_KEY)):
            path = self.w.secrets / name
            path.write_text(text)
            path.chmod(0o600)
        self.w.local(STATE_DIR=self.w.state, SECRETS_DIR=self.w.secrets, FAKE_LOG=self.w.fake_log,
                     ASSET_STORE=self.store.url("/b"), MEDIA_PUBLIC_BASE=self.public.base + "/pub")
        self.w.set_env("RIG_VENDOR", vendor.RIG_ID)
        self.w.commit(vendor.MANIFEST, "id\tsha256\tbytes\tsource\n"
                      f"lamp-keeper-ref\t{sha(IMAGE)}\t{len(IMAGE)}\tupload\n")
        pipeline = ".factory/pipeline.json"
        stages = json.loads((self.w.root / pipeline).read_text()) + STAGES
        self.w.commit(pipeline, json.dumps(stages, indent=1) + "\n")
        self.w.edit("issues", 3, labels=LABELS)
        self.w.commit(SHEET, "# The lamp keeper\n\nAn invented keeper of an invented lamp.\n")
        self.w.commit(LEDGER, LEDGER_HEADER + "lamp-keeper\tlamp\toil\nlamp-keeper\theight\t1.57 m\n")
        self.printed = []

    # -- jobs

    def job(self, tool, ref, expected, prompt="the lamp keeper, as drawn", paths=(vendor.MANIFEST,), **changes):
        """A request for ``ref`` committed on `trunk`, a job of ``tool`` on it and on ``paths`` of `trunk`, passed
        by the gate with the pull's verdicts at its head; ``(job file, hash)``."""
        reviewed = f"{self.w.head(BRANCH)} manifest {gitstate.manifest(self.w.root, 'trunk', BRANCH)}"
        text = (RUN_FIX / "github" / "comments" / "4.json").read_text().replace("{head:4} manifest none", reviewed)
        (self.w.dir / "comments" / "4.json").write_text(text)
        request = {"for": "lamp-keeper", "prompt": prompt, "refs": [ref], **changes}
        text = json.dumps(request, indent=1) + "\n"
        rel = f"requests/lamp-keeper-{sha(text.encode())[:12]}.json"
        self.w.commit(rel, text)
        inputs = [{"path": rel, "sha256": cmd_gate.sha256(text)}]
        inputs += [{"path": p, "sha256": cmd_gate.sha256(gitstate.show(self.w.root, "trunk", p))} for p in paths]
        path = self.w.job(f"{tool}-{sha(text.encode())[:12]}.json", tool=tool, expected=expected, inputs=inputs)
        digest = self.w.passed(path)
        self.assertEqual(digest, digest_of(path))
        return path, digest

    def model_job(self, prompt="the lamp keeper, as drawn", paths=(vendor.MANIFEST, SHEET, LEDGER), **changes):
        return self.job("model3d", "media:lamp-keeper-ref", MODEL_EXPECTED, prompt, paths, **changes)

    def rig_job(self, prompt="the lamp keeper, rigged", paths=(STATE,), **changes):
        """The model job's `assets/state.json` copied from the pull's branch onto `trunk`, the ticket at
        `stage:rigging` and a rig job of ``refs: ["asset:lamp-keeper"]`` on the state."""
        state = gitstate.show(self.w.root, BRANCH, STATE)
        self.assertIsNotNone(state)
        if gitstate.show(self.w.root, "trunk", STATE) != state:
            self.w.commit(STATE, state)
        self.w.edit("issues", 3, labels=[l.replace("stage:assets", "stage:rigging") for l in LABELS])
        return self.job("rig", "asset:lamp-keeper", RIG_EXPECTED, prompt, paths, **changes)

    def factory_run(self, job):
        found = self.w.run(job)
        self.printed.append(found)
        return found

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

    def assert_stage_commit(self, digest, paths):
        """The pull's branch got one commit by `factory`, adding exactly ``paths``; their texts."""
        w = self.w
        self.assertEqual(w.git("rev-list", f"{self.branch_head}..{BRANCH}").split(), [w.head(BRANCH)])
        self.assertEqual(w.git("log", "-1", "--format=%an|%s|%P", BRANCH).strip(),
                         f"factory|stage files: job {digest[:12]}|{self.branch_head}")
        self.assertEqual(w.git("diff", "--name-only", self.branch_head, BRANCH).split(), sorted(paths))
        return {rel: gitstate.show(w.root, BRANCH, rel) for rel in paths}

    def clean_model(self):
        self.branch_head = self.w.head(BRANCH)
        job, digest = self.model_job()
        self.assertEqual(self.factory_run(job), (0, line(digest, "DONE", "0.3", "5"), ""))
        return digest

    def model_state(self, kind="character", model=MODEL, height_m=1.57):
        return {"asset": "lamp-keeper", "kind": kind, "sha256": sha(model), "triangles": 1, "materials": 1,
                "textures": [{"height": 1024, "name": "lamp_keeper_body", "width": 2048}], "height_m": height_m,
                "skeleton": None}

    def test_model_job_clean(self):
        digest = self.clean_model()
        files = self.assert_stage_commit(digest, [MODEL_MANIFEST, STATE])
        self.assertEqual(files[STATE], stage_json(self.model_state()))
        self.assertEqual(self.store.objects, {sha(MODEL): MODEL})
        self.assertIsNone(self.w.manifest())
        self.assertEqual([r["tool"] for r in self.w.rows("run")], ["model3d"])
        [post] = self.vendor.made("POST")
        self.assertEqual(post["path"], "/" + vendor.MODEL_PATH)
        self.assertEqual(json.loads(post["body"]), {"image_url": f"{self.public.base}/pub/{sha(IMAGE)}",
                                                    "face_limit": 90000})
        self.assertEqual(post["headers"]["authorization"], f"Key {MODEL_KEY}")
        self.assertEqual([r["path"] for r in self.vendor.made()], [
            "/" + vendor.MODEL_PATH, REQUEST + "/status", REQUEST + "/status", REQUEST, "/files/model.glb"])
        self.assertNotIn("authorization", self.vendor.made()[-1]["headers"])
        self.assertEqual(os.listdir(self.tmp), [])

    def test_model_manifest(self):
        self.branch_head = self.w.head(BRANCH)
        job, digest = self.model_job()
        self.assertEqual(self.factory_run(job), (0, line(digest, "DONE", "0.3", "5"), ""))
        files = self.assert_stage_commit(digest, [MODEL_MANIFEST, STATE])
        manifest = json.loads(files[MODEL_MANIFEST])
        self.assertEqual(files[MODEL_MANIFEST], stage_json(manifest))
        inputs = json.loads(Path(job).read_text())["inputs"]
        self.assertEqual([i["path"] for i in inputs][1:], [vendor.MANIFEST, SHEET, LEDGER])
        self.assertEqual(manifest, {
            "job": digest, "engine": vendor.MODEL_PATH, "flags": [], "inputs": inputs, "seeds": {},
            "outputs": [output("model.glb", MODEL), output("state.json", files[STATE].encode())]})

    def test_model_kind(self):
        self.w.commit(PLACE, "# The lamp keeper's lamp\n")
        for kind, paths, model, height_m in (("prop", (vendor.MANIFEST, LEDGER), RAW, 1.7),
                                             ("set", (vendor.MANIFEST, PLACE), RAW, 1.7),
                                             ("set", (vendor.MANIFEST, PLACE, LEDGER), MODEL, 1.57),
                                             ("character", (vendor.MANIFEST, PLACE, SHEET, LEDGER), MODEL, 1.57)):
            with self.subTest(kind=kind, paths=paths):
                job, digest = self.model_job(f"the lamp keeper as a {kind} of {paths}", paths)
                self.assertEqual(self.factory_run(job), (0, line(digest, "DONE", "0.3", "5"), ""))
                self.assertEqual(json.loads(gitstate.show(self.w.root, BRANCH, STATE)),
                                 self.model_state(kind, model, height_m))

    def test_face_limit(self):
        for kind, paths, limit in (("character", (vendor.MANIFEST, SHEET, LEDGER), 90000),
                                   ("prop", (vendor.MANIFEST,), 18000)):
            with self.subTest(kind):
                self.vendor.requests.clear()
                job, digest = self.model_job(f"the lamp keeper as a {kind}", paths)
                self.assertEqual(self.factory_run(job), (0, line(digest, "DONE", "0.3", "5"), ""))
                [post] = self.vendor.made("POST")
                self.assertEqual(post["path"], "/" + vendor.MODEL_PATH)
                self.assertEqual(json.loads(post["body"]), {"image_url": f"{self.public.base}/pub/{sha(IMAGE)}",
                                                            "face_limit": limit})

    def test_face_limit_below_every_budget(self):
        self.assertEqual(model3d.FACE_LIMIT.keys(), TRIANGLE_BUDGETS.keys())
        for kind, budget in TRIANGLE_BUDGETS.items():
            with self.subTest(kind):
                self.assertEqual(model3d.FACE_LIMIT[kind], budget * 9 // 10)
                self.assertLess(model3d.FACE_LIMIT[kind], budget)

    def test_model_scaled_to_the_canon_height(self):
        self.assertEqual(glb.summary(RAW)["height_m"], 1.7)
        self.clean_model()
        state = json.loads(gitstate.show(self.w.root, BRANCH, STATE))
        self.assertEqual(state["height_m"], 1.57)
        [stored] = self.store.objects.values()
        self.assertEqual(sha(stored), state["sha256"])
        self.assertEqual(glb.summary(stored), {k: state[k] for k in
                                               ("triangles", "materials", "textures", "height_m", "skeleton")})

    def test_no_height_no_spend(self):
        cases = (("no ledger", (vendor.MANIFEST, SHEET), None),
                 ("no height row", (vendor.MANIFEST, SHEET, LEDGER), "lamp-keeper\tlamp\toil\nlamp\theight\t2 m\n"),
                 ("not <number> m", (vendor.MANIFEST, SHEET, LEDGER), "lamp-keeper\theight\ttall\n"))
        for name, paths, rows in cases:
            with self.subTest(name):
                if rows is not None:
                    self.w.commit(LEDGER, LEDGER_HEADER + rows)
                head = self.w.head(BRANCH)
                job, digest = self.model_job(f"the lamp keeper with {name}", paths)
                self.assert_failed(job, digest, "model3d", vendor.NO_HEIGHT, "0", "0")
                self.assertEqual(self.vendor.made(), [])
                self.assertEqual(self.public.made(), [])
                self.assertEqual(self.store.objects, {})
                self.assertEqual(self.w.head(BRANCH), head)
        self.w.commit(PLACE, "# The lamp keeper's lamp\n")
        job, digest = self.model_job("the lamp keeper as a set", (vendor.MANIFEST, PLACE, LEDGER))
        self.assertEqual(self.factory_run(job), (0, line(digest, "DONE", "0.3", "5"), ""))
        self.assertEqual(json.loads(gitstate.show(self.w.root, BRANCH, STATE)), self.model_state("set", RAW, 1.7))
        self.assertEqual(self.store.objects, {sha(RAW): RAW})

    def test_not_scalable(self):
        self.assertEqual(glb.summary(SPARSE)["height_m"], 1.7)
        self.vendor.answer("GET", "/files/model.glb", (200, SPARSE))
        head = self.w.head(BRANCH)
        job, digest = self.model_job("a sparse model")
        self.assert_failed(job, digest, "model3d", vendor.NOT_SCALABLE, "0.3")
        self.assertEqual(os.listdir(self.w.job_dir(digest) / "out"), [])
        self.assertEqual(self.store.objects, {})
        self.assertEqual(self.w.head(BRANCH), head)

    def test_model_state_is_deterministic(self):
        first = self.clean_model()
        job, second = self.model_job("the lamp keeper, again")
        self.assertEqual(self.factory_run(job), (0, line(second, "DONE", "0.3", "5"), ""))
        texts = [(self.w.job_dir(d) / "out" / "state.json").read_bytes() for d in (first, second)]
        self.assertEqual(texts[0], texts[1])
        self.assertEqual(texts[0], gitstate.show(self.w.root, BRANCH, STATE).encode())

    def test_broken_model(self):
        for name, data in BROKEN.items():
            with self.subTest(name):
                self.vendor.answer("GET", "/files/model.glb", (200, data))
                head = self.w.head(BRANCH)
                job, digest = self.model_job(f"the model is {name}")
                self.assert_failed(job, digest, "model3d", vendor.NOT_A_GLB, "0.3")
                self.assertEqual(os.listdir(self.w.job_dir(digest) / "out"), [])
                self.assertEqual(self.store.objects, {})
                self.assertEqual(self.w.head(BRANCH), head)
        self.vendor.answer("GET", "/files/model.glb", (200, RAW))
        self.clean_model()
        self.assertEqual(list(self.store.objects), [sha(MODEL)])

    def test_rig_job_clean(self):
        self.clean_model()
        self.vendor.requests.clear()
        job, digest = self.rig_job()
        self.branch_head = self.w.head(BRANCH)
        self.assertEqual(self.factory_run(job), (0, line(digest, "DONE", "0.5", "5"), ""))
        files = self.assert_stage_commit(digest, [RIG_MANIFEST])
        self.assertEqual(self.store.objects, {sha(MODEL): MODEL, sha(RIG): RIG})
        self.assertIsNone(self.w.manifest())
        manifest = json.loads(files[RIG_MANIFEST])
        self.assertEqual(files[RIG_MANIFEST], stage_json(manifest))
        inputs = json.loads(Path(job).read_text())["inputs"]
        state = gitstate.show(self.w.root, "trunk", STATE).encode()
        self.assertEqual(inputs[1:], [{"path": STATE, "sha256": sha(state)}])
        self.assertEqual(manifest, {
            "job": digest, "engine": vendor.RIG_PATH, "flags": [], "inputs": inputs, "seeds": {},
            "outputs": [output("rig.glb", RIG)], "skeleton": SKELETON})
        [post] = self.vendor.made("POST")
        self.assertEqual(post["path"], "/" + vendor.RIG_PATH)
        model_url = json.loads(post["body"])["model_url"]
        self.assertTrue(model_url.startswith("data:application/octet-stream;base64,"))
        self.assertEqual(base64.b64decode(model_url.split(",", 1)[1]), MODEL)
        self.assertEqual(sorted(json.loads(post["body"])), ["model_url"])
        self.assertEqual(post["headers"]["authorization"], f"Key {RIG_KEY}")
        self.assertEqual([(r["method"], r["path"]) for r in self.vendor.made()], [
            ("POST", "/" + vendor.RIG_PATH), ("GET", RIG_REQUEST + "/status"), ("GET", RIG_REQUEST + "/status"),
            ("GET", RIG_REQUEST), ("GET", RIG_FILE)])
        self.assertTrue(all(r["headers"]["authorization"] == f"Key {RIG_KEY}" for r in self.vendor.made()[1:-1]))
        self.assertNotIn("authorization", self.vendor.made()[-1]["headers"])
        self.assertEqual([r["tool"] for r in self.w.rows("run")], ["model3d", "rig"])
        self.assertEqual(os.listdir(self.tmp), [])

    def test_rig_without_skin(self):
        self.clean_model()
        self.vendor.answer("GET", RIG_FILE, (200, UNSKINNED))
        job, digest = self.rig_job("a rig with no skin")
        self.assertEqual(self.factory_run(job), (0, line(digest, "DONE", "0.5", "5"), ""))
        manifest = json.loads(gitstate.show(self.w.root, BRANCH, RIG_MANIFEST))
        self.assertIsNone(manifest["skeleton"])
        self.assertEqual(manifest["outputs"], [output("rig.glb", UNSKINNED)])

    def test_broken_rig(self):
        self.clean_model()
        for name, data in BROKEN.items():
            with self.subTest(name):
                self.vendor.answer("GET", RIG_FILE, (200, data))
                head = self.w.head(BRANCH)
                job, digest = self.rig_job(f"the rig is {name}")
                self.assert_failed(job, digest, "rig", vendor.NOT_A_GLB, "0.5")
                self.assertEqual(os.listdir(self.w.job_dir(digest) / "out"), [])
                self.assertEqual(list(self.store.objects), [sha(MODEL)])
                self.assertEqual(self.w.head(BRANCH), head)
                self.assertIsNone(gitstate.show(self.w.root, BRANCH, RIG_MANIFEST))

    def test_request_defects(self):
        for reason, changes in ((vendor.NOT_A_REQUEST, {"size": "tall"}),
                                (vendor.NOT_A_REQUEST, {"for": ""}),
                                (vendor.NO_REFERENCE, {"refs": []}),
                                (vendor.NOT_IN_MANIFEST, {"refs": ["media:lamp-keeper-sketch"]})):
            with self.subTest(tool="model3d", reason=reason, changes=changes):
                job, digest = self.model_job(f"model3d: {reason} {changes}", **changes)
                self.assert_failed(job, digest, "model3d", reason, "0", "0")
                self.assertEqual(self.vendor.requests, [])
        self.clean_model()
        self.vendor.requests.clear()
        for reason, paths, changes in ((vendor.NOT_A_REQUEST, (STATE,), {"size": "tall"}),
                                       (vendor.NOT_A_REQUEST, (STATE,), {"for": 7}),
                                       (vendor.NO_MODEL, (STATE,), {"refs": []}),
                                       (vendor.NO_MODEL, (STATE,), {"refs": ["media:lamp-keeper-ref"]}),
                                       (vendor.NO_MODEL, (STATE,), {"refs": ["asset:lamp-keeper", "asset:lamp"]}),
                                       (vendor.NO_STATE, (), {}),
                                       (vendor.NO_STATE, (vendor.MANIFEST,), {})):
            with self.subTest(tool="rig", reason=reason, changes=changes, paths=paths):
                job, digest = self.rig_job(f"rig: {reason} {changes} {paths}", paths, **changes)
                self.assert_failed(job, digest, "rig", reason, "0", "0")
                self.assertEqual(self.vendor.requests, [])
        self.assertEqual(len(self.w.rows("run")), 12)

    def test_key_refused_is_down(self):
        self.vendor.answer("POST", "/" + vendor.MODEL_PATH, (401, b'{"detail": "unauthorized"}'))
        job, digest = self.model_job()
        for number, api in ((5, self.vendor.base), (6, f"http://127.0.0.1:{free_port()}")):
            with self.subTest(api=api), mock.patch.object(vendor, "MODEL_API", api):
                self.assertEqual(self.factory_run(job), (2, "", f"run: resource gen.model3d is down (#{number})\n"))
                self.assertEqual(self.w.rows("run"), [])
                self.assertFalse(self.w.job_dir(digest).exists())
                self.w.edit("issues", number, state="closed")
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
        self.clean_model()
        self.vendor.answer("GET", RIG_REQUEST + "/status", self.vendor.fixture("rig/status-completed", status="FAILED"))
        job, digest = self.rig_job()
        self.assert_failed(job, digest, "rig", vendor.VENDOR_FAILED, "0.5")
        self.assertEqual([r["tool"] for r in self.w.rows("run")], ["model3d"] * 3 + ["rig"])

    def test_rig_vendor_cannot_rig(self):
        self.clean_model()
        self.vendor.answer("GET", RIG_REQUEST, self.vendor.fixture("rig/result-invalid"))
        job, digest = self.rig_job("not a valid GLB")
        self.assert_failed(job, digest, "rig", vendor.VENDOR_FAILED, "0.5")
        self.assertIn(("GET", RIG_REQUEST), [(r["method"], r["path"]) for r in self.vendor.made()])
        self.assertNotIn(RIG_FILE, [r["path"] for r in self.vendor.made()])
        self.assertEqual(os.listdir(self.w.job_dir(digest) / "out"), [])
        self.assertEqual(list(self.store.objects), [sha(MODEL)])
        self.assertIsNone(gitstate.show(self.w.root, BRANCH, RIG_MANIFEST))
        self.assertEqual(os.listdir(self.tmp), [])

    def test_rig_result_without_glb(self):
        self.clean_model()
        status, body = self.vendor.fixture("rig/result")
        body = json.loads(body)
        del body["rigged_character_glb"]
        self.vendor.answer("GET", RIG_REQUEST, (status, json.dumps(body).encode()))
        job, digest = self.rig_job("no rigged GLB")
        self.assert_failed(job, digest, "rig", vendor.VENDOR_FAILED, "0.5")
        self.assertEqual([r["path"] for r in self.vendor.made()][-1], RIG_REQUEST)
        self.assertEqual(list(self.store.objects), [sha(MODEL)])

    def test_rig_urls_outside_the_api(self):
        self.clean_model()
        outside = "https://elsewhere.invalid/fal-ai/meshy/requests/x"
        for name, url, path in (("status_url", outside + "/status", RIG_REQUEST + "/status"),
                                ("response_url", outside, RIG_REQUEST),
                                ("status_url", f"{self.vendor.base}.invalid{RIG_REQUEST}/status",
                                 RIG_REQUEST + "/status")):
            with self.subTest(name=name, url=url):
                self.vendor.answer("POST", "/" + vendor.RIG_PATH, self.vendor.fixture("rig/submit", **{name: url}))
                self.vendor.requests.clear()
                prompt = f"{name} {url}"
                job, digest = self.rig_job(prompt)
                self.assert_failed(job, digest, "rig", vendor.VENDOR_FAILED, "0.5")
                self.assertEqual([r["path"] for r in self.vendor.made()], ["/" + vendor.RIG_PATH])
                self.assertNotIn(path, [r["path"] for r in self.vendor.made()])
        self.assertEqual(list(self.store.objects), [sha(MODEL)])

    def test_data_uri_limit(self):
        self.assertEqual(vendor.DATA_URI_BYTES, 30_000_000)

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
        self.assertIsNone(gitstate.show(self.w.root, BRANCH, STATE))
        self.assertEqual(self.store.objects, {})

    def test_rig_input(self):
        self.clean_model()
        self.vendor.requests.clear()
        with mock.patch.object(vendor, "DATA_URI_BYTES", len(MODEL) - 1):
            job, digest = self.rig_job("too large")
            self.assert_failed(job, digest, "rig", vendor.TOO_LARGE, "0", "0")
        self.store.objects.clear()
        job, digest = self.rig_job("not in the store")
        self.assert_failed(job, digest, "rig", vendor.NOT_IN_STORE, "0", "0")
        self.assertEqual(self.vendor.requests, [])
        self.assertEqual(os.listdir(self.tmp), [])

    def test_card_scan_is_refused(self):
        self.ffmpeg.rgb = card().rgb()
        job, digest = self.model_job("the lamp keeper, from a card")
        self.assert_failed(job, digest, "model3d", vendor.FRAMED, "0", "0")
        self.assertEqual(vendor.FRAMED, "reference is framed like a card scan")
        self.assertEqual(self.vendor.requests, [])
        self.assertEqual([(r["method"], r["path"]) for r in self.public.made()], [("GET", f"/pub/{sha(IMAGE)}")])
        self.assertEqual(self.ffmpeg.inputs, [(cardscan.DECODE, IMAGE, {"capture_output": True, "timeout": 60})])
        self.assertEqual(os.listdir(self.w.job_dir(digest) / "out"), [])
        self.assertEqual(os.listdir(self.tmp), [])
        self.assertEqual(self.store.objects, {})

    def test_reference_not_decoded(self):
        self.ffmpeg.code = 1
        job, digest = self.model_job("the reference does not decode")
        self.assert_failed(job, digest, "model3d", vendor.NOT_DECODED, "0", "0")
        self.assertEqual(vendor.NOT_DECODED, "reference image could not be decoded")
        self.assertEqual(len(self.public.made("GET")), 1)
        self.ffmpeg.code = 0
        with mock.patch.object(vendor, "REFERENCE_MAX_BYTES", len(IMAGE) - 1):
            job, digest = self.model_job("the reference is too large")
            self.assert_failed(job, digest, "model3d", vendor.NOT_DECODED, "0", "0")
        self.assertEqual(len(self.public.made("GET")), 1)
        self.assertEqual(self.vendor.requests, [])
        self.assertEqual(os.listdir(self.tmp), [])

    def test_reference_link_down(self):
        self.public.answer("GET", f"/pub/{sha(IMAGE)}", (404, b""))
        job, digest = self.model_job("the public link answers 404")
        before = len(self.w.log())
        self.assertEqual(self.factory_run(job), (2, "", "run: resource gen.model3d is down (#5)\n"))
        self.assertEqual(self.w.rows("run"), [])
        self.assertFalse(self.w.job_dir(digest).exists())
        [entry] = self.w.log()[before:]
        self.assertEqual((entry["op"], entry["number"], entry["title"]), ("create_issue", 5, "[DOWN] gen.model3d"))
        self.assertEqual(self.vendor.requests, [])
        self.assertEqual(os.listdir(self.tmp), [])

    def test_reference_size_not_in_manifest(self):
        self.w.commit(vendor.MANIFEST, "id\tsha256\tbytes\tsource\n"
                      f"lamp-keeper-ref\t{sha(IMAGE)}\tx\tupload\n")
        job, digest = self.model_job("the manifest size is x")
        self.assert_failed(job, digest, "model3d", vendor.NOT_IN_MANIFEST, "0", "0")
        self.assertEqual(self.public.requests, [])
        self.assertEqual(self.vendor.requests, [])

    def test_reference_check_leaks_nothing(self):
        self.ffmpeg.rgb = card().rgb()
        job, digest = self.model_job("a card")
        self.assert_failed(job, digest, "model3d", vendor.FRAMED, "0", "0")
        self.ffmpeg.rgb = figure().rgb()
        self.clean_model()
        texts = [err for _, _, err in self.printed] + [p.read_text() for p in self.w.state.rglob("result.json")]
        self.assertEqual(len(texts), 4)
        for text in texts:
            for value in (self.public.base, "/pub/", str(self.tmp)):
                self.assertNotIn(value, text)
        self.assert_no_leak()

    def test_nothing_leaks(self):
        self.vendor.answer("POST", "/" + vendor.MODEL_PATH, (401, b""))
        job, model = self.model_job()
        self.assertEqual(self.factory_run(job)[0], 2)
        self.vendor.answer("POST", "/" + vendor.MODEL_PATH, self.vendor.fixture("model/submit"))
        self.w.edit("issues", 5, state="closed")
        self.assertEqual(self.factory_run(job), (0, line(model, "DONE", "0.3", "5"), ""))
        self.vendor.answer("GET", RIG_REQUEST + "/status", self.vendor.fixture("rig/status-completed", status="FAILED"))
        job, digest = self.rig_job()
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
            f"SECRETS_DIR={self.root / 'sec'}\nASSET_STORE=https://store.invalid/b\n"
            "MEDIA_PUBLIC_BASE=https://media.invalid/pub\n")
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
