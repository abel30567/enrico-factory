"""Tests for the macOS sandbox profile. Each denied action has a test that fails if the action succeeds."""

import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kit.lib import sandbox  # noqa: E402

ARGS = dict(root="/srv/prod", worktree="/srv/state/worktrees/w1", scratch="/srv/state/scratch/w1",
            state_dir="/srv/state", secrets_dir="/srv/keys", home="/srv/home")
ENFORCEMENT = {"ran": 0, "total": 0, "reason": ""}


def lines(text):
    return text.splitlines()


class ProfileTests(unittest.TestCase):
    def test_equal_arguments_give_identical_profiles(self):
        a = sandbox.profile(**ARGS, endpoints=("localhost:9000", "*:443"))
        b = sandbox.profile(**ARGS, endpoints=("*:443", "localhost:9000"))
        self.assertEqual(a.encode(), b.encode())
        self.assertEqual(a, sandbox.profile(**ARGS, endpoints=("*:443", "localhost:9000")))

    def test_base_is_allow_default(self):
        text = sandbox.profile(**ARGS)
        self.assertEqual(lines(text)[:2], ["(version 1)", "(allow default)"])
        self.assertNotIn("(deny default)", text)

    def test_read_denies_follow_the_read_allows(self):
        rules = lines(sandbox.profile(**ARGS))
        state = rules.index('(deny file-read* (subpath "/srv/state"))')
        allow = rules.index('(allow file-read* (subpath "/srv/state/worktrees/w1") (subpath "/srv/state/scratch/w1"))')
        secrets = rules.index('(deny file-read* (subpath "/srv/keys"))')
        self.assertLess(state, allow)
        self.assertLess(allow, secrets)

    def test_home_credentials_are_denied(self):
        text = sandbox.profile(**ARGS)
        for name in (".ssh", ".config/gh", ".gnupg", ".netrc", "Library/Keychains"):
            self.assertIn(f'(subpath "/srv/home/{name}")', text)
        rule = [r for r in lines(text) if "/srv/home/.ssh" in r][0]
        self.assertTrue(rule.startswith("(deny file-read* "))

    def test_writes_only_in_worktree_scratch_and_devices(self):
        rules = lines(sandbox.profile(**ARGS))
        deny = rules.index("(deny file-write*)")
        allow = rules.index('(allow file-write* (subpath "/srv/state/worktrees/w1") '
                            '(subpath "/srv/state/scratch/w1") (literal "/dev/null") (literal "/dev/tty"))')
        protected = rules.index('(deny file-write* (subpath "/srv/state/worktrees/w1/.factory") '
                                '(subpath "/srv/state/worktrees/w1/.git"))')
        self.assertLess(deny, allow)
        self.assertLess(allow, protected)

    def test_process_exec_denies(self):
        rule = [r for r in lines(sandbox.profile(**ARGS)) if r.startswith("(deny process-exec ")]
        self.assertEqual(len(rule), 1)
        for directory in ("/usr/bin", "/bin", "/usr/local/bin", "/opt/homebrew/bin"):
            for name in ("launchctl", "osascript", "crontab", "at", "gh"):
                self.assertIn(f'(literal "{directory}/{name}")', rule[0])

    def test_no_web_and_no_endpoints_allows_no_outbound(self):
        text = sandbox.profile(**ARGS)
        self.assertIn('(deny network-outbound (remote ip "*:*"))', lines(text))
        self.assertFalse([r for r in lines(text) if r.startswith("(allow network-outbound")])

    def test_endpoints_are_allowed_in_sorted_order(self):
        rules = lines(sandbox.profile(**ARGS, endpoints=("localhost:9000", "*:443")))
        allows = [r for r in rules if r.startswith("(allow network-outbound")]
        self.assertEqual(allows, ['(allow network-outbound (remote ip "*:443"))',
                                  '(allow network-outbound (remote ip "localhost:9000"))'])
        self.assertLess(rules.index('(deny network-outbound (remote ip "*:*"))'), rules.index(allows[0]))

    def test_web_allows_all_outbound(self):
        text = sandbox.profile(**ARGS, web=True, endpoints=("*:443",))
        self.assertNotIn("network-outbound", text)

    def test_bad_endpoints_are_refused(self):
        for endpoint in ("127.0.0.1:80", "api.example.com:443", "localhost", "*:0", "*:70000", "localhost:x", 443):
            with self.subTest(endpoint=endpoint), self.assertRaises(sandbox.SandboxError) as caught:
                sandbox.profile(**ARGS, endpoints=(endpoint,))
            self.assertEqual(str(caught.exception), "endpoint must be localhost:<port> or *:<port>")

    def test_paths_are_resolved_and_quoted(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = os.path.realpath(tmp)
            link = os.path.join(real, "link")
            os.symlink(os.path.join(real, "target"), link)
            odd = os.path.join(real, 'a "b" \\c')
            text = sandbox.profile(**dict(ARGS, secrets_dir=link, root=odd, state_dir=odd))
        self.assertIn(f'(deny file-read* (subpath "{real}/target"))', text)
        self.assertIn('a \\"b\\" \\\\c"', text)


class WebTests(unittest.TestCase):
    def test_web_tools(self):
        self.assertTrue(sandbox.web({"tools": "Read, WebSearch"}))
        self.assertTrue(sandbox.web({"tools": "WebFetch"}))
        self.assertFalse(sandbox.web({"tools": "Read, Edit, Bash"}))
        self.assertFalse(sandbox.web({}))


class WrapApplyTests(unittest.TestCase):
    ARGV = ["/bin/echo", "hi"]

    def test_wrap(self):
        self.assertEqual(sandbox.wrap(self.ARGV, "/p.sb"), ["/usr/bin/sandbox-exec", "-f", "/p.sb", "/bin/echo", "hi"])

    def test_on_wraps(self):
        with mock.patch.object(sandbox, "available", return_value=True):
            self.assertEqual(sandbox.apply({"SANDBOX": "on"}, self.ARGV, "/p.sb"), sandbox.wrap(self.ARGV, "/p.sb"))

    def test_on_is_the_default(self):
        with mock.patch.object(sandbox, "available", return_value=True):
            self.assertEqual(sandbox.apply({}, self.ARGV, "/p.sb"), sandbox.wrap(self.ARGV, "/p.sb"))

    def test_off_leaves_argv(self):
        with mock.patch.object(sandbox, "available", return_value=False):
            self.assertEqual(sandbox.apply({"SANDBOX": "off"}, self.ARGV, "/p.sb"), self.ARGV)

    def test_bad_value(self):
        with self.assertRaises(sandbox.SandboxError) as caught:
            sandbox.apply({"SANDBOX": "yes"}, self.ARGV, "/p.sb")
        self.assertEqual(str(caught.exception), "setting SANDBOX is not on or off")

    def test_on_without_sandbox_exec(self):
        with mock.patch.object(sandbox, "available", return_value=False):
            for settings in ({"SANDBOX": "on"}, {}):
                with self.assertRaises(sandbox.SandboxError) as caught:
                    sandbox.apply(settings, self.ARGV, "/p.sb")
                self.assertEqual(str(caught.exception), "sandbox is on and sandbox-exec is not available")

    def test_available_checks_the_executable(self):
        with mock.patch.object(sandbox, "SANDBOX_EXEC", "/nonexistent/sandbox-exec"):
            self.assertFalse(sandbox.available())
        with mock.patch.object(sandbox, "SANDBOX_EXEC", sys.executable):
            self.assertTrue(sandbox.available())

    def test_write_mode_0600(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "p.sb")
            Path(path).write_text("old", encoding="utf-8")
            os.chmod(path, 0o644)
            sandbox.write(path, "(version 1)\n")
            self.assertEqual(Path(path).read_text(encoding="utf-8"), "(version 1)\n")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)


class ErrorLeakTests(unittest.TestCase):
    SEEDED = "zz-seeded-secret-dir-7f3a"

    def test_secrets_dir_is_not_in_the_message(self):
        secrets_dir = f"/srv/{self.SEEDED}"
        with self.assertRaises(sandbox.SandboxError) as caught:
            sandbox.profile(**dict(ARGS, secrets_dir=secrets_dir), endpoints=(f"{secrets_dir}:1",))
        self.assertNotIn(self.SEEDED, str(caught.exception))
        for value in (secrets_dir, "on"):
            settings = {"SECRETS_DIR": secrets_dir, "SANDBOX": value}
            with mock.patch.object(sandbox, "available", return_value=False), \
                    self.assertRaises(sandbox.SandboxError) as caught:
                sandbox.apply(settings, ["/bin/true"], f"{secrets_dir}/p.sb")
            self.assertNotIn(self.SEEDED, str(caught.exception))


def _probe():
    if not sandbox.available():
        return "sandbox-exec is not available"
    try:
        probe = subprocess.run([sandbox.SANDBOX_EXEC, "-p", "(version 1)(allow default)", "/usr/bin/true"],
                               capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return "the sandbox-exec probe did not run"
    return "" if probe.returncode == 0 else "the sandbox-exec probe failed"


class EnforcementTests(unittest.TestCase):
    """Runs one /bin/sh action inside a generated profile and checks its exit status and its effect."""

    @classmethod
    def setUpClass(cls):
        ENFORCEMENT["total"] = len(unittest.TestLoader().getTestCaseNames(cls))
        ENFORCEMENT["reason"] = _probe()

    def setUp(self):
        if ENFORCEMENT["reason"]:
            self.skipTest(ENFORCEMENT["reason"])
        ENFORCEMENT["ran"] += 1
        base = Path(os.path.realpath(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, base)
        self.root = base / "prod"
        self.state = base / "state"
        self.worktree = self.state / "worktrees" / "w1"
        self.other = self.state / "worktrees" / "w2"
        self.scratch = self.state / "scratch" / "w1"
        self.secrets = base / "keys"
        self.home = base / "home"
        for directory in (self.root, self.worktree / ".factory", self.other, self.scratch, self.secrets,
                          self.state / "runs" / "x", self.home / ".ssh"):
            directory.mkdir(parents=True)
        self.profile_path = base / "profile.sb"

    def run_sh(self, action, web=False, endpoints=()):
        text = sandbox.profile(self.root, self.worktree, self.scratch, self.state, self.secrets, self.home,
                               web=web, endpoints=endpoints)
        sandbox.write(self.profile_path, text)
        argv = sandbox.wrap(["/bin/sh", "-c", action], str(self.profile_path))
        return subprocess.run(argv, cwd=self.worktree, capture_output=True, text=True, timeout=30)

    def assert_read(self, path, allowed):
        path.write_text("marker-content\n", encoding="utf-8")
        result = self.run_sh(f"/bin/cat '{path}'")
        if allowed:
            self.assertEqual((result.returncode, result.stdout), (0, "marker-content\n"), result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("marker-content", result.stdout)
            self.assertIn("Operation not permitted", result.stderr)

    def assert_write(self, path, allowed):
        result = self.run_sh(f"echo written > '{path}'")
        if allowed:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(path.read_text(encoding="utf-8"), "written\n")
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(path.exists())
            self.assertIn("Operation not permitted", result.stderr)

    def test_read_secrets_denied(self):
        self.assert_read(self.secrets / "model.key", allowed=False)

    def test_read_root_allowed(self):
        self.assert_read(self.root / "README.md", allowed=True)

    def test_read_run_prompt_in_state_denied(self):
        self.assert_read(self.state / "runs" / "x" / "prompt.md", allowed=False)

    def test_read_scratch_allowed(self):
        self.assert_read(self.scratch / "notes.md", allowed=True)

    def test_read_home_ssh_denied(self):
        self.assert_read(self.home / ".ssh" / "id_test", allowed=False)

    def test_write_root_denied(self):
        self.assert_write(self.root / "new.md", allowed=False)

    def test_write_worktree_allowed(self):
        self.assert_write(self.worktree / "new.md", allowed=True)

    def test_write_other_worktree_denied(self):
        self.assert_write(self.other / "new.md", allowed=False)

    def test_write_scratch_allowed(self):
        self.assert_write(self.scratch / "new.md", allowed=True)

    def test_write_worktree_factory_denied(self):
        self.assert_write(self.worktree / ".factory" / "x", allowed=False)

    def test_write_worktree_git_denied(self):
        self.assert_write(self.worktree / ".git", allowed=False)

    def test_exec_crontab_denied(self):
        result = self.run_sh("/usr/bin/crontab -l")
        self.assertEqual(result.returncode, 126, result.stderr)
        self.assertIn("Operation not permitted", result.stderr)

    def test_exec_launchctl_denied(self):
        result = self.run_sh("/bin/launchctl list")
        self.assertEqual(result.returncode, 126, result.stderr)
        self.assertIn("Operation not permitted", result.stderr)

    def test_exec_ls_allowed(self):
        (self.worktree / "seen.md").write_text("", encoding="utf-8")
        result = self.run_sh("/bin/ls")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("seen.md", result.stdout)

    def connect(self, allow):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        result = self.run_sh(f"exec 3<>/dev/tcp/127.0.0.1/{port}", endpoints=(f"localhost:{port}",) if allow else ())
        listener.settimeout(2)
        try:
            peer, _ = listener.accept()
            peer.close()
            connected = True
        except socket.timeout:
            connected = False
        return result, connected

    def test_connect_without_endpoint_denied(self):
        result, connected = self.connect(allow=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Operation not permitted", result.stderr)
        self.assertFalse(connected)

    def test_connect_to_endpoint_allowed(self):
        result, connected = self.connect(allow=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(connected)


def tearDownModule():
    ran, total = ENFORCEMENT["ran"], ENFORCEMENT["total"]
    if total:
        note = f" ({ENFORCEMENT['reason']})" if ENFORCEMENT["reason"] else ""
        print(f"\nsandbox enforcement tests: {total - ran} of {total} not run{note}", file=sys.stderr)


if __name__ == "__main__":
    unittest.main()
