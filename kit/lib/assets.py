"""The asset store client: put an engine binary in a private S3 object bucket under its sha256, get it, check it.

`ASSET_STORE` is the URL under which objects are put. An object's URL is `<ASSET_STORE>/<sha256>`, the
file's sha256 in lower-case hex. The bucket has no public link, so every request is signed: a `HEAD`, a
`PUT` and a `GET`, with the signing and the opener of `kit/lib/media.py`, which reads no proxy from the
environment and follows no redirect. An object is never overwritten or deleted, a file is never read
whole, and `get` never writes over a file. Above `MAX_BYTES` a file needs a multipart upload, which this
client does not have.

A reason never contains a value: not a setting, a key, a signature, a response body or header, or the
text of an exception.
"""

import contextlib
import hashlib
import os
import re
import stat
import tempfile
import urllib.request

from kit.lib import media

MAX_BYTES = 5 * 1024 ** 3
_SHA256 = re.compile(r"[0-9a-f]{64}")

STORE_URL = "setting ASSET_STORE is not a store URL"
NOT_THREE_LINES = "secret store.assets: not three lines"
KEY_UNREADABLE = "secret store.assets: cannot be read"
TOO_LARGE = "file is larger than one PUT allows"
NOT_A_SHA256 = "sha256 must be 64 lower-case hex characters"
NO_DIRECTORY = "path is not in an existing directory"
OTHER_BYTES_AT_PATH = "path holds other bytes"
PATH_UNWRITABLE = "path cannot be written"
NO_SUCH_OBJECT = "store has no such object"
OTHER_BYTES_SERVED = "store serves other bytes"


def store_base(settings):
    found = media.base(settings.get("ASSET_STORE", ""))
    if found is None:
        raise media.Refused(STORE_URL)
    return found


def read_key(key_file):
    """``(key_id, secret, region)`` from the key file: exactly three lines, none empty."""
    try:
        with open(key_file, "rb") as handle:
            text = handle.read().decode("utf-8")
    except UnicodeDecodeError:
        raise media.Refused(NOT_THREE_LINES) from None
    except OSError:
        raise media.Refused(KEY_UNREADABLE) from None
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    lines = [line.strip() for line in lines]
    if len(lines) != 3 or not all(lines):
        raise media.Refused(NOT_THREE_LINES)
    return tuple(lines)


def require_sha256(value):
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise media.Refused(NOT_A_SHA256)
    return value


def measure(path):
    """``(sha256, bytes)`` of a non-empty regular file of at most `MAX_BYTES`, its size checked before a byte is read."""
    try:
        info = os.stat(path)
    except (OSError, ValueError):
        raise media.Refused(media.NOT_A_FILE) from None
    if not stat.S_ISREG(info.st_mode) or info.st_size == 0:
        raise media.Refused(media.NOT_A_FILE)
    if info.st_size > MAX_BYTES:
        raise media.Refused(TOO_LARGE)
    try:
        sha256, size = media.digest(path)
    except OSError:
        raise media.Refused(media.NOT_A_FILE) from None
    if size > MAX_BYTES:
        raise media.Refused(TOO_LARGE)
    return sha256, size


def prepare(settings, key_file, path):
    """Every check `put` makes before a request: ``(url, key, sha256, bytes)``, or ``Refused``."""
    store = store_base(settings)
    key = read_key(key_file)
    sha256, size = measure(path)
    return f"{store}/{sha256}", key, sha256, size


def _head(url, key):
    status, headers, _ = media._send(urllib.request.Request(url, method="HEAD", headers=media._signed(
        "HEAD", url, media.EMPTY_SHA256, key)), media.STORE_UNREACHABLE)
    return status, headers


def _held(url, key, size):
    """True when the store holds ``size`` bytes at ``url``, False on a 404, ``Failed`` otherwise."""
    status, headers = _head(url, key)
    if status == 404:
        return False
    if status != 200:
        raise media._store_status(status)
    if headers.get("Content-Length") != str(size):
        raise media.Failed(media.OTHER_BYTES_IN_STORE)
    return True


def put(settings, key_file, path):
    """Put the file's bytes under its sha256 unless the store holds them.

    Returns ``{"bytes", "sent", "sha256"}``; ``sent`` is false when no `PUT` was sent. ``Refused`` before
    any request, ``Failed`` after one.
    """
    url, key, sha256, size = prepare(settings, key_file, path)
    if _held(url, key, size):
        return {"bytes": size, "sent": False, "sha256": sha256}
    with open(path, "rb") as body:
        request = urllib.request.Request(url, data=body, method="PUT", headers={
            **media._signed("PUT", url, sha256, key), "Content-Length": str(size), "Content-Type": media.OCTET_STREAM})
        status, _, _ = media._send(request, media.STORE_UNREACHABLE)
    if not 200 <= status < 300:
        raise media._store_status(status)
    if not _held(url, key, size):
        raise media._store_status(404)
    return {"bytes": size, "sent": True, "sha256": sha256}


def check(settings, key_file, sha256):
    """The length of the object ``sha256``, from one signed `HEAD`."""
    store = store_base(settings)
    key = read_key(key_file)
    require_sha256(sha256)
    status, headers = _head(f"{store}/{sha256}", key)
    if status == 404:
        raise media.Failed(NO_SUCH_OBJECT)
    if status != 200:
        raise media._store_status(status)
    length = headers.get("Content-Length") or ""
    if not (length.isascii() and length.isdigit()):
        raise media.Failed(f"store answered {status}")
    return int(length)


def _write(response, handle):
    """``(sha256, length)`` of at most `MAX_BYTES` + 1 bytes of the body, written to ``handle`` in pieces;
    None when a write failed. A read that fails raises, so `media._send` reports it as the store's."""
    hasher, length = hashlib.sha256(), 0
    while length <= MAX_BYTES and (piece := response.read(min(media.CHUNK, MAX_BYTES + 1 - length))):
        try:
            handle.write(piece)
        except OSError:
            return None
        hasher.update(piece)
        length += len(piece)
    return hasher.hexdigest(), length


def get(settings, key_file, sha256, path):
    """Write the object ``sha256`` to a new file at ``path``, or find it there already.

    Returns ``{"bytes", "sent", "sha256"}``; ``sent`` is false when ``path`` held the bytes and nothing was
    sent. The body goes to a temporary file beside ``path``, which is moved to ``path`` only when its
    sha256 is ``sha256``; otherwise it is removed and nothing is at ``path``.
    """
    store = store_base(settings)
    key = read_key(key_file)
    require_sha256(sha256)
    directory = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(directory):
        raise media.Refused(NO_DIRECTORY)
    if os.path.lexists(path):
        try:
            found, size = media.digest(path)
        except (media.Refused, OSError):
            found = None
        if found != sha256:
            raise media.Refused(OTHER_BYTES_AT_PATH)
        return {"bytes": size, "sent": False, "sha256": sha256}
    try:
        descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".assets-", suffix=".part")
    except OSError:
        raise media.Refused(PATH_UNWRITABLE) from None
    try:
        url = f"{store}/{sha256}"
        request = urllib.request.Request(url, headers=media._signed("GET", url, media.EMPTY_SHA256, key))
        try:
            with os.fdopen(descriptor, "wb") as handle:
                status, _, body = media._send(request, media.STORE_UNREACHABLE,
                                              lambda response: _write(response, handle))
        except OSError:
            raise media.Failed(PATH_UNWRITABLE) from None
        if status == 404:
            raise media.Failed(NO_SUCH_OBJECT)
        if status != 200:
            raise media._store_status(status)
        if body is None:
            raise media.Failed(PATH_UNWRITABLE)
        if body[0] != sha256:
            raise media.Failed(OTHER_BYTES_SERVED)
        try:
            os.replace(temporary, path)
        except OSError:
            raise media.Failed(PATH_UNWRITABLE) from None
        temporary = None
        return {"bytes": body[1], "sent": True, "sha256": sha256}
    finally:
        if temporary is not None:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
