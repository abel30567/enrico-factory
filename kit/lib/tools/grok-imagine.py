"""The tool `grok-imagine`: one image from the prompt of a request, by subscription login through the proxy.

The proxy answers as xAI's public image API: ``POST /v1/images/generations`` with ``model``, ``n``, ``prompt``
and ``response_format``, answered by ``{"data": [{"b64_json", "revised_prompt"}]}``. Text to image only: a
request with reference images fails before any call. A login that ended (401, 403), used-up usage (429) or
no answer is ``Down``: nothing was spent. A subscription call costs nothing.
"""

import base64
import http.client
import json
import os
import urllib.error
import urllib.request

from kit.lib.tools import Down, Failed

RESOURCE = "gen.image.login"
REASONS = ("not a request", "refs not supported", "refused", "proxy error", "no image")
PATH = "/v1/images/generations"
MODEL = "grok-imagine-image-2.0"
TIMEOUT = 300
KEYS = ["for", "prompt", "refs"]
DOWN_STATUS = (401, 403, 429)
IMAGES = ((b"\x89PNG\r\n\x1a\n", "image.png"), (b"\xff\xd8\xff", "image.jpg"))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _failed(reason):
    return Failed(reason, usd=0, compute_min=0)


def _prompt(call):
    """The request's prompt; ``Failed`` before any call when the request is not one this tool can make."""
    try:
        request = json.loads(call["inputs"][0]["text"])
    except (TypeError, ValueError):
        raise _failed("not a request") from None
    if (not isinstance(request, dict) or sorted(request) != KEYS or not isinstance(request["prompt"], str)
            or not request["prompt"].strip() or not isinstance(request["refs"], list)):
        raise _failed("not a request")
    if request["refs"]:
        raise _failed("refs not supported")
    return request["prompt"]


def _post(url, token, body):
    """``(status, body bytes)`` of the one request; ``Down`` when nothing answered."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with opener.open(request, timeout=TIMEOUT) as answer:
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


def run(call):
    prompt = _prompt(call)
    try:
        with open(call["secrets"]["proxy.client"], encoding="utf-8") as handle:
            token = handle.read().strip()
    except OSError:
        raise Down() from None
    body = json.dumps({"model": MODEL, "n": 1, "prompt": prompt, "response_format": "b64_json"}, sort_keys=True)
    status, data = _post(call["settings"]["PROXY_URL"].rstrip("/") + PATH, token, body.encode("utf-8"))
    if status in DOWN_STATUS:
        raise Down()
    if 400 <= status < 500:
        raise _failed("refused")
    if status != 200:
        raise _failed("proxy error")
    name, image = _image(data)
    with open(os.path.join(call["out"], name), "wb") as handle:
        handle.write(image)
    return {"usd": 0, "compute_min": 0}
