"""Tests for rule 13 (``keep``) of kit/lib/lockrules.py on a retro change (ISSUE-371).

The world is ``test_lock_lab.LabWorld`` whose KEEP commit appends a line to the writer's role file, with no
results log of its own. On ``trunk`` it then gains the loop file tests/fixtures/lock_retro/craft.json, the
proposal tests/fixtures/lock_retro/tighter-lines.md (``roles: writer``) and ``lab/results.tsv`` with five
``craft`` no-op controls and a ``craft`` KEEP of the pull's commit; the base is merged into pull #31 and
the pull is approved again.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lab import BASELINE  # noqa: E402
from test_lock_lab import PULL, STRUCTURE, WRITER, LabTest, LabWorld, append  # noqa: E402
from test_lockrules import NOW, ROOT  # noqa: E402

from kit.lib import gitstate, lab, lockrules  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "lock_retro"
LOOP_FILE = "lab/loops/craft.json"
PROPOSAL = "lab/proposals/tighter-lines.md"
NO_CRAFT = ("no KEEP of loop craft in lab/results.tsv on trunk names a commit of PR #31, "
            f"which changes a role file a proposal names: {WRITER}")
REPORT = "PR #31 #30 story/config checker-strength"


def row(loop, kind, commit, values, best="-", threshold="-", verdict="-"):
    return {"time": NOW, "loop": loop, "kind": kind, "commit": commit, "values": values, "best": best,
            "threshold": threshold, "verdict": verdict}


def log(rows):
    return lab.HEADER + "\n" + "".join(lab.line(r) + "\n" for r in rows)


class RetroWorld(LabWorld):
    """The world of the module docstring."""

    def __init__(self, test):
        LabWorld.__init__(self, test, keep={WRITER: append(WRITER, "Cut each line to ten words.\n")}, log=None)
        self.trunk = gitstate.head(self.root, "trunk")
        self.write(LOOP_FILE, (FIX / "craft.json").read_text())
        self.write(PROPOSAL, (FIX / "tighter-lines.md").read_text())
        self.write(lab.PATH, log(self.controls("craft") + [self.keep_row("craft")]))
        self.commit("the craft loop, a proposal and the results log")
        self.merge_base_into(PULL)
        self.approve()

    def controls(self, loop, values=BASELINE):
        return [row(loop, "control", self.trunk, value) for value in values]

    def keep_row(self, loop, **fields):
        return {**row(loop, "run", self.keep, "0.6,0.61", "0.5", "0.0316", "KEEP"), **fields}

    def defect(self, rel, text):
        """``rel`` on ``trunk`` becomes ``text`` (None removes it), merged into the pull and approved."""
        self.move_base(rel, text)
        self.merge_base_into(PULL)
        self.approve()

    def strength_only(self):
        """The world of test 2: the log holds a ``checker-strength`` epoch and KEEP, and no ``craft`` row."""
        self.defect(lab.PATH, log(self.controls("checker-strength") + [self.keep_row("checker-strength")]))


class CleanTest(LabTest):
    def test_1_clean_twin(self):
        w = RetroWorld(self)
        self.assertTrue(lockrules.lockable(w.rules(PULL)))
        p, _ = w.load()
        self.assertEqual(lockrules._proposed(p, w.root, "trunk", [WRITER, STRUCTURE]), ([WRITER], ""))


class DefectTest(LabTest):
    def test_2_a_keep_of_another_loop_does_not_ship_a_proposal(self):
        w = RetroWorld(self)
        w.strength_only()
        self.only(w, {13}, {13: NO_CRAFT})

    def test_3_other_lab_pulls_lock_as_today(self):
        for name, rel, text in (("no proposal", PROPOSAL, None),
                                ("another room's roles", PROPOSAL, "---\nroles: painter, keeper\n---\nText.\n")):
            with self.subTest(name):
                w = RetroWorld(self)
                w.strength_only()
                w.defect(rel, text)
                self.assertTrue(lockrules.lockable(w.rules(PULL)))
        with self.subTest("no role file, a broken proposal"):
            w = LabWorld(self)
            w.move_base("lab/proposals/broken.md", "Not front matter.\n")
            w.merge_base_into(PULL)
            w.approve()
            self.assertTrue(lockrules.lockable(w.rules(PULL)))

    def test_4_a_craft_keep_that_does_not_count(self):
        cases = (
            ("one value", lambda w: w.controls("craft") + [w.keep_row("craft", values="0.6")],
             "it has 1 value(s), not 2"),
            ("four controls", lambda w: w.controls("craft", BASELINE[:4]) + [w.keep_row("craft")],
             "4 of 5 no-op controls before it"),
            ("a rebaseline", lambda w: w.controls("craft")
             + [row("craft", "control", w.trunk, "0.5", verdict=lab.REBASELINE), w.keep_row("craft")],
             "0 of 5 no-op controls before it"),
            ("another threshold", lambda w: w.controls("craft") + [w.keep_row("craft", threshold="0.02")],
             "its threshold 0.02 is not 0.0316, twice the noise floor before it"),
        )
        for name, rows, why in cases:
            with self.subTest(name):
                w = RetroWorld(self)
                w.defect(lab.PATH, log(rows(w)))
                self.only(w, {13}, {13: f"KEEP {w.keep} of loop craft does not count: {why}"})

    def test_5_no_loop_file(self):
        w = RetroWorld(self)
        w.defect(LOOP_FILE, None)
        self.only(w, {13}, {13: "lab/loops/craft.json is not on trunk"})

    def test_6_broken_proposals(self):
        for text, reason in (("roles: writer\n", f"{PROPOSAL} on trunk: line 1: missing opening ---"),
                             ("---\nloop: craft\n---\nText.\n", f"{PROPOSAL} on trunk names no role in roles:")):
            with self.subTest(text=text):
                w = RetroWorld(self)
                w.defect(PROPOSAL, text)
                self.only(w, {13}, {13: reason})

    def test_7_the_change_still_has_to_match(self):
        for more in (True, False):
            with self.subTest(more=more):
                w = RetroWorld(self)
                w.defect(lab.PATH, log(w.controls("craft") + [w.keep_row("checker-strength"), w.keep_row("craft")]))
                if not more:
                    self.assertTrue(lockrules.lockable(w.rules(PULL)))
                    continue
                w.push(PULL, STRUCTURE, append(STRUCTURE, "# more\n"))
                w.approve()
                self.only(w, {13}, {13: f"PR #31 differs from KEEP {w.keep} of loop craft: {STRUCTURE}"})

    def test_8_the_report(self):
        w = RetroWorld(self)
        w.strength_only()
        self.assertEqual(w.lock("--room", "story"), (0, f"{REPORT}: NO 1 rule(s)\n  rule 13 keep: {NO_CRAFT}\n", ""))


if __name__ == "__main__":
    unittest.main()
