"""The tool `pad`: a picture with a plain margin, the source scaled by 2/3 and centred on a flat canvas.

The job's first input is a pad request, ``{"source": "media:<id>"}`` and no other key; its second input is
`media/manifest.tsv`. The source is found in the manifest, fetched from the public link of the media store and
checked against its row as `grok-imagine` checks a ref: no answer is ``Down``, a wrong answer is ``Failed``. It
must be a PNG or a JPEG. One ``ffmpeg`` call, run with an environment of ``HOME``, ``LANG`` and ``PATH`` only (as
`kit/lib/analysis.py` runs its programs), writes the one output ``pad.png``: the source's own width and height,
a canvas of ``COLOUR``, the whole source scaled by 2/3 in each direction and centred. The source is read as
coded: ``-noautorotate`` keeps ffmpeg from turning a JPEG by its Exif orientation, so the pixels padded are the
ones whose size the header gives. No model is called and nothing is spent. ``ffmpeg`` not on ``PATH`` is ``Down``.
"""

import hashlib
import http.client
import json
import os
import re
import struct
import subprocess
import tempfile
import urllib.error
import urllib.request

from kit.lib import media
from kit.lib.tools import Down, Failed

RESOURCE = "store.media"
NOT_IN_MANIFEST = "source not in the manifest"
NOT_FETCHED = "source not fetched"
NOT_PICTURE = "source is not a PNG or JPEG"
FFMPEG_FAILED = "ffmpeg failed"
REASONS = ("not a request", NOT_IN_MANIFEST, NOT_FETCHED, NOT_PICTURE, FFMPEG_FAILED)
KEYS = ["source"]
REF_PREFIX = "media:"
MANIFEST = "media/manifest.tsv"
OUTPUT = "pad.png"
COLOUR = "0xD3D3D3"
SCALE = (2, 3)
PNG = b"\x89PNG\r\n\x1a\n"
JPEG = b"\xff\xd8\xff"
TIMEOUT = 300
FFMPEG_S = 300
_SHA256 = re.compile(r"[0-9a-f]{64}")
_BYTES = re.compile(r"[0-9]+")
# The JPEG frame headers that carry the picture's size: SOF0..SOF15 but DHT (C4), JPG (C8) and DAC (CC).
_SOF = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _failed(reason):
    return Failed(reason, usd=0, compute_min=0)


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _request(call):
    """The media id of the request's ``source``; ``Failed`` when the request is not one this tool can make."""
    try:
        request = json.loads(call["inputs"][0]["text"])
    except (TypeError, ValueError):
        raise _failed("not a request") from None
    if not isinstance(request, dict) or sorted(request) != KEYS:
        raise _failed("not a request")
    source = request["source"]
    if not isinstance(source, str) or not source.startswith(REF_PREFIX) or source == REF_PREFIX:
        raise _failed("not a request")
    return source[len(REF_PREFIX):]


def _row(call, name):
    """``(sha256, bytes)`` of ``name`` in the manifest, the job's second input; ``Failed`` when it has no row."""
    inputs = call["inputs"]
    if len(inputs) < 2 or inputs[1]["path"] != MANIFEST or inputs[1]["text"] is None:
        raise _failed(NOT_IN_MANIFEST)
    rows = {}
    for line in inputs[1]["text"].splitlines():
        fields = line.split("\t")
        if len(fields) == 4 and _SHA256.fullmatch(fields[1]) and _BYTES.fullmatch(fields[2]):
            rows.setdefault(fields[0], (fields[1], int(fields[2])))
    if name not in rows:
        raise _failed(NOT_IN_MANIFEST)
    return rows[name]


def _fetch(settings, sha256, size):
    """The bytes of the source ``sha256`` from its public link; ``Down`` when nothing answered."""
    try:
        url = media.link(settings, sha256)
    except media.Refused:
        raise Down() from None
    request = urllib.request.Request(url, headers={"User-Agent": media.USER_AGENT})
    try:
        with _opener().open(request, timeout=TIMEOUT) as answer:
            status, data = answer.status, answer.read(size + 1)
    except urllib.error.HTTPError as error:
        with error:
            raise _failed(NOT_FETCHED) from None
    except (OSError, http.client.HTTPException, ValueError):
        raise Down() from None
    if status != 200 or len(data) != size or hashlib.sha256(data).hexdigest() != sha256:
        raise _failed(NOT_FETCHED)
    return data


def _jpeg_size(data):
    """``(width, height)`` from the first frame header of the JPEG ``data``, or None."""
    at = 2
    while at + 4 <= len(data):
        if data[at] != 0xFF:
            return None
        marker = data[at + 1]
        if marker == 0xFF:
            at += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            at += 2
            continue
        if marker in (0xD9, 0xDA):
            return None
        length = struct.unpack(">H", data[at + 2:at + 4])[0]
        if marker in _SOF:
            if length < 7 or at + 9 > len(data):
                return None
            height, width = struct.unpack(">HH", data[at + 5:at + 9])
            return width, height
        at += 2 + length
    return None


def _picture(data):
    """``(extension, width, height)`` of the PNG or JPEG ``data``; ``Failed`` when it is neither."""
    size = None
    if data.startswith(PNG) and data[12:16] == b"IHDR" and len(data) >= 24:
        size, extension = struct.unpack(">II", data[16:24]), "png"
    elif data.startswith(JPEG):
        size, extension = _jpeg_size(data), "jpg"
    if not size or not all(size):
        raise _failed(NOT_PICTURE)
    return extension, size[0], size[1]


def _filter(width, height):
    """The ``-vf`` of the pad: the source scaled by ``SCALE`` (at least 1 pixel), centred on ``COLOUR``."""
    inner_w = max(1, width * SCALE[0] // SCALE[1])
    inner_h = max(1, height * SCALE[0] // SCALE[1])
    return (f"format=rgb24,scale={inner_w}:{inner_h},"
            f"pad={width}:{height}:{(width - inner_w) // 2}:{(height - inner_h) // 2}:color={COLOUR}")


def _ffmpeg(directory, argv):
    """Runs ``argv`` in ``directory`` with ``HOME``, ``LANG`` and ``PATH`` only; ``Down`` when there is no
    ``ffmpeg``, ``Failed`` when it does not exit 0."""
    home = os.path.join(directory, "home")
    os.mkdir(home)
    env = {"HOME": home, "LANG": "C.UTF-8", "PATH": os.environ.get("PATH") or os.defpath}
    try:
        done = subprocess.run(argv, cwd=directory, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=FFMPEG_S, check=False)
    except FileNotFoundError:
        raise Down() from None
    except (OSError, ValueError, subprocess.SubprocessError):
        raise _failed(FFMPEG_FAILED) from None
    if done.returncode != 0:
        raise _failed(FFMPEG_FAILED)


def run(call):
    sha256, size = _row(call, _request(call))
    data = _fetch(call["settings"], sha256, size)
    extension, width, height = _picture(data)
    output = os.path.join(call["out"], OUTPUT)
    try:
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, f"source.{extension}")
            with open(source, "wb") as handle:
                handle.write(data)
            _ffmpeg(directory, ["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-noautorotate",
                                "-i", source,
                                "-vf", _filter(width, height), "-frames:v", "1", output])
        if not os.path.isfile(output) or os.path.getsize(output) == 0:
            raise _failed(FFMPEG_FAILED)
    except BaseException:
        if os.path.lexists(output):
            os.remove(output)
        raise
    return {"usd": 0, "compute_min": 0}
