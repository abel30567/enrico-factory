"""Tests for kit/lib/registry.py: the asset registry row and the stale shots of a lock (brief sections 6 and 7).

The production is ``factory init <tmp>/show --rooms story,production`` with
tests/fixtures/registry/production copied over it, made into a git repository on branch ``main``. Branch
``feat/ASSETS-AMA-2`` (pull 16) writes the files of tests/fixtures/registry/branches/16. GitHub is a copy of
tests/fixtures/registry/github; a comment body's ``{head:16}`` is the head of that branch.

Recorded (ticket -> pull, stage unit), all closed and merged but the last:
- #1 -> #2 intake ep01; #3 -> #4 assets ama (the version A of the registry's row);
- #5 -> #6 layout ep01/Q1-1; #7 -> #8 animation ep01/Q1-1; #9 -> #10 layout ep01/Q1-2;
- #11 -> #12 layout ep01/Q2-1; #13 -> #14 animation ep01/Q2-1;
- #15 -> #16 assets ama, open: the new version B, approved by ``look-reviewer``.
Shot states: ep01/Q1-1 layout and animation name ama at A and pier; ep01/Q1-2 layout names pier only;
ep01/Q2-1 layout names pier only, animation ama at A and pier.
"""

import contextlib
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

from kit.lib import cmd_lock, gitstate, github, production, registry, steps, tickets  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "registry"
NOW = "2026-11-13T12:00:00Z"
BRANCH = "feat/ASSETS-AMA-2"
A, B = "a" * 64, "b" * 64
HEAD = re.compile(r"\{head:(\d+)\}")
FIXTURE_ROWS = [registry.HEADER, f"2026-11-02T12:00:00Z\tama\t{A}\t3\t4\tnone"]


def copy(source, dest):
    shutil.copytree(source, dest, dirs_exist_ok=True, copy_function=shutil.copy)


def state_text(shot, stage, ama):
    actors = [{"id": "pier", "kind": "set", "asset": "asset:pier", "sha256": "c" * 64}]
    if ama is not None:
        actors.insert(0, {"id": "ama", "kind": "character", "asset": "asset:ama", "sha256": ama})
    return json.dumps({"shot": shot, "stage": stage, "actors": actors}, indent=1, sort_keys=True) + "\n"


class World:
    """The production repository, its state directory and a recorded GitHub, all temporary.

    ``edits`` maps a path to the text written on ``main`` before the first commit (None removes it),
    ``branch`` a path to the text written by pull 16's commit, ``drop`` the GitHub records removed and
    ``issues`` maps an issue number to the fields that replace its record's.
    """

    def __init__(self, test, edits=None, branch=None, drop=(), issues=None):
        self.test = test
        tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, tmp)
        self.root = tmp / "show"
        self.dir = tmp / "github"
        patch = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                                             "FACTORY_GITHUB": str(self.dir), "FACTORY_NOW": NOW})
        patch.start()
        test.addCleanup(patch.stop)
        self.root.mkdir()
        done = subprocess.run([sys.executable, str(ROOT / "kit" / "factory"), "init", str(self.root),
                               "--rooms", "story,production"], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)
        test.assertEqual(done.returncode, 0, done.stderr)
        (self.root / "production.local.env").write_text(f"STATE_DIR={tmp / 'state'}\n")
        copy(FIX / "production", self.root)
        for rel, text in sorted((edits or {}).items()):
            self.write(rel, text)
        self.git("init", "-q", "-b", "main")
        self.commit("the production")
        copy(FIX / "github", self.dir)
        for name in drop:
            (self.dir / name).unlink()
        for number, fields in sorted((issues or {}).items()):
            path = self.dir / "issues" / f"{number}.json"
            path.write_text(json.dumps({**json.loads(path.read_text()), **fields}, indent=1, sort_keys=True))
        self.git("checkout", "-q", "-b", BRANCH)
        copy(FIX / "branches" / "16", self.root)
        for rel, text in sorted((branch or {}).items()):
            self.write(rel, text)
        self.commit(f"work on {BRANCH}")
        self.git("checkout", "-q", "main")
        for path in sorted((self.dir / "comments").glob("*.json")):
            path.write_text(HEAD.sub(lambda m: gitstate.head(self.root, BRANCH), path.read_text()))
        self.gh = github.Recorded(self.dir)
        self.p = production.load(self.root)

    def git(self, *args):
        return gitstate.git(self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", *args)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def write(self, rel, text):
        path = self.root / rel
        if text is None:
            path.unlink()
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def state(self):
        return tickets.load(self.p, self.gh, self.root)

    def lock(self):
        """``(code, stdout, stderr)`` of locking pull 16 for ticket 15."""
        state = self.state()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_lock.lock(self.p, self.gh, state, state.tickets[15], state.pulls[16])
        return code, out.getvalue(), err.getvalue()

    def merge_sha(self):
        return gitstate.head(self.root, "main^{/^Merge pull request #16}")[:12]

    def registry(self):
        return gitstate.show(self.root, "main", registry.PATH).split("\n")[:-1]

    def subject(self):
        return self.git("log", "-1", "--format=%s", "main").strip()

    def issue(self, number):
        path = self.dir / "issues" / f"{number}.json"
        return json.loads(path.read_text()) if path.is_file() else None


class TestRegistry(unittest.TestCase):

    def test_second_version(self):
        w = World(self)
        code, out, err = w.lock()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines(), [
            f"locked PR #16 at {w.merge_sha()}",
            "registry: asset ama version bbbbbbbbbbbb, 2 stale shot(s)",
            "stale: ep01/Q1-1 at layout: filed #17",
            "stale: ep01/Q2-1 at animation: filed #18"])
        self.assertEqual(w.registry(), FIXTURE_ROWS + [
            f"2026-11-13T12:00:00Z\tama\t{B}\t15\t16\tep01/Q1-1:layout,ep01/Q2-1:animation"])
        self.assertEqual(w.subject(), "registry: asset ama bbbbbbbbbbbb PR #16")
        self.assertEqual(w.git("status", "--short"), "")
        first, second = w.issue(17), w.issue(18)
        self.assertEqual(first["title"], "[LAYOUT-EP01-Q1-1] layout for ep01/Q1-1")
        self.assertEqual(first["labels"], ["room:production", "stage:layout", "tracked", "unit:ep01/Q1-1"])
        self.assertEqual(first["body"],
                         "Do the stage layout for unit ep01/Q1-1 again: asset ama has the version bbbbbbbbbbbb.\n\n"
                         "Stale: #5 layout ep01/Q1-1, locked by PR #6.\n\nFiled by factory lock.\n")
        self.assertEqual(first["state"], "open")
        self.assertEqual(second["title"], "[ANIMATION-EP01-Q2-1] animation for ep01/Q2-1")
        self.assertEqual(second["labels"],
                         ["room:production", "stage:animation", "tracked", "unit:ep01/Q2-1"])
        self.assertEqual(second["body"],
                         "Do the stage animation for unit ep01/Q2-1 again: asset ama has the version "
                         "bbbbbbbbbbbb.\n\nStale: #13 animation ep01/Q2-1, locked by PR #14.\n\n"
                         "Filed by factory lock.\n")
        self.assertIsNone(w.issue(19))
        self.assertNotIn("ep01/Q1-2", out + w.registry()[-1])

    def test_work_restarts(self):
        w = World(self)
        self.assertEqual(w.lock()[0], 0)
        state = w.state()
        for number, role in ((17, "layout-artist"), (18, "animator")):
            step = steps.next_step(w.p, state, state.tickets[number], w.root, NOW)
            self.assertEqual((step["state"], step["step"], step["role"]), ("ready", "launch", role), step)
        twin = World(self, issues={5: {"state": "open"}})
        state = twin.state()
        step = steps.next_step(twin.p, state, state.tickets[5], twin.root, NOW)
        self.assertEqual((step["state"], step["reason"]), ("locked", "#6 is merged"))

    def test_earliest_stage(self):
        w = World(self, edits={"production/ep01/Q1-1/layout/state.json": state_text("Q1-1", "layout", B)})
        code, out, _ = w.lock()
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[2], "stale: ep01/Q1-1 at animation: filed #17")

    def test_same_version_twin(self):
        edits = {"production/ep01/Q1-1/layout/state.json": state_text("Q1-1", "layout", B),
                 "production/ep01/Q1-1/animation/state.json": state_text("Q1-1", "animation", B),
                 "production/ep01/Q2-1/animation/state.json": None}
        w = World(self, edits=edits)
        code, out, _ = w.lock()
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[1:], ["registry: asset ama version bbbbbbbbbbbb, 0 stale shot(s)"])
        self.assertTrue(w.registry()[-1].endswith("\tnone"), w.registry())
        self.assertEqual(len(w.registry()), 3)
        self.assertIsNone(w.issue(17))

    def test_open_ticket(self):
        w = World(self)
        number = w.gh.create_issue("[LAYOUT-EP01-Q1-1] layout for ep01/Q1-1", "Layout again.\n",
                                   ["tracked", "room:production", "stage:layout", "unit:ep01/Q1-1"])
        self.assertEqual(number, 17)
        code, out, _ = w.lock()
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[2:], ["stale: ep01/Q1-1 at layout: ticket #17 is open",
                                                "stale: ep01/Q2-1 at animation: filed #18"])
        self.assertIsNone(w.issue(19))

    def test_no_locked_ticket(self):
        w = World(self, drop=("issues/13.json", "pulls/14.json"))
        code, out, _ = w.lock()
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[2:], ["stale: ep01/Q1-1 at layout: filed #17",
                                                "stale: ep01/Q2-1 at animation: no locked ticket"])
        self.assertIsNotNone(w.issue(17))
        self.assertIsNone(w.issue(18))

    def test_no_version(self):
        w = World(self, branch={"production/assets/ama/assets/state.json": '{"asset": "ama"}\n'})
        code, out, err = w.lock()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines(), [
            f"locked PR #16 at {w.merge_sha()}",
            "registry: no row for PR #16: production/assets/ama/assets/state.json has no sha256"])
        self.assertEqual(w.registry(), FIXTURE_ROWS)
        self.assertIsNone(w.issue(17))

    def test_not_an_asset_id(self):
        labels = ["room:production", "stage:assets", "tracked", "unit:Ama"]
        w = World(self, issues={15: {"labels": labels}})
        code, out, err = w.lock()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines(), [f"locked PR #16 at {w.merge_sha()}",
                                            "registry: no row for PR #16: unit Ama is not an asset id"])
        self.assertEqual(w.registry(), FIXTURE_ROWS)
        self.assertIsNone(w.issue(17))

    def test_registry_differs(self):
        w = World(self)
        with open(w.root / registry.PATH, "a") as f:
            f.write("x\n")
        code, out, err = w.lock()
        self.assertEqual(code, 5)
        self.assertEqual(out.splitlines(), [f"locked PR #16 at {w.merge_sha()}"])
        self.assertEqual(err, "lock: registry: registry/assets.tsv differs from the base\n")
        self.assertEqual(w.gh.pull(16)["state"], "merged")
        self.assertEqual(w.issue(15)["state"], "closed")
        self.assertIsNone(w.issue(17))
        self.assertEqual(w.registry(), FIXTURE_ROWS)

    def test_other_stage(self):
        w = World(self)
        state = w.state()
        log = w.git("log", "--format=%H %s", "main")
        before = sorted(p.name for p in (w.dir / "issues").iterdir())
        self.assertEqual(registry.record(w.p, w.gh, w.root, state, state.tickets[5], state.pulls[6]), [])
        self.assertEqual(w.registry(), FIXTURE_ROWS)
        self.assertEqual(w.git("log", "--format=%H %s", "main"), log)
        self.assertEqual(sorted(p.name for p in (w.dir / "issues").iterdir()), before)
        self.assertEqual(w.git("status", "--short"), "")


if __name__ == "__main__":
    unittest.main()
