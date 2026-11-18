"""Tests for kit/lib/ec2.py: the driver `ec2` against written sessions replayed on 127.0.0.1.

`Ec2Replay` is one `ThreadingHTTPServer` on 127.0.0.1, port 0, in a thread. It answers each `POST` with the
next exchange of a session when the path, the signature and the fields are the exchange's, and 400
`ReplayMismatch` otherwise; a cleanup asserts that no request mismatched. `session(name)` reads the exchanges
of `tests/fixtures/render/ec2/<name>/`. The clock and the sleep of a node are replaced on the instance, so a
test never waits. Nothing reaches a host other than 127.0.0.1.
"""

import base64
import copy
import hashlib
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from kit.lib import assets, ec2, media, node, sigv4  # noqa: E402
from test_media import free_port  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "render" / "ec2"
KEY = ("factory-test-key-id", "factory-test-secret-0001", "test-region-7")
SETTINGS = {"RENDER_PROVIDER": "ec2", "RENDER_INSTANCE_TYPE": "g9.test", "RENDER_MACHINE_IMAGE": "ami-1234567890abcdef0",
            "RENDER_TAG": "project=test", "RENDER_USD_HOUR": "0.526", "RENDER_MAX_MIN": "75"}
IID = "i-1234567890abcdef0"
NOON = "2026-11-14T12:00:00Z"
BILLED = {"usd": 0.0877, "compute_min": 10.0}
NOTHING = {"usd": 0, "compute_min": 0}
TOKEN = re.compile(r"[0-9a-f]{32}")
UNAVAILABLE = ("InsufficientInstanceCapacity", "InstanceLimitExceeded", "VcpuLimitExceeded", "RequestLimitExceeded",
               "Unsupported", "ServiceUnavailable", "Unavailable")
REFUSED = ("UnauthorizedOperation", "InvalidAMIID.NotFound")
REASON = "<reason>User initiated (2026-11-14 12:10:00 GMT)</reason>"
PUBLIC_REAL = ("<ipAddress>203.0.113.9</ipAddress><dnsName>ec2-203-0-113-9.compute-1.amazonaws.com</dnsName>"
               "<macAddress>0e:1a:2b:3c:4d:5e</macAddress><attachmentId>eni-attach-0a1b2c3d4e5f60718</attachmentId>")
PUBLIC_INVENTED = ("<ipAddress>192.0.2.2</ipAddress><dnsName>ec2-192-0-2-2.compute-1.amazonaws.com</dnsName>"
                   "<macAddress>02:00:00:00:00:01</macAddress><attachmentId>eni-attach-1234567890abcdef0</attachmentId>")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def session(name):
    """The exchanges of the fixture directory ``name``, in the order of the number before the first `-`."""
    paths = sorted((FIXTURES / name).glob("*.json"), key=lambda path: int(path.name.split("-", 1)[0]))
    return [json.loads(path.read_text(encoding="utf-8")) for path in paths]


def error(code):
    return json.loads((FIXTURES / "errors" / f"{code}.json").read_text(encoding="utf-8"))


def answered(exchange, code, status=400):
    """``exchange``'s request answered with the error ``code``."""
    found = copy.deepcopy(exchange)
    found["response"] = {"status": status, "body": (
        f"<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<Response><Errors><Error><Code>{code}</Code><Message>"
        "invented</Message></Error></Errors><RequestID>00000000-0000-4000-8000-000000000009</RequestID></Response>\n")}
    return found


def with_body(exchange, old, new):
    found = copy.deepcopy(exchange)
    assert old in found["response"]["body"]
    found["response"]["body"] = found["response"]["body"].replace(old, new)
    return found


def at(text):
    return datetime.strptime(text, sigv4.TIME_FORMAT).replace(tzinfo=timezone.utc)


class Ec2Replay:
    """A replay of ``exchanges`` on 127.0.0.1, checking each request's path, signature under ``key`` and fields.

    A name of ``loose`` must only be present; `ClientToken` must be 32 lower-case hex characters, and `UserData`
    compares as `sha256:<hex>` of its decoded bytes. A difference or a request past the end goes to
    ``mismatches`` and is answered 400 `ReplayMismatch`. With n in ``cut``, the n-th request (counted from 1)
    is read and the connection closed without a status line; its exchange answers the next request.
    ``requests`` holds the fields of every request."""

    def __init__(self, test, exchanges, key, loose=("ClientToken", "UserData")):
        self.exchanges, self.key, self.loose = list(exchanges), key, tuple(loose)
        self.mismatches, self.requests, self.cut, self.next = [], [], set(), 0
        replay = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                replay.handle(self)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        threading.Thread(target=self.server.serve_forever, args=(0.01,), daemon=True).start()
        test.addCleanup(lambda: test.assertEqual(replay.mismatches, []))
        test.addCleanup(self.server.server_close)
        test.addCleanup(self.server.shutdown)

    def handle(self, h):
        body = h.rfile.read(int(h.headers.get("Content-Length") or 0))
        fields = dict(urllib.parse.parse_qsl(body.decode("utf-8"), keep_blank_values=True))
        self.requests.append(fields)
        n = len(self.requests)
        if n in self.cut:
            h.close_connection = True
            return
        name = f"request {n} ({fields.get('Action')})"
        if self.next >= len(self.exchanges):
            return self.mismatch(h, f"{name}: past the end")
        expected = self.exchanges[self.next]["request"]
        problems = [] if h.path == expected["path"] else ["path"]
        if not self.signed(h, body):
            problems.append("signature")
        problems += self.differences(fields, expected["params"])
        if problems:
            return self.mismatch(h, f"{name}: " + ", ".join(problems))
        response = self.exchanges[self.next]["response"]
        self.next += 1
        self.reply(h, response["status"], response["body"])

    def signed(self, h, body):
        headers = {name.lower(): value for name, value in h.headers.items()}
        stamp = headers.get("x-amz-date", "")
        try:
            time = datetime.strptime(stamp, sigv4.STAMP_FORMAT).strftime(sigv4.TIME_FORMAT)
        except ValueError:
            return False
        expected = sigv4.authorization("POST", self.url.rstrip("/") + h.path,
                                       {"content-type": headers.get("content-type", ""), "x-amz-date": stamp},
                                       sha(body), self.key, "ec2", time)
        return headers.get("authorization") == expected

    def differences(self, fields, expected):
        problems = []
        if "ClientToken" in fields and not TOKEN.fullmatch(fields["ClientToken"]):
            problems.append("ClientToken")
        for name in sorted(set(fields) | set(expected)):
            if name in self.loose:
                if name not in fields:
                    problems.append(name)
                continue
            value = fields.get(name)
            if name == "UserData" and value is not None:
                value = "sha256:" + sha(base64.b64decode(value))
            if value != expected.get(name):
                problems.append(name)
        return problems

    def mismatch(self, h, text):
        self.mismatches.append(text)
        self.reply(h, 400, "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<Response><Errors><Error><Code>"
                           "ReplayMismatch</Code><Message>replay</Message></Error></Errors></Response>\n")

    def reply(self, h, status, text):
        data = text.encode("utf-8")
        h.send_response(status)
        h.send_header("Content-Type", "text/xml;charset=UTF-8")
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)


class Case(unittest.TestCase):
    def node(self, url, settings=None, key=KEY, record=None, clock=NOON, key_file=None):
        """A node on ``url`` whose clock reads ``clock`` and whose sleeps are kept in ``sleeps``."""
        if key_file is None:
            directory = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, directory)
            key_file = os.path.join(directory, "render.key")
            Path(key_file).write_text("\n".join(key) + "\n", encoding="utf-8")
        job = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, job)
        Path(job, "engine.unreal").write_text("seeded-pull-token\n", encoding="utf-8")
        Path(job, "store.assets").write_text("\n".join(KEY) + "\n", encoding="utf-8")
        config = {"settings": {**SETTINGS, "RENDER_API_URL": url, "ASSET_STORE": "https://store.invalid/b",
                               "UNREAL_IMAGE": "registry.invalid/engine@sha256:" + "0" * 64, **(settings or {})},
                  "secrets": {"render.node": key_file, "engine.unreal": os.path.join(job, "engine.unreal"),
                              "store.assets": os.path.join(job, "store.assets")}}
        driver = ec2.Node(key_file, config, record)
        moment = at(clock)
        driver.now = lambda: moment
        driver.sleeps = []
        driver.sleep = driver.sleeps.append
        return driver

    def replay(self, exchanges, **kwargs):
        return Ec2Replay(self, exchanges, KEY, **kwargs)

    def started(self, exchanges, **kwargs):
        replay = self.replay(exchanges)
        driver = self.node(replay.url, **kwargs)
        driver.start()
        return replay, driver


class B1Billing(Case):
    def test_end_from_the_reason(self):
        replay, driver = self.started(session("start-stop"))
        billed = driver.stop()
        self.assertEqual(billed, BILLED)
        self.assertIs(type(billed["usd"]), float)
        self.assertIs(type(billed["compute_min"]), float)
        self.assertEqual(len(replay.requests), 6)

    def test_no_time_in_the_reason_ends_when_the_poll_is_parsed(self):
        exchanges = session("start-stop")
        exchanges[5] = with_body(exchanges[5], REASON, "<reason/>")
        replay, driver = self.started(exchanges, clock="2026-11-14T12:10:15Z")
        self.assertEqual(driver.stop(), {"usd": 0.0899, "compute_min": 10.25})


class B2AtLeastOneMinute(Case):
    def test_twenty_seconds_bill_one_minute(self):
        exchanges = session("start-stop")
        exchanges[5] = with_body(exchanges[5], REASON, "<reason>User initiated (2026-11-14 12:00:20 GMT)</reason>")
        replay, driver = self.started(exchanges)
        self.assertEqual(driver.stop(), {"usd": 0.0088, "compute_min": 1.0})

    def test_second_stop_sends_nothing(self):
        replay, driver = self.started(session("start-stop"))
        first = driver.stop()
        self.assertEqual(driver.stop(), first)
        self.assertEqual(len(replay.requests), 6)


class B3Unavailable(Case):
    def test_each_code_that_says_no_node_is_unavailable(self):
        for code in UNAVAILABLE:
            with self.subTest(code=code):
                replay = self.replay(session("start-stop")[:1] + [error(code)])
                driver = self.node(replay.url)
                with self.assertRaises(node.Unavailable) as raised:
                    driver.start()
                self.assertEqual(str(raised.exception), f"ec2 {code}")
                self.assertEqual(driver.stop(), NOTHING)
                self.assertEqual(len(replay.requests), 2)

    def test_other_codes_are_refused(self):
        for code in REFUSED:
            with self.subTest(code=code):
                replay = self.replay(session("start-stop")[:1] + [error(code)])
                driver = self.node(replay.url)
                with self.assertRaises(ec2.Refused) as raised:
                    driver.start()
                self.assertEqual(str(raised.exception), code)
                self.assertEqual(driver.stop(), NOTHING)
                self.assertEqual(len(replay.requests), 2)

    def test_no_capacity_after_launch_is_unavailable_and_forgotten(self):
        replay = self.replay(session("no-capacity"))
        driver = self.node(replay.url)
        with self.assertRaises(node.Unavailable) as raised:
            driver.start()
        self.assertEqual(str(raised.exception), "ec2 Server.InsufficientInstanceCapacity")
        self.assertEqual(driver.stop(), NOTHING)
        self.assertEqual(len(replay.requests), 4)

    def test_a_server_error_with_no_code_is_no_answer(self):
        exchanges = session("start-stop")[:1]
        exchanges[0]["response"] = {"status": 502, "body": "<html>bad gateway</html>"}
        replay = self.replay(exchanges)
        with self.assertRaises(node.Unavailable) as raised:
            self.node(replay.url).start()
        self.assertEqual(str(raised.exception), "ec2 no answer")

    def test_a_200_that_is_not_xml_is_refused(self):
        exchanges = session("start-stop")[:1]
        exchanges[0]["response"] = {"status": 200, "body": "not xml"}
        replay = self.replay(exchanges)
        with self.assertRaises(ec2.Refused) as raised:
            self.node(replay.url).start()
        self.assertEqual(str(raised.exception), "answer is not XML")

    def test_an_image_with_no_root_device_is_refused(self):
        exchanges = session("start-stop")[:1]
        exchanges[0] = with_body(exchanges[0], "<rootDeviceName>/dev/sda1</rootDeviceName>", "")
        replay = self.replay(exchanges)
        with self.assertRaises(ec2.Refused) as raised:
            self.node(replay.url).start()
        self.assertEqual(str(raised.exception), "image ami-1234567890abcdef0 has no root device")


class B4NoAnswer(Case):
    def test_no_server_is_unavailable(self):
        driver = self.node(f"http://127.0.0.1:{free_port()}/")
        with self.assertRaises(node.Unavailable) as raised:
            driver.start()
        self.assertEqual(str(raised.exception), "ec2 no answer")
        self.assertEqual(driver.stop(), NOTHING)

    def test_a_lost_launch_is_sent_again_with_its_token(self):
        replay = self.replay(session("start-stop"))
        replay.cut = {2}
        driver = self.node(replay.url)
        driver.start()
        self.assertEqual([fields["Action"] for fields in replay.requests[:3]],
                         ["DescribeImages", "RunInstances", "RunInstances"])
        self.assertEqual(replay.requests[1], replay.requests[2])
        self.assertEqual(driver.instance_id, IID)
        self.assertEqual(driver.stop(), BILLED)
        self.assertEqual(replay.next, 6)

    def test_a_launch_lost_twice_is_unavailable(self):
        replay = self.replay(session("start-stop"))
        replay.cut = {2, 3}
        driver = self.node(replay.url)
        with self.assertRaises(node.Unavailable) as raised:
            driver.start()
        self.assertEqual(str(raised.exception), "ec2 no answer")
        self.assertEqual(driver.stop(), NOTHING)
        self.assertEqual(len(replay.requests), 3)

    def test_a_lost_end_is_sent_again(self):
        replay, driver = self.started(session("start-stop"))
        replay.cut = {5}
        self.assertEqual(driver.stop(), BILLED)
        self.assertEqual([fields["Action"] for fields in replay.requests[4:]],
                         ["TerminateInstances", "TerminateInstances", "DescribeInstances"])

    def test_an_end_lost_twice_is_refused(self):
        replay, driver = self.started(session("start-stop"))
        replay.cut = {5, 6}
        with self.assertRaises(ec2.Refused) as raised:
            driver.stop()
        self.assertEqual(str(raised.exception), "no answer")


class B5Polls(Case):
    def test_still_running_after_forty_polls(self):
        exchanges = session("start-stop")
        replay, driver = self.started(exchanges[:5] + [exchanges[3]] * 40)
        driver.sleeps.clear()
        with self.assertRaises(ec2.Refused) as raised:
            driver.stop()
        self.assertEqual(str(raised.exception), f"instance {IID} still running after 40 polls")
        self.assertEqual([fields["Action"] for fields in replay.requests[4:]],
                         ["TerminateInstances"] + ["DescribeInstances"] * 40)
        self.assertEqual(driver.sleeps, [15] * 39)

    def test_ended_on_the_third_poll(self):
        exchanges = session("start-stop")
        replay, driver = self.started(exchanges[:5] + [exchanges[3], exchanges[3], exchanges[5]])
        driver.sleeps.clear()
        self.assertEqual(driver.stop(), BILLED)
        self.assertEqual(driver.sleeps, [15, 15])

    def test_not_running_after_forty_polls(self):
        exchanges = session("start-stop")
        replay = self.replay(exchanges[:2] + [exchanges[2]] * 40)
        driver = self.node(replay.url)
        with self.assertRaises(ec2.Refused) as raised:
            driver.start()
        self.assertEqual(str(raised.exception), f"instance {IID} not running after 40 polls")
        self.assertEqual(driver.sleeps, [15] * 39)
        self.assertEqual(driver.instance_id, IID)

    def test_an_unknown_id_and_a_lost_poll_count_as_polls(self):
        exchanges = session("start-stop")
        replay = self.replay(exchanges[:2] + [answered(exchanges[2], "InvalidInstanceID.NotFound")] + exchanges[2:])
        replay.cut = {4}
        driver = self.node(replay.url)
        driver.start()
        self.assertEqual(driver.sleeps, [15, 15, 15])
        self.assertEqual(driver.stop(), BILLED)

    def test_another_code_in_a_poll_is_refused_and_stop_ends_the_instance(self):
        exchanges = session("start-stop")
        replay = self.replay(exchanges[:2] + [answered(exchanges[2], "UnauthorizedOperation")] + exchanges[4:])
        driver = self.node(replay.url)
        with self.assertRaises(ec2.Refused) as raised:
            driver.start()
        self.assertEqual(str(raised.exception), "UnauthorizedOperation")
        self.assertEqual(driver.stop(), BILLED)


class B6Request(Case):
    def test_launch_fields(self):
        replay, driver = self.started(session("start-stop"))
        launch = replay.requests[1]
        for n, resource in (("1", "instance"), ("2", "volume")):
            self.assertEqual(launch[f"TagSpecification.{n}.ResourceType"], resource)
            self.assertEqual(launch[f"TagSpecification.{n}.Tag.1.Key"], "project")
            self.assertEqual(launch[f"TagSpecification.{n}.Tag.1.Value"], "test")
        self.assertEqual(launch["InstanceInitiatedShutdownBehavior"], "terminate")
        self.assertEqual(launch["MetadataOptions.HttpPutResponseHopLimit"], "1")
        self.assertEqual(launch["MetadataOptions.HttpTokens"], "required")
        self.assertEqual(launch["BlockDeviceMapping.1.DeviceName"], "/dev/sda1")
        self.assertEqual(launch["BlockDeviceMapping.1.Ebs.DeleteOnTermination"], "true")
        self.assertEqual(base64.b64decode(launch["UserData"]).decode("utf-8"), driver.user_data())
        self.assertEqual(driver.user_data().splitlines()[:3], ["#!/bin/bash", driver.user_data().splitlines()[1], "shutdown -h +75"])

    def test_another_tag_mismatches(self):
        replay = self.replay(session("start-stop"))
        with self.assertRaises(ec2.Refused) as raised:
            self.node(replay.url, settings={"RENDER_TAG": "project=other"}).start()
        self.assertEqual(str(raised.exception), "ReplayMismatch")
        self.assertEqual(len(replay.mismatches), 1)
        self.assertIn("TagSpecification.1.Tag.1.Value", replay.mismatches[0])
        replay.mismatches.clear()

    def test_a_wrong_secret_mismatches_the_signature(self):
        replay = self.replay(session("start-stop"))
        with self.assertRaises(ec2.Refused):
            self.node(replay.url, key=(KEY[0], "factory-test-secret-0002", KEY[2])).start()
        self.assertEqual(replay.mismatches, ["request 1 (DescribeImages): signature"])
        replay.mismatches.clear()

    def test_invalid_settings_send_nothing(self):
        cases = (("RENDER_TAG", "project", "RENDER_TAG is not key=value"),
                 ("RENDER_TAG", "=test", "RENDER_TAG is not key=value"),
                 ("RENDER_TAG", "project=", "RENDER_TAG is not key=value"),
                 ("RENDER_USD_HOUR", "-1", "RENDER_USD_HOUR is not a number of at least 0"),
                 ("RENDER_USD_HOUR", "NaN", "RENDER_USD_HOUR is not a number of at least 0"),
                 ("RENDER_USD_HOUR", "one", "RENDER_USD_HOUR is not a number of at least 0"),
                 ("RENDER_MAX_MIN", "7.5", "RENDER_MAX_MIN is not a whole number of at least 1"),
                 ("RENDER_MAX_MIN", "0", "RENDER_MAX_MIN is not a whole number of at least 1"))
        for name, value, message in cases:
            with self.subTest(name=name, value=value):
                replay = self.replay(session("start-stop"))
                driver = self.node(replay.url, settings={name: value}, key_file="/nonexistent/render.key")
                with self.assertRaises(ec2.Refused) as raised:
                    driver.start()
                self.assertEqual(str(raised.exception), message)
                self.assertEqual(replay.requests, [])
                self.assertEqual(driver.stop(), NOTHING)

    def test_a_bad_key_passes_through(self):
        replay = self.replay(session("start-stop"))
        with self.assertRaises(media.Refused) as raised:
            self.node(replay.url, key_file="/nonexistent/render.key").start()
        self.assertEqual(str(raised.exception), assets.KEY_UNREADABLE)
        self.assertEqual(replay.requests, [])

    def test_the_endpoint_of_the_region(self):
        driver = self.node("")
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")) as opened:
            with self.assertRaises(node.Unavailable):
                driver.start()
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "https://ec2.test-region-7.amazonaws.com/")
        self.assertEqual(opened.call_args.kwargs, {"timeout": 30})
        self.assertEqual(request.get_header("X-amz-date"), "20261114T120000Z")


class B7Record(Case):
    REAL = ((r"\bi-1234567890abcdef0", "i-0a1b2c3d4e5f60718"),
            ("<ownerId>123456789012</ownerId>", "<ownerId>987654321098</ownerId>"),
            ("ip-192-0-2-1", "ip-10-1-2-3"), ("192.0.2.1", "10.1.2.3"))

    def real(self):
        text = json.dumps(session("start-stop"))
        for invented, real in self.REAL:
            text = re.sub(invented, real, text)
        return json.loads(text)

    def directory(self):
        found = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, found)
        return found

    def test_record_scrubs_every_exchange(self):
        record = self.directory()
        replay, driver = self.started(self.real(), record=record)
        self.assertEqual(driver.stop(), {"usd": 0.0877, "compute_min": 10.0})
        names = sorted(os.listdir(record))
        self.assertEqual(names, ["01-DescribeImages.json", "02-RunInstances.json", "03-DescribeInstances.json",
                                 "04-DescribeInstances.json", "05-TerminateInstances.json",
                                 "06-DescribeInstances.json"])
        text = "".join(Path(record, name).read_text(encoding="utf-8") for name in names)
        self.assertIn("i-0a1b2c3d4e5f60718", json.dumps(self.real()))
        self.assertEqual(set(re.findall(r"i-[0-9a-f]{17}", text)), {IID})
        self.assertEqual(set(re.findall(r"<ownerId>[0-9]+", text)), {"<ownerId>123456789012"})
        self.assertEqual({a for a in re.findall(r"\d+\.\d+\.\d+\.\d+", text) if not a.startswith("192.0.2.")}, set())
        self.assertNotIn("10-1-2-3", text)
        self.assertNotIn("uthorization", text)
        self.assertNotIn("X-Amz-Date", text)
        for name, fixture in zip(names, session("start-stop")):
            written = Path(record, name).read_text(encoding="utf-8")
            exchange = json.loads(written)
            self.assertEqual(written, json.dumps(exchange, indent=1, sort_keys=True) + "\n")
            self.assertEqual(set(exchange["request"]), {"method", "path", "params"})
            del fixture["source"]
            if "ClientToken" in fixture["request"]["params"]:
                fixture["request"]["params"]["ClientToken"] = replay.requests[1]["ClientToken"]
                fixture["request"]["params"]["UserData"] = exchange["request"]["params"]["UserData"]
            self.assertEqual(exchange, fixture)
        self.assertTrue(json.loads(Path(record, names[1]).read_text())["request"]["params"]["UserData"]
                        .startswith("sha256:"))

    def test_no_record_writes_nothing(self):
        here, cwd = self.directory(), os.getcwd()
        os.chdir(here)
        self.addCleanup(os.chdir, cwd)
        replay, driver = self.started(self.real())
        driver.stop()
        self.assertEqual(os.listdir(here), [])
        self.assertEqual(driver.recorded, 0)

    def test_one_old_value_one_new_value(self):
        scrub = ec2._Scrub()
        text = ("i-0a1b2c3d4e5f60718 i-0f0f0f0f0f0f0f0f0 i-0a1b2c3d4e5f60718 vol-0a1b2c3d "
                "<requesterId>111111111111</requesterId> arn:aws:iam::987654321098:role 10.1.2.3 ip-10-9-9-9 "
                "10.9.9.9 <RequestID>abc</RequestID> <requestId>def</requestId>")
        self.assertEqual(scrub.text(text), (
            "i-1234567890abcdef0 i-1234567890abcdef1 i-1234567890abcdef0 vol-0abcdef0 "
            "<requesterId>123456789012</requesterId> arn:aws:iam::123456789013:role 192.0.2.1 ip-192-0-2-2 "
            "192.0.2.2 <RequestID>00000000-0000-4000-8000-000000000001</RequestID> "
            "<requestId>00000000-0000-4000-8000-000000000002</requestId>"))

    def test_public_dns_name_takes_the_new_address(self):
        text = ("ip-10-1-2-3 <dnsName>ec2-203-0-113-9.compute-1.amazonaws.com</dnsName> "
                "<ipAddress>203.0.113.9</ipAddress> ec2-10-1-2-3 10.1.2.3")
        self.assertEqual(ec2._Scrub().text(text), (
            "ip-192-0-2-1 <dnsName>ec2-192-0-2-2.compute-1.amazonaws.com</dnsName> "
            "<ipAddress>192.0.2.2</ipAddress> ec2-192-0-2-1 192.0.2.1"))

    def test_mac_address(self):
        text = "<macAddress>0e:1a:2b:3c:4d:5e</macAddress> 0E:1A:2B:3C:4D:5E 0e:1a:2b:3c:4d:5f"
        self.assertEqual(ec2._Scrub().text(text),
                         "<macAddress>02:00:00:00:00:01</macAddress> 02:00:00:00:00:01 02:00:00:00:00:02")

    def test_attachment_id(self):
        text = "eni-attach-0a1b2c3d4e5f60718 eni-0a1b2c3d4e5f60718 eni-attach-0f0f0f0f0f0f0f0f0"
        self.assertEqual(ec2._Scrub().text(text),
                         "eni-attach-1234567890abcdef0 eni-1234567890abcdef0 eni-attach-1234567890abcdef1")

    def test_record_scrubs_public_names_macs_and_attachments(self):
        record = self.directory()
        exchanges = self.real()
        exchanges[3] = with_body(exchanges[3], "<instanceState>", PUBLIC_REAL + "<instanceState>")
        replay, driver = self.started(exchanges, record=record)
        driver.stop()
        self.assertIn(PUBLIC_INVENTED, json.loads(Path(record, "04-DescribeInstances.json").read_text(
            encoding="utf-8"))["response"]["body"])
        text = "".join(Path(record, name).read_text(encoding="utf-8") for name in sorted(os.listdir(record)))
        self.assertEqual(len(os.listdir(record)), 6)
        for real in ("203.0.113.9", "203-0-113-9", "0e:1a:2b:3c:4d:5e", "eni-attach-0a1b2c3d4e5f60718"):
            self.assertNotIn(real, text)

    def test_invented_and_other_values_stay(self):
        text = ("<privateIpAddress>192.0.2.1</privateIpAddress>" + PUBLIC_INVENTED + "<dnsName></dnsName>"
                "<launchTime>2026-11-15T09:51:51.000Z</launchTime><availabilityZone>us-east-1c</availabilityZone>"
                "<clientToken>ce847f4ad2cdef8b49c0fc34113fc6e6</clientToken>")
        self.assertEqual(ec2._Scrub().text(text), text)


class B8Imports(unittest.TestCase):
    def python(self, code):
        done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip()

    def test_import_leaves_node_out(self):
        self.assertEqual(self.python("import sys; sys.path.insert(0, '.'); from kit.lib import ec2; "
                                     "print('kit.lib.node' in sys.modules)"), "False")

    def test_unavailable_brings_node_in(self):
        port = free_port()
        code = (
            "import os, sys, tempfile; sys.path.insert(0, '.'); from kit.lib import ec2\n"
            "key = os.path.join(tempfile.mkdtemp(), 'k'); open(key, 'w').write('a\\nb\\nc\\n')\n"
            f"settings = {dict(SETTINGS, RENDER_API_URL=f'http://127.0.0.1:{port}/')!r}\n"
            "settings['ASSET_STORE'] = 'https://store.invalid/b'\n"
            "driver = ec2.Node(key, {'settings': settings, 'secrets': {'engine.unreal': key, 'store.assets': key}})\n"
            "try:\n    driver.start()\nexcept Exception as error:\n"
            "    print(type(error).__module__, type(error).__name__, 'kit.lib.node' in sys.modules)\n")
        self.assertEqual(self.python(code), "kit.lib.node Unavailable True")


class B9Interface(unittest.TestCase):
    def test_names(self):
        self.assertEqual(ec2.Node.NEEDS, ("RENDER_INSTANCE_TYPE", "RENDER_MACHINE_IMAGE", "RENDER_TAG",
                                          "RENDER_USD_HOUR", "RENDER_MAX_MIN", "RENDER_RESOLUTION", "UNREAL_IMAGE",
                                          "ASSET_STORE", "engine.unreal", "store.assets"))
        self.assertEqual(ec2.Node.PROGRAMS, ("ffmpeg", "ffprobe", "tesseract"))
        self.assertEqual(str(ec2.Refused("user data too large")), "user data too large")

    def test_init_stores_and_run_is_later(self):
        driver = ec2.Node(None, None)
        with self.assertRaises(ec2.Refused):
            driver.run({})
        self.assertEqual(driver.stop(), NOTHING)
        self.assertIsNotNone(ec2.Node.now().tzinfo)



class B10CheckFailure(unittest.TestCase):
    """``failure["check"]`` (#606): the text of a check of the node's answer that failed on this machine. The job
    runs on the harness ``Job`` of tests/test_renderjob.py, imported here when a test starts (it imports this file)."""

    def setUp(self):
        import test_renderjob
        self.renderjob = test_renderjob
        self.job = test_renderjob.Job()
        self.job.addCleanup = self.addCleanup
        self.job.setUp()

    def test_a_scene_json_that_scenestate_refuses(self):
        scene = json.loads(dict(self.job.members)["scene.json"].decode("utf-8"))
        del scene["camera"]
        self.job.members = [(n, d) if n != "scene.json" else (n, json.dumps(scene).encode())
                            for n, d in self.job.members]
        check = "the node's answer failed a check: no actor labelled camera"
        _, driver = self.job.assert_failed(check)
        self.assertEqual(driver.failure["check"], check)
        self.assertEqual((driver.failure["step"], driver.failure["tail"]), (None, ["NODE 12:01:00 result ok"]))

    def test_a_clean_answer_leaves_no_failure(self):
        _, driver, error, billed, _ = self.job.session_run()
        self.assertIsNone(error)
        self.assertEqual(billed, BILLED)
        self.assertIsNone(driver.failure)
        self.assertTrue(os.path.isfile(os.path.join(self.job.out, "state.json")))

    def test_a_failed_node_step_is_no_check(self):
        self.job.members = None
        self.job.status = b'{"result": "failed", "step": "rebuild"}\n'
        self.job.log = b"NODE 12:01:00 step rebuild rc=139 seconds=12\nNODE 12:01:00 result failed step=rebuild\n"
        _, driver = self.job.assert_failed(self.renderjob.renderjob.JOB_FAILED)
        self.assertEqual(driver.failure, {"log": f"jobs/{driver.nonce}/log.txt", "step": "rebuild",
                                          "tail": ["NODE 12:01:00 step rebuild rc=139 seconds=12",
                                                   "NODE 12:01:00 result failed step=rebuild"],
                                          "check": None})


if __name__ == "__main__":
    unittest.main()
