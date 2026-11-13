"""Tests for kit/lib/tools/youtube-analytics.py: one window of YouTube Analytics into a snapshot.

The tool is loaded with `cmd_run._load_tool` and called directly with a ``call`` of the keys
kit/lib/tools/__init__.py lists. The key file `platform.youtube` (mode 0600) holds invented values, and
`urllib.request.build_opener` returns a fake opener that logs each request and answers it from the recorded
answer the scenario names for its method and URL (tests/fixtures/tool_youtube_analytics/). Nothing reaches
the network.
"""

import email.message
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import cmd_run, lint  # noqa: E402
from kit.lib.tools import Down, Failed  # noqa: E402

ANSWERS = ROOT / "tests" / "fixtures" / "tool_youtube_analytics"
CLEAN = ROOT / "tests" / "fixtures" / "lint_telemetry" / "clean" / "distribution" / "ep01" / "youtube" / "snapshot.json"
RESOURCE = "platform.youtube"
REQUEST_PATH = "distribution/ep01/youtube/requests/window.json"
REQUEST = {"end": "2026-11-19", "start": "2026-11-13", "video": "inventedVid"}
KEY_LINES = ["invented-client-id", "invented-client-secret", "invented-refresh-token"]
SECRETS = KEY_LINES + ["invented-access-token"]
TOKEN_URL = "https://oauth2.googleapis.com/token"
TOKEN_BODY = (b"client_id=invented-client-id&client_secret=invented-client-secret"
              b"&refresh_token=invented-refresh-token&grant_type=refresh_token")
REPORTS = "https://youtubeanalytics.googleapis.com/v2/reports?"
COMMON = [("ids", "channel==MINE"), ("startDate", "2026-11-13"), ("endDate", "2026-11-19")]
FILTER = [("filters", "video==inventedVid")]
TOTALS_URL = REPORTS + urllib.parse.urlencode(
    COMMON + [("metrics", "views,averageViewDuration,averageViewPercentage")] + FILTER)
RETENTION_URL = REPORTS + urllib.parse.urlencode(
    COMMON + [("dimensions", "elapsedVideoTimeRatio"), ("metrics", "audienceWatchRatio")] + FILTER)
REASONS = ("request is not a window request", "platform unreachable", "read refused", "answer malformed")
AUTH = "Bearer invented-access-token"


def answer(name):
    return json.loads((ANSWERS / f"{name}.json").read_text())


def clean_scenario():
    return {("POST", TOKEN_URL): answer("token-ok"), ("GET", TOTALS_URL): answer("totals-ok"),
            ("GET", RETENTION_URL): answer("retention-ok")}


class FakeResponse:
    def __init__(self, status, headers, body):
        self.status, self.headers, self._body = status, headers, io.BytesIO(body)

    def read(self, size=-1):
        return self._body.read(size)

    def close(self):
        pass


class FakeOpener:
    """Logs ``(method, URL, headers, body)`` of each request and answers it from the scenario."""

    def __init__(self, scenario):
        self.scenario, self.requests = scenario, []

    def open(self, request, timeout=None):
        method, url = request.get_method(), request.full_url
        headers = {name.lower(): value for name, value in request.header_items()}
        self.requests.append((method, url, headers, request.data))
        self.timeout = timeout
        found = self.scenario[(method, url)]
        if isinstance(found, Exception):
            raise found
        payload = json.dumps(found["body"]).encode()
        message = email.message.Message()
        for name, value in found["headers"].items():
            message[name] = value
        if found["status"] >= 400:
            raise urllib.error.HTTPError(url, found["status"], "error", message, io.BytesIO(payload))
        return FakeResponse(found["status"], message, payload)


class YoutubeAnalyticsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.out = self.tmp / "out"
        self.out.mkdir()
        self.key = self.tmp / "platform.youtube"
        self.write_key("\n".join(KEY_LINES) + "\n")
        self.tool = cmd_run._load_tool("youtube-analytics")

    def tearDown(self):
        self._tmp.cleanup()

    def write_key(self, text):
        self.key.write_text(text)
        os.chmod(self.key, 0o600)

    def call(self, text=None):
        text = json.dumps(REQUEST, sort_keys=True) + "\n" if text is None else text
        return {"hash": "0" * 64, "ticket": 3, "caller": "telemetry-puller",
                "expected": {"compute_min": 0, "usd": 0},
                "inputs": [{"path": REQUEST_PATH, "sha256": hashlib.sha256(text.encode()).hexdigest(),
                            "text": text}],
                "out": str(self.out), "settings": {}, "secrets": {RESOURCE: str(self.key)}}

    def run_tool(self, scenario, text=None):
        opener = FakeOpener(scenario)
        with mock.patch.object(urllib.request, "build_opener", return_value=opener) as build:
            try:
                result = self.tool.run(self.call(text))
            except (Failed, Down) as error:
                result = error
        self.build, self.timeout = build, getattr(opener, "timeout", None)
        return result, opener.requests

    def outputs(self):
        return sorted(p.name for p in self.out.iterdir())

    def assertFailed(self, result, reason):
        self.assertIsInstance(result, Failed)
        self.assertEqual((result.reason, result.usd, result.compute_min), (reason, 0, 0))

    def assertNoSecret(self, result):
        texts = [p.read_text() for p in self.out.iterdir()]
        if isinstance(result, Failed):
            texts.append(result.reason)
        for text in texts:
            for secret in SECRETS:
                self.assertNotIn(secret, text)

    def test_interface(self):
        self.assertEqual(self.tool.REASONS, REASONS)
        self.assertEqual(self.tool.RESOURCE, RESOURCE)

    def test_clean_pull_writes_the_snapshot_the_lint_passes(self):
        result, requests = self.run_tool(clean_scenario())
        self.assertEqual(result, {"usd": 0, "compute_min": 0})
        self.assertEqual([(m, u) for m, u, _, _ in requests],
                         [("POST", TOKEN_URL), ("GET", TOTALS_URL), ("GET", RETENTION_URL)])
        self.assertEqual(requests[0][3], TOKEN_BODY)
        self.assertEqual(self.timeout, 60)
        for _, _, headers, _ in requests[1:]:
            self.assertEqual(headers["authorization"], AUTH)
        self.assertEqual(self.outputs(), ["snapshot.json"])
        self.assertEqual((self.out / "snapshot.json").read_bytes(), CLEAN.read_bytes())
        self.assertNoSecret(result)
        tree = self.tmp / "tree"
        target = tree / "distribution" / "ep01" / "youtube" / "snapshot.json"
        target.parent.mkdir(parents=True)
        target.write_bytes((self.out / "snapshot.json").read_bytes())
        self.assertEqual(lint.run(tree, ["telemetry"])["telemetry"].status, lint.PASS)

    def test_opener_reads_no_proxy_and_follows_no_redirect(self):
        self.run_tool(clean_scenario())
        handlers = self.build.call_args.args
        self.assertEqual(len(handlers), 2)
        self.assertIsInstance(handlers[0], urllib.request.ProxyHandler)
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], urllib.request.HTTPRedirectHandler)
        self.assertIsNone(handlers[1].redirect_request(None, None, 302, "", {}, "https://elsewhere.invalid/"))

    def test_empty_totals_are_null(self):
        scenario = clean_scenario()
        scenario[("GET", TOTALS_URL)] = answer("totals-empty")
        result, _ = self.run_tool(scenario)
        self.assertEqual(result, {"usd": 0, "compute_min": 0})
        metrics = json.loads((self.out / "snapshot.json").read_text())["metrics"]
        self.assertEqual([metrics[k] for k in ("views", "averageViewDuration", "averageViewPercentage")],
                         [None, None, None])

    def test_request_defects(self):
        extra = dict(REQUEST, title="x")
        short = dict(REQUEST, video="inventedVi")
        late = dict(REQUEST, start="2026-11-20")
        for text in ("{not json", json.dumps(extra), json.dumps(short), json.dumps(late)):
            with self.subTest(text=text):
                result, requests = self.run_tool(clean_scenario(), text)
                self.assertFailed(result, "request is not a window request")
                self.assertEqual(requests, [])
                self.assertEqual(self.outputs(), [])

    def test_key_file_of_two_lines_is_down(self):
        self.write_key("invented-client-id\ninvented-client-secret\n")
        result, requests = self.run_tool(clean_scenario())
        self.assertIsInstance(result, Down)
        self.assertEqual(requests, [])

    def test_missing_key_file_is_down(self):
        self.key.unlink()
        result, requests = self.run_tool(clean_scenario())
        self.assertIsInstance(result, Down)
        self.assertEqual(requests, [])

    def test_invalid_grant_is_down(self):
        scenario = clean_scenario()
        scenario[("POST", TOKEN_URL)] = answer("token-invalid-grant")
        result, requests = self.run_tool(scenario)
        self.assertIsInstance(result, Down)
        self.assertEqual(len(requests), 1)
        self.assertEqual(self.outputs(), [])

    def test_refused_totals(self):
        scenario = clean_scenario()
        scenario[("GET", TOTALS_URL)] = answer("refused")
        result, requests = self.run_tool(scenario)
        self.assertFailed(result, "read refused")
        self.assertEqual(len(requests), 2)
        self.assertEqual(self.outputs(), [])
        self.assertNoSecret(result)

    def test_malformed_totals(self):
        scenario = clean_scenario()
        scenario[("GET", TOTALS_URL)] = dict(answer("totals-ok"), status=200, body={"rows": []})
        result, requests = self.run_tool(scenario)
        self.assertFailed(result, "answer malformed")
        self.assertEqual(len(requests), 2)
        self.assertEqual(self.outputs(), [])
        self.assertNoSecret(result)

    def test_unreachable_retention(self):
        scenario = clean_scenario()
        scenario[("GET", RETENTION_URL)] = urllib.error.URLError("down")
        result, requests = self.run_tool(scenario)
        self.assertFailed(result, "platform unreachable")
        self.assertEqual(len(requests), 3)
        self.assertEqual(self.outputs(), [])
        self.assertNoSecret(result)


if __name__ == "__main__":
    unittest.main()
