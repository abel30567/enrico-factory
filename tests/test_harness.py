import copy
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from kit.lib import frontmatter, harness

FIXTURES = Path(__file__).parent / "fixtures" / "harness"
ENVELOPES = FIXTURES / "envelopes"
KIT = Path(__file__).parent.parent / "kit"
ROLE = frontmatter.parse((FIXTURES / "role.md").read_text())[0]
RESULTS = json.loads((FIXTURES / "results.json").read_text())

# Distinctive strings that must never appear in an error message.
ACCOUNT = "zebra-account-q91"
SECRET_DIR_NAME = "secret-dir-k27"
TOKEN_TEXT = "token-text-u5x8-never-copied"

# macOS sets this in every process it starts; it does not come from the environment the adapter built.
OS_ADDED = {"__CF_USER_TEXT_ENCODING"}
ALLOWLIST = ["CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR", "CLAUDE_CONFIG_DIR", "HOME", "LANG",
             "LC_ALL", "PATH", "TERM", "TMPDIR"]

SETTINGS = {"HARNESS": "/opt/invented/harness", "MODEL_MAKER": "maker-model-1",
            "MODEL_CHECKER": "checker-model-1", "MODEL_OTHER": "other-model-1",
            "PROXY_URL": "http://127.0.0.1:9999"}


def result_for(marker):
    case = next(c for c in RESULTS if c["result"]["marker"] == marker)
    return case["kind"], case["actor"], copy.deepcopy(case["result"])


class SchemaTest(unittest.TestCase):
    def test_shape(self):
        schema = harness.RESULT_SCHEMA
        self.assertEqual(schema["required"], ["marker", "summary"])
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(sorted(schema["properties"]), sorted(
            ["marker", "summary", "evidence", "notes", "addressed", "prefers", "selected", "vote",
             "blocked"]))
        self.assertEqual(schema["properties"]["marker"]["enum"], [
            "READY", "ADDRESSED", "BLOCKED", "APPROVED", "NOTES", "PREFERS A", "PREFERS B",
            "NO PREFERENCE", "SYMPTOMS", "RULING", "SELECTED", "VOTE"])
        self.assertEqual(schema["properties"]["selected"], {"type": "integer"})
        self.assertEqual(schema["properties"]["notes"]["items"]["properties"]["severity"]["enum"],
                         ["Blocker", "Issue"])
        self.assertEqual(schema["properties"]["addressed"]["items"]["properties"]["action"]["enum"],
                         ["changed", "rebutted"])


class ValidateResultTest(unittest.TestCase):
    def check(self, kind, actor, result, expected):
        self.assertEqual(harness.validate_result(kind, actor, result), expected)

    def test_one_passing_result_per_marker(self):
        self.assertEqual(sorted(c["result"]["marker"] for c in RESULTS),
                         sorted(harness.RESULT_SCHEMA["properties"]["marker"]["enum"]))
        for case in RESULTS:
            with self.subTest(marker=case["result"]["marker"]):
                self.check(case["kind"], case["actor"], case["result"], [])

    def test_not_an_object(self):
        self.check("maker", "SKETCHER", ["READY"], ["result: is not an object"])

    def test_unknown_key(self):
        kind, actor, result = result_for("RULING")
        result["extra"] = "x"
        self.check(kind, actor, result, ["result: unknown key 'extra'"])

    def test_unknown_nested_key(self):
        kind, actor, result = result_for("READY")
        result["evidence"][0]["extra"] = "x"
        self.check(kind, actor, result, ["result.evidence[0]: unknown key 'extra'"])

    def test_missing_required(self):
        kind, actor, result = result_for("RULING")
        del result["summary"]
        self.check(kind, actor, result, ["result: missing key 'summary'"])

    def test_wrong_type(self):
        kind, actor, result = result_for("RULING")
        result["summary"] = 5
        self.check(kind, actor, result, ["result.summary: is not a string"])

    def test_boolean_is_not_an_integer(self):
        kind, actor, result = result_for("SELECTED")
        result["selected"] = True
        self.check(kind, actor, result, ["result.selected: is not an integer"])

    def test_marker_the_kind_may_not_post(self):
        for kind, actor, marker in (("maker", "SKETCHER", "APPROVED"),
                                    ("operator", "UPLOADER", "NOTES"),
                                    ("checker", "EDITOR", "READY"),
                                    ("judge", "PANEL", "RULING"),
                                    ("analyst", "ANALYST", "APPROVED"),
                                    ("orchestrator", "CHIEF", "READY")):
            with self.subTest(kind=kind):
                result = result_for(marker)[2]
                self.check(kind, actor, result,
                           [f"result.marker: a {kind} posting as {actor} may not post {marker!r}"])

    def test_launch(self):
        self.check("orchestrator", "CHIEF", {"marker": "LAUNCH", "summary": "x"}, [
            "result.marker: 'LAUNCH' is not one of READY, ADDRESSED, BLOCKED, APPROVED, NOTES, "
            "PREFERS A, PREFERS B, NO PREFERENCE, SYMPTOMS, RULING, SELECTED, VOTE",
            "result.marker: LAUNCH is posted by the launcher, never by a role"])

    def test_vote_only_for_a_judge_posting_as_braintrust(self):
        kind, actor, result = result_for("VOTE")
        self.check("judge", "BRAINTRUST", result, [])
        self.check("judge", "PANEL", result,
                   ["result.marker: a judge posting as PANEL may not post 'VOTE'"])
        self.check("checker", "BRAINTRUST", result,
                   ["result.marker: a checker posting as BRAINTRUST may not post 'VOTE'"])

    def test_notes_with_no_note(self):
        for marker in ("NOTES", "SYMPTOMS"):
            with self.subTest(marker=marker):
                kind, actor, result = result_for(marker)
                result["notes"] = []
                self.check(kind, actor, result, [f"result.notes: {marker} needs at least one note"])
                del result["notes"]
                self.check(kind, actor, result, [f"result.notes: {marker} needs at least one note"])

    def test_id_and_severity_disagree(self):
        kind, actor, result = result_for("NOTES")
        result["notes"][0]["severity"] = "Issue"
        result["notes"][1]["id"] = "I2"
        self.check(kind, actor, result, [
            "result.notes[0].id: 'B1' does not fit severity Issue (B<n> is a Blocker, I<n> an Issue)"])
        kind, actor, result = result_for("SYMPTOMS")
        result["notes"][0]["id"] = "N1"
        self.check(kind, actor, result, [
            "result.notes[0].id: 'N1' does not fit severity Issue (B<n> is a Blocker, I<n> an Issue)"])

    def test_duplicate_ids(self):
        kind, actor, result = result_for("NOTES")
        result["notes"][1] = dict(result["notes"][0])
        self.check(kind, actor, result, ["result.notes[1].id: duplicate id 'B1'"])

    def test_approved_with_a_blocker(self):
        kind, actor, result = result_for("APPROVED")
        result["notes"][0].update(id="B1", severity="Blocker")
        self.check(kind, actor, result, ["result.notes[0].severity: APPROVED carries a Blocker"])

    def test_prefers_choice_disagrees(self):
        kind, actor, result = result_for("PREFERS A")
        result["prefers"]["choice"] = "B"
        self.check(kind, actor, result, ["result.prefers.choice: PREFERS A needs choice 'A'"])
        kind, actor, result = result_for("NO PREFERENCE")
        del result["prefers"]
        self.check(kind, actor, result, ["result.prefers.choice: NO PREFERENCE needs choice 'none'"])

    def test_blocked_with_an_empty_field(self):
        kind, actor, result = result_for("BLOCKED")
        result["blocked"]["tried"] = "  "
        self.check(kind, actor, result, ["result.blocked.tried: is empty"])
        del result["blocked"]
        self.check(kind, actor, result,
                   ["result.blocked: BLOCKED needs missing, tried and resume_from"])

    def test_ready_with_no_evidence(self):
        for marker in ("READY", "ADDRESSED"):
            with self.subTest(marker=marker):
                kind, actor, result = result_for(marker)
                result["evidence"] = []
                self.check(kind, actor, result,
                           [f"result.evidence: {marker} needs at least one evidence entry"])

    def test_addressed_with_no_addressed(self):
        kind, actor, result = result_for("ADDRESSED")
        del result["addressed"]
        self.check(kind, actor, result,
                   ["result.addressed: ADDRESSED needs at least one addressed entry"])

    def test_selected_with_no_number(self):
        kind, actor, result = result_for("SELECTED")
        del result["selected"]
        self.check(kind, actor, result, ["result.selected: SELECTED needs a pull request number >= 1"])
        result["selected"] = 0
        self.check(kind, actor, result, ["result.selected: SELECTED needs a pull request number >= 1"])

    def test_vote_with_no_tally(self):
        kind, actor, result = result_for("VOTE")
        del result["vote"]
        self.check(kind, actor, result, ["result.vote: VOTE needs a tally with both counts >= 0"])
        result["vote"] = {"for": -1, "against": 0, "reasons": []}
        self.check(kind, actor, result, ["result.vote: VOTE needs a tally with both counts >= 0"])


class VersionTest(unittest.TestCase):
    def test_fake_harness(self):
        settings = {"HARNESS": str(FIXTURES / "fake-harness")}
        self.assertEqual(harness.version(settings), "0.0.0 (fake)")

    def test_could_not_run(self):
        def timeout(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

        def failing(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, "1.0.0\n", "")

        cases = (({"HARNESS": "/nonexistent/" + SECRET_DIR_NAME}, subprocess.run),
                 ({}, subprocess.run),
                 ({"HARNESS": "harness"}, timeout),
                 ({"HARNESS": "harness"}, failing))
        for settings, run in cases:
            with self.subTest(run=run.__name__, settings=settings):
                with self.assertRaises(harness.HarnessError) as caught:
                    harness.version(settings, run=run)
                self.assertEqual(str(caught.exception), "harness version: could not run")

    def test_timeout_and_first_line(self):
        seen = {}

        def run(argv, **kwargs):
            seen.update(argv=argv, timeout=kwargs["timeout"])
            return subprocess.CompletedProcess(argv, 0, "  2.1.0 (build)  \nmore\n", "")

        self.assertEqual(harness.version({"HARNESS": "h"}, run=run), "2.1.0 (build)")
        self.assertEqual(seen, {"argv": ["h", "--version"], "timeout": 20})


class ModelTest(unittest.TestCase):
    def test_other_with_proxy(self):
        meta = {"model": "checker", "family": "other"}
        self.assertEqual(harness.model(SETTINGS, meta), ("other-model-1", "other"))

    def test_other_without_proxy_is_same(self):
        meta = {"model": "checker", "family": "other"}
        for missing in ("PROXY_URL", "MODEL_OTHER"):
            with self.subTest(missing=missing):
                settings = dict(SETTINGS, **{missing: ""})
                self.assertEqual(harness.model(settings, meta), ("checker-model-1", "same"))

    def test_any(self):
        self.assertEqual(harness.model(SETTINGS, {"model": "maker", "family": "any"}),
                         ("maker-model-1", "any"))

    def test_empty_slot(self):
        with self.assertRaises(harness.HarnessError) as caught:
            harness.model(dict(SETTINGS, MODEL_CHECKER=""), {"model": "checker", "family": "any"})
        self.assertEqual(str(caught.exception), "setting MODEL_CHECKER is empty")


class CommandTest(unittest.TestCase):
    def test_whole_list(self):
        argv = harness.command(SETTINGS, ROLE, Path("/run/mcp.json"))
        self.assertEqual(argv, [
            "/opt/invented/harness", "-p", "--model", "maker-model-1",
            "--tools", "Read,Write,Grep,Bash",
            "--strict-mcp-config", "--mcp-config", "/run/mcp.json",
            "--setting-sources", "project", "--permission-mode", "bypassPermissions",
            "--output-format", "json", "--json-schema", argv[-1]])
        self.assertEqual(json.loads(argv[-1]), harness.RESULT_SCHEMA)
        self.assertEqual(argv[-1], json.dumps(harness.RESULT_SCHEMA, separators=(",", ":"),
                                              sort_keys=True))

    def test_no_tools(self):
        argv = harness.command(SETTINGS, dict(ROLE, tools=""), "/run/mcp.json")
        self.assertEqual(argv[argv.index("--tools") + 1], "")

    def test_unknown_tool(self):
        with self.assertRaises(harness.HarnessError):
            harness.command(SETTINGS, dict(ROLE, tools="Read, Teleport"), "/run/mcp.json")


class McpConfigTest(unittest.TestCase):
    def test_empty_and_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mcp.json"
            harness.write_mcp_config(path)
            self.assertEqual(json.loads(path.read_text()), {"mcpServers": {}})
            servers = {"gateway": {"command": "gateway", "args": []}}
            harness.write_mcp_config(path, servers)
            self.assertEqual(json.loads(path.read_text()), {"mcpServers": servers})


def invented_parent(**extra):
    """Twenty invented variables, among them keys and tokens that must never reach a worker."""
    parent = {
        "PATH": "/usr/bin:/bin", "HOME": "/home/invented", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "TMPDIR": "/tmp/invented", "TERM": "dumb",
        "INVENTED_VENDOR_API_KEY": "k1", "OTHER_SERVICE_API_KEY": "k2", "GH_TOKEN": "t1",
        "CLAUDE_CODE_OAUTH_TOKEN": "t2", "SOME_SESSION_TOKEN": "t3",
        "ANTHROPIC_BASE_URL": "http://parent.invalid", "CLAUDE_CONFIG_DIR": "/home/invented/cfg",
        "SHELL": "/bin/sh", "USER": "invented", "EDITOR": "vi", "PYTHONPATH": "/x",
        "LD_PRELOAD": "/x.so", "SSH_AUTH_SOCK": "/tmp/agent", "DISPLAY": ":0",
    }
    parent.update(extra)
    return parent


class EnvironmentTest(unittest.TestCase):
    def test_allowlist_only(self):
        parent = invented_parent()
        self.assertEqual(len(parent), 20)
        for family in ("any", "same"):
            with self.subTest(family=family):
                env = harness.environment(parent, SETTINGS, family, "/run/cfg", 3)
                self.assertEqual(sorted(env), ALLOWLIST)
                self.assertEqual(env["CLAUDE_CONFIG_DIR"], "/run/cfg")
                self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR"], "3")
                self.assertEqual(env["HOME"], "/home/invented")

    def test_other_points_at_the_proxy(self):
        env = harness.environment(invented_parent(), SETTINGS, "other", "/run/cfg", 3)
        self.assertEqual(sorted(env), sorted(ALLOWLIST + ["ANTHROPIC_BASE_URL"]))
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "http://127.0.0.1:9999")

    def test_absent_parent_keys_are_not_invented(self):
        env = harness.environment({"PATH": "/bin", "AWS_SECRET": "x"}, SETTINGS, "any", "/c", 3)
        self.assertEqual(sorted(env), ["CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR",
                                       "CLAUDE_CONFIG_DIR", "PATH"])

    def test_tmp_dir_is_the_workers_tmpdir(self):
        env = harness.environment(invented_parent(), SETTINGS, "any", "/run/cfg", 3, tmp_dir="/run/tmp")
        self.assertEqual(env["TMPDIR"], "/run/tmp")
        self.assertEqual(sorted(env), ALLOWLIST)
        env = harness.environment(invented_parent(), SETTINGS, "any", "/run/cfg", 3)
        self.assertEqual(env["TMPDIR"], "/tmp/invented")
        env = harness.environment({"PATH": "/bin"}, SETTINGS, "any", "/run/cfg", 3)
        self.assertNotIn("TMPDIR", env)


class AccountsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.secrets = Path(self.tmp.name) / SECRET_DIR_NAME
        (self.secrets / "inference.primary").mkdir(parents=True)
        self.settings = {"SECRETS_DIR": str(self.secrets),
                         "ACCOUNTS_MAKER": f" {ACCOUNT} , second-acct ,",
                         "ACCOUNTS_CHECKER": "", "ACCOUNTS_OTHER": "proxy"}

    def tearDown(self):
        self.tmp.cleanup()

    def write_token(self, path, text=TOKEN_TEXT, mode=0o600):
        path.write_text(text)
        os.chmod(path, mode)
        return path

    def refused(self, call, expected):
        with self.assertRaises(harness.HarnessError) as caught:
            call()
        message = str(caught.exception)
        self.assertEqual(message, expected)
        for secret in (ACCOUNT, SECRET_DIR_NAME, self.tmp.name, TOKEN_TEXT):
            self.assertNotIn(secret, message)

    def test_accounts(self):
        self.assertEqual(harness.accounts(self.settings, "MAKER"), [ACCOUNT, "second-acct"])
        self.assertEqual(harness.accounts(self.settings, "OTHER"), ["proxy"])

    def test_accounts_empty(self):
        self.refused(lambda: harness.accounts(self.settings, "CHECKER"),
                     "setting ACCOUNTS_CHECKER is empty")

    def test_token_path(self):
        path = self.write_token(self.secrets / "inference.primary" / ACCOUNT)
        self.assertEqual(harness.token_path(self.settings, "MAKER", ACCOUNT), path)
        proxy = self.write_token(self.secrets / "proxy.client")
        self.assertEqual(harness.token_path(self.settings, "OTHER", "ignored"), proxy)

    def test_token_path_expands_home(self):
        home = Path(self.tmp.name)
        path = self.write_token(self.secrets / "inference.primary" / ACCOUNT)
        settings = dict(self.settings, SECRETS_DIR="~/" + SECRET_DIR_NAME)
        old = os.environ.get("HOME")
        os.environ["HOME"] = str(home)
        try:
            self.assertEqual(harness.token_path(settings, "MAKER", ACCOUNT), path)
        finally:
            if old is None:
                del os.environ["HOME"]
            else:
                os.environ["HOME"] = old

    def test_token_missing(self):
        self.refused(lambda: harness.token_path(self.settings, "MAKER", ACCOUNT),
                     "token for slot MAKER: the file is missing")

    def test_token_empty(self):
        self.write_token(self.secrets / "inference.primary" / ACCOUNT, text="")
        self.refused(lambda: harness.token_path(self.settings, "MAKER", ACCOUNT),
                     "token for slot MAKER: the file is empty")

    def test_token_readable_by_others(self):
        self.write_token(self.secrets / "proxy.client", mode=0o644)
        self.refused(lambda: harness.token_path(self.settings, "OTHER", ACCOUNT),
                     "token for slot OTHER: the file grants access to group or other")

    def test_account_that_is_a_path(self):
        self.write_token(self.secrets / "proxy.client")
        self.refused(lambda: harness.token_path(self.settings, "CHECKER", "../proxy.client"),
                     "token for slot CHECKER: the account name is not a file name")

    def test_secrets_dir_empty(self):
        self.refused(lambda: harness.token_path({}, "MAKER", ACCOUNT),
                     "setting SECRETS_DIR is empty")

    def test_pick(self):
        names = [ACCOUNT, "second-acct", "third-acct"]
        now = "2026-11-11T10:00:00Z"
        self.assertEqual(harness.pick(names, {}, now), ACCOUNT)
        exhausted = {ACCOUNT: "2026-11-11T12:00:00Z", "second-acct": "2026-11-11T09:00:00Z"}
        self.assertEqual(harness.pick(names, exhausted, now), "second-acct")
        exhausted["second-acct"] = "2026-11-11T10:00:01Z"
        self.assertEqual(harness.pick(names, exhausted, now), "third-acct")
        exhausted["third-acct"] = "2026-11-12T00:00:00Z"
        self.assertIsNone(harness.pick(names, exhausted, now))


class FakeHarnessCase(unittest.TestCase):
    """A temporary directory holding a copy of the fake harness and its steps."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.fake = self.bin / "fake-harness"
        shutil.copy(FIXTURES / "fake-harness", self.fake)
        os.chmod(self.fake, 0o755)
        self.work = self.root / "work"
        self.work.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def steps(self, steps):
        (self.bin / "fake-harness.json").write_text(json.dumps(steps))

    def calls(self):
        path = self.bin / "fake-harness.calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def run_fake(self, prompt, *args):
        return subprocess.run([str(self.fake), *args], input=prompt, capture_output=True,
                              text=True, cwd=self.work, timeout=30)


class FakeHarnessTest(FakeHarnessCase):
    def test_version(self):
        done = self.run_fake("", "--version")
        self.assertEqual((done.returncode, done.stdout), (0, "0.0.0 (fake)\n"))

    def test_steps_in_order_of_first_match_never_reused(self):
        self.steps([{"match": "alpha", "result": {"n": 0}},
                    {"match": "a", "result": {"n": 1}},
                    {"match": "beta", "result": {"n": 2}}])
        outputs = []
        for prompt in ("an alpha", "an alpha", "beta", "an alpha"):
            done = self.run_fake(prompt)
            outputs.append((done.returncode, done.stdout and json.loads(done.stdout)))
        envelope = {"type": "result", "subtype": "success", "is_error": False, "result": ""}
        self.assertEqual(outputs, [(0, dict(envelope, structured_output={"n": 0})),
                                   (0, dict(envelope, structured_output={"n": 1})),
                                   (0, dict(envelope, structured_output={"n": 2})),
                                   (3, "")])
        self.assertEqual([c["step"] for c in self.calls()], [0, 1, 2])

    def test_no_match_exits_3(self):
        self.steps([{"match": "never"}])
        done = self.run_fake("something else")
        self.assertEqual((done.returncode, done.stdout), (3, ""))
        self.assertEqual(self.calls(), [])

    def test_files_stdout_envelope_and_exit(self):
        (self.work / "old.txt").write_text("old")
        self.steps([{"match": "one", "files": {"a/b.txt": "hello", "old.txt": None},
                     "stdout": "raw\n", "exit": 7},
                    {"match": "two", "envelope": {"type": "result", "subtype": "error"}}])
        done = self.run_fake("one")
        self.assertEqual((done.returncode, done.stdout), (7, "raw\n"))
        self.assertEqual((self.work / "a" / "b.txt").read_text(), "hello")
        self.assertFalse((self.work / "old.txt").exists())
        done = self.run_fake("two")
        self.assertEqual(json.loads(done.stdout), {"type": "result", "subtype": "error"})
        self.assertFalse(self.calls()[0]["fd3"])

    def test_sleep_is_honoured(self):
        self.steps([{"match": "", "sleep": 0.6}])
        began = time.monotonic()
        self.run_fake("x")
        self.assertGreaterEqual(time.monotonic() - began, 0.6)


class StartTest(FakeHarnessCase):
    def setUp(self):
        super().setUp()
        self.run_dir = self.root / "run"
        (self.run_dir / "config").mkdir(parents=True)
        secrets = self.root / SECRET_DIR_NAME
        (secrets / "inference.primary").mkdir(parents=True)
        token = secrets / "inference.primary" / ACCOUNT
        token.write_text(TOKEN_TEXT)
        os.chmod(token, 0o600)
        self.settings = dict(SETTINGS, HARNESS=str(self.fake), SECRETS_DIR=str(secrets),
                             ACCOUNTS_MAKER=ACCOUNT)
        self.token = harness.token_path(self.settings, "MAKER", ACCOUNT)
        (self.run_dir / "prompt.md").write_text("Sketch the invented scene.\n")
        mcp = self.run_dir / "mcp.json"
        harness.write_mcp_config(mcp)
        self.argv = harness.command(self.settings, ROLE, mcp)
        parent = invented_parent(PATH=os.environ["PATH"], HOME=str(self.root / "home"),
                                 TMPDIR=str(self.root / "scratch"))
        self.env = harness.environment(parent, self.settings, "any", self.run_dir / "config", 3)

    def start(self, token_fd=3):
        return harness.start(self.argv, self.env, self.work, self.run_dir / "prompt.md",
                             self.token, self.run_dir / "stdout", self.run_dir / "stderr",
                             token_fd=token_fd)

    def test_run(self):
        result = result_for("READY")[2]
        self.steps([{"match": "invented scene", "files": {"sketches/one.md": "# One\n"},
                     "result": result}])
        process = self.start()
        self.assertEqual(process.wait(timeout=30), 0)
        self.assertEqual((self.work / "sketches" / "one.md").read_text(), "# One\n")
        [call] = self.calls()
        self.assertIs(call["fd3"], True)
        self.assertEqual(call["argv"], self.argv)
        self.assertEqual(Path(call["cwd"]).resolve(), self.work.resolve())
        self.assertEqual(sorted(set(call["env_keys"]) - OS_ADDED), ALLOWLIST)
        outcome = harness.read((self.run_dir / "stdout").read_text(), process.returncode,
                               (self.run_dir / "stderr").read_text())
        self.assertEqual(outcome, harness.Outcome("ok", result, "result read (exit 0)"))
        self.assertNotIn(TOKEN_TEXT, " ".join(self.argv) + json.dumps(self.env))
        leaks = [str(p) for p in self.root.rglob("*")
                 if p.is_file() and p != self.token and TOKEN_TEXT.encode() in p.read_bytes()]
        self.assertEqual(leaks, [])

    def test_token_moved_to_the_descriptor(self):
        # A write-only descriptor 3 in the parent forces the token onto a higher number there; the
        # child must still read the token, not the parent's descriptor 3, on descriptor 3.
        write_only = os.open(os.devnull, os.O_WRONLY)
        try:
            saved = (os.dup(3), os.get_inheritable(3))
        except OSError:
            saved = None
        os.dup2(write_only, 3, inheritable=False)
        try:
            self.steps([{"match": "", "result": {"marker": "RULING", "summary": "x"}}])
            process = self.start()
            self.assertEqual(process.wait(timeout=30), 0)
        finally:
            if saved is None:
                os.close(3)
            else:
                os.dup2(saved[0], 3, inheritable=saved[1])
                os.close(saved[0])
            os.close(write_only)
        self.assertIs(self.calls()[0]["fd3"], True)

    def test_killed_by_process_group(self):
        self.steps([{"match": "", "sleep": 60}])
        process = self.start()
        try:
            deadline = time.monotonic() + 20
            while not self.calls() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(os.getpgid(process.pid), process.pid)
            os.killpg(process.pid, signal.SIGKILL)
            self.assertEqual(process.wait(timeout=10), -signal.SIGKILL)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def envelope(name):
    return (ENVELOPES / name).read_text()


class ReadTest(unittest.TestCase):
    def test_ok_structured(self):
        outcome = harness.read(envelope("ok-structured.stdout"), 0)
        self.assertEqual(outcome.status, "ok")
        self.assertEqual(outcome.result["marker"], "READY")
        self.assertEqual(outcome.reason, "result read (exit 0)")

    def test_ok_result_string_is_json(self):
        outcome = harness.read(envelope("ok-result-string.stdout"), 0)
        self.assertEqual(outcome, harness.Outcome(
            "ok", {"marker": "RULING", "summary": "Keep the invented title."}, "result read (exit 0)"))

    def test_last_result_line_wins(self):
        outcome = harness.read(envelope("with-log-lines.stdout"), 0)
        self.assertEqual((outcome.status, outcome.result["marker"]), ("ok", "APPROVED"))

    def test_neither_form(self):
        self.assertEqual(harness.read(envelope("no-result-object.stdout"), 0),
                         harness.Outcome("failed", None, "no result object (exit 0)"))

    def test_out_of_usage(self):
        for name in ("out-of-usage-status.stdout", "out-of-usage-text.stdout"):
            with self.subTest(name=name):
                self.assertEqual(harness.read(envelope(name), 1),
                                 harness.Outcome("out_of_usage", None, "account out of usage (exit 1)"))

    def test_login_ended(self):
        for name in ("login-ended-status.stdout", "login-ended-text.stdout"):
            with self.subTest(name=name):
                self.assertEqual(harness.read(envelope(name), 1), harness.Outcome(
                    "login_ended", None, "login expired or revoked (exit 1)"))
        forbidden = envelope("login-ended-status.stdout").replace("401", "403")
        self.assertEqual(harness.read(forbidden, 1).status, "login_ended")

    def test_proxy_down(self):
        self.assertEqual(harness.read("", 1, envelope("proxy-down.stderr")),
                         harness.Outcome("proxy_down", None, "model proxy unreachable (exit 1)"))

    def test_failed(self):
        for name in ("failed-subtype.stdout", "failed-error.stdout"):
            with self.subTest(name=name):
                self.assertEqual(harness.read(envelope(name), 0),
                                 harness.Outcome("failed", None, "run failed (exit 0)"))

    def test_not_json(self):
        self.assertEqual(harness.read("Segmentation fault\n{not json", 139, "boom"),
                         harness.Outcome("failed", None, "run failed (exit 139)"))

    def test_non_zero_exit_with_a_valid_envelope(self):
        self.assertEqual(harness.read(envelope("ok-structured.stdout"), 2),
                         harness.Outcome("failed", None, "run failed (exit 2)"))

    def test_reason_never_carries_output(self):
        for name in sorted(p.name for p in ENVELOPES.glob("*.stdout")):
            with self.subTest(name=name):
                reason = harness.read(envelope(name), 1, "stderr-secret-text").reason
                self.assertNotIn("stderr-secret-text", reason)
                self.assertRegex(reason, r"^[a-z ]+ \(exit 1\)$")

    def test_readme_lists_every_envelope(self):
        readme = (ENVELOPES / "README.md").read_text()
        self.assertIn("synthetic", readme)
        for path in ENVELOPES.iterdir():
            if path.name != "README.md":
                self.assertIn(f"`{path.name}`", readme)


class OnlyTheAdapterNamesHarnessFlags(unittest.TestCase):
    def test_kit(self):
        names = ("--strict-mcp-config", "--json-schema", "--setting-sources", "--permission-mode",
                 "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR", "ANTHROPIC_BASE_URL")
        found = sorted(str(p.relative_to(KIT)) for p in KIT.rglob("*")
                       if p.is_file() and p.suffix != ".pyc"
                       and any(n in p.read_text(errors="replace") for n in names))
        self.assertEqual(found, ["lib/harness.py"])


if __name__ == "__main__":
    unittest.main()
