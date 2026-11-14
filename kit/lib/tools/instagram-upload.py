"""The tool `instagram-upload`: publish one Reel from the media store to the Instagram test account.

The job has two inputs, in this order: the request, a JSON object with exactly the keys ``caption`` (a
string), ``captions`` and ``video``, and ``media/manifest.tsv``. ``video`` and ``captions`` are each
``media:<id>``, an id with exactly one row in the manifest.
``captions`` is true because the captions are burned into the uploaded file; no caption track is uploaded.
The Reel's bytes are in the media store already; the platform fetches them from their public link.

The tool checks the account, the token file, the public base and the API, then the request, then makes
its calls: it gets the public link, creates a Reels container with ``is_ai_generated=true``, polls the
container's status at most ``POLLS`` times, ``WAIT`` seconds apart, and publishes it. It posts to
``INSTAGRAM_TEST_ACCOUNT`` and to no other account. It writes no output: it records ``post.json`` in
``call["record"]``, with the keys of ``kit.lib.posts.KEYS``: the published media id as the post id,
visibility ``public`` (Instagram has no private upload), processed (the container was ``FINISHED`` and
published), the field ``is_ai_generated`` true, the sha256 of the ``video`` row, the job's hash and the time
of the publish answer.

The token file holds one line, the access token, sent only as ``Authorization: Bearer <token>``. An
answer with ``error.code`` 190 (the token is not valid) is ``Down``. The environment variable
``FACTORY_INSTAGRAM_API``, when not empty, replaces ``API``; it must be an ``http`` URL on a loopback host.
"""

import http.client
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from kit.lib import media, production
from kit.lib.tools import Down, Failed

RESOURCE = "platform.instagram"
API = "https://graph.facebook.com/v27.0"
POLLS = 5
WAIT = 60
TIMEOUT = 60

BAD_REQUEST = "bad request"
NO_REEL = "public link does not serve the reel"
UNREACHABLE = "platform unreachable"
REFUSED = "platform refused"
CONTAINER_FAILED = "container failed"
NOT_READY = "container not ready"
REASONS = (BAD_REQUEST, NO_REEL, UNREACHABLE, REFUSED, CONTAINER_FAILED, NOT_READY)

SECRET = "platform.instagram"
ACCOUNT = "INSTAGRAM_TEST_ACCOUNT"
SEAM = "FACTORY_INSTAGRAM_API"
MANIFEST = "media/manifest.tsv"
MANIFEST_HEADER = "id\tsha256\tbytes\tsource"
KEYS = ["caption", "captions", "video"]
RECORD = "post.json"
INVALID_TOKEN = 190
_DIGITS = re.compile(r"[0-9]+")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SIZE = re.compile(r"[1-9][0-9]*")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _api():
    """``API``, or the loopback URL of ``FACTORY_INSTAGRAM_API`` when it is set; ``Down`` for any other value."""
    value = os.environ.get(SEAM, "")
    if not value:
        return API
    found = media.base(value)
    if found is None or urllib.parse.urlsplit(value).scheme != "http":
        raise Down()
    return found


def _token(path):
    """The token: the file's one line, with no whitespace in it; ``Down`` otherwise."""
    try:
        with open(path, "rb") as handle:
            text = handle.read().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        raise Down() from None
    if text.endswith("\n"):
        text = text[:-1]
    if not text or any(c.isspace() for c in text):
        raise Down()
    return text


def _usable(call):
    """``(account, token, api)``, after checking the public base; ``Down`` when one is not usable."""
    settings = call["settings"]
    account = settings.get(ACCOUNT, "")
    if not _DIGITS.fullmatch(account):
        raise Down()
    token = _token(call["secrets"][SECRET])
    try:
        media.public_base(settings)
    except media.Refused:
        raise Down() from None
    return account, token, _api()


def _row(lines, value):
    """``(sha256, size)`` of the one manifest row of ``value``, ``media:<id>``; ``Failed(BAD_REQUEST)`` otherwise."""
    if not isinstance(value, str) or not value.startswith("media:") or value == "media:":
        raise Failed(BAD_REQUEST)
    rows = [line.split("\t") for line in lines[1:] if line.split("\t", 1)[0] == value[len("media:"):]]
    if len(rows) != 1 or len(rows[0]) != 4 or not _SHA256.fullmatch(rows[0][1]) or not _SIZE.fullmatch(rows[0][2]):
        raise Failed(BAD_REQUEST)
    return rows[0][1], int(rows[0][2])


def _request(inputs):
    """``(caption, sha256, size)`` from the job's two inputs, of the ``video`` row; ``Failed(BAD_REQUEST)``
    otherwise."""
    if len(inputs) != 2 or inputs[1]["path"] != MANIFEST:
        raise Failed(BAD_REQUEST)
    try:
        request = json.loads(inputs[0]["text"])
    except (TypeError, ValueError):
        raise Failed(BAD_REQUEST) from None
    if not isinstance(request, dict) or sorted(request) != KEYS or not isinstance(request["caption"], str):
        raise Failed(BAD_REQUEST)
    lines = (inputs[1]["text"] or "").splitlines()
    if not lines or lines[0] != MANIFEST_HEADER:
        raise Failed(BAD_REQUEST)
    sha256, size = _row(lines, request["video"])
    _row(lines, request["captions"])
    return request["caption"], sha256, size


def _answer(url, token, form=None):
    """The JSON object of the answer to a GET, or to a POST of ``form``; ``Down`` on an invalid token."""
    headers = {"Authorization": "Bearer " + token}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(sorted(form.items())).encode("ascii")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=data, headers=headers, method="GET" if data is None else "POST")
    try:
        with _OPENER.open(request, timeout=TIMEOUT) as response:
            status, body = response.status, response.read()
    except urllib.error.HTTPError as error:
        try:
            status, body = error.code, error.read()
        except (OSError, http.client.HTTPException):
            status, body = error.code, b""
        finally:
            error.close()
    except (OSError, http.client.HTTPException, ValueError):
        raise Failed(UNREACHABLE) from None
    try:
        answer = json.loads(body.decode("utf-8"))
    except ValueError:
        answer = None
    if isinstance(answer, dict) and isinstance(answer.get("error"), dict):
        code = answer["error"].get("code")
        if code == INVALID_TOKEN and not isinstance(code, bool):
            raise Down()
    if status != 200 or not isinstance(answer, dict) or "error" in answer:
        raise Failed(REFUSED)
    return answer


def _id(answer):
    found = answer.get("id")
    if not isinstance(found, str) or not _DIGITS.fullmatch(found):
        raise Failed(REFUSED)
    return found


def _wait_for(api, container, token):
    """Return when the container is ``FINISHED``; ``Failed`` when it failed or is not ready in time."""
    for poll in range(POLLS):
        if poll:
            time.sleep(WAIT)
        status = _answer(f"{api}/{container}?fields=status_code", token).get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise Failed(CONTAINER_FAILED)
        if status != "IN_PROGRESS":
            raise Failed(REFUSED)
    raise Failed(NOT_READY)


def run(call):
    account, token, api = _usable(call)
    caption, sha256, size = _request(call["inputs"])
    settings = call["settings"]
    try:
        media.check(settings, sha256, size)
    except media.Failed:
        raise Failed(NO_REEL) from None
    container = _id(_answer(f"{api}/{account}/media", token, {
        "caption": caption, "is_ai_generated": "true", "media_type": "REELS",
        "video_url": media.link(settings, sha256)}))
    _wait_for(api, container, token)
    post = _id(_answer(f"{api}/{account}/media_publish", token, {"creation_id": container}))
    record = {"captions": True, "fields": {"is_ai_generated": True}, "job": call["hash"], "platform": "instagram",
              "post_id": post, "processed": True, "read_at": production.now(), "sha256": sha256,
              "visibility": "public"}
    with open(os.path.join(call["record"], RECORD), "w", encoding="utf-8") as handle:
        handle.write(json.dumps(record, indent=1, sort_keys=True) + "\n")
    return {"usd": 0, "compute_min": 0}
