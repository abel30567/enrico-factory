"""youtube-analytics: pull one video's window of YouTube Analytics into a snapshot.

The job's first input is the window request ``distribution/<ep>/youtube/requests/window.json``, one JSON
object with exactly the keys ``video`` (the video id), ``start`` and ``end`` (``YYYY-MM-DD``, ``start`` not
after ``end``). The key file of ``platform.youtube`` holds three lines: client id, client secret, refresh
token.

The requests, in this order, all through one opener that reads no proxy from the environment and follows
no redirect: the token refresh, then two reads of the Analytics API reports.query, the video's totals
(``views``, ``averageViewDuration``, ``averageViewPercentage``) and its retention curve
(``audienceWatchRatio`` by ``elapsedVideoTimeRatio``). No read is retried.

The one output is ``snapshot.json``, the text `kit/lib/lints/telemetry.py` derives from the two answers as
received: a value the answers do not hold is null, never 0. Nothing of the key file or the access token is
in an output or a reason.
"""

import datetime
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from kit.lib.lints import telemetry
from kit.lib.tools import Down, Failed

RESOURCE = "platform.youtube"
NOT_A_REQUEST = "request is not a window request"
UNREACHABLE = "platform unreachable"
REFUSED = "read refused"
MALFORMED = "answer malformed"
REASONS = (NOT_A_REQUEST, UNREACHABLE, REFUSED, MALFORMED)

TIMEOUT = 60
TOKEN_URL = "https://oauth2.googleapis.com/token"
REPORTS_URL = "https://youtubeanalytics.googleapis.com/v2/reports?"
PLATFORM = "youtube"
OUTPUT = "snapshot.json"
REQUEST_PATH = re.compile(r"^distribution/[^/]+/youtube/requests/window\.json$")
KEYS = ["end", "start", "video"]
VIDEO = re.compile(r"^[A-Za-z0-9_-]{11}$")
DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _failed(reason):
    return Failed(reason, usd=0, compute_min=0)


def _date(value):
    if not isinstance(value, str) or not DATE.match(value):
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def _request(call):
    """The window request of the job's first input, or ``Failed`` before any request."""
    inputs = call["inputs"]
    try:
        path, request = inputs[0]["path"], json.loads(inputs[0]["text"])
    except (IndexError, KeyError, TypeError, ValueError):
        raise _failed(NOT_A_REQUEST) from None
    if not isinstance(path, str) or not REQUEST_PATH.match(path):
        raise _failed(NOT_A_REQUEST)
    if not isinstance(request, dict) or sorted(request) != KEYS:
        raise _failed(NOT_A_REQUEST)
    start, end = _date(request["start"]), _date(request["end"])
    if (not isinstance(request["video"], str) or not VIDEO.match(request["video"])
            or start is None or end is None or start > end):
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
                                   "refresh_token": refresh_token, "grant_type": "refresh_token"}).encode()
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


def _report(opener, auth, request, query):
    """One reports.query answer, a result table, or ``Failed``."""
    params = [("ids", "channel==MINE"), ("startDate", request["start"]), ("endDate", request["end"])]
    url = REPORTS_URL + urllib.parse.urlencode(params + query + [("filters", "video==" + request["video"])])
    try:
        status, body = _json(opener, urllib.request.Request(url, method="GET", headers={"Authorization": auth}))
    except (OSError, ValueError):
        raise _failed(UNREACHABLE) from None
    if status != 200:
        raise _failed(REFUSED)
    if not telemetry.table(body):
        raise _failed(MALFORMED)
    return body


def run(call):
    request = _request(call)
    key = _key(call["secrets"][RESOURCE])
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    auth = "Bearer " + _token(opener, key)
    totals = _report(opener, auth, request, [("metrics", ",".join(telemetry.TOTALS))])
    retention = _report(opener, auth, request, [("dimensions", telemetry.RATIO), ("metrics", telemetry.WATCH)])
    text = telemetry.snapshot(PLATFORM, request["video"], request["start"], request["end"], [totals, retention])
    with open(os.path.join(call["out"], OUTPUT), "w", encoding="utf-8") as handle:
        handle.write(text)
    return {"usd": 0, "compute_min": 0}
