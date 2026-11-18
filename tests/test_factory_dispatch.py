"""Tests for the `factory` dispatcher run from a copy of the kit in a directory not named `kit` (`.factory/`),
as a production installs it. No network, no model."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DISPATCHER = ROOT / "kit" / "factory"
TIMEOUT_S = 120

PROBE = '''import multiprocessing

from kit.lib import preview


def main(argv):
    with multiprocessing.get_context("spawn").Pool(2) as pool:
        print(pool.apply_async(repr, (preview.of,)).get(timeout=30))
    return 0
'''


def install(tmp: Path, dispatcher: Path = DISPATCHER) -> Path:
    """Copy the dispatcher and `kit/lib/` into `<tmp>/.factory/` and add the probe command there only."""
    kit = tmp / ".factory"
    shutil.copytree(ROOT / "kit" / "lib", kit / "lib", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(dispatcher, kit / "factory")
    (kit / "lib" / "cmd_probe.py").write_text(PROBE, encoding="utf-8")
    return kit / "factory"


def factory(path: Path, *argv: str) -> subprocess.CompletedProcess:
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    return subprocess.run([sys.executable, str(path), *argv], cwd=path.parent.parent, env=env,
                          capture_output=True, text=True, timeout=TIMEOUT_S)


class InstalledDispatcherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.factory = install(self.tmp)

    def test_spawned_child_imports_kit(self):
        self.assertFalse((self.tmp / "kit").exists())
        done = factory(self.factory, "probe")
        self.assertNotIn("ModuleNotFoundError", done.stderr)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(done.stdout.startswith("<function of"), done.stdout)

    def test_help_lists_commands(self):
        done = factory(self.factory, "--help")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("  run\n", done.stdout)
        self.assertIn("  probe\n", done.stdout)

    def test_unknown_command_exits_2(self):
        done = factory(self.factory, "nosuchcommand")
        self.assertEqual(done.returncode, 2)
        self.assertIn("unknown command", done.stderr)
        self.assertIn("usage: factory", done.stderr)


if __name__ == "__main__":
    unittest.main()
