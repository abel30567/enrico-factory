"""The OAuth grants a program keeps alive: which are due for a refresh, the refresh, and when one lapses.

``platform.tiktok`` is the one grant refreshed here: its key file ``<SECRETS_DIR>/platform.tiktok`` is one
JSON object, refreshable when ``client_key``, ``client_secret`` and ``refresh_token`` are each a non-empty
string. TikTok's refresh token rotates on every refresh, so ``refresh`` stores the new ``access_token`` and
``refresh_token`` with ``expires_at`` and ``refresh_expires_at`` (UTC, ``TIME_FORMAT``) and keeps every
other key. ``factory tick`` refreshes; ``factory doctor`` reads ``lapse``. No token, client key or client
secret leaves the key file. Standard library only: ``factory doctor`` imports this module.
"""

import contextlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
TIKTOK = "platform.tiktok"
INSTAGRAM = "platform.instagram"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
TIMEOUT = 60
DUE = timedelta(minutes=30)
WARN = timedelta(days=14)
INSTAGRAM_LIFE = timedelta(days=60)
REFRESHABLE = ("client_key", "client_secret", "refresh_token")

_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


class Failed(Exception):
    """A refresh that was not done; its text follows ``FAIL - refresh <id>: ``."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is not followed: it reaches the caller as an ``HTTPError``."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def now(env=None) -> str:
    """UTC now as `YYYY-MM-DDTHH:MM:SSZ`, or `FACTORY_NOW` when set (empty counts as unset); `ValueError`
    when `FACTORY_NOW` is not such a time."""
    env = os.environ if env is None else env
    value = env.get("FACTORY_NOW", "")
    if not value:
        return datetime.now(timezone.utc).strftime(TIME_FORMAT)
    if _time(value) is None:
        raise ValueError("FACTORY_NOW is not a UTC time YYYY-MM-DDTHH:MM:SSZ")
    return value


def _time(value):
    """`value` as an aware UTC datetime when it is a `TIME_FORMAT` string, else None."""
    if not isinstance(value, str) or not _TIME.fullmatch(value):
        return None
    try:
        return datetime.strptime(value, TIME_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _text(moment) -> str:
    return moment.strftime(TIME_FORMAT)


def _object(path):
    """The key file at `path` as a JSON object, or None when it cannot be read as one."""
    try:
        with open(path, encoding="utf-8") as handle:
            found = json.loads(handle.read())
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def _grant(path):
    """The refreshable grant at `path`, or None."""
    grant = _object(path)
    if grant is None or not all(isinstance(grant.get(key), str) and grant[key] for key in REFRESHABLE):
        return None
    return grant


def due(secrets_dir, now) -> list[str]:
    """The sorted ids of the grants in `secrets_dir` to refresh at `now`: a key file that is not a
    refreshable grant, has no valid `expires_at` or `refresh_expires_at`, or expires within `DUE`."""
    if not secrets_dir:
        return []
    path = os.path.join(secrets_dir, TIKTOK)
    if not os.path.lexists(path):
        return []
    grant = _grant(path)
    if grant is None:
        return [TIKTOK]
    expires, lapses = _time(grant.get("expires_at")), _time(grant.get("refresh_expires_at"))
    if expires is None or lapses is None or expires <= _time(now) + DUE:
        return [TIKTOK]
    return []


def _good(answer) -> bool:
    if not isinstance(answer, dict):
        return False
    tokens = all(isinstance(answer.get(key), str) and answer[key] for key in ("access_token", "refresh_token"))
    lives = all(isinstance(answer.get(key), int) and not isinstance(answer[key], bool) and answer[key] >= 1
                for key in ("expires_in", "refresh_expires_in"))
    return tokens and lives


def _ask(grant):
    """TikTok's answer to one refresh of `grant`, a JSON object; `Failed` when there is none."""
    body = urllib.parse.urlencode([("client_key", grant["client_key"]), ("client_secret", grant["client_secret"]),
                                   ("grant_type", "refresh_token"), ("refresh_token", grant["refresh_token"])])
    request = urllib.request.Request(TOKEN_URL, data=body.encode("ascii"), method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            status, data = response.status, response.read()
    except urllib.error.HTTPError:
        raise Failed("refresh refused") from None
    except (OSError, ValueError):
        raise Failed("token endpoint unreachable") from None
    try:
        answer = json.loads(data)
    except ValueError:
        answer = None
    if status != 200 or (isinstance(answer, dict) and "error" in answer):
        raise Failed("refresh refused")
    if not _good(answer):
        raise Failed("refresh answer malformed")
    return answer


def refresh(secrets_dir, rid, now) -> None:
    """Refresh the grant `rid` in `secrets_dir` at `now` and store the rotated tokens in its key file.

    `Failed(reason)` when it is not done; the key file is then unchanged and no other file is left."""
    path = os.path.join(secrets_dir, rid)
    grant = _grant(path)
    if grant is None:
        raise Failed("key file is not a refreshable grant")
    lapses = _time(grant.get("refresh_expires_at"))
    moment = _time(now)
    if lapses is not None and lapses <= moment:
        raise Failed("grant has lapsed")
    try:
        fd, temporary = tempfile.mkstemp(dir=secrets_dir, prefix=f".{rid}.")
    except OSError:
        raise Failed("key file cannot be replaced") from None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            answer = _ask(grant)
            grant.update({
                "access_token": answer["access_token"], "refresh_token": answer["refresh_token"],
                "expires_at": _text(moment + timedelta(seconds=answer["expires_in"])),
                "refresh_expires_at": _text(moment + timedelta(seconds=answer["refresh_expires_in"]))})
            handle.write(json.dumps(grant, indent=1, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError:
        raise Failed("key file cannot be replaced") from None
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)

def lapse(rid, path, now):
    """None, ``"lapses within 14 days"`` or ``"lapsed"``: when the grant `rid` with the key file `path` ends.

    TikTok's ends at its ``refresh_expires_at``; Instagram's 60 days after its key file was last written,
    since each write of the token restarts the 60 days. Any other grant, or no such time: None."""
    if rid == TIKTOK:
        grant = _object(path)
        ends = None if grant is None else _time(grant.get("refresh_expires_at"))
    elif rid == INSTAGRAM:
        try:
            ends = datetime.fromtimestamp(os.stat(path).st_mtime, timezone.utc) + INSTAGRAM_LIFE
        except (OSError, ValueError, OverflowError):
            ends = None
    else:
        ends = None
    if ends is None:
        return None
    moment = _time(now)
    if ends <= moment:
        return "lapsed"
    if ends - moment <= WARN:
        return "lapses within 14 days"
    return None
