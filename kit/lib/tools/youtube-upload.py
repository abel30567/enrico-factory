"""youtube-upload: upload one video to YouTube as a private video with the synthetic-media disclosure set.

The job's first input is the upload request, one JSON object with exactly the keys ``link`` (the video's
public link in the media store), ``sha256``, ``bytes``, ``title`` and ``description``. The key file of
``platform.youtube`` holds three lines: client id, client secret, refresh token.

The requests, in this order, all through one opener that reads no proxy from the environment and follows
no redirect: a ``GET`` of the public link into a temporary file, the token refresh, the resumable upload's
``POST``, the ``PUT`` of the file, and one read of the video's status. Every upload is
``"privacyStatus": "private"`` and ``"containsSyntheticMedia": true``; the request cannot change that.

The one output is ``post.json``: the video's id, its visibility and disclosure as the platform answered,
its upload and processing status, and the file's sha256 and bytes. It is written before the visibility and
disclosure are checked, so a failed check keeps the id. Nothing of the key file or the access token is in
an output or a reason.
"""

import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from kit.lib import media
from kit.lib.tools import Down, Failed

RESOURCE = "platform.youtube"
NOT_A_REQUEST = "request is not an upload request"
UPLOAD_REFUSED = "upload refused"
UPLOAD_FAILED = "upload failed"
NOT_PRIVATE = "video is not private"
NO_DISCLOSURE = "disclosure not set"
REASONS = (NOT_A_REQUEST, media.LINK_UNREACHABLE, media.OTHER_BYTES_AT_LINK, UPLOAD_REFUSED, UPLOAD_FAILED,
           NOT_PRIVATE, NO_DISCLOSURE)

TIMEOUT = 600
CHUNK = 1024 * 1024
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
READ_URL = "https://www.googleapis.com/youtube/v3/videos?part=status,processingDetails&id="
VIDEO_TYPE = "video/*"
OUTPUT = "post.json"
KEYS = ["bytes", "description", "link", "sha256", "title"]
HEX = set("0123456789abcdef")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _failed(reason):
    return Failed(reason, usd=0, compute_min=0)


def _request(call):
    """The upload request of the job's first input, or ``Failed`` before any request."""
    inputs = call["inputs"]
    try:
        request = json.loads(inputs[0]["text"])
    except (IndexError, KeyError, TypeError, ValueError):
        raise _failed(NOT_A_REQUEST) from None
    if not isinstance(request, dict) or sorted(request) != KEYS:
        raise _failed(NOT_A_REQUEST)
    sha256, size, link = request["sha256"], request["bytes"], request["link"]
    if (not isinstance(sha256, str) or len(sha256) != 64 or not set(sha256) <= HEX
            or not isinstance(size, int) or isinstance(size, bool) or size < 1
            or not isinstance(link, str) or media.base(link) is None or not link.endswith("/" + sha256)
            or not isinstance(request["title"], str) or not request["title"].strip()
            or not isinstance(request["description"], str)):
        raise _failed(NOT_A_REQUEST)
    return request


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
            or not isinstance(body.get("status"), dict)):
        raise _failed(UPLOAD_FAILED)
    return body


def _read(opener, auth, video_id):
    """``(upload_status, processing_status)`` of one read, each None where the read does not give it."""
    request = urllib.request.Request(READ_URL + urllib.parse.quote(video_id, safe=""), method="GET",
                                     headers={"Authorization": auth})
    try:
        status, _, body = _json(opener, request)
    except (OSError, ValueError):
        return None, None
    items = body.get("items") if status == 200 and isinstance(body, dict) else None
    item = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
    video_status = item.get("status") if isinstance(item.get("status"), dict) else {}
    details = item.get("processingDetails") if isinstance(item.get("processingDetails"), dict) else {}
    return video_status.get("uploadStatus"), details.get("processingStatus")


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
    upload_status, processing = _read(opener, auth, video["id"])
    if upload_status is None:
        upload_status = video["status"].get("uploadStatus")
    post = {"bytes": request["bytes"], "disclosure": video["status"].get("containsSyntheticMedia"),
            "platform": "youtube", "post_id": video["id"], "processing": processing, "sha256": request["sha256"],
            "upload_status": upload_status, "visibility": video["status"].get("privacyStatus")}
    with open(os.path.join(call["out"], OUTPUT), "w", encoding="utf-8") as handle:
        handle.write(json.dumps(post, indent=1, sort_keys=True) + "\n")
    if post["visibility"] != "private":
        raise _failed(NOT_PRIVATE)
    if post["disclosure"] is not True:
        raise _failed(NO_DISCLOSURE)
    return {"usd": 0, "compute_min": 0}
