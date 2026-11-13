"""Tests for the resources register and `factory doctor`. No network, no model, no tool that may be absent."""

import contextlib
import copy
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kit.lib import cmd_doctor, resources  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "doctor"
FIXTURE_REGISTER = FIXTURES / "register.json"
STUB = {"secrets_dir_setting": "SECRETS_DIR"}
SHIPPED = ROOT / "kit" / "resources.json"
FAKE_HARNESS = ROOT / "tests" / "fixtures" / "harness" / "fake-harness"
VERSION_PATTERN = r"^[0-9]+\.[0-9]+\.[0-9]+ \([^()]+\)"
NEW_KINDS = {"accounts": "accounts", "connect": "connect", "directory": "directory"}


def fixture_register():
    return json.loads(FIXTURE_REGISTER.read_text(encoding="utf-8"))


def resource(*checks, **fields):
    entry = {"id": "x.test", "title": "A test resource", "class": "local", "auth": "none", "holder": "none",
             "rooms": ["all"], "wave": 1, "setup": "agent", "checks": list(checks)}
    entry.update(fields)
    return entry


def shipped(rid):
    """The resource `rid` of the shipped register, and the register."""
    register = json.loads(SHIPPED.read_text(encoding="utf-8"))
    return next(r for r in register["resources"] if r["id"] == rid), register


def free_port():
    """A loopback port nothing listens on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class TempDir(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)


class Production(TempDir):
    """A copy of the clean fixture with machine-specific settings written to production.local.env."""

    def setUp(self):
        super().setUp()
        self.root = self.tmp / "production"
        shutil.copytree(FIXTURES / "clean", self.root)
        secrets = self.root / "secrets"
        for path in (secrets / "key.alpha", secrets / "key.pool" / "one", secrets / "key.pool" / "two"):
            path.chmod(0o600)
        self.local = {"PY": sys.executable, "SECRETS_DIR": str(secrets), "DATA_DIR": str(self.root / "data")}
        self.write_local()

    def write_local(self, **changes):
        self.local.update(changes)
        lines = [f"{key}={value}" for key, value in sorted(self.local.items())]
        (self.root / "production.local.env").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def doctor(self, *args, register=None):
        path = FIXTURE_REGISTER
        if register is not None:
            path = self.tmp / "register.json"
            path.write_text(json.dumps(register), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cmd_doctor, "REGISTER", str(path)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_doctor.main(["--root", str(self.root), *args])
        return code, out.getvalue(), err.getvalue()


class ShippedRegister(unittest.TestCase):
    def test_shipped_register_has_no_problems(self):
        register, problems = resources.load(ROOT / "kit" / "resources.json")
        self.assertEqual(problems, [])
        self.assertTrue(register["resources"])

    def test_fixture_register_has_no_problems(self):
        self.assertEqual(resources.load(FIXTURE_REGISTER)[1], [])

    def test_script_reads_the_register_of_its_own_kit(self):
        # --wave 0 needs nothing, so no check runs and no tool or network is touched.
        done = subprocess.run([sys.executable, str(ROOT / "kit" / "lib" / "cmd_doctor.py"), "--root",
                               str(FIXTURES / "clean"), "--wave", "0"], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        shipped = json.loads((ROOT / "kit" / "resources.json").read_text(encoding="utf-8"))
        self.assertEqual(done.stdout.splitlines()[-1], f"doctor: 0 ok, 0 missing, 0 absent, "
                                                       f"{len(shipped['resources'])} skipped")


class Validation(unittest.TestCase):
    """Each test seeds exactly one defect into the valid fixture register."""

    def problems(self, mutate):
        register = fixture_register()
        mutate(register)
        return resources.validate(register)

    def assertOne(self, mutate, expected):
        self.assertEqual(self.problems(mutate), [expected])

    def at(self, register, rid):
        return next(r for r in register["resources"] if r["id"] == rid)

    def test_version_must_be_1(self):
        self.assertOne(lambda r: r.update(version=2), "register: version is not 1")

    def test_ids_must_be_unique(self):
        def dup(r):
            self.at(r, "lib.json")["id"] = "tool.runner"
        self.assertOne(dup, "tool.runner: id is not unique")

    def test_every_required_key_must_be_present(self):
        for key in resources.REQUIRED:
            with self.subTest(key=key):
                expected = "resources[1]: missing id" if key == "id" else f"lib.json: missing {key}"
                self.assertOne(lambda r: self.at(r, "lib.json").pop(key), expected)

    def test_auth_must_be_known(self):
        self.assertOne(lambda r: self.at(r, "lib.json").update(auth="token"),
                       "lib.json: auth is not one of none, login, key, grant")

    def test_holder_must_be_known(self):
        self.assertOne(lambda r: self.at(r, "lib.json").update(holder="worker"),
                       "lib.json: holder is not one of none, launcher, gateway, run")

    def test_setup_must_be_known(self):
        self.assertOne(lambda r: self.at(r, "lib.json").update(setup="anyone"),
                       "lib.json: setup is not one of owner, agent")

    def test_rooms_must_be_non_empty(self):
        self.assertOne(lambda r: self.at(r, "lib.json").update(rooms=[]), "lib.json: rooms is not a non-empty list")

    def test_rooms_must_be_known(self):
        self.assertOne(lambda r: self.at(r, "lib.json").update(rooms=["story", "kitchen"]),
                       "lib.json: rooms has an unknown room: kitchen")

    def test_wave_must_be_an_integer_from_1_to_7(self):
        for wave in (0, 8, "3", 3.0, True):
            with self.subTest(wave=wave):
                self.assertOne(lambda r: self.at(r, "lib.json").update(wave=wave),
                               "lib.json: wave is not an integer from 1 to 7")

    def test_checks_must_be_non_empty(self):
        self.assertOne(lambda r: self.at(r, "lib.json").update(checks=[]), "lib.json: checks is not a non-empty list")

    def test_check_kind_must_be_a_key_of_check_kinds(self):
        self.assertOne(lambda r: self.at(r, "lib.json").update(checks=[{"kind": "ping", "name": "json"}]),
                       "lib.json: check 1: kind is not a key of check_kinds")

    def test_check_kind_must_be_declared_by_the_register(self):
        self.assertOne(lambda r: r["check_kinds"].pop("module"),
                       "lib.json: check 1: kind is not a key of check_kinds")

    def test_command_needs_argv(self):
        self.assertOne(lambda r: self.at(r, "tool.runner")["checks"][0].pop("argv"),
                       "tool.runner: check 1: command check has no argv")

    def test_command_argv_must_be_a_list_of_strings(self):
        for argv in ("python3 --version", [], ["{PY}", 3]):
            with self.subTest(argv=argv):
                self.assertOne(lambda r: self.at(r, "tool.runner")["checks"][0].update(argv=argv),
                               "tool.runner: check 1: command check argv is not a non-empty list of strings")

    def test_command_match_all_must_be_a_list_of_strings(self):
        self.assertOne(lambda r: self.at(r, "tool.runner")["checks"][0].update(match_all="runner"),
                       "tool.runner: check 1: command check match_all is not a list of strings")

    def test_command_match_all_patterns_must_compile(self):
        self.assertOne(lambda r: self.at(r, "tool.runner")["checks"][0].update(match_all=["ok", "([0-9]"]),
                       "tool.runner: check 1: command check pattern does not compile: ([0-9]")

    def test_module_needs_name(self):
        self.assertOne(lambda r: self.at(r, "lib.json")["checks"][0].pop("name"),
                       "lib.json: check 1: module check has no name")

    def test_secret_needs_name(self):
        self.assertOne(lambda r: self.at(r, "key.alpha")["checks"][0].pop("name"),
                       "key.alpha: check 1: secret check has no name")

    def test_secret_min_must_be_an_integer_of_at_least_1(self):
        for least in (0, -1, "2", 1.5, True):
            with self.subTest(min=least):
                self.assertOne(lambda r: self.at(r, "key.pool")["checks"][0].update(min=least),
                               "key.pool: check 1: secret check min is not an integer of at least 1")

    def test_setting_needs_name(self):
        self.assertOne(lambda r: self.at(r, "mode.pick")["checks"][0].pop("name"),
                       "mode.pick: check 1: setting check has no name")

    def test_setting_one_of_must_be_a_list(self):
        self.assertOne(lambda r: self.at(r, "mode.pick")["checks"][0].update(one_of="fast"),
                       "mode.pick: check 1: setting check one_of is not a list of strings")

    def test_path_needs_setting(self):
        self.assertOne(lambda r: self.at(r, "data.dir")["checks"][0].pop("setting"),
                       "data.dir: check 1: path check has no setting")

    def test_unknown_key_on_a_resource(self):
        self.assertOne(lambda r: self.at(r, "lib.json").update(owner="someone"), "lib.json: unknown key owner")

    def test_unknown_key_on_a_check(self):
        self.assertOne(lambda r: self.at(r, "lib.json")["checks"][0].update(version="1"),
                       "lib.json: check 1: module check has unknown key version")

    def test_problems_name_the_resource_and_are_sorted(self):
        def two(r):
            self.at(r, "mode.pick").update(auth="token")
            self.at(r, "data.dir").update(setup="anyone")
        self.assertEqual(self.problems(two), ["data.dir: setup is not one of owner, agent",
                                              "mode.pick: auth is not one of none, login, key, grant"])

    def test_unreadable_or_malformed_register_is_a_problem(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "resources.json"
            bad.write_text("{not json", encoding="utf-8")
            self.assertEqual(resources.load(bad), (None, ["register: not valid JSON"]))
            self.assertEqual(resources.load(Path(tmp) / "absent.json"), (None, ["register: cannot be read"]))


class Settings(TempDir):
    def test_files_in_order_later_wins_comments_quotes_no_expansion(self):
        (self.tmp / "production.env").write_text(
            "# a comment\n\nA=one\nB='two words'\nC=\"three\"\nD=$HOME/x\nE='mismatched\"\nF=a=b\n", encoding="utf-8")
        (self.tmp / "production.local.env").write_text("A=local\n", encoding="utf-8")
        self.assertEqual(resources.settings(self.tmp, fixture_register()),
                         {"A": "local", "B": "two words", "C": "three", "D": "$HOME/x",
                          "E": "'mismatched\"", "F": "a=b"})

    def test_missing_files_are_not_an_error(self):
        self.assertEqual(resources.settings(self.tmp, fixture_register()), {})

    def test_defaults_to_the_shipped_register(self):
        (self.tmp / "production.local.env").write_text("A=1\n", encoding="utf-8")
        self.assertEqual(resources.settings(self.tmp), {"A": "1"})


class CheckKinds(TempDir):
    def run_check(self, check, values):
        return resources.check(resource(check), values, STUB)

    def assertPasses(self, check, values):
        self.assertEqual(self.run_check(check, values), (True, []))

    def assertFails(self, check, values, reasons):
        self.assertEqual(self.run_check(check, values), (False, reasons))

    # command

    def test_command_passes(self):
        check = {"kind": "command", "argv": ["{PY}", "-c", "print('tool 4.5')"], "match_all": ["tool", r"\d+\.\d+"]}
        self.assertPasses(check, {"PY": sys.executable})

    def test_command_empty_setting(self):
        check = {"kind": "command", "argv": ["{PY}", "--version"]}
        self.assertFails(check, {"PY": ""}, ["setting PY is empty"])
        self.assertFails(check, {}, ["setting PY is empty"])

    def test_command_not_found(self):
        check = {"kind": "command", "argv": ["{PY}", "--version"]}
        self.assertFails(check, {"PY": str(self.tmp / "no-such-program")}, ["command not found: {PY} --version"])

    def test_command_that_cannot_start_names_argv_not_value(self):
        check = {"kind": "command", "argv": ["{PY}", "-c", "{CODE}"]}
        self.assertFails(check, {"PY": sys.executable, "CODE": "secret\0value"},
                         ["command could not start: {PY} -c {CODE}"])

    def test_command_non_zero_exit(self):
        check = {"kind": "command", "argv": ["{PY}", "-c", "raise SystemExit(3)"]}
        self.assertFails(check, {"PY": sys.executable}, ["command failed: {PY} -c raise SystemExit(3)"])

    def test_command_pattern_missing(self):
        check = {"kind": "command", "argv": ["{PY}", "-c", "print('tool')"], "match_all": ["tool", "libvmaf"]}
        self.assertFails(check, {"PY": sys.executable}, ["pattern not found: libvmaf"])

    def test_command_patterns_search_stderr_too(self):
        check = {"kind": "command", "argv": ["{PY}", "-c", "import sys; sys.stderr.write('filters: ssim')"],
                 "match_all": ["ssim"]}
        self.assertPasses(check, {"PY": sys.executable})

    def test_command_timeout(self):
        check = {"kind": "command", "argv": ["{PY}", "-c", "import time; time.sleep(30)"]}
        with mock.patch.object(resources, "TIMEOUT", 0.5):
            self.assertFails(check, {"PY": sys.executable},
                             ["command timed out: {PY} -c import time; time.sleep(30)"])

    def test_command_stdin_is_closed(self):
        check = {"kind": "command", "argv": ["{PY}", "-c", "import sys; print(repr(sys.stdin.read()))"],
                 "match_all": ["^''$"]}
        self.assertPasses(check, {"PY": sys.executable})

    # module

    def test_module_passes(self):
        self.assertPasses({"kind": "module", "name": "json"}, {})

    def test_module_not_found(self):
        self.assertFails({"kind": "module", "name": "invented_module_zz"}, {},
                         ["module not found: invented_module_zz"])
        self.assertFails({"kind": "module", "name": "invented_pkg_zz.sub"}, {},
                         ["module not found: invented_pkg_zz.sub"])

    # secret

    def secret(self, relative, content="invented\n", mode=0o600):
        path = self.tmp / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        path.chmod(mode)
        return path

    def test_secret_file_passes(self):
        self.secret("key.one")
        self.assertPasses({"kind": "secret", "name": "key.one"}, {"SECRETS_DIR": str(self.tmp)})

    def test_secret_directory_passes_with_min_files(self):
        self.secret("pool/a")
        self.secret("pool/b")
        self.assertPasses({"kind": "secret", "name": "pool", "min": 2}, {"SECRETS_DIR": str(self.tmp)})
        self.assertPasses({"kind": "secret", "name": "pool"}, {"SECRETS_DIR": str(self.tmp)})

    def test_secret_missing(self):
        self.assertFails({"kind": "secret", "name": "key.one"}, {"SECRETS_DIR": str(self.tmp)},
                         ["secret key.one: missing"])

    def test_secret_empty(self):
        self.secret("key.one", content="")
        self.assertFails({"kind": "secret", "name": "key.one"}, {"SECRETS_DIR": str(self.tmp)},
                         ["secret key.one: empty"])

    def test_secret_mode_0644(self):
        self.secret("key.one", mode=0o644)
        self.assertFails({"kind": "secret", "name": "key.one"}, {"SECRETS_DIR": str(self.tmp)},
                         ["secret key.one: readable by others"])

    def test_secret_mode_group_write_only(self):
        self.secret("key.one", mode=0o620)
        self.assertFails({"kind": "secret", "name": "key.one"}, {"SECRETS_DIR": str(self.tmp)},
                         ["secret key.one: open to group or others"])

    def test_secret_directory_with_too_few_files(self):
        self.secret("pool/a")
        self.secret("pool/b", mode=0o644)
        self.secret("pool/c", content="")
        self.assertFails({"kind": "secret", "name": "pool", "min": 2}, {"SECRETS_DIR": str(self.tmp)},
                         ["secret pool: fewer than 2 usable files"])

    def test_secret_empty_directory(self):
        (self.tmp / "pool").mkdir()
        self.assertFails({"kind": "secret", "name": "pool"}, {"SECRETS_DIR": str(self.tmp)},
                         ["secret pool: fewer than 1 usable files"])

    def test_secret_dir_setting_empty(self):
        self.assertFails({"kind": "secret", "name": "key.one"}, {}, ["setting SECRETS_DIR is empty"])

    def test_secret_dir_expands_home(self):
        self.secret("keys/key.one")
        with mock.patch.dict(os.environ, {"HOME": str(self.tmp)}):
            self.assertPasses({"kind": "secret", "name": "key.one"}, {"SECRETS_DIR": "~/keys"})

    # setting

    def test_setting_passes(self):
        self.assertPasses({"kind": "setting", "name": "MODE"}, {"MODE": "anything"})
        self.assertPasses({"kind": "setting", "name": "MODE", "one_of": ["fast", "slow"]}, {"MODE": "slow"})

    def test_setting_empty(self):
        self.assertFails({"kind": "setting", "name": "MODE"}, {"MODE": ""}, ["setting MODE is empty"])
        self.assertFails({"kind": "setting", "name": "MODE"}, {}, ["setting MODE is empty"])

    def test_setting_not_one_of(self):
        self.assertFails({"kind": "setting", "name": "MODE", "one_of": ["fast", "slow"]}, {"MODE": "medium"},
                         ["setting MODE is not one of: fast, slow"])

    # path

    def test_path_passes(self):
        (self.tmp / "editor").write_text("x", encoding="utf-8")
        self.assertPasses({"kind": "path", "setting": "EDITOR_BIN"}, {"EDITOR_BIN": str(self.tmp / "editor")})
        self.assertPasses({"kind": "path", "setting": "EDITOR_BIN"}, {"EDITOR_BIN": str(self.tmp)})

    def test_path_expands_home(self):
        (self.tmp / "editor").write_text("x", encoding="utf-8")
        with mock.patch.dict(os.environ, {"HOME": str(self.tmp)}):
            self.assertPasses({"kind": "path", "setting": "EDITOR_BIN"}, {"EDITOR_BIN": "~/editor"})

    def test_path_empty(self):
        self.assertFails({"kind": "path", "setting": "EDITOR_BIN"}, {}, ["setting EDITOR_BIN is empty"])

    def test_path_does_not_exist(self):
        self.assertFails({"kind": "path", "setting": "EDITOR_BIN"}, {"EDITOR_BIN": str(self.tmp / "nothing")},
                         ["setting EDITOR_BIN names nothing that exists"])

    def test_every_failed_check_gives_a_reason(self):
        both = resource({"kind": "setting", "name": "A"}, {"kind": "setting", "name": "B"})
        self.assertEqual(resources.check(both, {}, STUB), (False, ["setting A is empty", "setting B is empty"]))


class Doctor(Production):
    def test_clean_production_is_all_ok(self):
        code, out, err = self.doctor()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, "".join(f"ok\t{rid}\t{title}\n" for rid, title in [
            ("data.dir", "A directory named by a setting"),
            ("extra.optional", "An optional extra"),
            ("key.alpha", "One key file"),
            ("key.pool", "A directory of tokens"),
            ("lib.json", "A module that imports"),
            ("mode.pick", "A setting from a fixed list"),
            ("tool.runner", "A command that prints its version"),
        ]) + "doctor: 7 ok, 0 missing, 0 absent, 0 skipped\n")

    def test_output_is_deterministic(self):
        self.write_local(MODE="none")
        self.assertEqual(self.doctor(), self.doctor())
        self.assertEqual(self.doctor("--json"), self.doctor("--json"))

    def test_json_output(self):
        self.write_local(MODE="medium")
        code, out, err = self.doctor("--json")
        self.assertEqual(code, 1)
        report = json.loads(out)
        self.assertEqual(out, json.dumps(report, indent=2, sort_keys=True) + "\n")
        self.assertEqual(report["counts"], {"ok": 6, "missing": 1, "absent": 0, "skipped": 0})
        self.assertEqual([r["id"] for r in report["resources"]], sorted(r["id"] for r in report["resources"]))
        self.assertIn({"id": "mode.pick", "state": "missing", "reasons": ["setting MODE is not one of: fast, slow"]},
                      report["resources"])

    def test_optional_resource_failing_is_absent_and_exit_0(self):
        self.write_local(EXTRA="")
        code, out, _ = self.doctor()
        self.assertEqual(code, 0)
        self.assertIn("absent\textra.optional\tAn optional extra\n  setting EXTRA is empty\n", out)
        self.assertTrue(out.endswith("doctor: 6 ok, 0 missing, 1 absent, 0 skipped\n"))

    def test_required_resource_failing_is_missing_and_exit_1(self):
        (self.root / "secrets" / "key.alpha").chmod(0o644)
        code, out, _ = self.doctor()
        self.assertEqual(code, 1)
        self.assertIn("missing\tkey.alpha\tOne key file\n  secret key.alpha: readable by others\n", out)
        self.assertTrue(out.endswith("doctor: 6 ok, 1 missing, 0 absent, 0 skipped\n"))

    def marker_register(self):
        """The fixture register with a production-room wave 4 command that leaves a marker file when it runs."""
        register = fixture_register()
        marker = self.tmp / "ran"
        register["resources"].append(resource(
            {"kind": "command", "argv": ["{PY}", "-c", f"open({str(marker)!r}, 'w').close()"]},
            id="marker.tool", rooms=["production"], wave=4))
        return register, marker

    def test_rooms_option_skips_without_running_checks(self):
        register, marker = self.marker_register()
        code, out, _ = self.doctor("--rooms", "story,post", register=register)
        self.assertFalse(marker.exists())
        self.assertIn("skipped\tmarker.tool\t", out)
        self.assertIn("skipped\tkey.pool\t", out)
        self.assertIn("ok\tkey.alpha\t", out)
        self.assertIn("ok\ttool.runner\t", out)
        self.assertTrue(out.endswith("doctor: 4 ok, 0 missing, 0 absent, 4 skipped\n"))
        self.assertEqual(code, 0)
        self.doctor("--rooms", "production", register=register)
        self.assertTrue(marker.exists())

    def test_rooms_setting_is_used_without_the_option(self):
        register, marker = self.marker_register()
        self.write_local(ROOMS="distribution, lab")
        _, out, _ = self.doctor(register=register)
        self.assertFalse(marker.exists())
        self.assertTrue(out.endswith("doctor: 3 ok, 0 missing, 0 absent, 5 skipped\n"))

    def test_wave_option_skips_without_running_checks(self):
        register, marker = self.marker_register()
        code, out, _ = self.doctor("--wave", "3", register=register)
        self.assertFalse(marker.exists())
        self.assertIn("skipped\tmarker.tool\t", out)
        self.assertIn("skipped\tmode.pick\t", out)
        self.assertIn("ok\tkey.pool\t", out)
        self.assertTrue(out.endswith("doctor: 4 ok, 0 missing, 0 absent, 4 skipped\n"))
        self.assertEqual(code, 0)
        self.doctor("--wave", "4", register=register)
        self.assertTrue(marker.exists())

    def test_invalid_register_exits_2(self):
        register = fixture_register()
        register["version"] = 3
        code, out, err = self.doctor(register=register)
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(err, "register: version is not 1\ndoctor: invalid register, 1 problems\n")


class NoValues(Production):
    """Run the real script, from a kit holding the fixture register, and look for seeded values in its output."""

    SETTING = "zq-setting-value-4417"
    SECRET = "zq-secret-content-5528"
    OUTPUT = "zq-command-output-6639"

    def test_no_value_reaches_stdout_or_stderr(self):
        kit = self.tmp / "copy" / "kit"
        (kit / "lib").mkdir(parents=True)
        for name in ("__init__.py", "lib/__init__.py", "lib/resources.py", "lib/grants.py", "lib/cmd_doctor.py"):
            shutil.copy(ROOT / "kit" / name, kit / name)
        register = fixture_register()
        output = "'-'.join(['zq', 'command', 'output', '6639'])"
        register["resources"].append(resource(
            {"kind": "command", "argv": ["{PY}", "-c", f"print({output})"], "match_all": ["will-not-match"]},
            {"kind": "command", "argv": ["{PY}", "-c", f"import sys; sys.stderr.write({output}); sys.exit(1)"]},
            {"kind": "command", "argv": ["{PY}", "-c", f"print({output})"]},
            id="leak.command"))
        register["resources"].append(resource({"kind": "secret", "name": "leak.secret"}, id="leak.secret"))
        register["resources"].append(resource({"kind": "secret", "name": "leak.secret.ok"}, id="leak.secret.ok"))
        register["resources"].append(resource({"kind": "path", "setting": "LEAK_PATH"}, id="leak.path"))
        (kit / "resources.json").write_text(json.dumps(register), encoding="utf-8")
        for name, mode in (("leak.secret", 0o644), ("leak.secret.ok", 0o600)):
            (self.root / "secrets" / name).write_text(self.SECRET, encoding="utf-8")
            (self.root / "secrets" / name).chmod(mode)
        self.write_local(MODE=self.SETTING, EXTRA=self.SETTING, LEAK_PATH=str(self.tmp / self.SETTING))

        outputs = {}
        for extra in ([], ["--json"]):
            done = subprocess.run([sys.executable, str(kit / "lib" / "cmd_doctor.py"), "--root", str(self.root),
                                   *extra], capture_output=True, text=True, cwd=self.tmp)
            self.assertEqual(done.returncode, 1, done.stderr)
            self.assertNotIn(str(self.root), done.stdout + done.stderr)
            for value in (self.SETTING, self.SECRET, self.OUTPUT):
                self.assertNotIn(value, done.stdout)
                self.assertNotIn(value, done.stderr)
            outputs[tuple(extra)] = done.stdout
        text = outputs[()]
        # The seeded resources really were checked and reported.
        for line in ("missing\tleak.command\t", "  pattern not found: will-not-match",
                     "  command failed: {PY} -c import sys;", "missing\tleak.secret\t",
                     "  secret leak.secret: readable by others", "ok\tleak.secret.ok\t",
                     "missing\tleak.path\t", "  setting LEAK_PATH names nothing that exists",
                     "  setting MODE is not one of: fast, slow"):
            self.assertIn(line, text)

    ACCOUNT = "zq-account-name-7731"
    URL_PATH = "zq-url-path-8842"
    DIR_NAME = "zq-dir-name-9953"

    def test_no_value_from_accounts_connect_or_directory(self):
        kit = self.tmp / "copy" / "kit"
        (kit / "lib").mkdir(parents=True)
        for name in ("__init__.py", "lib/__init__.py", "lib/resources.py", "lib/grants.py", "lib/cmd_doctor.py"):
            shutil.copy(ROOT / "kit" / name, kit / name)
        register = fixture_register()
        register["check_kinds"].update(NEW_KINDS)
        register["resources"].append(resource({"kind": "accounts", "settings": ["LEAK_ACCOUNTS"], "secret": "leak.pool"},
                                              id="leak.accounts"))
        register["resources"].append(resource({"kind": "connect", "setting": "LEAK_URL"}, id="leak.connect"))
        register["resources"].append(resource({"kind": "directory", "setting": "LEAK_DIR"}, id="leak.directory"))
        (kit / "resources.json").write_text(json.dumps(register), encoding="utf-8")
        (self.tmp / self.DIR_NAME).write_text("a file, not a directory", encoding="utf-8")
        url = f"http://127.0.0.1:{free_port()}/{self.URL_PATH}"
        self.write_local(LEAK_ACCOUNTS=self.ACCOUNT, LEAK_URL=url, LEAK_DIR=str(self.tmp / self.DIR_NAME))

        for extra in ([], ["--json"]):
            done = subprocess.run([sys.executable, str(kit / "lib" / "cmd_doctor.py"), "--root", str(self.root),
                                   *extra], capture_output=True, text=True, cwd=self.tmp)
            self.assertEqual(done.returncode, 1, done.stderr)
            for value in (self.ACCOUNT, url, self.URL_PATH, self.DIR_NAME, str(self.tmp)):
                self.assertNotIn(value, done.stdout)
                self.assertNotIn(value, done.stderr)
            if not extra:
                for line in ("missing\tleak.accounts\t", "  account 1 of LEAK_ACCOUNTS: missing",
                             "missing\tleak.connect\t", "  setting LEAK_URL: nothing accepts a connection there",
                             "missing\tleak.directory\t", "  setting LEAK_DIR is not a directory"):
                    self.assertIn(line, done.stdout)


class Parser(TempDir):
    """`resources.settings`: inline comments, and `~` expanded for the register's path settings only."""

    def read(self, text, register, local=None):
        (self.tmp / "production.env").write_text(text, encoding="utf-8")
        if local is not None:
            (self.tmp / "production.local.env").write_text(local, encoding="utf-8")
        with mock.patch.dict(os.environ, {"HOME": "/home/demo"}):
            return resources.settings(self.tmp, register)

    def path_register(self):
        register = fixture_register()
        register["path_settings"] = ["HARNESS", "SECRETS_DIR", "STATE_DIR"]
        return register

    def test_each_line_gives_its_value(self):
        rows = [
            ("A=one # note", "A", "one"),
            ("B=one#two", "B", "one#two"),
            ("C='two words' # note", "C", "two words"),
            ('D="x #y"', "D", '"x'),
            ("E= # only", "E", ""),
            ("E=#x", "E", "#x"),
            ("F=a\t# tab", "F", "a"),
            ("K = v ", "K", "v"),
            ("STATE_DIR=~/s", "STATE_DIR", "/home/demo/s"),
            ("SECRETS_DIR=~", "SECRETS_DIR", "/home/demo"),
            ("HARNESS='~/bin/h' # pinned", "HARNESS", "/home/demo/bin/h"),
            ("STATE_DIR=~user/x", "STATE_DIR", "~user/x"),
            ("OTHER=~/keep", "OTHER", "~/keep"),
            ("G=x~/y", "G", "x~/y"),
        ]
        for line, key, value in rows:
            with self.subTest(line=line):
                self.assertEqual(self.read(line + "\n", self.path_register()), {key: value})

    def test_both_files_are_expanded_and_the_later_wins(self):
        values = self.read("A=one # note\nSTATE_DIR=~/s\nHARNESS='~/bin/h' # pinned\nOTHER=~/keep\nG=x~/y\n",
                           self.path_register(), local="STATE_DIR=~user/x\nSECRETS_DIR=~ # home\n")
        self.assertEqual(values, {"A": "one", "STATE_DIR": "~user/x", "HARNESS": "/home/demo/bin/h",
                                  "OTHER": "~/keep", "G": "x~/y", "SECRETS_DIR": "/home/demo"})

    def test_shipped_register_expands_every_path_setting(self):
        values = self.read("UNREAL_EDITOR=~/ue # note\nTRANSCRIBE_MODEL=\"~/m\"\nSTATE_DIR=~/s\nOTHER=~/keep\n", None)
        self.assertEqual(values, {"UNREAL_EDITOR": "/home/demo/ue", "TRANSCRIBE_MODEL": "/home/demo/m",
                                  "STATE_DIR": "/home/demo/s", "OTHER": "~/keep"})

    def test_register_without_path_settings_expands_nothing(self):
        self.assertEqual(self.read("STATE_DIR=~/s\nHARNESS=~/bin/h\n", fixture_register()),
                         {"STATE_DIR": "~/s", "HARNESS": "~/bin/h"})

    def test_expand(self):
        with mock.patch.dict(os.environ, {"HOME": "/home/demo"}):
            for value, expected in (("~", "/home/demo"), ("~/a", "/home/demo/a"), ("~user/x", "~user/x"),
                                    ("x~/y", "x~/y"), ("/abs", "/abs"), ("", "")):
                with self.subTest(value=value):
                    self.assertEqual(resources.expand(value), expected)


class ShippedHarness(TempDir):
    """The shipped `harness` resource: the version, the state directory and the sandbox setting."""

    def setUp(self):
        super().setUp()
        self.resource, self.register = shipped("harness")
        self.root = self.tmp / "production"
        self.root.mkdir()

    def run_harness(self, *root, **changes):
        values = {"HARNESS": str(FAKE_HARNESS), "STATE_DIR": str(self.tmp / "state"), "SANDBOX": "on"}
        values.update(changes)
        values = {key: value for key, value in values.items() if value is not None}
        return resources.check(self.resource, values, self.register, *root)

    def test_fake_harness_passes(self):
        self.assertEqual(self.run_harness(), (True, []))

    def harness_at_home(self, harness):
        """The fake harness copied to `<home>/bin/h`, read through the parser as `HARNESS=<harness>`."""
        home = self.tmp / "home"
        (home / "bin").mkdir(parents=True)
        shutil.copy(FAKE_HARNESS, home / "bin" / "h")
        (self.root / "production.local.env").write_text(
            f"HARNESS={harness.format(home=home)}\nSTATE_DIR={self.tmp / 'state'}\nSANDBOX=on\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            values = resources.settings(self.root)
            return resources.check(self.resource, values, self.register)

    def test_home_in_harness_is_expanded_by_the_parser(self):
        self.assertEqual(self.harness_at_home("~/bin/h"), (True, []))

    def test_absolute_harness_passes(self):
        self.assertEqual(self.harness_at_home("{home}/bin/h"), (True, []))

    def test_a_program_that_is_not_a_harness_fails(self):
        self.assertEqual(self.run_harness(HARNESS=sys.executable), (False, [f"pattern not found: {VERSION_PATTERN}"]))

    def test_version_pattern(self):
        self.assertEqual(self.resource["checks"][0]["match_all"], [VERSION_PATTERN])
        pattern = self.resource["checks"][0]["match_all"][0]
        for text in ("2.1.290 (hbar)", "0.0.0 (fake)"):
            with self.subTest(text=text):
                self.assertTrue(re.search(pattern, text))
        for text in ("git version 2.39.5 (Apple Git-154)", "Python 3.13.1", "v2.1.290 (x)", "2.1 (x)"):
            with self.subTest(text=text):
                self.assertIsNone(re.search(pattern, text))

    def test_state_dir_is_checked(self):
        self.assertEqual(self.run_harness(STATE_DIR=""), (False, ["setting STATE_DIR is empty"]))
        self.assertEqual(self.run_harness(self.root, STATE_DIR=str(self.root / "state")),
                         (False, ["setting STATE_DIR is inside the production"]))

    def test_sandbox_setting(self):
        self.assertEqual(self.run_harness(SANDBOX="on"), (True, []))
        self.assertEqual(self.run_harness(SANDBOX="off"), (True, []))
        self.assertEqual(self.run_harness(SANDBOX="maybe"), (False, ["setting SANDBOX is not one of: on, off"]))
        self.assertEqual(self.run_harness(SANDBOX=None), (False, ["setting SANDBOX is empty"]))


class DirectoryCheck(TempDir):
    CHECK = {"kind": "directory", "setting": "STATE_DIR"}

    def setUp(self):
        super().setUp()
        self.root = self.tmp / "production"
        self.root.mkdir()

    def run_dir(self, value, root=True):
        values = {} if value is None else {"STATE_DIR": str(value)}
        return resources.check(resource(self.CHECK), values, STUB, self.root if root else None)

    def locked(self, name, mode):
        path = self.tmp / name
        path.mkdir()
        path.chmod(mode)
        self.addCleanup(path.chmod, 0o700)
        return path

    def test_existing_writable_directory_passes(self):
        (self.tmp / "state").mkdir()
        self.assertEqual(self.run_dir(self.tmp / "state"), (True, []))

    def test_missing_directory_under_a_writable_one_passes_and_is_not_created(self):
        self.assertEqual(self.run_dir(self.tmp / "new" / "state"), (True, []))
        self.assertFalse((self.tmp / "new").exists())

    def test_each_defect(self):
        (self.tmp / "file").write_text("x", encoding="utf-8")
        rows = [
            (None, "setting STATE_DIR is empty"),
            ("", "setting STATE_DIR is empty"),
            ("state", "setting STATE_DIR is not an absolute path"),
            (self.root, "setting STATE_DIR is inside the production"),
            (self.root / "state", "setting STATE_DIR is inside the production"),
            (self.tmp / "file", "setting STATE_DIR is not a directory"),
            (self.tmp / "file" / "state", "setting STATE_DIR is not a directory"),
            (self.locked("readonly", 0o500), "setting STATE_DIR is not writable"),
            (self.locked("sealed", 0o555) / "state", "setting STATE_DIR cannot be created"),
        ]
        for value, reason in rows:
            with self.subTest(value=value):
                self.assertEqual(self.run_dir(value), (False, [reason]))
        self.assertFalse((self.root / "state").exists())

    def test_without_a_root_the_production_rule_is_not_checked(self):
        self.assertEqual(self.run_dir(self.root / "state", root=False), (True, []))

    def test_home_is_expanded(self):
        with mock.patch.dict(os.environ, {"HOME": str(self.tmp)}):
            self.assertEqual(self.run_dir("~/state"), (True, []))
            self.assertEqual(self.run_dir("~/production/state"), (False, ["setting STATE_DIR is inside the production"]))


class AccountsCheck(TempDir):
    CHECK = {"kind": "accounts", "settings": ["ACCOUNTS_MAKER", "ACCOUNTS_CHECKER"], "secret": "inference.primary"}

    def setUp(self):
        super().setUp()
        self.pool = self.tmp / "inference.primary"
        self.pool.mkdir()
        self.token("alpha")
        self.token("beta")

    def token(self, name, content="invented\n", mode=0o600):
        path = self.pool / name
        path.write_text(content, encoding="utf-8")
        path.chmod(mode)

    def run_accounts(self, **changes):
        values = {"SECRETS_DIR": str(self.tmp), "ACCOUNTS_MAKER": "alpha,beta", "ACCOUNTS_CHECKER": "beta, alpha ,"}
        values.update(changes)
        values = {key: value for key, value in values.items() if value is not None}
        return resources.check(resource(self.CHECK), values, STUB)

    def test_good_lists_pass(self):
        self.assertEqual(self.run_accounts(), (True, []))

    def test_account_without_a_file(self):
        self.assertEqual(self.run_accounts(ACCOUNTS_MAKER="alpha,gamma"),
                         (False, ["account 2 of ACCOUNTS_MAKER: missing"]))

    def test_empty_lists(self):
        self.assertEqual(self.run_accounts(ACCOUNTS_MAKER="", ACCOUNTS_CHECKER=" , "),
                         (False, ["setting ACCOUNTS_MAKER is empty", "setting ACCOUNTS_CHECKER is empty"]))
        self.assertEqual(self.run_accounts(ACCOUNTS_MAKER=None), (False, ["setting ACCOUNTS_MAKER is empty"]))

    def test_unusable_files_and_names(self):
        self.token("open", mode=0o644)
        self.token("void", content="")
        (self.pool / "dir").mkdir()
        self.assertEqual(self.run_accounts(ACCOUNTS_MAKER="open,void,dir,../x,alpha"), (False, [
            "account 1 of ACCOUNTS_MAKER: readable by others", "account 2 of ACCOUNTS_MAKER: empty",
            "account 3 of ACCOUNTS_MAKER: not a file", "account 4 of ACCOUNTS_MAKER: not a file name"]))

    def test_no_secrets_dir(self):
        self.assertEqual(self.run_accounts(SECRETS_DIR=None), (False, ["setting SECRETS_DIR is empty"]))

    def test_shipped_resource_reads_the_account_lists(self):
        primary, register = shipped("inference.primary")
        values = {"SECRETS_DIR": str(self.tmp), "ACCOUNTS_MAKER": "gamma", "ACCOUNTS_CHECKER": "alpha"}
        self.assertEqual(resources.check(primary, values, register), (False, ["account 1 of ACCOUNTS_MAKER: missing"]))


class ConnectCheck(TempDir):
    CHECK = {"kind": "connect", "setting": "PROXY_URL"}
    DEAD = "setting PROXY_URL: nothing accepts a connection there"

    def run_connect(self, value):
        values = {} if value is None else {"PROXY_URL": value}
        return resources.check(resource(self.CHECK), values, STUB)

    def test_host_and_port_are_connected_to(self):
        for url, address in (("http://proxy.invented:4000", ("proxy.invented", 4000)),
                             ("http://proxy.invented", ("proxy.invented", 80)),
                             ("https://proxy.invented", ("proxy.invented", 443)),
                             ("https://proxy.invented:4001/v1?x=y", ("proxy.invented", 4001))):
            with self.subTest(url=url), mock.patch.object(resources.socket, "create_connection") as connect:
                self.assertEqual(self.run_connect(url), (True, []))
                connect.assert_called_once_with(address, timeout=resources.CONNECT_TIMEOUT)
                connect.return_value.close.assert_called_once_with()

    def test_nothing_accepts(self):
        for error in (ConnectionRefusedError, TimeoutError):
            with self.subTest(error=error), mock.patch.object(resources.socket, "create_connection",
                                                              side_effect=error) as connect:
                self.assertEqual(self.run_connect("http://proxy.invented:4000"), (False, [self.DEAD]))
                connect.assert_called_once()

    def test_malformed_urls_are_not_connected_to(self):
        for url in ("127.0.0.1:1", "http://", "http://127.0.0.1:notaport", "ftp://127.0.0.1"):
            with self.subTest(url=url), mock.patch.object(resources.socket, "create_connection") as connect:
                self.assertEqual(self.run_connect(url), (False, ["setting PROXY_URL names no host and port"]))
                connect.assert_not_called()

    def test_empty(self):
        self.assertEqual(self.run_connect(""), (False, ["setting PROXY_URL is empty"]))
        self.assertEqual(self.run_connect(None), (False, ["setting PROXY_URL is empty"]))

    def test_real_listener_receives_nothing(self):
        other, register = shipped("inference.other")
        client = self.tmp / "proxy.client"
        client.write_text("invented\n", encoding="utf-8")
        client.chmod(0o600)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(5)
        values = {"SECRETS_DIR": str(self.tmp), "PROXY_URL": f"http://127.0.0.1:{listener.getsockname()[1]}/v1"}
        self.assertEqual(resources.check(other, values, register), (True, []))
        listener.settimeout(2)
        peer, _ = listener.accept()
        self.addCleanup(peer.close)
        peer.settimeout(2)
        self.assertEqual(peer.recv(1024), b"")
        listener.close()
        self.assertEqual(resources.check(other, values, register), (False, [self.DEAD]))

    def test_shipped_resource_with_a_closed_port(self):
        other, register = shipped("inference.other")
        client = self.tmp / "proxy.client"
        client.write_text("invented\n", encoding="utf-8")
        client.chmod(0o600)
        values = {"SECRETS_DIR": str(self.tmp), "PROXY_URL": f"http://127.0.0.1:{free_port()}"}
        self.assertEqual(resources.check(other, values, register), (False, [self.DEAD]))


class NewKindsValidation(unittest.TestCase):
    def problems(self, check=None, **top):
        register = fixture_register()
        register["check_kinds"].update(NEW_KINDS)
        if check is not None:
            register["resources"].append(resource(check, id="new.kind"))
        register.update(top)
        return resources.validate(register)

    def test_valid_checks_and_path_settings(self):
        self.assertEqual(self.problems(path_settings=["HARNESS", "STATE_DIR"]), [])
        self.assertEqual(self.problems(path_settings=[]), [])
        for check in ({"kind": "accounts", "settings": ["A", "B"], "secret": "pool"},
                      {"kind": "connect", "setting": "URL"}, {"kind": "directory", "setting": "DIR"}):
            with self.subTest(check=check):
                self.assertEqual(self.problems(check), [])

    def test_each_problem(self):
        rows = [
            ({"kind": "accounts", "secret": "pool"}, "accounts check has no settings"),
            ({"kind": "accounts", "settings": [], "secret": "pool"},
             "accounts check settings is not a non-empty list of setting names"),
            ({"kind": "accounts", "settings": ["A", 3], "secret": "pool"},
             "accounts check settings is not a non-empty list of setting names"),
            ({"kind": "accounts", "settings": ["A"]}, "accounts check has no secret"),
            ({"kind": "accounts", "settings": ["A"], "secret": ""}, "accounts check secret is not a non-empty string"),
            ({"kind": "connect"}, "connect check has no setting"),
            ({"kind": "directory"}, "directory check has no setting"),
            ({"kind": "accounts", "settings": ["A"], "secret": "pool", "name": "x"}, "accounts check has unknown key name"),
            ({"kind": "connect", "setting": "URL", "port": 1}, "connect check has unknown key port"),
            ({"kind": "directory", "setting": "DIR", "mode": "0700"}, "directory check has unknown key mode"),
        ]
        for check, problem in rows:
            with self.subTest(check=check):
                self.assertEqual(self.problems(check), [f"new.kind: check 1: {problem}"])

    def test_path_settings_must_be_a_list_of_names(self):
        for value in ("HARNESS", ["HARNESS", ""], ["HARNESS", 3], None):
            with self.subTest(value=value):
                self.assertEqual(self.problems(path_settings=value),
                                 ["register: path_settings is not a list of setting names"])


class GatewayOff(Production):
    TITLE = "A resource behind the gateway"

    def gated(self, holder="gateway", wave=1, failing=False):
        register = fixture_register()
        marker = self.tmp / "ran"
        check = ({"kind": "setting", "name": "GATED_URL"} if failing else
                 {"kind": "command", "argv": ["{PY}", "-c", f"open({str(marker)!r}, 'w').close()"]})
        register["resources"].append(resource(check, id="gated.tool", title=self.TITLE, holder=holder, wave=wave))
        return register, marker

    def test_gateway_off_skips_without_running_checks(self):
        register, marker = self.gated()
        self.write_local(GATEWAY="off")
        code, out, err = self.doctor(register=register)
        self.assertEqual((code, err), (0, ""))
        self.assertIn(f"skipped\tgated.tool\t{self.TITLE}\n  GATEWAY=off\n", out)
        # mode.pick of the fixture register is held by the gateway too.
        self.assertIn("skipped\tmode.pick\tA setting from a fixed list\n  GATEWAY=off\n", out)
        self.assertTrue(out.endswith("doctor: 6 ok, 0 missing, 0 absent, 2 skipped\n"))
        code, out, _ = self.doctor("--json", register=register)
        self.assertEqual(code, 0)
        self.assertIn({"id": "gated.tool", "reasons": ["GATEWAY=off"], "state": "skipped"},
                      json.loads(out)["resources"])
        self.assertFalse(marker.exists())

    def test_gateway_off_does_not_fail_on_a_gateway_resource(self):
        register, _ = self.gated(failing=True)
        self.write_local(GATEWAY="off")
        self.assertEqual(self.doctor(register=register)[0], 0)
        self.write_local(GATEWAY="on")
        code, out, _ = self.doctor(register=register)
        self.assertEqual(code, 1)
        self.assertIn(f"missing\tgated.tool\t{self.TITLE}\n  setting GATED_URL is empty\n", out)

    def test_any_other_gateway_value_checks_it(self):
        register, marker = self.gated()
        for value in ("on", None, "OFF"):
            with self.subTest(value=value):
                if value is None:
                    self.local.pop("GATEWAY", None)
                    self.write_local()
                else:
                    self.write_local(GATEWAY=value)
                code, out, _ = self.doctor(register=register)
                self.assertEqual(code, 0)
                self.assertIn(f"ok\tgated.tool\t{self.TITLE}\n", out)
                self.assertTrue(marker.exists())
                marker.unlink()

    def test_another_holder_is_checked(self):
        register, marker = self.gated(holder="launcher")
        self.write_local(GATEWAY="off")
        code, out, _ = self.doctor(register=register)
        self.assertEqual(code, 0)
        self.assertIn(f"ok\tgated.tool\t{self.TITLE}\n", out)
        self.assertTrue(marker.exists())

    def test_skipped_by_wave_has_no_reason(self):
        register, marker = self.gated(wave=4)
        self.write_local(GATEWAY="off")
        _, out, _ = self.doctor("--wave", "3", register=register)
        self.assertIn(f"skipped\tgated.tool\t{self.TITLE}\n", out)
        self.assertNotIn("GATEWAY=off", out)
        _, out, _ = self.doctor("--wave", "3", "--json", register=register)
        self.assertIn({"id": "gated.tool", "reasons": [], "state": "skipped"}, json.loads(out)["resources"])
        self.assertFalse(marker.exists())


class NamesIn(TempDir):
    """A command check with `names_in`: its stripped standard output must be one of the names of a setting."""

    SHOWN = "{PY} -c print('Octo-Cat')"
    NOT_NAMED = f"setting LOGINS does not name the output of: {SHOWN}"

    def check(self, code="print('Octo-Cat')", **extra):
        return {"kind": "command", "argv": ["{PY}", "-c", code], "names_in": "LOGINS", **extra}

    def run_check(self, check, **values):
        return resources.check(resource(check), {"PY": sys.executable, **values}, STUB)

    def test_output_named_passes_and_another_name_fails(self):
        self.assertEqual(self.run_check(self.check(), LOGINS="octo-cat"), (True, []))
        self.assertEqual(self.run_check(self.check(), LOGINS="someone-else"), (False, [self.NOT_NAMED]))

    def test_names_are_trimmed_case_ignored_and_empty_ones_dropped(self):
        self.assertEqual(self.run_check(self.check(), LOGINS=" first , OCTO-CAT ,"), (True, []))
        self.assertEqual(self.run_check(self.check(), LOGINS=" first , OCTO-DOG ,"), (False, [self.NOT_NAMED]))

    def test_empty_setting_is_the_only_reason_and_the_command_does_not_run(self):
        marker = self.tmp / "ran"
        code = f"open({str(marker)!r}, 'w').close(); print('octo-cat')"
        shown = f"setting LOGINS does not name the output of: {{PY}} -c {code}"
        for values in ({}, {"LOGINS": ""}, {"LOGINS": " , ,"}):
            with self.subTest(values=values):
                self.assertEqual(self.run_check(self.check(code), **values), (False, ["setting LOGINS is empty"]))
                self.assertFalse(marker.exists())
        self.assertEqual(self.run_check(self.check(code), LOGINS="other"), (False, [shown]))
        self.assertTrue(marker.exists())

    def test_empty_or_white_space_output_is_not_named(self):
        for code in ("pass", "print('  ')"):
            with self.subTest(code=code):
                self.assertEqual(self.run_check(self.check(code), LOGINS="octo-cat"),
                                 (False, [f"setting LOGINS does not name the output of: {{PY}} -c {code}"]))
        self.assertEqual(self.run_check(self.check("print('  octo-cat  ')"), LOGINS="octo-cat"), (True, []))

    def test_standard_error_is_ignored(self):
        code = "import sys; sys.stderr.write('octo-cat')"
        self.assertEqual(self.run_check(self.check(code), LOGINS="octo-cat"),
                         (False, [f"setting LOGINS does not name the output of: {{PY}} -c {code}"]))
        code = "import sys; sys.stderr.write('noise'); print('octo-cat')"
        self.assertEqual(self.run_check(self.check(code), LOGINS="octo-cat"), (True, []))

    def test_non_zero_exit_gives_only_todays_reason(self):
        code = "print('octo-cat'); raise SystemExit(3)"
        self.assertEqual(self.run_check(self.check(code), LOGINS="octo-cat"),
                         (False, [f"command failed: {{PY}} -c {code}"]))
        self.assertEqual(self.run_check(self.check("print('octo-cat')"), LOGINS="octo-cat"), (True, []))

    def test_match_all_and_names_in_both_apply_pattern_first(self):
        check = self.check(match_all=["Octo", "missing-pattern"])
        self.assertEqual(self.run_check(check, LOGINS="someone-else"),
                         (False, ["pattern not found: missing-pattern", self.NOT_NAMED]))
        self.assertEqual(self.run_check(self.check(match_all=["Octo"]), LOGINS="octo-cat"), (True, []))

    def test_validation(self):
        def problems(check):
            register = fixture_register()
            register["resources"][0]["checks"] = [check]
            return resources.validate(register)

        command = {"kind": "command", "argv": ["{PY}", "-c", "print('octo-cat')"]}
        for value in ("", 3):
            with self.subTest(names_in=value):
                self.assertEqual(problems({**command, "names_in": value}),
                                 ["tool.runner: check 1: command check names_in is not a non-empty string"])
        self.assertEqual(problems({"kind": "setting", "name": "MODE", "names_in": "LOGINS"}),
                         ["tool.runner: check 1: setting check has unknown key names_in"])
        self.assertEqual(problems({**command, "names_in": "LOGINS"}), [])

    def test_shipped_github_checks(self):
        register, problems = resources.load(SHIPPED)
        self.assertEqual(problems, [])
        github = next(r for r in register["resources"] if r["id"] == "state.github")
        shown = [(c["kind"], " ".join(c["argv"]), sorted(set(c) - {"kind", "argv"}), c.get("names_in"))
                 for c in github["checks"]]
        self.assertEqual(shown, [("command", "git --version", [], None),
                                 ("command", "gh auth status", [], None),
                                 ("command", "gh api user --jq .login", ["names_in"], "GITHUB_LOGINS")])


class NamesInNoValues(Production):
    """Run the real script with a `names_in` check whose output and setting value are seeded strings."""

    SETTING = "zq-logins-value-3306"
    OUTPUT = "zq-login-output-2295"

    def test_no_value_reaches_stdout_or_stderr(self):
        kit = self.tmp / "copy" / "kit"
        (kit / "lib").mkdir(parents=True)
        for name in ("__init__.py", "lib/__init__.py", "lib/resources.py", "lib/grants.py", "lib/cmd_doctor.py"):
            shutil.copy(ROOT / "kit" / name, kit / name)
        register = fixture_register()
        output = "'-'.join(['zq', 'login', 'output', '2295'])"
        register["resources"].append(resource(
            {"kind": "command", "argv": ["{PY}", "-c", f"print({output})"], "names_in": "LEAK_LOGINS"},
            id="leak.logins"))
        (kit / "resources.json").write_text(json.dumps(register), encoding="utf-8")
        cases = ((self.SETTING, 1, "missing\tleak.logins\t"), (f"{self.SETTING},{self.OUTPUT}", 0, "ok\tleak.logins\t"))
        for value, code, line in cases:
            self.write_local(LEAK_LOGINS=value)
            for extra in ([], ["--json"]):
                with self.subTest(code=code, extra=extra):
                    done = subprocess.run([sys.executable, str(kit / "lib" / "cmd_doctor.py"), "--root",
                                           str(self.root), *extra], capture_output=True, text=True, cwd=self.tmp)
                    self.assertEqual(done.returncode, code, done.stderr)
                    for seeded in (self.SETTING, self.OUTPUT):
                        self.assertNotIn(seeded, done.stdout)
                        self.assertNotIn(seeded, done.stderr)
                    if not extra:
                        self.assertIn(line, done.stdout)
                        if code:
                            self.assertIn("  setting LEAK_LOGINS does not name the output of: {PY} -c print(",
                                          done.stdout)


if __name__ == "__main__":
    unittest.main()
