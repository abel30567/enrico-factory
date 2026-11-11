"""Tests for the resources register and `factory doctor`. No network, no model, no tool that may be absent."""

import contextlib
import copy
import io
import json
import os
import shutil
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


def fixture_register():
    return json.loads(FIXTURE_REGISTER.read_text(encoding="utf-8"))


def resource(*checks, **fields):
    entry = {"id": "x.test", "title": "A test resource", "class": "local", "auth": "none", "holder": "none",
             "rooms": ["all"], "wave": 1, "setup": "agent", "checks": list(checks)}
    entry.update(fields)
    return entry


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
        for name in ("__init__.py", "lib/__init__.py", "lib/resources.py", "lib/cmd_doctor.py"):
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


if __name__ == "__main__":
    unittest.main()
