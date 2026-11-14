"""youtube-upload: upload one video to YouTube as a private video with the synthetic-media disclosure set.

The job has two inputs, in this order: the upload request and ``media/manifest.tsv``. The request is one JSON
object with exactly the keys ``captions``, ``description``, ``link``, ``title`` and ``video``: ``video`` and
``captions`` are each ``media:<id>``, an id with exactly one row in the manifest, and ``link`` is the video's
public link in the media store, ending with ``/`` and the sha256 of the ``video`` row, whose bytes are those
of the row.
``captions`` is true because the captions are burned into the uploaded file; no caption track is uploaded.
The key file of ``platform.youtube`` holds three lines: client id, client secret, refresh token.

The requests, in this order, all through one opener that reads no proxy from the environment and follows
no redirect: a ``GET`` of the public link into a temporary file, the token refresh, the resumable upload's
``POST``, the ``PUT`` of the file, and one read of the video's status. Every upload is
``"privacyStatus": "private"`` and ``"containsSyntheticMedia": true``; the request cannot change that.

The tool writes no output. It records the platform's answer as ``post.json`` in ``call["record"]``, with the
keys of ``kit.lib.posts.KEYS``: the video id, its visibility and disclosure as the upload answered, whether
the read gave processing ``succeeded``, the sha256 of the ``video`` row, the job's hash and the time of the
read. A post that is not private or not disclosed is still done: the publish check judges it. Nothing of the
key file or the access token is in the record or a reason.
"""

import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from kit.lib import media, production
from kit.lib.tools import Down, Failed

RESOURCE = "platform.youtube"
NOT_A_REQUEST = "request is not an upload request"
UPLOAD_REFUSED = "upload refused"
UPLOAD_FAILED = "upload failed"
REASONS = (NOT_A_REQUEST, media.LINK_UNREACHABLE, media.OTHER_BYTES_AT_LINK, UPLOAD_REFUSED, UPLOAD_FAILED)

TIMEOUT = 600
CHUNK = 1024 * 1024
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
READ_URL = "https://www.googleapis.com/youtube/v3/videos?part=status,processingDetails&id="
VIDEO_TYPE = "video/*"
RECORD = "post.json"
KEYS = ["captions", "description", "link", "title", "video"]
MANIFEST = "media/manifest.tsv"
MANIFEST_HEADER = "id\tsha256\tbytes\tsource"
VISIBILITIES = ("private", "unlisted", "public")
DISCLOSURE = "status.containsSyntheticMedia"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SIZE = re.compile(r"[1-9][0-9]*")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _failed(reason):
    return Failed(reason, usd=0, compute_min=0)


def _row(lines, value):
    """``(sha256, bytes)`` of the one manifest row of ``value``, ``media:<id>``; ``Failed`` otherwise."""
    if not isinstance(value, str) or not value.startswith("media:") or value == "media:":
        raise _failed(NOT_A_REQUEST)
    rows = [line.split("\t") for line in lines[1:] if line.split("\t", 1)[0] == value[len("media:"):]]
    if len(rows) != 1 or len(rows[0]) != 4 or not _SHA256.fullmatch(rows[0][1]) or not _SIZE.fullmatch(rows[0][2]):
        raise _failed(NOT_A_REQUEST)
    return rows[0][1], int(rows[0][2])


def _request(call):
    """The upload request of the job's two inputs, with the ``sha256`` and ``bytes`` of its ``video`` row, or
    ``Failed`` before any request."""
    inputs = call["inputs"]
    if len(inputs) != 2 or inputs[1]["path"] != MANIFEST:
        raise _failed(NOT_A_REQUEST)
    try:
        request = json.loads(inputs[0]["text"])
    except (KeyError, TypeError, ValueError):
        raise _failed(NOT_A_REQUEST) from None
    if not isinstance(request, dict) or sorted(request) != KEYS:
        raise _failed(NOT_A_REQUEST)
    lines = (inputs[1]["text"] or "").splitlines()
    if not lines or lines[0] != MANIFEST_HEADER:
        raise _failed(NOT_A_REQUEST)
    sha256, size = _row(lines, request["video"])
    _row(lines, request["captions"])
    link = request["link"]
    if (not isinstance(link, str) or media.base(link) is None or not link.endswith("/" + sha256)
            or not isinstance(request["title"], str) or not request["title"].strip()
            or not isinstance(request["description"], str)):
        raise _failed(NOT_A_REQUEST)
    return {"bytes": size, "description": request["description"], "link": link, "sha256": sha256,
            "title": request["title"]}


def _key(path):
    """``(client_id, client_secret, refresh_token)`` from the key file, or ``Down``."""
    try:
        with open(path, "rb") as handle:
            text = handle.read().decode("utf-8")
    except (OSError, TypeError, UnicodeDecodeError):
        raise Down() from None
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    lines = [line.strip() for line in lines]
    if len(lines) != 3 or not all(lines):
        raise Down()
    return tuple(lines)


def _send(opener, request):
    """``(status, headers, response)`` of one request; an error status is answered, not raised.

    ``response`` is open; the caller closes it. A request that gets no answer raises ``OSError``.
    """
    try:
        response = opener.open(request, timeout=TIMEOUT)
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error
    return response.status, response.headers, response


def _json(opener, request):
    """``(status, headers, body)`` with the body parsed as JSON (None when it is not); ``OSError`` without an answer."""
    status, headers, response = _send(opener, request)
    try:
        text = response.read()
    finally:
        response.close()
    try:
        body = json.loads(text) if text else None
    except ValueError:
        body = None
    return status, headers, body


def _fetch(opener, request, path):
    """Get the file at the request's link into ``path``, or ``Failed``."""
    try:
        status, _, response = _send(opener, urllib.request.Request(request["link"], method="GET"))
    except (OSError, ValueError):
        raise _failed(media.LINK_UNREACHABLE) from None
    hasher, size, limit = hashlib.sha256(), 0, request["bytes"] + 1
    try:
        if status != 200:
            raise _failed(media.LINK_UNREACHABLE)
        with open(path, "wb") as handle:
            while size < limit:
                piece = response.read(min(CHUNK, limit - size))
                if not piece:
                    break
                handle.write(piece)
                hasher.update(piece)
                size += len(piece)
    except (OSError, ValueError):
        raise _failed(media.LINK_UNREACHABLE) from None
    finally:
        response.close()
    if size != request["bytes"] or hasher.hexdigest() != request["sha256"]:
        raise _failed(media.OTHER_BYTES_AT_LINK)


def _token(opener, key):
    client_id, client_secret, refresh_token = key
    form = urllib.parse.urlencode({"client_id": client_id, "client_secret": client_secret,
                                   "grant_type": "refresh_token", "refresh_token": refresh_token}).encode()
    request = urllib.request.Request(TOKEN_URL, data=form, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        status, _, body = _json(opener, request)
    except (OSError, ValueError):
        raise Down() from None
    token = body.get("access_token") if isinstance(body, dict) else None
    if status != 200 or not isinstance(token, str) or not token:
        raise Down()
    return token


def _start(opener, auth, request):
    """The upload URL of a new resumable upload, or ``Failed``."""
    metadata = {"snippet": {"description": request["description"], "title": request["title"]},
                "status": {"containsSyntheticMedia": True, "privacyStatus": "private"}}
    headers = {"Authorization": auth, "Content-Type": "application/json; charset=UTF-8",
               "X-Upload-Content-Length": str(request["bytes"]), "X-Upload-Content-Type": VIDEO_TYPE}
    try:
        status, headers, _ = _json(opener, urllib.request.Request(
            UPLOAD_URL, data=json.dumps(metadata, sort_keys=True).encode(), method="POST", headers=headers))
    except (OSError, ValueError):
        raise _failed(UPLOAD_REFUSED) from None
    location = headers.get("Location") if headers is not None else None
    if status != 200 or not location:
        raise _failed(UPLOAD_REFUSED)
    return location


def _put(opener, auth, location, request, path):
    """The video resource the platform answered the upload with, or ``Failed``."""
    headers = {"Authorization": auth, "Content-Type": VIDEO_TYPE, "Content-Length": str(request["bytes"])}
    try:
        with open(path, "rb") as handle:
            status, _, body = _json(opener, urllib.request.Request(location, data=handle, method="PUT",
                                                                   headers=headers))
    except (OSError, ValueError):
        raise _failed(UPLOAD_FAILED) from None
    if (status not in (200, 201) or not isinstance(body, dict) or not isinstance(body.get("id"), str)
            or not isinstance(body.get("status"), dict)
            or body["status"].get("privacyStatus") not in VISIBILITIES):
        raise _failed(UPLOAD_FAILED)
    return body


def _read(opener, auth, video_id):
    """The ``processingStatus`` of one read, None where the read does not give it."""
    request = urllib.request.Request(READ_URL + urllib.parse.quote(video_id, safe=""), method="GET",
                                     headers={"Authorization": auth})
    try:
        status, _, body = _json(opener, request)
    except (OSError, ValueError):
        return None
    items = body.get("items") if status == 200 and isinstance(body, dict) else None
    item = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
    details = item.get("processingDetails") if isinstance(item.get("processingDetails"), dict) else {}
    return details.get("processingStatus")


def run(call):
    request = _request(call)
    key = _key(call["secrets"][RESOURCE])
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "video")
        _fetch(opener, request, path)
        auth = "Bearer " + _token(opener, key)
        location = _start(opener, auth, request)
        video = _put(opener, auth, location, request, path)
    processing = _read(opener, auth, video["id"])
    disclosure = video["status"].get("containsSyntheticMedia")
    post = {"captions": True, "fields": {DISCLOSURE: disclosure} if isinstance(disclosure, bool) else {},
            "job": call["hash"], "platform": "youtube", "post_id": video["id"], "processed": processing == "succeeded",
            "read_at": production.now(), "sha256": request["sha256"], "visibility": video["status"]["privacyStatus"]}
    with open(os.path.join(call["record"], RECORD), "w", encoding="utf-8") as handle:
        handle.write(json.dumps(post, indent=1, sort_keys=True) + "\n")
    return {"usd": 0, "compute_min": 0}
