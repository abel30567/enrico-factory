"""Upload one video to TikTok through the Content Posting API, in the mode the production chose.

The job's first input is the upload request, a JSON object with exactly the keys ``video_url`` (a media
store public link) and ``title``. TikTok pulls the video from that link (``PULL_FROM_URL``); the tool
never holds the video's bytes. Setting ``TIKTOK_POSTING`` picks the one request:

- ``inbox``: the video goes to the account's inbox as a draft. That endpoint takes no ``post_info``, so
  no request can set ``is_aigc``; the owner sets the AI-generated content label when finishing the draft.
- ``private``: a post visible to the owner only (``SELF_ONLY``), with ``is_aigc`` true.
- ``direct``: a public post (``PUBLIC_TO_EVERYONE``), with ``is_aigc`` true.

The key file ``platform.tiktok`` is a JSON object holding the string ``access_token``; other keys are
ignored. The token is sent only in the ``Authorization`` header. On success the tool writes ``post.json``
with the mode, the privacy level, the ``is_aigc`` sent and the ``publish_id``. Posting is free.
"""

import json
import re
import urllib.error
import urllib.request

from kit.lib.tools import Down, Failed

RESOURCE = "platform.tiktok"
BAD_REQUEST = "request is not {video_url, title}"
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


def _request(inputs):
    """``(video_url, title)`` of the first input; ``Failed`` when it is not the upload request."""
    try:
        data = json.loads(inputs[0]["text"])
    except (IndexError, TypeError, ValueError):
        _fail(BAD_REQUEST)
    if not isinstance(data, dict) or sorted(data) != ["title", "video_url"]:
        _fail(BAD_REQUEST)
    video_url, title = data["video_url"], data["title"]
    if (not isinstance(video_url, str) or not VIDEO_URL.fullmatch(video_url)
            or not isinstance(title, str) or not title.strip()
            or len(title.encode("utf-16-le")) // 2 > TITLE_MAX):
        _fail(BAD_REQUEST)
    return video_url, title


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
    video_url, title = _request(call["inputs"])
    token = _token(call["secrets"][RESOURCE])
    mode = call["settings"]["TIKTOK_POSTING"]
    privacy = PRIVACY[mode]
    source = {"source": "PULL_FROM_URL", "video_url": video_url}
    if privacy is None:
        url, body, is_aigc = INBOX_URL, {"source_info": source}, None
    else:
        url, is_aigc = POST_URL, True
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
        post = {"is_aigc": is_aigc, "mode": mode, "privacy_level": privacy, "publish_id": publish_id}
        with open(f"{call['out']}/post.json", "w", encoding="utf-8") as handle:
            handle.write(json.dumps(post, indent=1, sort_keys=True) + "\n")
        return {"usd": 0, "compute_min": 0}
    if code in DOWN_CODES:
        raise Down()
    _fail(CODE_REASONS.get(code, REFUSED) if isinstance(code, str) else REFUSED)
