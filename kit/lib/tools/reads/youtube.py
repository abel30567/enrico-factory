"""The YouTube read: the public videos of one topic, from the YouTube Data API v3, read only.

One `search` (the budget's one search) gives the video ids in the platform's order; then `videos` gives
their counts, `channels` their authors' followers and uploads playlists, `playlistItems` and `videos` the
views of each author's other uploads, and `commentThreads` each video's top comments. Every other request
costs one unit of the day's 10,000. A missing count is null, never zero.

The key file is an API key (one token, sent as the parameter `key`) or a grant (a JSON object with an
`access_token`, sent as a bearer token).
"""

import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from kit.lib import media, resources
from kit.lib.tools import Down, Failed

PLATFORM = "youtube"
RESOURCE = "external.youtube"
API = "https://www.googleapis.com/youtube/v3"
BUDGET = ("YOUTUBE_SEARCHES_PER_DAY", 100)
NOT_A_KEY = "the key file is not a key or a grant"
REFUSED = "refused by the platform"
ERROR = "the platform answered with an error"
BAD = "bad answer"
REASONS = (NOT_A_KEY, REFUSED, ERROR, BAD)

PAGE = 50
AUTHOR_VIEWS = 20
DOWN = ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded")
DURATION = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?")
PUBLISHED = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?Z")
TOKEN = re.compile(r"\S+")
DIGITS = re.compile(r"[0-9]+")


def _get(value, *keys):
    """``value[k1][k2]...``, or None where a level is not an object or lacks the key."""
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _count(value):
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, str) and DIGITS.fullmatch(value):
        return int(value)
    raise Failed(BAD)


def _auth(path):
    """``(params, headers)`` that carry the key file."""
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read().strip()
    except (OSError, UnicodeDecodeError):
        raise Failed(NOT_A_KEY) from None
    try:
        grant = json.loads(text)
    except ValueError:
        grant = None
    token = _get(grant, "access_token")
    if isinstance(token, str) and token:
        return {}, {"Authorization": f"Bearer {token}"}
    if TOKEN.fullmatch(text):
        return {"key": text}, {}
    raise Failed(NOT_A_KEY)


def _reasons(body):
    try:
        errors = _get(json.loads(body), "error", "errors")
    except (ValueError, UnicodeDecodeError):
        return []
    return [_get(e, "reason") for e in errors] if isinstance(errors, list) else []


class _Client:
    def __init__(self, api, key):
        self.api = api
        self.params, self.headers = _auth(key)

    def get(self, endpoint, params, disabled=False):
        """The answer's ``items``; None when ``disabled`` and the platform says comments are off."""
        query = urllib.parse.urlencode(sorted({**params, **self.params}.items()))
        request = urllib.request.Request(f"{self.api}/{endpoint}?{query}", headers=self.headers)
        try:
            with media._OPENER.open(request, timeout=resources.TIMEOUT) as answer:
                status, body = answer.status, answer.read()
        except urllib.error.HTTPError as error:
            status, body = error.code, error.read()
        except (OSError, http.client.HTTPException):
            raise Down() from None
        if status != 200:
            reasons = _reasons(body)
            if status == 403 and any(r in DOWN for r in reasons):
                raise Down()
            if disabled and status == 403 and "commentsDisabled" in reasons:
                return None
            raise Failed(REFUSED if status in (401, 403) else ERROR)
        try:
            items = json.loads(body).get("items")
        except (ValueError, UnicodeDecodeError, AttributeError):
            raise Failed(BAD) from None
        if not isinstance(items, list):
            raise Failed(BAD)
        return items


def _ids(items, *keys):
    """The string ids at ``keys`` of each item that has one, first place kept; another value is a bad answer."""
    found = []
    for item in items:
        value = _get(item, *keys)
        if value is None:
            continue
        if not isinstance(value, str) or not TOKEN.fullmatch(value) or "," in value:
            raise Failed(BAD)
        if value not in found:
            found.append(value)
    return found


def _by_id(items):
    return {item["id"]: item for item in items if isinstance(_get(item, "id"), str)}


def _duration(value):
    found = DURATION.fullmatch(value) if isinstance(value, str) else None
    if found is None:
        return None
    days, hours, minutes, seconds = (int(g or 0) for g in found.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def _published(value):
    found = PUBLISHED.fullmatch(value) if isinstance(value, str) else None
    return found.group(1) + "Z" if found else None


def _search(client, read_at, topic):
    after = datetime.strptime(read_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    after -= timedelta(days=topic["days"])
    items = client.get("search", {
        "maxResults": PAGE, "part": "snippet", "publishedAfter": after.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "q": " ".join(topic["words"]), "regionCode": topic["region"], "type": "video"})
    _ids(items, "id", "videoId")
    order = {}
    for place, vid in enumerate((v for v in (_get(item, "id", "videoId") for item in items) if v is not None), 1):
        order.setdefault(vid, place)
    return order


def read(call):
    client = _Client(call["api"], call["secrets"][RESOURCE])
    order = _search(client, call["read_at"], call["topic"])
    if not order:
        return {"videos": []}
    ids = list(order)
    videos = _by_id(client.get("videos", {"id": ",".join(ids), "maxResults": PAGE,
                                          "part": "contentDetails,snippet,statistics"}))
    authors = sorted(set(_ids(videos.values(), "snippet", "channelId")))
    channels = _by_id(client.get("channels", {"id": ",".join(authors), "maxResults": PAGE,
                                              "part": "contentDetails,statistics"})) if authors else {}
    uploads = {}
    for author in sorted(channels):
        playlist = _get(channels[author], "contentDetails", "relatedPlaylists", "uploads")
        if isinstance(playlist, str) and playlist:
            uploads[author] = _ids(client.get("playlistItems", {"maxResults": AUTHOR_VIEWS + 1,
                                                                "part": "contentDetails", "playlistId": playlist}),
                                   "contentDetails", "videoId")
    listed = sorted({vid for found in uploads.values() for vid in found})
    views = {}
    for start in range(0, len(listed), PAGE):
        answer = client.get("videos", {"id": ",".join(listed[start:start + PAGE]), "maxResults": PAGE,
                                       "part": "statistics"})
        views.update({vid: _count(_get(item, "statistics", "viewCount")) for vid, item in _by_id(answer).items()})
    stored = []
    for vid in ids:
        video = videos.get(vid)
        author = _get(video, "snippet", "channelId")
        channel = channels.get(author)
        stats = _get(video, "statistics")
        hidden = _get(channel, "statistics", "hiddenSubscriberCount") is True
        title = _get(video, "snippet", "title")
        threads = client.get("commentThreads", {"maxResults": 20, "order": "relevance", "part": "snippet",
                                                "textFormat": "plainText", "videoId": vid}, disabled=True)
        stored.append({
            "author": author,
            "author_views": None if author not in uploads else [
                views[v] for v in uploads[author] if v != vid and views.get(v) is not None][:AUTHOR_VIEWS],
            "comments": _count(_get(stats, "commentCount")),
            "duration_s": _duration(_get(video, "contentDetails", "duration")),
            "followers": None if channel is None or hidden else _count(_get(channel, "statistics", "subscriberCount")),
            "id": vid,
            "likes": _count(_get(stats, "likeCount")),
            "link": f"https://www.youtube.com/watch?v={vid}",
            "order": order[vid],
            "published_at": _published(_get(video, "snippet", "publishedAt")),
            "title": title if isinstance(title, str) else None,
            "top_comments": None if threads is None else [
                text for text in (_get(thread, "snippet", "topLevelComment", "snippet", "textDisplay") for thread in threads)
                if isinstance(text, str)],
            "views": _count(_get(stats, "viewCount")),
        })
    return {"videos": stored}
