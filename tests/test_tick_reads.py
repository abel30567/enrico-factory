"""Tests of the GitHub reads of one tick (kit/lib/github.py and kit/lib/tickets.py).

The production is tests/fixtures/tickets/production (its last story stage is `reel`), made a git
repository on branch `trunk` in a temporary directory, with branches `feat/2001` and `feat/1000`.
GitHub is ``github.Gh`` with a fake ``run`` that answers each listing in pages of 100, newest first,
and every comment read with no comments:
- tickets 1 to 1000: closed `reel` tickets, each locked by a merged pull 1001 to 2000;
- tickets 2001 and 2002: open `research` tickets;
- pull 2003: open, ticket 2001; 2004: closed, not merged, ticket 2002; 2005: open, ticket 1000.
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

from kit.lib import cmd_tick, gitstate, github, production, tickets  # noqa: E402

PRODUCTION = ROOT / "tests" / "fixtures" / "tickets" / "production"
REPO = "example/demo-show"
CREATED = "2026-11-01T09:00:00Z"
MERGED = "2026-11-02T09:00:00Z"
LISTING = re.compile(r"^repos/example/demo-show/(issues|pulls)\?state=(open|closed|all)&per_page=100$")
COMMENTS = re.compile(r"^repos/example/demo-show/issues/(\d+)/comments$")


def label(name):
    return {"color": "ededed", "default": False, "description": "", "id": 1, "name": name, "node_id": "LA_1"}


def issue(number, title, labels, state):
    return {"body": "", "created_at": CREATED, "labels": [label(n) for n in labels], "number": number,
            "state": state, "title": title, "user": {"login": "someone"}}


def pull(number, ticket, head_ref, state, merged_at=None):
    return {"base": {"label": "example:trunk", "ref": "trunk", "sha": "0" * 40}, "body": f"Ticket: #{ticket}\n",
            "created_at": CREATED, "head": {"label": f"example:{head_ref}", "ref": head_ref, "sha": "0" * 40},
            "labels": [], "merged_at": merged_at, "number": number, "state": state,
            "title": f"Work for #{ticket}", "user": {"login": "someone"}}


def recorded():
    issues = [issue(n, f"[K-{n}] reel for ep{n:04d}",
                    ["tracked", "room:story", "stage:reel", f"unit:ep{n:04d}"], "closed") for n in range(1, 1001)]
    issues += [issue(n, f"[K-{n}] research for topic{n}",
                     ["tracked", "room:story", "stage:research", f"unit:topic{n}"], "open") for n in (2001, 2002)]
    pulls = [pull(1000 + n, n, f"feat/reel-{n}", "closed", MERGED) for n in range(1, 1001)]
    pulls += [pull(2003, 2001, "feat/2001", "open"), pull(2004, 2002, "feat/topic2002", "closed"),
              pull(2005, 1000, "feat/1000", "open")]
    return {"issues": issues, "pulls": pulls}


class FakeGh:
    """A ``run`` for ``github.Gh`` that records each argv and answers from its last argument."""

    def __init__(self, test):
        self.test = test
        self.items = recorded()
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.test.assertEqual(argv[0], "gh")
        self.calls.append(list(argv))
        path = argv[-1]
        m = COMMENTS.match(path)
        if m:
            return subprocess.CompletedProcess(argv, 0, "[[]]", "")
        m = LISTING.match(path)
        self.test.assertIsNotNone(m, path)
        kind, state = m.groups()
        found = sorted((i for i in self.items[kind] if state == "all" or i["state"] == state),
                       key=lambda i: -i["number"])
        pages = [found[at:at + 100] for at in range(0, len(found), 100)] or [[]]
        return subprocess.CompletedProcess(argv, 0, json.dumps(pages), "")

    def comment_reads(self):
        return sorted(int(COMMENTS.match(argv[-1]).group(1)) for argv in self.calls if COMMENTS.match(argv[-1]))


class LoadReadsOpenItemsTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
        patch.start()
        self.addCleanup(patch.stop)
        os.environ.pop("FACTORY_NOW", None)
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        self.root = tmp / "show"
        shutil.copytree(PRODUCTION, self.root)
        self.git("init", "-q", "-b", "trunk")
        self.commit("the production")
        for ref in ("feat/2001", "feat/1000"):
            self.git("checkout", "-q", "-b", ref, "trunk")
            path = self.root / "work" / (ref.replace("/", "-") + ".md")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(ref + "\n")
            self.commit("write " + ref)
            self.git("checkout", "-q", "trunk")
        self.p = production.load(self.root)
        self.fake = FakeGh(self)
        self.gh = github.Gh(REPO, run=self.fake)

    def git(self, *args):
        return gitstate.git(self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", *args)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def test_load_reads_comments_of_open_items_only(self):
        state = tickets.load(self.p, self.gh, self.root)
        self.assertEqual(len(state.tickets), 1002)
        self.assertEqual(len(state.pulls), 1003)
        self.assertEqual(tickets.locked_pull(state, 1)["number"], 1001)
        self.assertEqual(tickets.locked_pull(state, 1000)["number"], 2000)
        self.assertEqual(self.fake.comment_reads(), [2001, 2002, 2003, 2004, 2005])
        self.assertEqual(len(self.fake.calls), 7)
        self.assertEqual(sorted(state.ticket_comments), [2001, 2002])
        self.assertEqual(state.pulls[1001]["comments"], [])

    def test_dry_tick(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cmd_tick._tick(self.p, self.gh, ["story"], False)
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue(), "STEP #2001 lint PR #2003\n"
                                         "STEP #2002 launch scout on #2002\n"
                                         "tick: 2 steps, 0 need, 0 wait, 0 failed\n")
        self.assertEqual(self.fake.comment_reads(), [2001, 2002, 2003, 2004, 2005])
        self.assertEqual(len(self.fake.calls), 9)


if __name__ == "__main__":
    unittest.main()
