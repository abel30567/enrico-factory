"""Tests for kit/lib/gateway.py, the knowledge gateway, and its start and stop in kit/lib/finish.py.

Fermi is a fake on 127.0.0.1 (``FakeFermi``) with Fermi's tool shapes and rules. Launch tests use the world
of tests/test_finish.py with tests/fixtures/gateway/mcp-harness as the harness: it talks to the servers of
its ``mcp.json``, records the answers in the run directory, then runs the fake harness. Each class names
the behaviour of the ticket it covers; ``test_defect_*`` fails on the seeded defect, ``test_twin_*`` is
the clean twin.
"""

import http.client
import http.server
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from kit.lib import gateway, markers, sandbox  # noqa: E402
from test_finish import World, ready  # noqa: E402

MCP_HARNESS = ROOT / "tests" / "fixtures" / "gateway" / "mcp-harness"
KEY = "fermi-key-pelican-7d2a-do-not-print"
TAG = "example/salt-marsh"
FERMI_ERROR = "fermi internal trace: heron-table locked"
FERMI_TOOLS = {"memory_recall": {"query", "limit", "sort_by"}, "memory_write": {"kind", "body", "pinned"},
               "skill_search": {"query", "limit"}, "skill_load": {"slug"},
               "search": {"query", "types", "limit", "entity", "memoryContext"},
               "skill_set": {"slug", "body", "metadata", "origin_memory_id"}}
PREFIX = "factory-a87ee4c9-"


def memory(n, body, created_at=None):
    return {"id": n, "kind": "fact", "body": body, "pinned": False,
            "created_at": created_at or f"2026-11-{n:02d}T10:00:00Z"}


def skill(slug, keywords, body="Step one: read the outline."):
    return {"slug": slug, "name": slug.replace("-", " "), "description": f"How to {slug}.",
            "keywords": keywords, "body": body}


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class FakeFermi:
    """Fermi's MCP endpoint on 127.0.0.1. ``mode`` is None, ``stream``, ``503``, ``notjson``, ``iserror``
    or ``slow``; ``fail_tool`` answers 503 and ``empty_tool`` answers ``{}`` to one named tool only.
    ``records`` holds, per request, the headers, method, tool, arguments and rows answered."""

    def __init__(self, test, memories=(), skills=()):
        self.memories = [dict(m) for m in memories]
        self.skills = [dict(s) for s in skills]
        self.records, self.mode, self.sessions = [], None, 0
        self.fail_tool = self.empty_tool = None
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                fake.handle(self)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.handle_error = lambda request, address: None  # a caller that gave up is not a failure
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/mcp"
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()
        test.addCleanup(self.server.server_close)
        test.addCleanup(self.server.shutdown)

    def methods(self):
        return [r["method"] for r in self.records]

    def calls(self):
        return [r for r in self.records if r["method"] == "tools/call"]

    @staticmethod
    def send(h, status, body, kind, headers=None):
        h.send_response(status)
        if kind:
            h.send_header("Content-Type", kind)
        for key, value in (headers or {}).items():
            h.send_header(key, value)
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)

    def handle(self, h):
        message = json.loads(h.rfile.read(int(h.headers.get("Content-Length") or 0)))
        params = message.get("params") or {}
        record = {"authorization": h.headers.get("Authorization"), "session": h.headers.get("Mcp-Session-Id"),
                  "method": message.get("method"), "tool": params.get("name"),
                  "arguments": params.get("arguments"), "rows": None}
        self.records.append(record)
        if self.mode == "slow":
            time.sleep(1)
        if self.mode == "503" or self.fail_tool and record["tool"] == self.fail_tool:
            return self.send(h, 503, b"busy", "text/plain")
        if "id" not in message:
            return self.send(h, 202, b"", None)
        if self.mode == "notjson":
            return self.send(h, 200, b"<html>busy</html>", "application/json")
        headers = {}
        if message["method"] == "initialize":
            self.sessions += 1
            headers["Mcp-Session-Id"] = f"session-{self.sessions}"
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "fermi", "version": "0"}}
        elif self.mode == "iserror":
            result = {"content": [{"type": "text", "text": FERMI_ERROR}], "isError": True}
        elif self.empty_tool and record["tool"] == self.empty_tool:
            result = {"content": [{"type": "text", "text": "{}"}], "isError": False}
        else:
            answer, record["rows"] = self.tool(params["name"], params.get("arguments") or {})
            result = {"content": [{"type": "text", "text": json.dumps(answer)}], "isError": False}
        answer = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        if self.mode == "stream":
            other = {"jsonrpc": "2.0", "method": "notifications/message", "params": {"data": "working"}}
            body = f"event: message\ndata: {json.dumps(other)}\n\nevent: message\ndata: {json.dumps(answer)}\n\n"
            return self.send(h, 200, body.encode(), "text/event-stream", headers)
        return self.send(h, 200, json.dumps(answer).encode(), "application/json", headers)

    def tool(self, name, args):
        """``(answer, rows)`` with Fermi's shapes: substring match, cut to ``limit``."""
        if set(args) - FERMI_TOOLS[name]:
            return {"error": "invalid_arguments"}, None
        if name == "memory_recall":
            rows = [m for m in self.memories if args["query"] in m["body"]]
            if args.get("sort_by") == "created_at":
                rows = sorted(rows, key=lambda m: m["created_at"], reverse=True)
            rows = rows[:args.get("limit", 10)]
            return {"query": args["query"], "sort_by": args.get("sort_by", "relevance"), "results": rows,
                    "total": len(rows)}, rows
        if name == "memory_write":
            row = memory(len(self.memories) + 1, args["body"])
            row.update(kind=args["kind"], pinned=bool(args.get("pinned")))
            self.memories.append(row)
            return {"id": row["id"], "kind": row["kind"], "pinned": row["pinned"]}, [row]
        if name == "skill_search":
            rows = [{k: s[k] for k in ("slug", "name", "description", "keywords")} for s in self.skills
                    if args["query"] in " ".join([s["slug"], s["name"], s["description"], *s["keywords"]])]
            rows = rows[:args.get("limit", 10)]
            return {"query": args["query"], "results": rows, "total": len(rows)}, rows
        if name == "search":
            return self.search(args)
        if name == "skill_set":
            metadata = args.get("metadata") or {}
            old = next((s for s in self.skills if s["slug"] == args["slug"]), None)
            row = skill(args["slug"], list(metadata.get("keywords", [])), body=args["body"])
            row.update({k: metadata[k] for k in ("name", "description") if k in metadata},
                       version=old.get("version", 1) + 1 if old else 1, metadata=metadata)
            self.skills = [s for s in self.skills if s is not old] + [row]
            return {"slug": row["slug"], "version": row["version"], "created": old is None,
                    "r2_key": f"skills/{row['slug']}/v{row['version']}.md"}, [row]
        found = [s for s in self.skills if s["slug"] == args["slug"]]
        if not found:
            return {"error": "skill_not_found", "slug": args["slug"]}, []
        s = found[0]
        return {"skill": {k: s[k] for k in ("slug", "name", "description", "keywords")}, "body": s["body"],
                "resources": []}, [s]

    def search(self, args):
        """Fermi's ``search``: the memory and skill matches of the asked ``types``, and for every query one
        message, one capability and one secret match (tagged, so that only their type drops them)."""
        types = args.get("types", ["capability", "memory", "secret", "skill"])
        query = args["query"]
        other = {"snippet": f"[factory:{TAG}]\n{query}", "score": 0.9,
                 "metadata": {"keywords": [f"factory:{TAG}", "factory:studio"]}}
        memories = [{"type": "memory", "id": str(m["id"]), "name": m["kind"], "snippet": m["body"], "score": 0.8,
                     "metadata": {"kind": m["kind"]}} for m in self.memories if query in m["body"]]
        skills = [{"type": "skill", "id": s["slug"], "name": s["slug"], "snippet": s["description"], "score": 0.7,
                   "metadata": {"keywords": s["keywords"]}} for s in self.skills
                  if query in " ".join([s["slug"], s["name"], s["description"], *s["keywords"]])]
        rows = [{"type": "message", "id": "m-1", "name": "chat", **other},
                *(memories if "memory" in types else []),
                {"type": "capability", "id": "c-1", "name": "browser", **other},
                *(skills if "skill" in types else []),
                {"type": "secret", "id": "s-1", "name": "heron-token", **other}][:args.get("limit", 10)]
        counts = {"message": 1, "capability": 1, "secret": 1, "memory": len(memories), "skill": len(skills)}
        return {"matches": rows, "offline": False, "warnings": [], "guidance": "Load a skill by its slug.",
                "telemetry": {"candidateCounts": counts}, "memories": []}, rows


class Base(unittest.TestCase):
    """A secrets directory with the key file and a fake Fermi."""

    memories = ()
    skills = ()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.secrets = self.tmp / "secrets"
        self.secrets.mkdir()
        self.key(KEY + "\n")
        self.fake = FakeFermi(self, self.memories, self.skills)

    def key(self, text):
        path = self.secrets / "knowledge.fermi"
        path.write_text(text)
        path.chmod(0o600)

    def settings(self, **extra):
        return {"GATEWAY": "on", "REPO": TAG, "FERMI_URL": self.fake.url, "SECRETS_DIR": str(self.secrets),
                **extra}

    def start(self, capabilities="skills", role="writer", ticket=3, **extra):
        gw = gateway.start(self.settings(**extra), capabilities, role, ticket)
        self.addCleanup(gw.stop)
        return gw


def auth(gw):
    return gw.servers["knowledge"]["headers"]["Authorization"]


def send(gw, payload=None, authorization="", method="POST", path="/mcp", raw=None, headers=None):
    """``(status, headers, body)`` of one request to the gateway; ``authorization`` None sends none."""
    conn = http.client.HTTPConnection("127.0.0.1", gw.port, timeout=10)
    body = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
    sent = {"Content-Type": "application/json", **(headers or {})}
    if authorization is not None:
        sent["Authorization"] = authorization or auth(gw)
    try:
        conn.request(method, path, body=body, headers=sent)
        response = conn.getresponse()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
    finally:
        conn.close()


def rpc(gw, method, params=None, ident=1):
    message = {"jsonrpc": "2.0", "id": ident, "method": method}
    if params is not None:
        message["params"] = params
    status, headers, body = send(gw, message)
    assert status == 200 and headers["content-type"] == "application/json", (status, headers)
    answer = json.loads(body)
    assert answer["id"] == ident, answer
    return answer


def call(gw, name, arguments=None):
    """``(text, isError)`` of one ``tools/call``."""
    params = {"name": name} if arguments is None else {"name": name, "arguments": arguments}
    result = rpc(gw, "tools/call", params)["result"]
    assert len(result["content"]) == 1 and result["content"][0]["type"] == "text", result
    return result["content"][0]["text"], result["isError"]


def tool_names(gw):
    return [t["name"] for t in rpc(gw, "tools/list")["result"]["tools"]]


# -- launches through `factory launch ... --wait`

def gateway_world(test, fake, calls=None, direct=None, harness_name="mcp-harness", **settings):
    w = World(test)
    shutil.copy2(MCP_HARNESS, w.harness / "mcp-harness")
    key = w.secrets / "knowledge.fermi"
    key.write_text(KEY + "\n")
    key.chmod(0o600)
    plan = {"calls": calls if calls is not None else [{"name": "skill_search", "arguments": {"query": "outline"}}]}
    if direct:
        plan["direct"] = direct
    (w.harness / "mcp-harness.json").write_text(json.dumps(plan))
    w.local(**{"GATEWAY": "on", "FERMI_URL": fake.url, "HARNESS": str(w.harness / harness_name), **settings})
    w.steps(ready())
    return w


def answers(w, name="writer-3-1"):
    return json.loads((w.run_dir(name) / "mcp-harness.answers.json").read_text())


def mcp_port(w, name="writer-3-1"):
    servers = json.loads((w.run_dir(name) / "mcp.json").read_text())["mcpServers"]
    return int(servers["knowledge"]["url"].rsplit(":", 1)[1].split("/")[0])


def refused(test, port):
    with test.assertRaises(ConnectionRefusedError):
        socket.create_connection(("127.0.0.1", port), timeout=5).close()


class Behaviour1LaunchServer(unittest.TestCase):

    def setUp(self):
        self.fake = FakeFermi(self, skills=[skill("outline-beats", ["factory:studio"])])

    def test_defect_exactly_one_knowledge_server(self):
        w = gateway_world(self, self.fake)
        code, out, err = w.launch("writer", "3")
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(markers.parse(w.posted("writer")[-1]["body"]).verb, "READY")
        servers = answers(w)["servers"]
        self.assertEqual(list(servers), ["knowledge"])
        self.assertEqual(servers["knowledge"], {"type": "http", "url": f"http://127.0.0.1:{mcp_port(w)}/mcp"})
        listed = answers(w)["answers"]["knowledge"][2]["body"]["result"]["tools"]
        self.assertEqual([t["name"] for t in listed], ["skill_load", "skill_search"])
        self.assertEqual(self.fake.methods(), ["initialize", "notifications/initialized", "tools/call"])

    def test_twin_gateway_off(self):
        w = gateway_world(self, self.fake, GATEWAY="off")
        with mock.patch.object(gateway, "start", wraps=gateway.start) as started, \
                mock.patch.object(gateway, "_Server", side_effect=AssertionError("a port was opened")):
            code, out, err = w.launch("writer", "3")
        self.assertEqual((code, err), (0, ""), out)
        started.assert_not_called()
        self.assertEqual(json.loads((w.run_dir("writer-3-1") / "mcp.json").read_text()), {"mcpServers": {}})
        self.assertEqual(answers(w)["servers"], {})
        self.assertEqual(self.fake.records, [])


class Behaviour2Tools(Base):

    def test_twin_tools_list_per_capability(self):
        self.assertEqual(tool_names(self.start("skills")), ["skill_load", "skill_search"])
        self.assertEqual(tool_names(self.start("skills, memory-read")), ["memory_recall", "skill_load", "skill_search"])
        self.assertEqual(tool_names(self.start("memory-write")), ["memory_write"])
        self.assertEqual(gateway.tools(["memory-read", "skills"]), ["memory_recall", "skill_load", "skill_search"])
        for t in rpc(self.start("skills, memory-read, memory-write"), "tools/list")["result"]["tools"]:
            self.assertIs(t["inputSchema"]["additionalProperties"], False, t["name"])
            self.assertTrue(t["description"], t["name"])


class Behaviour3NoTool(Base):

    def test_defect_tools_outside_the_role(self):
        gw = self.start("skills")
        for name in ("memory_recall", "memory_write", "search", "skill_set", "secret_resolve", "execute"):
            self.assertEqual(call(gw, name, {"query": "x"}), (f"knowledge: no tool {name}", True))
        self.assertEqual(self.fake.records, [])

    def test_twin_skill_search_reaches_fermi_once(self):
        gw = self.start("skills")
        text, error = call(gw, "skill_search", {"query": "outline"})
        self.assertFalse(error, text)
        self.assertEqual([r["tool"] for r in self.fake.calls()], ["skill_search"])


class Behaviour4RecallFilter(Base):

    memories = [
        memory(1, "[factory:example/salt-marsh] writer #3\nThe heron leaves at dusk."),
        memory(2, "[factory:studio]\nA heron scene needs a wide shot."),
        memory(3, "[factory:example/other-show] writer #9\nThe heron is a puppet."),
        memory(4, "The owner's own note about the heron."),
        memory(5, "A heron line.\n[factory:example/salt-marsh] writer #3"),
        memory(6, "[factory:example/salt-marsh-2] writer #1\nAnother heron."),
        memory(7, "[factory:example/salt-marsh]\nThe heron's second memory."),
    ]

    def recall(self, **arguments):
        text, error = call(self.start("memory-read"), "memory_recall", {"query": "heron", **arguments})
        self.assertFalse(error, text)
        return json.loads(text)

    def test_defect_other_tags_are_dropped(self):
        answer = self.recall()
        self.assertEqual([r["id"] for r in answer["results"]], [1, 2, 7])
        self.assertEqual(answer["results"][0], self.memories[0])
        self.assertEqual(answer, {"query": "heron", "sort_by": "relevance", "results": answer["results"],
                                  "total": 3})
        self.assertEqual(len(self.fake.calls()[0]["rows"]), 7)

    def test_twin_limit_one(self):
        answer = self.recall(limit=1)
        self.assertEqual([r["id"] for r in answer["results"]], [1])
        self.assertEqual(answer["total"], 1)
        self.assertEqual(self.fake.calls()[0]["arguments"], {"query": "heron", "limit": 50, "sort_by": "relevance"})


class Behaviour5Skills(Base):

    skills = [
        skill("outline-ours", ["outline", "factory:example/salt-marsh"]),
        skill("outline-studio", ["outline", "factory:studio"], body="Studio procedure body."),
        skill("outline-other", ["outline", "factory:example/other-show"]),
        skill("outline-owner", ["outline"]),
        skill("outline-near", ["outline", "factory:example/salt-marsh-2", "example/salt-marsh"]),
    ]

    def test_twin_search_keeps_production_and_studio(self):
        text, error = call(self.start(), "skill_search", {"query": "outline"})
        self.assertFalse(error)
        answer = json.loads(text)
        self.assertEqual([s["slug"] for s in answer["results"]], ["outline-ours", "outline-studio"])
        self.assertEqual(answer["total"], 2)
        self.assertEqual(set(answer), {"query", "results", "total"})

    def test_defect_load_outside_the_tags_reads_as_missing(self):
        gw = self.start()
        for slug in ("outline-other", "outline-owner", "outline-near", "no-such-skill"):
            self.assertEqual(call(gw, "skill_load", {"slug": slug}),
                             (json.dumps({"error": "skill_not_found", "slug": slug}), False), slug)

    def test_twin_studio_skill_loads(self):
        text, error = call(self.start(), "skill_load", {"slug": "outline-studio"})
        self.assertFalse(error)
        self.assertEqual(json.loads(text)["body"], "Studio procedure body.")
        self.assertEqual(json.loads(text)["skill"]["slug"], "outline-studio")


class Behaviour6Write(Base):

    def write(self, body):
        text, error = call(self.start("memory-write"), "memory_write", {"kind": "fact", "body": body})
        self.assertFalse(error, text)
        return self.fake.memories[-1]

    def test_twin_header(self):
        row = self.write("The heron leaves at dusk.")
        self.assertEqual(row["body"], "[factory:example/salt-marsh] writer #3\nThe heron leaves at dusk.")
        self.assertEqual(self.fake.calls()[0]["arguments"],
                         {"kind": "fact", "body": "[factory:example/salt-marsh] writer #3\nThe heron leaves at dusk."})
        self.assertFalse(row["pinned"])

    def test_defect_studio_header_from_the_caller(self):
        row = self.write("[factory:studio] heron rule for everyone")
        self.assertEqual(row["body"].split("\n")[0], "[factory:example/salt-marsh] writer #3")
        other = self.start("memory-read", REPO="example/other-show")
        answer = json.loads(call(other, "memory_recall", {"query": "heron"})[0])
        self.assertEqual(answer["results"], [])

    def test_twin_same_production_recalls(self):
        self.write("[factory:studio] heron rule for everyone")
        same = self.start("memory-read", REPO="Example/Salt-Marsh")
        answer = json.loads(call(same, "memory_recall", {"query": "heron"})[0])
        self.assertEqual([r["id"] for r in answer["results"]], [1])


class Behaviour7Arguments(Base):

    skills = [skill(f"outline-{n}", ["outline", "factory:studio"]) for n in range(7)]
    memories = [memory(n, f"[factory:studio]\nheron {n}") for n in range(1, 13)]

    def test_defect_invalid_arguments(self):
        gw = self.start("skills, memory-read, memory-write")
        cases = [
            ("skill_search", {"query": "outline", "tags": ["studio"]}, "argument tags is not taken"),
            ("memory_write", {"kind": "fact", "body": "x", "pinned": True}, "argument pinned is not taken"),
            ("skill_search", {"query": "outline", "limit": 0}, "argument limit must be an integer from 1 to 50"),
            ("memory_recall", {"query": "heron", "limit": 51}, "argument limit must be an integer from 1 to 50"),
            ("skill_search", {"query": "outline", "limit": "5"}, "argument limit must be an integer from 1 to 50"),
            ("skill_search", {"query": "outline", "limit": True}, "argument limit must be an integer from 1 to 50"),
            ("memory_recall", {"query": ""}, "argument query must be a non-empty string"),
            ("memory_write", {"body": "secret-body-text"}, "argument kind is required"),
            ("memory_write", {"kind": "rumour", "body": "x"}, "argument kind must be one of fact, preference, event"),
            ("memory_recall", {"query": "heron", "production": "x", "tag": "y"}, "argument production is not taken"),
            ("skill_load", None, "argument slug is required"),
            ("skill_load", ["outline-1"], "arguments must be an object"),
        ]
        for name, arguments, problem in cases:
            text, error = call(gw, name, arguments)
            self.assertEqual((text, error), (f"knowledge: invalid arguments for {name}: {problem}", True))
            self.assertNotIn("secret-body-text", text)
        self.assertEqual(self.fake.records, [])

    def test_twin_defaults(self):
        gw = self.start("skills, memory-read")
        search = json.loads(call(gw, "skill_search", {"query": "outline"})[0])
        recall = json.loads(call(gw, "memory_recall", {"query": "heron"})[0])
        self.assertEqual(len(search["results"]), 5)
        self.assertEqual(len(recall["results"]), 10)
        self.assertEqual([r["arguments"] for r in self.fake.calls()],
                         [{"query": "outline", "limit": 50}, {"query": "heron", "limit": 50, "sort_by": "relevance"}])
        recall = json.loads(call(gw, "memory_recall", {"query": "heron", "sort_by": "created_at", "limit": 2})[0])
        self.assertEqual([r["id"] for r in recall["results"]], [12, 11])


class Behaviour8Wire(Base):

    def test_defect_unauthorized_and_bad_requests(self):
        gw = self.start()
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "skill_search", "arguments": {"query": "outline"}}}
        for authorization in (None, "Bearer wrong-token", "Bearer " + KEY, auth(gw).lower(), auth(gw) + "x"):
            self.assertEqual(send(gw, message, authorization=authorization)[0], 401, authorization)
        self.assertEqual(send(gw, method="GET")[0], 405)
        self.assertEqual(send(gw, method="DELETE")[0], 405)
        self.assertEqual(send(gw, message, path="/other")[0], 404)
        self.assertEqual(send(gw, message, path="/mcp/")[0], 404)
        status, headers, body = send(gw, [])
        self.assertEqual((status, json.loads(body)["error"]["code"]), (400, -32700))
        self.assertEqual(send(gw, raw=b"{not json")[0], 400)
        status = send(gw, raw=b"{}", headers={"Content-Length": str(gateway.MAX_BODY + 1)})[0]
        self.assertEqual(status, 413)
        self.assertEqual(rpc(gw, "resources/list")["error"]["code"], -32601)
        self.assertEqual(self.fake.records, [])

    def test_twin_initialize_ping_notification(self):
        gw = self.start()
        for asked, given in (("2025-11-25", "2025-11-25"), ("2025-06-18", "2025-06-18"),
                             ("2025-03-26", "2025-03-26"), ("1999-01-01", "2025-06-18")):
            result = rpc(gw, "initialize", {"protocolVersion": asked, "capabilities": {},
                                            "clientInfo": {"name": "t", "version": "0"}})["result"]
            self.assertEqual(result, {"protocolVersion": given, "capabilities": {"tools": {}},
                                      "serverInfo": {"name": "knowledge", "version": "1"}})
        self.assertEqual(rpc(gw, "ping")["result"], {})
        status, headers, body = send(gw, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual((status, body), (202, b""))
        self.assertNotIn("mcp-session-id", headers)
        self.assertEqual(self.fake.records, [])


class Behaviour9FermiWire(Base):

    skills = [skill("outline-studio", ["outline", "factory:studio"])]

    def test_twin_handshake_key_and_session(self):
        gw = self.start()
        call(gw, "skill_search", {"query": "outline"})
        call(gw, "skill_load", {"slug": "outline-studio"})
        self.assertEqual(self.fake.methods(), ["initialize", "notifications/initialized", "tools/call", "tools/call"])
        self.assertEqual({r["authorization"] for r in self.fake.records}, {"Bearer " + KEY})
        self.assertEqual([r["session"] for r in self.fake.records], [None, "session-1", "session-1", "session-1"])
        self.assertEqual(self.fake.records[0]["arguments"], None)

    def test_twin_event_stream_reads_like_json(self):
        plain = call(self.start(), "skill_search", {"query": "outline"})
        self.fake.mode = "stream"
        self.assertEqual(call(self.start(), "skill_search", {"query": "outline"}), plain)
        self.assertFalse(plain[1])

    def test_defect_start_and_stop_send_nothing(self):
        gw = self.start("skills, memory-read, memory-write")
        gw.stop()
        gw.stop()
        self.assertEqual(self.fake.records, [])


class Behaviour10FermiDown(Base):

    skills = [skill("outline-studio", ["outline", "factory:studio"])]

    def unreachable(self, gw):
        text, error = call(gw, "skill_search", {"query": "outline"})
        self.assertEqual((text, error), (gateway.UNREACHABLE, True))
        self.assertNotIn(FERMI_ERROR, text)

    def test_defect_every_failure_is_one_text(self):
        for mode in ("503", "notjson", "iserror"):
            self.fake.mode = mode
            self.unreachable(self.start())
        self.fake.mode = None
        self.unreachable(self.start(FERMI_URL=f"http://127.0.0.1:{free_port()}/mcp"))

    def test_defect_timeout(self):
        self.fake.mode = "slow"
        with mock.patch.object(gateway, "FERMI_TIMEOUT", 0.2):
            self.unreachable(self.start())

    def test_twin_recovers_with_a_new_session(self):
        gw = self.start()
        self.assertFalse(call(gw, "skill_search", {"query": "outline"})[1])
        self.fake.mode = "503"
        self.unreachable(gw)
        self.fake.mode = None
        text, error = call(gw, "skill_search", {"query": "outline"})
        self.assertFalse(error, text)
        self.assertEqual(json.loads(text)["total"], 1)
        self.assertEqual(self.fake.methods()[-3:], ["initialize", "notifications/initialized", "tools/call"])
        self.assertEqual(self.fake.records[-1]["session"], "session-2")

    def test_defect_launch_goes_on(self):
        self.fake.mode = "503"
        w = gateway_world(self, self.fake)
        code, out, err = w.launch("writer", "3")
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(markers.parse(w.posted("writer")[-1]["body"]).verb, "READY")
        answer = answers(w)["answers"]["knowledge"][-1]["body"]["result"]
        self.assertEqual(answer, {"content": [{"type": "text", "text": gateway.UNREACHABLE}], "isError": True})


class Behaviour12Stop(unittest.TestCase):

    def setUp(self):
        self.fake = FakeFermi(self)

    def test_twin_stopped_after_supervise(self):
        w = gateway_world(self, self.fake)
        self.assertEqual(w.launch("writer", "3")[0], 0)
        refused(self, mcp_port(w))

    def test_defect_harness_exits_non_zero(self):
        w = gateway_world(self, self.fake)
        w.steps(ready(exit=1))
        self.assertEqual(w.launch("writer", "3")[0], 3)
        refused(self, mcp_port(w))

    def test_defect_timed_out(self):
        w = gateway_world(self, self.fake, RUN_TIMEOUT_MIN="0.02")
        w.steps(ready(sleep=30))
        code, out, err = w.launch("writer", "3")
        self.assertEqual(code, 3, out + err)
        self.assertIn("(timed out)", w.posted("writer")[-1]["body"])
        refused(self, mcp_port(w))

    def test_defect_profile_not_built(self):
        w = gateway_world(self, self.fake, SANDBOX_DENY="relative/path")
        code, out, err = w.launch("writer", "3")
        self.assertEqual(code, 3, out + err)
        self.assertIn("SANDBOX_DENY", w.posted("writer")[-1]["body"])
        refused(self, mcp_port(w))


class Behaviour13Secrets(unittest.TestCase):

    def setUp(self):
        self.fake = FakeFermi(self, skills=[skill("outline-studio", ["outline", "factory:studio"])])

    def test_twin_key_and_token_stay_put(self):
        w = gateway_world(self, self.fake)
        code, out, err = w.launch("writer", "3")
        self.assertEqual(code, 0, out + err)
        run_dir = w.run_dir("writer-3-1")
        token = json.loads((run_dir / "mcp.json").read_text())["mcpServers"]["knowledge"]["headers"]["Authorization"]
        token = token.removeprefix("Bearer ")
        self.assertGreater(len(token), 30)
        holding = {"key": [], "token": []}
        for path in sorted(p for p in run_dir.rglob("*") if p.is_file()):
            data = path.read_bytes()
            for name, secret in (("key", KEY), ("token", token)):
                if secret.encode() in data:
                    holding[name].append(path.relative_to(run_dir).as_posix())
        self.assertEqual(holding, {"key": [], "token": ["mcp.json"]})
        self.assertIn("mcp-harness.answers.json", [p.name for p in run_dir.iterdir()])
        github_text = "".join(p.read_text() for p in sorted(w.github.rglob("*.json*")))
        for secret in (KEY, token):
            self.assertNotIn(secret, github_text)
            self.assertNotIn(secret, out + err)
        self.assertNotIn(self.fake.url, out + err + github_text)

    def test_defect_blank_key_file(self):
        w = gateway_world(self, self.fake)
        (w.secrets / "knowledge.fermi").write_text("   \n  ")
        code, out, err = w.launch("writer", "3")
        self.assertEqual(code, 3, out + err)
        body = w.posted("writer")[-1]["body"]
        self.assertIn("Missing: a valid result (secret knowledge.fermi: empty)", body)
        self.assertEqual(self.fake.records, [])
        self.assertEqual(json.loads((w.run_dir("writer-3-1") / "mcp.json").read_text()), {"mcpServers": {}})


class Behaviour14Sandbox(unittest.TestCase):

    def setUp(self):
        if not sandbox.available():
            self.skipTest("sandbox-exec is not available")
        self.fake = FakeFermi(self, skills=[skill("outline-studio", ["outline", "factory:studio"])])
        self.w = gateway_world(self, self.fake, direct=self.fake.url, SANDBOX="on")
        self.code, self.out, self.err = self.w.launch("writer", "3")

    def test_defect_direct_connection_to_fermi_fails(self):
        self.assertEqual(self.code, 0, self.out + self.err)
        record = answers(self.w)
        self.assertIn("error", record["direct"])
        self.assertEqual([r["method"] for r in self.fake.records],
                         ["initialize", "notifications/initialized", "tools/call"])

    def test_twin_gateway_reached_through_the_profile(self):
        record = answers(self.w)["answers"]["knowledge"]
        self.assertEqual([t["name"] for t in record[2]["body"]["result"]["tools"]], ["skill_load", "skill_search"])
        self.assertFalse(record[3]["body"]["result"]["isError"])
        port = mcp_port(self.w)
        self.assertIn(f'(remote ip "localhost:{port}")', (self.w.run_dir("writer-3-1") / "sandbox.sb").read_text())
        self.assertNotIn(f":{self.fake.port}", (self.w.run_dir("writer-3-1") / "sandbox.sb").read_text())


class Behaviour15SearchTools(Base):

    def test_defect_empty_line_gets_no_tool(self):
        self.assertEqual(gateway.tools("search"), ["search"])
        self.assertEqual(gateway.tools("skills-write"), ["skill_set"])
        self.assertEqual(gateway.tools("skills, memory-read, search"),
                         ["memory_recall", "search", "skill_load", "skill_search"])
        self.assertTrue(gateway.wanted(self.settings(), "search"))
        self.assertNotEqual(gateway.check({"GATEWAY": "on"}, "search"), [])
        for line in ("", None):
            self.assertEqual(gateway.tools(line), [], line)
            self.assertFalse(gateway.wanted(self.settings(), line), line)
            self.assertEqual(gateway.check({"GATEWAY": "on"}, line), [], line)

    def test_twin_search_gateway_lists_search_only(self):
        listed = rpc(self.start("search"), "tools/list")["result"]["tools"]
        self.assertEqual([t["name"] for t in listed], ["search"])
        self.assertIs(listed[0]["inputSchema"]["additionalProperties"], False)
        self.assertEqual(listed[0]["description"], gateway.TOOLS["search"][1])
        self.assertEqual(self.fake.records, [])


class Behaviour16SearchFilter(Base):

    memories = Behaviour4RecallFilter.memories
    skills = [skill("heron-ours", ["heron", "factory:example/salt-marsh"]),
              skill("heron-studio", ["heron", "factory:studio"]),
              skill("heron-other", ["heron", "factory:example/other-show"]),
              skill("heron-owner", ["heron"])]

    def search(self, **arguments):
        text, error = call(self.start("memory-read, search"), "search", {"query": "heron", **arguments})
        self.assertFalse(error, text)
        return text, json.loads(text)

    def test_defect_other_types_and_tags_are_dropped(self):
        text, answer = self.search()
        self.assertEqual([(m["type"], m["id"]) for m in answer["matches"]],
                         [("memory", "1"), ("memory", "2"), ("memory", "7"), ("skill", "heron-ours"),
                          ("skill", "heron-studio")])
        self.assertEqual(answer, {"matches": answer["matches"], "total": 5})
        self.assertEqual(list(answer), ["matches", "total"])
        rows = self.fake.calls()[0]["rows"]
        self.assertEqual(answer["matches"][0], rows[1])
        self.assertEqual({r["type"] for r in rows}, {"message", "capability", "secret", "memory", "skill"})
        self.assertNotIn("message", {m["type"] for m in answer["matches"]})
        self.assertNotIn("capability", {m["type"] for m in answer["matches"]})
        self.assertNotIn("secret", {m["type"] for m in answer["matches"]})
        for word in ("telemetry", "candidateCounts", "offline", "warnings", "guidance", "memories"):
            self.assertNotIn(word, text)

    def test_twin_limit_one(self):
        _, answer = self.search(limit=1)
        self.assertEqual([m["id"] for m in answer["matches"]], ["1"])
        self.assertEqual(answer["total"], 1)
        self.assertEqual(self.fake.calls()[0]["arguments"],
                         {"query": "heron", "types": ["memory", "skill"], "limit": 50})


class Behaviour17SearchMemoryRead(Base):

    memories = [memory(1, "[factory:example/salt-marsh] writer #3\nThe heron leaves at dusk.")]
    skills = [skill("heron-ours", ["heron", "factory:example/salt-marsh"])]

    def test_defect_memory_dropped_without_memory_read(self):
        original = self.fake.tool
        self.fake.tool = lambda name, args: original(name, {**args, "types": ["memory", "skill"]})
        text, error = call(self.start("search"), "search", {"query": "heron"})
        self.assertFalse(error, text)
        self.assertEqual(self.fake.calls()[0]["arguments"], {"query": "heron", "types": ["skill"], "limit": 50})
        self.assertIn("memory", [r["type"] for r in self.fake.calls()[0]["rows"]])
        self.assertEqual([(m["type"], m["id"]) for m in json.loads(text)["matches"]], [("skill", "heron-ours")])

    def test_twin_memory_kept_with_memory_read(self):
        text, error = call(self.start("search, memory-read"), "search", {"query": "heron"})
        self.assertFalse(error, text)
        self.assertEqual([(m["type"], m["id"]) for m in json.loads(text)["matches"]],
                         [("memory", "1"), ("skill", "heron-ours")])


class Behaviour18SearchArguments(Base):

    memories = [memory(n, f"[factory:studio]\nheron {n}") for n in range(1, 13)]

    def test_defect_invalid_arguments(self):
        gw = self.start("memory-read, search")
        cases = [({"query": "heron", "entity": "s-1"}, "argument entity is not taken"),
                 ({"query": "heron", "types": ["secret"]}, "argument types is not taken"),
                 ({"query": "heron", "memoryContext": "x"}, "argument memoryContext is not taken"),
                 ({"query": "heron", "limit": 51}, "argument limit must be an integer from 1 to 50"),
                 ({"query": ""}, "argument query must be a non-empty string")]
        for arguments, problem in cases:
            self.assertEqual(call(gw, "search", arguments),
                             (f"knowledge: invalid arguments for search: {problem}", True), arguments)
        self.assertEqual(self.fake.records, [])

    def test_twin_default_limit(self):
        text, error = call(self.start("memory-read, search"), "search", {"query": "heron"})
        self.assertFalse(error, text)
        self.assertEqual(json.loads(text)["total"], 10)
        self.assertEqual(self.fake.calls()[0]["arguments"]["limit"], 50)


def skill_sets(fake):
    return [r["arguments"] for r in fake.calls() if r["tool"] == "skill_set"]


class Behaviour19SkillSet(Base):

    def write(self):
        return call(self.start("skills-write"), "skill_set",
                    {"slug": "outline-beats", "body": "Step one.", "keywords": ["outline"]})

    def test_twin_written_with_prefix_and_tag(self):
        text, error = self.write()
        self.assertFalse(error, text)
        self.assertEqual(json.loads(text), {"slug": PREFIX + "outline-beats", "version": 1, "created": True})
        self.assertEqual([(r["tool"], r["arguments"]) for r in self.fake.calls()],
                         [("skill_load", {"slug": PREFIX + "outline-beats"}),
                          ("skill_set", {"slug": PREFIX + "outline-beats", "body": "Step one.",
                                         "metadata": {"keywords": ["outline", "factory:example/salt-marsh"],
                                                      "allowed_tools": []}})])
        gw = self.start("skills")
        found = json.loads(call(gw, "skill_search", {"query": "outline"})[0])
        self.assertEqual([s["slug"] for s in found["results"]], [PREFIX + "outline-beats"])
        loaded = json.loads(call(gw, "skill_load", {"slug": PREFIX + "outline-beats"})[0])
        self.assertEqual(loaded["body"], "Step one.")

    def test_defect_other_production_cannot_read_it(self):
        self.assertFalse(self.write()[1])
        other = self.start("skills", REPO="example/other-show")
        self.assertEqual(json.loads(call(other, "skill_load", {"slug": PREFIX + "outline-beats"})[0]),
                         {"error": "skill_not_found", "slug": PREFIX + "outline-beats"})
        self.assertEqual(json.loads(call(other, "skill_search", {"query": "outline"})[0])["results"], [])


class Behaviour20SkillSetOutside(Base):

    skills = [skill(PREFIX + "taken", ["factory:example/other-show"]),
              skill(PREFIX + "shared", ["factory:studio"]),
              skill(PREFIX + "bare", []),
              skill(PREFIX + "mine", ["mine", "factory:example/salt-marsh"])]

    def test_defect_skill_outside_production_is_not_replaced(self):
        gw = self.start("skills-write")
        for slug in ("taken", "shared", "bare"):
            self.assertEqual(call(gw, "skill_set", {"slug": slug, "body": "Replaced."}),
                             (f"knowledge: skill {PREFIX}{slug} is outside this production; choose another slug",
                              True), slug)
        self.assertEqual([r["tool"] for r in self.fake.calls()], ["skill_load"] * 3)
        self.assertEqual(skill_sets(self.fake), [])
        self.assertEqual([s["body"] for s in self.fake.skills[:3]], ["Step one: read the outline."] * 3)

    def test_twin_own_skill_is_changed(self):
        gw = self.start("skills-write")
        for slug, version in (("mine", 2), (PREFIX + "mine", 3)):
            text, error = call(gw, "skill_set", {"slug": slug, "body": "Changed."})
            self.assertFalse(error, text)
            self.assertEqual(json.loads(text), {"slug": PREFIX + "mine", "version": version, "created": False})
        self.assertEqual([a["slug"] for a in skill_sets(self.fake)], [PREFIX + "mine"] * 2)
        self.assertFalse([s["slug"] for s in self.fake.skills if s["slug"].startswith(PREFIX * 2)])
        self.assertEqual(len(self.fake.skills), 4)


class Behaviour21SkillSetArguments(Base):

    def test_defect_invalid_arguments(self):
        gw = self.start("skills-write")
        slug = "argument slug must be lowercase words joined by single dashes, at most 64 characters"
        cases = [({"slug": "Outline_Beats", "body": "x"}, slug),
                 ({"slug": "a" * 65, "body": "x"}, slug),
                 ({"slug": "outline-", "body": "x"}, slug),
                 ({"slug": "outline\n", "body": "x"}, slug),
                 ({"slug": "outline", "body": "x", "keywords": "outline"},
                  "argument keywords must be a list of non-empty strings"),
                 ({"slug": "outline", "body": "x", "keywords": [""]},
                  "argument keywords must be a list of non-empty strings"),
                 ({"slug": "outline", "body": "x", "keywords": ["outline", "factory:studio"]},
                  "argument keywords must not hold a factory: keyword"),
                 ({"slug": "outline", "body": "x", "origin_memory_id": "1"}, "argument origin_memory_id is not taken"),
                 ({"slug": "outline", "body": "x", "metadata": {}}, "argument metadata is not taken"),
                 ({"slug": "outline", "body": "x", "allowed_tools": []}, "argument allowed_tools is not taken"),
                 ({"slug": "outline"}, "argument body is required"),
                 ({"slug": "outline", "body": "x", "name": ""}, "argument name must be a non-empty string")]
        for arguments, problem in cases:
            self.assertEqual(call(gw, "skill_set", arguments),
                             (f"knowledge: invalid arguments for skill_set: {problem}", True), arguments)
        self.assertEqual(self.fake.records, [])

    def test_twin_name_and_description_reach_metadata(self):
        text, error = call(self.start("skills-write"), "skill_set",
                           {"slug": "outline-beats", "body": "Step one.", "name": "Outline beats",
                            "description": "How to outline beats."})
        self.assertFalse(error, text)
        self.assertEqual(skill_sets(self.fake)[0]["metadata"],
                         {"name": "Outline beats", "description": "How to outline beats.",
                          "keywords": ["factory:example/salt-marsh"], "allowed_tools": []})
        self.assertEqual(self.fake.skills[-1]["name"], "Outline beats")


class Behaviour22SkillSetDown(Base):

    def write(self, gw):
        return call(gw, "skill_set", {"slug": "outline-beats", "body": "Step one."})

    def test_defect_fermi_fails_skill_set(self):
        self.fake.fail_tool = "skill_set"
        self.assertEqual(self.write(self.start("skills-write")), (gateway.UNREACHABLE, True))
        self.assertEqual([r["tool"] for r in self.fake.calls()], ["skill_load", "skill_set"])

    def test_defect_empty_skill_load_answer(self):
        self.fake.empty_tool = "skill_load"
        self.assertEqual(self.write(self.start("skills-write")), (gateway.UNREACHABLE, True))
        self.assertEqual([r["tool"] for r in self.fake.calls()], ["skill_load"])

    def test_twin_recovers(self):
        gw = self.start("skills-write")
        self.fake.fail_tool = "skill_set"
        self.assertEqual(self.write(gw), (gateway.UNREACHABLE, True))
        self.fake.fail_tool = None
        text, error = self.write(gw)
        self.assertFalse(error, text)
        self.assertEqual(json.loads(text), {"slug": PREFIX + "outline-beats", "version": 1, "created": True})


class Behaviour23SearchNoTool(Base):

    def test_defect_tools_outside_the_line(self):
        gw = self.start("skills, memory-read, search")
        for name in ("skill_set", "session_search"):
            self.assertEqual(call(gw, name, {"slug": "x", "body": "y"}), (f"knowledge: no tool {name}", True))
        writer = self.start("skills-write")
        for name in ("skill_load", "search"):
            self.assertEqual(call(writer, name, {"query": "x"}), (f"knowledge: no tool {name}", True))
        self.assertEqual(self.fake.records, [])

    def test_twin_skills_write_gateway(self):
        gw = self.start("skills-write")
        self.assertEqual(tool_names(gw), ["skill_set"])
        self.assertFalse(call(gw, "skill_set", {"slug": "outline-beats", "body": "Step one."})[1])
        self.assertEqual([r["tool"] for r in self.fake.calls()], ["skill_load", "skill_set"])


class Checks(Base):
    """The rules ``gateway.check`` and ``gateway.start`` add to the register's checks (behaviour 11 is
    in tests/test_launch.py)."""

    def test_reasons(self):
        good = self.settings()
        self.assertEqual(gateway.check(good, "skills"), [])
        cases = [
            ({"REPO": ""}, ["setting REPO is empty"]),
            ({"REPO": "salt-marsh"}, ["setting REPO is not owner/name"]),
            ({"REPO": "example/salt marsh"}, ["setting REPO is not owner/name"]),
            ({"FERMI_URL": "http://fermi.invalid/mcp"}, ["setting FERMI_URL is not an https URL"]),
            ({"FERMI_URL": "ftp://127.0.0.1/mcp"}, ["setting FERMI_URL is not an https URL"]),
            ({"FERMI_URL": "https://user:pw@fermi.invalid/mcp"}, ["setting FERMI_URL is not an https URL"]),
            ({"FERMI_URL": "https://fermi.invalid:99999/mcp"}, ["setting FERMI_URL is not an https URL"]),
            ({"FERMI_URL": ""}, ["setting FERMI_URL is empty"]),
        ]
        for extra, reasons in cases:
            self.assertEqual(gateway.check({**good, **extra}, "skills"), reasons, extra)
        self.assertEqual(gateway.check({**good, "FERMI_URL": "https://fermi.invalid/mcp"}, "skills"), [])
        self.assertEqual(gateway.check({**good, "FERMI_URL": "http://127.0.0.1:9/mcp"}, "skills"), [])
        self.assertEqual(gateway.check({**good, "GATEWAY": "off", "REPO": ""}, "skills"), [])

    def test_start_refuses_without_a_word_of_value(self):
        with self.assertRaises(gateway.GatewayError) as caught:
            gateway.start(self.settings(REPO="", FERMI_URL="http://fermi.invalid/x"), "skills", "writer", 3)
        self.assertEqual(str(caught.exception), "the knowledge gateway is not usable: setting REPO is empty; "
                                                "setting FERMI_URL is not an https URL")
        self.key("  \n")
        with self.assertRaises(gateway.GatewayError) as caught:
            gateway.start(self.settings(), "skills", "writer", 3)
        self.assertEqual(str(caught.exception), "secret knowledge.fermi: empty")

    def test_proxy_settings_are_ignored(self):
        with mock.patch.dict(os.environ, {"http_proxy": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1"}):
            gw = self.start()
            self.assertFalse(call(gw, "skill_search", {"query": "x"})[1])


if __name__ == "__main__":
    unittest.main()
