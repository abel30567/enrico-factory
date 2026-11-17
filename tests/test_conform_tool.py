"""Tests for the tool `conform`, kit/lib/tools/conform.py and kit/lib/conformjob.py.

The tool is loaded by `cmd_run._load_tool` and called directly with a call built here. The asset store is
`AssetStore` of tests/test_assets.py on 127.0.0.1, holding 408 frames and five sounds. `conformjob.RUN` is a
stand-in for ffmpeg: it answers a version, writes a few bytes to the file a stem or master call names, and
answers an `ebur128` summary to the measure. The reel edit list is the one of
tests/fixtures/lint_conform/clean, and the `mix.json` the tool makes is checked by the conform lint in a copy of
that fixture. Test 8 runs the real ffmpeg when it is on `PATH` with libx264. Nothing reaches a host other than
127.0.0.1.
"""

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from kit.lib import cmd_run, conformjob  # noqa: E402
from kit.lib.lints import conform as conform_lint  # noqa: E402
from kit.lib.tools import Down, Failed  # noqa: E402
from test_assets import KEY_ID, REGION, SECRET, AssetStore  # noqa: E402

CLEAN = ROOT / "tests" / "fixtures" / "lint_conform" / "clean"
REEL = "episodes/ep01/reel/edit-list.json"
REQUEST = "production/ep01/conform/requests/mix.json"
PLAN = "production/ep01/conform/mix-plan.json"
SHOTS = "production/ep01/shots.json"
MANIFEST = "media/manifest.tsv"
JOB = "c0" * 32
SOUNDS = {name: f"invented sound {name}".encode() for name in
          ("voice-l1", "voice-l2", "voice-l3", "music-sea", "sfx-oar")}
REASONS = (
    "the first input is not a mix request",
    "an input the mix needs is missing",
    "the edit list has no fps that is a whole number above zero",
    "shots.json is not one run of shots from frame 0",
    "mix-plan.json is not {lines, music, effects} with frames and gains",
    "a line of mix-plan.json lies outside the shots",
    "a music cue or effect of mix-plan.json ends after the last frame",
    "a media id of mix-plan.json is not in the manifest",
    "a frame of the picture is not in its shot's render manifest",
    "a frame or a sound is not in the asset store",
    "ffmpeg failed",
    "ebur128 gave no integrated loudness or true peak",
)
SUMMARY = ("[Parsed_ebur128_0 @ 0x1] t: 17 TARGET:-23 LUFS M: -14.0 S: -14.1 I: -14.2 LUFS\n"
           "[Parsed_ebur128_0 @ 0x1] Summary:\n\n  Integrated loudness:\n    I:         -14.2 LUFS\n"
           "    Threshold: -24.4 LUFS\n\n  Loudness range:\n    LRA:         0.0 LU\n\n"
           "  True peak:\n    Peak:       {peak} dBFS\n")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def frame(n):
    return f"frame {n}".encode()


def dumps(data):
    return json.dumps(data, indent=2) + "\n"


class FakeFfmpeg:
    """`conformjob.RUN`: records each argv and its directory; ``fail`` is ``(word of the argv, exit)``."""

    def __init__(self, peak="-1.6"):
        self.calls, self.peak, self.fail, self.raises = [], peak, None, None

    def __call__(self, argv, cwd, stdin, capture_output, timeout):
        assert stdin == subprocess.DEVNULL and capture_output and timeout == conformjob.STEP_S
        self.calls.append((list(argv), cwd))
        if self.raises is not None:
            raise self.raises
        if self.fail and self.fail[0] in argv:
            return subprocess.CompletedProcess(argv, self.fail[1], b"", b"failed")
        if argv == ["ffmpeg", "-version"]:
            return subprocess.CompletedProcess(argv, 0, b"ffmpeg version 6.1.1-fake\nbuilt with an invented compiler\n", b"")
        if "ebur128=peak=true" in argv:
            return subprocess.CompletedProcess(argv, 0, b"", SUMMARY.format(peak=self.peak).encode())
        Path(argv[-1]).write_bytes(b"fake " + os.path.basename(argv[-1]).encode())
        return subprocess.CompletedProcess(argv, 0, b"", b"")


class ConformToolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.temp = self.tmp / "tmp"
        self.temp.mkdir()
        key = self.tmp / "store.assets"
        key.write_text(f"{KEY_ID}\n{SECRET}\n{REGION}\n")
        key.chmod(0o600)
        self.secrets = {"store.assets": str(key)}
        self.store = AssetStore(self)
        self.settings = {"ASSET_STORE": self.store.url("/b")}
        for n in range(408):
            self.store.objects[sha(frame(n))] = frame(n)
        for data in SOUNDS.values():
            self.store.objects[sha(data)] = data
        self.ffmpeg = FakeFfmpeg()
        for patch in (mock.patch.object(conformjob, "RUN", self.ffmpeg),
                      mock.patch.object(tempfile, "tempdir", str(self.temp))):
            patch.start()
            self.addCleanup(patch.stop)
        self.request = REQUEST
        self.texts = {
            PLAN: {"lines": [{"line": "L1", "media": "voice-l1", "start": 12, "end": 60, "gain": 0},
                             {"line": "L2", "media": "voice-l2", "start": 72, "end": 110, "gain": -1.5},
                             {"line": "L3", "media": "voice-l3", "start": 150, "end": 210, "gain": 0}],
                   "music": [{"media": "music-sea", "start": 0, "end": 407, "gain": -12}],
                   "effects": [{"media": "sfx-oar", "start": 100, "end": 110, "gain": -6}]},
            MANIFEST: "id\tsha256\tbytes\tsource\n" + "".join(
                f"{name}\t{sha(data)}\t{len(data)}\tupload\n" for name, data in SOUNDS.items()),
            REEL: (CLEAN / REEL).read_text(),
            SHOTS: [{"shot": "Q1-1", "first": 0, "last": 119}, {"shot": "Q1-2", "first": 120, "last": 407}],
            "production/ep01/Q1-1/render/manifest.json": self.render(0, 119),
            "production/ep01/Q1-2/render/manifest.json": self.render(120, 407),
        }
        self.tool = cmd_run._load_tool("conform")
        self.runs = 0

    @staticmethod
    def render(first, last):
        return {"job": "a1" * 32, "outputs": [{"name": f"frame-{n:06d}.png", "sha256": sha(frame(n)), "bytes": 9}
                                              for n in range(first, last + 1)]}

    def call(self):
        inputs = []
        for path, value in [(self.request, "{}\n"), *self.texts.items()]:
            text = value if isinstance(value, str) or value is None else dumps(value)
            inputs.append({"path": path, "sha256": sha((text or "").encode()), "text": text})
        self.runs += 1
        out = self.tmp / f"out-{self.runs}"
        out.mkdir()
        self.out = out
        return {"hash": JOB, "ticket": 9, "caller": "sound-mixer", "expected": {"compute_min": 0, "usd": 0},
                "inputs": inputs, "out": str(out), "record": str(self.tmp / "record"),
                "settings": self.settings, "secrets": self.secrets}

    def requests(self, method):
        return [r for r in self.store.requests if r["method"] == method]

    def failed(self, reason):
        with self.assertRaises(Failed) as caught:
            self.tool.run(self.call())
        self.assertEqual((caught.exception.reason, caught.exception.usd, caught.exception.compute_min),
                         (reason, 0, 0))
        self.assertEqual(os.listdir(self.temp), [])
        return caught.exception

    def refused(self, reason):
        """The reason, before any program or store call, with nothing written."""
        self.failed(reason)
        self.assertEqual(self.ffmpeg.calls, [])
        self.assertEqual(self.store.requests, [])
        self.assertEqual(os.listdir(self.out), [])

    def test_1_clean(self):
        self.assertEqual(self.tool.run(self.call()), {"usd": 0, "compute_min": 0})
        names = ["dialogue.wav", "effects.wav", "master.mp4", "mix.json", "music.wav"]
        self.assertEqual(sorted(os.listdir(self.out)), sorted(names + ["manifest.json"]))
        manifest = json.loads((self.out / "manifest.json").read_text())
        self.assertEqual([o["name"] for o in manifest["outputs"]], names)
        for o in manifest["outputs"]:
            data = (self.out / o["name"]).read_bytes()
            self.assertEqual((o["sha256"], o["bytes"]), (sha(data), len(data)))
        self.assertEqual(manifest["engine"], "ffmpeg version 6.1.1-fake")
        self.assertEqual((manifest["job"], manifest["flags"], manifest["seeds"]), (JOB, [], {}))
        self.assertEqual([i["path"] for i in manifest["inputs"]], [REQUEST, *self.texts])
        mix = json.loads((self.out / "mix.json").read_text())
        self.assertEqual(mix["stems"], [{"name": s, "sha256": sha((self.out / f"{s}.wav").read_bytes())}
                                        for s in ("dialogue", "music", "effects")])
        self.assertEqual(mix["master"], sha((self.out / "master.mp4").read_bytes()))
        self.assertEqual((mix["loudness_lufs"], mix["true_peak_dbtp"]), (-14.2, -1.6))
        lint = self.tmp / "lint"
        shutil.copytree(CLEAN, lint)
        shutil.copy(self.out / "mix.json", lint / "production" / "ep01" / "conform" / "mix.json")
        self.assertEqual(conform_lint.check(lint), [])
        self.assertEqual((len(self.requests("GET")), len(self.requests("PUT"))), (413, 0))
        self.assertEqual(len(self.store.requests), 413)
        self.assertEqual(os.listdir(self.temp), [])

    def test_2_command_lines(self):
        self.tool.run(self.call())
        argvs = [argv for argv, _ in self.ffmpeg.calls]
        self.assertEqual(len(argvs), 6)
        self.assertEqual(argvs[0], ["ffmpeg", "-version"])
        work = {cwd for _, cwd in self.ffmpeg.calls}
        self.assertEqual(len(work), 1)
        self.assertTrue(os.path.basename(work.pop()).startswith("factory-conform-"))
        out = str(self.out)
        ffmpeg = ["ffmpeg", "-nostdin", "-hide_banner"]
        self.assertEqual([a[-1] for a in argvs[1:4]], [f"{out}/dialogue.wav", f"{out}/music.wav",
                                                       f"{out}/effects.wav"])
        self.assertEqual(argvs[3], ffmpeg + [
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-i", f"audio/{sha(SOUNDS['sfx-oar'])}",
            "-filter_complex",
            "[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,volume=-6dB,"
            "atrim=end_sample=22000,adelay=delays=200000S:all=1[a1];"
            "[0:a][a1]amix=inputs=2:duration=first:normalize=0,atrim=end_sample=816000[out]",
            "-map", "[out]", "-c:a", "pcm_s24le", f"{out}/effects.wav"])
        self.assertIn("volume=-1.5dB", argvs[1][argvs[1].index("-filter_complex") + 1])
        self.assertIn("[0:a][a1][a2][a3]amix=inputs=4:", argvs[1][argvs[1].index("-filter_complex") + 1])
        self.assertEqual(argvs[4], ffmpeg + [
            "-framerate", "24", "-start_number", "0", "-i", "frames/frame-%06d.png",
            "-i", f"{out}/dialogue.wav", "-i", f"{out}/music.wav", "-i", f"{out}/effects.wav",
            "-filter_complex", "[1:a][2:a][3:a]amix=inputs=3:duration=first:normalize=0[a]",
            "-map", "0:v", "-map", "[a]", "-frames:v", "408", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-crf", "18", "-c:a", "aac", "-b:a", "320k", f"{out}/master.mp4"])
        self.assertEqual(argvs[5], ffmpeg + ["-i", f"{out}/master.mp4", "-map", "0:a", "-af", "ebur128=peak=true",
                                             "-f", "null", "-"])

    def test_3_line_outside_the_shots(self):
        self.texts[PLAN]["lines"][2].update(start=400, end=420)
        self.refused("a line of mix-plan.json lies outside the shots")
        self.texts[PLAN]["lines"][2].update(start=110, end=130)
        self.assertEqual(self.tool.run(self.call()), {"usd": 0, "compute_min": 0})

    def test_4_each_defect(self):
        def plan(change):
            return lambda: change(self.texts[PLAN])

        def edit(path, change):
            return lambda: change(self.texts[path])

        cases = [
            ("a voice request", lambda: setattr(self, "request", "production/ep01/conform/requests/voice-L1.json"),
             "the first input is not a mix request"),
            ("shots.json absent", lambda: self.texts.pop(SHOTS), "an input the mix needs is missing"),
            ("fps 23.976", lambda: self.texts.__setitem__(REEL, self.texts[REEL].replace('"fps": 24', '"fps": 23.976')),
             "the edit list has no fps that is a whole number above zero"),
            ("Q1-2 from 121", edit(SHOTS, lambda d: d[1].update(first=121)),
             "shots.json is not one run of shots from frame 0"),
            ("a key notes", plan(lambda d: d.update(notes=[])),
             "mix-plan.json is not {lines, music, effects} with frames and gains"),
            ("a gain loud", plan(lambda d: d["lines"][0].update(gain="loud")),
             "mix-plan.json is not {lines, music, effects} with frames and gains"),
            ("start above end", plan(lambda d: d["effects"][0].update(start=111)),
             "mix-plan.json is not {lines, music, effects} with frames and gains"),
            ("L1 twice", plan(lambda d: d["lines"][1].update(line="L1")),
             "mix-plan.json is not {lines, music, effects} with frames and gains"),
            ("music ending at 408", plan(lambda d: d["music"][0].update(end=408)),
             "a music cue or effect of mix-plan.json ends after the last frame"),
            ("media voice-l9", plan(lambda d: d["lines"][2].update(media="voice-l9")),
             "a media id of mix-plan.json is not in the manifest"),
            ("Q1-2's render manifest absent", lambda: self.texts.pop("production/ep01/Q1-2/render/manifest.json"),
             "an input the mix needs is missing"),
            ("frame 200 absent", edit("production/ep01/Q1-2/render/manifest.json",
                                      lambda d: d["outputs"].pop(80)),
             "a frame of the picture is not in its shot's render manifest"),
        ]
        clean = copy.deepcopy(self.texts)
        for name, change, reason in cases:
            with self.subTest(name):
                self.texts, self.request = copy.deepcopy(clean), REQUEST
                self.ffmpeg.calls.clear()
                self.store.requests.clear()
                change()
                self.refused(reason)
        self.texts, self.request = copy.deepcopy(clean), "production/ep01/conform/requests/mix-ep01.json"
        self.assertEqual(self.tool.run(self.call()), {"usd": 0, "compute_min": 0})

    def test_5_store_and_ffmpeg(self):
        del self.store.objects[sha(frame(7))]
        self.failed("a frame or a sound is not in the asset store")
        self.store.objects[sha(frame(7))] = frame(7)

        self.store.requests.clear()
        self.ffmpeg.raises = FileNotFoundError(2, "No such file or directory")
        with self.assertRaises(Down):
            self.tool.run(self.call())
        self.assertEqual(self.store.requests, [])
        self.assertEqual(os.listdir(self.temp), [])
        self.ffmpeg.raises = None

        self.ffmpeg.fail = ("-frames:v", 1)
        self.failed("ffmpeg failed")
        self.ffmpeg.fail = None

        self.ffmpeg.peak = "-inf"
        self.failed("ebur128 gave no integrated loudness or true peak")

    def test_6_deterministic(self):
        self.tool.run(self.call())
        first = self.out
        self.tool.run(self.call())
        for name in ("mix.json", "manifest.json"):
            self.assertEqual((first / name).read_bytes(), (self.out / name).read_bytes())

    def test_7_interface(self):
        self.assertEqual(self.tool.RESOURCE, "store.assets")
        self.assertEqual(self.tool.STAGE_FILES, ("manifest.json", "mix.json"))
        self.assertEqual(self.tool.REASONS, REASONS)


def _real_ffmpeg():
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        return False
    try:
        done = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return b"libx264" in done.stdout


class RealFfmpegTest(unittest.TestCase):
    @unittest.skipUnless(_real_ffmpeg(), "ffmpeg and ffprobe with libx264 are not on PATH")
    def test_8_real_ffmpeg(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        made = tmp / "made"
        made.mkdir()
        quiet = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i"]
        subprocess.run(quiet + ["testsrc=size=64x48:rate=24", "-frames:v", "48", "-start_number", "0",
                                str(made / "frame-%06d.png")], check=True, capture_output=True)
        subprocess.run(quiet + ["sine=frequency=440:duration=1", str(made / "voice.wav")], check=True,
                       capture_output=True)
        subprocess.run(quiet + ["sine=frequency=220:duration=3", str(made / "music.wav")], check=True,
                       capture_output=True)
        store = AssetStore(self)
        frames = [(made / f"frame-{n:06d}.png").read_bytes() for n in range(48)]
        voice, music = (made / "voice.wav").read_bytes(), (made / "music.wav").read_bytes()
        for data in frames + [voice, music]:
            store.objects[sha(data)] = data
        key = tmp / "store.assets"
        key.write_text(f"{KEY_ID}\n{SECRET}\n{REGION}\n")
        key.chmod(0o600)
        shots = [{"shot": "S1", "first": 0, "last": 23}, {"shot": "S2", "first": 24, "last": 47}]
        texts = {
            REQUEST: "{}\n",
            PLAN: dumps({"lines": [{"line": "L1", "media": "voice", "start": 6, "end": 20, "gain": -3}],
                         "music": [{"media": "music", "start": 0, "end": 47, "gain": -12}], "effects": []}),
            MANIFEST: f"id\tsha256\tbytes\tsource\nvoice\t{sha(voice)}\t{len(voice)}\tupload\n"
                      f"music\t{sha(music)}\t{len(music)}\tupload\n",
            REEL: dumps({"fps": 24, "events": []}),
            SHOTS: dumps(shots),
        }
        for row in shots:
            texts[f"production/ep01/{row['shot']}/render/manifest.json"] = dumps({"outputs": [
                {"name": f"frame-{n:06d}.png", "sha256": sha(frames[n])} for n in range(row["first"], row["last"] + 1)]})
        out = tmp / "out"
        out.mkdir()
        call = {"hash": JOB, "ticket": 9, "caller": "sound-mixer", "expected": {"compute_min": 0, "usd": 0},
                "inputs": [{"path": p, "sha256": sha(t.encode()), "text": t} for p, t in texts.items()],
                "out": str(out), "record": str(tmp / "record"),
                "settings": {"ASSET_STORE": store.url("/b")}, "secrets": {"store.assets": str(key)}}
        self.assertEqual(cmd_run._load_tool("conform").run(call), {"usd": 0, "compute_min": 0})
        for stem in ("dialogue", "music", "effects"):
            done = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                                   "stream=duration_ts", "-of", "csv=p=0", str(out / f"{stem}.wav")],
                                  check=True, capture_output=True, text=True)
            self.assertEqual(done.stdout.strip(), "96000", stem)
        mix = json.loads((out / "mix.json").read_text())
        for key_name in ("loudness_lufs", "true_peak_dbtp"):
            self.assertIsInstance(mix[key_name], float)


if __name__ == "__main__":
    unittest.main()
