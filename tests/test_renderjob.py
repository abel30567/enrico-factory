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
import copy
import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
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

from kit.lib import ec2, filing, golden, lint, renderjob, sigv4  # noqa: E402
from test_assets import KEY_ID, REGION, SECRET, AssetStore  # noqa: E402
from test_ec2 import BILLED, KEY, NOON, SETTINGS, Ec2Replay, at, session  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "renderjob"
FAKE = ROOT / "tests" / "fixtures" / "frames" / "fake_tool.py"
SCENE = ROOT / "kit" / "evals" / "golden" / "lit-render" / "scene.py"
DROP = ROOT / "kit" / "evals" / "golden" / "prop-drop" / "scene.py"
TOOLS = ("ffmpeg", "ffprobe", "tesseract", "cvvdp")
TOKEN = "seeded-pull-token-5d2e81"
IMAGE = "registry.invalid/engine@sha256:" + "0" * 64
JOB = "4f" * 32
STAGE = "production/ep01/Q1-1/render"
SIM = "production/ep01/Q1-1/simulation"
EDIT = "episodes/ep01/reel/edit-list.json"
# The asset of tests/test_asset_jobs.py.
BIN = hashlib.sha256(b"wren glb").hexdigest()
WREN = {"asset": "wren", "kind": "character", "sha256": BIN}
# The sha256 of the clean job's state.json, measured on main at 51aee1b.
CLEAN_STATE = "f3671acbd47faaecbf30672be2942bf8ed4d9237816379ec4caad7ab501105a9"
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


def frames(first=FIRST, last=LAST, sampled=SAMPLED):
    """The frames of the clean job: one solid colour per frame, an EXR placeholder, the re-rendered frames."""
    found = {}
    for frame in range(first, last + 1):
        found[f"frame-{frame:06d}.png"] = png(WIDTH, HEIGHT, ((40 + frame * 5) % 256, 90, (160 - frame * 3) % 256))
        found[f"frame-{frame:06d}.exr"] = b"fake exr"
    for frame in sampled:
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


def asset_item(state, digest=BIN, text=None):
    """The input `asset:wren` with ``sha256`` ``digest``; its text ``text``, else ``state`` as JSON (None for None)."""
    if text is None and state is not None:
        text = json.dumps(state, indent=1) + "\n"
    return {"path": "asset:wren", "sha256": digest, "text": text}


def with_wren(members, like, names=("scene.json",)):
    """``members`` with an actor `wren`, a copy of the actor labelled ``like``, in each scene of ``names``."""
    found = []
    for name, data in sorted(members):
        if name in names:
            scene = json.loads(data.decode("utf-8"))
            wren = copy.deepcopy(next(actor for actor in scene["actors"] if actor["label"] == like))
            wren["label"] = "wren"
            scene["actors"].append(wren)
            data = (json.dumps(scene, sort_keys=True, indent=2) + "\n").encode("utf-8")
        found.append((name, data))
    return found


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
        self.log = b"NODE 12:01:00 result ok\n"
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
            for name, data in (("out", None if self.members is None else tar(self.members)), ("log", self.log),
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

    def test_an_agreeing_edit_list_travels_into_the_manifest(self):
        edit = item(EDIT, json.dumps({"fps": 24}))
        self.inputs.insert(2, edit)
        problems, _ = self.golden(metric=False)
        self.assertEqual(problems, ["lit-render/render.json lacks rerender"])
        manifest = json.loads((self.out / "manifest.json").read_text())
        self.assertIn({"path": EDIT, "sha256": edit["sha256"]}, manifest["inputs"])

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


class Assets(Job):
    """A job with the input `asset:wren` and an actor `wren` in its `scene.json` (#515)."""

    def setUp(self):
        super().setUp()
        self.members = with_wren(self.members, "ball")
        self.inputs.append(asset_item(WREN))

    def test_the_actor_of_an_asset_takes_its_locked_state(self):
        replay, driver, error, billed, _ = self.session_run()
        self.assertIsNone(error)
        self.assertEqual(billed, BILLED)
        self.assertEqual(replay.next, 6)
        actors = json.loads((self.out / "state.json").read_text())["actors"]
        wren = next(actor for actor in actors if actor["id"] == "wren")
        self.assertEqual((wren["kind"], wren["asset"], wren["sha256"]), ("character", "asset:wren", BIN))
        with tarfile.open(fileobj=io.BytesIO(self.got["in.tar"])) as archive:
            described = json.loads(archive.extractfile("job.json").read())
        self.assertEqual(described["assets"], {"wren": BIN})
        manifest = json.loads((self.out / "manifest.json").read_text())
        self.assertIn({"path": "asset:wren", "sha256": BIN}, manifest["inputs"])

    def test_a_missing_or_other_state_is_refused_before_any_upload(self):
        cases = (("no text", asset_item(None)), ("not json", asset_item(WREN, text="not json\n")),
                 ("not an object", asset_item(WREN, text=json.dumps([WREN]))),
                 ("another sha256", asset_item(WREN, "0" * 64)), ("another asset", asset_item(dict(WREN, asset="bo"))),
                 ("no kind of KINDS", asset_item(dict(WREN, kind="tree"))))
        for name, last in cases:
            with self.subTest(name):
                self.inputs[-1] = last
                self.store.requests.clear()
                self.assert_failed("input asset:wren does not match its locked state")
                self.assertEqual([r for r in self.store.requests if r["method"] == "PUT"], [])
                self.assertEqual(os.listdir(self.out), [])


class NoAsset(Job):
    def test_the_state_of_a_job_without_an_asset_is_as_before(self):
        _, _, error, _, _ = self.session_run()
        self.assertIsNone(error)
        self.assertEqual(sha((self.out / "state.json").read_bytes()), CLEAN_STATE)


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

    def test_a_rerun_scene_at_another_stage(self):
        self.members.append(("rerun-scene.json", (FIX / "out-simulation" / "rerun-scene.json").read_bytes()))
        self.assert_failed()
        self.assertEqual(os.listdir(self.out), [])

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

    def test_an_edit_list_that_disagrees_with_the_level(self):
        self.inputs.insert(2, item(EDIT, json.dumps({"fps": 25})))
        self.assert_before_upload(f"the unit's LEVEL has fps 24, {EDIT} has fps 25")

    def test_a_member_outside_the_directory_leaves_no_step(self):
        self.members.append(("../x", b"outside"))
        _, driver = self.assert_failed()
        self.assertEqual(driver.failure, {"log": f"jobs/{driver.nonce}/log.txt", "step": None,
                                          "tail": ["NODE 12:01:00 result ok"], "check": None})

    def test_no_status_leaves_no_step_and_no_tail(self):
        self.status = None
        exchanges = session("start-stop")
        _, driver = self.assert_failed(exchanges=exchanges[:4] + [exchanges[5]] + exchanges[4:])
        self.assertEqual(driver.failure, {"log": f"jobs/{driver.nonce}/log.txt", "step": None, "tail": [],
                                          "check": None})

    def test_a_refusal_of_describe_leaves_no_failure(self):
        self.inputs = [i for i in self.inputs if not i["path"].endswith("scene.py")]
        _, driver = self.assert_failed("no input named scene.py")
        self.assertIsNone(driver.failure)

    def test_the_clean_job_leaves_no_failure(self):
        _, driver, error, _, _ = self.session_run()
        self.assertIsNone(error)
        self.assertIsNone(driver.failure)

    def gets(self):
        return [r["path"].rsplit("/", 1)[1] for r in self.store.requests if r["method"] == "GET"]

    def test_each_step_of_a_failed_node(self):
        for step in renderjob.STEPS:
            with self.subTest(step=step):
                self.store.requests.clear()
                self.status = json.dumps({"result": "failed", "step": step}).encode()
                _, driver = self.assert_failed()
                self.assertEqual(driver.failure["step"], step)
                self.assertEqual(self.gets(), ["status.json", "log.txt"])
                self.assertEqual(os.listdir(self.out), [])

    def test_a_status_without_a_step_of_steps(self):
        for status in ({"result": "failed", "step": "warp"}, {"result": "failed"}):
            with self.subTest(status=status):
                self.store.requests.clear()
                self.status = json.dumps(status).encode()
                _, driver = self.assert_failed()
                self.assertIsNone(driver.failure["step"])
                self.assertEqual(self.gets(), ["status.json", "out.tar", "log.txt"])

    def test_a_failed_pull_puts_no_out_tar(self):
        self.members = None
        self.status = b'{"result": "failed", "step": "pull"}\n'
        self.log = b"NODE 12:00:01 pull rc=1\nNODE 12:00:02 result failed step=pull\n"
        _, driver = self.assert_failed()
        self.assertEqual(driver.failure, {"log": f"jobs/{driver.nonce}/log.txt", "step": "pull",
                                          "tail": ["NODE 12:00:01 pull rc=1", "NODE 12:00:02 result failed step=pull"],
                                          "check": None})
        self.assertEqual(self.gets(), ["status.json", "log.txt"])
        self.assertEqual(os.listdir(self.out), [])


class Check(Job):
    """A check of the node's answer that fails on this machine (#606): ``CheckFailed`` with the error's message,
    kept by the driver in ``failure["check"]``."""

    def setUp(self):
        super().setUp()
        self.control["metric"] = {"stdout": "9.95\n"}
        self.write_control()
        self.settings = {"RENDER_RESOLUTION": f"{WIDTH}x{HEIGHT}", "UNREAL_IMAGE": IMAGE, "PERCEPTUAL_METRIC": "cvvdp"}

    def finish(self, run=subprocess.run, out="out"):
        """``renderjob.finish`` of the answer ``self.members`` into ``self.tmp / out``."""
        unpacked = self.tmp / f"{out}-answer"
        unpacked.mkdir()
        for name, data in self.members:
            (unpacked / name).write_bytes(data)
        job = dict(self.job(), out=str(self.tmp / out))
        (self.tmp / out).mkdir(exist_ok=True)
        described = renderjob.describe(job, self.settings)
        return renderjob.finish(job, described, {"result": "ok"}, str(unpacked), self.settings, run=run)

    def test_a_metric_that_exits_1_names_itself(self):
        self.finish(out="clean")
        self.assertEqual(sha((self.tmp / "clean" / "state.json").read_bytes()), CLEAN_STATE)
        self.assertTrue((self.tmp / "clean" / "manifest.json").is_file())

        def run(argv, *args, **kwargs):
            if argv[0] == "cvvdp":
                return subprocess.CompletedProcess(argv, 1, b"", b"")
            return subprocess.run(argv, *args, **kwargs)

        with self.assertRaises(renderjob.JobError) as raised:
            self.finish(run=run, out="seeded")
        self.assertIsInstance(raised.exception, renderjob.CheckFailed)
        self.assertEqual(str(raised.exception), "the node's answer failed a check: the metric exited 1 on frame 0")
        self.assertFalse((self.tmp / "seeded" / "state.json").exists())
        self.assertFalse((self.tmp / "seeded" / "manifest.json").exists())

    def test_other_errors_name_their_class_and_a_long_message_is_cut(self):
        cases = ((KeyError("frame"), "KeyError: 'frame'"), (TypeError("bad"), "TypeError: bad"),
                 (ValueError("odd"), "ValueError: odd"), (renderjob.frames.FramesError("f" * 241), "f" * 239 + "…"),
                 (renderjob.frames.FramesError("f" * 240), "f" * 240))
        for n, (error, message) in enumerate(cases):
            with self.subTest(error=type(error).__name__, message=message[:12]), \
                    mock.patch.object(renderjob.frames, "measure", side_effect=error):
                with self.assertRaises(renderjob.CheckFailed) as raised:
                    self.finish(out=f"out-{n}")
                self.assertEqual(str(raised.exception), renderjob.CHECK_FAILED.format(message))

    def seed_actor(self, label):
        """The answer's `scene.json` with an actor labelled ``label`` that no level names."""
        scene = json.loads(dict(self.members)["scene.json"].decode("utf-8"))
        scene["actors"].append(dict(copy.deepcopy(scene["actors"][0]), label=label))
        self.members = [(n, d) if n != "scene.json" else (n, (json.dumps(scene, indent=2) + "\n").encode())
                        for n, d in self.members]

    def test_the_driver_keeps_the_check_text(self):
        self.seed_actor("stray")
        _, driver = self.assert_failed("the node's answer failed a check: actor stray has no kind: no level names it")
        self.assertEqual(driver.failure, {"log": f"jobs/{driver.nonce}/log.txt", "step": None,
                                          "tail": ["NODE 12:01:00 result ok"],
                                          "check": "the node's answer failed a check: actor stray has no kind: "
                                                   "no level names it"})

    def test_a_secret_or_a_signed_link_in_a_check_message_is_withheld(self):
        original = self.members
        for label in (f"stray-{TOKEN}", f"stray-{SECRET}", f"stray-{KEY[1]}", "stray?X-Amz-Signature=" + "e" * 64):
            with self.subTest(label=label[:12]):
                self.members = original
                self.seed_actor(label)
                self.store.requests.clear()
                _, driver = self.assert_failed(renderjob.CHECK_FAILED.format(
                    f"actor {label} has no kind: no level names it"))
                self.assertEqual(driver.failure["check"], renderjob.WITHHELD)
                for secret in (KEY_ID, SECRET, KEY[1], TOKEN, "X-Amz-"):
                    self.assertNotIn(secret, json.dumps(driver.failure))


class Simulation(Job):
    """The job at `simulation` (#500): the node builds twice and `finish` writes `rerun.json` beside `state.json`."""

    def setUp(self):
        super().setUp()
        self.inputs = [item(f"{SIM}/requests/simulation.json", "{}\n"),
                       item("production/ep01/shots.json", json.dumps([{"shot": "Q1-1", "first": 0, "last": 47}])),
                       item(f"{SIM}/scene.py", DROP.read_text(encoding="utf-8"))]
        self.members = sorted({**{p.name: p.read_bytes() for p in (FIX / "out-simulation").iterdir()},
                               **frames(0, 47, [])}.items())

    def replace(self, name, data):
        self.members = [(n, data if n == name else d) for n, d in self.members]

    def node_steps(self, steps):
        node = json.loads((FIX / "out-simulation" / "node.json").read_text())
        node["steps"] = steps(node["steps"])
        self.replace("node.json", (json.dumps(node) + "\n").encode())

    def clean_run(self):
        replay, driver, error, billed, _ = self.session_run()
        self.assertIsNone(error)
        self.assertEqual(billed, BILLED)
        self.assertEqual(replay.next, 6)
        self.assertEqual(os.listdir(self.temp), [])

    def tree(self):
        """The stage directory of a production tree with the script and the job's files; the lint's findings."""
        unit = self.tmp / "tree" / SIM
        unit.mkdir(parents=True)
        shutil.copyfile(DROP, unit / "scene.py")
        for name in ("manifest.json", "state.json", "rerun.json"):
            shutil.copyfile(self.out / name, unit / name)
        result = lint.run(self.tmp / "tree", only=["simulation"])["simulation"]
        return [(f.path, f.line, f.rule, f.message) for f in result.findings]

    def test_the_clean_job_writes_rerun_json(self):
        self.clean_run()
        names = sorted([n for n, _ in self.members] + ["manifest.json", "render.json", "rerun.json", "state.json"])
        self.assertEqual(sorted(os.listdir(self.out)), names)
        for name, data in self.members:
            self.assertEqual((self.out / name).read_bytes(), data)
        self.assertEqual((self.out / "rerun.json").read_bytes(), (self.out / "state.json").read_bytes())
        manifest = json.loads((self.out / "manifest.json").read_text())
        self.assertIn({"name": "rerun.json", "sha256": sha((self.out / "rerun.json").read_bytes()),
                       "bytes": (self.out / "rerun.json").stat().st_size}, manifest["outputs"])
        self.assertIn("rerun-scene.json", [o["name"] for o in manifest["outputs"]])
        unit = self.tmp / "golden" / "prop-drop"
        unit.mkdir(parents=True)
        shutil.copyfile(DROP, unit / "scene.py")
        for name in golden.RECORDED + ("rerun.json",):
            shutil.copyfile(self.out / name, unit / name)
        self.assertEqual(golden.unit_problems(unit.parent, "prop-drop"), [])
        self.assertEqual(self.tree(), [])

    def test_a_second_build_that_differs_fails_the_lint(self):
        scene = json.loads((FIX / "out-simulation" / "rerun-scene.json").read_text())
        scene["actors"][1]["location"][2] += 1.0
        self.replace("rerun-scene.json", (json.dumps(scene, indent=2) + "\n").encode())
        self.clean_run()
        self.assertNotEqual((self.out / "rerun.json").read_bytes(), (self.out / "state.json").read_bytes())
        self.assertEqual(self.tree(), [(f"{SIM}/rerun.json", 1, "rerun", f"actors differs from {SIM}/state.json")])

    def test_rerun_scene_left_out_of_out_tar(self):
        self.members = [(n, d) for n, d in self.members if n != "rerun-scene.json"]
        self.assert_failed()
        self.assertEqual(os.listdir(self.out), [])

    def test_no_rebuild_step_in_node_json(self):
        self.node_steps(lambda steps: [s for s in steps if s["step"] != "rebuild"])
        self.assert_failed()
        self.assertEqual(os.listdir(self.out), [])

    def test_a_rebuild_step_without_the_ok_line(self):
        self.node_steps(lambda steps: [dict(s, markers=[]) if s["step"] == "rebuild" else s for s in steps])
        self.assert_failed()
        self.assertEqual(os.listdir(self.out), [])

    def test_a_node_that_fails_at_rebuild_leaves_its_step_and_tail(self):
        self.members = None
        self.status = b'{"result": "failed", "step": "rebuild"}\n'
        lines = [f"NODE 11:59:{n:02d} get x http=200" for n in range(40)]
        lines += ["NODE 12:01:00 step rebuild rc=139 seconds=12", f"pull token {TOKEN} in the engine log",
                  "GET https://store.invalid/b/x?X-Amz-Signature=" + "e" * 64, "x" * 300,
                  "NODE 12:01:00 result failed step=rebuild"]
        self.log = ("\n".join(lines) + "\n").encode()
        _, driver = self.assert_failed()
        tail = lines[5:41] + [renderjob.WITHHELD, renderjob.WITHHELD, "x" * 239 + "…", lines[44]]
        self.assertEqual(driver.failure, {"log": f"jobs/{driver.nonce}/log.txt", "step": "rebuild", "tail": tail,
                                          "check": None})
        self.assertEqual(len(tail), 40)
        self.assertEqual([r["path"].rsplit("/", 1)[1] for r in self.store.requests if r["method"] == "GET"],
                         ["status.json", "log.txt"])
        self.assertEqual(os.listdir(self.out), [])
        for secret in (KEY_ID, SECRET, KEY[1], TOKEN):
            self.assertNotIn(secret, json.dumps(driver.failure))

    def test_the_clean_job_leaves_no_failure(self):
        replay, driver, error, _, _ = self.session_run()
        self.assertIsNone(error)
        self.assertIsNone(driver.failure)

    def test_the_rerun_state_takes_the_asset_too(self):
        self.members = with_wren(self.members, "prop", ("scene.json", "rerun-scene.json"))
        self.inputs.append(asset_item(WREN))
        self.clean_run()
        self.assertEqual((self.out / "rerun.json").read_bytes(), (self.out / "state.json").read_bytes())
        actors = json.loads((self.out / "rerun.json").read_text())["actors"]
        wren = next(actor for actor in actors if actor["id"] == "wren")
        self.assertEqual((wren["kind"], wren["asset"], wren["sha256"]), ("character", "asset:wren", BIN))


class Tail(unittest.TestCase):
    def test_the_last_lines(self):
        lines = [f"line {n}" for n in range(41)]
        self.assertEqual(renderjob.tail("\n".join(lines) + "\n", []), lines[1:])
        self.assertEqual(renderjob.tail("", ["hidden"]), [])

    def test_a_long_line_is_cut(self):
        self.assertEqual(renderjob.tail("y" * 240, []), ["y" * 240])
        [cut] = renderjob.tail("y" * 241, [])
        self.assertEqual((len(cut), cut), (240, "y" * 239 + "…"))

    def test_a_line_that_may_hold_a_secret_is_withheld(self):
        text = "a seeded-secret-1 here\nhttps://x.invalid/?X-Amz-Credential=k\n" + "z" * 300 + "seeded-secret-1\nkept\n"
        self.assertEqual(renderjob.tail(text, ["seeded-secret-1"]),
                         [renderjob.WITHHELD, renderjob.WITHHELD, renderjob.WITHHELD, "kept"])

    def test_a_short_clean_line_is_kept(self):
        self.assertEqual(renderjob.tail("NODE 12:01:00 result ok\n", ["seeded-secret-1"]), ["NODE 12:01:00 result ok"])

    def test_failed_step(self):
        self.assertEqual(renderjob.failed_step({"result": "failed", "step": "rebuild"}), "rebuild")
        for status in ({"result": "ok"}, None, [], {"result": "failed", "step": "warp"}, {"result": "failed"},
                       {"result": "ok", "step": "rebuild"}):
            with self.subTest(status=status):
                self.assertIsNone(renderjob.failed_step(status))


class Describe(unittest.TestCase):
    def job(self, *inputs):
        return {"hash": JOB, "inputs": list(inputs), "out": "out"}

    def production(self, *extra, text="import unreal\n"):
        return self.job(item(f"{STAGE}/scene.py", text),
                        item("production/ep01/shots.json", json.dumps([{"shot": "Q1-1", "first": 0, "last": 23}])),
                        *extra)

    def fps(self, job):
        return renderjob.describe(job, {"RENDER_RESOLUTION": "640x360"})["fps"]

    def test_fps_of_a_production_shot_from_the_edit_list(self):
        job = self.production(item(EDIT, json.dumps({"fps": 25, "bible": "A-1", "events": []})))
        self.assertEqual(self.fps(job), 25)

    def test_a_golden_level_keeps_its_fps(self):
        level = SCENE.read_text(encoding="utf-8")
        for extra in ((), (item(EDIT, json.dumps({"fps": 24})),)):
            with self.subTest(edit_list=bool(extra)):
                self.assertEqual(self.fps(self.production(*extra, text=level)), 24)

    def test_fps_refusals(self):
        level = SCENE.read_text(encoding="utf-8")
        no_fps = f"no input {EDIT} and the unit's LEVEL has no fps"
        bad_fps = f"{EDIT} has no fps that is a whole number above zero"
        cases = [("no edit list, no LEVEL", self.production(), no_fps),
                 ("another episode's edit list",
                  self.production(item("episodes/ep02/reel/edit-list.json", json.dumps({"fps": 25}))), no_fps),
                 ("LEVEL 24, edit list 25", self.production(item(EDIT, json.dumps({"fps": 25})), text=level),
                  f"the unit's LEVEL has fps 24, {EDIT} has fps 25")]
        cases += [(text, self.production(item(EDIT, text)), bad_fps)
                  for text in ('{"fps": 23.976}', '{"fps": 24.0}', '{"fps": true}', '{"fps": "24"}', "{}", "[]", "{")]
        for name, job, message in cases:
            with self.subTest(name):
                with self.assertRaises(renderjob.JobError) as raised:
                    renderjob.describe(job, {"RENDER_RESOLUTION": "640x360"})
                self.assertEqual(str(raised.exception), message)

    def test_the_edit_list_path_is_the_filing_one(self):
        self.assertEqual(renderjob.EDIT_LIST, filing.EDIT_LIST)

    def test_scripts_in_input_order_and_the_stage_of_the_last(self):
        level = SCENE.read_text(encoding="utf-8")
        job = self.job(item("production/ep01/Q1-1/layout/scene.py", "x = 1\n"),
                       item("production/ep01/shots.json", json.dumps([{"shot": "Q1-1", "first": 24, "last": 47}])),
                       item("production/ep01/Q1-1/lighting/scene.py", level.replace('"render"', '"lighting"')))
        found = renderjob.describe(job, {"RENDER_RESOLUTION": "1920x1080"})
        self.assertEqual(found, {"hash": JOB, "shot": "Q1-1", "stage": "lighting", "fps": 24, "first": 24, "last": 47,
                                 "width": 1920, "height": 1080, "scripts": ["scripts/00.py", "scripts/01.py"],
                                 "assets": {}, "sampled": []})

    def test_assets_of_the_inputs(self):
        bo = {"asset": "bo", "kind": "prop", "sha256": "b" * 64}
        bo_item = {"path": "asset:bo", "sha256": "b" * 64, "text": json.dumps(bo)}
        job = self.production(asset_item(WREN), bo_item, text=SCENE.read_text(encoding="utf-8"))
        self.assertEqual(renderjob.asset_states(job), {"wren": WREN, "bo": bo})
        self.assertEqual(renderjob.describe(job, {"RENDER_RESOLUTION": "640x360"})["assets"],
                         {"bo": "b" * 64, "wren": BIN})
        cases = (("no text", dict(bo_item, text=None), "bo"),
                 ("an upper-case sha256", {"path": "asset:bo", "sha256": "B" * 64,
                                           "text": json.dumps(dict(bo, sha256="B" * 64))}, "bo"),
                 ("an id that is not an ASSET_ID", {"path": "asset:Bo", "sha256": "b" * 64,
                                                     "text": json.dumps(dict(bo, asset="Bo"))}, "Bo"))
        for name, seeded, shown in cases:
            with self.subTest(name):
                job = self.production(asset_item(WREN), seeded, text=SCENE.read_text(encoding="utf-8"))
                with self.assertRaises(renderjob.JobError) as raised:
                    renderjob.describe(job, {"RENDER_RESOLUTION": "640x360"})
                self.assertEqual(str(raised.exception), f"input asset:{shown} does not match its locked state")

    def test_refusals(self):
        scene = item(f"{STAGE}/scene.py", SCENE.read_text(encoding="utf-8"))
        shots = item("production/ep01/shots.json", json.dumps([{"shot": "Q1-1", "first": 0, "last": 23}]))
        cases = ((self.job(scene), {"RENDER_RESOLUTION": "640x360"}, "no input ending shots.json"),
                 (self.job(shots), {"RENDER_RESOLUTION": "640x360"}, "no input named scene.py"),
                 (self.job(scene, item("production/ep01/shots.json", "[]")), {"RENDER_RESOLUTION": "640x360"},
                  "shots.json has no row {shot, first, last} for shot Q1-1"),
                 (self.job(scene, shots), {"RENDER_RESOLUTION": "640"}, "RENDER_RESOLUTION is not <width>x<height>"),
                 (self.job(item(f"{STAGE}/scene.py", "x = 1\n"), shots), {"RENDER_RESOLUTION": "640x360"},
                  "no input episodes/ep01/reel/edit-list.json and the unit's LEVEL has no fps"))
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
