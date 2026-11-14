"""Tests for kit/lib/verdicts.py: `factory lock` appends the judges' verdicts of a blind lock to
registry/verdicts.tsv on the base.

The world is test_lockrules.World built from a copy of tests/fixtures/lock/ in which the judge `viewer` is
`audience-sim`. Pull #19 (story/board ep01) is blind, its locked version is pull #4, and its key shows the new
version as A: `critic` and `elder` prefer A (new), `audience-sim` prefers B (old).
"""

import contextlib
import io
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_lockrules  # noqa: E402
from test_lockrules import NOW  # noqa: E402

from kit.lib import cmd_lock, verdicts  # noqa: E402

HEADER = "locked_at\tepisode\tstage\tpull\trole\tside\tretell"
SEEDED = (f"{NOW}\tep01\tboard\t19\taudience-sim\told\told\n"
          f"{NOW}\tep01\tboard\t19\tcritic\tnew\t-\n"
          f"{NOW}\tep01\tboard\t19\telder\tnew\t-\n")
EARLIER = "2026-11-01T09:00:00Z\tep01\tboard\t9\tcritic\tnew\t-\n"
LINE = "registry/verdicts.tsv: PR #19 board ep01, 3 judge(s)"
BAD_HEADER = ("lock: verdicts: registry/verdicts.tsv line 1: the header is not "
              "locked_at episode stage pull role side retell\n")


def fixture(test) -> Path:
    """A copy of tests/fixtures/lock/ whose judge `viewer` is `audience-sim`."""
    tmp = Path(tempfile.mkdtemp())
    test.addCleanup(shutil.rmtree, tmp)
    copy = tmp / "lock"
    shutil.copytree(test_lockrules.FIX, copy)
    roles = copy / "production" / ".factory" / "roles"
    (roles / "viewer.md").rename(roles / "audience-sim.md")
    for path in sorted(copy.rglob("*")):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            new = re.sub(r"\bVIEWER\b", "AUDIENCE-SIM", re.sub(r"\bviewer\b", "audience-sim", text))
            if new != text:
                path.write_text(new, encoding="utf-8")
    return copy


def verdict(criterion="1", result="B, the retelling keeps the turn", table=True) -> str:
    rows = ("| Criterion | Result | Command |\n|---|---|---|\n"
            f"| {criterion} | {result} | board/panel-1.md |\n| 2 | even, both hook | board/panel-2.md |\n")
    return ("[AUDIENCE-SIM] PREFERS B\nThe single panel is calmer.\n\n" + (rows if table else "")
            + "reviewed head {head:19} manifest none\n")


def world(test, pulls=(19,), **kw):
    with mock.patch.object(test_lockrules, "FIX", fixture(test)):
        w = test_lockrules.World(test, pulls)
    if 19 in pulls:
        w.edit(19, 1906, body=verdict(**kw))
    return w


def on_trunk(w):
    """The file on trunk, or None."""
    if not w.git("ls-tree", "--name-only", "trunk", "registry/verdicts.tsv").strip():
        return None
    return w.git("show", "trunk:registry/verdicts.tsv")


def written(out) -> list:
    return [line for line in out.splitlines() if line.startswith("registry/verdicts.tsv")]


class Seeded(unittest.TestCase):
    def test_a_blind_lock_with_a_key_appends_one_row_per_judge(self):
        """Behaviour 1."""
        w = world(self)
        code, out, err = w.lock("--apply")
        self.assertEqual((code, err), (0, ""))
        trunk = w.git("rev-parse", "trunk").strip()
        lines = out.splitlines()
        self.assertEqual(lines[-1], LINE)
        self.assertRegex(lines[-2], r"^locked PR #19 at [0-9a-f]{12}$")
        self.assertEqual(verdicts.HEADER, HEADER)
        self.assertEqual(on_trunk(w), HEADER + "\n" + SEEDED)
        self.assertEqual(w.git("log", "-1", "--format=%s", trunk).strip(), "registry: verdicts PR #19")
        self.assertEqual(w.git("status", "--short"), "")

    def test_the_retell_is_the_side_criterion_1_names(self):
        """Behaviour 2."""
        for criterion, result, table, expected in (
                ("1", "A, every persona retells the turn", True, "new"),
                ("1. Retell", "even: both keep the turn", True, "none"),
                ("1", "Even", True, "none"),
                ("1", "The retell of B holds", True, "-"),
                ("1", "And B", True, "-"),
                ("10", "A", True, "-"),
                ("2", "A", True, "-"),
                ("1", "B", False, "-")):
            with self.subTest(criterion=criterion, result=result, table=table):
                w = world(self, criterion=criterion, result=result, table=table)
                code, out, err = w.lock("--apply")
                self.assertEqual((code, err), (0, ""))
                self.assertIn(LINE, written(out))
                seeded = SEEDED.splitlines(keepends=True)
                self.assertEqual(on_trunk(w), HEADER + "\n" + f"{NOW}\tep01\tboard\t19\taudience-sim\told\t{expected}\n"
                                 + "".join(seeded[1:]))

    def test_an_existing_file_gets_the_rows_after_its_own(self):
        """Behaviour 3."""
        w = world(self)
        w.move_base("registry/verdicts.tsv", HEADER + "\n" + EARLIER)
        w.merge_base_into(19)
        code, out, err = w.lock("--apply")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(on_trunk(w), HEADER + "\n" + EARLIER + SEEDED)


class CleanTwins(unittest.TestCase):
    def test_a_stage_that_is_not_blind_appends_nothing(self):
        """Behaviour 4."""
        for number in (8, 6, 10):
            with self.subTest(pull=number):
                w = world(self, (number,))
                code, out, err = w.lock("--apply")
                self.assertEqual((code, err), (0, ""))
                self.assertIn(f"locked PR #{number} at ", out)
                self.assertEqual(written(out), [])
                self.assertEqual(w.git("ls-tree", "--name-only", "trunk", "registry/verdicts.tsv"), "")

    def test_no_locked_version_appends_nothing(self):
        """Behaviour 5: called as Tick.merge calls it; rule 3 would refuse the pull."""
        w = world(self)
        w.set_record("pulls", 4, state="closed", merged_at=None)
        p, state = w.load()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cmd_lock.lock(p, w.gh, state, state.tickets[18], state.pulls[19])
        self.assertEqual(code, 0)
        self.assertEqual(w.record("pulls", 19)["state"], "merged")
        self.assertEqual(written(out.getvalue()), [])
        self.assertIsNone(on_trunk(w))

    def test_without_apply_nothing_is_written(self):
        """Behaviour 7."""
        w = world(self)
        code, out, err = w.lock()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(written(out), [])
        self.assertIsNone(on_trunk(w))
        self.assertEqual(w.record("pulls", 19)["state"], "open")


class Unwritable(unittest.TestCase):
    def assertUnwritten(self, w, before, stderr):
        code, out, err = w.lock("--apply")
        self.assertEqual((code, err), (6, stderr))
        self.assertRegex(out.splitlines()[-1], r"^locked PR #19 at [0-9a-f]{12}$")
        self.assertEqual(written(out), [])
        self.assertEqual(w.record("pulls", 19)["state"], "merged")
        self.assertEqual(w.record("issues", 18)["state"], "closed")
        self.assertEqual(on_trunk(w), before)

    def test_an_uncommitted_file(self):
        """Behaviour 6."""
        w = world(self)
        w.write("registry/verdicts.tsv", HEADER + "\n")
        self.assertUnwritten(w, None, "lock: verdicts: registry/verdicts.tsv differs from the base\n")

    def test_a_file_with_another_header(self):
        """Behaviour 6."""
        w = world(self)
        w.move_base("registry/verdicts.tsv", "locked_at,episode\n")
        w.merge_base_into(19)
        self.assertUnwritten(w, "locked_at,episode\n", BAD_HEADER)


if __name__ == "__main__":
    unittest.main()
