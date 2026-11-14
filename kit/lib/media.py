"""The media store client: put a file's bytes in an S3 object bucket under its sha256 and check its public link.

`MEDIA_STORE` is the URL under which objects are put, `MEDIA_PUBLIC_BASE` the URL under which anyone gets
them. An object's key is the file's sha256 in lower-case hex. Requests are a signed `HEAD` and `PUT`
(Signature Version 4, service `s3`, the payload signed in one piece) and a plain `GET` on the public link
with no credential and the kit's own `User-Agent`, all through one opener that reads no proxy from the
environment and follows no redirect. An object is never overwritten or deleted.

A reason never contains a value: not a setting, a key, a signature, a response body or header, or the
text of an exception.
"""

import hashlib
import hmac
import http.client
import os
import re
import stat
import urllib.error
import urllib.parse
import urllib.request

from kit.lib import production

TIMEOUT = 60
# A public address may refuse a client by its signature (Python's own user agent): the link check names the kit.
USER_AGENT = "enrico-factory-media/1"
CHUNK = 1024 * 1024
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
SIGNED_HEADERS = "host;x-amz-content-sha256;x-amz-date"
CONTENT_TYPES = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif",
    "mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm", "wav": "audio/wav", "mp3": "audio/mpeg",
}
OCTET_STREAM = "application/octet-stream"
LOOPBACK = ("127.0.0.1", "localhost")
_PATH = re.compile(r"[A-Za-z0-9._~/-]*")
_HOST = re.compile(r"[a-z0-9.-]+")

STORE_URL = "setting MEDIA_STORE is not a store URL"
PUBLIC_URL = "setting MEDIA_PUBLIC_BASE is not a public URL"
NOT_THREE_LINES = "secret store.media: not three lines"
NOT_A_FILE = "file is not a non-empty regular file"
REFUSED_KEY = "store refused the key"
STORE_UNREACHABLE = "store unreachable"
OTHER_BYTES_IN_STORE = "store holds other bytes"
LINK_UNREACHABLE = "public link unreachable"
OTHER_BYTES_AT_LINK = "public link serves other bytes"


class Refused(Exception):
    """Refused before any request."""


class Failed(Exception):
    """A request was sent and the store or the public link did not answer as needed."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def base(value):
    """``value`` with one trailing ``/`` dropped when it is a URL the client may use, otherwise None."""
    if not value:
        return None
    try:
        url = urllib.parse.urlsplit(value)
        host, port = url.hostname, url.port
    except ValueError:
        return None
    # urlsplit drops an empty query or fragment and some whitespace: what it parsed must be the whole value.
    # A netloc that is only the host and port has no user.
    if (value != f"{url.scheme}://{url.netloc}{url.path}" or not host or not _HOST.fullmatch(host)
            or url.netloc.lower() not in (host, f"{host}:{port}") or not _PATH.fullmatch(url.path)):
        return None
    if not (url.scheme == "https" or (url.scheme == "http" and host in LOOPBACK)):
        return None
    return value[:-1] if value.endswith("/") else value


def _setting(settings, name, reason):
    found = base(settings.get(name, ""))
    if found is None:
        raise Refused(reason)
    return found


def store_base(settings):
    return _setting(settings, "MEDIA_STORE", STORE_URL)


def public_base(settings):
    return _setting(settings, "MEDIA_PUBLIC_BASE", PUBLIC_URL)


def link(settings, sha256):
    """The public link of the object ``sha256``."""
    return f"{public_base(settings)}/{sha256}"


def read_key(key_file):
    """``(key_id, secret, region)`` from the key file: exactly three lines, none empty."""
    try:
        with open(key_file, "rb") as handle:
            text = handle.read().decode("utf-8")
    except UnicodeDecodeError:
        raise Refused(NOT_THREE_LINES) from None
    except OSError:
        raise Refused("secret store.media: cannot be read") from None
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    lines = [line.strip() for line in lines]
    if len(lines) != 3 or not all(lines):
        raise Refused(NOT_THREE_LINES)
    return tuple(lines)


def digest(path):
    """``(sha256, bytes)`` of a non-empty regular file, read in pieces."""
    try:
        info = os.stat(path)
    except (OSError, ValueError):
        raise Refused(NOT_A_FILE) from None
    if not stat.S_ISREG(info.st_mode) or info.st_size == 0:
        raise Refused(NOT_A_FILE)
    hasher, size = hashlib.sha256(), 0
    with open(path, "rb") as handle:
        while piece := handle.read(CHUNK):
            hasher.update(piece)
            size += len(piece)
    if size == 0:
        raise Refused(NOT_A_FILE)
    return hasher.hexdigest(), size


def content_type(path):
    name = os.path.basename(path)
    extension = name.rsplit(".", 1)[1].lower() if "." in name else ""
    return CONTENT_TYPES.get(extension, OCTET_STREAM)


def _hmac(key, text):
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).digest()


def authorization(method, url, payload_sha256, key_id, secret, region, time):
    """The `Authorization` header value of a Signature Version 4 request; ``time`` as `production.now()`."""
    stamp = _amz_date(time)
    day = stamp[:8]
    parts = urllib.parse.urlsplit(url)
    canonical = "\n".join((method, parts.path or "/", "", f"host:{parts.netloc}",
                           f"x-amz-content-sha256:{payload_sha256}", f"x-amz-date:{stamp}", "",
                           SIGNED_HEADERS, payload_sha256))
    scope = f"{day}/{region}/s3/aws4_request"
    to_sign = "\n".join(("AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode("utf-8")).hexdigest()))
    key = ("AWS4" + secret).encode("utf-8")
    for part in (day, region, "s3", "aws4_request"):
        key = _hmac(key, part)
    signature = hmac.new(key, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return (f"AWS4-HMAC-SHA256 Credential={key_id}/{scope},SignedHeaders={SIGNED_HEADERS},"
            f"Signature={signature}")


def _amz_date(time):
    return time.replace("-", "").replace(":", "")


def _signed(method, url, payload_sha256, key):
    time = production.now()
    return {"x-amz-date": _amz_date(time), "x-amz-content-sha256": payload_sha256,
            "Authorization": authorization(method, url, payload_sha256, *key, time)}


def _send(request, unreachable, read=None):
    """``(status, headers, result of read(response))``; ``Failed(unreachable)`` when no answer came."""
    try:
        with _OPENER.open(request, timeout=TIMEOUT) as response:
            return response.status, response.headers, read(response) if read else None
    except urllib.error.HTTPError as error:
        error.close()
        return error.code, None, None
    except (OSError, http.client.HTTPException, ValueError):
        raise Failed(unreachable) from None


def _store_status(status):
    if status in (401, 403):
        return Failed(REFUSED_KEY)
    return Failed(f"store answered {status}")


def put(settings, key_file, path):
    """Put the file's bytes under its sha256 unless the store holds them, then check the public link.

    Returns ``{"bytes", "link", "sha256"}``. ``Refused`` before any request, ``Failed`` after one.
    """
    store = store_base(settings)
    public_base(settings)
    key = read_key(key_file)
    sha256, size = digest(path)
    url = f"{store}/{sha256}"
    status, headers, _ = _send(urllib.request.Request(url, method="HEAD", headers=_signed(
        "HEAD", url, EMPTY_SHA256, key)), STORE_UNREACHABLE)
    if status == 200:
        if headers.get("Content-Length") != str(size):
            raise Failed(OTHER_BYTES_IN_STORE)
    elif status == 404:
        with open(path, "rb") as body:
            request = urllib.request.Request(url, data=body, method="PUT", headers={
                **_signed("PUT", url, sha256, key), "Content-Length": str(size), "Content-Type": content_type(path)})
            status, _, _ = _send(request, STORE_UNREACHABLE)
        if not 200 <= status < 300:
            raise _store_status(status)
    else:
        raise _store_status(status)
    check(settings, sha256, size)
    return {"bytes": size, "link": link(settings, sha256), "sha256": sha256}


def _body(response, size):
    """``(sha256, length)`` of at most ``size + 1`` bytes of the body."""
    hasher, length = hashlib.sha256(), 0
    while length <= size and (piece := response.read(min(CHUNK, size + 1 - length))):
        hasher.update(piece)
        length += len(piece)
    return hasher.hexdigest(), length


def check(settings, sha256, size):
    """Get the public link with no credential; ``Failed`` unless it serves ``size`` bytes of ``sha256``."""
    request = urllib.request.Request(link(settings, sha256), headers={"User-Agent": USER_AGENT})
    status, _, body = _send(request, LINK_UNREACHABLE, lambda response: _body(response, size))
    if status != 200:
        raise Failed(f"public link answered {status}")
    if body != (sha256, size):
        raise Failed(OTHER_BYTES_AT_LINK)
