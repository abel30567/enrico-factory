"""The one vendor of voice, music and sound effects, shared by the tools `voice`, `music` and `sfx`.

This file is not in ``kit/lib/tools/``, where every ``.py`` file is a tool. A tool here makes one call:
``POST BASE + <path> + "?output_format=mp3_44100_128"`` with the key in the header ``xi-api-key`` and a
JSON body with sorted keys. Only tests replace ``BASE``.

The key is the text of the tool's own key file with surrounding whitespace removed; an empty key, or one
with whitespace inside it, is ``Down``. The job's first input is a request: JSON with exactly the keys
``for``, ``prompt`` and ``refs``, ``prompt`` a string that is not empty after stripping. How an answer ends:

- 200 and an MP3 body (``ID3``, or ``0xFF`` then a byte whose top three bits are set): the body goes to
  ``out/<name>.mp3`` and the cost is the job's expected cost, since no answer carries a money amount;
- 200 with any other body: ``the vendor answer is not audio``;
- 401, 402, 403, 429, 503, or no answer: ``Down``;
- 400, 404 or 422: the tool's own reason when ``detail.status`` or ``detail.code`` is its word, else
  ``the vendor refused the request``;
- any other status: ``the vendor failed``.

A failure after a call carries no amounts, so the expected ones count.
"""

import http.client
import json
import urllib.error
import urllib.request
from pathlib import Path

from kit.lib.tools import Down, Failed

BASE = "https://api.elevenlabs.io"
QUERY = "?output_format=mp3_44100_128"
TIMEOUT = 300

NOT_A_REQUEST = "the first input is not a request"
NOT_AUDIO = "the vendor answer is not audio"
REFUSED = "the vendor refused the request"
VENDOR_FAILED = "the vendor failed"
REASONS = (NOT_A_REQUEST, NOT_AUDIO, REFUSED, VENDOR_FAILED)

DOWN = (401, 402, 403, 429, 503)
REFUSALS = (400, 404, 422)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def key(call, name):
    """The key in the key file ``name`` of ``call``; ``Down`` when it is not one word."""
    try:
        text = Path(call["secrets"][name]).read_text(encoding="utf-8").strip()
    except (KeyError, OSError, UnicodeError):
        raise Down() from None
    if not text or not text.isascii() or not text.isprintable() or any(c.isspace() for c in text):
        raise Down()
    return text


def prompt(call):
    """The prompt of the job's first input, a request; ``Failed`` before any call when it is not one."""
    first = call["inputs"][0]["text"] if call["inputs"] else None
    try:
        request = json.loads(first) if first is not None else None
    except ValueError:
        request = None
    if (not isinstance(request, dict) or sorted(request) != ["for", "prompt", "refs"]
            or not isinstance(request["prompt"], str) or not request["prompt"].strip()):
        raise Failed(NOT_A_REQUEST, usd=0, compute_min=0)
    return request["prompt"]


def _words(data):
    """``detail.status`` and ``detail.code`` of an error body, each kept only when it is a string."""
    try:
        detail = json.loads(data).get("detail")
    except (ValueError, AttributeError):
        return set()
    if not isinstance(detail, dict):
        return set()
    return {word for word in (detail.get("status"), detail.get("code")) if isinstance(word, str)}


def _audio(data):
    return data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0)


def post(call, secret, path, body, name, own=None):
    """Make the one call and end it as the module says; ``own`` maps a vendor word to the tool's reason."""
    request = urllib.request.Request(
        BASE + path + QUERY, data=json.dumps(body, sort_keys=True).encode("utf-8"), method="POST",
        headers={"xi-api-key": secret, "Content-Type": "application/json", "Accept": "audio/mpeg"})
    try:
        with _OPENER.open(request, timeout=TIMEOUT) as answer:
            status, data = answer.status, answer.read()
    except urllib.error.HTTPError as error:
        status = error.code
        try:
            data = error.read()
        except (OSError, http.client.HTTPException):
            data = b""
    except (OSError, http.client.HTTPException):
        raise Down() from None
    if status in DOWN:
        raise Down()
    if status in REFUSALS:
        words = _words(data)
        raise Failed(next((reason for word, reason in sorted((own or {}).items()) if word in words), REFUSED))
    if status != 200:
        raise Failed(VENDOR_FAILED)
    if not _audio(data):
        raise Failed(NOT_AUDIO)
    (Path(call["out"]) / f"{name}.mp3").write_bytes(data)
    return {"usd": call["expected"]["usd"], "compute_min": call["expected"]["compute_min"]}
