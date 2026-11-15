"""Tests for kit/lib/preview.py: the preview of a picture, a GLB (four rendered views) and a video (a contact sheet).

Every GLB, PNG and clip is built here; no binary file is committed. ffmpeg is a stand-in `run` except in
`RealFfmpeg`, which is skipped where ffmpeg is not installed.
"""

import json
import math
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import glb, preview  # noqa: E402

BACKGROUND = (112, 112, 118)
TILE = 512
CENTRES = {"front": (256, 256), "right": (768, 256), "back": (256, 768), "three-quarter": (768, 768)}


def _pad(data, fill):
    return data + fill * (-len(data) % 4)


def make_glb(document, binary=b""):
    """A GLB of ``document`` (a JSON value, or bytes taken as they are) and, when not empty, ``binary``."""
    text = document if isinstance(document, bytes) else json.dumps(document).encode("utf-8")
    chunks = [(glb.JSON_CHUNK, _pad(text, b" "))]
    if binary:
        chunks.append((glb.BIN_CHUNK, _pad(binary, b"\x00")))
    body = b"".join(struct.pack("<II", len(data), kind) + data for kind, data in chunks)
    return struct.pack("<4sII", glb.MAGIC, glb.VERSION, 12 + len(body)) + body


def make_png(width, height, pixel=lambda x, y: (255, 0, 0)):
    """An 8-bit RGB PNG, written the way the module writes its sheet."""
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    raw = b"".join(b"\x00" + bytes(c for x in range(width) for c in pixel(x, y)) for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def decode_png(data):
    """``(width, height, rows)`` of an 8-bit RGB PNG of filter 0 and one IDAT, as the module writes it."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    at, chunks = 8, []
    while at < len(data):
        length, kind = struct.unpack_from(">I4s", data, at)
        chunks.append((kind, data[at + 8:at + 8 + length]))
        at += 12 + length
    kinds = [kind for kind, _ in chunks]
    assert kinds == [b"IHDR", b"IDAT", b"IEND"], kinds
    width, height, depth, colour, _, _, _ = struct.unpack(">IIBBBBB", chunks[0][1])
    assert (depth, colour) == (8, 2)
    raw = zlib.decompress(chunks[1][1])
    line = 1 + 3 * width
    rows = []
    for y in range(height):
        assert raw[y * line] == 0, "filter"
        rows.append(raw[y * line + 1:(y + 1) * line])
    return width, height, rows


def pixel(rows, x, y):
    return tuple(rows[y][3 * x:3 * x + 3])


def foreground(rows, view):
    """Pixels of a tile that are not the background."""
    cx, cy = CENTRES[view]
    left, top = cx - TILE // 2, cy - TILE // 2
    back = bytes(BACKGROUND)
    count = 0
    for y in range(top, top + TILE):
        row = rows[y]
        for x in range(left, left + TILE):
            if row[3 * x:3 * x + 3] != back:
                count += 1
    return count


class Builder:
    """A glTF document and its binary chunk, grown one buffer view at a time."""

    def __init__(self):
        self.binary = b""
        self.doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "buffers": [],
                    "bufferViews": [], "accessors": [], "meshes": [], "nodes": [{"mesh": 0}]}

    def view(self, data, **extra):
        self.binary += b"\x00" * (-len(self.binary) % 4)
        self.doc["bufferViews"].append({"buffer": 0, "byteOffset": len(self.binary), "byteLength": len(data),
                                        **extra})
        self.binary += data
        return len(self.doc["bufferViews"]) - 1

    def accessor(self, values, kind, component=5126, **extra):
        form = {5126: "f", 5125: "I", 5123: "H"}[component]
        width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3}[kind]
        data = struct.pack(f"<{len(values)}{form}", *values)
        self.doc["accessors"].append({"bufferView": self.view(data), "componentType": component,
                                      "count": len(values) // width, "type": kind, **extra})
        return len(self.doc["accessors"]) - 1

    def material(self, factor, texture=None):
        pbr = {"baseColorFactor": factor}
        if texture is not None:
            self.doc.setdefault("images", []).append({"bufferView": self.view(texture), "mimeType": "image/png"})
            self.doc.setdefault("textures", []).append({"source": len(self.doc["images"]) - 1})
            pbr["baseColorTexture"] = {"index": len(self.doc["textures"]) - 1}
        self.doc.setdefault("materials", []).append({"pbrMetallicRoughness": pbr})
        return len(self.doc["materials"]) - 1

    def mesh(self, *primitives):
        self.doc["meshes"].append({"primitives": list(primitives)})

    def glb(self):
        self.doc["buffers"] = [{"byteLength": len(self.binary)}]
        return make_glb(self.doc, self.binary)


BOX_FACES = [(0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6), (0, 4, 5), (0, 5, 1), (3, 2, 6), (3, 6, 7),
             (0, 3, 7), (0, 7, 4), (1, 5, 6), (1, 6, 2)]


def box(factor=(0.1, 0.3, 0.9, 1.0), texture=None, point=None, transform=None):
    """A box of 2 x 1 x 1 centred on the origin, 8 vertices and 12 triangles; ``point`` puts every vertex there."""
    corners = [(x, y, z) for z in (0.5, -0.5) for x, y in ((-1, -0.5), (1, -0.5), (1, 0.5), (-1, 0.5))]
    if point is not None:
        corners = [point] * 8
    b = Builder()
    if transform:
        b.doc["nodes"][0].update(transform)
    attributes = {"POSITION": b.accessor([c for corner in corners for c in corner], "VEC3")}
    primitive = {"attributes": attributes,
                 "indices": b.accessor([i for face in BOX_FACES for i in face], "SCALAR", 5125)}
    if factor is not None:
        primitive["material"] = b.material(list(factor), texture)
    if texture is not None:
        attributes["TEXCOORD_0"] = b.accessor([0.5, 0.5] * 8, "VEC2")
    b.mesh(primitive)
    return b


def strip(triangles):
    """``triangles`` thin triangles side by side, not indexed, no material."""
    values = []
    for n in range(triangles):
        x = n * 0.01
        values += [x, 0.0, 0.0, x + 0.008, 0.0, 0.0, x, 1.0, 0.0]
    b = Builder()
    b.mesh({"attributes": {"POSITION": b.accessor(values, "VEC3")}})
    return b.glb()


def no_ffmpeg(argv, **kwargs):
    raise FileNotFoundError(2, "No such file or directory", argv[0])


def answer(stdout=b"", returncode=0):
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, returncode, stdout, b"")
    return run


def red_decode(argv, **kwargs):
    assert argv[:3] == preview.FFMPEG and argv[-1] == "pipe:1" and "scale=128:128" in argv, argv
    assert kwargs["timeout"] == preview.TEXTURE_S and kwargs["input"].startswith(b"\x89PNG")
    return subprocess.CompletedProcess(argv, 0, b"\xff\x00\x00" * (128 * 128), b"")


class Sheet(unittest.TestCase):
    """Done when 1 and 2."""

    @classmethod
    def setUpClass(cls):
        cls.data = box().glb()
        cls.sheet = preview.glb_sheet(cls.data, run=no_ffmpeg, workers=1)
        cls.width, cls.height, cls.rows = decode_png(cls.sheet.data)
        cls.counts = {view: foreground(cls.rows, view) for view in CENTRES}

    def test_png(self):
        self.assertEqual((self.sheet.kind, self.sheet.ext), ("glb", "png"))
        self.assertEqual((self.width, self.height), (1024, 1024))

    def test_views(self):
        front, right, back, three = (self.counts[v] for v in ("front", "right", "back", "three-quarter"))
        self.assertGreater(front, 1000)
        self.assertLessEqual(abs(front - back), 0.02 * front)
        self.assertTrue(1.8 <= front / right <= 2.2, front / right)
        for other in (front, right, back):
            self.assertNotEqual(three, other)

    def test_not_one_colour(self):
        colours = {pixel(self.rows, x, y) for y in range(0, 1024, 8) for x in range(0, 1024, 8)}
        self.assertGreater(len(colours), 2)

    def test_colour(self):
        red, _, blue = pixel(self.rows, *CENTRES["front"])
        self.assertGreater(blue, red)
        self.assertEqual(pixel(self.rows, 2, 2), BACKGROUND)

    def test_fills_92_percent(self):
        cx, cy = CENTRES["front"]
        lit = [x for x in range(cx - 256, cx + 256) if pixel(self.rows, x, cy) != BACKGROUND]
        # The widest view is the three-quarter one, 2 cos 40 + sin 40 units: 92% of the tile.
        expected = 2 / (2 * math.cos(math.radians(40)) + math.sin(math.radians(40))) * 0.92 * 512
        self.assertLessEqual(abs(len(lit) - expected), 2, (len(lit), expected))

    def test_notes(self):
        self.assertEqual(list(self.sheet.notes), ["4 views: front, right, back, three-quarter", "12 triangles",
                                                  "flat shading"])

    def test_same_bytes(self):
        again = preview.glb_sheet(self.data, run=no_ffmpeg, workers=1)
        parallel = preview.glb_sheet(self.data, run=no_ffmpeg, workers=4)
        self.assertEqual(again.data, self.sheet.data)
        self.assertEqual(parallel.data, self.sheet.data)
        self.assertEqual(parallel.notes, self.sheet.notes)

    def test_node_transform(self):
        turned = box(transform={"rotation": [0, math.sqrt(0.5), 0, math.sqrt(0.5)]}).glb()
        rows = decode_png(preview.glb_sheet(turned, run=no_ffmpeg, workers=1).data)[2]
        front, right = foreground(rows, "front"), foreground(rows, "right")
        self.assertTrue(1.8 <= right / front <= 2.2, right / front)

    def test_no_material_is_white(self):
        rows = decode_png(preview.glb_sheet(box(factor=None).glb(), run=no_ffmpeg, workers=1).data)[2]
        r, g, b = pixel(rows, *CENTRES["front"])
        self.assertTrue(max(r, g, b) - min(r, g, b) <= 2 and r > 150, (r, g, b))


class Texture(unittest.TestCase):
    """Done when 3."""

    @classmethod
    def setUpClass(cls):
        cls.data = box(factor=(1, 1, 1, 1), texture=make_png(128, 128, lambda x, y: (0, 200, 0))).glb()

    def centre(self, sheet):
        return pixel(decode_png(sheet.data)[2], *CENTRES["front"])

    def test_red_decode(self):
        sheet = preview.glb_sheet(self.data, run=red_decode, workers=1)
        red, green, blue = self.centre(sheet)
        self.assertGreater(red, 150)
        self.assertLess(green, 10)
        self.assertLess(blue, 10)
        self.assertIn("textured", sheet.notes)
        self.assertNotIn("flat shading", sheet.notes)

    def test_decoded_once(self):
        calls = []

        def counting(argv, **kwargs):
            calls.append(argv)
            return red_decode(argv, **kwargs)
        preview.glb_sheet(self.data, run=counting, workers=1)
        self.assertEqual(len(calls), 1)

    def check_flat(self, run):
        sheet = preview.glb_sheet(self.data, run=run, workers=1)
        r, g, b = self.centre(sheet)
        self.assertTrue(max(r, g, b) - min(r, g, b) <= 2, (r, g, b))
        self.assertGreater(r, 150)
        self.assertEqual(sheet.notes[2], "flat shading: texture not decoded")
        return sheet

    def test_no_ffmpeg(self):
        self.check_flat(no_ffmpeg)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "box.glb"
            path.write_bytes(self.data)
            self.assertEqual(preview.of(str(path), run=no_ffmpeg, workers=1).notes[2],
                             "flat shading: texture not decoded")

    def test_exit_1(self):
        self.check_flat(answer(b"\xff\x00\x00" * (128 * 128), returncode=1))

    def test_wrong_size(self):
        self.check_flat(answer(b"\xff\x00\x00" * (128 * 127)))

    def test_timeout(self):
        def slow(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        self.check_flat(slow)


class Malformed(unittest.TestCase):
    """Done when 4."""

    def unreadable(self, data):
        with self.assertRaises(preview.PreviewError) as caught:
            preview.glb_sheet(data, run=no_ffmpeg, workers=1)
        self.assertEqual(str(caught.exception), "glb: not a GLB the kit can read")

    def edited(self, change):
        b = box()
        change(b.doc)
        return b.glb()

    def test_clean(self):
        self.assertEqual(preview.glb_sheet(box().glb(), run=no_ffmpeg, workers=1).kind, "glb")

    def test_empty(self):
        self.unreadable(b"")

    def test_magic(self):
        self.unreadable(b"glTX" + box().glb()[4:])

    def test_length(self):
        data = box().glb()
        self.unreadable(data[:8] + struct.pack("<I", len(data) + 4) + data[12:])

    def test_every_prefix(self):
        data = box().glb()
        for end in range(0, len(data), 7):
            with self.subTest(end=end):
                self.unreadable(data[:end])

    def test_json_not_an_object(self):
        self.unreadable(make_glb([1, 2, 3]))

    def test_buffer_view_out_of_range(self):
        self.unreadable(self.edited(lambda doc: doc["accessors"][0].update(bufferView=99)))

    def test_negative_index(self):
        self.unreadable(self.edited(lambda doc: doc["accessors"][0].update(bufferView=-1)))

    def test_count_past_view(self):
        self.unreadable(self.edited(lambda doc: doc["accessors"][0].update(count=9)))

    def test_count_not_whole(self):
        self.unreadable(self.edited(lambda doc: doc["accessors"][0].update(count=7.5)))

    def test_meshes_a_string(self):
        self.unreadable(self.edited(lambda doc: doc.update(meshes="box")))

    def test_own_child(self):
        self.unreadable(self.edited(lambda doc: doc["nodes"][0].update(children=[0])))

    def test_cycle(self):
        def change(doc):
            doc["nodes"] = [{"mesh": 0, "children": [1]}, {"children": [0]}]
        self.unreadable(self.edited(change))

    def test_index_past_last_vertex(self):
        b = box()
        b.doc["accessors"][1] = {**b.doc["accessors"][1], "bufferView": b.view(struct.pack("<3I", 0, 1, 8)),
                                 "count": 3}
        self.unreadable(b.glb())

    def test_missing_position(self):
        self.unreadable(self.edited(lambda doc: doc["meshes"][0]["primitives"][0]["attributes"].clear()))


class Degenerate(unittest.TestCase):
    """Done when 5."""

    def error(self, data):
        with self.assertRaises(preview.PreviewError) as caught:
            preview.glb_sheet(data, run=no_ffmpeg, workers=1)
        return str(caught.exception)

    def test_clean(self):
        self.assertEqual(preview.glb_sheet(box().glb(), run=no_ffmpeg, workers=1).kind, "glb")

    def test_one_point(self):
        self.assertEqual(self.error(box(point=(0.25, 0.5, 0.75)).glb()), "glb: the model has no visible area")

    def test_line(self):
        b = Builder()
        b.mesh({"attributes": {"POSITION": b.accessor([0, 0, 0, 1, 1, 1, 2, 2, 2], "VEC3")}})
        self.assertEqual(self.error(b.glb()), "glb: the model has no visible area")

    def test_no_triangle(self):
        b = box()
        b.doc["accessors"][1]["count"] = 0
        self.assertEqual(self.error(b.glb()), "glb: the model has no triangle to draw")

    def test_only_points(self):
        b = box()
        b.doc["meshes"][0]["primitives"][0]["mode"] = 0
        self.assertEqual(self.error(b.glb()), "glb: the model has no triangle to draw")

    def test_not_finite(self):
        for bad in (b"\x00\x00\xc0\x7f", b"\x00\x00\x80\x7f"):  # NaN, Infinity
            with self.subTest(bad=bad):
                b = box()
                b.binary = bad + b.binary[4:]
                self.assertEqual(self.error(b.glb()), "glb: a position is not a finite number")

    def test_extension(self):
        b = box()
        b.doc["extensionsRequired"] = ["KHR_texture_transform", "KHR_draco_mesh_compression"]
        self.assertEqual(self.error(b.glb()), "glb: needs the extension KHR_draco_mesh_compression")

    def test_ignored_extensions(self):
        b = box()
        b.doc["extensionsRequired"] = ["KHR_materials_unlit", "KHR_texture_transform"]
        self.assertEqual(preview.glb_sheet(b.glb(), run=no_ffmpeg, workers=1).kind, "glb")


class Caps(unittest.TestCase):
    """Done when 6 and 7."""

    def test_over_the_cap(self):
        with mock.patch.object(preview, "TRIANGLE_CAP", 1000):
            sheet = preview.glb_sheet(strip(1001), run=no_ffmpeg, workers=1)
        self.assertEqual(sheet.notes[1], "1001 triangles")
        self.assertEqual(sheet.notes[-1], "drew 501 of 1001 triangles")

    def test_every_second_triangle(self):
        drawn = []

        def spy(job, check=None):
            drawn.append(len(job[1]) // 3)
            return real(job, check)
        real = preview._draw
        with mock.patch.object(preview, "TRIANGLE_CAP", 1000), mock.patch.object(preview, "_draw", spy):
            preview.glb_sheet(strip(1001), run=no_ffmpeg, workers=1)
        self.assertEqual(drawn, [501] * 4)

    def test_at_the_cap(self):
        with mock.patch.object(preview, "TRIANGLE_CAP", 1000):
            sheet = preview.glb_sheet(strip(1000), run=no_ffmpeg, workers=1)
        self.assertEqual(list(sheet.notes), ["4 views: front, right, back, three-quarter", "1000 triangles",
                                             "flat shading"])

    def test_time_cap(self):
        ticks = iter(range(0, 10 ** 6, 100))
        with self.assertRaises(preview.PreviewError) as caught:
            preview.glb_sheet(box().glb(), run=no_ffmpeg, clock=lambda: next(ticks), workers=1)
        self.assertEqual(str(caught.exception), "glb: preview took longer than 180 s")
        sheet = preview.glb_sheet(box().glb(), run=no_ffmpeg, clock=lambda: 0.0, workers=1)
        self.assertEqual(sheet.kind, "glb")

    def test_time_cap_inside_the_loop(self):
        calls = []

        def clock():
            calls.append(1)
            return 0.0
        with mock.patch.object(preview, "CHECK_EVERY", 100):
            preview.glb_sheet(strip(1000), run=no_ffmpeg, clock=clock, workers=1)
        self.assertGreaterEqual(len(calls), 4 * 10)


class Kinds(unittest.TestCase):
    """Done when 8."""

    def test_heads(self):
        heads = {b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR": "picture", b"\xff\xd8\xff\xe0\x00\x10JFIF": "picture",
                 b"GIF87a\x01\x00": "picture", b"GIF89a\x01\x00": "picture", b"RIFF\x00\x00\x00\x00WEBPVP8 ": "picture",
                 b"glTF\x02\x00\x00\x00": "glb", b"\x00\x00\x00\x18ftypisom\x00\x00": "video",
                 b"\x1a\x45\xdf\xa3\x01\x00": "video",
                 b'{"a":1}': None, b"# Title\n": None, bytes(16): None, b"": None}
        for head, kind in heads.items():
            with self.subTest(head=head):
                self.assertEqual(preview.kind_of(head), kind)

    def test_never_raises(self):
        self.assertIsNone(preview.kind_of(None))

    def test_picture_unchanged(self):
        data = make_png(4, 3, lambda x, y: (x * 60, y * 80, 7))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gradient.png"
            path.write_bytes(data)
            found = preview.of(str(path), run=no_ffmpeg)
        self.assertEqual(found, preview.Preview("picture", "png", data, ()))

    def test_text_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.md"
            path.write_text("# Title\n")
            with self.assertRaises(preview.PreviewError) as caught:
                preview.of(str(path), run=no_ffmpeg)
        self.assertEqual(str(caught.exception), "not a picture, a GLB or a video")

    def test_glb_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "box.glb"
            path.write_bytes(box().glb())
            self.assertEqual(preview.of(str(path), run=no_ffmpeg, workers=1).kind, "glb")

    def test_glb_too_large(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "big.glb"
            with open(path, "wb") as handle:
                handle.write(b"glTF\x02\x00\x00\x00")
                handle.truncate(preview.GLB_MAX_BYTES + 1)
            with mock.patch.object(preview, "glb_sheet") as sheet, self.assertRaises(preview.PreviewError) as caught:
                preview.of(str(path), run=no_ffmpeg)
        self.assertEqual(str(caught.exception), "glb: larger than 67108864 bytes")
        sheet.assert_not_called()


class FakeVideo:
    """A stand-in `run`: ffprobe and ffmpeg each answer as told."""

    def __init__(self, probe=b"8.0\n", sheet=None, probe_exit=0, sheet_exit=0, raises=None):
        self.probe, self.sheet = probe, make_png(8, 6) if sheet is None else sheet
        self.probe_exit, self.sheet_exit, self.raises, self.calls = probe_exit, sheet_exit, raises or {}, []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        program = argv[0]
        if program in self.raises:
            raise self.raises[program]
        if program == "ffprobe":
            return subprocess.CompletedProcess(argv, self.probe_exit, self.probe, b"")
        return subprocess.CompletedProcess(argv, self.sheet_exit, self.sheet, b"")


class Video(unittest.TestCase):
    """Done when 9."""

    path = "clip.mp4"

    def error(self, run):
        with self.assertRaises(preview.PreviewError) as caught:
            preview.video_sheet(self.path, run=run)
        return str(caught.exception)

    def test_eight_seconds(self):
        run = FakeVideo()
        found = preview.video_sheet(self.path, run=run)
        self.assertEqual(found, preview.Preview("video", "png", run.sheet, ("12 frames over 8.0 s",)))
        (probe, probe_kw), (argv, kwargs) = run.calls
        self.assertEqual(probe, ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                 "default=noprint_wrappers=1:nokey=1", self.path])
        graph = argv[argv.index("-vf") + 1]
        for part in ("fps=12/8.0", "scale=320:-2", "tile=4x3"):
            self.assertIn(part, graph)
        self.assertEqual(argv[argv.index("-i") + 1], self.path)
        self.assertEqual(argv[-1], "pipe:1")
        self.assertEqual((probe_kw["timeout"], kwargs["timeout"]), (120, 120))

    def test_no_duration(self):
        for probe in (b"0\n", b"N/A\n", b"", b"-3\n", b"nan\n"):
            with self.subTest(probe=probe):
                self.assertEqual(self.error(FakeVideo(probe=probe)), "video: no duration")

    def test_not_installed(self):
        for program in ("ffprobe", "ffmpeg"):
            with self.subTest(program=program):
                missing = FileNotFoundError(2, "No such file or directory", program)
                self.assertEqual(self.error(FakeVideo(raises={program: missing})), "video: ffmpeg is not installed")

    def test_exit(self):
        self.assertEqual(self.error(FakeVideo(probe_exit=1)), "video: ffprobe exited 1")
        self.assertEqual(self.error(FakeVideo(sheet_exit=1)), "video: ffmpeg exited 1")

    def test_timeout(self):
        for program in ("ffprobe", "ffmpeg"):
            with self.subTest(program=program):
                slow = subprocess.TimeoutExpired([program, self.path], 120)
                self.assertEqual(self.error(FakeVideo(raises={program: slow})),
                                 "video: ffmpeg did not finish in 120 s")

    def test_no_frame(self):
        for sheet in (b"", b"not a png"):
            with self.subTest(sheet=sheet):
                self.assertEqual(self.error(FakeVideo(sheet=sheet)), "video: no frame decoded")

    def test_of_a_video(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "clip.mp4"
            path.write_bytes(b"\x00\x00\x00\x18ftypisom" + bytes(32))
            run = FakeVideo()
            self.assertEqual(preview.of(str(path), run=run).notes, ("12 frames over 8.0 s",))
            self.assertEqual(run.calls[0][0][-1], str(path))


def real_ffmpeg():
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        return False
    try:
        return subprocess.run(["ffmpeg", "-hide_banner", "-version"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@unittest.skipUnless(real_ffmpeg(), "needs ffmpeg")
class RealFfmpeg(unittest.TestCase):
    """Done when 10."""

    def test_clip(self):
        with tempfile.TemporaryDirectory() as directory:
            clip = str(Path(directory) / "clip.mp4")
            subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                            "testsrc=duration=3:size=160x90:rate=10", "-pix_fmt", "yuv420p", clip],
                           check=True, capture_output=True, timeout=120)
            found = preview.of(clip)
        self.assertEqual(found.data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">I", found.data[16:20])[0], 1290)
        self.assertEqual(list(found.notes), ["12 frames over 3.0 s"])

    def test_texture(self):
        data = box(factor=(1, 1, 1, 1), texture=make_png(16, 16, lambda x, y: (20, 190, 40))).glb()
        sheet = preview.glb_sheet(data, workers=1)
        self.assertIn("textured", sheet.notes)
        r, g, b = pixel(decode_png(sheet.data)[2], *CENTRES["front"])
        self.assertTrue(g > 150 and r < 40 and b < 60, (r, g, b))


class NoPaths(unittest.TestCase):
    """Done when 11: no message, note or exception text holds a path or the file name the test gave."""

    def check(self, text, directory, name):
        for bad in ("/", "\\", Path(directory).name, name):
            self.assertNotIn(bad, text)

    def test_every_case(self):
        texts = []
        with tempfile.TemporaryDirectory() as directory:
            def of(name, data, run=no_ffmpeg):
                path = Path(directory) / name
                path.write_bytes(data)
                try:
                    texts.extend(preview.of(str(path), run=run, workers=1).notes)
                except preview.PreviewError as error:
                    texts.append(str(error))
                    texts.extend(str(a) for a in error.args)
                return name
            names = [
                of("plain.glb", box().glb()),
                of("textured.glb", box(factor=(1, 1, 1, 1), texture=make_png(4, 4)).glb()),
                of("red.glb", box(factor=(1, 1, 1, 1), texture=make_png(128, 128)).glb(), red_decode),
                of("broken.glb", box().glb()[:-9]),
                of("point.glb", box(point=(0, 0, 0)).glb()),
                of("notes.md", b"# Title\n"),
            ]
            b = box()
            b.doc["extensionsRequired"] = ["KHR_draco_mesh_compression"]
            names.append(of("draco.glb", b.glb()))
            clip = b"\x00\x00\x00\x18ftypisom" + bytes(16)
            for run in (FakeVideo(), FakeVideo(probe=b"N/A"), FakeVideo(probe_exit=1), FakeVideo(sheet=b""),
                        FakeVideo(raises={"ffprobe": FileNotFoundError(2, "No such file", "/x/ffprobe")}),
                        FakeVideo(raises={"ffmpeg": subprocess.TimeoutExpired(["ffmpeg"], 120)})):
                names.append(of("take-one.mp4", clip, run))
            self.assertGreater(len(texts), 15)
            for text in texts:
                for name in names:
                    with self.subTest(text=text, name=name):
                        self.check(text, directory, name)


class Module(unittest.TestCase):
    """Done when 12: the module imports only the standard library, glb and media, and writes nothing."""

    def test_imports(self):
        source = (ROOT / "kit" / "lib" / "preview.py").read_text()
        imports = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
        kit = [line for line in imports if "kit" in line]
        self.assertEqual(kit, ["from kit.lib import glb, media"])
        for word in ("urllib", "socket", "Popen", "print(", "os.environ", "getenv"):
            self.assertFalse(word in source, word)
        self.assertNotRegex(source, r"open\(.*[\"'][wa]")


if __name__ == "__main__":
    unittest.main()
