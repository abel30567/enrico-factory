"""Tests for kit/lib/metric.py and kit/lib/cmd_metric.py: `factory metric`, the metric amendment test.

The production is tests/fixtures/lab/production, copied into a temporary directory and committed on branch
`trunk` with criterion 1's loop file `judge-calibration`, its held-out list and the real retention of four
held-out episodes. A ref is a branch with one commit on `trunk`. Nothing reaches the network or `gh`.
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import cmd_metric, gitstate  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "lab" / "production"
NOW = "2026-11-13T09:00:00Z"
IDENTITY = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")
LOOP = "judge-calibration"
LOOP_FILE = "lab/loops/judge-calibration.json"
HELD_OUT = "lab/held-out/judge-calibration.json"
TSV = "lab/metric-amendments/judge-calibration.tsv"
SPEC = {"metric": "agreement", "better": "higher", "command": [".factory/factory", "agreement", LOOP],
        "controls": 5, "drift_every": 10, "ticket": 1}
EPISODES = ["ep02", "ep03", "ep04", "ep05"]
REAL = {"ep02": 0.62, "ep03": 0.48, "ep04": 0.35, "ep05": 0.21}
HEADER = "episode\tagreement\tagreement_by_beat\n"
ROWS = ["ep02\t0.5\t0.9", "ep03\t0.6\t0.7", "ep04\t0.4\t0.5", "ep05\t0.3\t0.2"]
GOOD = HEADER + "".join(r + "\n" for r in ROWS)

COUNTS = ("metric judge-calibration: 4 held-out episodes, 6 pairs of different real retention, "
          "0 of equal retention not counted\n")
OLD_5 = "metric judge-calibration agreement: 5 of 6 pairs ordered as real retention, 0.8333\n"
NEW_6 = "metric judge-calibration agreement_by_beat: 6 of 6 pairs ordered as real retention, 1.0\n"
NEW_0 = "metric judge-calibration agreement_by_beat: 0 of 6 pairs ordered as real retention, 0.0\n"
REPLACES = ("metric judge-calibration: agreement_by_beat replaces agreement: it predicts real retention better "
            "on held-out episodes\n")
ACCEPTED = COUNTS + OLD_5 + NEW_6 + REPLACES
UNCHANGED = "metric judge-calibration: agreement is unchanged\n"


def refused(b, a, counted=6):
    return (f"metric: agreement_by_beat does not predict real retention better than agreement on held-out "
            f"episodes ({b} against {a} of {counted} pairs)\n")


def retention_json(ep, last):
    data = {"beats": [{"beat": "B1", "end": 5, "retention": 0.9, "start": 0},
                      {"beat": "B2", "end": 10, "retention": last, "start": 5}],
            "post": f"{ep}/youtube", "post_id": "inventedVid", "snapshot_sha256": "0" * 64}
    return json.dumps(data, indent=1, sort_keys=True) + "\n"


def loop_json(**changes):
    return json.dumps({**SPEC, **changes}) + "\n"


def held_out_json(episodes=EPISODES):
    return json.dumps({"episodes": episodes, "judge": "audience-sim", "platform": "youtube"}) + "\n"


def comparison(rows, header=HEADER):
    return header + "".join(r + "\n" for r in rows)


class World:
    """Criterion 1's production repository on branch `trunk`, in a temporary directory."""

    def __init__(self, test, real=None, held_out=True, extra=None):
        tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, tmp)
        self.root = tmp / "show"
        patch = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
        patch.start()
        test.addCleanup(patch.stop)
        for name in IDENTITY:
            os.environ.pop(name, None)
        shutil.copytree(FIX, self.root)
        self.git("init", "-q", "-b", "trunk")
        files = {LOOP_FILE: loop_json()}
        if held_out:
            files[HELD_OUT] = held_out_json()
        for ep, value in sorted((real or REAL).items()):
            files[f"distribution/{ep}/youtube/retention.json"] = retention_json(ep, value)
        files.update(extra or {})
        for rel, text in sorted(files.items()):
            self.write(rel, text)
        self.commit("base")

    def git(self, *args):
        return gitstate.git(self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", *args,
                            env={"GIT_AUTHOR_DATE": NOW, "GIT_COMMITTER_DATE": NOW})

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text)

    def ref(self, name="amend", files=None, gone=()):
        """A branch ``name`` with one commit on `trunk` writing ``files`` and deleting ``gone``."""
        self.git("checkout", "-q", "-b", name, "trunk")
        for rel, text in sorted((files or {}).items()):
            self.write(rel, text)
        for rel in gone:
            (self.root / rel).unlink()
        self.commit("ref " + name)
        self.git("checkout", "-q", "trunk")
        return name

    def amend(self, tsv=GOOD, name="amend", **changes):
        """A ref that renames the metric to `agreement_by_beat`, with ``changes``, and the comparison ``tsv``."""
        files = {LOOP_FILE: loop_json(**{"metric": "agreement_by_beat", **changes})}
        if tsv is not None:
            files[TSV] = tsv
        return self.ref(name, files)

    def metric(self, *argv):
        """``(exit, stdout, stderr)`` of ``factory metric`` run in-process."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_metric.main(list(argv) + ["--root", str(self.root)])
        return code, out.getvalue(), err.getvalue()


class Case(unittest.TestCase):
    def setUp(self):
        self.world = World(self)

    def run_ref(self, ref="amend", loop=LOOP, world=None):
        return (world or self.world).metric(loop, ref)

    def assertAccepted(self, ref="amend", world=None, out=ACCEPTED):
        self.assertEqual(self.run_ref(ref, world=world), (0, out, ""))

    def assertRefused(self, message, ref="amend", loop=LOOP, world=None):
        self.assertEqual(self.run_ref(ref, loop, world), (2, "", f"metric: {message}\n"))


class TheRefusal(Case):
    """Behaviour 1: a metric change without its held-out comparison is refused."""

    def test_no_comparison_is_refused(self):
        self.world.amend(tsv=None)
        self.assertRefused("lab/metric-amendments/judge-calibration.tsv is missing at amend")

    def test_an_earlier_comparison_on_the_base_is_not_this_one(self):
        world = World(self, extra={TSV: "episode\tcatch_rate\tagreement\n" + "".join(
            f"{ep}\t0.1\t0.2\n" for ep in EPISODES)})
        world.amend(tsv=None)
        self.assertRefused("lab/metric-amendments/judge-calibration.tsv line 1: header is not episode, "
                           "agreement, agreement_by_beat", world=world)


class Accepted(Case):
    """Behaviour 2: criterion 2's amendment."""

    def test_the_new_metric_orders_more_pairs(self):
        self.world.amend()
        self.assertAccepted()

    def test_two_runs_print_the_same_bytes(self):
        self.world.amend()
        self.assertEqual(self.run_ref(), self.run_ref())

    def test_nothing_is_written_in_the_production(self):
        self.world.amend()
        self.run_ref()
        self.assertEqual(gitstate.status(self.world.root), [])


class NotBetter(Case):
    """Behaviour 3."""

    def test_a_tie_is_refused(self):
        self.world.amend(comparison([r.rsplit("\t", 1)[0] + "\t" + r.split("\t")[1] for r in ROWS]))
        self.assertEqual(self.run_ref(), (2, COUNTS + OLD_5 + NEW_6.replace("6 of 6", "5 of 6").replace(
            "1.0", "0.8333"), refused(5, 5)))

    def test_a_reversed_column_is_refused(self):
        self.world.amend(comparison(["ep02\t0.5\t0.2", "ep03\t0.6\t0.5", "ep04\t0.4\t0.7", "ep05\t0.3\t0.9"]))
        self.assertEqual(self.run_ref(), (2, COUNTS + OLD_5 + NEW_0, refused(0, 5)))


class Direction(Case):
    """Behaviour 4: each metric is read with its own loop file's `better`."""

    def test_lower_with_the_same_column_is_refused(self):
        self.world.amend(better="lower")
        self.assertEqual(self.run_ref(), (2, COUNTS + OLD_5 + NEW_0, refused(0, 5)))

    def test_lower_with_the_column_negated_is_accepted(self):
        self.world.amend(comparison(["ep02\t0.5\t-0.9", "ep03\t0.6\t-0.7", "ep04\t0.4\t-0.5", "ep05\t0.3\t-0.2"]),
                         better="lower")
        self.assertAccepted()


class Ties(Case):
    """Behaviour 5: a pair of equal real retention is not counted."""

    def test_a_pair_of_equal_retention_is_not_counted(self):
        world = World(self, real={**REAL, "ep04": 0.48})
        world.amend()
        self.assertAccepted(world=world, out=(
            "metric judge-calibration: 4 held-out episodes, 5 pairs of different real retention, "
            "1 of equal retention not counted\n"
            "metric judge-calibration agreement: 4 of 5 pairs ordered as real retention, 0.8\n"
            "metric judge-calibration agreement_by_beat: 5 of 5 pairs ordered as real retention, 1.0\n" + REPLACES))

    def test_every_retention_equal_is_refused(self):
        world = World(self, real={ep: 0.5 for ep in EPISODES})
        world.amend()
        self.assertRefused("the held-out episodes of loop judge-calibration all have the same real retention",
                           world=world)


class Unchanged(Case):
    """Behaviour 6: no metric change, nothing else read."""

    def setUp(self):
        self.world = World(self, held_out=False)

    def test_a_change_of_controls_only(self):
        self.world.ref(files={LOOP_FILE: loop_json(controls=6)})
        self.assertEqual(self.run_ref(), (0, UNCHANGED, ""))

    def test_the_base_itself(self):
        self.assertEqual(self.run_ref("trunk"), (0, UNCHANGED, ""))


class Frozen(Case):
    """Behaviour 7: the held-out list is the base's, and a metric keeps its name only if it is unchanged."""

    FROZEN = "lab/held-out/judge-calibration.json differs at amend: the held-out episodes are frozen for an amendment"

    def test_a_changed_held_out_list(self):
        self.world.ref(files={LOOP_FILE: loop_json(metric="agreement_by_beat"), TSV: GOOD,
                              HELD_OUT: held_out_json(["ep02", "ep03", "ep04", "ep06"])})
        self.assertRefused(self.FROZEN)

    def test_a_held_out_list_the_base_lacks(self):
        world = World(self, held_out=False)
        world.ref(files={LOOP_FILE: loop_json(metric="agreement_by_beat"), TSV: GOOD, HELD_OUT: held_out_json()})
        self.assertRefused(self.FROZEN, world=world)

    def test_a_changed_better_under_the_same_name(self):
        self.world.ref(files={LOOP_FILE: loop_json(better="lower"), TSV: GOOD})
        self.assertRefused("lab/loops/judge-calibration.json at amend changes the metric agreement and keeps its name")

    def test_a_changed_command_under_the_same_name(self):
        self.world.ref(files={LOOP_FILE: loop_json(command=["python3", "other.py"]), TSV: GOOD})
        self.assertRefused("lab/loops/judge-calibration.json at amend changes the metric agreement and keeps its name")


class TheComparison(Case):
    """Behaviour 8: every refusal of the comparison file, in ruling 8's order."""

    def assertFile(self, tsv, message):
        self.world.amend(tsv)
        self.assertRefused(message)

    def test_not_utf8(self):
        self.assertFile(b"\xff", f"{TSV} is not UTF-8 text")

    def test_empty(self):
        self.assertFile("", f"{TSV} line 1: header is not episode, agreement, agreement_by_beat")

    def test_wrong_header(self):
        self.assertFile(comparison(ROWS, "episode\tagreement_by_beat\tagreement\n"),
                        f"{TSV} line 1: header is not episode, agreement, agreement_by_beat")

    def test_two_cells(self):
        self.assertFile(comparison(ROWS[:1] + ["ep03\t0.6"] + ROWS[2:]), f"{TSV} line 3: 2 cells, not 3")

    def test_not_held_out(self):
        self.assertFile(comparison(ROWS + ["ep06\t0.1\t0.1"]), f"{TSV} line 6: episode ep06 is not held out")

    def test_repeated(self):
        self.assertFile(comparison(ROWS[:2] + ["ep03\t0.6\t0.7"] + ROWS[2:]),
                        f"{TSV} line 4: episode ep03 is repeated (first on line 3)")

    def test_old_value_not_a_number(self):
        self.assertFile(comparison(["ep02\t.7\t0.9"] + ROWS[1:]), f"{TSV} line 2: .7 is not a number")

    def test_new_value_not_a_number(self):
        self.assertFile(comparison(ROWS[:3] + ["ep05\t0.3\t1e-3"]), f"{TSV} line 5: 1e-3 is not a number")

    def test_missing_row(self):
        self.assertFile(comparison(ROWS[:3]), f"{TSV}: no row for held-out episode ep05")

    def test_rows_in_any_order(self):
        self.world.amend(comparison(list(reversed(ROWS))))
        self.assertAccepted()


class WhatAgreementRefuses(Case):
    """Behaviour 9: the refusals of `agreement.held_out` and `agreement.retention`, read on the base."""

    def test_no_held_out_list_on_the_base(self):
        world = World(self, held_out=False)
        world.amend()
        self.assertRefused("lab/held-out/judge-calibration.json is missing", world=world)

    def test_a_null_last_retention(self):
        world = World(self, real={**REAL, "ep05": None})
        world.amend()
        self.assertRefused("post ep05/youtube has no retention on its last beat B2", world=world)

    def test_the_ref_retention_is_not_read(self):
        self.world.ref(files={LOOP_FILE: loop_json(metric="agreement_by_beat"), TSV: GOOD,
                              "distribution/ep05/youtube/retention.json": retention_json("ep05", 0.99)})
        self.assertAccepted()


class Interface(Case):
    """Behaviour 10."""

    def test_help(self):
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as caught:
            cmd_metric.main(["--help"])
        self.assertEqual(caught.exception.code, 0)

    def test_no_arguments(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            cmd_metric.main([])
        self.assertEqual(caught.exception.code, 2)

    def test_not_a_loop_id(self):
        self.world.amend()
        self.assertRefused("'Bad' is not a loop id", loop="Bad")

    def test_not_a_commit(self):
        self.assertRefused("nosuch is not a commit", ref="nosuch")

    def test_the_ref_deletes_the_loop_file(self):
        self.world.ref("gone", gone=[LOOP_FILE])
        self.assertRefused("lab/loops/judge-calibration.json is not on gone", ref="gone")

    def test_the_base_lacks_the_loop_file(self):
        self.assertRefused("lab/loops/other.json is not on trunk", ref="trunk", loop="other")


if __name__ == "__main__":
    unittest.main()
