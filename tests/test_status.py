"""Tests for kit/lib/cmd_status.py, cmd_freeze.py and cmd_unfreeze.py.

GitHub is a recorded directory under tests/fixtures/status/ (copied, never changed in place); `show`
holds three open tickets in two rooms, and `malformed` adds one malformed tracked issue to it. The git
repository is built in a temporary directory from `production/`, with one branch per recorded pull.
In a comment body and in `expected.txt`, ``{head:N}`` stands for the head of pull N's branch (the
first 12 characters in `expected.txt`).
"""

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import cmd_freeze, cmd_status, cmd_unfreeze, gitstate, github, tickets  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "status"
PRODUCTION = FIX / "production"
HEAD = re.compile(r"\{head:(\d+)\}")
NOW = "2026-11-11T11:00:00Z"
COMMANDS = {"status": cmd_status, "freeze": cmd_freeze, "unfreeze": cmd_unfreeze}


class World:
    """A production repository on branch `trunk` and a recorded GitHub directory, both temporary."""

    def __init__(self, test, *github_fixtures):
        tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, tmp)
        self.root = tmp / "show"
        self.dir = tmp / "github"
        patch = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                                             "FACTORY_GITHUB": str(self.dir), "FACTORY_NOW": NOW})
        patch.start()
        test.addCleanup(patch.stop)
        # A live adapter must never be built: every command reads the recorded directory.
        gh = mock.patch.object(github, "Gh", side_effect=AssertionError("the live gh adapter was built"))
        gh.start()
        test.addCleanup(gh.stop)
        shutil.copytree(PRODUCTION, self.root)
        self.git("init", "-q", "-b", "trunk")
        self.commit("the production")
        self.dir.mkdir()
        for name in github_fixtures:
            shutil.copytree(FIX / name, self.dir, dirs_exist_ok=True)
        for path in sorted((self.dir / "pulls").glob("*.json")) if (self.dir / "pulls").is_dir() else []:
            self.branch(json.loads(path.read_text())["head_ref"])
        for path in sorted((self.dir / "comments").glob("*.json")) if (self.dir / "comments").is_dir() else []:
            path.write_text(self.heads(path.read_text()))

    def git(self, *args):
        return gitstate.git(self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", *args)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def branch(self, ref):
        """A branch `ref` with one commit on top of `trunk`."""
        self.git("checkout", "-q", "-b", ref, "trunk")
        path = self.root / "work" / (ref.replace("/", "-") + ".md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(ref + "\n")
        self.commit("write " + ref)
        self.git("checkout", "-q", "trunk")

    def head(self, number):
        pull = json.loads((self.dir / "pulls" / f"{number}.json").read_text())
        return gitstate.head(self.root, pull["head_ref"])

    def heads(self, text, length=40):
        return HEAD.sub(lambda m: self.head(int(m.group(1)))[:length], text)

    def read(self, rel):
        return json.loads((self.dir / rel).read_text())

    def write(self, rel, data):
        (self.dir / rel).write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")

    def log(self):
        path = self.dir / "log.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []

    def run(self, command, *argv, root=None):
        """``(exit, stdout, stderr)`` of the command's ``main`` with ``--root``."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = COMMANDS[command].main(["--root", str(root or self.root), *argv])
        return code, out.getvalue(), err.getvalue()


def ticket_lines(text):
    return [line for line in text.splitlines() if line.startswith("#")]


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.w = World(self, "show")

    def test_text(self):
        code, out, err = self.w.run("status")
        self.assertEqual((code, err), (0, ""))
        expected = self.w.heads((FIX / "expected.txt").read_text(), length=12)
        self.assertEqual(out, expected)

    def test_deterministic(self):
        self.assertEqual(self.w.run("status"), self.w.run("status"))
        self.assertEqual(self.w.run("status", "--json"), self.w.run("status", "--json"))

    def test_json(self):
        code, out, err = self.w.run("status", "--json")
        self.assertEqual((code, err), (0, ""))
        data = json.loads(out)
        self.assertEqual(out, json.dumps(data, indent=2, sort_keys=True) + "\n")
        self.assertEqual(data["frozen"], False)
        self.assertEqual(data["errors"], [])
        self.assertEqual([(t["number"], t["room"], t["stage"], t["unit"], t["state"], t["step"], t["role"])
                          for t in data["tickets"]], [
            (3, "post", "rough", "ep01", "lockable", "lock", None),
            (1, "story", "premise", "ep01", "ready", "launch", "writer"),
            (2, "story", "premise", "ep02", "review", "launch", "editor"),
        ])
        self.assertEqual(data["tickets"][0]["reason"], "#5 has every counting verdict")
        self.assertEqual(data["tickets"][1]["pulls"], [])
        self.assertEqual(data["tickets"][0]["pulls"], [
            {"number": 5, "state": "lockable", "step": "lock", "role": None, "head": self.w.head(5),
             "lint": None, "rounds": 1, "blockers": 0}])
        self.assertEqual(data["tickets"][2]["pulls"], [
            {"number": 4, "state": "review", "step": "launch", "role": "editor", "head": self.w.head(4),
             "lint": "PASS", "rounds": 0, "blockers": 0}])
        self.assertEqual(len(self.w.head(4)), 40)

    def test_room(self):
        code, out, _ = self.w.run("status", "--room", "story")
        self.assertEqual(code, 0)
        self.assertEqual([line.split()[0] for line in ticket_lines(out)], ["#1", "#2"])
        self.assertIn("  PR #4 ", out)
        self.assertNotIn("PR #5", out)
        self.assertEqual(out.splitlines()[-1], "errors: 0")
        code, out, _ = self.w.run("status", "--room", "post", "--json")
        self.assertEqual([t["number"] for t in json.loads(out)["tickets"]], [3])

    def test_unknown_room(self):
        code, out, err = self.w.run("status", "--room", "lab")
        self.assertEqual((code, out, err), (2, "", "status: unknown room lab\n"))

    def test_closed_ticket_is_not_listed(self):
        self.w.write("issues/1.json", {**self.w.read("issues/1.json"), "state": "closed"})
        _, out, _ = self.w.run("status")
        self.assertEqual([line.split()[0] for line in ticket_lines(out)], ["#3", "#2"])

    def test_timeout_prints_the_role(self):
        launch = {"id": 9, "created_at": "2026-11-11T06:00:00Z", "role": "chief",
                  "body": "[CHIEF] LAUNCH editor PM-2-b\n"}
        self.w.write("comments/4.json", self.w.read("comments/4.json") + [launch])
        _, out, _ = self.w.run("status")
        self.assertIn("#2 story premise ep02 running next=timeout:editor ", out)
        self.assertIn("  PR #4 running next=timeout:editor ", out)

    def test_missing_branch_is_one_line(self):
        self.w.git("branch", "-q", "-D", "feat/PM-2-ep02")
        code, out, err = self.w.run("status")
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(len(err.splitlines()), 1)
        self.assertTrue(err.startswith("status: git "), err)


class ErrorsTest(unittest.TestCase):
    def test_malformed_issue(self):
        w = World(self, "show", "malformed")
        code, out, err = w.run("status")
        self.assertEqual((code, err), (0, ""))
        lines = out.splitlines()
        self.assertEqual(lines[-2:], ["errors: 1", "ticket #8: title is not [KEY] summary"])
        self.assertEqual(json.loads(w.run("status", "--json")[1])["errors"],
                         ["ticket #8: title is not [KEY] summary"])

    def test_clean_twin(self):
        w = World(self, "show")
        code, out, _ = w.run("status")
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[-1], "errors: 0")


class FreezeTest(unittest.TestCase):
    def setUp(self):
        self.w = World(self, "show")

    def test_freeze_then_status(self):
        self.assertEqual(self.w.run("freeze"), (0, "frozen (#7)\n", ""))
        self.assertEqual(self.w.read("issues/7.json"), {
            "number": 7, "title": "[FREEZE] production frozen", "body": "frozen by factory freeze",
            "state": "open", "labels": [tickets.FREEZE], "created_at": NOW})
        code, out, _ = self.w.run("status")
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[0], "FROZEN")
        lines = ticket_lines(out)
        self.assertEqual(len(lines), 3)
        for line in lines + [line for line in out.splitlines() if line.startswith("  PR ")]:
            self.assertIn(" frozen next=none ", line)
        self.assertTrue(json.loads(self.w.run("status", "--json")[1])["frozen"])

    def test_freeze_twice_creates_one_issue(self):
        self.w.run("freeze", "--reason", "judges drifting")
        self.assertEqual(self.w.run("freeze"), (0, "already frozen (#7)\n", ""))
        self.assertEqual(sorted(p.name for p in (self.w.dir / "issues").iterdir()),
                         ["1.json", "2.json", "3.json", "6.json", "7.json"])
        self.assertEqual(self.w.read("issues/7.json")["body"], "judges drifting")
        self.assertEqual([(e["op"], e["number"]) for e in self.w.log()], [("create_issue", 7)])

    def test_already_frozen_names_the_lowest(self):
        for n in (12, 9):
            self.w.write(f"issues/{n}.json", {"number": n, "title": "stop", "body": "", "state": "open",
                                              "labels": [tickets.FREEZE], "created_at": NOW})
        self.assertEqual(self.w.run("freeze"), (0, "already frozen (#9)\n", ""))
        self.assertEqual(self.w.log(), [])

    def test_unfreeze(self):
        self.w.run("freeze")
        self.assertEqual(self.w.run("unfreeze"), (0, "unfrozen (#7)\n", ""))
        self.assertEqual(self.w.read("issues/7.json")["state"], "closed")
        self.assertEqual([(e["op"], e["number"]) for e in self.w.log()], [("create_issue", 7), ("close_issue", 7)])
        self.assertEqual(self.w.run("unfreeze"), (0, "not frozen\n", ""))
        self.assertEqual(len(self.w.log()), 2)
        self.assertNotIn("FROZEN", self.w.run("status")[1])

    def test_unfreeze_closes_every_freeze_issue(self):
        for n in (12, 9):
            self.w.write(f"issues/{n}.json", {"number": n, "title": "stop", "body": "", "state": "open",
                                              "labels": [tickets.FREEZE], "created_at": NOW})
        self.assertEqual(self.w.run("unfreeze"), (0, "unfrozen (#9)\nunfrozen (#12)\n", ""))
        self.assertEqual([(e["op"], e["number"]) for e in self.w.log()], [("close_issue", 9), ("close_issue", 12)])

    def test_unfreeze_when_not_frozen(self):
        self.assertEqual(self.w.run("unfreeze"), (0, "not frozen\n", ""))
        self.assertFalse((self.w.dir / "log.jsonl").exists())


class ReadOnlyTest(unittest.TestCase):
    def test_log_unchanged(self):
        w = World(self, "show")
        w.run("freeze")
        log = w.dir / "log.jsonl"
        before = hashlib.sha256(log.read_bytes()).hexdigest()
        w.run("status")
        w.run("status", "--json")
        self.assertEqual(hashlib.sha256(log.read_bytes()).hexdigest(), before)

    def test_no_log_created(self):
        w = World(self, "show")
        files = sorted(p.relative_to(w.dir) for p in w.dir.rglob("*"))
        w.run("status")
        w.run("status", "--json")
        self.assertFalse((w.dir / "log.jsonl").exists())
        self.assertEqual(sorted(p.relative_to(w.dir) for p in w.dir.rglob("*")), files)


class OutsideTest(unittest.TestCase):
    def test_no_production(self):
        w = World(self, "show")
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside)
        for command in COMMANDS:
            with self.subTest(command=command):
                code, out, err = w.run(command, root=outside)
                self.assertEqual((code, out), (2, ""))
                self.assertEqual(len(err.splitlines()), 1)
                self.assertTrue(err.startswith(f"{command}: "), err)
        self.assertFalse((w.dir / "log.jsonl").exists())


class EntryPointTest(unittest.TestCase):
    def test_factory_and_script(self):
        w = World(self, "show")
        expected = w.heads((FIX / "expected.txt").read_text(), length=12)
        for argv in (["python3", str(ROOT / "kit" / "factory"), "status"],
                     ["python3", str(ROOT / "kit" / "lib" / "cmd_status.py")]):
            with self.subTest(argv=argv[1]):
                done = subprocess.run([*argv, "--root", str(w.root / ".factory" / "roles")], capture_output=True,
                                      text=True, stdin=subprocess.DEVNULL, cwd=str(w.root.parent))
                self.assertEqual((done.returncode, done.stdout, done.stderr), (0, expected, ""))


if __name__ == "__main__":
    unittest.main()
