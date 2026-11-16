"""What the 3D tools share: the vendors' addresses, the reasons, the job's reference image and model, the
stage files and every HTTP request.

The model tool (`kit/lib/tools/model3d.py`) asks the model vendor's queue API for a model of one reference
image; the rig tool (`kit/lib/tools/rig.py`) asks the rig vendor, on the same queue API, to rig a model held
in the asset store, the one the asset's `assets/state.json` names.
A tool module is loaded fresh for each call, so every constant a test patches is here, and the tools read
them as ``vendor.<NAME>`` at call time.

Every request goes through the opener of `kit/lib/media.py`, which reads no proxy from the environment and
follows no redirect, with `TIMEOUT`. A download sends no key. A reason never contains a value: not a
setting, a key, a URL, a response body or the text of an exception.
"""

import base64
import contextlib
import hashlib
import http.client
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from kit.lib import assets, cardscan, glb, media
from kit.lib.tools import Down, Failed

MODEL_API = "https://queue.fal.run"
MODEL_PATH = "tripo3d/tripo/v2.5/image-to-3d"
RIG_API = "https://queue.fal.run"
RIG_PATH = "fal-ai/meshy/rigging"
RIG_ID = "meshy"
POLL_SECONDS = 5
POLLS = 360
TIMEOUT = 60
DATA_URI_BYTES = 30 * 1000 * 1000
REFERENCE_MAX_BYTES = 30000000
RUN = subprocess.run

MANIFEST = "media/manifest.tsv"
REQUEST_KEYS = ["for", "prompt", "refs"]
REF_PREFIX = "media:"
DATA_URI_PREFIX = "data:application/octet-stream;base64,"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SIZE = re.compile(r"[1-9][0-9]*")

NOT_A_REQUEST = "request is not {for, prompt, refs}"
NO_REFERENCE = "request names no reference image"
NOT_IN_MANIFEST = "reference is not in the manifest"
REFUSED = "vendor refused the job"
UNREACHABLE = "vendor unreachable"
VENDOR_FAILED = "vendor failed the job"
TOO_SLOW = "vendor did not finish in time"
DOWNLOAD_FAILED = "download failed"
NOT_STORED = "asset store did not take the output"
NOT_IN_STORE = "model is not in the asset store"
TOO_LARGE = "model is larger than a data URI allows"
NO_MODEL = "request names no asset model"
NO_STATE = "asset state is not an input"
NOT_A_GLB = "output is not a GLB the kit can read"
FRAMED = "reference is framed like a card scan"
NOT_DECODED = "reference image could not be decoded"
REASONS = (NOT_A_REQUEST, NO_REFERENCE, NOT_IN_MANIFEST, REFUSED, UNREACHABLE, VENDOR_FAILED, TOO_SLOW,
           DOWNLOAD_FAILED, NOT_STORED, NOT_A_GLB)
RIG_REASONS = REASONS + (NOT_IN_STORE, TOO_LARGE, NO_MODEL, NO_STATE)
MODEL_REASONS = REASONS + (FRAMED, NOT_DECODED)
ASSET_PREFIX = "asset:"


class _NoAnswer(Exception):
    pass


def _before(reason):
    """A failure before the first vendor request: nothing was spent."""
    return Failed(reason, usd=0, compute_min=0)


def _json(text):
    try:
        return json.loads(text) if text is not None else None
    except ValueError:
        return None


def request(call):
    """The request, the first input: ``{for, prompt, refs}`` with ``refs`` a list."""
    inputs = call["inputs"]
    found = _json(inputs[0]["text"]) if inputs else None
    if not isinstance(found, dict) or sorted(found) != REQUEST_KEYS or not isinstance(found["refs"], list):
        raise _before(NOT_A_REQUEST)
    return found


def asset(call):
    """The request's ``for``, a non-empty string."""
    found = request(call)["for"]
    if not isinstance(found, str) or not found:
        raise _before(NOT_A_REQUEST)
    return found


def reference_row(call):
    """``(sha256, size)`` of the job's reference image: ``refs[0]`` of the request (the first input), looked
    up in the manifest (the second input), its size a whole number of bytes."""
    inputs = call["inputs"]
    refs = request(call)["refs"]
    if not refs or not isinstance(refs[0], str) or not refs[0].startswith(REF_PREFIX) or refs[0] == REF_PREFIX:
        raise _before(NO_REFERENCE)
    name = refs[0][len(REF_PREFIX):]
    if len(inputs) < 2 or inputs[1]["path"] != MANIFEST or inputs[1]["text"] is None:
        raise _before(NOT_IN_MANIFEST)
    for line in inputs[1]["text"].splitlines()[1:]:
        fields = line.split("\t")
        if (len(fields) >= 3 and fields[0] == name and _SHA256.fullmatch(fields[1])
                and _SIZE.fullmatch(fields[2])):
            return fields[1], int(fields[2])
    raise _before(NOT_IN_MANIFEST)


def reference(call):
    """The sha256 of the job's reference image (`reference_row`)."""
    return reference_row(call)[0]


def check_reference(call, sha256, size):
    """Refuse, before any vendor request, a reference image that does not decode or is framed like a card
    scan (`kit/lib/cardscan.py`). It is fetched from its public link into a temporary directory, never
    into the job's ``out``, and the directory is gone afterwards; ``Down`` when the link does not serve it."""
    if size > REFERENCE_MAX_BYTES:
        raise _before(NOT_DECODED)
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "reference")
        try:
            media.fetch(call["settings"], sha256, size, path)
            with open(path, "rb") as handle:
                data = handle.read()
        except (media.Refused, media.Failed, OSError):
            raise Down() from None
    pixels = cardscan.decode(data, RUN)
    if pixels is None:
        raise _before(NOT_DECODED)
    if cardscan.art_region(pixels) is not None:
        raise _before(FRAMED)


def model(call):
    """The sha256 of the model to rig: the request (the first input) names ``refs`` exactly
    ``["asset:<for>"]``, and a later input is ``production/assets/<for>/assets/state.json``, whose
    ``sha256`` it is."""
    name = asset(call)
    if request(call)["refs"] != [ASSET_PREFIX + name]:
        raise _before(NO_MODEL)
    rel = f"production/assets/{name}/assets/state.json"
    for item in call["inputs"][1:]:
        state = _json(item["text"]) if item["path"] == rel else None
        if isinstance(state, dict) and isinstance(state.get("sha256"), str) and _SHA256.fullmatch(state["sha256"]):
            return state["sha256"]
    raise _before(NO_STATE)


def summary(path):
    """`kit.lib.glb.summary` of the output at ``path``; a GLB that does not read is deleted and fails the job."""
    with open(path, "rb") as handle:
        data = handle.read()
    try:
        return glb.summary(data)
    except glb.Broken:
        os.unlink(path)
        raise Failed(NOT_A_GLB) from None


def stage_file(out, name, data):
    """Write the stage file ``name`` in ``out``: ``data`` as JSON, keys sorted, one space of indent."""
    with open(os.path.join(out, name), "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, indent=1, sort_keys=True) + "\n")


def manifest(call, engine, names, **extra):
    """The stage manifest of a vendor job, ``{job, engine, flags, inputs, seeds, outputs}`` and ``extra``:
    ``outputs`` the outputs ``names`` of the job, in name order."""
    outputs = []
    for name in sorted(names):
        with open(os.path.join(call["out"], name), "rb") as handle:
            data = handle.read()
        outputs.append({"bytes": len(data), "name": name, "sha256": hashlib.sha256(data).hexdigest()})
    return {"job": call["hash"], "engine": engine, "flags": [],
            "inputs": [{"path": item["path"], "sha256": item["sha256"]} for item in call["inputs"]],
            "seeds": {}, "outputs": outputs, **extra}


def read_key(path):
    """The one line of a key file; ``Down`` when it cannot be read, is empty or holds more than one line."""
    try:
        with open(path, "rb") as handle:
            key = handle.read().decode("utf-8").strip()
    except (OSError, UnicodeDecodeError):
        raise Down() from None
    if not key or "\n" in key or "\r" in key:
        raise Down()
    return key


def link(settings, sha256):
    """The public link of the image ``sha256``; ``Down`` when the media settings are not usable."""
    try:
        return media.link(settings, sha256)
    except media.Refused:
        raise Down() from None


def fetch_model(call, sha256, path):
    """Write the model ``sha256`` from the asset store to ``path``; its size."""
    try:
        return assets.get(call["settings"], call["secrets"]["store.assets"], sha256, path)["bytes"]
    except media.Refused:
        raise Down() from None
    except media.Failed as failure:
        if str(failure) == assets.NO_SUCH_OBJECT:
            raise _before(NOT_IN_STORE) from None
        raise Down() from None


def data_uri(path, size):
    """The file as a data URI; it must not be larger than `DATA_URI_BYTES`."""
    if size > DATA_URI_BYTES:
        raise _before(TOO_LARGE)
    with open(path, "rb") as handle:
        return DATA_URI_PREFIX + base64.b64encode(handle.read()).decode("ascii")


def _send(method, url, headers, payload=None):
    """``(status, body)`` of one request; ``_NoAnswer`` when no answer came."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    if data is not None:
        headers = {**headers, "Content-Type": "application/json"}
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with media._OPENER.open(request, timeout=TIMEOUT) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        error.close()
        return error.code, b""
    except (OSError, http.client.HTTPException, ValueError):
        raise _NoAnswer() from None


def _object(body):
    try:
        found = json.loads(body.decode("utf-8"))
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


def submit(url, authorization, payload):
    """The answer of the first `POST`, a JSON object. ``Down`` when no answer came or the key is refused."""
    try:
        status, body = _send("POST", url, {"Authorization": authorization}, payload)
    except _NoAnswer:
        raise Down() from None
    if status in (401, 403):
        raise Down()
    answer = _object(body) if 200 <= status < 300 else None
    if answer is None:
        raise Failed(REFUSED)
    return answer


def get(url, authorization):
    """The answer of a later `GET`, a JSON object."""
    try:
        status, body = _send("GET", url, {"Authorization": authorization})
    except _NoAnswer:
        raise Failed(UNREACHABLE) from None
    answer = _object(body) if 200 <= status < 300 else None
    if answer is None:
        raise Failed(VENDOR_FAILED)
    return answer


def poll(url, authorization, done, waiting):
    """`GET` ``url`` every `POLL_SECONDS`, at most `POLLS` times, until its ``status`` is ``done``; that answer."""
    for _ in range(POLLS):
        time.sleep(POLL_SECONDS)
        answer = get(url, authorization)
        status = answer.get("status")
        if status == done:
            return answer
        if status not in waiting:
            raise Failed(VENDOR_FAILED)
    raise Failed(TOO_SLOW)


def under(url, api):
    """``url`` when it is a string under ``api``; ``Failed`` otherwise."""
    if not isinstance(url, str) or not url.startswith(api + "/"):
        raise Failed(VENDOR_FAILED)
    return url


def download_url(value):
    """``value`` when it is an `https` URL, or `http` to a loopback host, its query kept; ``Failed`` otherwise."""
    try:
        url = urllib.parse.urlsplit(value) if isinstance(value, str) else None
        host = url.hostname if url else None
    except ValueError:
        url = host = None
    if (url is None or not host or any(c.isspace() for c in value)
            or not (url.scheme == "https" or (url.scheme == "http" and host in media.LOOPBACK))):
        raise Failed(VENDOR_FAILED)
    return value


def download(url, path):
    """Write the file at ``url`` to ``path``, sending no key; ``Failed`` unless it came whole and not empty."""
    request = urllib.request.Request(url)
    size = 0
    try:
        with media._OPENER.open(request, timeout=TIMEOUT) as response, open(path, "wb") as handle:
            if response.status != 200:
                raise OSError()
            while piece := response.read(media.CHUNK):
                handle.write(piece)
                size += len(piece)
    except (OSError, http.client.HTTPException, ValueError):
        size = 0
    if size == 0:
        with contextlib.suppress(OSError):
            os.unlink(path)
        raise Failed(DOWNLOAD_FAILED)


def store(call, path):
    """Put the output in the asset store."""
    try:
        assets.put(call["settings"], call["secrets"]["store.assets"], path)
    except (media.Refused, media.Failed):
        raise Failed(NOT_STORED) from None


def spent(call):
    """Neither vendor answers a price in dollars: a job costs what it expected."""
    return {"usd": call["expected"]["usd"], "compute_min": call["expected"]["compute_min"]}
