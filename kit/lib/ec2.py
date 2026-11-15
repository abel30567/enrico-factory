"""The render node driver `ec2`: one instance of the cloud's compute API, started for a job and ended after it.

``Node(key_file, config, record=None)`` reads the key ``(key_id, secret, region)`` of ``assets.read_key``
and the settings of ``NEEDS``. Every request is a signed `POST` of a Query API form (``sigv4``) to
``RENDER_API_URL`` when that setting is there and not empty, else to the region's endpoint. ``start()``
reads the machine image's root device, launches one instance that ends itself (terminate on shutdown, the
start script's first command a shutdown at ``RENDER_MAX_MIN``, the root disk deleted with it) and waits
until it is running. ``stop()`` ends it (end, not stop: nothing is kept), waits until it is
``shutting-down`` or ``terminated`` and returns the minutes and dollars from its launch time to its end.

``node.Unavailable`` is raised only before an instance id is known, or for an instance the cloud ended
for want of capacity; any other refusal is ``Refused`` with the error code or a message as its text.
With ``record`` a directory, every answered exchange is written there with its ids, accounts, request
ids and addresses replaced by invented ones, and no header.
"""

import base64
import hashlib
import http.client
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal, InvalidOperation

from kit.lib import assets, media, sigv4  # noqa: F401  (media.Refused passes through from assets.read_key)

VERSION = "2016-11-15"
CONTENT_TYPE = "application/x-www-form-urlencoded; charset=utf-8"
TIMEOUT = 30
POLLS = 40
POLL_SECONDS = 15
UNAVAILABLE = ("InsufficientInstanceCapacity", "InstanceLimitExceeded", "VcpuLimitExceeded",
               "RequestLimitExceeded", "Unsupported", "ServiceUnavailable", "Unavailable")
NOT_FOUND = "InvalidInstanceID.NotFound"
NO_CAPACITY = "Server.InsufficientInstanceCapacity"
ENDED = ("shutting-down", "terminated")

BAD_TAG = "RENDER_TAG is not key=value"
BAD_USD_HOUR = "RENDER_USD_HOUR is not a number of at least 0"
BAD_MAX_MIN = "RENDER_MAX_MIN is not a whole number of at least 1"
NOT_XML = "answer is not XML"
NO_ANSWER = "no answer"

_DIGITS = re.compile(r"[0-9]+")
_LAUNCH_TIME = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?Z")
_USER_INITIATED = re.compile(r"User initiated \((\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) GMT\)")
_FOURTH = Decimal("0.0001")


class Refused(Exception):
    """The cloud or the settings refused the node; ``str(error)`` is the error code or the message."""


class _Code(Exception):
    """An answer holding an error code."""


class _NoAnswer(Exception):
    """No answer: a lost connection, a cut answer, or a status other than 200 with no error code."""


def _unavailable(text):
    from kit.lib import node
    raise node.Unavailable(text)


def _ceil4(value):
    return value.quantize(_FOURTH, rounding=ROUND_CEILING)


class _Scrub:
    """One session's map of real values to invented ones: one old value always gets one new value."""

    IDS = re.compile(r"\b(i|r|vol|eni|subnet|vpc|sg|ami)-([0-9a-f]{17}|[0-9a-f]{8})\b")
    ACCOUNT_ELEMENT = re.compile(r"(<(ownerId|requesterId)>)([^<]*)(</\2>)")
    ACCOUNT_ARN = re.compile(r"(?<=:)([0-9]{12})(?=:)")
    REQUEST_ID = re.compile(r"(<(requestId|RequestID)>)([^<]*)(</\2>)")
    ADDRESS = re.compile(r"\bip-([0-9]{1,3})-([0-9]{1,3})-([0-9]{1,3})-([0-9]{1,3})\b"
                         r"|(?<![0-9.])([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})(?![0-9]|\.[0-9])")

    def __init__(self):
        self.seen = {}

    def _new(self, kind, old, make):
        values = self.seen.setdefault(kind, {})
        if old not in values:
            values[old] = make(len(values))
        return values[old]

    def _id(self, match):
        prefix, digits = match.groups()
        new = self._new("id " + prefix, digits, lambda k: format(0x1234567890abcdef0 + k, "x"))
        return f"{prefix}-{new[-len(digits):]}"

    def _account(self, old):
        return self._new("account", old, lambda k: str(123456789012 + k))

    def _address(self, match):
        """An IPv4 address, dotted or dashed (``ip-10-1-2-3``), as ``192.0.2.<k + 1>`` in the same form."""
        dashed = match.group(1) is not None
        parts = match.groups()[:4] if dashed else match.groups()[4:]
        new = self._new("address", ".".join(parts), lambda k: f"192.0.2.{k + 1}")
        return "ip-" + new.replace(".", "-") if dashed else new

    def text(self, text):
        text = self.REQUEST_ID.sub(lambda m: m.group(1) + self._new(
            "request", m.group(3), lambda k: f"00000000-0000-4000-8000-{k + 1:012x}") + m.group(4), text)
        text = self.ACCOUNT_ELEMENT.sub(lambda m: m.group(1) + self._account(m.group(3)) + m.group(4), text)
        text = self.ACCOUNT_ARN.sub(lambda m: self._account(m.group(1)), text)
        text = self.IDS.sub(self._id, text)
        return self.ADDRESS.sub(self._address, text)


class Node:
    NEEDS = ("RENDER_INSTANCE_TYPE", "RENDER_MACHINE_IMAGE", "RENDER_TAG", "RENDER_USD_HOUR", "RENDER_MAX_MIN")
    PROGRAMS = ()

    sleep = staticmethod(time.sleep)

    @staticmethod
    def now():
        """The time of a request's signature and of an end the state reason does not give: aware, UTC."""
        return datetime.now(timezone.utc)

    def __init__(self, key_file, config, record=None):
        self.key_file, self.config, self.record = key_file, config, record
        self.instance_id = self.billed = self.key = self.url = self.rate = None
        self.recorded = 0
        self.scrub = _Scrub()

    def _setting(self, name):
        return str(self.config["settings"].get(name, ""))

    def user_data(self):
        """The start script; its first command ends the instance at ``RENDER_MAX_MIN``."""
        return f"#!/bin/bash\nshutdown -h +{self._setting('RENDER_MAX_MIN')}\n"

    def run(self, job):
        raise NotImplementedError("run is #464")

    def _settings(self):
        tag_key, equals, tag_value = self._setting("RENDER_TAG").partition("=")
        if not equals or not tag_key or not tag_value:
            raise Refused(BAD_TAG)
        try:
            rate = Decimal(self._setting("RENDER_USD_HOUR"))
        except InvalidOperation:
            raise Refused(BAD_USD_HOUR) from None
        if not rate.is_finite() or rate < 0:
            raise Refused(BAD_USD_HOUR)
        max_min = self._setting("RENDER_MAX_MIN")
        if not _DIGITS.fullmatch(max_min) or int(max_min) < 1:
            raise Refused(BAD_MAX_MIN)
        return tag_key, tag_value, rate

    def _record(self, action, path, params, status, body):
        if self.record is None:
            return
        fields = dict(params)
        if "UserData" in fields:
            fields["UserData"] = "sha256:" + hashlib.sha256(base64.b64decode(fields["UserData"])).hexdigest()
        fields = {name: self.scrub.text(value) for name, value in sorted(fields.items())}
        exchange = {"request": {"method": "POST", "path": path, "params": fields},
                    "response": {"status": status, "body": self.scrub.text(body)}}
        self.recorded += 1
        name = os.path.join(self.record, f"{self.recorded:02d}-{action}.json")
        with open(name, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(exchange, indent=1, sort_keys=True) + "\n")

    def _request(self, action, fields):
        """The parsed answer to ``action``; raises ``_Code``, ``_NoAnswer`` or ``Refused(NOT_XML)``."""
        params = {"Action": action, "Version": VERSION}
        params.update(fields)
        body = sigv4.form(params).encode("utf-8")
        moment = self.now()
        headers = {"Content-Type": CONTENT_TYPE, "X-Amz-Date": moment.strftime(sigv4.STAMP_FORMAT)}
        headers["Authorization"] = sigv4.authorization("POST", self.url, headers, hashlib.sha256(body).hexdigest(),
                                                       self.key, "ec2", moment.strftime(sigv4.TIME_FORMAT))
        request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                status, text = response.status, response.read()
        except urllib.error.HTTPError as error:
            try:
                status, text = error.code, error.read()
            except (OSError, http.client.HTTPException):
                raise _NoAnswer() from None
        except (OSError, http.client.HTTPException):
            raise _NoAnswer() from None
        text = text.decode("utf-8", "replace")
        try:
            root = ElementTree.fromstring(text)
        except ElementTree.ParseError:
            root = None
        code = root.find(".//{*}Code") if root is not None else None
        if code is None and status != 200:
            raise _NoAnswer()
        self._record(action, urllib.parse.urlsplit(self.url).path or "/", params, status, text)
        if code is not None:
            raise _Code((code.text or "").strip())
        if root is None:
            raise Refused(NOT_XML)
        return root

    def _before_launch(self, action, fields, tries=1):
        """``_request`` before an instance id is known, sent up to ``tries`` times while no answer comes: a
        lost answer or a code of ``UNAVAILABLE`` is ``node.Unavailable``, any other code ``Refused``."""
        for count in range(tries):
            try:
                return self._request(action, fields)
            except _NoAnswer:
                if count + 1 == tries:
                    _unavailable("ec2 " + NO_ANSWER)
            except _Code as error:
                if str(error) in UNAVAILABLE:
                    _unavailable(f"ec2 {error}")
                raise Refused(str(error)) from None

    def _poll(self, seen, failure):
        """Poll the instance until ``seen(item)`` is not None, at most ``POLLS`` times; that value and the time
        its answer was parsed. ``InvalidInstanceID.NotFound`` and a lost answer count as a poll that saw no
        change."""
        for count in range(POLLS):
            if count:
                self.sleep(POLL_SECONDS)
            try:
                root = self._request("DescribeInstances", {"InstanceId.1": self.instance_id})
            except _NoAnswer:
                continue
            except _Code as error:
                if str(error) == NOT_FOUND:
                    continue
                raise Refused(str(error)) from None
            parsed = self.now()
            item = root.find(".//{*}instancesSet/{*}item")
            found = None if item is None else seen(item)
            if found is not None:
                return found, parsed
        raise Refused(failure)

    def start(self):
        tag_key, tag_value, self.rate = self._settings()
        self.key = assets.read_key(self.key_file)
        user_data = self.user_data()
        self.url = self.config["settings"].get("RENDER_API_URL") or f"https://ec2.{self.key[2]}.amazonaws.com/"
        image = self._setting("RENDER_MACHINE_IMAGE")
        found = self._before_launch("DescribeImages", {"ImageId.1": image}).find(".//{*}rootDeviceName")
        if found is None or not (found.text or "").strip():
            raise Refused(f"image {image} has no root device")
        fields = {"ImageId": image, "InstanceType": self._setting("RENDER_INSTANCE_TYPE"),
                  "MinCount": "1", "MaxCount": "1", "InstanceInitiatedShutdownBehavior": "terminate",
                  "ClientToken": secrets.token_hex(16),
                  "UserData": base64.b64encode(user_data.encode("utf-8")).decode("ascii"),
                  "MetadataOptions.HttpTokens": "required", "MetadataOptions.HttpPutResponseHopLimit": "1",
                  "BlockDeviceMapping.1.DeviceName": found.text.strip(),
                  "BlockDeviceMapping.1.Ebs.DeleteOnTermination": "true"}
        for n, resource in ((1, "instance"), (2, "volume")):
            fields[f"TagSpecification.{n}.ResourceType"] = resource
            fields[f"TagSpecification.{n}.Tag.1.Key"] = tag_key
            fields[f"TagSpecification.{n}.Tag.1.Value"] = tag_value
        found = self._before_launch("RunInstances", fields, tries=2).find(".//{*}instancesSet/{*}item/{*}instanceId")
        if found is None or not (found.text or "").strip():
            raise ValueError("RunInstances answer has no instance id")
        self.instance_id = found.text.strip()
        state, _ = self._poll(self._launched, f"instance {self.instance_id} not running after {POLLS} polls")
        if state != "running":
            self.instance_id = None
            _unavailable("ec2 " + state)

    @staticmethod
    def _launched(item):
        """``running``, the state reason's code of an instance the cloud ended for want of capacity, or None."""
        state = item.findtext("{*}instanceState/{*}name")
        reason = (item.findtext("{*}stateReason/{*}code") or "").strip()
        if state in ENDED and reason.startswith(NO_CAPACITY):
            return reason
        return state if state == "running" else None

    @staticmethod
    def _ended(item):
        return item if item.findtext("{*}instanceState/{*}name") in ENDED else None

    def stop(self):
        if self.billed is not None:
            return dict(self.billed)
        if self.instance_id is None:
            return {"usd": 0, "compute_min": 0}
        for count in range(2):
            try:
                self._request("TerminateInstances", {"InstanceId.1": self.instance_id})
                break
            except _NoAnswer:
                if count:
                    raise Refused(NO_ANSWER) from None
            except _Code as error:
                raise Refused(str(error)) from None
        item, parsed = self._poll(self._ended,
                                  f"instance {self.instance_id} still running after {POLLS} polls")
        launched = _LAUNCH_TIME.fullmatch((item.findtext("{*}launchTime") or "").strip())
        if launched is None:
            raise ValueError("the instance has no launch time")
        start = datetime.strptime(launched.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        fraction = Decimal("0." + launched.group(2)) if launched.group(2) else Decimal(0)
        reason = _USER_INITIATED.search(item.findtext("{*}reason") or "")
        if reason:
            end = datetime.strptime(reason.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        else:
            end = parsed
        delta = end - start
        seconds = Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / 1000000 - fraction
        compute_min = max(Decimal(1), _ceil4(seconds / 60))
        usd = _ceil4(compute_min / 60 * self.rate)
        self.billed = {"usd": float(usd), "compute_min": float(compute_min)}
        return dict(self.billed)
