"""youtube-public: switch one private, processed, disclosed YouTube video to public, the release's own job.

The job's first input is the switch request, one JSON object with exactly the keys ``post_id`` (the video's
id) and ``sha256`` (the hash of the file the upload sent). The key file of ``platform.youtube`` is the one of
``youtube-upload``: three lines, client id, client secret, refresh token.

The requests, in this order, all through one opener that reads no proxy from the environment and follows
no redirect: the token refresh, one read of the video's status and processing, and the ``PUT`` of its status
with ``"privacyStatus": "public"`` and every other status field as read. The switch is made only when the
read shows the video private, processed and with the synthetic-media disclosure set. No ``publishAt``: the
switch is made when the job runs.

The outputs are ``post.json`` (the video's id, its visibility and disclosure as the switch answered, its
processing status as read before it and the request's sha256), written before the visibility and disclosure
are checked, and then ``release.json`` (``kit/lib/release.py``). Nothing of the key file or the access token
is in an output or a reason.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from kit.lib.tools import Down, Failed

RESOURCE = "platform.youtube"
NOT_A_REQUEST = "request is not a switch request"
NOT_FOUND = "video not found"
NOT_PRIVATE = "video is not private"
NOT_PROCESSED = "video is not processed"
NO_DISCLOSURE = "disclosure not set"
REFUSED = "switch refused"
NOT_PUBLIC = "video is not public"
REASONS = (NOT_A_REQUEST, NOT_FOUND, NOT_PRIVATE, NOT_PROCESSED, NO_DISCLOSURE, REFUSED, NOT_PUBLIC)

TIMEOUT = 600
TOKEN_URL = "https://oauth2.googleapis.com/token"
READ_URL = "https://www.googleapis.com/youtube/v3/videos?part=status,processingDetails&id="
UPDATE_URL = "https://www.googleapis.com/youtube/v3/videos?part=status"
KEPT = ("containsSyntheticMedia", "embeddable", "license", "publicStatsViewable", "selfDeclaredMadeForKids")
POST, RELEASE = "post.json", "release.json"
KEYS = ["post_id", "sha256"]
HEX = set("0123456789abcdef")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _failed(reason):
    return Failed(reason, usd=0, compute_min=0)


def _request(call):
    """The switch request of the job's first input, or ``Failed`` before any request."""
    try:
        request = json.loads(call["inputs"][0]["text"])
    except (IndexError, KeyError, TypeError, ValueError):
        raise _failed(NOT_A_REQUEST) from None
    if not isinstance(request, dict) or sorted(request) != KEYS:
        raise _failed(NOT_A_REQUEST)
    post_id, sha256 = request["post_id"], request["sha256"]
    if (not isinstance(post_id, str) or not post_id
            or not isinstance(sha256, str) or len(sha256) != 64 or not set(sha256) <= HEX):
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


def _json(opener, request):
    """``(status, body)`` with the body parsed as JSON (None when it is not); ``OSError`` without an answer."""
    try:
        response = opener.open(request, timeout=TIMEOUT)
        status = response.status
    except urllib.error.HTTPError as error:
        response, status = error, error.code
    try:
        text = response.read()
    finally:
        response.close()
    try:
        body = json.loads(text) if text else None
    except ValueError:
        body = None
    return status, body


def _token(opener, key):
    client_id, client_secret, refresh_token = key
    form = urllib.parse.urlencode({"client_id": client_id, "client_secret": client_secret,
                                   "grant_type": "refresh_token", "refresh_token": refresh_token}).encode()
    request = urllib.request.Request(TOKEN_URL, data=form, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        status, body = _json(opener, request)
    except (OSError, ValueError):
        raise Down() from None
    token = body.get("access_token") if isinstance(body, dict) else None
    if status != 200 or not isinstance(token, str) or not token:
        raise Down()
    return token


def _read(opener, auth, post_id):
    """``(status, processing)`` of the video as read, or ``Failed`` when it is not ready to switch."""
    request = urllib.request.Request(READ_URL + urllib.parse.quote(post_id, safe=""), method="GET",
                                     headers={"Authorization": auth})
    try:
        status, body = _json(opener, request)
    except (OSError, ValueError):
        raise _failed(NOT_FOUND) from None
    items = body.get("items") if status == 200 and isinstance(body, dict) else None
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise _failed(NOT_FOUND)
    item = items[0]
    video = item.get("status")
    if item.get("id") != post_id or not isinstance(video, dict):
        raise _failed(NOT_FOUND)
    details = item.get("processingDetails") if isinstance(item.get("processingDetails"), dict) else {}
    processing = details.get("processingStatus")
    if video.get("privacyStatus") != "private":
        raise _failed(NOT_PRIVATE)
    if processing != "succeeded":
        raise _failed(NOT_PROCESSED)
    if video.get("containsSyntheticMedia") is not True:
        raise _failed(NO_DISCLOSURE)
    return video, processing


def _switch(opener, auth, post_id, video):
    """The video's status the platform answered the switch with, or ``Failed``."""
    new = {name: video[name] for name in KEPT if name in video}
    new["privacyStatus"] = "public"
    data = json.dumps({"id": post_id, "status": new}, sort_keys=True).encode()
    request = urllib.request.Request(UPDATE_URL, data=data, method="PUT",
                                     headers={"Authorization": auth,
                                              "Content-Type": "application/json; charset=UTF-8"})
    try:
        status, body = _json(opener, request)
    except (OSError, ValueError):
        raise _failed(REFUSED) from None
    if (status != 200 or not isinstance(body, dict) or body.get("id") != post_id
            or not isinstance(body.get("status"), dict)):
        raise _failed(REFUSED)
    return body["status"]


def _write(call, name, data):
    with open(os.path.join(call["out"], name), "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, indent=1, sort_keys=True) + "\n")


def run(call):
    request = _request(call)
    key = _key(call["secrets"][RESOURCE])
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    auth = "Bearer " + _token(opener, key)
    post_id = request["post_id"]
    video, processing = _read(opener, auth, post_id)
    after = _switch(opener, auth, post_id, video)
    post = {"disclosure": after.get("containsSyntheticMedia"), "platform": "youtube", "post_id": post_id,
            "processing": processing, "sha256": request["sha256"], "visibility": after.get("privacyStatus")}
    _write(call, POST, post)
    if post["visibility"] != "public":
        raise _failed(NOT_PUBLIC)
    if post["disclosure"] is not True:
        raise _failed(NO_DISCLOSURE)
    _write(call, RELEASE, {"job": call["hash"], "platform": "youtube", "post_id": post_id,
                           "sha256": request["sha256"], "ticket": call["ticket"]})
    return {"usd": 0, "compute_min": 0}
