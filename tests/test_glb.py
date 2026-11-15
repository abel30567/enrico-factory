"""Tests for kit/lib/glb.py: a GLB read with the standard library, and its summary.

Every GLB is built here by `make_glb`; no binary file is committed.
"""

import json
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import glb  # noqa: E402


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


def png(width, height):
    return glb.PNG + struct.pack(">I", 13) + b"IHDR" + struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)


def jpeg(width, height):
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    dht = b"\xff\xc4" + struct.pack(">H", 4) + b"\x00\x00"
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + dht + sof + b"\xff\xd9"


class Builder:
    """A glTF document and its binary chunk, grown one buffer view at a time."""

    def __init__(self):
        self.binary = b""
        self.doc = {"asset": {"version": "2.0"}, "buffers": [], "bufferViews": [], "accessors": [],
                    "meshes": [], "nodes": []}

    def view(self, data, **extra):
        self.binary += b"\x00" * (-len(self.binary) % 4)
        self.doc["bufferViews"].append({"buffer": 0, "byteOffset": len(self.binary), "byteLength": len(data),
                                        **extra})
        self.binary += data
        return len(self.doc["bufferViews"]) - 1

    def accessor(self, **fields):
        self.doc["accessors"].append(fields)
        return len(self.doc["accessors"]) - 1

    def position(self, count, low=0.0, high=1.7):
        data = struct.pack(f"<{3 * count}f", *([0.0] * 3 * count))
        return self.accessor(bufferView=self.view(data), componentType=5126, count=count, type="VEC3",
                             min=[0.0, low, 0.0], max=[1.0, high, 0.0])

    def weights(self, rows, component=5126, stride=None):
        form = {5126: "f", 5121: "B", 5123: "H"}[component]
        data = b""
        for row in rows:
            data += struct.pack(f"<4{form}", *row)
            if stride:
                data += b"\x00" * (stride - struct.calcsize(f"<4{form}"))
        extra = {"byteStride": stride} if stride else {}
        return self.accessor(bufferView=self.view(data, **extra), componentType=component, count=len(rows),
                             type="VEC4")

    def mesh(self, *primitives):
        self.doc["meshes"].append({"primitives": list(primitives)})
        return len(self.doc["meshes"]) - 1

    def image(self, data, name=None):
        image = {"bufferView": self.view(data), "mimeType": "image/png"}
        if name is not None:
            image["name"] = name
        self.doc.setdefault("images", []).append(image)

    def skin(self, *names):
        """Nodes ``names``, each the parent of the next, and a skin of them all."""
        first = len(self.doc["nodes"])
        for n, name in enumerate(names):
            node = {"name": name}
            if n + 1 < len(names):
                node["children"] = [first + n + 1]
            self.doc["nodes"].append(node)
        self.doc["skins"] = [{"joints": list(range(first, first + len(names)))}]

    def glb(self):
        doc = dict(self.doc, buffers=[{"byteLength": len(self.binary)}] if self.binary else [])
        return make_glb(doc, self.binary)


def triangle(builder=None):
    b = builder or Builder()
    b.mesh({"attributes": {"POSITION": b.position(3)}})
    return b


class ReadTest(unittest.TestCase):
    def test_reads_the_document_and_the_binary(self):
        b = triangle()
        document, binary = glb.read(b.glb())
        self.assertEqual(document["meshes"], b.doc["meshes"])
        self.assertEqual(binary[:len(b.binary)], b.binary)
        self.assertEqual(glb.read(make_glb({"meshes": []})), ({"meshes": []}, b""))

    def test_broken_files(self):
        good = triangle().glb()
        wrong_length = good[:8] + struct.pack("<I", len(good) + 4) + good[12:]
        cases = {
            "bad magic": b"glTX" + good[4:],
            "version 1": good[:4] + struct.pack("<I", 1) + good[8:],
            "wrong length": wrong_length,
            "short": good[:10],
            "first chunk not JSON": good[:16] + struct.pack("<I", glb.BIN_CHUNK) + good[20:],
            "JSON array": make_glb([{"meshes": []}]),
            "not UTF-8 JSON": make_glb(b"\xff\xfe{}"),
            "not JSON": make_glb(b"{x"),
            "second chunk not binary": good[:-len(triangle().binary) - 4] + struct.pack("<I", glb.JSON_CHUNK)
            + good[-len(triangle().binary):],
        }
        for name, data in cases.items():
            with self.subTest(name):
                with self.assertRaises(glb.Broken):
                    glb.read(data)
                with self.assertRaises(glb.Broken):
                    glb.summary(data)


class SummaryTest(unittest.TestCase):
    def test_a_triangle(self):
        self.assertEqual(glb.summary(triangle().glb()),
                         {"triangles": 1, "materials": 0, "textures": [], "height_m": 1.7, "skeleton": None})

    def test_triangles_by_mode(self):
        b = Builder()
        indices = b.accessor(bufferView=b.view(b"\x00" * 24), componentType=5123, count=12, type="SCALAR")
        b.mesh({"attributes": {"POSITION": b.position(12)}, "indices": indices},
               {"attributes": {"POSITION": b.position(7)}, "mode": 5},
               {"attributes": {"POSITION": b.position(9)}, "mode": 0},
               {"attributes": {"POSITION": b.position(5)}, "mode": 6},
               {"attributes": {"POSITION": b.position(1)}, "mode": 5})
        self.assertEqual(glb.summary(b.glb())["triangles"], 4 + 5 + 0 + 3 + 0)

    def test_a_mesh_used_by_two_nodes_counts_once(self):
        b = triangle()
        b.doc["nodes"] = [{"mesh": 0}, {"mesh": 0, "translation": [2, 0, 0]}]
        self.assertEqual(glb.summary(b.glb())["triangles"], 1)
        b.mesh({"attributes": {"POSITION": b.position(6)}})
        self.assertEqual(glb.summary(b.glb())["triangles"], 3)

    def test_materials_and_textures(self):
        b = triangle()
        b.doc["materials"] = [{"name": "body"}, {"name": "lamp"}]
        b.image(png(2048, 1024), "body")
        b.image(jpeg(640, 480))
        summary = glb.summary(b.glb())
        self.assertEqual(summary["materials"], 2)
        self.assertEqual(summary["textures"], [{"name": "body", "width": 2048, "height": 1024},
                                               {"name": "image1", "width": 640, "height": 480}])

    def test_height(self):
        b = Builder()
        b.mesh({"attributes": {"POSITION": b.position(3, -0.25, 1.0)}},
               {"attributes": {"POSITION": b.position(3, 0.5, 1.512345)}})
        self.assertEqual(glb.summary(b.glb())["height_m"], 1.7623)

    def test_skeleton(self):
        b = Builder()
        weights = b.weights([(0.5, 0.5, 0, 0), (1, 0, 0, 0), (0.25, 0.25, 0.25, 0.255)])
        b.mesh({"attributes": {"POSITION": b.position(3), "WEIGHTS_0": weights}})
        b.doc["nodes"] = [{"name": "scene", "children": [1]}, {"children": [2]}]
        b.skin("spine", "head")
        b.doc["skins"] = [{"joints": [1, 2, 3]}]
        self.assertEqual(glb.summary(b.glb())["skeleton"], {
            "bones": [{"name": "node1", "parent": None}, {"name": "spine", "parent": "node1"},
                      {"name": "head", "parent": "spine"}],
            "weights_normalised": True})

    def test_nearest_joint_ancestor(self):
        b = triangle()
        b.doc["nodes"] = [{"name": "hips", "children": [1]}, {"name": "helper", "children": [2]}, {"name": "toe"}]
        b.doc["skins"] = [{"joints": [2, 0]}]
        self.assertEqual(glb.summary(b.glb())["skeleton"]["bones"],
                         [{"name": "toe", "parent": "hips"}, {"name": "hips", "parent": None}])

    def skinned(self, *sets, component=5126, stride=None):
        b = Builder()
        attributes = {"POSITION": b.position(2)}
        for n, rows in enumerate(sets):
            attributes[f"WEIGHTS_{n}"] = b.weights(rows, component, stride)
        b.mesh({"attributes": attributes})
        b.skin("root")
        return b

    def normalised(self, *sets, **options):
        return glb.summary(self.skinned(*sets, **options).glb())["skeleton"]["weights_normalised"]

    def test_weights(self):
        self.assertTrue(self.normalised([(255, 0, 0, 0), (100, 100, 55, 0)], component=5121))
        self.assertTrue(self.normalised([(65535, 0, 0, 0), (0, 0, 0, 65535)], component=5123))
        self.assertTrue(self.normalised([(1, 0, 0, 0), (0.5, 0.5, 0, 0)], stride=20))
        self.assertTrue(self.normalised([(0.5, 0, 0, 0), (0.5, 0, 0, 0)], [(0.5, 0, 0, 0), (0.25, 0.25, 0, 0)]))
        self.assertTrue(self.normalised([(0.995, 0, 0, 0), (1.005, 0, 0, 0)]))
        self.assertFalse(self.normalised([(1, 0, 0, 0), (0.9, 0, 0, 0)]))
        self.assertFalse(self.normalised([(0.5, 0, 0, 0), (0.5, 0, 0, 0)]))
        self.assertFalse(self.normalised([(254, 0, 0, 0), (200, 0, 0, 0)], component=5121))

    def test_a_skin_without_weights(self):
        b = triangle()
        b.skin("root", "spine")
        self.assertEqual(glb.summary(b.glb())["skeleton"],
                         {"bones": [{"name": "root", "parent": None}, {"name": "spine", "parent": "root"}],
                          "weights_normalised": False})

    def test_no_skin(self):
        b = triangle()
        b.doc["skins"] = []
        self.assertIsNone(glb.summary(b.glb())["skeleton"])

    def test_broken_summaries(self):
        def change(edit):
            b = self.skinned([(1, 0, 0, 0), (1, 0, 0, 0)])
            b.image(png(16, 16))
            edit(b)
            return b.glb()

        weights = 1  # the accessor of WEIGHTS_0
        cases = {
            "no meshes": lambda b: b.doc.pop("meshes"),
            "empty meshes": lambda b: b.doc.__setitem__("meshes", []),
            "sparse weights": lambda b: b.doc["accessors"][weights].__setitem__("sparse", {"count": 1}),
            "weights VEC3": lambda b: b.doc["accessors"][weights].__setitem__("type", "VEC3"),
            "weights of int": lambda b: b.doc["accessors"][weights].__setitem__("componentType", 5125),
            "WebP image": lambda b: b.image(b"RIFF\x00\x00\x00\x00WEBPVP8 "),
            "image without bufferView": lambda b: b.doc["images"].append({"uri": "body.png"}),
            "view outside the chunk": lambda b: b.doc["bufferViews"][1].__setitem__("byteLength", 10 ** 6),
            "view of another buffer": lambda b: b.doc["bufferViews"][1].__setitem__("buffer", 1),
            "accessor without view": lambda b: b.doc["accessors"][weights].pop("bufferView"),
            "POSITION without min": lambda b: b.doc["accessors"][0].pop("min"),
            "POSITION without max": lambda b: b.doc["accessors"][0].pop("max"),
            "no POSITION": lambda b: b.doc["meshes"][0]["primitives"][0]["attributes"].pop("POSITION"),
            "flat": lambda b: b.doc["accessors"][0].__setitem__("max", [1.0, 0.0, 0.0]),
            "negative index": lambda b: b.doc["meshes"][0]["primitives"][0]["attributes"].__setitem__("POSITION", -1),
            "count not whole": lambda b: b.doc["accessors"][0].__setitem__("count", 2.5),
            "joint outside nodes": lambda b: b.doc["skins"][0]["joints"].append(9),
            "a cycle": lambda b: b.doc["nodes"].extend([{"children": [2]}, {"children": [1, 3]}, {}]) or
            b.doc["skins"][0]["joints"].append(3),
        }
        for name, edit in cases.items():
            with self.subTest(name):
                with self.assertRaises(glb.Broken):
                    glb.summary(change(edit))
        glb.summary(change(lambda b: None))


if __name__ == "__main__":
    unittest.main()
