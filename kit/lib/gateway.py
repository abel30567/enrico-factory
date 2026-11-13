"""The knowledge gateway: one small local MCP server per run (docs/harness.md, section 6).

It holds the Fermi login outside the sandbox, serves a role only the tools of its capabilities, tags
every write with the production, role and ticket, and keeps from every read only what is tagged with
this production or with the shared ``studio`` tag. Fermi stores no tag, so the tag lives in a field it
has: a memory's first line ``[factory:<tag>]``, a skill's keyword ``factory:<tag>``.

The server is a thread of the supervisor: Streamable HTTP on ``127.0.0.1``, a port the system picks,
one bearer token per gateway. It answers JSON only, with no session and no event stream. Fermi down is
not a blocker: every failure to reach it is one fixed text, and the run goes on.

Nothing here prints, logs or raises the key, the run token, the value of ``FERMI_URL``, a query or a
body. A ``GatewayError`` holds fixed words and setting names only.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import http.server
import json
import os
import re
import secrets
import threading
import urllib.parse
import urllib.request

from kit.lib import resources
from kit.lib.frontmatter import as_list

RESOURCE = "knowledge.fermi"
SHARED = "studio"
FERMI_TIMEOUT = 20
MAX_BODY = 1024 * 1024
FERMI_LIMIT = 50
FERMI_VERSION = "2025-06-18"
VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
UNREACHABLE = ("knowledge: the knowledge server is unreachable; work from the repository alone and say so "
               "in your result")
_REPO = re.compile(r"[a-z0-9._-]+/[a-z0-9._-]+")
_SLUG = r"^[a-z0-9]+(-[a-z0-9]+)*$"
_LOCAL_HOSTS = ("127.0.0.1", "localhost")


class GatewayError(Exception):
    """A gateway that cannot start. The message holds fixed words and setting names only."""


class _Down(Exception):
    """Fermi did not answer as expected."""


class _Refused(Exception):
    """A call the gateway answers with this fixed text as an error."""


def _text(minimum=1):
    return {"type": "string", "minLength": minimum}


def _limit(default):
    return {"type": "integer", "minimum": 1, "maximum": FERMI_LIMIT, "default": default}


# name: (capability, description, properties, required)
TOOLS = {
    "skill_search": ("skills", "Search the studio's skills, procedures for craft tasks, by words in their "
                     "name, description or keywords. Answers the matching skills with slug, name, "
                     "description and keywords.",
                     {"query": _text(), "limit": _limit(5)}, ("query",)),
    "skill_load": ("skills", "Load one skill by its slug: its body and its resources.",
                   {"slug": _text()}, ("slug",)),
    "memory_recall": ("memory-read", "Recall memories of earlier tickets whose body contains the query. "
                      "A memory advises; only files may be cited.",
                      {"query": _text(), "limit": _limit(10),
                       "sort_by": {"type": "string", "enum": ["relevance", "created_at"],
                                   "default": "relevance"}}, ("query",)),
    "memory_write": ("memory-write", "Write a memory for later tickets: a fact, a preference or an event. "
                     "It is tagged with this production, role and ticket.",
                     {"kind": {"type": "string", "enum": ["fact", "preference", "event"]}, "body": _text()},
                     ("kind", "body")),
    "search": ("search", "Search the studio's skills and, for a role that reads memories, the memories of "
               "earlier tickets, by words. Answers the matches of this production and of the studio. A "
               "memory advises; only files may be cited.",
               {"query": _text(), "limit": _limit(10)}, ("query",)),
    "skill_set": ("skills-write", "Write or replace one skill of this production, a procedure for a craft "
                  "task. The slug gets this production's prefix. A skill outside this production is never "
                  "replaced.",
                  {"slug": {"type": "string", "pattern": _SLUG, "maxLength": 64}, "body": _text(),
                   "name": _text(), "description": _text(), "keywords": {"type": "array", "items": _text()}},
                  ("slug", "body")),
}


def _capabilities(value):
    return as_list(value) if value is None or isinstance(value, str) else list(value)


def tools(capabilities) -> list[str]:
    """The sorted tool names the capabilities (a role's ``knowledge`` line or a list) give."""
    found = set(_capabilities(capabilities))
    return sorted(name for name, spec in TOOLS.items() if spec[0] in found)


def wanted(settings, capabilities) -> bool:
    """Whether a run with these settings and capabilities gets a gateway."""
    return settings.get("GATEWAY", "") == "on" and bool(tools(capabilities))


def _tag(settings):
    """``(production tag or None, reasons)``."""
    repo = settings.get("REPO", "")
    if not repo:
        return None, ["setting REPO is empty"]
    tag = repo.lower()
    if not _REPO.fullmatch(tag):
        return None, ["setting REPO is not owner/name"]
    return tag, []


def _url_ok(value):
    try:
        url = urllib.parse.urlsplit(value)
        host = url.hostname
        url.port  # a port that does not parse raises ValueError
    except ValueError:
        return False
    if not host or url.username is not None or url.password is not None:
        return False
    return url.scheme == "https" or url.scheme == "http" and host in _LOCAL_HOSTS


def _reasons(settings):
    register, _ = resources.load(resources.REGISTER)
    resource = next(r for r in register["resources"] if r["id"] == RESOURCE)
    _, reasons = resources.check(resource, settings, register)
    reasons += _tag(settings)[1]
    value = settings.get("FERMI_URL", "")
    if value and not _url_ok(value):
        reasons.append("setting FERMI_URL is not an https URL")
    return reasons, register


def check(settings, capabilities) -> list[str]:
    """Why a run with these settings and capabilities cannot have its gateway; empty when it can or
    when it gets none."""
    if not wanted(settings, capabilities):
        return []
    return _reasons(settings)[0]


# -- Fermi

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _event(response, wanted_id):
    """The first event of an event stream whose JSON is a message with ``wanted_id``."""
    data = []

    def message():
        try:
            found = json.loads("\n".join(data))
        except ValueError:
            return None
        return found if isinstance(found, dict) and found.get("id") == wanted_id else None

    for raw in response:
        line = raw.decode("utf-8").rstrip("\r\n")
        if line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
        elif not line and data:
            found, data = message(), []
            if found is not None:
                return found
    found = message() if data else None
    if found is None:
        raise _Down
    return found


class _Fermi:
    """A client of Fermi's MCP endpoint. One request at a time; any failure forgets the session."""

    def __init__(self, url, key):
        self.url, self.key = url, key
        self.session, self.ready, self.count = None, False, 0
        self.lock = threading.Lock()

    def _post(self, method, params=None, notify=False):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if not notify:
            self.count += 1
            message["id"] = self.count
        headers = {"Authorization": "Bearer " + self.key, "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        request = urllib.request.Request(self.url, data=json.dumps(message).encode("utf-8"),
                                         headers=headers, method="POST")
        with _OPENER.open(request, timeout=FERMI_TIMEOUT) as response:
            if response.status not in (200, 202):
                raise _Down
            self.session = response.headers.get("Mcp-Session-Id") or self.session
            if notify:
                return None
            if response.status != 200:
                raise _Down
            kind = response.headers.get_content_type()
            if kind == "text/event-stream":
                answer = _event(response, message["id"])
            elif kind == "application/json":
                answer = json.loads(response.read())
            else:
                raise _Down
        if not isinstance(answer, dict) or answer.get("id") != message["id"] or "result" not in answer:
            raise _Down
        return answer["result"]

    def call(self, name, arguments):
        """Fermi's answer to one tool call: the JSON object in its text; ``_Down`` for anything else."""
        with self.lock:
            try:
                if not self.ready:
                    self.session = None
                    self._post("initialize", {"protocolVersion": FERMI_VERSION, "capabilities": {},
                                              "clientInfo": {"name": "knowledge-gateway", "version": "1"}})
                    self._post("notifications/initialized", notify=True)
                    self.ready = True
                result = self._post("tools/call", {"name": name, "arguments": arguments})
                content = result.get("content") if isinstance(result, dict) else None
                if not content or not isinstance(content, list) or result.get("isError"):
                    raise _Down
                item = content[0]
                if not isinstance(item, dict) or item.get("type") != "text" or not isinstance(item.get("text"), str):
                    raise _Down
                answer = json.loads(item["text"])
                if not isinstance(answer, dict):
                    raise _Down
                return answer
            except (_Down, OSError, ValueError, http.client.HTTPException):
                self.ready, self.session = False, None
                raise _Down from None


# -- the tools

def _problem(name, arguments):
    """The first problem of ``arguments`` for tool ``name``, naming an argument and never a value."""
    if not isinstance(arguments, dict):
        return "arguments must be an object"
    _, _, properties, required = TOOLS[name]
    for key in sorted(arguments):
        if key not in properties:
            return f"argument {key} is not taken"
    for key in required:
        if key not in arguments:
            return f"argument {key} is required"
    for key, schema in properties.items():
        if key not in arguments:
            continue
        value = arguments[key]
        if schema["type"] == "integer":
            if type(value) is not int or not schema["minimum"] <= value <= schema["maximum"]:
                return f"argument {key} must be an integer from {schema['minimum']} to {schema['maximum']}"
        elif "enum" in schema:
            if value not in schema["enum"]:
                return f"argument {key} must be one of {', '.join(schema['enum'])}"
        elif "pattern" in schema:
            if not isinstance(value, str) or len(value) > schema["maxLength"] or \
                    not re.fullmatch(schema["pattern"], value):
                return f"argument {key} must be lowercase words joined by single dashes, at most 64 characters"
        elif schema["type"] == "array":
            if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
                return f"argument {key} must be a list of non-empty strings"
            if any(v.strip().lower().startswith("factory:") for v in value):
                return f"argument {key} must not hold a factory: keyword"
        elif not isinstance(value, str) or len(value) < schema["minLength"]:
            return f"argument {key} must be a non-empty string"
    return None


def _answer(value, error=False):
    text = value if isinstance(value, str) else json.dumps(value)
    return {"content": [{"type": "text", "text": text}], "isError": error}


class Gateway:
    """One running gateway: ``port``, ``servers`` (for ``mcp.json``) and ``stop()``."""

    def __init__(self, fermi, allowed, tag, header):
        self.fermi, self.allowed, self.header = fermi, allowed, header
        self.tags = (tag, SHARED)
        self.prefix = "factory-" + hashlib.sha256(tag.encode("utf-8")).hexdigest()[:8] + "-"
        self.token = secrets.token_urlsafe(32)
        self.server = _Server(("127.0.0.1", 0), _Handler)
        self.server.gateway = self
        self.port = self.server.server_address[1]
        self.servers = {"knowledge": {"headers": {"Authorization": "Bearer " + self.token}, "type": "http",
                                      "url": f"http://127.0.0.1:{self.port}/mcp"}}
        self.stopped = False
        self.thread = threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True)
        self.thread.start()

    def stop(self):
        if self.stopped:
            return
        self.stopped = True
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def authorized(self, value):
        expected = ("Bearer " + self.token).encode("ascii")
        return hmac.compare_digest(value.encode("latin-1", "replace"), expected)

    def _memory_tagged(self, row):
        body = row.get("body") if isinstance(row, dict) else None
        if not isinstance(body, str):
            return False
        first = body.split("\n", 1)[0]
        return any(first == f"[factory:{t}]" or first.startswith(f"[factory:{t}] ") for t in self.tags)

    def _skill_tagged(self, skill):
        keywords = skill.get("keywords") if isinstance(skill, dict) else None
        return isinstance(keywords, list) and any(f"factory:{t}" in keywords for t in self.tags)

    def _match_tagged(self, match):
        kind = match.get("type") if isinstance(match, dict) else None
        if kind == "memory":
            return "memory_recall" in self.allowed and self._memory_tagged({"body": match.get("snippet")})
        return kind == "skill" and self._skill_tagged(match.get("metadata"))

    def _kept(self, answer, name, keys, tagged, limit):
        rows = answer.get(name)
        if not isinstance(rows, list):
            raise _Down
        kept = [row for row in rows if tagged(row)][:limit]
        shaped = {key: answer[key] for key in keys if key in answer}
        return {**shaped, name: kept, "total": len(kept)}

    def _skill_set(self, args):
        slug = args["slug"] if args["slug"].startswith(self.prefix) else self.prefix + args["slug"]
        found = self.fermi.call("skill_load", {"slug": slug})
        if found.get("error") != "skill_not_found":
            skill = found.get("skill")
            if not isinstance(skill, dict):
                raise _Down
            keywords = skill.get("keywords")
            if not isinstance(keywords, list) or f"factory:{self.tags[0]}" not in keywords:
                raise _Refused(f"knowledge: skill {slug} is outside this production; choose another slug")
        metadata = {key: args[key] for key in ("name", "description") if key in args}
        metadata.update(keywords=[*args.get("keywords", []), f"factory:{self.tags[0]}"], allowed_tools=[])
        answer = self.fermi.call("skill_set", {"slug": slug, "body": args["body"], "metadata": metadata})
        if answer.get("slug") != slug or not {"version", "created"} <= set(answer):
            raise _Down
        return {key: answer[key] for key in ("slug", "version", "created")}

    def _run(self, name, args):
        if name == "skill_search":
            answer = self.fermi.call(name, {"query": args["query"], "limit": FERMI_LIMIT})
            return self._kept(answer, "results", ("query",), self._skill_tagged, args["limit"])
        if name == "memory_recall":
            answer = self.fermi.call(name, {"query": args["query"], "limit": FERMI_LIMIT,
                                            "sort_by": args["sort_by"]})
            return self._kept(answer, "results", ("query", "sort_by"), self._memory_tagged, args["limit"])
        if name == "skill_load":
            answer = self.fermi.call(name, {"slug": args["slug"]})
            if "error" not in answer and self._skill_tagged(answer.get("skill")):
                return answer
            return {"error": "skill_not_found", "slug": args["slug"]}
        if name == "search":
            types = ["memory", "skill"] if "memory_recall" in self.allowed else ["skill"]
            answer = self.fermi.call(name, {"query": args["query"], "types": types, "limit": FERMI_LIMIT})
            return self._kept(answer, "matches", (), self._match_tagged, args["limit"])
        if name == "skill_set":
            return self._skill_set(args)
        return self.fermi.call(name, {"kind": args["kind"], "body": self.header + "\n" + args["body"]})

    def call(self, name, arguments):
        if name not in self.allowed:
            return _answer(f"knowledge: no tool {name}", True)
        problem = _problem(name, arguments)
        if problem:
            return _answer(f"knowledge: invalid arguments for {name}: {problem}", True)
        properties = TOOLS[name][2]
        args = {key: s["default"] for key, s in properties.items() if "default" in s} | arguments
        try:
            return _answer(self._run(name, args))
        except _Down:
            return _answer(UNREACHABLE, True)
        except _Refused as refused:
            return _answer(str(refused), True)

    def listing(self):
        return [{"name": name, "description": TOOLS[name][1],
                 "inputSchema": {"type": "object", "properties": TOOLS[name][2],
                                 "required": list(TOOLS[name][3]), "additionalProperties": False}}
                for name in self.allowed]

    def handle(self, message):
        """The JSON-RPC answer to one message with an id."""
        method, ident = message.get("method"), message["id"]
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if method == "initialize":
            asked = params.get("protocolVersion")
            result = {"protocolVersion": asked if asked in VERSIONS else FERMI_VERSION,
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "knowledge", "version": "1"}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": self.listing()}
        elif method == "tools/call":
            arguments = params.get("arguments", {})
            result = self.call(params.get("name"), {} if arguments is None else arguments)
        else:
            return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": "Method not found"}}
        return {"jsonrpc": "2.0", "id": ident, "result": result}


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        """A request that broke is dropped without a word."""


class _Handler(http.server.BaseHTTPRequestHandler):
    timeout = 60

    def log_message(self, *args):
        """No request is logged."""

    def __getattr__(self, name):
        if name.startswith("do_"):
            return self._not_allowed
        raise AttributeError(name)

    def _not_allowed(self):
        self._send(405, extra={"Allow": "POST"})

    def _send(self, status, payload=None, extra=None):
        data = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if payload is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if data and self.command != "HEAD":
            self.wfile.write(data)

    def do_POST(self):
        gateway = self.server.gateway
        if self.path != "/mcp":
            return self._send(404)
        if not gateway.authorized(self.headers.get("Authorization", "")):
            return self._send(401)
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            length = 0
        if length > MAX_BODY:
            return self._send(413)
        body = self.rfile.read(length) if length > 0 else b""
        try:
            message = json.loads(body)
        except ValueError:
            message = None
        if not isinstance(message, dict):
            return self._send(400, {"jsonrpc": "2.0", "id": None,
                                    "error": {"code": -32700, "message": "Parse error"}})
        if "id" not in message:
            return self._send(202)
        return self._send(200, gateway.handle(message))


def start(settings, capabilities, role, ticket) -> Gateway:
    """Read the key once and start the gateway of one run; ``GatewayError`` when it cannot be used."""
    reasons, register = _reasons(settings)
    if reasons:
        raise GatewayError("the knowledge gateway is not usable: " + "; ".join(reasons))
    tag = _tag(settings)[0]
    path = os.path.join(resources.expand(settings[register["secrets_dir_setting"]]), RESOURCE)
    try:
        with open(path, encoding="utf-8") as handle:
            key = handle.read().strip()
    except (OSError, UnicodeDecodeError):
        raise GatewayError(f"secret {RESOURCE}: cannot be read") from None
    if not key:
        raise GatewayError(f"secret {RESOURCE}: empty")
    fermi = _Fermi(settings["FERMI_URL"], key)
    return Gateway(fermi, tools(capabilities), tag, f"[factory:{tag}] {role} #{ticket}")
