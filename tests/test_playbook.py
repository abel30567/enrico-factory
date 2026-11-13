"""Tests for kit/lib/cmd_playbook.py: `factory playbook` lists the expired lessons and, with --apply, drops them
by a pull request the program opens (ISSUE-200).

The world is test_lab.World: a production on branch `trunk` and a recorded GitHub, `FACTORY_NOW`
2026-11-13T09:00:00Z. Nothing reaches the network or `gh`.
"""

import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lab import World  # noqa: E402

from kit.lib import cmd_playbook, gitstate  # noqa: E402

LESSON = ("---\nconfidence: high\nexpires: {expires}\nevidence: lab/loops/checker-strength.json:1,#1\n---\n"
          "A cold open holds the first three seconds better than a title card.\n")
LESSONS = {"PB-1.md": "2026-11-12", "PB-2.md": "2026-11-13", "PB-3.md": "2026-02-30", "PB-10.md": "2025-01-31",
           "notes.md": "2020-01-01"}
LISTED = "playbook PB-1: expired 2026-11-12\nplaybook PB-10: expired 2025-01-31\n"
BRANCH = "playbook/expired-2026-11-13"
BODY = ("Lessons of the playbook whose expiry date is before 2026-11-13, dropped by `factory playbook`:\n\n"
        "- `playbook/PB-1.md`: expired 2026-11-12\n- `playbook/PB-10.md`: expired 2025-01-31\n")


class Playbook(unittest.TestCase):
    def setUp(self):
        self.w = World(self)
        for name, expires in LESSONS.items():
            self.w.write(f"playbook/{name}", LESSON.format(expires=expires))
        self.w.commit("the playbook")
        self.trunk = gitstate.head(self.w.root, "trunk")

    def run_playbook(self, apply=False):
        out, err = io.StringIO(), io.StringIO()
        argv = ["--root", str(self.w.root)] + (["--apply"] if apply else [])
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_playbook.main(argv)
        return code, out.getvalue(), err.getvalue()

    def written(self):
        return (self.w.dir / "log.jsonl").exists(), (self.w.dir / "pulls").exists()

    def log(self):
        path = self.w.dir / "log.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    # Behaviour 6
    def test_lists_the_expired_lessons_and_writes_nothing(self):
        self.assertEqual(self.run_playbook(), (0, LISTED, ""))
        self.assertEqual(self.written(), (False, False))
        self.assertEqual(self.w.git("branch", "--list", BRANCH), "")
        self.assertEqual(gitstate.head(self.w.root, "trunk"), self.trunk)

    # Behaviour 7
    def test_apply_drops_them_by_a_pull_request(self):
        status = self.w.git("status", "--porcelain")
        self.assertEqual(self.run_playbook(apply=True),
                         (0, LISTED + f"playbook: PR #2 drops 2 lesson(s) on branch {BRANCH}\n", ""))
        pull = json.loads((self.w.dir / "pulls" / "2.json").read_text())
        self.assertEqual({k: pull[k] for k in ("title", "head_ref", "base_ref", "ticket", "body")},
                         {"title": "Drop the expired playbook lessons of 2026-11-13", "head_ref": BRANCH,
                          "base_ref": "trunk", "ticket": None, "body": BODY})
        self.assertEqual([(e["op"], e.get("ref", e.get("head_ref"))) for e in self.log()],
                         [("push", BRANCH), ("open_pull", BRANCH)])
        self.assertEqual(self.w.git("rev-list", "--count", f"trunk..{BRANCH}").strip(), "1")
        self.assertEqual(self.w.git("diff", "--name-status", "trunk", BRANCH).splitlines(),
                         ["D\tplaybook/PB-1.md", "D\tplaybook/PB-10.md"])
        self.assertEqual(self.w.git("log", "-1", "--format=%an <%ae>%n%s", BRANCH).splitlines(),
                         ["factory <factory@factory.invalid>", "playbook: drop 2 expired lesson(s)"])
        self.assertEqual(gitstate.head(self.w.root, "trunk"), self.trunk)
        self.assertEqual(self.w.git("symbolic-ref", "--short", "HEAD").strip(), "trunk")
        self.assertEqual(self.w.git("status", "--porcelain"), status)
        self.assertEqual(len(self.w.git("worktree", "list").splitlines()), 1)
        self.assertEqual(self.w.gh.comments(2), [])

    # Behaviour 8
    def test_apply_twice_is_refused(self):
        self.assertEqual(self.run_playbook(apply=True)[0], 0)
        log = self.log()
        self.assertEqual(self.run_playbook(apply=True), (2, "", f"playbook: branch {BRANCH} already exists\n"))
        self.assertEqual(self.log(), log)

    # Behaviour 9
    def test_nothing_expired(self):
        with mock.patch.dict(os.environ, {"FACTORY_NOW": "2025-01-01T00:00:00Z"}):
            self.assertEqual(self.run_playbook(apply=True), (0, "playbook: no lesson has expired on 2025-01-01\n", ""))
        self.assertEqual(self.written(), (False, False))
        self.assertEqual(self.w.git("branch", "--list", "playbook/*"), "")

    def test_a_lesson_holds_on_its_expiry_date(self):
        with mock.patch.dict(os.environ, {"FACTORY_NOW": "2026-11-14T00:00:00Z"}):
            self.assertEqual(self.run_playbook(), (0, "playbook PB-1: expired 2026-11-12\nplaybook PB-2: expired "
                                                      "2026-11-13\nplaybook PB-10: expired 2025-01-31\n", ""))

    def test_lessons_are_read_on_the_base(self):
        self.w.write("playbook/PB-4.md", LESSON.format(expires="2020-01-01"))
        (self.w.root / "playbook" / "PB-1.md").unlink()
        self.assertEqual(self.run_playbook(), (0, LISTED, ""))

    def test_bad_now_is_an_error(self):
        with mock.patch.dict(os.environ, {"FACTORY_NOW": "today"}):
            self.assertEqual(self.run_playbook(),
                             (2, "", "playbook: FACTORY_NOW is not a UTC time YYYY-MM-DDTHH:MM:SSZ\n"))

    # Behaviour 10
    def test_frozen(self):
        self.w.gh.create_issue("[FREEZE] Stop everything", "", ["factory:freeze"])
        for apply in (False, True):
            self.assertEqual(self.run_playbook(apply=apply), (2, "", "playbook: frozen\n"))
        self.assertFalse((self.w.dir / "pulls").exists())
        self.assertEqual(self.w.git("branch", "--list", BRANCH), "")


if __name__ == "__main__":
    unittest.main()
