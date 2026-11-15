"""Tests for kit/lib/ec2.py on the first real render job, replayed from the recorded session.

Recorded 2026-11-15 on g4dn.xlarge at 640x360 (#452). Boot: the start script's first line ran 47 s after the
launch time; the node was ready 524 s later, 521 s of it the image pull; in.tar is 30720 bytes. A node that ended
itself reads shutting-down with the reason `User initiated` and no time, state reason
Client.InstanceInitiatedShutdown; stop() then sends TerminateInstances and reads the end on its first poll. The
root disk was not read by the driver: not in this record. The launch with ClientToken and the tag on instance and
volume answered 200 twice. The job built once (exit 0, marker `FACTORY build ok`), so two builds were not
compared.

The EC2 side is `Ec2Replay` of tests/test_ec2.py on the exchanges of `tests/fixtures/render/session-lit-render/`
(and `tests/fixtures/render/ec2/self-ended/`). The store is `SessionStore`, an `AssetStore` of
tests/test_assets.py that answers `status.json`, `out.tar` and `log.txt` with 404 until the replay's next
exchange is `TerminateInstances`, then with the recorded files. `renderjob.finish` is replaced by a stand-in
that keeps what it was given: the recorded `out.tar` holds three frames, and `finish` would measure 24 with
`ffmpeg` and `tesseract`. The clock and the sleep of a node are replaced on the instance. Nothing reaches a host
other than 127.0.0.1.
"""

import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from kit.lib import ec2, golden, node, renderjob, scenestate  # noqa: E402
from test_assets import KEY_ID, REGION, SECRET, AssetStore  # noqa: E402
from test_ec2 import KEY, NOTHING, Ec2Replay, answered  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "render"
SESSION = FIXTURES / "session-lit-render"
SELF_ENDED = FIXTURES / "ec2" / "self-ended"
SCENE = ROOT / "kit" / "evals" / "golden" / "lit-render" / "scene.py"
STAGE = "production/ep01/Q1-1/render"
SHOTS = json.dumps([{"shot": "Q1-1", "first": 0, "last": 23}])
MANIFEST = json.loads((SESSION / "out" / "manifest.json").read_text(encoding="utf-8"))
SETTINGS = {"RENDER_PROVIDER": "ec2", "RENDER_INSTANCE_TYPE": "g4dn.xlarge",
            "RENDER_MACHINE_IMAGE": "ami-1234567890abcdef0", "RENDER_TAG": "project=enrico-factory",
            "RENDER_USD_HOUR": "0.526", "RENDER_MAX_MIN": "25", "RENDER_RESOLUTION": "640x360",
            "UNREAL_IMAGE": MANIFEST["engine"]}
JOB_CLOCK = datetime(2026, 11, 15, 10, 5, 25, 884000, tzinfo=timezone.utc)
SELF_ENDED_CLOCK = datetime(2026, 11, 15, 10, 8, 48, 106000, tzinfo=timezone.utc)
RESULTS = ("status.json", "out.tar", "log.txt")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def exchanges(directory):
    """The exchanges `[0-9]*-*.json` of ``directory``, in the order of the number before the first `-`."""
    paths = sorted(directory.glob("[0-9]*-*.json"), key=lambda path: int(path.name.split("-", 1)[0]))
    return [json.loads(path.read_text(encoding="utf-8")) for path in paths]


def billed(directory):
    return json.loads((directory / "billed.json").read_text(encoding="utf-8"))


def members(tar_bytes):
    """The names and bytes of the members of ``tar_bytes``, in archive order."""
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tar:
        found = []
        for member in tar.getmembers():
            with tar.extractfile(member) as handle:
                found.append((member.name, handle.read()))
        return found


def renamed(tar_bytes, old, new):
    """``tar_bytes`` with the member ``old`` named ``new``."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name, data in members(tar_bytes):
            info = tarfile.TarInfo(new if name == old else name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class SessionStore(AssetStore):
    """``AssetStore`` whose `status.json`, `out.tar` and `log.txt` are 404 until ``replay``'s next exchange is
    `TerminateInstances`, then the bytes of ``served``. ``seen`` keeps `(method, name, replay.next)`."""

    def __init__(self, test, replay):
        super().__init__(test, (KEY_ID, SECRET, REGION))
        self.replay, self.seen = replay, []
        self.served = {name: (SESSION / name).read_bytes() for name in RESULTS}

    def handle(self, h, method):
        name = h.path.rsplit("/", 1)[-1]
        self.seen.append((method, name, self.replay.next))
        if name in RESULTS:
            following = self.replay.exchanges[self.replay.next] if self.replay.next < len(self.replay.exchanges) \
                else None
            if following is not None and following["request"]["params"].get("Action") == "TerminateInstances" \
                    and name in self.served:
                self.objects[name] = self.served[name]
            else:
                self.objects.pop(name, None)
        return super().handle(h, method)


class Case(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory)
        self.temp = self.directory / "tmp"
        self.temp.mkdir()
        self.out = self.directory / "out"
        self.out.mkdir()
        self.calls = []

    def node(self, replay, store, settings=None, clock=JOB_CLOCK):
        """A node of ``replay`` and ``store`` whose clock reads ``clock`` and whose sleeps are in ``sleeps``."""
        secrets = self.directory / "secrets"
        secrets.mkdir(exist_ok=True)
        key_file = secrets / "render.node"
        key_file.write_text("\n".join(KEY) + "\n", encoding="utf-8")
        (secrets / "engine.unreal").write_text("seeded-pull-token\n", encoding="utf-8")
        (secrets / "store.assets").write_text("\n".join((KEY_ID, SECRET, REGION)) + "\n", encoding="utf-8")
        config = {"settings": {**SETTINGS, "RENDER_API_URL": replay.url, "ASSET_STORE": store.url("/b"),
                               **(settings or {})},
                  "secrets": {"render.node": str(key_file), "engine.unreal": str(secrets / "engine.unreal"),
                              "store.assets": str(secrets / "store.assets")}}
        driver = ec2.Node(str(key_file), config)
        driver.now = lambda: clock
        driver.sleeps = []
        driver.sleep = driver.sleeps.append
        return driver

    def session(self, recorded=None, settings=None):
        replay = Ec2Replay(self, exchanges(SESSION) if recorded is None else recorded, KEY)
        store = SessionStore(self, replay)
        return replay, store, self.node(replay, store, settings)

    def job(self):
        scene = SCENE.read_text(encoding="utf-8")
        inputs = [{"path": f"{STAGE}/scene.py", "text": scene}, {"path": "production/ep01/shots.json", "text": SHOTS}]
        for item in inputs:
            item["sha256"] = sha(item["text"].encode("utf-8"))
        return {"hash": MANIFEST["job"], "out": str(self.out), "inputs": inputs}

    def stand_in(self, job, described, status, unpacked, settings):
        files = {path.name: path.read_bytes() for path in sorted(Path(unpacked).iterdir())}
        self.calls.append({"job": job, "described": described, "status": status, "files": files,
                           "settings": settings})

    def run_job(self, driver, job):
        with mock.patch.object(renderjob, "finish", self.stand_in), \
                mock.patch.object(tempfile, "tempdir", str(self.temp)):
            driver.run(job)

    def recorded_run(self):
        """The whole recorded job: `start()`, `run(job)` under the stand-in, `stop()`."""
        replay, store, driver = self.session()
        job = self.job()
        driver.start()
        self.run_job(driver, job)
        return replay, store, driver, job, driver.stop()


class B1RecordedJob(Case):
    def test_the_recorded_job_replays_from_start_to_stop(self):
        replay, store, driver, job, bill = self.recorded_run()
        self.assertEqual(replay.next, 56)
        self.assertEqual(len(replay.requests), 56)
        self.assertEqual(replay.mismatches, [])
        self.assertEqual(bill, billed(SESSION))
        self.assertEqual(bill, {"usd": 0.1191, "compute_min": 13.5814})
        self.assertEqual(driver.sleeps, [15] * 51)
        self.assertEqual(store.seen, [("PUT", "in.tar", 4), ("PUT", "links.txt", 4)]
                         + [("HEAD", "status.json", 4 + k) for k in range(51)]
                         + [("GET", "status.json", 54), ("GET", "out.tar", 54), ("GET", "log.txt", 54)])
        prefix = f"/b/jobs/{driver.nonce}/"
        self.assertEqual([request["path"] for request in store.requests if not request["path"].startswith(prefix)],
                         [])

    def test_in_tar_member_by_member(self):
        replay, store, driver, job, bill = self.recorded_run()
        sent = members(store.objects["in.tar"])
        recorded = dict(members((SESSION / "in.tar").read_bytes()))
        self.assertEqual([name for name, _ in sent], ["job.json", "scripts/00.py", "job.sh", "build.py"])
        sent = dict(sent)
        self.assertEqual(sent["job.json"], recorded["job.json"])
        self.assertEqual(sent["scripts/00.py"], recorded["scripts/00.py"])
        self.assertEqual(sent["job.sh"], (ROOT / "kit" / "engine" / "job.sh").read_bytes())
        self.assertEqual(sent["build.py"], (ROOT / "kit" / "engine" / "build.py").read_bytes())

    def test_another_instance_type_is_refused_at_the_launch(self):
        replay, store, driver = self.session(settings={"RENDER_INSTANCE_TYPE": "g9.test"})
        with self.assertRaises(ec2.Refused) as raised:
            driver.start()
        self.assertEqual(str(raised.exception), "ReplayMismatch")
        self.assertEqual(replay.mismatches, ["request 2 (RunInstances): InstanceType"])
        replay.mismatches.clear()
        self.assertEqual(driver.stop(), NOTHING)


class B2NoCapacity(Case):
    def test_no_capacity_at_the_launch_is_unavailable(self):
        recorded = exchanges(SESSION)
        recorded[1] = answered(recorded[1], "InsufficientInstanceCapacity", 500)
        replay, store, driver = self.session(recorded)
        with self.assertRaises(node.Unavailable) as raised:
            driver.start()
        self.assertEqual(str(raised.exception), "ec2 InsufficientInstanceCapacity")
        self.assertEqual(replay.next, 2)
        self.assertEqual(store.seen, [])
        self.assertEqual(driver.stop(), NOTHING)

    def test_the_recorded_launch_starts(self):
        replay, store, driver = self.session()
        driver.start()
        self.assertEqual(replay.next, 4)
        self.assertEqual(driver.instance_id, "i-1234567890abcdef0")
        self.assertEqual(store.seen, [])


class B3StandIn(Case):
    def test_finish_gets_the_recorded_answer(self):
        replay, store, driver, job, bill = self.recorded_run()
        self.assertEqual(len(self.calls), 1)
        call = self.calls[0]
        self.assertEqual(call["job"], self.job())
        self.assertEqual(call["described"], json.loads(dict(members((SESSION / "in.tar").read_bytes()))["job.json"]))
        self.assertEqual(call["status"], {"result": "ok"})
        self.assertEqual(call["settings"], driver.config["settings"])
        out_tar = members((SESSION / "out.tar").read_bytes())
        self.assertEqual(len(out_tar), 13)
        self.assertEqual(call["files"], dict(out_tar))
        self.assertEqual(driver.log, (SESSION / "log.txt").read_text(encoding="utf-8"))
        self.assertEqual(os.listdir(self.out), [])
        self.assertEqual(os.listdir(self.temp), [])

    def assert_failed_job(self, replay, store, driver):
        driver.start()
        with self.assertRaises(renderjob.JobError) as raised:
            self.run_job(driver, self.job())
        self.assertEqual(str(raised.exception), "job failed on the node")
        self.assertEqual(self.calls, [])
        self.assertEqual(os.listdir(self.temp), [])
        self.assertEqual(driver.stop(), billed(SESSION))
        self.assertEqual(replay.next, 56)

    def test_a_member_outside_the_directory_fails_the_job(self):
        replay, store, driver = self.session()
        store.served["out.tar"] = renamed(store.served["out.tar"], "node.json", "../node.json")
        self.assert_failed_job(replay, store, driver)

    def test_no_out_tar_fails_the_job(self):
        replay, store, driver = self.session()
        del store.served["out.tar"]
        self.assert_failed_job(replay, store, driver)


class B4RecordedOut(Case):
    def unit(self, render=None):
        """A golden directory holding the unit `lit-render`: `scene.py` and the three files of `out/`."""
        unit = self.directory / "golden" / "lit-render"
        unit.mkdir(parents=True)
        shutil.copyfile(SCENE, unit / "scene.py")
        for name in ("manifest.json", "render.json", "state.json"):
            shutil.copyfile(SESSION / "out" / name, unit / name)
        if render is not None:
            (unit / "render.json").write_bytes(render)
        return unit.parent

    def test_the_recorded_out_agrees_with_the_recorded_run(self):
        replay, store, driver, job, bill = self.recorded_run()
        call = self.calls[0]
        files = call["files"]
        scene = json.loads(files["scene.json"].decode("utf-8"))
        node_json = json.loads(files["node.json"].decode("utf-8"))
        level = renderjob.level(SCENE.read_text(encoding="utf-8"))
        self.assertEqual(scenestate.dumps(scenestate.state(scene, call["described"], level)).encode("utf-8"),
                         (SESSION / "out" / "state.json").read_bytes())
        self.assertTrue(renderjob.built(node_json))
        self.assertEqual(renderjob.flags(node_json), MANIFEST["flags"])
        self.assertEqual(renderjob.seeds(job, STAGE), MANIFEST["seeds"])
        self.assertEqual(MANIFEST["inputs"], [{"path": "scene.py", "sha256": job["inputs"][0]["sha256"]},
                                              {"path": "production/ep01/shots.json",
                                               "sha256": job["inputs"][1]["sha256"]}])
        outputs = {item["name"]: (item["sha256"], item["bytes"]) for item in MANIFEST["outputs"]}
        written = dict(files)
        for name in ("render.json", "state.json"):
            written[name] = (SESSION / "out" / name).read_bytes()
        self.assertEqual(len(written), 15)
        for name, data in sorted(written.items()):
            self.assertEqual(outputs.get(name), (sha(data), len(data)), name)
        render = json.loads((SESSION / "out" / "render.json").read_text(encoding="utf-8"))
        for frame in (0, 11, 23):
            self.assertEqual(render["frames_sha256"][frame], sha(files[f"frame-{frame:06d}.png"]))

    def test_the_recorded_out_is_a_golden_record(self):
        self.assertEqual(golden.unit_problems(self.unit(), "lit-render"), [])

    def test_a_changed_render_json_is_not_the_recorded_one(self):
        render = (SESSION / "out" / "render.json").read_bytes() + b" "
        self.assertEqual(golden.unit_problems(self.unit(render), "lit-render"),
                         ["lit-render/manifest.json does not record render.json at its sha256 and size"])


class B5SelfEnded(Case):
    def self_ended(self, clock=SELF_ENDED_CLOCK):
        replay = Ec2Replay(self, exchanges(SELF_ENDED), KEY)
        store = SessionStore(self, replay)
        driver = self.node(replay, store, {"RENDER_MAX_MIN": "2"}, clock)
        driver.start()
        return replay, driver

    def polled(self, driver):
        """The answers of `_running()` until it returns False."""
        found = []
        while not found or found[-1]:
            found.append(driver._running())
        return found

    def test_a_node_that_ended_itself_is_billed_to_the_poll(self):
        replay, driver = self.self_ended()
        self.assertEqual(self.polled(driver), [True] * 12 + [False])
        self.assertEqual(driver.stop(), billed(SELF_ENDED))
        self.assertEqual(billed(SELF_ENDED), {"usd": 0.0293, "compute_min": 3.3351})
        self.assertEqual(driver.sleeps, [15])
        self.assertEqual(replay.next, 19)

    def test_stop_before_the_polls_is_not_the_recorded_session(self):
        replay, driver = self.self_ended()
        with self.assertRaises(ec2.Refused) as raised:
            driver.stop()
        self.assertEqual(str(raised.exception), "ReplayMismatch")
        self.assertEqual(replay.mismatches, ["request 5 (TerminateInstances): Action"])
        replay.mismatches.clear()

    def test_a_second_later_bills_more(self):
        replay, driver = self.self_ended(SELF_ENDED_CLOCK + timedelta(seconds=1))
        self.polled(driver)
        self.assertEqual(driver.stop(), {"usd": 0.0294, "compute_min": 3.3518})


if __name__ == "__main__":
    unittest.main()
