"""Tests for rule 13 (``keep``) and rule 7 for a lab pull in kit/lib/lockrules.py (ISSUE-174).

The production is tests/fixtures/lock/production as ``test_lockrules.World`` makes it, with no open pull of
its own. On ``trunk`` it gains the role tests/fixtures/lock_lab/experimenter.md (room ``lab``) and the story
stage tests/fixtures/lock_lab/config-stage.json as the first entry of its pipeline. Pull #31 (ticket #30,
story/config) has one commit on ``lab/checker-strength-1``, the KEEP commit; then ``trunk`` gets
``lab/results.tsv`` with a KEEP row naming that commit, the base is merged into the pull, and the pull has an
``[EXPERIMENTER] READY`` and an ``[EDITOR] APPROVED`` of its head.
"""

import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_lockrules  # noqa: E402
from test_lockrules import NOW, ROOT, OneRule, at  # noqa: E402

from kit.lib import gitstate, lab, lockrules, production  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "lock_lab"
PULL = 31
TICKET = 30
BRANCH = "lab/checker-strength-1"
STRUCTURE = ".factory/lib/lints/structure.py"
WRITER = ".factory/roles/writer.md"
NO_KEEP = "no KEEP in lab/results.tsv on trunk names a commit of PR #31"
REPORT = "PR #31 #30 story/config checker-strength"
APPROVED = "[EDITOR] APPROVED\nreviewed head {head:31} manifest none\n"


def append(rel, line):
    """The text of ``rel`` in the lock production with ``line`` appended."""
    return (test_lockrules.FIX / "production" / rel).read_text() + line


class LabWorld(test_lockrules.World):
    """The world of the module docstring.

    ``keep`` maps a path to the text the KEEP commit writes, by default ``# threshold 3`` appended to
    ``structure.py``; ``row`` updates the KEEP row, and a ``commit`` of None names the ``trunk`` head;
    ``log`` is the text of ``lab/results.tsv`` on ``trunk`` instead of the row, None for no file.
    """

    def __init__(self, test, keep=None, row=None, log="row"):
        super().__init__(test, pulls=())
        self.next_id = 3101
        self.write(".factory/roles/experimenter.md", (FIX / "experimenter.md").read_text())
        pipeline = json.loads((self.root / ".factory" / "pipeline.json").read_text())
        stage = json.loads((FIX / "config-stage.json").read_text())
        self.write(".factory/pipeline.json", json.dumps([stage] + pipeline, indent=1) + "\n")
        self.commit("the lab")
        self.git("checkout", "-q", "-b", BRANCH, "trunk")
        for rel, text in sorted((keep or {STRUCTURE: append(STRUCTURE, "# threshold 3\n")}).items()):
            self.write(rel, text)
        self.commit("the KEEP")
        self.keep = gitstate.head(self.root, BRANCH)
        self.git("checkout", "-q", "trunk")
        if log == "row":
            fields = {"time": NOW, "loop": "checker-strength", "kind": "run", "commit": self.keep,
                      "values": "0.6,0.61", "best": "0.5", "threshold": "0.0316", "verdict": "KEEP"}
            fields.update(row or {})
            if fields["commit"] is None:
                fields["commit"] = gitstate.head(self.root, "trunk")
            log = lab.HEADER + "\n" + lab.line(fields) + "\n"
        if log is not None:
            self.write(lab.PATH, log)
            self.commit("the results log")
        shutil.copy(FIX / "github" / "issues" / f"{TICKET}.json", self.dir / "issues" / f"{TICKET}.json")
        shutil.copy(FIX / "github" / "pulls" / f"{PULL}.json", self.dir / "pulls" / f"{PULL}.json")
        self.merge_base_into(PULL)
        self.post("experimenter", "[EXPERIMENTER] READY\nThe KEEP is on the branch.\n")
        self.approve()

    def post(self, role, body):
        self.add(PULL, self.next_id, at(9, self.next_id - 3100), role, body)
        self.next_id += 1

    def approve(self):
        self.post("editor", APPROVED)

    def reason(self, number):
        return self.rules(PULL)[number - 1].reason


class LabTest(OneRule):
    def only(self, w, expected, reasons):
        """Exactly the rules ``expected`` fail, with exactly the reasons ``{number: reason}``."""
        rules = self.assertOnly(w, PULL, expected)
        self.assertEqual({r.number: r.reason for r in rules if not r.ok}, reasons)
        return rules


class LockTest(LabTest):
    def test_1_a_keep_is_locked_by_the_target_room(self):
        w = LabWorld(self)
        rules = w.rules(PULL)
        self.assertEqual([r.number for r in rules], list(range(1, 14)))
        self.assertEqual(rules[-1].name, "keep")
        self.assertEqual([r for r in rules if not r.ok], [])
        self.assertTrue(lockrules.lockable(rules))
        code, out, err = w.lock("--room", "story", "--apply")
        self.assertEqual((code, err), (0, ""))
        lines = out.splitlines()
        self.assertEqual(len(lines), 2, out)
        self.assertEqual(lines[0], f"{REPORT}: LOCKABLE")
        self.assertRegex(lines[1], r"^locked PR #31 at [0-9a-f]{12}$")
        self.assertTrue(w.comments(PULL)[-1]["body"].startswith("[CHIEF] LOCK"))
        self.assertEqual(w.record("issues", TICKET)["state"], "closed")

    def test_1_the_pull_is_a_lab_pull(self):
        w = LabWorld(self)
        p, state = w.load()
        entry = state.pulls[PULL]
        maker = lockrules.maker_role(production.stage(p, "story", "config"), entry)
        self.assertEqual(maker, "experimenter")
        self.assertTrue(lockrules.is_lab(p, maker))
        self.assertFalse(lockrules.is_lab(p, None))
        self.assertFalse(lockrules.is_lab(p, "writer"))


class NoKeepTest(LabTest):
    def test_2_no_results_log(self):
        w = LabWorld(self, log=None)
        self.only(w, {13}, {13: NO_KEEP})
        code, out, err = w.lock("--room", "story")
        self.assertEqual((code, out, err), (0, f"{REPORT}: NO 1 rule(s)\n  rule 13 keep: {NO_KEEP}\n", ""))

    def test_2_a_noise_row_and_a_control_row(self):
        for row in ({"verdict": "NOISE"}, {"kind": "control"}):
            with self.subTest(row=row):
                self.only(LabWorld(self, row=row), {13}, {13: NO_KEEP})

    def test_3_a_keep_of_another_commit(self):
        w = LabWorld(self, row={"commit": None})
        _, state = w.load()
        kept = lab.read(gitstate.show(w.root, "trunk", lab.PATH))[0]["commit"]
        self.assertEqual(kept, gitstate.head(w.root, "trunk~1"))
        self.assertNotIn(kept, [c["sha"] for c in state.pulls[PULL]["git"]["commits"]])
        self.only(w, {13}, {13: NO_KEEP})


class ChangeTest(LabTest):
    def test_4_the_pull_must_carry_the_keeps_change(self):
        w = LabWorld(self)
        w.push(PULL, WRITER, append(WRITER, "One more line.\n"))
        w.approve()
        self.only(w, {13}, {13: f"PR #31 differs from KEEP {w.keep} of loop checker-strength: {WRITER}"})

    def test_4_a_changed_text_differs(self):
        w = LabWorld(self)
        w.push(PULL, STRUCTURE, append(STRUCTURE, "# threshold 4\n"))
        w.approve()
        self.only(w, {13}, {13: f"PR #31 differs from KEEP {w.keep} of loop checker-strength: {STRUCTURE}"})

    def test_4_twin_the_base_merged_after_the_keep_still_counts(self):
        w = LabWorld(self)
        _, state = w.load()
        commits = state.pulls[PULL]["git"]["commits"]
        self.assertEqual([c["sha"] for c in commits][0], w.keep)
        self.assertEqual(len(commits[-1]["parents"]), 2)
        w.move_base()
        w.merge_base_into(PULL)
        w.approve()
        self.assertTrue(lockrules.lockable(w.rules(PULL)))

    def test_5_a_row_on_the_branch_does_not_count(self):
        w = LabWorld(self, log=None)
        row = {"time": NOW, "loop": "checker-strength", "kind": "run", "commit": w.keep, "values": "0.6,0.61",
               "best": "0.5", "threshold": "0.0316", "verdict": "KEEP"}
        w.push(PULL, lab.PATH, lab.HEADER + "\n" + lab.line(row) + "\n")
        w.approve()
        self.only(w, {7, 13}, {7: "frozen for the lab: lab/results.tsv", 13: NO_KEEP})


class WritesTest(LabTest):
    def test_6_frozen_paths(self):
        w = LabWorld(self, keep={STRUCTURE: append(STRUCTURE, "# threshold 3\n"),
                                 "lab/loops/checker-strength.json": "{}\n", ".factory/evals/x/case.json": "{}\n"})
        self.only(w, {7}, {7: "frozen for the lab: .factory/evals/x/case.json, lab/loops/checker-strength.json"})

    def test_6_frozen_comes_first(self):
        w = LabWorld(self, keep={"notes/a.md": "A note.\n", "lab/x.md": "x\n"})
        self.assertEqual(w.reason(7), "frozen for the lab: lab/x.md; outside the config of room story: notes/a.md")

    def test_7_another_room(self):
        w = LabWorld(self, keep={".factory/roles/painter.md": append(".factory/roles/painter.md", "More.\n"),
                                 "notes/a.md": "A note.\n", WRITER: append(WRITER, "More.\n"),
                                 ".factory/lib/lints/canon.py": append(".factory/lib/lints/canon.py", "# more\n")})
        self.only(w, {7}, {7: "outside the config of room story: .factory/roles/painter.md, notes/a.md"})

    def test_7_twin_a_role_and_a_lint_of_the_room_hold(self):
        w = LabWorld(self, keep={WRITER: append(WRITER, "More.\n"),
                                 ".factory/lib/lints/canon.py": append(".factory/lib/lints/canon.py", "# more\n")})
        self.assertTrue(lockrules.lockable(w.rules(PULL)))

    def test_7_a_role_the_base_lacks_and_a_private_lint(self):
        w = LabWorld(self, keep={".factory/roles/newcomer.md": "---\nrole: newcomer\nroom: story\n---\n",
                                 ".factory/lib/lints/_util.py": "# shared\n",
                                 ".factory/roles/experimenter.md": "changed\n"})
        self.assertEqual(w.reason(7), "outside the config of room story: .factory/lib/lints/_util.py, "
                                      ".factory/roles/experimenter.md, .factory/roles/newcomer.md")

    def test_8_a_lint_of_two_rooms(self):
        w = LabWorld(self, keep={".factory/lib/lints/media.py": append(".factory/lib/lints/media.py", "# more\n")})
        pipeline = json.loads((w.root / ".factory" / "pipeline.json").read_text())
        for entry in pipeline:
            if entry["stage"] == "frames":
                entry["lint"] = ["media"]
        w.move_base(".factory/pipeline.json", json.dumps(pipeline, indent=1) + "\n")
        w.merge_base_into(PULL)
        w.approve()
        self.assertEqual(w.reason(7), "outside the config of room story: .factory/lib/lints/media.py")

    def test_8_twin_a_lint_of_one_room_holds(self):
        w = LabWorld(self, keep={".factory/lib/lints/media.py": append(".factory/lib/lints/media.py", "# more\n")})
        self.assertTrue(lockrules.lockable(w.rules(PULL)))


class LogTest(LabTest):
    def test_9_a_broken_log(self):
        w = LabWorld(self, log="time\tloop\n")
        self.only(w, {13}, {13: "lab/results.tsv on trunk: results line 1: the header is not "
                                "time loop kind commit values best threshold verdict"})


class OtherPullsTest(LabTest):
    def test_10_the_pulls_of_the_lock_fixture_are_untouched(self):
        w = test_lockrules.World(self)
        for number in test_lockrules.OPEN:
            with self.subTest(pull=number):
                rules = w.rules(number)
                self.assertEqual([r.name for r in rules], list(lockrules.NAMES))
                self.assertEqual((rules[12].ok, rules[12].reason), (True, ""))
                failed = {r.number: r.reason for r in rules if not r.ok}
                self.assertEqual(failed, {5: "ticket #15 selected PR #16"} if number == 17 else {})

    def test_10_a_seeded_defect_keeps_its_reason(self):
        w = test_lockrules.World(self, (8,), edits={8: {"notes/x.md": "x\n"}})
        rules = w.rules(8)
        self.assertEqual({r.number: r.reason for r in rules if not r.ok},
                         {7: "outside the writes of writer: notes/x.md; outside room story: notes/x.md"})

    def test_10_a_writer_ready_is_no_maker(self):
        w = LabWorld(self)
        w.replace(PULL, 3101, "[EXPERIMENTER] READY", "[WRITER] READY")
        w.edit(PULL, 3101, role="writer")
        no_maker = "no maker of stage config has posted READY"
        self.only(w, {7, 9}, {7: no_maker, 9: no_maker})


if __name__ == "__main__":
    unittest.main()
