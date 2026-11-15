"""The preview of an artifact a reviewer must see: a picture as it is, a GLB as a sheet of four rendered views,
a video as a contact sheet of its frames. Pure Python and the standard library; ffmpeg through `run`.

`kind_of(head)` names the kind of a file from its first 16 bytes; `of(path)` gives its `Preview`:

- a picture (`media.picture_type`): its own bytes, unchanged.
- a GLB: `glb_sheet`, one 1024x1024 PNG of four orthographic views of `TILE` x `TILE` (front, right, back,
  three-quarter; the first two on the top row), one scale for all four, each view centred on its projected
  bounding box. Every primitive of every mesh of the default scene is drawn with its node's transform, in
  modes 4, 5 and 6, with a z-buffer and Lambert shading from either side. The colour is the material's
  `baseColorFactor`, times the base-colour texture at the pixel's `uv` (nearest) where ffmpeg decodes it;
  without ffmpeg the primitive is flat. More than `TRIANGLE_CAP` triangles are sampled, every
  `ceil(n / TRIANGLE_CAP)`-th one; the whole sheet takes at most `TIME_CAP_S`.
- a video: `video_sheet`, ffprobe's duration, then one ffmpeg call that tiles 12 frames 4 x 3 into a PNG.

Anything else raises `PreviewError`, whose message is one of a fixed few and never holds a path, a file name
or a setting value. Nothing here writes a file, reads the environment, calls the network or prints.
`run` is `subprocess.run` or a stand-in with its signature.
"""

from __future__ import annotations

import math
import multiprocessing
import os
import re
import struct
import subprocess
import time
import zlib
from array import array
from typing import NamedTuple

from kit.lib import glb, media

HEAD_BYTES = 16
GLB_MAX_BYTES = 67108864
TILE = 512
FILL = 0.92
VIEWS = (("front", 0.0, 0.0), ("right", 90.0, 0.0), ("back", 180.0, 0.0), ("three-quarter", 40.0, 18.0))
BACKGROUND = (112, 112, 118)
LIGHT = (-0.45, 0.6, 0.66)
AMBIENT, DIFFUSE = 0.38, 0.72
TRIANGLE_CAP = 600000
TIME_CAP_S = 180
CHECK_EVERY = 4096
SHEET_MAX_BYTES = 4194304
TEXTURE_MAX = 1024
TEXTURE_S = 60
VIDEO_S = 120
COLUMNS, ROWS = 4, 3
FFMPEG = ["ffmpeg", "-nostdin", "-hide_banner"]
FFPROBE = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1"]
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SOF = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
MATROSKA = b"\x1a\x45\xdf\xa3"
TRIANGLES, STRIPS, FANS = 4, 5, 6
COMPONENTS = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}
NORMALISED = {5120: 127.0, 5121: 255.0, 5122: 32767.0, 5123: 65535.0}
WIDTHS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
EXTENSION_NAME = re.compile(r"[A-Za-z0-9_]{1,64}")

UNREADABLE = "glb: not a GLB the kit can read"
TOO_LARGE = f"glb: larger than {GLB_MAX_BYTES} bytes"
NEEDS_EXTENSION = "glb: needs the extension {}"
NOT_FINITE = "glb: a position is not a finite number"
NO_TRIANGLE = "glb: the model has no triangle to draw"
NO_AREA = "glb: the model has no visible area"
TOO_SLOW = f"glb: preview took longer than {TIME_CAP_S} s"
SHEET_TOO_LARGE = f"glb: the sheet is larger than {SHEET_MAX_BYTES} bytes"
NOT_INSTALLED = "video: ffmpeg is not installed"
EXITED = "video: {} exited {}"
VIDEO_TOO_SLOW = f"video: ffmpeg did not finish in {VIDEO_S} s"
NO_DURATION = "video: no duration"
NO_FRAME = "video: no frame decoded"
NOT_A_PREVIEW = "not a picture, a GLB or a video"

VIEWS_NOTE = "4 views: " + ", ".join(name for name, _, _ in VIEWS)
TEXTURED, FLAT, NOT_DECODED = "textured", "flat shading", "flat shading: texture not decoded"


class PreviewError(Exception):
    """The file has no preview; the message is one of the fixed messages above, never a path."""


class Preview(NamedTuple):
    kind: str
    ext: str
    data: bytes
    notes: tuple


def kind_of(head: bytes) -> str | None:
    """``"picture"``, ``"glb"``, ``"video"`` or None from the first 16 bytes of a file. Never raises."""
    try:
        head = bytes(head[:HEAD_BYTES])
        if media.picture_type(head) is not None:
            return "picture"
        if head[:4] == glb.MAGIC:
            return "glb"
        if head[4:8] == b"ftyp" or head[:4] == MATROSKA:
            return "video"
    except Exception:
        pass
    return None


def of(path: str, run=subprocess.run, clock=time.monotonic, workers: int = 4) -> Preview:
    """The preview of the file at ``path``. Raises PreviewError for a file kind_of does not name."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(HEAD_BYTES)
            kind = kind_of(head)
            if kind == "glb" and os.fstat(handle.fileno()).st_size > GLB_MAX_BYTES:
                raise PreviewError(TOO_LARGE)
            data = head + handle.read() if kind in ("picture", "glb") else b""
    except OSError:
        raise PreviewError(NOT_A_PREVIEW) from None
    if kind == "picture":
        return Preview("picture", media.picture_type(data[:media.PICTURE_HEAD_BYTES]), data, ())
    if kind == "glb":
        return glb_sheet(data, run=run, clock=clock, workers=workers)
    if kind == "video":
        return video_sheet(path, run=run)
    raise PreviewError(NOT_A_PREVIEW)


# The video sheet.

def _video_call(run, argv: list[str]) -> bytes:
    try:
        done = run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=VIDEO_S)
    except subprocess.TimeoutExpired:
        raise PreviewError(VIDEO_TOO_SLOW) from None
    except OSError:
        raise PreviewError(NOT_INSTALLED) from None
    if done.returncode != 0:
        raise PreviewError(EXITED.format(argv[0], done.returncode))
    return done.stdout or b""


def video_sheet(path: str, run=subprocess.run) -> Preview:
    """A PNG of 12 frames of the video at ``path``, 4 x 3, each at most 320 pixels wide."""
    printed = _video_call(run, FFPROBE + [path])
    try:
        duration = float(printed.decode("utf-8", "replace").strip().splitlines()[0])
    except (ValueError, IndexError):
        raise PreviewError(NO_DURATION) from None
    if not (math.isfinite(duration) and duration > 0):
        raise PreviewError(NO_DURATION)
    frames = COLUMNS * ROWS
    graph = f"fps={frames}/{duration!r},scale=320:-2,tile={COLUMNS}x{ROWS}:padding=2:margin=2:color=black"
    sheet = _video_call(run, FFMPEG + ["-i", path, "-vf", graph, "-frames:v", "1", "-f", "image2pipe",
                                       "-vcodec", "png", "pipe:1"])
    if not sheet.startswith(PNG_SIGNATURE):
        raise PreviewError(NO_FRAME)
    return Preview("video", "png", bytes(sheet), (f"{frames} frames over {duration:.1f} s",))


# Reading the model.

def _whole(value) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("not a whole number")
    return value


def _list(owner, key) -> list:
    value = owner.get(key, [])
    if not isinstance(value, list):
        raise TypeError(key)
    return value


def _item(items: list, index) -> dict:
    found = items[_whole(index)]
    if not isinstance(found, dict):
        raise TypeError("not an object")
    return found


def _numbers(value, count: int) -> list[float]:
    if not isinstance(value, list) or len(value) != count:
        raise ValueError("not a list of numbers")
    for number in value:
        if not isinstance(number, (int, float)) or isinstance(number, bool):
            raise ValueError("not a number")
    return [float(number) for number in value]


def _view(document, binary, index) -> tuple[int, int, int]:
    """``(start, length, stride)`` of buffer view ``index``, which lies in the binary chunk."""
    view = _item(_list(document, "bufferViews"), index)
    start, length = _whole(view.get("byteOffset", 0)), _whole(view["byteLength"])
    if view.get("buffer", 0) != 0 or start + length > len(binary):
        raise ValueError("buffer view outside the binary chunk")
    return start, length, _whole(view.get("byteStride", 0))


def _accessor(document, binary, index, width: int, normalise: bool = False) -> list:
    """The values of accessor ``index`` of ``width`` components each, flat."""
    accessor = _item(_list(document, "accessors"), index)
    if "sparse" in accessor or WIDTHS[accessor["type"]] != width:
        raise ValueError("accessor")
    component = accessor["componentType"]
    form, count = COMPONENTS[component], _whole(accessor["count"])
    if "bufferView" not in accessor:
        return [0] * (count * width)
    start, length, stride = _view(document, binary, accessor["bufferView"])
    offset = _whole(accessor.get("byteOffset", 0))
    element = struct.calcsize("<" + form * width)
    stride = stride or element
    if stride < element or (count and offset + stride * (count - 1) + element > length):
        raise ValueError("accessor outside its buffer view")
    base = start + offset
    if stride == element:
        values = list(struct.unpack_from(f"<{count * width}{form}", binary, base))
    else:
        one = struct.Struct("<" + form * width)
        values = [v for n in range(count) for v in one.unpack_from(binary, base + n * stride)]
    if normalise and accessor.get("normalized") and component in NORMALISED:
        scale = NORMALISED[component]
        values = [max(v / scale, -1.0) for v in values]
    return values


def _multiply(a: tuple, b: tuple) -> tuple:
    """Two affine transforms, each 12 numbers row by row (3 rows of 4)."""
    return tuple(a[4 * r] * b[c] + a[4 * r + 1] * b[4 + c] + a[4 * r + 2] * b[8 + c] + (a[4 * r + 3] if c == 3 else 0.0)
                 for r in range(3) for c in range(4))


IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0)


def _local(node: dict) -> tuple:
    if "matrix" in node:
        m = _numbers(node["matrix"], 16)
        return (m[0], m[4], m[8], m[12], m[1], m[5], m[9], m[13], m[2], m[6], m[10], m[14])
    tx, ty, tz = _numbers(node.get("translation", [0, 0, 0]), 3)
    x, y, z, w = _numbers(node.get("rotation", [0, 0, 0, 1]), 4)
    sx, sy, sz = _numbers(node.get("scale", [1, 1, 1]), 3)
    rotation = (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w),
                2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w),
                2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y))
    return (rotation[0] * sx, rotation[1] * sy, rotation[2] * sz, tx,
            rotation[3] * sx, rotation[4] * sy, rotation[5] * sz, ty,
            rotation[6] * sx, rotation[7] * sy, rotation[8] * sz, tz)


def _drawn_nodes(document) -> list[tuple[int, tuple]]:
    """``(mesh, world transform)`` of every node of the default scene with a mesh, in tree order."""
    nodes = _list(document, "nodes")
    for node in nodes:
        if not isinstance(node, dict):
            raise TypeError("node")
    parent = {}
    for index, node in enumerate(nodes):
        for child in _list(node, "children"):
            _item(nodes, child)
            if child == index or child in parent:
                raise ValueError("a node with two parents or its own child")
            parent[child] = index
    for index in range(len(nodes)):
        seen, at = {index}, parent.get(index)
        while at is not None:
            if at in seen:
                raise ValueError("a node is its own ancestor")
            seen.add(at)
            at = parent.get(at)
    scenes = _list(document, "scenes")
    if "scene" in document:
        roots = _list(_item(scenes, document["scene"]), "nodes")
    elif scenes:
        roots = _list(_item(scenes, 0), "nodes")
    else:
        roots = [index for index in range(len(nodes)) if index not in parent]
    drawn, stack = [], [(root, IDENTITY) for root in reversed(roots)]
    while stack:
        index, above = stack.pop()
        node = _item(nodes, index)
        world = _multiply(above, _local(node))
        if "mesh" in node:
            drawn.append((_whole(node["mesh"]), world))
        stack.extend((child, world) for child in reversed(_list(node, "children")))
    return drawn


def _corners(mode: int, count: int) -> list[int]:
    """The vertex positions, three per triangle, of ``count`` indices in ``mode``."""
    if mode == TRIANGLES:
        return list(range(count - count % 3))
    corners = []
    for n in range(max(count - 2, 0)):
        corners += (n, n + 1, n + 2) if mode == STRIPS else (0, n + 1, n + 2)
    return corners


def _material(document, primitive, has_uv: bool) -> tuple:
    """``(red, green, blue, image)`` of a primitive: its base colour factor and its texture's image or None."""
    if "material" not in primitive:
        return (1.0, 1.0, 1.0, None)
    pbr = _item(_list(document, "materials"), primitive["material"]).get("pbrMetallicRoughness", {})
    if not isinstance(pbr, dict):
        raise TypeError("pbrMetallicRoughness")
    red, green, blue, _ = _numbers(pbr.get("baseColorFactor", [1, 1, 1, 1]), 4)
    image = None
    if has_uv and "baseColorTexture" in pbr:
        texture = _item(_list(document, "textures"), pbr["baseColorTexture"]["index"])
        if "source" in texture:
            image = _whole(texture["source"])
            _item(_list(document, "images"), image)
    return (red, green, blue, image)


class _Model(NamedTuple):
    positions: list   # x, y, z of every vertex, in world space
    corners: list     # three vertex numbers per triangle
    materials: list   # one per triangle, a number in `table`
    uvs: list         # u, v of every vertex
    table: list       # (red, green, blue, image)


def _model(document, binary) -> _Model:
    positions, corners, materials, uvs, table, keys = [], [], [], [], [], {}
    meshes = _list(document, "meshes")
    for mesh, world in _drawn_nodes(document):
        for primitive in _list(_item(meshes, mesh), "primitives"):
            if not isinstance(primitive, dict):
                raise TypeError("primitive")
            mode = _whole(primitive.get("mode", TRIANGLES))
            if mode not in (TRIANGLES, STRIPS, FANS):
                continue
            attributes = primitive["attributes"]
            local = _accessor(document, binary, attributes["POSITION"], 3)
            vertices = len(local) // 3
            if "indices" in primitive:
                indices = _accessor(document, binary, primitive["indices"], 1)
                if any(not isinstance(i, int) for i in indices):
                    raise ValueError("an index that is not a whole number")
            else:
                indices = range(vertices)
            if indices and max(indices) >= vertices:
                raise ValueError("an index past the last vertex")
            uv = None
            if "TEXCOORD_0" in attributes:
                uv = _accessor(document, binary, attributes["TEXCOORD_0"], 2, normalise=True)
                if len(uv) != 2 * vertices:
                    raise ValueError("TEXCOORD_0 count")
            key = _material(document, primitive, uv is not None)
            number = keys.setdefault(key, len(table))
            if number == len(table):
                table.append(key)
            first = len(positions) // 3
            a, b, c, d, e, f, g, h, i, j, k, m = world
            for n in range(0, len(local), 3):
                x, y, z = local[n], local[n + 1], local[n + 2]
                positions += (a * x + b * y + c * z + d, e * x + f * y + g * z + h, i * x + j * y + k * z + m)
            uvs += uv if uv is not None else [0.0] * (2 * vertices)
            drawn = [first + indices[n] for n in _corners(mode, len(indices))]
            corners += drawn
            materials += [number] * (len(drawn) // 3)
    return _Model(positions, corners, materials, uvs, table)


def _image_size(data: bytes) -> tuple[int, int]:
    """``(width, height)`` of a PNG (its IHDR) or a JPEG (its SOF marker)."""
    if data.startswith(PNG_SIGNATURE):
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


def _decode(document, binary, image: int, run):
    """``(width, height, rgb24 bytes)`` of image ``image`` through ffmpeg, or None where it is not decoded."""
    try:
        start, length, _ = _view(document, binary, _item(_list(document, "images"), image)["bufferView"])
        data = binary[start:start + length]
        width, height = _image_size(data)
    except Exception:
        return None
    width, height = min(width, TEXTURE_MAX), min(height, TEXTURE_MAX)
    if width < 1 or height < 1:
        return None
    argv = FFMPEG + ["-loglevel", "error", "-i", "pipe:0", "-frames:v", "1", "-vf", f"scale={width}:{height}",
                     "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    try:
        done = run(argv, input=data, capture_output=True, timeout=TEXTURE_S)
    except (OSError, subprocess.SubprocessError):
        return None
    pixels = done.stdout
    if done.returncode != 0 or not isinstance(pixels, bytes) or len(pixels) != width * height * 3:
        return None
    return width, height, pixels


# Drawing.

def _camera(azimuth: float, elevation: float) -> tuple:
    """The camera's right, up and back axes in world space: it looks along minus back."""
    a, e = math.radians(azimuth), math.radians(elevation)
    right = (math.cos(a), 0.0, -math.sin(a))
    up = (-math.sin(a) * math.sin(e), math.cos(e), -math.cos(a) * math.sin(e))
    back = (math.sin(a) * math.cos(e), math.sin(e), math.cos(a) * math.cos(e))
    return right, up, back


def _project(positions: list, used: list, axes: tuple) -> tuple[list, list, list]:
    (rx, ry, rz), (ux, uy, uz), (bx, by, bz) = axes
    xs, ys, zs = [0.0] * len(used), [0.0] * len(used), [0.0] * len(used)
    for n, vertex in enumerate(used):
        x, y, z = positions[3 * vertex], positions[3 * vertex + 1], positions[3 * vertex + 2]
        xs[n], ys[n], zs[n] = rx * x + ry * y + rz * z, ux * x + uy * y + uz * z, bx * x + by * y + bz * z
    return xs, ys, zs


def _draw(job: tuple, check=None) -> tuple[bytes, int]:
    """One view: ``(rgb24 bytes of TILE x TILE, pixels drawn)``. ``check`` is called every CHECK_EVERY
    triangles. A triangle's corner of pixel coordinates (x right, y down) and depth (larger is nearer)."""
    points, corners, materials, uvs, table, textures = job
    size = TILE
    colour = bytearray(bytes(BACKGROUND) * (size * size))
    depth = [-math.inf] * (size * size)
    length = math.sqrt(sum(c * c for c in LIGHT))
    lx, ly, lz = (c / length for c in LIGHT)
    ceil, floor = math.ceil, math.floor
    for t in range(len(corners) // 3):
        if check is not None and t % CHECK_EVERY == 0:
            check()
        i0, i1, i2 = 3 * corners[3 * t], 3 * corners[3 * t + 1], 3 * corners[3 * t + 2]
        x0, y0, z0 = points[i0], points[i0 + 1], points[i0 + 2]
        x1, y1, z1 = points[i1], points[i1 + 1], points[i1 + 2]
        x2, y2, z2 = points[i2], points[i2 + 1], points[i2 + 2]
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if area == 0:
            continue
        left = max(ceil(min(x0, x1, x2) - 0.5), 0)
        right = min(floor(max(x0, x1, x2) - 0.5), size - 1)
        top = max(ceil(min(y0, y1, y2) - 0.5), 0)
        bottom = min(floor(max(y0, y1, y2) - 0.5), size - 1)
        if left > right or top > bottom:
            continue
        # The normal in camera space (y up), turned to the viewer: lit from either side.
        ax, ay, az = x1 - x0, y0 - y1, z1 - z0
        bx, by, bz = x2 - x0, y0 - y2, z2 - z0
        nx, ny, nz = ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx
        if nz < 0:
            nx, ny, nz = -nx, -ny, -nz
        lit = (nx * lx + ny * ly + nz * lz) / math.sqrt(nx * nx + ny * ny + nz * nz)
        shade = AMBIENT + DIFFUSE * (lit if lit > 0 else 0.0)
        red, green, blue, texture = table[materials[t]]
        inverse = 1.0 / area
        dzdx = ((z1 - z0) * (y2 - y0) - (z2 - z0) * (y1 - y0)) * inverse
        dzdy = ((z2 - z0) * (x1 - x0) - (z1 - z0) * (x2 - x0)) * inverse
        if texture is None:
            flat = bytes((min(255, max(0, int(red * 255 * shade + 0.5))),
                          min(255, max(0, int(green * 255 * shade + 0.5))),
                          min(255, max(0, int(blue * 255 * shade + 0.5)))))
        else:
            width, height, texels = textures[texture]
            kr, kg, kb = red * shade, green * shade, blue * shade
            j0, j1, j2 = 2 * corners[3 * t], 2 * corners[3 * t + 1], 2 * corners[3 * t + 2]
            u0, v0 = uvs[j0], uvs[j0 + 1]
            u1, v1, u2, v2 = uvs[j1], uvs[j1 + 1], uvs[j2], uvs[j2 + 1]
            dudx = ((u1 - u0) * (y2 - y0) - (u2 - u0) * (y1 - y0)) * inverse
            dudy = ((u2 - u0) * (x1 - x0) - (u1 - u0) * (x2 - x0)) * inverse
            dvdx = ((v1 - v0) * (y2 - y0) - (v2 - v0) * (y1 - y0)) * inverse
            dvdy = ((v2 - v0) * (x1 - x0) - (v1 - v0) * (x2 - x0)) * inverse
        sign = 1.0 if area > 0 else -1.0
        # Each edge as a * x + b * y + c, at least 0 inside.
        edges = []
        for (px, py), (qx, qy) in (((x0, y0), (x1, y1)), ((x1, y1), (x2, y2)), ((x2, y2), (x0, y0))):
            edges.append((-(qy - py) * sign, (qx - px) * sign, ((qy - py) * px - (qx - px) * py) * sign))
        for row in range(top, bottom + 1):
            yc = row + 0.5
            low, high = left, right
            for a, b, c in edges:
                rest = b * yc + c
                if a > 0:
                    low = max(low, ceil(-rest / a - 0.5))
                elif a < 0:
                    high = min(high, floor(-rest / a - 0.5))
                elif rest < 0:
                    high = -1
            if low > high:
                continue
            xc = low + 0.5
            z = z0 + dzdx * (xc - x0) + dzdy * (yc - y0)
            if texture is not None:
                u = u0 + dudx * (xc - x0) + dudy * (yc - y0)
                v = v0 + dvdx * (xc - x0) + dvdy * (yc - y0)
            at = row * size + low
            for column in range(low, high + 1):
                if z > depth[at]:
                    depth[at] = z
                    if texture is None:
                        colour[3 * at:3 * at + 3] = flat
                    else:
                        k = 3 * ((floor(v * height) % height) * width + floor(u * width) % width)
                        colour[3 * at] = min(255, max(0, int(texels[k] * kr + 0.5)))
                        colour[3 * at + 1] = min(255, max(0, int(texels[k + 1] * kg + 0.5)))
                        colour[3 * at + 2] = min(255, max(0, int(texels[k + 2] * kb + 0.5)))
                z += dzdx
                if texture is not None:
                    u += dudx
                    v += dvdx
                at += 1
    drawn = sum(1 for d in depth if d != -math.inf)
    return bytes(colour), drawn


def _png(width: int, height: int, rows) -> bytes:
    """An 8-bit RGB PNG of ``rows`` (rgb24 bytes each), filter 0, one IDAT."""
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    raw = b"".join(b"\x00" + row for row in rows)
    return (PNG_SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def _sheet(tiles: list[bytes]) -> bytes:
    line = 3 * TILE
    rows = []
    for top, bottom in ((0, 1), (2, 3)):
        for row in range(TILE):
            rows.append(tiles[top][row * line:(row + 1) * line] + tiles[bottom][row * line:(row + 1) * line])
    return _png(2 * TILE, 2 * TILE, rows)


def _read(data: bytes):
    """``(document, model)`` of a GLB; every failure of the reading is UNREADABLE."""
    try:
        document, binary = glb.read(data)
        required = _list(document, "extensionsRequired")
        for name in required:
            if not isinstance(name, str) or not EXTENSION_NAME.fullmatch(name):
                raise ValueError("extension name")
        for name in required:
            if not (name.startswith("KHR_materials_") or name == "KHR_texture_transform"):
                raise PreviewError(NEEDS_EXTENSION.format(name))
        return document, binary, _model(document, binary)
    except PreviewError:
        raise
    except Exception:
        raise PreviewError(UNREADABLE) from None


def glb_sheet(data: bytes, run=subprocess.run, clock=time.monotonic, workers: int = 4) -> Preview:
    """The 1024x1024 PNG of four views of a GLB."""
    start = clock()

    def check():
        if clock() - start > TIME_CAP_S:
            raise PreviewError(TOO_SLOW)

    if len(data) > GLB_MAX_BYTES:
        raise PreviewError(TOO_LARGE)
    document, binary, model = _read(data)
    if not all(map(math.isfinite, model.positions)):
        raise PreviewError(NOT_FINITE)
    total = len(model.corners) // 3
    if total == 0:
        raise PreviewError(NO_TRIANGLE)
    step = -(-total // TRIANGLE_CAP)
    kept = [model.corners[3 * t + k] for t in range(0, total, step) for k in range(3)]
    materials = model.materials[::step]
    notes = [VIEWS_NOTE, f"{total} triangles"]

    images, decoded, missed = {}, [], False
    table = []
    for red, green, blue, image in model.table:
        slot = None
        if image is not None:
            if image not in images:
                texture = _decode(document, binary, image, run)
                images[image] = None if texture is None else len(decoded)
                if texture is not None:
                    decoded.append(texture)
                check()
            slot = images[image]
            missed = missed or slot is None
        table.append((red, green, blue, slot))
    notes.append(NOT_DECODED if missed else TEXTURED if images else FLAT)
    if step > 1:
        notes.append(f"drew {len(kept) // 3} of {total} triangles")

    used = sorted(set(kept))
    number = {vertex: n for n, vertex in enumerate(used)}
    corners = array("I", [number[vertex] for vertex in kept])
    uvs = array("d", [w for vertex in used for w in (model.uvs[2 * vertex], model.uvs[2 * vertex + 1])])
    projected = [_project(model.positions, used, _camera(azimuth, elevation)) for _, azimuth, elevation in VIEWS]
    extent = max(max(max(axis) - min(axis) for axis in view[:2]) for view in projected)
    if not extent > 0:
        raise PreviewError(NO_AREA)
    scale = FILL * TILE / extent
    jobs = []
    for xs, ys, zs in projected:
        mx, my = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        points = array("d")
        for x, y, z in zip(xs, ys, zs):
            points.extend((TILE / 2 + (x - mx) * scale, TILE / 2 - (y - my) * scale, z * scale))
        jobs.append((points, corners, array("I", materials), uvs, table, decoded))
    check()
    if workers > 1:
        with multiprocessing.Pool(processes=min(workers, len(jobs))) as pool:
            waiting = [pool.apply_async(_draw, (job,)) for job in jobs]
            try:
                views = [result.get(timeout=max(TIME_CAP_S - (clock() - start), 0)) for result in waiting]
            except multiprocessing.TimeoutError:
                raise PreviewError(TOO_SLOW) from None
    else:
        views = [_draw(job, check) for job in jobs]
    check()
    if not any(drawn for _, drawn in views):
        raise PreviewError(NO_AREA)
    sheet = _sheet([tile for tile, _ in views])
    if len(sheet) > SHEET_MAX_BYTES:
        raise PreviewError(SHEET_TOO_LARGE)
    return Preview("glb", "png", sheet, tuple(notes))
