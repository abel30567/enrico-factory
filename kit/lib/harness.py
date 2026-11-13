"""The one adapter between the factory and the agent harness (docs/harness.md, sections 1 to 5).

It turns a role file and settings into a command line and an environment, starts the process, and
reads the result object back. No other file in the kit knows which harness runs or names its flags.

Settings are a plain ``dict[str, str]`` (``resources.settings``). A ``HarnessError`` names setting
keys and slots, never values, token paths or account names.
"""

import json
import os
import re
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, Optional

from kit.lib import markers, roles
from kit.lib.frontmatter import as_list


class HarnessError(Exception):
    pass


MARKERS = ("READY", "ADDRESSED", "BLOCKED", "APPROVED", "NOTES", "PREFERS A", "PREFERS B",
           "NO PREFERENCE", "SYMPTOMS", "RULING", "SELECTED", "VOTE")
PREFERS = {"PREFERS A": "A", "PREFERS B": "B", "NO PREFERENCE": "none"}
SLOTS = ("MAKER", "CHECKER", "OTHER")
ENV_COPIED = ("PATH", "LANG", "LC_ALL", "TMPDIR", "TERM", "HOME")
# Never move a shell command to the background: a later turn would replace the result envelope.
NO_BACKGROUND_TASKS = "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"

_STRING = {"type": "string"}
_STRINGS = {"type": "array", "items": _STRING}
_INTEGER = {"type": "integer"}


def _object(properties):
    return {"type": "object", "properties": properties, "required": sorted(properties),
            "additionalProperties": False}


def _enum(*values):
    return {"type": "string", "enum": list(values)}


RESULT_SCHEMA = {
    "type": "object",
    "required": ["marker", "summary"],
    "additionalProperties": False,
    "properties": {
        "marker": _enum(*MARKERS),
        "summary": _STRING,
        "evidence": {"type": "array", "items": _object(
            {"criterion": _STRING, "result": _STRING, "command": _STRING})},
        "notes": {"type": "array", "items": _object(
            {"id": _STRING, "severity": _enum("Blocker", "Issue"), "text": _STRING,
             "evidence": _STRING})},
        "addressed": {"type": "array", "items": _object(
            {"note": _STRING, "by": _STRING, "action": _enum("changed", "rebutted"),
             "text": _STRING})},
        "prefers": _object({"choice": _enum("A", "B", "none"), "cited": _STRINGS}),
        "selected": _INTEGER,
        "vote": _object({"for": _INTEGER, "against": _INTEGER, "reasons": _STRINGS}),
        "blocked": _object({"missing": _STRING, "tried": _STRING, "resume_from": _STRING}),
    },
}

_TYPES = {"object": (dict, "an object"), "array": (list, "a list"), "string": (str, "a string"),
          "integer": (int, "an integer")}
_NOTE_ID = re.compile(r"([BI])\d+")


def _check(schema, value, where, problems):
    """Append to ``problems`` every way ``value`` departs from the subset of JSON Schema used here."""
    kind = schema["type"]
    python_type, name = _TYPES[kind]
    if not isinstance(value, python_type) or isinstance(value, bool):
        problems.append(f"{where}: is not {name}")
        return
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{where}: {value!r} is not one of {', '.join(schema['enum'])}")
    if kind == "array":
        for index, item in enumerate(value):
            _check(schema["items"], item, f"{where}[{index}]", problems)
    elif kind == "object":
        for key in sorted(set(value) - set(schema["properties"])):
            problems.append(f"{where}: unknown key {key!r}")
        for key in schema["required"]:
            if key not in value:
                problems.append(f"{where}: missing key {key!r}")
        for key, sub in schema["properties"].items():
            if key in value:
                _check(sub, value[key], f"{where}.{key}", problems)


def _marker_rules(marker, result, problems):
    """The per-marker rules, for a result that already matches RESULT_SCHEMA."""
    notes = result.get("notes", [])
    if marker in ("NOTES", "SYMPTOMS"):
        if not notes:
            problems.append(f"result.notes: {marker} needs at least one note")
        seen = set()
        for index, note in enumerate(notes):
            m = _NOTE_ID.fullmatch(note["id"])
            if not m or (m.group(1) == "B") != (note["severity"] == "Blocker"):
                problems.append(f"result.notes[{index}].id: {note['id']!r} does not fit severity "
                                f"{note['severity']} (B<n> is a Blocker, I<n> an Issue)")
            if note["id"] in seen:
                problems.append(f"result.notes[{index}].id: duplicate id {note['id']!r}")
            seen.add(note["id"])
    if marker == "APPROVED":
        for index, note in enumerate(notes):
            if note["severity"] == "Blocker":
                problems.append(f"result.notes[{index}].severity: APPROVED carries a Blocker")
    if marker in PREFERS and result.get("prefers", {}).get("choice") != PREFERS[marker]:
        problems.append(f"result.prefers.choice: {marker} needs choice {PREFERS[marker]!r}")
    if marker == "BLOCKED":
        if "blocked" not in result:
            problems.append("result.blocked: BLOCKED needs missing, tried and resume_from")
        else:
            for key in ("missing", "tried", "resume_from"):
                if not result["blocked"][key].strip():
                    problems.append(f"result.blocked.{key}: is empty")
    if marker in ("READY", "ADDRESSED") and not result.get("evidence"):
        problems.append(f"result.evidence: {marker} needs at least one evidence entry")
    if marker == "ADDRESSED" and not result.get("addressed"):
        problems.append("result.addressed: ADDRESSED needs at least one addressed entry")
    if marker == "SELECTED" and result.get("selected", 0) < 1:
        problems.append("result.selected: SELECTED needs a pull request number >= 1")
    if marker == "VOTE":
        vote = result.get("vote")
        if vote is None or vote["for"] < 0 or vote["against"] < 0:
            problems.append("result.vote: VOTE needs a tally with both counts >= 0")


def validate_result(kind, actor, result):
    """Sorted problems with a worker's result object; empty when the launcher may post it.

    The per-marker rules are checked only on a result that matches RESULT_SCHEMA, so a type error
    is reported once and not again as a missing field.
    """
    problems = []
    _check(RESULT_SCHEMA, result, "result", problems)
    well_formed = not problems
    marker = result.get("marker") if isinstance(result, dict) else None
    if isinstance(marker, str):
        if marker == "LAUNCH":
            problems.append("result.marker: LAUNCH is posted by the launcher, never by a role")
        elif not markers.may_post(kind, marker, actor):
            problems.append(f"result.marker: a {kind} posting as {actor} may not post {marker!r}")
    if well_formed:
        _marker_rules(marker, result, problems)
    return sorted(problems)


def _setting(settings, key):
    value = settings.get(key, "").strip()
    if not value:
        raise HarnessError(f"setting {key} is empty")
    return value


def version(settings, run=subprocess.run):
    """The first line the harness prints for ``--version``, trimmed."""
    try:
        done = run([settings.get("HARNESS", ""), "--version"], capture_output=True, text=True,
                   timeout=20)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise HarnessError("harness version: could not run") from None
    if done.returncode != 0:
        raise HarnessError("harness version: could not run")
    lines = done.stdout.splitlines()
    return lines[0].strip() if lines else ""


def model(settings, meta):
    """``(model id, family)`` for a role: family ``other``, ``same`` or ``any``."""
    slot = _setting(settings, "MODEL_" + meta["model"].upper())
    if meta["family"] == "other":
        if settings.get("PROXY_URL", "").strip() and settings.get("MODEL_OTHER", "").strip():
            return settings["MODEL_OTHER"].strip(), "other"
        return slot, "same"
    return slot, "any"


def schema_json():
    """RESULT_SCHEMA as compact JSON with sorted keys: the form passed on the command line."""
    return json.dumps(RESULT_SCHEMA, separators=(",", ":"), sort_keys=True)


def command(settings, meta, mcp_config_path):
    """The harness command line for one headless run of the role ``meta``."""
    tools = as_list(meta.get("tools"))
    for tool in tools:
        if tool not in roles.TOOLS:
            raise HarnessError(f"tool {tool!r} is not one of {', '.join(roles.TOOLS)}")
    return [_setting(settings, "HARNESS"), "-p",
            "--model", model(settings, meta)[0],
            "--tools", ",".join(tools),
            "--strict-mcp-config", "--mcp-config", str(mcp_config_path),
            "--setting-sources", "project",
            "--permission-mode", "bypassPermissions",
            "--output-format", "json",
            "--json-schema", schema_json()]


def write_mcp_config(path, servers=None):
    """Write the run's MCP config: exactly ``servers``, or none at all."""
    Path(path).write_text(json.dumps({"mcpServers": servers or {}}, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")


def environment(parent, settings, family, config_dir, token_fd, tmp_dir=None):
    """The worker's environment, built from an allowlist: nothing else of ``parent`` reaches it.

    With ``tmp_dir``, ``TMPDIR`` is that directory instead of the parent's.
    """
    env = {key: parent[key] for key in ENV_COPIED if key in parent}
    if tmp_dir is not None:
        env["TMPDIR"] = str(tmp_dir)
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    env["CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR"] = str(token_fd)
    env[NO_BACKGROUND_TASKS] = "1"
    if family == "other":
        env["ANTHROPIC_BASE_URL"] = _setting(settings, "PROXY_URL")
    return env


def _slot(slot):
    if slot not in SLOTS:
        raise HarnessError(f"slot {slot!r} is not one of {', '.join(SLOTS)}")
    return slot


def accounts(settings, slot):
    """The account names of a slot, in the order written."""
    key = "ACCOUNTS_" + _slot(slot)
    names = as_list(settings.get(key))
    if not names:
        raise HarnessError(f"setting {key} is empty")
    return names


def token_path(settings, slot, account):
    """The login token file of ``account`` in ``slot``; it must exist, be non-empty and private."""
    _slot(slot)
    base = _setting(settings, "SECRETS_DIR")
    base = Path(os.path.expanduser(base) if base.startswith("~/") else base)
    if slot == "OTHER":
        path = base / "proxy.client"
    elif not account or "/" in account or account in (".", ".."):
        raise HarnessError(f"token for slot {slot}: the account name is not a file name")
    else:
        path = base / "inference.primary" / account
    try:
        info = path.stat()
    except OSError:
        raise HarnessError(f"token for slot {slot}: the file is missing") from None
    if not stat.S_ISREG(info.st_mode):
        raise HarnessError(f"token for slot {slot}: not a regular file")
    if info.st_size == 0:
        raise HarnessError(f"token for slot {slot}: the file is empty")
    if info.st_mode & 0o077:
        raise HarnessError(f"token for slot {slot}: the file grants access to group or other")
    return path


def _when(value):
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def pick(names, exhausted, now):
    """The first account not out of usage at ``now`` (an ISO time or datetime), or None."""
    now = _when(now)
    for name in names:
        if name not in exhausted or _when(exhausted[name]) <= now:
            return name
    return None


def start(argv, env, cwd, prompt_path, token_path, stdout_path, stderr_path, token_fd=3):
    """Start the harness detached in its own session; the token is descriptor ``token_fd`` only."""
    token = os.open(token_path, os.O_RDONLY)
    try:
        with open(prompt_path, "rb") as prompt, open(stdout_path, "wb") as out, \
                open(stderr_path, "wb") as err:
            return subprocess.Popen(
                list(argv), env=dict(env), cwd=cwd, stdin=prompt, stdout=out, stderr=err,
                pass_fds=(token_fd,), start_new_session=True,
                # os.open returns the lowest free descriptor, so the token is usually token_fd
                # already; otherwise the child moves it there before the harness runs.
                preexec_fn=None if token == token_fd else (lambda: os.dup2(token, token_fd)))
    finally:
        os.close(token)


class Outcome(NamedTuple):
    status: str
    result: Optional[dict]
    reason: str


PHRASES = {
    "ok": "result read",
    "out_of_usage": "account out of usage",
    "login_ended": "login expired or revoked",
    "proxy_down": "model proxy unreachable",
    "failed": "run failed",
}
_USAGE = re.compile(r"(?i)usage limit|out of usage")
_LOGIN = re.compile(r"(?i)token (has )?(expired|been revoked)|not logged in")
_REFUSED = re.compile(r"(?i)ECONNREFUSED|connection ?refused")


def _envelope(text):
    """The last line of ``text`` that is a JSON object with ``"type": "result"``, or None."""
    for line in reversed(text.splitlines()):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("type") == "result":
            return value
    return None


def _result_object(envelope):
    if isinstance(envelope.get("structured_output"), dict):
        return envelope["structured_output"]
    if isinstance(envelope.get("result"), str):
        try:
            value = json.loads(envelope["result"])
        except ValueError:
            return None
        return value if isinstance(value, dict) else None
    return None


def _status(envelope, exit_code, stderr_text):
    if envelope is None:
        return "proxy_down" if _REFUSED.search(stderr_text or "") else "failed"
    text = envelope["result"] if isinstance(envelope.get("result"), str) else ""
    is_error = envelope.get("is_error") is True
    code = envelope.get("api_error_status")
    if code == 429 or (is_error and _USAGE.search(text)):
        return "out_of_usage"
    if code in (401, 403) or (is_error and _LOGIN.search(text)):
        return "login_ended"
    if is_error and code is None and _REFUSED.search(text):
        return "proxy_down"
    if is_error or envelope.get("subtype") != "success" or exit_code != 0:
        return "failed"
    return "ok"


def read(stdout_text, exit_code, stderr_text=""):
    """Classify a finished run. The reason never carries model output or stderr."""
    envelope = _envelope(stdout_text)
    status = _status(envelope, exit_code, stderr_text)
    if status != "ok":
        return Outcome(status, None, f"{PHRASES[status]} (exit {exit_code})")
    result = _result_object(envelope)
    if result is None:
        return Outcome("failed", None, f"no result object (exit {exit_code})")
    return Outcome("ok", result, f"{PHRASES['ok']} (exit {exit_code})")
