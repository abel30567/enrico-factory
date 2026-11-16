"""Tests for kit/lib/tools/pad.py: the tool called with a job's ``call``, as `factory run` calls it.

The media store's public link is one `ThreadingHTTPServer` on 127.0.0.1, port 0, in a thread, answering
`GET /media/<sha256>` with the bytes it holds under that sha256 and 404 otherwise; it records each `GET` path.
The pictures and the manifest are the fixtures of tests/fixtures/pad/. `ffmpeg` is a copy of
tests/fixtures/pad/fake_ffmpeg.py in a `bin/` that is the whole `PATH`, as tests/test_analysis.py installs
fake_tool.py; the tests of `Real` run the real `ffmpeg` and are skipped where there is none. Nothing reaches a
host other than 127.0.0.1.
"""

import hashlib
import http.server
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib.tools import Down, Failed, pad  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "pad"
MANIFEST = "media/manifest.tsv"
GREY = (0xD3, 0xD3, 0xD3)


def sha(data):
    return hashlib.sha256(data).hexdigest()


PNG = (FIX / "source.png").read_bytes()
JPEG = (FIX / "source.jpg").read_bytes()
GIF = (FIX / "source.gif").read_bytes()
MANIFEST_TEXT = (FIX / "manifest.tsv").read_text(encoding="utf-8")
HELD = {sha(data): data for data in (PNG, JPEG, GIF)}


def request(**keys):
    return json.dumps(keys)


def free_port():
    """A loopback port nothing listens on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def png(width, height, rgb):
    """A PNG of ``width`` x ``height`` pixels, every one the colour ``rgb``."""
    rows = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def pixels(data):
    """``(width, height, rows)`` of an 8-bit RGB or RGBA PNG, ``rows[y][x]`` an ``(r, g, b)``."""
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    at, idat, header = 8, b"", None
    while at < len(data):
        length, kind = struct.unpack(">I4s", data[at:at + 8])
        body = data[at + 8:at + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        at += 12 + length
    width, height, depth, colour, _, _, interlace = header
    assert depth == 8 and colour in (2, 6) and interlace == 0, header
    step = 3 if colour == 2 else 4
    raw, stride, rows, previous = zlib.decompress(idat), width * step, [], bytes(width * step)
    for y in range(height):
        kind, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            left = line[i - step] if i >= step else 0
            up, corner = previous[i], previous[i - step] if i >= step else 0
            if kind == 1:
                line[i] = (line[i] + left) & 255
            elif kind == 2:
                line[i] = (line[i] + up) & 255
            elif kind == 3:
                line[i] = (line[i] + (left + up) // 2) & 255
            elif kind == 4:
                p = left + up - corner
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - corner)
                line[i] = (line[i] + (left if pa <= pb and pa <= pc else up if pb <= pc else corner)) & 255
        previous = bytes(line)
        rows.append([tuple(line[x * step:x * step + 3]) for x in range(width)])
    return width, height, rows


class Link:
    """The media store's public link on 127.0.0.1: ``held`` is the bytes it serves by sha256; ``gets`` every
    GET path seen."""

    def __init__(self, held):
        self.held, self.gets = held, []
        link = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                link.gets.append(self.path)
                data = link.held.get(self.path.rsplit("/", 1)[-1]) if self.path.startswith("/media/") else None
                self.send_response(200 if data is not None else 404)
                self.send_header("Content-Length", str(len(data or b"")))
                self.end_headers()
                self.wfile.write(data or b"")

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/media"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class PadCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.link = Link(dict(HELD))
        self.addCleanup(self.link.close)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        self.log = self.dir / "ffmpeg.log"
        self.control = {"log": str(self.log), "fail": False, "write": str(FIX / "source.png")}
        self.path = str(self.bin)

    def fake_ffmpeg(self):
        text = (FIX / "fake_ffmpeg.py").read_text(encoding="utf-8").split("\n", 1)[1]
        path = self.bin / "ffmpeg"
        path.write_text(f"#!{sys.executable}\n{text}", encoding="utf-8")
        path.chmod(0o755)
        (self.bin / "control.json").write_text(json.dumps(self.control), encoding="utf-8")

    def call(self, text, manifest=MANIFEST_TEXT, base=None):
        out, record = self.dir / "job" / "out", self.dir / "job" / "record"
        out.mkdir(parents=True, exist_ok=True)
        record.mkdir(parents=True, exist_ok=True)
        return {"hash": "0" * 64, "ticket": 41, "caller": "operator",
                "expected": {"compute_min": 0, "usd": 0},
                "inputs": [{"path": "media/requests/pad.json", "sha256": sha(text.encode()), "text": text},
                           {"path": MANIFEST, "sha256": sha(manifest.encode()), "text": manifest}],
                "out": str(out), "record": str(record),
                "settings": {"MEDIA_STORE": "http://127.0.0.1:9/b", "MEDIA_PUBLIC_BASE": base or self.link.url},
                "secrets": {"store.media": str(self.dir / "absent-key")}}

    def run_tool(self, call):
        with mock.patch.dict(os.environ, {"PATH": self.path}):
            return pad.run(call)

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def assertRefused(self, call, reason):
        with self.assertRaises(Failed) as caught:
            self.run_tool(call)
        self.assertEqual(caught.exception.reason, reason)
        self.assertIn(reason, pad.REASONS)
        self.assertEqual((caught.exception.usd, caught.exception.compute_min), (0, 0))
        self.assertEqual(os.listdir(call["out"]), [])


class Module(PadCase):
    def test_contract(self):
        self.assertEqual(pad.RESOURCE, "store.media")
        self.assertIsInstance(pad.REASONS, tuple)
        self.assertTrue(all(isinstance(r, str) for r in pad.REASONS))
        self.assertFalse(hasattr(pad, "STAGE_FILES"))
        self.assertEqual((pad.COLOUR, pad.SCALE, pad.OUTPUT), ("0xD3D3D3", (2, 3), "pad.png"))


class Fake(PadCase):
    def setUp(self):
        super().setUp()
        self.fake_ffmpeg()

    def test_png_clean_and_exact_argv(self):
        call = self.call(request(source="media:card-png"))
        self.assertEqual(self.run_tool(call), {"usd": 0, "compute_min": 0})
        self.assertEqual(os.listdir(call["out"]), ["pad.png"])
        self.assertEqual(self.link.gets, [f"/media/{sha(PNG)}"])
        [seen] = self.calls()
        source = os.path.join(seen["cwd"], "source.png")
        self.assertEqual(seen["argv"], [
            "-nostdin", "-hide_banner", "-v", "error", "-noautorotate", "-i", source,
            "-vf", "format=rgb24,scale=4:5,pad=6:8:1:1:color=0xD3D3D3",
            "-frames:v", "1", os.path.join(call["out"], "pad.png")])
        self.assertEqual(seen["input_sha256"], sha(PNG))
        self.assertEqual(seen["env"], {"HOME": os.path.join(seen["cwd"], "home"), "LANG": "C.UTF-8",
                                       "PATH": self.path})
        self.assertFalse(os.path.exists(seen["cwd"]))

    def test_jpeg_clean(self):
        call = self.call(request(source="media:card-jpg"))
        self.assertEqual(self.run_tool(call), {"usd": 0, "compute_min": 0})
        self.assertEqual(os.listdir(call["out"]), ["pad.png"])
        [seen] = self.calls()
        self.assertEqual(seen["argv"][4:7], ["-noautorotate", "-i", os.path.join(seen["cwd"], "source.jpg")])
        self.assertEqual(seen["argv"][8], "format=rgb24,scale=5:4,pad=8:6:1:1:color=0xD3D3D3")
        self.assertEqual(seen["input_sha256"], sha(JPEG))

    def test_not_a_request(self):
        for text in (request(source="media:card-png", colour="#000000"),
                     request(source="card-png"), request(source="media:"), request(source=["media:card-png"]),
                     request(source=["media:card-png", "media:card-jpg"]), request(sources="media:card-png"),
                     request(), "[]", "not json"):
            with self.subTest(text=text):
                self.assertRefused(self.call(text), "not a request")
        self.assertEqual((self.link.gets, self.calls()), ([], []))

    def test_source_not_in_the_manifest(self):
        self.assertRefused(self.call(request(source="media:card-webp")), "source not in the manifest")
        self.assertRefused(self.call(request(source="media:card-png"), manifest="id\tsha256\tbytes\tsource\n"),
                           "source not in the manifest")
        self.assertEqual((self.link.gets, self.calls()), ([], []))

    def test_wrong_bytes(self):
        self.link.held[sha(PNG)] = JPEG
        self.assertRefused(self.call(request(source="media:card-png")), "source not fetched")
        self.link.held[sha(PNG)] = PNG + b"\x00"
        self.assertRefused(self.call(request(source="media:card-png")), "source not fetched")
        self.link.held[sha(PNG)] = PNG[:-1] + bytes([PNG[-1] ^ 1])
        self.assertRefused(self.call(request(source="media:card-png")), "source not fetched")
        del self.link.held[sha(PNG)]
        self.assertRefused(self.call(request(source="media:card-png")), "source not fetched")
        self.assertEqual(self.calls(), [])

    def test_gif_is_not_a_picture(self):
        self.assertRefused(self.call(request(source="media:card-gif")), "source is not a PNG or JPEG")
        self.assertEqual(self.link.gets, [f"/media/{sha(GIF)}"])
        self.assertEqual(self.calls(), [])

    def test_link_down(self):
        call = self.call(request(source="media:card-png"), base=f"http://127.0.0.1:{free_port()}/media")
        with self.assertRaises(Down):
            self.run_tool(call)
        self.assertEqual((os.listdir(call["out"]), self.calls()), ([], []))

    def test_ffmpeg_absent(self):
        self.path = str(self.dir / "empty")
        os.mkdir(self.path)
        call = self.call(request(source="media:card-png"))
        with self.assertRaises(Down):
            self.run_tool(call)
        self.assertEqual(os.listdir(call["out"]), [])

    def test_ffmpeg_exits_1(self):
        """An exit 1 is a failure even when a file was written, and the file is removed."""
        self.control["fail"] = True
        self.fake_ffmpeg()
        self.assertRefused(self.call(request(source="media:card-png")), "ffmpeg failed")
        self.assertEqual(len(self.calls()), 1)

    def test_ffmpeg_writes_nothing(self):
        self.control["write"] = None
        self.fake_ffmpeg()
        self.assertRefused(self.call(request(source="media:card-png")), "ffmpeg failed")
        self.assertEqual(len(self.calls()), 1)


@unittest.skipUnless(shutil.which("ffmpeg"), "no ffmpeg on PATH")
class Real(PadCase):
    COLOUR = (42, 110, 155)

    def setUp(self):
        super().setUp()
        self.path = os.environ.get("PATH", "")

    def serve(self, name, data):
        self.link.held[sha(data)] = data
        return f"id\tsha256\tbytes\tsource\n{name}\t{sha(data)}\t{len(data)}\tsources/{name}.png\n"

    def test_real_ffmpeg_pads_a_png(self):
        manifest = self.serve("plain", png(60, 90, self.COLOUR))
        call = self.call(request(source="media:plain"), manifest=manifest)
        self.assertEqual(self.run_tool(call), {"usd": 0, "compute_min": 0})
        self.assertEqual(os.listdir(call["out"]), ["pad.png"])
        width, height, rows = pixels((Path(call["out"]) / "pad.png").read_bytes())
        self.assertEqual((width, height), (60, 90))
        band = [rows[y][x] for y in range(90) for x in range(60) if not (10 <= x < 50 and 15 <= y < 75)]
        self.assertEqual(len(band), 60 * 90 - 40 * 60)
        self.assertEqual(set(band), {GREY})
        self.assertEqual(rows[45][30], self.COLOUR)

    def test_real_ffmpeg_pads_the_jpeg_fixture(self):
        call = self.call(request(source="media:card-jpg"))
        self.run_tool(call)
        width, height, rows = pixels((Path(call["out"]) / "pad.png").read_bytes())
        self.assertEqual((width, height), (8, 6))
        band = [rows[y][x] for y in range(6) for x in range(8) if not (1 <= x < 6 and 1 <= y < 5)]
        self.assertEqual(set(band), {GREY})

    def test_real_ffmpeg_keeps_a_jpeg_as_coded(self):
        """A JPEG with an Exif orientation of 6 (turn 90 degrees) is padded as coded, not turned: its 80x40 header
        size is the canvas, the left half stays red and the right half blue."""
        red, blue = (255, 0, 0), (0, 0, 255)
        rows = b"".join(b"\x00" + bytes(red) * 40 + bytes(blue) * 40 for _ in range(40))
        source = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 80, 40, 8, 2, 0, 0, 0))
                  + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
        (self.dir / "halves.png").write_bytes(source)
        subprocess.run(["ffmpeg", "-y", "-nostdin", "-hide_banner", "-v", "error", "-i", str(self.dir / "halves.png"),
                        "-frames:v", "1", "-q:v", "2", str(self.dir / "halves.jpg")], check=True)
        tiff = b"II*\x00" + struct.pack("<IH", 8, 1) + struct.pack("<HHII", 0x0112, 3, 1, 6) + struct.pack("<I", 0)
        exif = b"Exif\x00\x00" + tiff
        coded = (self.dir / "halves.jpg").read_bytes()
        oriented = coded[:2] + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + coded[2:]
        call = self.call(request(source="media:oriented"), manifest=self.serve("oriented", oriented))
        self.assertEqual(self.run_tool(call), {"usd": 0, "compute_min": 0})
        width, height, rows = pixels((Path(call["out"]) / "pad.png").read_bytes())
        self.assertEqual((width, height), (80, 40))

        def kind(rgb):
            if rgb == GREY:
                return "G"
            return "R" if rgb[0] > 180 and rgb[2] < 80 else "B" if rgb[2] > 180 and rgb[0] < 80 else "?"

        middle = "".join(kind(rows[20][x]) for x in range(80))
        self.assertEqual(middle[:13] + middle[66:], "G" * 27)
        self.assertEqual(middle[13:38], "R" * 25)
        self.assertEqual(middle[41:66], "B" * 25)


if __name__ == "__main__":
    unittest.main()
