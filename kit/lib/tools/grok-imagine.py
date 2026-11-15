"""The tool `grok-imagine`: one image from the prompt and the reference images of a request, by subscription login through the proxy.

The proxy answers as xAI's public image API, ``{"data": [{"b64_json", "revised_prompt"}]}``. A request with no
``refs`` is posted as before to ``POST /v1/images/generations`` with ``model``, ``n``, ``prompt`` and
``response_format``. Each ``media:<id>`` of ``refs`` (at most ``MAX_REFS``, PNG only) is found in
``media/manifest.tsv``, the job's second input, fetched from the public link of the media store, checked against
its row and sent in the one form ``_body`` builds. The output is stored as before. A login that ended (401, 403),
used-up usage (429) or no answer from the proxy or the link is ``Down``: nothing was spent. The cost recorded is
the one the service reports, 0 when it reports none.
"""

import base64
import hashlib
import http.client
import json
import os
import re
import urllib.error
import urllib.request

from kit.lib import media
from kit.lib.tools import Down, Failed

RESOURCE = "gen.image.login"
MAX_REFS = 4
TOO_MANY = f"refs over the limit of {MAX_REFS}"
REASONS = ("not a request", TOO_MANY, "ref not in the manifest", "ref not fetched", "ref is not a PNG", "refused",
           "proxy error", "no image")
PATH = "/v1/images/generations"
MODEL = "grok-imagine-image-2.0"
TIMEOUT = 300
KEYS = ["for", "prompt", "refs"]
REF_PREFIX = "media:"
MANIFEST = "media/manifest.tsv"
DOWN_STATUS = (401, 403, 429)
IMAGES = ((b"\x89PNG\r\n\x1a\n", "image.png"), (b"\xff\xd8\xff", "image.jpg"))
TICKS_PER_USD = 10 ** 10
_SHA256 = re.compile(r"[0-9a-f]{64}")
_BYTES = re.compile(r"[0-9]+")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _failed(reason):
    return Failed(reason, usd=0, compute_min=0)


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _request(call):
    """``(prompt, ids)`` of the request, ``ids`` the media ids of its ``refs``; ``Failed`` before any call when
    the request is not one this tool can make."""
    try:
        request = json.loads(call["inputs"][0]["text"])
    except (TypeError, ValueError):
        raise _failed("not a request") from None
    if (not isinstance(request, dict) or sorted(request) != KEYS or not isinstance(request["prompt"], str)
            or not request["prompt"].strip() or not isinstance(request["refs"], list)):
        raise _failed("not a request")
    refs = request["refs"]
    if (not all(isinstance(ref, str) and ref.startswith(REF_PREFIX) and ref != REF_PREFIX for ref in refs)
            or len(set(refs)) != len(refs)):
        raise _failed("not a request")
    if len(refs) > MAX_REFS:
        raise _failed(TOO_MANY)
    return request["prompt"], [ref[len(REF_PREFIX):] for ref in refs]


def _rows(call, ids):
    """``[(sha256, bytes)]`` of each id in the manifest, the job's second input; ``Failed`` when one has no row."""
    if not ids:
        return []
    inputs = call["inputs"]
    if len(inputs) < 2 or inputs[1]["path"] != MANIFEST or inputs[1]["text"] is None:
        raise _failed("ref not in the manifest")
    rows = {}
    for line in inputs[1]["text"].splitlines():
        fields = line.split("\t")
        if len(fields) == 4 and _SHA256.fullmatch(fields[1]) and _BYTES.fullmatch(fields[2]):
            rows.setdefault(fields[0], (fields[1], int(fields[2])))
    if not all(name in rows for name in ids):
        raise _failed("ref not in the manifest")
    return [rows[name] for name in ids]


def _fetch(settings, sha256, size):
    """The bytes of the reference ``sha256`` from its public link; ``Down`` when nothing answered."""
    try:
        url = media.link(settings, sha256)
    except media.Refused:
        raise Down() from None
    request = urllib.request.Request(url, headers={"User-Agent": media.USER_AGENT})
    try:
        with _opener().open(request, timeout=TIMEOUT) as answer:
            status, data = answer.status, answer.read(size + 1)
    except urllib.error.HTTPError as error:
        with error:
            raise _failed("ref not fetched") from None
    except (OSError, http.client.HTTPException, ValueError):
        raise Down() from None
    if status != 200 or len(data) != size or hashlib.sha256(data).hexdigest() != sha256:
        raise _failed("ref not fetched")
    if not data.startswith(IMAGES[0][0]):
        raise _failed("ref is not a PNG")
    return data


def _body(prompt, pngs):
    """``(path, payload)`` of the image call with the reference pictures ``pngs``, at most ``MAX_REFS``."""
    payload = {"model": MODEL, "n": 1, "prompt": prompt, "response_format": "b64_json"}
    if not pngs:
        return PATH, payload
    urls = [{"url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")} for png in pngs]
    if len(urls) == 1:
        payload["image"] = urls[0]
    else:
        payload["images"] = urls
    return "/v1/images/edits", payload


def _post(url, token, body):
    """``(status, body bytes)`` of the one request; ``Down`` when nothing answered."""
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with _opener().open(request, timeout=TIMEOUT) as answer:
            return answer.status, answer.read()
    except urllib.error.HTTPError as error:
        with error:
            return error.code, b""
    except (OSError, http.client.HTTPException, ValueError):
        raise Down() from None


def _image(data):
    """``(file name, bytes)`` of the one image in the answer ``data``; ``Failed`` when there is none."""
    try:
        answer = json.loads(data)
    except ValueError:
        raise _failed("no image") from None
    items = answer.get("data") if isinstance(answer, dict) else None
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise _failed("no image")
    encoded = items[0].get("b64_json")
    try:
        image = base64.b64decode(encoded, validate=True) if isinstance(encoded, str) else b""
    except ValueError:
        image = b""
    for magic, name in IMAGES:
        if image.startswith(magic):
            return name, image
    raise _failed("no image")


def _usd(data):
    """The dollars the answer ``data`` reports in ``usage.cost_in_usd_ticks``; 0 when it reports none."""
    try:
        answer = json.loads(data)
    except ValueError:
        return 0
    usage = answer.get("usage") if isinstance(answer, dict) else None
    ticks = usage.get("cost_in_usd_ticks") if isinstance(usage, dict) else None
    if not isinstance(ticks, int) or isinstance(ticks, bool) or ticks < 0:
        return 0
    try:
        return round(ticks / TICKS_PER_USD, 4)
    except OverflowError:
        return 0


def run(call):
    prompt, ids = _request(call)
    rows = _rows(call, ids)
    try:
        with open(call["secrets"]["proxy.client"], encoding="utf-8") as handle:
            token = handle.read().strip()
    except OSError:
        raise Down() from None
    pngs = [_fetch(call["settings"], sha256, size) for sha256, size in rows]
    path, payload = _body(prompt, pngs)
    body = json.dumps(payload, sort_keys=True)
    status, data = _post(call["settings"]["PROXY_URL"].rstrip("/") + path, token, body.encode("utf-8"))
    if status in DOWN_STATUS:
        raise Down()
    if 400 <= status < 500:
        raise _failed("refused")
    if status != 200:
        raise _failed("proxy error")
    name, image = _image(data)
    with open(os.path.join(call["out"], name), "wb") as handle:
        handle.write(image)
    return {"usd": _usd(data), "compute_min": 0}
