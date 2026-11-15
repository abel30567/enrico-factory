"""Signature Version 4: a request signed in its `Authorization` header, a link signed in its query string.

`authorization` signs a request for any service (`s3`, `ec2`, ...): the signed headers are `host` and every
header the caller gives, the canonical query the URL's own. `presign` signs a link for `UNSIGNED-PAYLOAD` with
`host` the only signed header. `form` is the body of a Query API request. A key is `(key_id, secret, region)`
as `assets.read_key` returns it; a time is `production.now()`'s form, `YYYY-MM-DDTHH:MM:SSZ`.

The module prints nothing and reads no file.
"""

import hashlib
import hmac
import urllib.parse
from datetime import datetime

ALGORITHM = "AWS4-HMAC-SHA256"
UNSIGNED_PAYLOAD = "UNSIGNED-PAYLOAD"
MAX_EXPIRES = 7 * 24 * 3600
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"


def _quote(text, safe="-_.~"):
    return urllib.parse.quote(str(text), safe=safe)


def _hmac(key, text):
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).digest()


def signing_key(secret, day, region, service):
    """The key that signs a string to sign: `AWS4` + secret, then the day, region, service and `aws4_request`."""
    key = ("AWS4" + secret).encode("utf-8")
    for part in (day, region, service, "aws4_request"):
        key = _hmac(key, part)
    return key


def _stamp(time):
    return datetime.strptime(time, TIME_FORMAT).strftime(STAMP_FORMAT)


def _encoded(pairs):
    """``name=value`` pairs, each side encoded, sorted by name then value, joined by `&`."""
    return "&".join(f"{n}={v}" for n, v in sorted((_quote(n), _quote(v)) for n, v in pairs))


def _query_pairs(query):
    """The URL's own query as decoded ``(name, value)`` pairs; a name without `=` has the empty value."""
    pairs = []
    for item in query.split("&"):
        if item:
            name, _, value = item.partition("=")
            pairs.append((urllib.parse.unquote(name), urllib.parse.unquote(value)))
    return pairs


def _signature(method, parts, canonical_query, headers, payload_sha256, key, service, time):
    """``(credential, signed headers, signature)`` of a request; ``headers`` maps lower-case names to values."""
    key_id, secret, region = key
    stamp = _stamp(time)
    day = stamp[:8]
    names = sorted(headers)
    canonical = "\n".join((method, _quote(parts.path or "/", safe="/-_.~"), canonical_query,
                           "".join(f"{n}:{headers[n]}\n" for n in names), ";".join(names), payload_sha256))
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join((ALGORITHM, stamp, scope, hashlib.sha256(canonical.encode("utf-8")).hexdigest()))
    signature = hmac.new(signing_key(secret, day, region, service), to_sign.encode("utf-8"),
                         hashlib.sha256).hexdigest()
    return f"{key_id}/{scope}", ";".join(names), signature


def authorization(method, url, headers, payload_sha256, key, service, time):
    """The `Authorization` value; ``headers`` are the signed headers besides `host`, `x-amz-date` among them."""
    parts = urllib.parse.urlsplit(url)
    signed = {"host": parts.netloc}
    signed.update((name.lower(), str(value).strip()) for name, value in headers.items())
    credential, names, signature = _signature(method, parts, _encoded(_query_pairs(parts.query)), signed,
                                              payload_sha256, key, service, time)
    return f"{ALGORITHM} Credential={credential},SignedHeaders={names},Signature={signature}"


def presign(method, url, key, time, expires, service="s3"):
    """``url`` signed in its query string for ``expires`` seconds (1 to seven days); `X-Amz-Signature` comes last."""
    if type(expires) is not int or not 1 <= expires <= MAX_EXPIRES:
        raise ValueError("expires is not an integer from 1 to 604800")
    if "?" in url or "#" in url:
        raise ValueError("the URL already has a query or a fragment")
    parts = urllib.parse.urlsplit(url)
    key_id, _, region = key
    stamp = _stamp(time)
    query = _encoded((("X-Amz-Algorithm", ALGORITHM),
                      ("X-Amz-Credential", f"{key_id}/{stamp[:8]}/{region}/{service}/aws4_request"),
                      ("X-Amz-Date", stamp), ("X-Amz-Expires", str(expires)), ("X-Amz-SignedHeaders", "host")))
    _, _, signature = _signature(method, parts, query, {"host": parts.netloc}, UNSIGNED_PAYLOAD, key, service, time)
    path = _quote(parts.path or "/", safe="/-_.~")
    return f"{parts.scheme}://{parts.netloc}{path}?{query}&X-Amz-Signature={signature}"


def form(params):
    """The body of a Query API request (`application/x-www-form-urlencoded`): a mapping or ``(name, value)`` pairs."""
    pairs = params.items() if hasattr(params, "items") else params
    return _encoded(pairs)
