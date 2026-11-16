"""Tests for kit/lib/handoff.py: the one preview comment on a pull that hands off an artifact.

Every picture, GLB, clip and package here is made by the test (a gradient, a box, invented sections). The
previewer is the stand-in ``StubPreviews`` (the contract of ``kit.lib.preview``), except in ``Integration``,
which runs only once ``kit.lib.preview`` is on the base. GitHub is ``github.Recorded`` in a temporary
directory; the media store is ``test_media.FakeStore`` or a stub ``put``. Nothing reaches the network.
"""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import unittest
import zlib
from pathlib import Path
from typing import NamedTuple
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from kit.lib import github, glb, handoff, markers, media, resources  # noqa: E402
from kit.lib.handoff import HEADING, ROLE, HandoffError, Item  # noqa: E402

SOURCE = "The outputs of job 0123456789ab by tool fake."
LINK = "https://media.example.invalid/"
GLB_NOTES = ("4 views: front, right, back, three-quarter",)
VIDEO_NOTES = ("12 frames over 8.0 s",)
BOX_LINE = "12 triangles, 1 material(s), textures none, height 1.0 m, no skeleton"
NOT_A_GLB = "glb: not a GLB the kit can read"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def png(width=4, height=4, shade=0):
    """An 8-bit RGB PNG of a gradient."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    rows = b"".join(b"\x00" + bytes(v for x in range(width) for v in ((x * 40 + shade) % 256, (y * 40) % 256, 128))
                    for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


def png_size(data):
    return struct.unpack(">II", data[16:24])


def box(width=2.0, height=1.0, depth=1.0):
    """A GLB of a box, 12 triangles, one material with a blue base colour."""
    x, z = width / 2, depth / 2
    corners = [(a, b, c) for a in (-x, x) for b in (0.0, height) for c in (-z, z)]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    indices = [i for a, b, c, d in faces for i in (a, b, c, a, c, d)]
    positions = struct.pack(f"<{3 * len(corners)}f", *[v for corner in corners for v in corner])
    index_bytes = struct.pack(f"<{len(indices)}H", *indices)
    binary = positions + index_bytes + b"\x00" * (-len(index_bytes) % 4)
    document = {
        "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(positions)},
                        {"buffer": 0, "byteOffset": len(positions), "byteLength": len(index_bytes)}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(corners), "type": "VEC3",
                       "min": [-x, 0.0, -z], "max": [x, height, z]},
                      {"bufferView": 1, "componentType": 5123, "count": len(indices), "type": "SCALAR"}],
        "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [0.1, 0.3, 0.9, 1]}}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
    }
    text = json.dumps(document).encode()
    text += b" " * (-len(text) % 4)
    body = struct.pack("<II", len(text), glb.JSON_CHUNK) + text + struct.pack("<II", len(binary), glb.BIN_CHUNK) + binary
    return struct.pack("<4sII", glb.MAGIC, glb.VERSION, 12 + len(body)) + body


VIDEO = b"\x00\x00\x00\x18ftypisom" + b"an invented clip" * 4


def package(sections, lead=("A lead line of the package.",), extra=None):
    """A markdown package of ``sections`` invented sections, each with two lines."""
    lines = ["---", "title: an invented package", "---", ""] + list(lead) + [""]
    for n in range(1, sections + 1):
        lines += [f"## Section {n}", ""] + (extra or {}).get(n, [f"Line one of section {n}.",
                                                                f"Line two of section {n}."]) + [""]
    return "\n".join(lines)


class PreviewError(Exception):
    pass


class Preview(NamedTuple):
    kind: str
    ext: str
    data: bytes
    notes: tuple


class StubPreviews:
    """The contract of ``kit.lib.preview`` (#549, ruling 1) with a fixed sheet; ``calls`` counts ``of``."""

    PreviewError = PreviewError
    Preview = Preview

    def __init__(self, sheet=None, error=None):
        self.sheet = png(8, 8) if sheet is None else sheet
        self.error, self.calls = error, 0

    def kind_of(self, head):
        if media.picture_type(head) is not None:
            return "picture"
        if head[:4] == b"glTF":
            return "glb"
        if head[4:8] == b"ftyp" or head[:4] == b"\x1a\x45\xdf\xa3":
            return "video"
        return None

    def of(self, path):
        self.calls += 1
        with open(path, "rb") as handle:
            data = handle.read()
        kind = self.kind_of(data[:16])
        if kind is None:
            raise PreviewError("not a picture, a GLB or a video")
        if kind == "picture":
            return Preview("picture", media.picture_type(data[:12]), data, ())
        if self.error is not None:
            raise PreviewError(self.error)
        if kind == "glb":
            try:
                glb.read(data)
            except glb.Broken:
                raise PreviewError(NOT_A_GLB) from None
            return Preview("glb", "png", self.sheet, GLB_NOTES)
        return Preview("video", "png", self.sheet, VIDEO_NOTES)


class Put:
    """A stub ``put``: the link of the file's sha256, every call recorded as ``(name, bytes)``."""

    def __init__(self, error=None):
        self.error, self.calls = error, []

    def __call__(self, path):
        data = Path(path).read_bytes()
        self.calls.append((os.path.basename(path), data, path))
        if self.error is not None:
            raise HandoffError(self.error)
        return LINK + sha(data)


def slash(*parts):
    """A machine path made of ``parts``, written so that this file names none."""
    return "/" + "/".join(parts)


def forbidden(tmp):
    return (str(tmp), str(Path.home()), tempfile.gettempdir())


def checked(test, body, tmp):
    """``body`` after the checks every built body passes: its first line, not a marker, no machine path."""
    first = body.split("\n", 1)[0]
    test.assertEqual(first, HEADING)
    test.assertIsNone(markers.parse(first))
    test.assertIsNone(markers.parse(body))
    handoff.guard(body, forbidden(tmp))
    test.assertTrue(github.fits(body, ROLE))
    return body


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.out = self.tmp / "out"
        self.out.mkdir()

    def write(self, name, data):
        path = self.out / name
        path.write_bytes(data if isinstance(data, bytes) else data.encode())
        return path

    def items(self, files):
        """The items of ``files`` (name to bytes or text) written in the output directory."""
        rows = [{"name": name, "bytes": len(self.write(name, data).read_bytes()), "sha256": "-"}
                for name, data in files.items()]
        return handoff.of_job(self.out, rows)

    def build(self, files, put=None, previews=None, source=SOURCE):
        put = Put() if put is None else put
        previews = StubPreviews() if previews is None else previews
        body, complete = handoff.build(self.items(files), put, source, previews, forbidden(self.tmp))
        return checked(self, body, self.tmp), complete, put

    def recorded(self, *numbers):
        directory = self.tmp / "github"
        (directory / "pulls").mkdir(parents=True)
        for number in numbers or (7,):
            (directory / "pulls" / f"{number}.json").write_text(json.dumps({
                "number": number, "title": "an invented pull", "body": "", "state": "open", "labels": [],
                "head_ref": "feat/x", "base_ref": "trunk", "ticket": None, "created_at": "2026-11-15T00:00:00Z",
                "merged_at": None}))
        return github.Recorded(directory)

    def previews_of(self, gh, number=7):
        return [c for c in gh.comments(number) if c["role"] == ROLE]


# -- the scenarios NoPaths runs again

def picture_body(case):
    return case.build({"panel.png": png()})[0]


def glb_body(case):
    return case.build({"model.glb": box()})[0]


def video_body(case):
    return case.build({"clip.mp4": VIDEO})[0]


def text_body(case):
    return case.build({"outline.md": package(12)})[0]


def failed_body(case):
    return case.build({"model.glb": box(), "panel.png": png()}, put=Put("the media store did not take a preview: "
                                                                        "store answered 500"))[0]


class Pictures(Case):
    def test_picture_embedded_by_the_link_put_returned(self):
        data = png()
        body, complete, put = self.build({"panel.png": data})
        lines = body.rstrip("\n").split("\n")
        self.assertEqual(lines[0], HEADING)
        self.assertEqual(lines[1:4], ["", SOURCE, ""])
        self.assertIn(f"![panel.png]({LINK}{sha(data)})", lines)
        self.assertEqual(lines[-1], f"<!-- preview-of {sha(data)} -->")
        self.assertTrue(complete)
        self.assertEqual([(name, got) for name, got, _ in put.calls], [("panel.png", data)])
        self.assertEqual(put.calls[0][2], str(self.out / "panel.png"))

    def test_the_exact_form(self):
        data = png()
        body = self.build({"panel.png": data})[0]
        self.assertEqual(body, "\n".join([
            HEADING, "", SOURCE, "", "### `panel.png`", f"sha256 {sha(data)[:12]}, {len(data)} B",
            f"![panel.png]({LINK}{sha(data)})", "", f"<!-- preview-of {sha(data)} -->"]) + "\n")
        self.assertEqual(self.build({"panel.png": data})[0], body)

    def test_seeded_the_name_is_not_the_link(self):
        body = self.build({"panel.png": png()})[0]
        self.assertNotIn("![panel.png](panel.png)", body)
        self.assertNotIn(str(self.out), body)

    def test_sizes(self):
        self.assertEqual([handoff._size(n) for n in (0, 999, 1000, 12345, 999999, 1000000, 16 * 10 ** 6)],
                         ["0 B", "999 B", "1.0 kB", "12.3 kB", "1000.0 kB", "1.0 MB", "16.0 MB"])


class Glb(Case):
    def test_box_summary_sheet_and_notes(self):
        sheet = png(16, 16)
        seen = []

        def put(path):
            seen.append((os.path.basename(path), Path(path).read_bytes(), path))
            return LINK + "sheet"

        body, complete, _ = self.build({"model.glb": box()}, put=put, previews=StubPreviews(sheet))
        lines = body.split("\n")
        self.assertTrue(complete)
        at = lines.index("### `model.glb`")
        self.assertEqual(lines[at + 2:at + 5], [BOX_LINE, f"![model.glb, 4 views]({LINK}sheet)", GLB_NOTES[0]])
        self.assertEqual([(name, data) for name, data, _ in seen], [(f"{sha(sheet)}.png", sheet)])
        self.assertFalse(os.path.exists(seen[0][2]))
        self.assertFalse(os.path.exists(os.path.dirname(seen[0][2])))

    def test_seeded_the_preview_fails(self):
        body, complete, put = self.build({"model.glb": box()},
                                         previews=StubPreviews(error="glb: the model has no visible area"))
        lines = body.split("\n")
        self.assertFalse(complete)
        self.assertIn(BOX_LINE, lines)
        self.assertIn("Preview not available: glb: the model has no visible area", lines)
        self.assertFalse([line for line in lines if line.startswith("![")])
        self.assertEqual(put.calls, [])

    def test_seeded_not_a_glb(self):
        body, complete, _ = self.build({"model.glb": b"glTF" + b"\x02\x00\x00\x00 broken bytes"})
        lines = body.split("\n")
        self.assertFalse(complete)
        self.assertFalse([line for line in lines if "triangles" in line])
        self.assertIn(f"Preview not available: {NOT_A_GLB}", lines)

    def test_seeded_the_store_fails(self):
        body, complete, put = self.build({"model.glb": box()}, put=Put("the media store did not take a preview: "
                                                                       "store answered 500"))
        lines = body.split("\n")
        self.assertFalse(complete)
        self.assertIn("Preview not available: the media store did not take a preview: store answered 500", lines)
        self.assertIn(BOX_LINE, lines)
        self.assertIn(GLB_NOTES[0], lines)
        self.assertFalse(os.path.exists(put.calls[0][2]))

    def test_seeded_an_error_out_of_of_is_not_caught_by_build(self):
        stub = StubPreviews()
        stub.of = mock.Mock(side_effect=RuntimeError("unexpected"))
        with self.assertRaises(RuntimeError):
            self.build({"model.glb": box()}, previews=stub)


class Video(Case):
    def test_contact_sheet_and_notes(self):
        sheet = png(12, 9)
        body, complete, put = self.build({"clip.mp4": VIDEO}, previews=StubPreviews(sheet))
        lines = body.split("\n")
        self.assertTrue(complete)
        at = lines.index("### `clip.mp4`")
        self.assertEqual(lines[at + 2:at + 4], [f"![clip.mp4, contact sheet]({LINK}{sha(sheet)})", VIDEO_NOTES[0]])
        self.assertNotIn("4 views", body)
        self.assertEqual([name for name, _, _ in put.calls], [f"{sha(sheet)}.png"])


class Summary(Case):
    def twelve(self):
        return package(12, extra={
            1: ["~~~ python", "# not a heading", "~~~ end", "after the fence"],
            2: ["x" * 600],
            3: [],
            4: ["one", "two", "three", "four", "five"]})

    def test_twelve_sections(self):
        found = handoff.summarise(self.twelve())
        self.assertNotIn("---", found)
        self.assertNotIn("title: an invented package", found)
        self.assertEqual(found[0], "A lead line of the package.")
        headings = [line for line in found if line.startswith("## ")]
        self.assertEqual(headings, [f"## Section {n}" for n in range(1, 9)])
        self.assertIn("# not a heading", found)
        self.assertEqual(found[found.index("## Section 1") + 1:found.index("## Section 2")],
                         ["~~~ python", "# not a heading", "~~~ end"])
        long = found[found.index("## Section 2") + 1]
        self.assertEqual((len(long), long[-1], long[:239]), (240, "…", "x" * 239))
        self.assertEqual(found[found.index("## Section 3") + 1], "## Section 4")
        self.assertEqual(found[found.index("## Section 4") + 1:found.index("## Section 5")], ["one", "two", "three"])
        self.assertEqual(found[-1], "... 4 more headings: Section 9; Section 10; Section 11; Section 12")

    def test_the_block_in_build_indents_a_tilde_line(self):
        body = self.build({"outline.md": self.twelve()})[0]
        lines = body.split("\n")
        start = lines.index(handoff.FENCE)
        end = lines.index(handoff.FENCE, start + 1)
        inside = lines[start + 1:end]
        self.assertFalse([line for line in inside if line.startswith("~~~")])
        self.assertIn(" ~~~ end", inside)
        self.assertIn(" ~~~ python", inside)

    def test_seeded_a_heading_inside_a_fence(self):
        text = "```\n# fenced\n```\n# Real\n\nbody\n"
        self.assertEqual(handoff.summarise(text), ["```", "# fenced", "```", "# Real", "body"])

    def test_seeded_a_summary_longer_than_the_cap(self):
        found = handoff.summarise(package(400, extra={n: ["y" * 300] * 3 for n in range(1, 401)}))
        self.assertLessEqual(len("\n".join(found)), handoff.TEXT_CHARS)
        self.assertEqual(found[-1], "[cut at 4000 characters]")
        self.assertLessEqual(len("\n".join(handoff.summarise(package(400)))), handoff.TEXT_CHARS)

    def test_more_headings_lists_twenty(self):
        found = handoff.summarise(package(40))
        self.assertTrue(found[-1].startswith("... 32 more headings: Section 9; "))
        self.assertLessEqual(len(found[-1]), handoff.LINE_CHARS)
        self.assertNotIn("Section 29", found[-1])
        short = handoff.summarise("\n".join(f"# H{n}" for n in range(40)))
        self.assertEqual(short[-1], "... 32 more headings: " + "; ".join(f"H{n}" for n in range(8, 28)))

    def test_empty(self):
        self.assertEqual(handoff.summarise(""), ["(empty)"])
        self.assertEqual(handoff.summarise("---\ntitle: x\n---\n\n   \n"), ["(empty)"])


class Guard(Case):
    REFUSED = (slash("Users", "someone", "art", "ref.png"), slash("home", "a", "b"), slash("tmp", "x"), "~/work",
               "$HOME/x", "file://" + slash("etc", "passwd"), "C:\\Users\\a", "C:/a", "see ~ there", slash("var"),
               f"({slash('opt')})", slash("srv", "x"), slash("tmp"), slash("Users"))
    ALLOWED = ("and/or", "https://media.example.invalid/ab12/cd", "episodes/ep02/outline.md", "1/2",
               "<!-- preview-of " + "a" * 64 + " -->", "[the outline](episodes/ep02/outline.md)", "a / b",
               "~~~~", "http://127.0.0.1:9/pub/ab")

    def test_refused(self):
        for text in self.REFUSED:
            with self.subTest(text=text), self.assertRaises(HandoffError) as raised:
                handoff.guard(f"{HEADING}\n\nsee {text} here\n")
            self.assertEqual(str(raised.exception), "the comment holds a path")
        with self.assertRaises(HandoffError):
            handoff.guard("a body naming a-secret-place-9f\n", forbid=("a-secret-place-9f",))

    def test_allowed(self):
        for text in self.ALLOWED:
            with self.subTest(text=text):
                handoff.guard(f"{HEADING}\n\nsee {text} here\n", forbid=("a-secret-place-9f",))

    def test_a_path_in_a_package_is_withheld(self):
        text = f"# Notes\n\nThe reference is at {slash('Users', 'x', 'y')} today.\nA clean line.\n"
        self.assertEqual(handoff.summarise(text), ["# Notes", "[line withheld: it holds a path]", "A clean line."])
        body = self.build({"notes.md": text})[0]
        self.assertNotIn(slash("Users", "x", "y"), body)

    def test_seeded_a_url_is_not_a_path(self):
        handoff.guard("![a](https://media.example.invalid" + slash("Users", "home", "tmp") + ")\n")

    def test_a_refused_body_is_not_posted(self):
        gh = self.recorded()
        items = [Item("outline.md", "a" * 64, 10, None, "# One\n\nbody\n")]
        with self.assertRaises(HandoffError):
            handoff.post(gh, 7, items, Put(), "the source names " + slash("home", "a", "b"), StubPreviews())
        self.assertEqual(self.previews_of(gh), [])


class NotAMarker(Case):
    def test_heading_is_never_a_marker(self):
        self.assertIsNone(markers.parse(HEADING))
        self.assertFalse(HEADING.startswith("["))
        for source in (SOURCE, "[PRODUCER] GATE PASS", "[PREVIEW] POSTED"):
            for name in ("outline.md", "[WRITER] READY.md"):
                items = [Item(name, sha(b"x"), 1, None, "[WRITER] READY\n\n# H\n")]
                body, _ = handoff.build(items, Put(), source, StubPreviews())
                with self.subTest(source=source, name=name):
                    checked(self, body, self.tmp)
                    self.assertTrue(body.startswith(HEADING + "\n"))


class Idempotent(Case):
    def test_twice_posts_once_and_builds_nothing_the_second_time(self):
        gh, put, stub = self.recorded(), Put(), StubPreviews()
        items = self.items({"panel.png": png(), "model.glb": box()})
        first = handoff.post(gh, 7, items, put, SOURCE, stub)
        self.assertEqual(first["role"], ROLE)
        checked(self, first["body"], self.tmp)
        calls, of_calls = len(put.calls), stub.calls
        self.assertEqual((calls, of_calls), (2, 1))
        self.assertIsNone(handoff.post(gh, 7, items, put, SOURCE, stub))
        self.assertEqual((len(put.calls), stub.calls), (calls, of_calls))
        self.assertIsNone(handoff.post(gh, 7, list(reversed(items)), put, SOURCE, stub))
        self.assertEqual(len(self.previews_of(gh)), 1)
        self.write("panel.png", png(shade=7))
        changed = handoff.of_job(self.out, [{"name": "panel.png"}, {"name": "model.glb"}])
        second = handoff.post(gh, 7, changed, put, SOURCE, stub)
        self.assertIsNotNone(second)
        self.assertEqual(len(self.previews_of(gh)), 2)
        self.assertEqual([e["op"] for e in gh_log(gh)], ["post", "post"])

    def test_incomplete_then_complete(self):
        gh, stub = self.recorded(), StubPreviews()
        items = self.items({"panel.png": png()})
        failing = Put("the media store did not take a preview: store unreachable")
        first = handoff.post(gh, 7, items, failing, SOURCE, stub)
        self.assertIn("Preview not available: the media store did not take a preview: store unreachable",
                      first["body"])
        self.assertIsNone(handoff.post(gh, 7, items, failing, SOURCE, stub))
        self.assertEqual(len(self.previews_of(gh)), 1)
        second = handoff.post(gh, 7, items, Put(), SOURCE, stub)
        self.assertNotIn("Preview not available", second["body"])
        put = Put()
        self.assertIsNone(handoff.post(gh, 7, items, put, SOURCE, stub))
        self.assertEqual(put.calls, [])
        self.assertEqual(len(self.previews_of(gh)), 2)


def gh_log(gh):
    path = gh.dir / "log.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class Bounds(Case):
    def test_thirty_pictures(self):
        files = {f"panel-{n:02d}.png": png(shade=n) for n in range(30)}
        body, complete, put = self.build(files)
        self.assertTrue(complete)
        self.assertEqual(len(put.calls), 12)
        self.assertEqual([name for name, _, _ in put.calls], [f"panel-{n:02d}.png" for n in range(12)])
        self.assertEqual(body.count("\n### "), 12)
        self.assertIn("\n18 more file(s) are not shown.\n", body)
        key = body.rstrip("\n").split("\n")[-1]
        self.assertEqual(key, "<!-- preview-of " + " ".join(sorted(sha(d) for d in files.values())) + " -->")

    def test_large_packages(self):
        big = package(3000, extra={n: ["z" * 330] * 3 for n in range(1, 3001)})
        self.assertGreater(len(big.encode()), 1024 * 1024 - 1)
        items = [Item(f"pkg-{n}.md", sha(f"{n}{big}".encode()), len(big), None, big) for n in range(6)]
        body, complete = handoff.build(items, Put(), SOURCE, StubPreviews())
        checked(self, body, self.tmp)
        self.assertTrue(complete)
        many = [Item(f"pkg-{n:02d}.md", sha(f"{n}".encode()), len(big), None, big) for n in range(24)]
        body, _ = handoff.build(many, Put(), SOURCE, StubPreviews())
        checked(self, body, self.tmp)
        shown = body.count("\n### ")
        self.assertLess(shown, 24)
        self.assertIn(f"\n{24 - shown} more file(s) are not shown.\n", body)
        gh = self.recorded()
        posted = handoff.post(gh, 7, many, Put(), SOURCE, StubPreviews())
        self.assertLessEqual(len(github._sent(posted["body"], ROLE)), github.BODY_MAX)

    def test_three_small_items(self):
        body = self.build({"a.png": png(), "b.md": "# B\n\nbee\n", "c.glb": box()})[0]
        self.assertEqual(body.count("\n### "), 3)
        self.assertNotIn("more file(s)", body)


class Nothing(Case):
    def test_only_records(self):
        gh, put = self.recorded(), Put()
        items = self.items({"state.json": '{"a": 1}\n', "post.json": "{}\n", "render.json": "[]\n"})
        self.assertEqual(len(items), 3)
        self.assertIsNone(handoff.post(gh, 7, items, put, SOURCE, StubPreviews()))
        self.assertEqual(put.calls, [])
        self.assertFalse((gh.dir / "comments").exists())

    def test_text_needs_no_store(self):
        gh, put = self.recorded(), Put("the register has no resource store.media")
        items = self.items({"package.md": package(2), "state.json": "{}\n"})
        posted = handoff.post(gh, 7, items, put, SOURCE, StubPreviews())
        checked(self, posted["body"], self.tmp)
        self.assertNotIn("Preview not available", posted["body"])
        self.assertNotIn("![", posted["body"])
        self.assertEqual(put.calls, [])
        self.assertIn(f"<!-- preview-of {sha(package(2).encode())} -->", posted["body"])


class Items(Case):
    NAME = __import__("re").compile(r"^[A-Za-z0-9._-][A-Za-z0-9._/-]*$")

    def git(self, repo, *args):
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        return subprocess.run(["git", "-C", str(repo), "-c", "user.name=tester", "-c",
                               "user.email=tester@example.invalid", *args],
                              check=True, capture_output=True, env=env).stdout

    def test_of_job(self):
        data, text = png(), "# Package\n"
        (self.out / "a-directory").mkdir()
        rows = [{"name": "panel.png"}, {"name": "package.md"}, {"name": "a-directory"}, {"name": "gone.txt"}]
        self.write("panel.png", data)
        self.write("package.md", text)
        found = handoff.of_job(self.out, rows)
        self.assertEqual(found, [Item("package.md", sha(text.encode()), len(text), str(self.out / "package.md"), None),
                                 Item("panel.png", sha(data), len(data), str(self.out / "panel.png"), None)])
        self.assertTrue(all(self.NAME.match(item.name) for item in found))

    def test_of_branch(self):
        repo = self.tmp / "repo"
        repo.mkdir()
        self.git(repo, "init", "-q", "-b", "trunk")
        (repo / "README.md").write_text("# The production\n")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", "base")
        self.git(repo, "checkout", "-q", "-b", "feat/ep02")
        outline = "# Outline\n\nThe heron leaves.\n"
        (repo / "episodes" / "ep02").mkdir(parents=True)
        (repo / "episodes" / "ep02" / "outline.md").write_text(outline)
        (repo / "notes.txt").write_text("not markdown\n")
        (repo / "big.md").write_text("b" * (2 * 1024 * 1024))
        (repo / "latin.md").write_bytes(b"# caf\xe9\n")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", "work")
        self.git(repo, "checkout", "-q", "trunk")
        (repo / "episodes" / "ep02").mkdir(parents=True)
        (repo / "episodes" / "ep02" / "outline.md").write_text("# The working tree, not the branch\n")
        found = handoff.of_branch(repo, "trunk", "feat/ep02")
        self.assertEqual(found, [Item("episodes/ep02/outline.md", sha(outline.encode()), len(outline), None,
                                      outline)])
        self.assertTrue(all(self.NAME.match(item.name) for item in found))
        self.assertFalse(found[0].name.startswith("/"))


class Uploader(Case):
    def setUp(self):
        super().setUp()
        from test_media import KEY_ID, REGION, SECRET, FakeStore
        self.secret_words = (KEY_ID, SECRET)
        self.store = FakeStore(self)
        self.root = self.tmp / "show"
        self.root.mkdir()
        self.secrets = self.tmp / "secrets"
        self.secrets.mkdir()
        fixture = ROOT / "tests" / "fixtures" / "run" / "resources.json"
        self.without = fixture
        register = json.loads(fixture.read_text())
        shipped = {r["id"]: r for r in json.loads((ROOT / "kit" / "resources.json").read_text())["resources"]}
        register["resources"].append(shipped["store.media"])
        self.register = self.tmp / "resources.json"
        self.register.write_text(json.dumps(register))
        self.patch(self.register)
        key = self.secrets / "store.media"
        key.write_text(f"{KEY_ID}\n{SECRET}\n{REGION}\n")
        key.chmod(0o600)
        (self.root / "production.local.env").write_text(
            f"SECRETS_DIR={self.secrets}\nMEDIA_STORE={self.store.url('/b')}\nMEDIA_PUBLIC_BASE={self.store.url('/pub')}\n")
        self.p = types.SimpleNamespace(root=self.root, settings={})
        self.file = self.write("panel.png", png())

    def patch(self, register):
        patch = mock.patch.object(resources, "REGISTER", register)
        patch.start()
        self.addCleanup(patch.stop)

    def refused(self, put):
        with self.assertRaises(HandoffError) as raised:
            put(str(self.file))
        text = str(raised.exception)
        for word in (str(self.secrets), str(self.tmp), *self.secret_words):
            self.assertNotIn(word, text)
        return text

    def test_puts_then_heads(self):
        with mock.patch.object(resources, "load", wraps=resources.load) as load:
            put = handoff.uploader(self.p)
            self.assertEqual((load.call_count, self.store.requests), (0, []))
            link = put(str(self.file))
            self.assertEqual(load.call_count, 1)
        digest = sha(self.file.read_bytes())
        self.assertEqual(link, self.store.url("/pub") + "/" + digest)
        self.assertEqual([(r["method"], r["path"]) for r in self.store.requests if r["method"] != "GET"],
                         [("HEAD", f"/b/{digest}"), ("PUT", f"/b/{digest}")])
        self.store.requests.clear()
        self.assertEqual(put(str(self.file)), link)
        self.assertEqual([r["method"] for r in self.store.requests if r["method"] != "GET"], ["HEAD"])

    def test_seeded_no_media_store_in_the_register(self):
        self.patch(self.without)
        self.assertEqual(self.refused(handoff.uploader(self.p)), "the register has no resource store.media")
        self.assertEqual(self.store.requests, [])

    def test_seeded_no_key_file(self):
        (self.secrets / "store.media").unlink()
        self.assertEqual(self.refused(handoff.uploader(self.p)),
                         "resource store.media is not usable: secret store.media: missing")
        self.assertEqual(self.store.requests, [])

    def test_seeded_the_store_answers_500(self):
        self.store.status["PUT"] = (500, b"")
        self.assertEqual(self.refused(handoff.uploader(self.p)),
                         "the media store did not take a preview: store answered 500")


class RunHook(unittest.TestCase):
    """Criterion 12: `factory run` on a job whose outputs are a picture and a package."""

    PANEL = "GIF89a an invented panel\n"
    PACKAGE = "# The package\n\nAn invented lead.\n\n## First part\n\nA line.\n\n## Second part\n\nAnother.\n"

    def setUp(self):
        import test_run
        from test_media import KEY_ID, REGION, SECRET, FakeStore
        self.t = test_run
        self.w = w = test_run.World(self)
        self.store = FakeStore(self)
        register = json.loads((test_run.FIX / "resources.json").read_text())
        shipped = {r["id"]: r for r in json.loads((ROOT / "kit" / "resources.json").read_text())["resources"]}
        register["resources"].append(shipped["store.media"])
        path = w.tmp / "resources.json"
        path.write_text(json.dumps(register, indent=2) + "\n")
        patch = mock.patch.object(resources, "REGISTER", path)
        patch.start()
        self.addCleanup(patch.stop)
        key = w.secrets / "store.media"
        key.write_text(f"{KEY_ID}\n{SECRET}\n{REGION}\n")
        key.chmod(0o600)
        w.local(STATE_DIR=w.state, SECRETS_DIR=w.secrets, FAKE_LOG=w.fake_log, MEDIA_STORE=self.store.url("/b"),
                MEDIA_PUBLIC_BASE=self.store.url("/pub"))
        self.job = w.script("hook", outputs={"panel.gif": self.PANEL, "package.md": self.PACKAGE}, usd=1.5,
                            compute_min=20)
        self.digest = w.passed(self.job)
        self.done = f"run job {self.digest}: DONE usd 1.5 compute_min 20\n"

    def previews(self):
        path = self.w.dir / "comments" / f"{self.t.PULL}.json"
        return [c for c in json.loads(path.read_text()) if c["role"] == ROLE] if path.is_file() else []

    def test_one_comment_on_the_pull(self):
        w = self.w
        self.assertEqual(w.run(self.job), (0, self.done, ""))
        [comment] = self.previews()
        body = comment["body"]
        self.assertEqual(body.split("\n", 1)[0], HEADING)
        panel = self.PANEL.encode()
        self.assertIn(f"![panel.gif]({self.store.url('/pub')}/{sha(panel)})", body)
        for heading in ("# The package", "## First part", "## Second part"):
            self.assertIn("\n" + heading + "\n", body)
        self.assertEqual(self.store.objects, {sha(panel): panel})
        handoff.guard(body, forbidden(w.tmp) + (str(w.root), str(w.state), str(w.secrets)))
        log = self.t.World.log(w)
        posted = [n for n, e in enumerate(log) if e["op"] == "post" and e.get("role") == ROLE]
        trunk = [n for n, e in enumerate(log) if e["op"] == "push" and e.get("ref") == "trunk"]
        self.assertEqual(len(posted), 1)
        self.assertLess(posted[0], trunk[-1])
        self.assertEqual(w.run(self.job), (0, self.done.rstrip("\n") + " (already recorded)\n", ""))
        self.assertEqual(len(self.previews()), 1)

    def test_a_failed_post_changes_nothing_of_the_run(self):
        w = self.w
        self.assertEqual(w.run(self.job, gh=self.t.FailingPost(w.dir)),
                         (0, self.done, f"run: preview not posted: post #{self.t.PULL}: exit 1\n"))
        self.assertEqual([(r["hash"], r["verdict"]) for r in w.rows("run")], [(self.digest, "DONE")])
        self.assertEqual(self.previews(), [])

    def test_the_store_refuses_the_picture(self):
        w = self.w
        self.store.status["PUT"] = (500, b"")
        self.assertEqual(w.run(self.job),
                         (2, "", "run: the media store did not take panel.gif: store answered 500\n"))
        self.assertEqual(self.previews(), [])

    def test_the_earlier_call_posts_once(self):
        w = self.w
        code, _, err = w.run(self.job, gh=self.t.FlakyPush(w.dir, {1}))
        self.assertEqual((code, err), (2, "run: push: exit 1\n"))
        self.assertEqual(self.previews(), [])
        self.assertEqual(w.run(self.job), (0, self.done, ""))
        self.assertEqual(len(self.previews()), 1)
        self.assertEqual(w.run(self.job)[0], 0)
        self.assertEqual(len(self.previews()), 1)
        self.assertEqual(w.fake_calls(), [self.digest])


class NoPreviewPost(github.Recorded):
    def post(self, number, body, role):
        if role == ROLE:
            raise github.GitHubError(f"post #{number}: exit 1")
        return super().post(number, body, role)


class MakerHook(unittest.TestCase):
    """Criterion 13: `finish.post` after a writer's READY and ADDRESSED."""

    OUTLINE = "Episode two, told short.\n\n# Outline of episode two\n\nThe heron leaves.\n"

    def setUp(self):
        import test_finish
        self.t = test_finish
        self.w = test_finish.World(self)

    def ready(self, text=None):
        return self.t.ready(files={"episodes/ep02/outline.md": text or self.OUTLINE})

    def addressed(self, text):
        return self.t.result_step(self.t.WRITER, {
            "marker": "ADDRESSED", "summary": "The heron leaves at dawn now.", "evidence": self.t.EVIDENCE,
            "addressed": [{"note": "B1", "by": "critic", "action": "changed", "text": "the time of day"}]},
            {"episodes/ep02/outline.md": text})

    def test_ready_then_addressed(self):
        w = self.w
        w.steps(self.ready())
        self.assertEqual(w.launch("writer", "3"), (0, "launched writer 3-1\nposted [WRITER] READY for launch 3-1\n", ""))
        comments = w.comments(6)
        self.assertEqual([c["role"] for c in comments], ["writer", ROLE])
        self.assertTrue(comments[0]["body"].startswith("[WRITER] READY\n"))
        self.assertTrue(comments[0]["body"].endswith(f"Launch: 3-1\nhead {w.head('outline/OL-2-writer-1')} "
                                                     "manifest none\n"))
        body = comments[1]["body"]
        self.assertEqual(body.split("\n", 1)[0], HEADING)
        self.assertIsNone(markers.parse(body))
        for line in ("### `episodes/ep02/outline.md`", "Episode two, told short.", "# Outline of episode two",
                     "The heron leaves."):
            self.assertIn(line, body.split("\n"))
        handoff.guard(body, forbidden(w.tmp) + (str(w.root), str(w.secrets), str(w.state)))
        self.assertEqual(w.by(3, ROLE), [])
        w.steps(self.addressed(self.OUTLINE))
        self.assertEqual(w.launch("writer", "6")[0], 0)
        self.assertEqual(len(w.by(6, ROLE)), 1)
        self.assertEqual(w.by(6, "writer")[-1]["body"].split("\n", 1)[0], "[WRITER] ADDRESSED")
        w.steps(self.addressed(self.OUTLINE + "\n## The dawn\n\nIt is dawn.\n"))
        self.assertEqual(w.launch("writer", "6")[0], 0)
        previews = w.by(6, ROLE)
        self.assertEqual(len(previews), 2)
        self.assertIn("## The dawn", previews[1]["body"].split("\n"))

    def test_blocked_posts_none(self):
        w = self.w
        w.steps(self.t.result_step(self.t.WRITER, {
            "marker": "BLOCKED", "summary": "Cannot place the heron.",
            "blocked": {"missing": "the heron's route", "tried": "the map", "resume_from": "nothing yet"}},
            {"episodes/ep02/outline.md": "half\n"}))
        self.assertEqual(w.launch("writer", "3")[0], 3)
        self.assertEqual(w.posted(ROLE), [])

    def test_a_failed_preview_post_is_logged(self):
        w = self.w
        w.steps(self.ready())
        with mock.patch.object(github, "connect", return_value=NoPreviewPost(w.github)):
            self.assertEqual(w.launch("writer", "3"),
                             (0, "launched writer 3-1\nposted [WRITER] READY for launch 3-1\n", ""))
        self.assertEqual([c["role"] for c in w.comments(6)], ["writer"])
        self.assertEqual((w.run_dir("writer-3-1") / "supervisor.log").read_text(),
                         "launch 3-1: preview not posted: post #6: exit 1\n")


class Integration(Case):
    """Criterion 14: the real previewer once it is on the base; a previewer that cannot be imported, always."""

    @unittest.skipUnless(importlib.util.find_spec("kit.lib.preview"), "needs kit.lib.preview")
    def test_a_box_through_the_real_previewer(self):
        seen = []

        def put(path):
            seen.append(Path(path).read_bytes())
            return LINK + sha(seen[-1])

        items = self.items({"model.glb": box()})
        body, _ = handoff.build(items, put, SOURCE, forbid=forbidden(self.tmp))
        checked(self, body, self.tmp)
        [sheet] = seen
        self.assertTrue(sheet.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(png_size(sheet), (1024, 1024))
        self.assertIn(f"![model.glb, 4 views]({LINK}{sha(sheet)})", body.split("\n"))

    def test_not_installed(self):
        gh = self.recorded(4)
        self.write("panel.png", png())
        p = types.SimpleNamespace(root=self.tmp, settings={})
        with mock.patch.dict(sys.modules, {"kit.lib.preview": None}):
            message = handoff.after_job(p, gh, 4, "0" * 64, "fake", self.out, [{"name": "panel.png"}])
        self.assertEqual(message, "kit.lib.preview is not installed")
        self.assertFalse((gh.dir / "comments").exists())

    def test_no_hard_import(self):
        text = (ROOT / "kit" / "lib" / "handoff.py").read_text()
        self.assertNotIn("from kit.lib import preview", text)
        self.assertNotIn("import kit.lib.preview", text)


class AfterHooks(Case):
    """Ruling 7: the hooks catch everything and return a fixed text."""

    def test_an_unexpected_error_is_its_type(self):
        gh = self.recorded(4)
        p = types.SimpleNamespace(root=self.tmp, settings={})
        with mock.patch.object(handoff, "post", side_effect=KeyError("a value")):
            self.assertEqual(handoff.after_job(p, gh, 4, "0" * 64, "fake", self.out, []), "KeyError")
        with mock.patch.object(handoff, "of_branch", side_effect=github.GitHubError("post #4: exit 1")):
            self.assertEqual(handoff.after_maker(p, gh, {"base_ref": "trunk", "branch": "b", "role": "writer"}, 4),
                             "post #4: exit 1")


class NoPaths(Case):
    """Criterion 15: no body any scenario builds holds a machine path; nor the one `factory run` posts."""

    def test_every_scenario(self):
        for scenario in (picture_body, glb_body, video_body, text_body, failed_body):
            with self.subTest(scenario=scenario.__name__):
                body = scenario(self)
                handoff.guard(body, forbidden(self.tmp) + (str(self.out),))
                self.assertNotIn(str(self.out), body)

    def test_the_posted_comment_read_back(self):
        hook = RunHook("test_one_comment_on_the_pull")
        hook.setUp()
        try:
            w = hook.w
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(w.run(hook.job)[0], 0)
            stored = json.loads((w.dir / "comments" / f"{hook.t.PULL}.json").read_text())
            [body] = [c["body"] for c in stored if c["role"] == ROLE]
            handoff.guard(body, forbidden(w.tmp) + (str(w.root), str(w.state), str(w.secrets)))
        finally:
            hook.doCleanups()


if __name__ == "__main__":
    unittest.main()
