"""Tests for kit/lib/frames.py: the frame measurement of `<stage>/render.json` (brief section 16).

Each test writes frames 0 to 3, 64x36, into a fresh directory with `zlib` and `struct` (no frame is
committed), and an EXR placeholder beside each. `ffmpeg`, `tesseract` and the metric `cvvdp` are copies of
tests/fixtures/frames/fake_tool.py in `<tmp>/bin`, which read `bin/control.json` and log each call; `run`
maps a command's first word to that copy. Each seeded defect is one change to the clean world. The
clipping case uses its own 100x36 frames, so that 1 % of the pixels is a whole number of them.
"""

import hashlib
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from kit.lib import frames  # noqa: E402
from kit.lib.lints import render as render_lint  # noqa: E402
from kit.lib.lints import lighting as lighting_lint  # noqa: E402

FAKE = ROOT / "tests" / "fixtures" / "frames" / "fake_tool.py"
TOOLS = ("ffmpeg", "tesseract", "cvvdp")
FIRST, LAST, FPS = 0, 3, 24
WIDTH, HEIGHT = 64, 36
BLACK_LINE = "[blackdetect @ 0x5581] black_start:0.0833 black_end:0.125 black_duration:0.0417\n"


def png(path, width, height, pixels):
    """An 8-bit RGB PNG of `pixels` (rgb24 bytes), every row with filter type 0."""
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    stride = width * 3
    raw = b"".join(b"\x00" + pixels[y * stride:(y + 1) * stride] for y in range(height))
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def pattern(frame, width=WIDTH, height=HEIGHT):
    """A frame with no channel at 0 or 255, different on every frame."""
    out = bytearray()
    for y in range(height):
        for x in range(width):
            out += bytes(((x * 3 + y * 2 + frame * 5) % 200 + 20, (x + y * 4 + frame) % 180 + 30,
                          (x * 2 + frame * 7) % 150 + 40))
    return bytes(out)


def spread(pixels):
    """The measured spread of #115: 0.56 % of the bytes changed, each by 1 to 9."""
    out = bytearray(pixels)
    changed = round(len(out) * 0.0056)
    for i in range(changed):
        out[(i * 997) % len(out)] += 1 + i % 9
    return bytes(out)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="frames-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        lines = FAKE.read_text(encoding="utf-8").splitlines(keepends=True)
        for name in TOOLS:
            tool = self.bin / name
            tool.write_text(f"#!{sys.executable}\n" + "".join(lines[1:]), encoding="utf-8")
            tool.chmod(0o755)
        self.log = self.tmp / "log.jsonl"
        self.control = {"log": str(self.log)}
        self.dir = self.tmp / "frames"
        self.write(self.dir)

    exr = True

    def write(self, directory, change=None, width=WIDTH, height=HEIGHT):
        directory.mkdir()
        for frame in range(FIRST, LAST + 1):
            pixels = pattern(frame, width, height)
            png(directory / f"frame-{frame:06d}.png", width, height, change(pixels) if change else pixels)
            if self.exr:
                (directory / f"frame-{frame:06d}.exr").write_bytes(b"fake exr")
        return directory

    def run_fake(self, argv, **kwargs):
        (self.bin / "control.json").write_text(json.dumps(self.control), encoding="utf-8")
        return subprocess.run([str(self.bin / argv[0]), *argv[1:]], **kwargs)

    def measure(self, directory=None, sampled=(0, 3), run=None):
        return frames.measure(str(directory or self.dir), FPS, FIRST, LAST, sampled, run=run or self.run_fake)

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def assertRaisesNaming(self, name, *args, **kwargs):
        with self.assertRaises(frames.FramesError) as caught:
            self.measure(*args, **kwargs)
        self.assertIn(name, str(caught.exception))


class Files(Base):
    """Rulings 1 and 2: the frames on disk, their size and their hashes."""

    def test_clean(self):
        result = self.measure()
        self.assertNotIn("rerender", result)
        self.assertEqual(list(result), ["fps", "frames", "count", "width", "height", "frames_sha256",
                                        "sequence_sha256", "black", "nan", "text", "samples"])
        self.assertEqual((result["fps"], result["frames"], result["count"], result["width"], result["height"]),
                         (FPS, {"first": 0, "last": 3}, 4, WIDTH, HEIGHT))
        self.assertEqual((result["black"], result["nan"], result["text"]), ([], [], []))
        hashes = [hashlib.sha256((self.dir / f"frame-{f:06d}.png").read_bytes()).hexdigest() for f in range(4)]
        self.assertEqual(result["frames_sha256"], hashes)
        self.assertEqual(result["sequence_sha256"], hashlib.sha256("\n".join(hashes).encode()).hexdigest())
        self.assertEqual([s["frame"] for s in result["samples"]], [0, 3])

    def test_missing_frame(self):
        (self.dir / "frame-000002.png").unlink()
        self.assertRaisesNaming("frame-000002.png")
        self.assertEqual(self.calls(), [])

    def test_extra_frame(self):
        shutil.copyfile(self.dir / "frame-000003.png", self.dir / "frame-000009.png")
        self.assertRaisesNaming("frame-000009.png")

    def test_other_files_ignored(self):
        (self.dir / "frame-9.png").write_bytes(b"not of the pattern")
        (self.dir / "notes.txt").write_text("x")
        self.assertEqual(self.measure()["count"], 4)

    def test_other_size(self):
        png(self.dir / "frame-000001.png", 32, 18, pattern(1, 32, 18))
        self.assertRaisesNaming("frame-000001.png")

    def test_not_a_png(self):
        (self.dir / "frame-000001.png").write_bytes(b"GIF89a" + bytes(40))
        self.assertRaisesNaming("frame-000001.png")

    def test_sampled_outside(self):
        self.assertRaisesNaming("7", sampled=(0, 7))


class Black(Base):
    """Ruling 3 and correction 2: blackdetect's pairs as frames."""

    def test_clean(self):
        self.assertEqual(self.measure()["black"], [])
        argv = [c["argv"] for c in self.calls() if c["step"] == "black"]
        self.assertEqual(argv, [["-nostdin", "-hide_banner", "-framerate", "24", "-start_number", "0", "-i",
                                 "frame-%06d.png", "-vf", "blackdetect=d=0:pix_th=0.10", "-an", "-f", "null", "-"]])
        self.assertEqual({c["cwd"] for c in self.calls()}, {str(self.dir.resolve())})

    def test_black(self):
        self.control["black"] = BLACK_LINE
        self.assertEqual(self.measure()["black"], [2])

    def test_black_offset_by_first(self):
        self.control["black"] = BLACK_LINE
        for frame in range(4):
            (self.dir / f"frame-{frame:06d}.png").rename(self.dir / f"frame-{frame + 10:06d}.png")
            (self.dir / f"frame-{frame:06d}.exr").rename(self.dir / f"frame-{frame + 10:06d}.exr")
        self.assertEqual(frames.measure(str(self.dir), FPS, 10, 13, [], run=self.run_fake)["black"], [12])

    def test_ffmpeg_fails(self):
        self.control["fail"] = "black"
        self.assertRaisesNaming("ffmpeg")


class Nan(Base):
    """Ruling 4: the EXR frames decoded to 32-bit floats."""

    def test_finite(self):
        self.assertEqual(self.measure()["nan"], [])
        exr = [c["argv"] for c in self.calls() if c["step"] == "exr"]
        self.assertEqual([a[a.index("-i") + 1] for a in exr], [f"frame-{f:06d}.exr" for f in range(4)])
        self.assertTrue(all(a[-5:] == ["-f", "rawvideo", "-pix_fmt", "gbrpf32le", "-"] for a in exr))

    def test_nan(self):
        self.control["exr"] = {"frame-000003.exr": ["nan"]}
        self.assertEqual(self.measure()["nan"], [3])

    def test_inf(self):
        self.control["exr"] = {"frame-000003.exr": ["inf"]}
        self.assertEqual(self.measure()["nan"], [3])

    def test_nan_and_inf(self):
        self.control["exr"] = {"frame-000003.exr": ["nan", "inf"]}
        self.assertEqual(self.measure()["nan"], [3])

    def test_missing_exr(self):
        (self.dir / "frame-000002.exr").unlink()
        self.assertRaisesNaming("frame-000002.exr")


class Text(Base):
    """Ruling 5: tesseract on each frame, a word of three letters or more."""

    def test_text(self):
        self.control["text"] = {"frame-000001.png": "Unreal Engine\n\n"}
        self.assertEqual(self.measure()["text"], [{"frame": 1, "text": "Unreal Engine"}])
        argv = [c["argv"] for c in self.calls() if c["step"] == "tesseract"]
        self.assertEqual(argv, [[f"frame-{f:06d}.png", "stdout"] for f in range(4)])

    def test_no_word(self):
        self.control["text"] = {"frame-000001.png": "  \n", "frame-000002.png": "ab", "frame-000003.png": "12 34\n"}
        self.assertEqual(self.measure()["text"], [])

    def test_tesseract_fails(self):
        self.control["fail"] = "tesseract"
        self.assertRaisesNaming("tesseract")


class Samples(Base):
    """Ruling 6: mean linear colour, its luminance and the clipped share."""

    def test_pure_red(self):
        png(self.dir / "frame-000002.png", WIDTH, HEIGHT, b"\xff\x00\x00" * (WIDTH * HEIGHT))
        self.assertEqual(self.measure(sampled=[2])["samples"],
                         [{"frame": 2, "luma": 0.2126, "clipped": 1.0, "color": [1.0, 0.0, 0.0]}])

    def test_one_percent_clipped(self):
        directory = self.write(self.tmp / "wide", width=100, height=36)
        pixels = bytearray(pattern(1, 100, 36))
        for i in range(36):
            pixels[i * 100 * 3 + 3 * (i % 3) + i % 3] = 255
        png(directory / "frame-000001.png", 100, 36, bytes(pixels))
        sample = self.measure(directory, sampled=[1])["samples"][0]
        self.assertEqual(sample["clipped"], 0.01)
        self.assertEqual(self.measure(directory, sampled=[0])["samples"][0]["clipped"], 0.0)

    def test_srgb_curve(self):
        png(self.dir / "frame-000000.png", WIDTH, HEIGHT, b"\x80\x0a\x00" * (WIDTH * HEIGHT))
        sample = self.measure(sampled=[0])["samples"][0]
        red, green = ((128 / 255 + 0.055) / 1.055) ** 2.4, 10 / 255 / 12.92
        self.assertEqual(sample["color"], [round(red, 6), round(green, 6), 0.0])
        self.assertEqual(sample["luma"], round(0.2126 * red + 0.7152 * green, 6))
        self.assertEqual(sample["clipped"], 0.0)

    def test_spread(self):
        """Two renders are never byte-equal: different hashes, samples inside the lighting lint."""
        second = self.write(self.tmp / "second", change=spread)
        for frame in range(4):
            a = (self.dir / f"frame-{frame:06d}.png").read_bytes()
            self.assertNotEqual(a, (second / f"frame-{frame:06d}.png").read_bytes())
        one, two = self.measure(sampled=range(4)), self.measure(second, sampled=range(4))
        self.assertTrue(all(a != b for a, b in zip(one["frames_sha256"], two["frames_sha256"])))
        self.assertNotEqual(one["sequence_sha256"], two["sequence_sha256"])
        for a, b in zip(one["samples"], two["samples"]):
            self.assertEqual(a["clipped"], b["clipped"])
            self.assertLess(abs(a["luma"] - b["luma"]), 0.001)
            self.assertLess(abs(a["luma"] - b["luma"]), lighting_lint.LUMA_SHARE * a["luma"])
            self.assertTrue(all(abs(x - y) < 0.001 for x, y in zip(a["color"], b["color"])))
            self.assertNotEqual(a, b)

    def test_spread_is_measured(self):
        pixels = pattern(0)
        changed = spread(pixels)
        diffs = [abs(x - y) for x, y in zip(pixels, changed) if x != y]
        self.assertEqual((max(diffs), round(len(diffs) / len(pixels) * 100, 2)), (9, 0.56))
        self.assertLess(max(changed), 255)

    def test_short_decode(self):
        def run(argv, **kwargs):
            done = self.run_fake(argv, **kwargs)
            if "rgb24" in argv:
                done.stdout = done.stdout[:-3]
            return done
        self.assertRaisesNaming("frame-000000.png", sampled=[0], run=run)


class Rerender(Base):
    """Ruling 7: the metric on each re-rendered pair."""

    def pairs(self):
        return [(0, self.dir / "frame-000000.png", self.dir / "frame-000001.png"),
                (3, self.dir / "frame-000003.png", self.dir / "frame-000002.png")]

    def test_scores(self):
        self.control["metric"] = {"stdout": "computing\n9.95\n"}
        result = frames.rerender(self.pairs(), ["cvvdp", "--quiet"], run=self.run_fake)
        self.assertEqual(result, {"frames": [0, 3], "metric": "cvvdp", "scores": [9.95, 9.95]})
        self.assertTrue(render_lint._rerender(result))
        argv = [c["argv"] for c in self.calls()]
        self.assertEqual(argv[1], ["--quiet", str(self.dir / "frame-000003.png"), str(self.dir / "frame-000002.png")])

    def installed_at(self, command):
        """`rerender` with `command`; the fake metric answers whatever path the command names."""
        self.control["metric"] = {"stdout": "computing\n9.95\n"}
        called = []

        def run(argv, **kwargs):
            called.append(argv[0])
            return self.run_fake(["cvvdp", *argv[1:]], **kwargs)
        result = frames.rerender(self.pairs(), command, run=run)
        self.assertEqual(called, [command[0], command[0]])
        return result

    def test_metric_is_the_file_name(self):
        """ISSUE-609: the record names the metric, not where one machine installed it."""
        result = self.installed_at(["/opt/x/bin/metric-perceptual", "--fast"])
        self.assertEqual(result, {"frames": [0, 3], "metric": "metric-perceptual", "scores": [9.95, 9.95]})
        self.assertTrue(render_lint._rerender(result))

    def test_metric_on_path(self):
        result = self.installed_at(["metric-perceptual"])
        self.assertEqual(result, {"frames": [0, 3], "metric": "metric-perceptual", "scores": [9.95, 9.95]})

    def test_exit_1(self):
        self.control["metric"] = {"stdout": "9.95\n", "exit": 1}
        with self.assertRaises(frames.FramesError) as caught:
            frames.rerender(self.pairs(), ["cvvdp"], run=self.run_fake)
        self.assertIn("exited 1 on frame 0", str(caught.exception))

    def test_no_number(self):
        self.control["metric"] = {"stdout": "9.95\nno number\n"}
        with self.assertRaises(frames.FramesError) as caught:
            frames.rerender(self.pairs(), ["cvvdp"], run=self.run_fake)
        self.assertIn("no number", str(caught.exception))

    def test_nothing_printed(self):
        self.control["metric"] = {"stdout": ""}
        with self.assertRaises(frames.FramesError):
            frames.rerender(self.pairs(), ["cvvdp"], run=self.run_fake)


def real_ffmpeg():
    if not shutil.which("ffmpeg"):
        return False
    try:
        return subprocess.run(["ffmpeg", "-hide_banner", "-version"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@unittest.skipUnless(real_ffmpeg(), "needs ffmpeg")
class RealFfmpeg(Base):
    """Done when 2: the real ffmpeg on the written frames gives the fake's `width`, `count` and `samples`.

    No EXR file is written: `run` answers the EXR decoding of ruling 4 itself (finite floats) and passes every
    other command to `subprocess.run`, tesseract as the fake copy, since the machine may not have it.
    """

    exr = False

    def test_real_ffmpeg(self):
        def answer_exr(passed):
            def run(argv, **kwargs):
                if "gbrpf32le" in argv:
                    return subprocess.CompletedProcess(argv, 0, struct.pack("<3f", 0.5, 0.5, 0.5), b"")
                return passed(argv, **kwargs)
            return run

        def real(argv, **kwargs):
            if argv[0] == "tesseract":
                argv = [str(self.bin / "tesseract"), *argv[1:]]
            return subprocess.run(argv, **kwargs)

        (self.bin / "control.json").write_text(json.dumps(self.control), encoding="utf-8")
        png(self.dir / "frame-000002.png", WIDTH, HEIGHT, b"\xff\x00\x00" * (WIDTH * HEIGHT))
        fake = self.measure(sampled=range(4), run=answer_exr(self.run_fake))
        measured = self.measure(sampled=range(4), run=answer_exr(real))
        self.assertEqual(list(self.dir.glob("*.exr")), [])
        self.assertEqual((measured["width"], measured["count"], measured["samples"]),
                         (fake["width"], fake["count"], fake["samples"]))
        self.assertEqual(measured["samples"][2]["color"], [1.0, 0.0, 0.0])


class Interface(unittest.TestCase):
    """The module reads only the standard library and compares no frame with another run's."""

    def test_imports(self):
        import ast
        tree = ast.parse((ROOT / "kit" / "lib" / "frames.py").read_text(encoding="utf-8"))
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level == 0}
        self.assertTrue(all(n.split(".")[0] in sys.stdlib_module_names or n == "__future__" for n in names))


if __name__ == "__main__":
    unittest.main()
