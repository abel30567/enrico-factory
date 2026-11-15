"""Tests for kit/lib/renderjob.py and `ec2.Node.run`: one render job from `start()` to `stop()`, offline.

The node is `ec2.Node` with `RENDER_API_URL` on `Ec2Replay` (tests/test_ec2.py) of the session
`tests/fixtures/render/ec2/start-stop/`, `UserData` and `ClientToken` loose. The asset store is `JobStore`, the
`AssetStore` of tests/test_assets.py that also takes a link signed in its query (`sigv4.presign`) and checks
its signature. Once `links.txt` is in the store, and before the store answers its `PUT`, a fake node thread
reads `in.tar` by its signed link and puts `out.tar`, `log.txt` and `status.json` by theirs: `out.tar` holds
tests/fixtures/renderjob/out-clean/ and the frames the test writes (24 frames 640x360 of one solid colour each,
an EXR placeholder beside each, the re-rendered frames), or a seeded copy. `ffmpeg`, `ffprobe`, `tesseract`
and the metric `cvvdp` are copies of tests/fixtures/frames/fake_tool.py first on `PATH`. The node's clock and
sleep are replaced, so no test waits. Nothing reaches a host other than 127.0.0.1.
"""

import base64
import contextlib
import hashlib
import io
import json
import os
import shutil
import struct
import sys
import tarfile
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from kit.lib import ec2, golden, renderjob, sigv4  # noqa: E402
from test_assets import KEY_ID, REGION, SECRET, AssetStore  # noqa: E402
from test_ec2 import BILLED, KEY, NOON, SETTINGS, Ec2Replay, at, session  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "renderjob"
FAKE = ROOT / "tests" / "fixtures" / "frames" / "fake_tool.py"
SCENE = ROOT / "kit" / "evals" / "golden" / "lit-render" / "scene.py"
TOOLS = ("ffmpeg", "ffprobe", "tesseract", "cvvdp")
TOKEN = "seeded-pull-token-5d2e81"
IMAGE = "registry.invalid/engine@sha256:" + "0" * 64
JOB = "4f" * 32
STAGE = "production/ep01/Q1-1/render"
FIRST, LAST, SAMPLED = 0, 23, [0, 11, 23]
WIDTH, HEIGHT = 640, 360
EXPIRES = (75 + 15) * 60
OUT_NAMES = sorted(["engine-build.log", "engine-render.log", "node.json", "scene.json",
                    *(f"frame-{f:06d}.{e}" for f in range(FIRST, LAST + 1) for e in ("png", "exr")),
                    *(f"rerender-{f:06d}.png" for f in SAMPLED)])
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def png(width, height, colour):
    """An 8-bit RGB PNG of one colour, every row with filter type 0."""
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    raw = (b"\x00" + bytes(colour) * width) * height
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def frames():
    """The frames of the clean job: one solid colour per frame, an EXR placeholder, the re-rendered frames."""
    found = {}
    for frame in range(FIRST, LAST + 1):
        found[f"frame-{frame:06d}.png"] = png(WIDTH, HEIGHT, (40 + frame * 5, 90, 160 - frame * 3))
        found[f"frame-{frame:06d}.exr"] = b"fake exr"
    for frame in SAMPLED:
        found[f"rerender-{frame:06d}.png"] = found[f"frame-{frame:06d}.png"]
    return found


def tar(members):
    """An archive of ``(name, data)`` pairs, or ``(TarInfo, None)`` for a member that is not a file."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in members:
            if isinstance(name, tarfile.TarInfo):
                archive.addfile(name)
                continue
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def item(path, text):
    return {"path": path, "sha256": sha(text.encode("utf-8")), "text": text}


class JobStore(AssetStore):
    """`AssetStore` that also answers a link signed in its query when its signature is the key's, and runs
    ``on_links`` once `links.txt` is stored, before it answers that `PUT`."""

    def __init__(self, test):
        super().__init__(test)
        self.on_links, self.linked = None, []

    def handle(self, h, method):
        parts = urllib.parse.urlsplit(h.path)
        query = dict(urllib.parse.parse_qsl(parts.query))
        if "X-Amz-Signature" not in query:
            return super().handle(h, method)
        length = int(h.headers.get("Content-Length") or 0)
        body = h.rfile.read(length) if length else b""
        self.linked.append({"method": method, "path": h.path, "body": body})
        name = parts.path.rsplit("/", 1)[-1]
        try:
            time = datetime.strptime(query["X-Amz-Date"], sigv4.STAMP_FORMAT).strftime(sigv4.TIME_FORMAT)
            signed = sigv4.presign(method, self.url(parts.path), self.key, time, int(query["X-Amz-Expires"]))
        except (KeyError, ValueError):
            signed = None
        if self.url(h.path) != signed:
            return self.reply(h, method, 403, b"<Error>SignatureDoesNotMatch</Error>")
        if method == "PUT":
            self.objects[name] = body
            return self.reply(h, method, 200, b"")
        data = self.objects.get(name)
        return self.reply(h, method, 404, b"") if data is None else self.reply(h, method, 200, data)

    def reply(self, h, method, code, payload, extra=None):
        if method == "PUT" and code == 200 and h.path.endswith("/links.txt") and self.on_links:
            self.on_links()
        return super().reply(h, method, code, payload, extra)


class Job(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="renderjob-test-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        lines = FAKE.read_text(encoding="utf-8").splitlines(keepends=True)
        for name in TOOLS:
            tool = self.bin / name
            tool.write_text(f"#!{sys.executable}\n" + "".join(lines[1:]), encoding="utf-8")
            tool.chmod(0o755)
        self.control = {"log": str(self.tmp / "tools.jsonl")}
        self.write_control()
        patcher = mock.patch.dict(os.environ, {"PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
                                               "FACTORY_NOW": NOON})
        patcher.start()
        self.addCleanup(patcher.stop)
        # Every temporary directory of the run goes here, so a test sees what was written beside it.
        self.temp = self.tmp / "temp"
        self.temp.mkdir()
        patcher = mock.patch.object(tempfile, "tempdir", str(self.temp))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.out = self.tmp / "out"
        self.out.mkdir()
        self.secrets = self.tmp / "secrets"
        self.secrets.mkdir()
        for name, text in (("render.node", "\n".join(KEY) + "\n"), ("engine.unreal", TOKEN + "\nsecond line\n"),
                           ("store.assets", f"{KEY_ID}\n{SECRET}\n{REGION}\n")):
            (self.secrets / name).write_text(text, encoding="utf-8")
        self.store = JobStore(self)
        self.store.on_links = self.node_side
        self.members = sorted({**{p.name: p.read_bytes() for p in (FIX / "out-clean").iterdir()},
                               **frames()}.items())
        self.status = b'{"result": "ok"}\n'
        self.inputs = [item(f"{STAGE}/requests/render.json", "{}\n"),
                       item("production/ep01/shots.json", json.dumps([{"shot": "Q1-1", "first": FIRST, "last": LAST}])),
                       item(f"{STAGE}/scene.py", SCENE.read_text(encoding="utf-8"))]
        self.got = {}

    def write_control(self):
        (self.bin / "control.json").write_text(json.dumps(self.control), encoding="utf-8")

    def node_side(self):
        """The fake node, in its own thread: reads `in.tar` by its link, then puts out, log and status."""
        def work():
            links = {}
            for line in self.store.objects["links.txt"].decode("utf-8").splitlines():
                method, name, url = line.split(" ")
                links[name] = (method, url)
            self.got["links"] = links
            with OPENER.open(links["in"][1], timeout=10) as response:
                self.got["in.tar"] = response.read()
            for name, data in (("out", tar(self.members)), ("log", b"NODE 12:01:00 result ok\n"),
                               ("status", self.status)):
                if data is None:
                    continue
                request = urllib.request.Request(links[name][1], data=data, method="PUT")
                with OPENER.open(request, timeout=10) as response:
                    self.got[name] = response.status
        thread = threading.Thread(target=work)
        thread.start()
        thread.join(30)

    def node(self, exchanges=None, **settings):
        replay = Ec2Replay(self, session("start-stop") if exchanges is None else exchanges, KEY,
                           loose=("ClientToken", "UserData"))
        config = {"settings": {**SETTINGS, "RENDER_API_URL": replay.url, "RENDER_RESOLUTION": f"{WIDTH}x{HEIGHT}",
                               "UNREAL_IMAGE": IMAGE, "ASSET_STORE": self.store.url("/b"), **settings},
                  "secrets": {name: str(self.secrets / name) for name in ("render.node", "engine.unreal",
                                                                          "store.assets")}}
        driver = ec2.Node(config["secrets"]["render.node"], config)
        self.moment = at(NOON)
        driver.now = lambda: self.moment
        driver.sleeps = []

        def sleep(seconds):
            driver.sleeps.append(seconds)
            self.moment += timedelta(seconds=seconds)

        driver.sleep = sleep
        return replay, driver

    def job(self):
        return {"hash": JOB, "inputs": self.inputs, "out": str(self.out)}

    def session_run(self, exchanges=None, **settings):
        """``(replay, driver, error or None, billed, printed)`` of start, run and stop."""
        replay, driver = self.node(exchanges, **settings)
        printed, error = io.StringIO(), None
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(printed):
            driver.start()
            try:
                driver.run(self.job())
            except Exception as raised:
                error = raised
            billed = driver.stop()
        for secret in (KEY_ID, SECRET, KEY[1], TOKEN):
            self.assertNotIn(secret, printed.getvalue())
        return replay, driver, error, billed, printed.getvalue()

    def assert_failed(self, message=renderjob.JOB_FAILED, exchanges=None, **settings):
        replay, driver, error, billed, _ = self.session_run(exchanges, **settings)
        self.assertIsInstance(error, renderjob.JobError)
        self.assertEqual(str(error), message)
        self.assertEqual(billed, BILLED)
        self.assertEqual(replay.next, len(replay.exchanges))
        self.assertEqual(os.listdir(self.temp), [])
        return replay, driver


class Clean(Job):
    def golden(self, metric):
        replay, driver, error, billed, _ = self.session_run(**({"PERCEPTUAL_METRIC": "cvvdp"} if metric else {}))
        self.assertIsNone(error)
        self.assertEqual(billed, BILLED)
        self.assertEqual(replay.next, 6)
        self.assertEqual(sorted(os.listdir(self.out)), sorted(OUT_NAMES + ["manifest.json", "render.json",
                                                                            "state.json"]))
        for name, data in self.members:
            self.assertEqual((self.out / name).read_bytes(), data)
        unit = self.tmp / "golden" / "lit-render"
        unit.mkdir(parents=True)
        shutil.copyfile(SCENE, unit / "scene.py")
        for name in golden.RECORDED:
            shutil.copyfile(self.out / name, unit / name)
        self.assertEqual(os.listdir(self.temp), [])
        return golden.unit_problems(unit.parent, "lit-render"), driver

    def test_without_a_metric_the_render_lacks_rerender(self):
        problems, driver = self.golden(metric=False)
        self.assertEqual(problems, ["lit-render/render.json lacks rerender"])
        manifest = json.loads((self.out / "manifest.json").read_text())
        self.assertEqual(sorted(manifest), ["engine", "flags", "inputs", "job", "outputs", "seeds"])
        self.assertEqual((manifest["job"], manifest["engine"]), (JOB, IMAGE))
        node = json.loads((FIX / "out-clean" / "node.json").read_text())
        self.assertEqual(manifest["flags"], node["steps"][1]["command"])
        self.assertEqual(manifest["inputs"], [{"path": "requests/render.json", "sha256": self.inputs[0]["sha256"]},
                                              {"path": "production/ep01/shots.json",
                                               "sha256": self.inputs[1]["sha256"]},
                                              {"path": "scene.py", "sha256": sha(SCENE.read_bytes())}])
        self.assertEqual(manifest["seeds"], {"FixedSeed": 0})
        names = sorted(set(OUT_NAMES) | {"render.json", "state.json"})
        self.assertEqual(manifest["outputs"], [{"name": n, "sha256": sha((self.out / n).read_bytes()),
                                                "bytes": (self.out / n).stat().st_size} for n in names])
        render = json.loads((self.out / "render.json").read_text())
        self.assertEqual((render["count"], render["width"], render["height"], [s["frame"] for s in render["samples"]]),
                         (24, WIDTH, HEIGHT, SAMPLED))
        self.assertEqual(driver.log, "NODE 12:01:00 result ok\n")

    def test_with_a_metric_the_golden_level_passes(self):
        self.control["metric"] = {"stdout": "9.95\n"}
        self.write_control()
        problems, _ = self.golden(metric=True)
        self.assertEqual(problems, [])
        rerender = json.loads((self.out / "render.json").read_text())["rerender"]
        self.assertEqual(rerender, {"frames": SAMPLED, "metric": "cvvdp", "scores": [9.95, 9.95, 9.95]})

    def test_in_tar_holds_the_job(self):
        replay, driver, error, _, _ = self.session_run()
        self.assertIsNone(error)
        with tarfile.open(fileobj=io.BytesIO(self.got["in.tar"])) as archive:
            names = archive.getnames()
            read = {name: archive.extractfile(name).read() for name in names}
        self.assertEqual(names, ["job.json", "scripts/00.py", "job.sh", "build.py"])
        self.assertEqual(json.loads(read["job.json"]), {
            "hash": JOB, "shot": "Q1-1", "stage": "render", "fps": 24, "first": FIRST, "last": LAST,
            "width": WIDTH, "height": HEIGHT, "scripts": ["scripts/00.py"], "assets": {}, "sampled": SAMPLED})
        self.assertEqual(read["scripts/00.py"], SCENE.read_bytes())
        for name in ("job.sh", "build.py"):
            self.assertEqual(read[name], (ROOT / "kit" / "engine" / name).read_bytes())
        # in.tar, then links.txt last; the node's PUTs all answered.
        puts = [r["path"].rsplit("/", 1)[1] for r in self.store.requests if r["method"] == "PUT"]
        self.assertEqual(puts, ["in.tar", "links.txt"])
        self.assertEqual((self.got["out"], self.got["log"], self.got["status"]), (200, 200, 200))
        # The objects of jobs/<nonce>/ are left in the store.
        self.assertTrue({"in.tar", "links.txt", "out.tar", "log.txt", "status.json"} <= set(self.store.objects))
        self.assertTrue(all(r["path"].startswith(f"/b/jobs/{driver.nonce}/") for r in self.store.requests))


class Links(Job):
    def test_every_link_expires_and_holds_no_secret(self):
        replay, driver, error, _, printed = self.session_run()
        self.assertIsNone(error)
        self.assertEqual(printed, "")
        text = self.store.objects["links.txt"].decode("utf-8")
        rows = [line.split(" ") for line in text.splitlines()]
        self.assertEqual([(method, name) for method, name, _ in rows],
                         [("GET", "in"), ("PUT", "out"), ("PUT", "log"), ("PUT", "status")])
        user_data = driver.user_data()
        links = [url for _, _, url in rows] + [driver.links]
        self.assertIn(f"'{driver.links}'", user_data)
        for url in links:
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
            self.assertEqual(query["X-Amz-Expires"], str(EXPIRES))
            self.assertNotIn(SECRET, url)
        self.assertEqual([urllib.parse.urlsplit(url).path.rsplit("/", 1)[1] for url in links],
                         ["in.tar", "out.tar", "log.txt", "status.json", "links.txt"])
        self.assertNotIn(SECRET, text)
        self.assertLess(len(user_data.encode("utf-8")), 16384)
        self.assertEqual(user_data.count(TOKEN), 1)
        self.assertNotIn("second line", user_data)
        self.assertNotIn(SECRET, user_data)
        self.assertEqual(base64.b64decode(replay.requests[1]["UserData"]).decode("utf-8"), user_data)
        self.assertEqual(user_data.splitlines()[2], "shutdown -h +75")
        self.assertIn(f"IMAGE='{IMAGE}'", user_data)
        self.assertNotIn("__", user_data.replace("__pycache__", ""))

    def test_too_large_user_data_is_refused_before_any_request(self):
        replay, driver = self.node()
        with mock.patch.object(ec2, "MAX_USER_DATA", 100):
            with self.assertRaises(ec2.Refused) as raised:
                driver.start()
        self.assertEqual(str(raised.exception), "user data too large")
        self.assertEqual((replay.requests, self.store.requests), ([], []))


class Seeded(Job):
    def test_a_member_outside_the_directory(self):
        self.members.append(("../x", b"outside"))
        self.assert_failed()
        self.assertFalse((self.temp / "x").exists())
        self.assertFalse((self.tmp / "x").exists())
        self.assertEqual(os.listdir(self.out), [])

    def test_a_symbolic_link_member(self):
        link = tarfile.TarInfo("frame-000024.png")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/hostname"
        self.members.append((link, None))
        self.assert_failed()
        self.assertEqual(os.listdir(self.out), [])

    def test_a_name_job_sh_does_not_write(self):
        self.members.append(("notes.txt", b"x"))
        self.assert_failed()

    def test_status_failed(self):
        self.status = b'{"result": "failed", "step": "render"}\n'
        self.assert_failed()
        self.assertEqual(os.listdir(self.out), [])

    def test_no_status_before_the_limit(self):
        # RENDER_MAX_MIN 1: the wait gives up at 6 minutes, after 24 polls of 15 s that saw the node running
        # (and the one sleep of start() until it runs).
        self.status = None
        exchanges = session("start-stop")
        replay, driver = self.assert_failed(exchanges=exchanges[:4] + [exchanges[3]] * 24 + exchanges[4:],
                                            RENDER_MAX_MIN="1")
        self.assertEqual(driver.sleeps, [15] * 25)
        self.assertEqual(self.moment - at(NOON), timedelta(minutes=6, seconds=15))

    def test_the_node_ended_without_a_status(self):
        self.status = None
        exchanges = session("start-stop")
        self.assert_failed(exchanges=exchanges[:4] + [exchanges[5]] + exchanges[4:])

    def test_node_json_without_the_ok_line(self):
        node = json.loads((FIX / "out-clean" / "node.json").read_text())
        node["steps"][0]["markers"] = []
        self.members = [(n, d) if n != "node.json" else (n, (json.dumps(node, sort_keys=True) + "\n").encode())
                        for n, d in self.members]
        self.assert_failed()

    def assert_before_upload(self, message):
        self.assert_failed(message, exchanges=None)
        self.assertEqual([r for r in self.store.requests if r["method"] == "PUT"], [])
        self.assertEqual(self.store.linked, [])

    def test_no_input_named_scene_py(self):
        self.inputs = [i for i in self.inputs if not i["path"].endswith("scene.py")]
        self.assert_before_upload("no input named scene.py")

    def test_no_input_ending_shots_json(self):
        self.inputs = [i for i in self.inputs if not i["path"].endswith("shots.json")]
        self.assert_before_upload("no input ending shots.json")


class Describe(unittest.TestCase):
    def job(self, *inputs):
        return {"hash": JOB, "inputs": list(inputs), "out": "out"}

    def test_scripts_in_input_order_and_the_stage_of_the_last(self):
        level = SCENE.read_text(encoding="utf-8")
        job = self.job(item("production/ep01/Q1-1/layout/scene.py", "x = 1\n"),
                       item("production/ep01/shots.json", json.dumps([{"shot": "Q1-1", "first": 24, "last": 47}])),
                       item("production/ep01/Q1-1/lighting/scene.py", level.replace('"render"', '"lighting"')))
        found = renderjob.describe(job, {"RENDER_RESOLUTION": "1920x1080"})
        self.assertEqual(found, {"hash": JOB, "shot": "Q1-1", "stage": "lighting", "fps": 24, "first": 24, "last": 47,
                                 "width": 1920, "height": 1080, "scripts": ["scripts/00.py", "scripts/01.py"],
                                 "assets": {}, "sampled": []})

    def test_refusals(self):
        scene = item(f"{STAGE}/scene.py", SCENE.read_text(encoding="utf-8"))
        shots = item("production/ep01/shots.json", json.dumps([{"shot": "Q1-1", "first": 0, "last": 23}]))
        cases = ((self.job(scene), {"RENDER_RESOLUTION": "640x360"}, "no input ending shots.json"),
                 (self.job(shots), {"RENDER_RESOLUTION": "640x360"}, "no input named scene.py"),
                 (self.job(scene, item("production/ep01/shots.json", "[]")), {"RENDER_RESOLUTION": "640x360"},
                  "shots.json has no row {shot, first, last} for shot Q1-1"),
                 (self.job(scene, shots), {"RENDER_RESOLUTION": "640"}, "RENDER_RESOLUTION is not <width>x<height>"),
                 (self.job(item(f"{STAGE}/scene.py", "x = 1\n"), shots), {"RENDER_RESOLUTION": "640x360"},
                  "the unit's LEVEL has no fps"))
        for job, settings, message in cases:
            with self.subTest(message=message):
                with self.assertRaises(renderjob.JobError) as raised:
                    renderjob.describe(job, settings)
                self.assertEqual(str(raised.exception), message)

    def test_seeds_of_the_scripts(self):
        job = self.job(item(f"{STAGE}/scene.py", "import random\n\nR = random.Random(7)\nS = random.Random(x)\n"),
                       item("production/ep01/Q1-1/layout/scene.py", "import random\nR = random.Random(3)\n"))
        self.assertEqual(renderjob.seeds(job, STAGE),
                         {"FixedSeed": 0, "scene.py:3": 7, "production/ep01/Q1-1/layout/scene.py:2": 3})


if __name__ == "__main__":
    unittest.main()
