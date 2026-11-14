"""Upload one video to TikTok through the Content Posting API, in the mode the production chose.

The job has two inputs, in this order: the upload request and ``media/manifest.tsv``. The request is a JSON
object with exactly the keys ``captions``, ``link``, ``title`` and ``video``: ``video`` and ``captions`` are
each ``media:<id>``, an id with exactly one row in the manifest, and ``link`` is a media store public link
ending with ``/`` and the sha256 of the ``video`` row, sent as ``video_url``. ``captions`` is true because the
captions are burned into the uploaded file; no caption track is uploaded. TikTok pulls the video from that
link (``PULL_FROM_URL``); the tool never holds the video's bytes. Setting ``TIKTOK_POSTING`` picks the one
request:

- ``inbox``: the video goes to the account's inbox as a draft. That endpoint takes no ``post_info``, so
  no request can set ``is_aigc``; the owner sets the AI-generated content label when finishing the draft.
- ``private``: a post visible to the owner only (``SELF_ONLY``), with ``is_aigc`` true.
- ``direct``: a public post (``PUBLIC_TO_EVERYONE``), with ``is_aigc`` true.

The key file ``platform.tiktok`` is a JSON object holding the string ``access_token``; other keys are
ignored. The token is sent only in the ``Authorization`` header. The tool writes no output. On success it
records ``post.json`` in ``call["record"]``, with the keys of ``kit.lib.posts.KEYS``: the ``publish_id`` as
the post id, visibility ``private`` (``inbox``, ``private``) or ``public`` (``direct``), never processed (no
status is read), ``post_info.is_aigc`` among its fields when it was sent, the sha256 of the ``video`` row,
the job's hash and the time of the answer. Posting is free.
"""

import json
import re
import urllib.error
import urllib.request

from kit.lib import production
from kit.lib.tools import Down, Failed

RESOURCE = "platform.tiktok"
BAD_REQUEST = "request is not {captions, link, title, video}"
NOT_PRIVATE = "account is not private"
NO_PRIVACY = "privacy level not offered"
TOO_MANY = "too many posts"
NOT_VERIFIED = "video url not verified"
UNREACHABLE = "platform unreachable"
REFUSED = "upload refused"
REASONS = (BAD_REQUEST, NOT_PRIVATE, NO_PRIVACY, TOO_MANY, NOT_VERIFIED, UNREACHABLE, REFUSED)

INBOX_URL = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
POST_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
PRIVACY = {"inbox": None, "private": "SELF_ONLY", "direct": "PUBLIC_TO_EVERYONE"}
VISIBILITY = {"inbox": "private", "private": "private", "direct": "public"}
KEYS = ["captions", "link", "title", "video"]
MANIFEST = "media/manifest.tsv"
MANIFEST_HEADER = "id\tsha256\tbytes\tsource"
RECORD = "post.json"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SIZE = re.compile(r"[1-9][0-9]*")
VIDEO_URL = re.compile(r"https://[^\s?#/]+(/[^\s?#]*)?/[0-9a-f]{64}")
TITLE_MAX = 2200
TIMEOUT = 60
DOWN_CODES = ("access_token_invalid", "scope_not_authorized")
CODE_REASONS = {
    "unaudited_client_can_only_post_to_private_accounts": NOT_PRIVATE,
    "privacy_level_option_mismatch": NO_PRIVACY,
    "spam_risk_too_many_posts": TOO_MANY,
    "spam_risk_too_many_pending_share": TOO_MANY,
    "rate_limit_exceeded": TOO_MANY,
    "url_ownership_unverified": NOT_VERIFIED,
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Follow no redirect: a 3xx answer raises ``HTTPError``."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _fail(reason):
    raise Failed(reason, usd=0, compute_min=0)


def _row(lines, value):
    """The sha256 of the one manifest row of ``value``, ``media:<id>``; ``Failed`` otherwise."""
    if not isinstance(value, str) or not value.startswith("media:") or value == "media:":
        _fail(BAD_REQUEST)
    rows = [line.split("\t") for line in lines[1:] if line.split("\t", 1)[0] == value[len("media:"):]]
    if len(rows) != 1 or len(rows[0]) != 4 or not _SHA256.fullmatch(rows[0][1]) or not _SIZE.fullmatch(rows[0][2]):
        _fail(BAD_REQUEST)
    return rows[0][1]


def _request(inputs):
    """``(link, title, sha256)`` of the job's two inputs, ``sha256`` that of the ``video`` row; ``Failed``
    when they are not the upload request and the manifest."""
    if len(inputs) != 2 or inputs[1]["path"] != MANIFEST:
        _fail(BAD_REQUEST)
    try:
        data = json.loads(inputs[0]["text"])
    except (TypeError, ValueError):
        _fail(BAD_REQUEST)
    if not isinstance(data, dict) or sorted(data) != KEYS:
        _fail(BAD_REQUEST)
    lines = (inputs[1]["text"] or "").splitlines()
    if not lines or lines[0] != MANIFEST_HEADER:
        _fail(BAD_REQUEST)
    sha256 = _row(lines, data["video"])
    _row(lines, data["captions"])
    link, title = data["link"], data["title"]
    if (not isinstance(link, str) or not VIDEO_URL.fullmatch(link) or not link.endswith("/" + sha256)
            or not isinstance(title, str) or not title.strip()
            or len(title.encode("utf-16-le")) // 2 > TITLE_MAX):
        _fail(BAD_REQUEST)
    return link, title, sha256


def _token(path):
    """The access token in the key file at ``path``; ``Down`` when there is none."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        raise Down() from None
    token = data.get("access_token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token:
        raise Down()
    return token


def _answer(response):
    """The JSON object of an answer's body, or None."""
    try:
        data = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def run(call):
    video_url, title, sha256 = _request(call["inputs"])
    token = _token(call["secrets"][RESOURCE])
    mode = call["settings"]["TIKTOK_POSTING"]
    privacy = PRIVACY[mode]
    source = {"source": "PULL_FROM_URL", "video_url": video_url}
    if privacy is None:
        url, body, fields = INBOX_URL, {"source_info": source}, {}
    else:
        url, fields = POST_URL, {"post_info.is_aigc": True}
        body = {"post_info": {"title": title, "privacy_level": privacy, "is_aigc": True}, "source_info": source}
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            answer = _answer(response)
    except urllib.error.HTTPError as error:
        answer = _answer(error)
    except (urllib.error.URLError, OSError):
        _fail(UNREACHABLE)
    error = answer.get("error") if answer is not None else None
    code = error.get("code") if isinstance(error, dict) else None
    data = answer.get("data") if answer is not None else None
    publish_id = data.get("publish_id") if isinstance(data, dict) else None
    if code == "ok" and isinstance(publish_id, str) and publish_id:
        post = {"captions": True, "fields": fields, "job": call["hash"], "platform": "tiktok", "post_id": publish_id,
                "processed": False, "read_at": production.now(), "sha256": sha256, "visibility": VISIBILITY[mode]}
        with open(f"{call['record']}/{RECORD}", "w", encoding="utf-8") as handle:
            handle.write(json.dumps(post, indent=1, sort_keys=True) + "\n")
        return {"usd": 0, "compute_min": 0}
    if code in DOWN_CODES:
        raise Down()
    _fail(CODE_REASONS.get(code, REFUSED) if isinstance(code, str) else REFUSED)
