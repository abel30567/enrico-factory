"""Read a GLB, the binary form of glTF 2.0, with the standard library: its document, its binary chunk and
the summary an asset's state records.

`read(data)` checks the header (`glTF`, version 2, the length of the file), a first chunk of JSON holding
an object and a second chunk, if any, of binary data. `summary(data)` gives `{triangles, materials, textures,
height_m, skeleton}`:

- `triangles`: per primitive of each mesh, the `count` of its `indices`, else of its `POSITION`; mode 4 (the
  default) adds `count // 3`, modes 5 and 6 `max(count - 2, 0)`, modes 0 to 3 nothing. A mesh counts once.
- `materials`: the number of materials.
- `textures`: `{name, width, height}` per image, in order, its size read in its buffer view from a PNG `IHDR`
  or a JPEG `SOF` marker. `name` is the image's name, else `image<index>`.
- `height_m`: the highest `max[1]` less the lowest `min[1]` of the `POSITION` accessors, rounded to 4
  places, no node transform (glTF is Y up, in metres). It must be above 0.
- `skeleton`: null without a skin, else the joints of the first skin, `{name, parent}` each in order, the
  parent the nearest ancestor that is a joint, and `weights_normalised`: some primitive has `WEIGHTS_0`, and
  in each such primitive every vertex's weights, summed over its `WEIGHTS_<n>`, are within 0.01 of 1.

Anything else, or a file that does not read, raises `Broken`. `height(data)` is `height_m` before it is
rounded.

`scale(path, factor)` scales the GLB at ``path`` uniformly by ``factor`` in place: the float32 VEC3 data of
every `POSITION` accessor (of each primitive and of its morph targets) in the binary chunk, at its
`byteOffset` with its view's `byteStride`, that accessor's `min` and `max` (as float32), and every node
`translation`. The file is written again with the JSON chunk padded with spaces and the binary chunk with
zeros to 4 bytes, and the lengths of the header and of each chunk. It raises `ValueError`, writing nothing,
for a sparse `POSITION` accessor, one not FLOAT VEC3 in a buffer view, a `KHR_draco_mesh_compression` or
`EXT_meshopt_compression` extension, a skin, or a file that does not read.
"""

import json
import re
import struct

MAGIC = b"glTF"
VERSION = 2
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942
PNG = b"\x89PNG\r\n\x1a\n"
JPEG_SOF = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
TRIANGLES, STRIPS, FANS = 4, 5, 6
WEIGHTS = re.compile(r"WEIGHTS_[0-9]+")
# componentType: (struct format of one VEC4, the value of 1).
WEIGHT_TYPES = {5126: ("<4f", 1.0), 5121: ("<4B", 255.0), 5123: ("<4H", 65535.0)}
WEIGHT_TOLERANCE = 0.01
FLOAT = 5126
COMPRESSIONS = ("KHR_draco_mesh_compression", "EXT_meshopt_compression")


class Broken(Exception):
    """The data is not a GLB the kit can read."""


def _guarded(function, data):
    try:
        return function(data)
    except (KeyError, IndexError, TypeError, ValueError, struct.error):
        raise Broken() from None


def read(data):
    """``(document, binary)`` of a GLB; ``binary`` is ``b""`` without a binary chunk."""
    return _guarded(_read, data)


def summary(data):
    """``{triangles, materials, textures, height_m, skeleton}`` of a GLB."""
    return _guarded(_summary, data)


def height(data):
    """The height of a GLB as `summary` gives it, not rounded."""
    return _guarded(_raw_height, data)


def scale(path, factor):
    """Scale the GLB at ``path`` uniformly by ``factor``; ``ValueError`` when it cannot be."""
    with open(path, "rb") as handle:
        data = handle.read()
    try:
        scaled = _scale(data, factor)
    except (AttributeError, KeyError, IndexError, TypeError, struct.error) as error:
        raise ValueError("not scalable") from error
    with open(path, "wb") as handle:
        handle.write(scaled)


def _float32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _scale(data, factor):
    document, binary = _read(data)
    if _list(document, "skins"):
        raise ValueError("a skin")
    named = {*_list(document, "extensionsUsed"), *_list(document, "extensionsRequired")}
    named |= {name for owner in [*_primitives(document), *_list(document, "bufferViews")]
              for name in owner.get("extensions", {})}
    if named & set(COMPRESSIONS):
        raise ValueError("compressed")
    accessors, views = _list(document, "accessors"), _list(document, "bufferViews")
    positions = set()
    for primitive in _primitives(document):
        for attributes in [primitive["attributes"], *_list(primitive, "targets")]:
            if "POSITION" in attributes:
                positions.add(_whole(attributes["POSITION"]))
    binary, done = bytearray(binary), set()
    for index in sorted(positions):
        accessor = _at(accessors, index)
        if "sparse" in accessor or accessor["componentType"] != FLOAT or accessor["type"] != "VEC3":
            raise ValueError("POSITION is not FLOAT VEC3")
        view = _at(views, accessor["bufferView"])
        _view(document, binary, accessor["bufferView"])
        stride = _whole(view.get("byteStride") or 12)
        offset = _whole(view.get("byteOffset", 0))
        start, count = offset + _whole(accessor.get("byteOffset", 0)), _whole(accessor["count"])
        if stride < 12 or (count and start + (count - 1) * stride + 12 > offset + view["byteLength"]):
            raise ValueError("POSITION outside its buffer view")
        for at in range(start, start + count * stride, stride):
            if at not in done:
                done.add(at)
                struct.pack_into("<3f", binary, at, *(v * factor for v in struct.unpack_from("<3f", binary, at)))
        for key in ("min", "max"):
            if key in accessor:
                accessor[key] = [_float32(_number(v) * factor) for v in accessor[key]]
    for node in _list(document, "nodes"):
        if "translation" in node:
            node["translation"] = [_number(v) * factor for v in node["translation"]]
    text = json.dumps(document, separators=(",", ":")).encode("utf-8")
    chunks = [(JSON_CHUNK, text + b" " * (-len(text) % 4))]
    if binary:
        chunks.append((BIN_CHUNK, bytes(binary) + b"\x00" * (-len(binary) % 4)))
    body = b"".join(struct.pack("<II", len(chunk), kind) + chunk for kind, chunk in chunks)
    return struct.pack("<4sII", MAGIC, VERSION, 12 + len(body)) + body


def _read(data):
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    if magic != MAGIC or version != VERSION or length != len(data):
        raise ValueError("header")
    size, kind = struct.unpack_from("<II", data, 12)
    if kind != JSON_CHUNK or 20 + size > len(data):
        raise ValueError("JSON chunk")
    document = json.loads(data[20:20 + size].decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError("document")
    at, binary = 20 + size, b""
    if at < len(data):
        size, kind = struct.unpack_from("<II", data, at)
        if kind != BIN_CHUNK or at + 8 + size > len(data):
            raise ValueError("binary chunk")
        binary = data[at + 8:at + 8 + size]
    return document, binary


def _whole(value):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("not a whole number")
    return value


def _number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value != value:
        raise ValueError("not a number")
    return value


def _list(owner, key):
    value = owner.get(key, [])
    if not isinstance(value, list):
        raise TypeError(key)
    return value


def _at(items, index):
    return items[_whole(index)]


def _view(document, binary, index):
    """The bytes of buffer view ``index``, which must lie in the binary chunk; ``(bytes, view)``."""
    view = _at(_list(document, "bufferViews"), index)
    start, length = _whole(view.get("byteOffset", 0)), _whole(view["byteLength"])
    if view["buffer"] != 0 or start + length > len(binary):
        raise ValueError("buffer view outside the binary chunk")
    return binary[start:start + length], view


def _primitives(document):
    meshes = document["meshes"]
    if not isinstance(meshes, list) or not meshes:
        raise ValueError("no meshes")
    return [p for mesh in meshes for p in _list(mesh, "primitives")]


def _triangles(document, primitives):
    accessors = _list(document, "accessors")
    total = 0
    for primitive in primitives:
        key = primitive["indices"] if "indices" in primitive else primitive["attributes"]["POSITION"]
        count = _whole(_at(accessors, key)["count"])
        mode = _whole(primitive.get("mode", TRIANGLES))
        if mode == TRIANGLES:
            total += count // 3
        elif mode in (STRIPS, FANS):
            total += max(count - 2, 0)
        elif mode > FANS:
            raise ValueError("mode")
    return total


def _image_size(data):
    """``(width, height)`` of a PNG or a JPEG."""
    if data.startswith(PNG):
        if data[12:16] != b"IHDR":
            raise ValueError("PNG without IHDR")
        return struct.unpack_from(">II", data, 16)
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("not a PNG or a JPEG")
    at = 2
    while True:
        if data[at] != 0xFF:
            raise ValueError("JPEG marker")
        marker = data[at + 1]
        if marker == 0xFF:
            at += 1
        elif marker in JPEG_SOF:
            height, width = struct.unpack_from(">HH", data, at + 5)
            return width, height
        elif marker == 0x01 or 0xD0 <= marker <= 0xD7:
            at += 2
        elif marker in (0xD8, 0xD9, 0xDA):
            raise ValueError("JPEG without SOF")
        else:
            at += 2 + struct.unpack_from(">H", data, at + 2)[0]


def _textures(document, binary):
    found = []
    for index, image in enumerate(_list(document, "images")):
        name = image.get("name")
        width, height = _image_size(_view(document, binary, image["bufferView"])[0])
        found.append({"name": name if isinstance(name, str) and name else f"image{index}",
                      "width": width, "height": height})
    return found


def _extent(document, primitives):
    accessors = _list(document, "accessors")
    lows, highs = [], []
    for primitive in primitives:
        position = _at(accessors, primitive["attributes"]["POSITION"])
        lows.append(_number(position["min"][1]))
        highs.append(_number(position["max"][1]))
    return max(highs) - min(lows)


def _height(document, primitives):
    height = round(_extent(document, primitives), 4)
    if not height > 0:
        raise ValueError("height")
    return height


def _weights(document, binary, index):
    """The four weights of each vertex of accessor ``index``, each set of four scaled to 1."""
    accessor = _at(_list(document, "accessors"), index)
    if "sparse" in accessor or accessor["type"] != "VEC4":
        raise ValueError("weights")
    form, one = WEIGHT_TYPES[accessor["componentType"]]
    data, view = _view(document, binary, accessor["bufferView"])
    start, count = _whole(accessor.get("byteOffset", 0)), _whole(accessor["count"])
    stride = _whole(view.get("byteStride") or struct.calcsize(form))
    return [[v / one for v in struct.unpack_from(form, data, start + n * stride)] for n in range(count)]


def _normalised(document, binary, primitives):
    weighted = [p["attributes"] for p in primitives if "WEIGHTS_0" in p["attributes"]]
    for attributes in weighted:
        sets = [_weights(document, binary, attributes[key]) for key in sorted(attributes) if WEIGHTS.fullmatch(key)]
        if any(len(s) != len(sets[0]) for s in sets):
            raise ValueError("weight sets differ in count")
        for vertex in zip(*sets):
            if not abs(sum(sum(four) for four in vertex) - 1) <= WEIGHT_TOLERANCE:
                return False
    return bool(weighted)


def _skeleton(document, binary, primitives):
    skins = _list(document, "skins")
    if not skins:
        return None
    nodes = _list(document, "nodes")
    joints = [_whole(j) for j in skins[0]["joints"]]
    parents = {}
    for index, node in enumerate(nodes):
        for child in _list(node, "children"):
            _at(nodes, child)
            parents[child] = index

    def name(index):
        found = _at(nodes, index).get("name")
        return found if isinstance(found, str) and found else f"node{index}"

    bones = []
    for joint in joints:
        parent, seen = parents.get(joint), {joint}
        while parent is not None and parent not in joints:
            if parent in seen:
                raise ValueError("a node is its own ancestor")
            seen.add(parent)
            parent = parents.get(parent)
        bones.append({"name": name(joint), "parent": None if parent is None else name(parent)})
    return {"bones": bones, "weights_normalised": _normalised(document, binary, primitives)}


def _raw_height(data):
    document = _read(data)[0]
    return _extent(document, _primitives(document))


def _summary(data):
    document, binary = _read(data)
    primitives = _primitives(document)
    return {
        "triangles": _triangles(document, primitives),
        "materials": len(_list(document, "materials")),
        "textures": _textures(document, binary),
        "height_m": _height(document, primitives),
        "skeleton": _skeleton(document, binary, primitives),
    }
