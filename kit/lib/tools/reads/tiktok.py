"""The TikTok read: the hashtags of one topic and their top videos, from the TikTok Discovery API, read only.

One `trending_list` gives the rising hashtags of the country over the last 7 days, and so the ids of the
topic's hashtags that are rising; then `video_list`, for at most 10 ids a request, gives each one's top
videos in TikTok's own order. The answers hold no count of a video, so a stored video has only its id,
link and order; the rank of each hashtag is stored beside the videos.

The key file holds the access token on its first line, sent as the header `Access-Token`.
"""

import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from kit.lib import media, resources
from kit.lib.tools import Down, Failed

PLATFORM = "tiktok"
RESOURCE = "external.tiktok"
API = "https://business-api.tiktok.com/open_api/v1.3"
BUDGET = ("TIKTOK_SEARCHES_PER_DAY", 100)
STORED = ("hashtags",)
NOT_A_SETTING = "a setting of external.tiktok is not valid"
ERROR_CODE = "tiktok answered with an error code"
NOT_A_LIST = "tiktok answered something that is not a discovery list"
REASONS = (NOT_A_SETTING, ERROR_CODE, NOT_A_LIST)

DATE_RANGE = "7DAY"
PER_CALL = 10
DOWN = (401, 403, 429)
HASHTAG = re.compile(r"[^\s#]+")
ADVERTISER = re.compile(r"[0-9]+")
COUNTRY = re.compile(r"[A-Z]{2}")
TOKEN = re.compile(r"\S+")


def _clean(value):
    """``value`` with one leading ``#`` removed, casefolded."""
    return (value[1:] if value.startswith("#") else value).casefold()


def _hashtags(value):
    return (isinstance(value, list) and len(value) > 0
            and all(isinstance(h, str) and HASHTAG.fullmatch(_clean(h)) is not None for h in value))


WATCH = {"hashtags": _hashtags}


def _token(path):
    """The first line of the key file, stripped; ``Down`` when there is none."""
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except (OSError, UnicodeDecodeError):
        raise Down() from None
    token = lines[0].strip() if lines else ""
    if token == "":
        raise Down()
    return token


def _whole(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _link(video):
    for key in ("share_url", "embed_url"):
        value = video.get(key)
        if isinstance(value, str) and TOKEN.fullmatch(value):
            return value
    return None


class _Client:
    def __init__(self, api, token, settings):
        self.api, self.token = api, token
        self.params = {"advertiser_id": settings["TIKTOK_ADVERTISER_ID"], "country_code": settings["TIKTOK_COUNTRY"],
                       "date_range": DATE_RANGE, "discovery_type": "HASHTAG"}

    def get(self, endpoint, params=None):
        """The answer's ``data.list``, each item an object."""
        query = urllib.parse.urlencode(sorted({**self.params, **(params or {})}.items()))
        request = urllib.request.Request(f"{self.api}/discovery/{endpoint}/?{query}",
                                         headers={"Access-Token": self.token})
        try:
            with media._OPENER.open(request, timeout=resources.TIMEOUT) as answer:
                body = answer.read()
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            if status in DOWN or status >= 500:
                raise Down() from None
            raise Failed(ERROR_CODE) from None
        except (OSError, http.client.HTTPException):
            raise Down() from None
        try:
            doc = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise Failed(NOT_A_LIST) from None
        if not isinstance(doc, dict):
            raise Failed(NOT_A_LIST)
        code = doc.get("code")
        if not (isinstance(code, int) and not isinstance(code, bool) and code == 0):
            raise Failed(ERROR_CODE)
        data = doc.get("data")
        items = data.get("list") if isinstance(data, dict) else None
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise Failed(NOT_A_LIST)
        return items


def read(call):
    settings = call.get("settings") or {}
    advertiser, country = settings.get("TIKTOK_ADVERTISER_ID"), settings.get("TIKTOK_COUNTRY")
    if not (isinstance(advertiser, str) and ADVERTISER.fullmatch(advertiser)
            and isinstance(country, str) and COUNTRY.fullmatch(country)):
        raise Failed(NOT_A_SETTING)
    names = []
    for value in call["topic"]["hashtags"]:
        name = _clean(value)
        if name not in names:
            names.append(name)
    client = _Client(call["api"], _token(call["secrets"][RESOURCE]), settings)

    rising = {}
    for item in client.get("trending_list"):
        hashtag_id, hashtag_name = item.get("hashtag_id"), item.get("hashtag_name")
        if not (isinstance(hashtag_id, str) and isinstance(hashtag_name, str)):
            raise Failed(NOT_A_LIST)
        rising.setdefault(hashtag_name.casefold(), item)
    hashtags, place_of = [], {}
    for index, name in enumerate(names):
        item = rising.get(name)
        if item is None:
            hashtags.append({"hashtag_id": None, "name": name, "rank_position": None})
            continue
        rank = item.get("rank_position")
        hashtags.append({"hashtag_id": item["hashtag_id"], "name": name,
                         "rank_position": rank if _whole(rank) else None})
        place_of.setdefault(item["hashtag_id"], index)

    ids, keys, links = list(place_of), {}, {}
    for start in range(0, len(ids), PER_CALL):
        chunk = ids[start:start + PER_CALL]
        for item in client.get("video_list", {"hashtag_ids": json.dumps(chunk, separators=(",", ":"))}):
            hashtag_id, top = item.get("hashtag_id"), item.get("top_video_list")
            if hashtag_id not in chunk or not isinstance(top, list):
                raise Failed(NOT_A_LIST)
            for place, video in enumerate(top, 1):
                vid = video.get("video_id") if isinstance(video, dict) else None
                link = _link(video) if isinstance(video, dict) else None
                if not (isinstance(vid, str) and TOKEN.fullmatch(vid) and link is not None):
                    continue
                key = (place, place_of[hashtag_id])
                if vid not in keys or key < keys[vid]:
                    keys[vid], links[vid] = key, link
    ordered = sorted(keys, key=lambda vid: (keys[vid], vid))
    videos = [{"id": vid, "link": links[vid], "order": order} for order, vid in enumerate(ordered, 1)]
    return {"videos": videos, "hashtags": hashtags}
