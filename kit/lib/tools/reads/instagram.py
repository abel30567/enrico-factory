"""The Instagram read: the Reels of a watch list of professional accounts, by Business Discovery, read only.

One request per watched account, accounts sorted: `GET <api>/<INSTAGRAM_USER_ID>` with the field
`business_discovery.username(<account>)`, its followers and its last 20 media, on one page. Only `REELS` are
videos; a Reel already read for an earlier account is not added again. A missing count is null, never zero.
An account that is not professional, or gives no data (as an age-gated one), is listed in `unreadable` with
why, and adds no video. Hashtags are checked and not read. The key file holds the access token.
"""

import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from kit.lib import media, resources
from kit.lib.tools import Down, Failed

PLATFORM = "instagram"
RESOURCE = "external.instagram"
API = "https://graph.facebook.com/v27.0"
BUDGET = ("INSTAGRAM_SEARCHES_PER_DAY", 24)
STORED = ("unreadable",)
NOT_A_NUMBER = "INSTAGRAM_USER_ID is not a number"
NOT_A_TOKEN = "the key file is not a token"
REJECTED = "token rejected"
LIMITED = "rate limited"
UNEXPECTED = "unexpected answer"
REASONS = (NOT_A_NUMBER, NOT_A_TOKEN, REJECTED, LIMITED, UNEXPECTED)

MEDIA_LIMIT = 20
MOST_ACCOUNTS = 25
NOT_PROFESSIONAL = "not a professional account, or no such account"
NO_DATA = "no data returned, as for an age-gated account"
ACCOUNT = re.compile(r"[A-Za-z0-9._]{1,30}")
DIGITS = re.compile(r"[0-9]+")
TOKEN = re.compile(r"\S+")


def _accounts(value):
    return (isinstance(value, list) and 0 < len(value) <= MOST_ACCOUNTS
            and all(isinstance(a, str) and ACCOUNT.fullmatch(a) for a in value)
            and len(set(value)) == len(value))


def _hashtags(value):
    return isinstance(value, list) and all(isinstance(h, str) and h != "" for h in value)


WATCH = {"accounts": _accounts, "hashtags": _hashtags}


def _whole(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _count(value):
    return value if _whole(value) and value >= 0 else None


def _published(value):
    try:
        moment = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
    except (TypeError, ValueError):
        return None
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _token(path):
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read().strip()
    except (OSError, UnicodeDecodeError):
        raise Failed(NOT_A_TOKEN) from None
    if not TOKEN.fullmatch(text):
        raise Failed(NOT_A_TOKEN)
    return text


def _fields(account):
    return (f"business_discovery.username({account}){{username,followers_count,media.limit({MEDIA_LIMIT})"
            "{id,media_product_type,permalink,timestamp,view_count,like_count,comments_count}}")


def _get(api, user, token, account):
    """``(status, body)`` of the account's one request; ``Down`` when the platform cannot be reached."""
    query = urllib.parse.urlencode(sorted({"access_token": token, "fields": _fields(account)}.items()))
    request = urllib.request.Request(f"{api}/{user}?{query}")
    try:
        with media._OPENER.open(request, timeout=resources.TIMEOUT) as answer:
            return answer.status, answer.read()
    except urllib.error.HTTPError as error:
        try:
            return error.code, error.read()
        except (OSError, http.client.HTTPException):
            raise Down() from None
        finally:
            error.close()
    except (OSError, http.client.HTTPException):
        raise Down() from None


def _error(error):
    """The reason an ``error`` object fails the read, or None for an account that is not professional."""
    code, subcode = error.get("code"), error.get("error_subcode")
    if _whole(code) and code == 110 and _whole(subcode) and subcode == 2207013:
        return None
    if _whole(code) and code in (102, 190):
        return REJECTED
    if _whole(code) and code in (4, 17):
        return LIMITED
    return UNEXPECTED


def _account(status, body):
    """The account's ``business_discovery`` object, or the reason it is unreadable (a string)."""
    try:
        doc = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise Failed(UNEXPECTED) from None
    if not isinstance(doc, dict):
        raise Failed(UNEXPECTED)
    error = doc.get("error")
    if isinstance(error, dict):
        reason = _error(error)
        if reason is not None:
            raise Failed(reason)
        return NOT_PROFESSIONAL
    if status != 200 or "error" in doc:
        raise Failed(UNEXPECTED)
    found = doc.get("business_discovery")
    if found is None:
        return NO_DATA
    if not isinstance(found, dict):
        raise Failed(UNEXPECTED)
    return found


def _reels(found):
    """The account's Reels, in the answer's order."""
    if "media" not in found:
        return []
    data = found["media"].get("data") if isinstance(found["media"], dict) else None
    if not isinstance(data, list) or not all(isinstance(m, dict) for m in data):
        raise Failed(UNEXPECTED)
    reels = [m for m in data if m.get("media_product_type") == "REELS"]
    for reel in reels:
        if not all(isinstance(reel.get(k), str) and TOKEN.fullmatch(reel[k]) for k in ("id", "permalink")):
            raise Failed(UNEXPECTED)
    return reels


def read(call):
    user = call["settings"]["INSTAGRAM_USER_ID"]
    if not (isinstance(user, str) and DIGITS.fullmatch(user)):
        raise Failed(NOT_A_NUMBER)
    token = _token(call["secrets"][RESOURCE])
    videos, unreadable, seen = [], [], set()
    for account in sorted(call["topic"]["accounts"]):
        found = _account(*_get(call["api"], user, token, account))
        if isinstance(found, str):
            unreadable.append({"account": account, "why": found})
            continue
        reels = _reels(found)
        views = [_count(reel.get("view_count")) for reel in reels]
        for place, reel in enumerate(reels):
            if reel["id"] in seen:
                continue
            seen.add(reel["id"])
            videos.append({
                "account": account,
                "author_views": [v for at, v in enumerate(views) if at != place and v is not None],
                "comments": _count(reel.get("comments_count")),
                "followers": _count(found.get("followers_count")),
                "id": reel["id"],
                "likes": _count(reel.get("like_count")),
                "link": reel["permalink"],
                "published_at": _published(reel.get("timestamp")),
                "views": views[place],
            })
    return {"videos": videos, "unreadable": unreadable}
