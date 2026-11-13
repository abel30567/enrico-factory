"""Tests for kit/lib/lab.py and kit/lib/cmd_lab.py: `factory lab control` and `factory lab run`, end to end.

The production is tests/fixtures/lab/production, made into a git repository on branch `trunk` in a
temporary directory; GitHub is a copy of tests/fixtures/lab/github. The metric is the fixture's
`lab/metrics/scripted.py`, which pops one line of the file `$SCRIPTED_METRIC` per measure.
Every test sets ``FACTORY_GITHUB`` and ``FACTORY_NOW``: nothing reaches the network or ``gh``.
"""

import contextlib
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import cmd_lab, gitstate, github, lab  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "lab"
NOW = "2026-11-13T09:00:00Z"
LOOP = "checker-strength"
LOOP_FILE = "lab/loops/checker-strength.json"
BASELINE = ("0.50", "0.52", "0.48", "0.51", "0.49")
THRESHOLD_LINE = "lab checker-strength threshold 0.0316: twice the noise floor 0.0158 over 5 controls, best 0.5"
IDENTITY = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")


class World:
    """A production repository on branch `trunk`, a recorded GitHub and a metric script, all temporary."""

    def __init__(self, test):
        self.test = test
        tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, tmp)
        self.root = tmp / "show"
        self.dir = tmp / "github"
        self.script = tmp / "script.txt"
        self.script.write_text("")
        patch = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                                             "FACTORY_GITHUB": str(self.dir), "FACTORY_NOW": NOW,
                                             "SCRIPTED_METRIC": str(self.script)})
        patch.start()
        test.addCleanup(patch.stop)
        for name in IDENTITY:
            os.environ.pop(name, None)
        shutil.copytree(FIX / "production", self.root)
        self.git("init", "-q", "-b", "trunk")
        self.commit("the production")
        shutil.copytree(FIX / "github", self.dir)
        self.gh = github.Recorded(self.dir)

    def git(self, *args):
        return gitstate.git(self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", *args,
                            env={"GIT_AUTHOR_DATE": NOW, "GIT_COMMITTER_DATE": NOW})

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def ref(self, name="experiment", files=None):
        """A branch ``name`` with one commit on top of `trunk` writing ``files``; its full sha."""
        self.git("checkout", "-q", "-b", name, "trunk")
        for rel, text in sorted((files or {"work/change.md": name + "\n"}).items()):
            self.write(rel, text)
        self.commit("experiment " + name)
        sha = gitstate.head(self.root, name)
        self.git("checkout", "-q", "trunk")
        return sha

    def loop_file(self, name, **changes):
        """Commit on `trunk` a copy of the clean loop file named ``name``, with ``changes``."""
        spec = json.loads((FIX / "production" / LOOP_FILE).read_text())
        spec.update(changes)
        self.write(f"lab/loops/{name}.json", json.dumps(spec))
        self.commit("loop " + name)

    def plan(self, *values):
        self.script.write_text("".join(v + "\n" for v in values))

    def lab(self, *argv, apply=True, script=None):
        """``(exit, stdout, stderr)`` of ``factory lab`` run in-process."""
        if script is not None:
            self.plan(*script)
        out, err = io.StringIO(), io.StringIO()
        full = list(argv) + ["--root", str(self.root)] + (["--apply"] if apply else [])
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_lab.main(full)
        return code, out.getvalue(), err.getvalue()

    def control(self, value, loop=LOOP, apply=True):
        return self.lab("control", loop, apply=apply, script=[value])

    def baseline(self, loop=LOOP, values=BASELINE):
        for value in values:
            code, out, err = self.control(value, loop)
            self.test.assertEqual((code, err), (0, ""), out)
        return out

    def run(self, ref, *script, loop=LOOP, apply=True):
        return self.lab("run", loop, ref, apply=apply, script=list(script))

    def rows(self, loop=LOOP):
        text = gitstate.show(self.root, "trunk", lab.PATH)
        rows = [] if text is None else lab.read(text)
        return [r for r in rows if r["loop"] == loop]

    def results(self):
        return gitstate.show(self.root, "trunk", lab.PATH)

    def posts(self):
        path = self.dir / "log.jsonl"
        log = [json.loads(l) for l in path.read_text().splitlines()] if path.is_file() else []
        return [entry for entry in log if entry["op"] == "post"]

    def log_text(self):
        path = self.dir / "log.jsonl"
        return path.read_text() if path.is_file() else ""

    def trunk(self):
        return gitstate.head(self.root, "trunk")


class LabTest(unittest.TestCase):
    def setUp(self):
        self.w = World(self)

    def assertRefused(self, result, message):
        code, out, err = result
        self.assertEqual((code, out, err), (2, "", f"lab: {message}\n"))

    def assertNothingWritten(self, trunk, posts=0):
        self.assertEqual(self.w.trunk(), trunk)
        self.assertEqual(len(self.w.posts()), posts)


class ControlsTest(LabTest):
    """Behaviour 1: controls set the threshold."""

    def test_four_controls_refuse_a_run(self):
        self.w.baseline(values=BASELINE[:4])
        sha = self.w.ref()
        trunk = self.w.trunk()
        self.assertRefused(self.w.run(sha, "0.6", "0.61"),
                           "checker-strength: 4 of 5 no-op controls; run factory lab control checker-strength first")
        self.assertNothingWritten(trunk)

    def test_the_fifth_control_sets_the_threshold(self):
        out = self.w.baseline()
        base = gitstate.head(self.w.root, "trunk~1")
        self.assertEqual(out, f"lab checker-strength control {base}: - values 0.49 best - threshold -\n"
                              + THRESHOLD_LINE + "\n")
        rows = self.w.rows()
        self.assertEqual([r["values"] for r in rows], ["0.5", "0.52", "0.48", "0.51", "0.49"])
        self.assertEqual({(r["kind"], r["best"], r["threshold"], r["verdict"]) for r in rows},
                         {("control", "-", "-", "-")})
        self.assertEqual(self.w.posts(), [])

    def test_twin_a_run_is_accepted_after_five(self):
        self.w.baseline()
        code, out, err = self.w.run(self.w.ref(), "0.5")
        self.assertEqual((code, err), (0, ""))
        self.assertIn(": NOISE values 0.5 best 0.5 threshold 0.0316", out)

    def test_ten_controls_make_the_baseline_of_a_ten_control_loop(self):
        self.w.loop_file("ten", controls=10)
        out = self.w.baseline("ten", BASELINE + BASELINE)
        self.assertTrue(out.endswith("lab ten threshold 0.0298: twice the noise floor 0.0149 over 10 controls, "
                                     "best 0.5\n"), out)


class KeepTest(LabTest):
    """Behaviour 2: KEEP is re-run once."""

    def test_keep(self):
        self.w.baseline()
        sha = self.w.ref()
        code, out, err = self.w.run(sha, "0.6", "0.61")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, f"lab checker-strength run {sha}: KEEP values 0.6,0.61 best 0.5 threshold 0.0316\n")
        row = self.w.rows()[-1]
        self.assertEqual([row[c] for c in lab.COLUMNS],
                         [NOW, LOOP, "run", sha, "0.6,0.61", "0.5", "0.0316", "KEEP"])
        posts = self.w.posts()
        self.assertEqual(len(posts), 1)
        self.assertEqual((posts[0]["number"], posts[0]["role"]), (1, "program:lab"))
        self.assertEqual(posts[0]["body"], "\n".join([
            "[AUTORESEARCH] KEEP",
            "",
            "loop: checker-strength",
            "metric: catch_rate (higher is better)",
            f"commit: {sha}",
            "runs: 0.6, 0.61",
            "best: 0.5",
            "noise floor: 0.0158 over 5 controls",
            "threshold: 0.0316",
            f"results: {NOW}\tchecker-strength\trun\t{sha}\t0.6,0.61\t0.5\t0.0316\tKEEP",
        ]) + "\n")
        self.assertNotIn("reviewed head", posts[0]["body"])
        self.w.run(sha, "0.6")
        self.assertEqual(self.w.rows()[-1]["best"], "0.605")

    def test_twin_a_second_value_inside_the_threshold_is_noise(self):
        self.w.baseline()
        sha = self.w.ref()
        code, out, _ = self.w.run(sha, "0.6", "0.52")
        self.assertEqual(out, f"lab checker-strength run {sha}: NOISE values 0.6,0.52 best 0.5 threshold 0.0316\n")
        self.assertEqual(self.w.posts()[-1]["body"].split("\n")[0], "[AUTORESEARCH] NOISE")
        self.w.run(sha, "0.5")
        self.assertEqual(self.w.rows()[-1]["best"], "0.5")


class EdgeTest(LabTest):
    """Behaviour 3: a gain equal to the threshold is NOISE, measured once."""

    def test_the_edges_are_noise(self):
        self.w.baseline()
        sha = self.w.ref()
        for value in ("0.5316", "0.4684"):
            code, out, _ = self.w.run(sha, value, "0.9")
            self.assertEqual(out, f"lab checker-strength run {sha}: NOISE values {value} best 0.5 threshold 0.0316\n")
            self.assertEqual(self.w.script.read_text(), "0.9\n")


class DiscardTest(LabTest):
    """Behaviour 4: worse than the best by more than the threshold, or a broken floor."""

    def verdict(self, *script):
        sha = self.w.ref()
        _, out, _ = self.w.run(sha, *script)
        return out.split(f"run {sha}: ", 1)[1].split(" best ")[0]

    def test_discard(self):
        self.w.baseline()
        self.assertEqual(self.verdict("0.46"), "DISCARD values 0.46")

    def test_a_broken_floor_is_discard(self):
        self.w.baseline()
        self.assertEqual(self.verdict("floor"), "DISCARD values floor")
        self.assertEqual(self.w.posts()[-1]["body"].split("\n")[5], "runs: floor")

    def test_a_rerun_that_breaks_the_floor_is_discard(self):
        self.w.baseline()
        self.assertEqual(self.verdict("0.6", "floor"), "DISCARD values 0.6,floor")
        self.assertEqual(self.w.rows()[-1]["values"], "0.6,floor")

    def test_twin_just_inside_is_noise(self):
        self.w.baseline()
        self.assertEqual(self.verdict("0.47"), "NOISE values 0.47")


class LowerTest(LabTest):
    """Behaviour 5: lower is better."""

    def test_lower_is_better(self):
        self.w.loop_file("render-cost", better="lower")
        self.w.baseline("render-cost")
        sha = self.w.ref()
        _, out, _ = self.w.run(sha, "0.4", "0.39", loop="render-cost")
        self.assertIn(": KEEP values 0.4,0.39 best 0.5 ", out)
        _, out, _ = self.w.run(sha, "0.6", loop="render-cost")
        self.assertIn(": DISCARD values 0.6 best 0.395 ", out)
        self.assertEqual(self.w.posts()[0]["body"].split("\n")[3], "metric: catch_rate (lower is better)")


class DriftTest(LabTest):
    """Behaviour 6: a no-op control every drift_every runs."""

    def ten_runs(self):
        self.w.baseline()
        sha = self.w.ref()
        for _ in range(10):
            self.assertEqual(self.w.run(sha, "0.5")[0], 0)
        return sha

    def test_a_control_is_due_after_ten_runs(self):
        sha = self.ten_runs()
        trunk, posts = self.w.trunk(), len(self.w.posts())
        self.assertRefused(self.w.run(sha, "0.5"),
                           "checker-strength: a no-op control is due after 10 runs; "
                           "run factory lab control checker-strength first")
        self.assertNothingWritten(trunk, posts)

    def test_a_steady_control_lets_runs_go_on(self):
        sha = self.ten_runs()
        posts = len(self.w.posts())
        code, out, _ = self.w.control("0.51")
        self.assertEqual(code, 0)
        self.assertIn(": - values 0.51 best 0.5 threshold 0.0316\n", out)
        self.assertEqual(len(self.w.posts()), posts)
        self.assertEqual(self.w.run(sha, "0.5")[0], 0)

    def test_drift_rebaselines(self):
        self.w.baseline()
        sha = self.w.ref()
        self.w.run(sha, "0.6", "0.61")
        code, out, _ = self.w.control("0.7")
        self.assertEqual(code, 0)
        self.assertIn(": REBASELINE values 0.7 best 0.605 threshold 0.0316\n", out)
        body = self.w.posts()[-1]["body"]
        self.assertEqual(body.split("\n")[0], "[AUTORESEARCH] REBASELINE")
        self.assertIn("noise floor: 0.0158 over 5 controls\n", body)
        self.assertRefused(self.w.run(sha, "0.5"),
                           "checker-strength: 0 of 5 no-op controls; run factory lab control checker-strength first")
        out = self.w.baseline(values=("0.70", "0.72", "0.68", "0.71", "0.69"))
        self.assertTrue(out.endswith("lab checker-strength threshold 0.0316: twice the noise floor 0.0158 "
                                     "over 5 controls, best 0.7\n"), out)
        _, out, _ = self.w.run(sha, "0.7")
        self.assertIn(": NOISE values 0.7 best 0.7 threshold 0.0316\n", out)

    def test_drift_is_from_the_mean_not_the_best(self):
        self.w.baseline()
        sha = self.w.ref()
        self.w.run(sha, "0.6", "0.61")
        _, out, _ = self.w.control("0.52")
        self.assertIn(": - values 0.52 best 0.605 ", out)


class FrozenHarnessTest(LabTest):
    """Behaviour 7: the metric and the loop file are the base's, never the ref's."""

    def test_a_changed_metric_on_the_ref_is_not_run(self):
        self.w.baseline()
        sha = self.w.ref(files={"lab/metrics/scripted.py": "print('0.99')\n"})
        _, out, _ = self.w.run(sha, "0.5")
        self.assertIn(": NOISE values 0.5 ", out)

    def test_a_changed_loop_file_on_the_ref_is_not_read(self):
        self.w.baseline()
        spec = json.loads((FIX / "production" / LOOP_FILE).read_text())
        spec.update(better="lower", controls=10)
        sha = self.w.ref(files={LOOP_FILE: json.dumps(spec)})
        _, out, _ = self.w.run(sha, "0.6", "0.61")
        self.assertIn(": KEEP values 0.6,0.61 ", out)

    def test_twin_the_tree_measured_is_the_ref(self):
        self.w.baseline()
        sha = self.w.ref(files={"score.txt": "0.9\n"})
        _, out, _ = self.w.run(sha, "tree", "tree")
        self.assertEqual(out, f"lab checker-strength run {sha}: KEEP values 0.9,0.9 best 0.5 threshold 0.0316\n")

    def test_a_control_measures_the_base(self):
        self.w.write("score.txt", "0.5\n")
        self.w.commit("a score")
        self.w.ref(files={"score.txt": "0.9\n"})
        base = self.w.trunk()
        _, out, _ = self.w.control("tree")
        self.assertIn(f"control {base}: - values 0.5 ", out)


class OwnedLogTest(LabTest):
    """Behaviour 8: only `factory lab --apply` writes the log, on the base, as `factory`."""

    def test_without_apply_nothing_is_written(self):
        self.w.baseline()
        sha = self.w.ref()
        trunk = self.w.trunk()
        dry = self.w.run(sha, "0.6", "0.61", apply=False)
        self.assertNothingWritten(trunk)
        self.assertEqual(self.w.run(sha, "0.6", "0.61"), dry)

    def test_with_apply_one_commit_of_the_log(self):
        self.w.baseline()
        sha = self.w.ref()
        before = self.w.trunk()
        self.w.run(sha, "0.6", "0.61")
        self.assertEqual(gitstate.git(self.w.root, "rev-parse", "trunk~1").strip(), before)
        shown = gitstate.git(self.w.root, "log", "-1", "--format=%an%n%s", "--name-only", "trunk")
        self.assertEqual(shown, "factory\nlab: checker-strength run KEEP\n\nlab/results.tsv\n")
        self.assertEqual(self.w.posts()[-1]["number"], 1)

    def test_the_header_comes_with_the_first_row(self):
        code, out, _ = self.w.control("0.5")
        base = gitstate.head(self.w.root, "trunk~1")
        self.assertEqual(self.w.results(), lab.HEADER + "\n" + f"{NOW}\t{LOOP}\tcontrol\t{base}\t0.5\t-\t-\t-\n")
        self.assertEqual(lab.HEADER, "time\tloop\tkind\tcommit\tvalues\tbest\tthreshold\tverdict")
        shown = gitstate.git(self.w.root, "log", "-1", "--format=%an %s", "trunk")
        self.assertEqual(shown, "factory lab: checker-strength control -\n")

    def test_defect_a_working_tree_log_that_differs(self):
        self.w.control("0.5")
        self.w.write(lab.PATH, lab.HEADER + "\n")
        trunk = self.w.trunk()
        self.assertRefused(self.w.control("0.5"), "lab/results.tsv differs from the base")
        self.assertNothingWritten(trunk)

    def test_defect_not_on_the_base(self):
        self.w.ref("other")
        self.w.git("checkout", "-q", "other")
        trunk = self.w.trunk()
        self.assertRefused(self.w.control("0.5"), "the checkout is not on trunk with a clean index")
        self.assertNothingWritten(trunk)

    def test_defect_a_staged_file(self):
        self.w.write("notes.md", "x\n")
        self.w.git("add", "notes.md")
        trunk = self.w.trunk()
        self.assertRefused(self.w.control("0.5"), "the checkout is not on trunk with a clean index")
        self.assertNothingWritten(trunk)

    def test_the_log_is_read_on_the_base_not_on_disk(self):
        self.w.baseline(values=BASELINE[:4])
        self.w.write(lab.PATH, "not a log\n")
        sha = self.w.ref()
        self.assertRefused(self.w.run(sha, "0.5", apply=False),
                           "checker-strength: 4 of 5 no-op controls; run factory lab control checker-strength first")


class SeparateLoopsTest(LabTest):
    """Behaviour 9: a loop reads its own rows."""

    def test_a_second_loop_needs_its_own_controls(self):
        self.w.loop_file("judge-calibration")
        self.w.baseline()
        sha = self.w.ref()
        self.assertRefused(self.w.run(sha, "0.5", loop="judge-calibration"),
                           "judge-calibration: 0 of 5 no-op controls; "
                           "run factory lab control judge-calibration first")
        self.w.baseline("judge-calibration", ("0.30", "0.32", "0.28", "0.31", "0.29"))
        _, out, _ = self.w.run(sha, "0.3", loop="judge-calibration")
        self.assertIn(": NOISE values 0.3 best 0.3 ", out)
        _, out, _ = self.w.run(sha, "0.5")
        self.assertIn(": NOISE values 0.5 best 0.5 ", out)


class RefusalTest(LabTest):
    """Behaviour 10: every refusal exits 2 and writes nothing."""

    def refused(self, argv, message, script=(), apply=True):
        trunk, posts = self.w.trunk(), len(self.w.posts())
        self.assertRefused(self.w.lab(*argv, apply=apply, script=list(script)), message)
        self.assertNothingWritten(trunk, posts)

    def test_twin_the_clean_fixture(self):
        code, out, err = self.w.control("0.5")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.startswith("lab checker-strength control "), out)

    def test_not_a_loop_id(self):
        self.refused(["control", "Checker_Strength"], "'Checker_Strength' is not a loop id")
        self.refused(["control", "../x"], "'../x' is not a loop id")

    def test_no_loop_file(self):
        self.refused(["control", "render-cost"], "lab/loops/render-cost.json is not on trunk")

    def test_a_loop_file_on_disk_only_is_not_a_loop(self):
        self.w.write("lab/loops/render-cost.json", (FIX / "production" / LOOP_FILE).read_text())
        self.refused(["control", "render-cost"], "lab/loops/render-cost.json is not on trunk")

    def test_bad_loop_files(self):
        shape = "lab/loops/bad.json is not {metric, better, command, controls, drift_every, ticket}"
        for changes in ({"metric": "catch rate"}, {"metric": ""}, {"better": "more"}, {"command": []},
                        {"command": "python3"}, {"command": ["python3", ""]}, {"controls": 4},
                        {"controls": 11}, {"controls": True}, {"drift_every": 9}, {"drift_every": 16},
                        {"ticket": 0}, {"ticket": "1"}, {"extra": 1}):
            with self.subTest(changes=changes):
                self.w.loop_file("bad", **changes)
                self.refused(["control", "bad"], shape)
        self.w.write("lab/loops/bad.json", "not json")
        self.w.commit("bad json")
        self.refused(["control", "bad"], shape)
        spec = json.loads((FIX / "production" / LOOP_FILE).read_text())
        del spec["ticket"]
        self.w.write("lab/loops/bad.json", json.dumps(spec))
        self.w.commit("no ticket")
        self.refused(["control", "bad"], shape)

    def test_bad_header(self):
        self.w.write(lab.PATH, "time\tloop\n")
        self.w.commit("a bad log")
        self.refused(["control", LOOP], "results line 1: the header is not "
                                        "time loop kind commit values best threshold verdict", ["0.5"])

    def test_bad_columns(self):
        self.w.write(lab.PATH, lab.HEADER + "\n" + "a\tb\tc\n")
        self.w.commit("a bad log")
        self.refused(["control", LOOP], "results line 2: 3 columns, not 8", ["0.5"])

    def test_the_base_head_is_a_control(self):
        self.w.baseline()
        self.refused(["run", LOOP, "trunk"], "checker-strength: trunk is the base head; a no-op is a control",
                     ["0.6", "0.61"])

    def test_run_needs_a_ref(self):
        self.refused(["run", LOOP], "checker-strength: run needs a ref", ["0.5"])

    def test_a_control_takes_no_ref(self):
        sha = self.w.ref()
        self.refused(["control", LOOP, sha], "checker-strength: a control takes no ref", ["0.5"])

    def test_the_metric_exited(self):
        self.w.loop_file("crash", command=["python3", "-c", "import sys; sys.exit(3)"])
        self.refused(["control", "crash"], "crash: the metric exited 3")
        self.refused(["control", LOOP], "checker-strength: the metric exited 3", [])

    def test_the_metric_printed_no_number(self):
        self.w.loop_file("words", command=["python3", "-c", "print('0.5'); print('done')"])
        self.refused(["control", "words"], "words: the metric printed no number on its last line")
        self.refused(["control", LOOP], "checker-strength: the metric printed no number on its last line",
                     ["0.5.1"])
        self.w.loop_file("silent", command=["python3", "-c", "pass"])
        self.refused(["control", "silent"], "silent: the metric printed no number on its last line")

    def test_twin_the_last_non_empty_line_is_read(self):
        self.w.loop_file("chatty", command=["python3", "-c", "print('warming'); print(' -0.25 '); print('')"])
        _, out, _ = self.w.control("unused", loop="chatty")
        self.assertIn(": - values -0.25 best - ", out)

    def test_a_metric_error_after_a_first_value_writes_nothing(self):
        self.w.baseline()
        sha = self.w.ref()
        self.refused(["run", LOOP, sha], "checker-strength: the metric exited 3", ["0.6"])

    def test_a_control_that_breaks_the_floor(self):
        self.refused(["control", LOOP], "checker-strength: the no-op control breaks a frozen floor (metric exit 1)",
                     ["floor"])

    def test_missing_issue(self):
        (self.w.dir / "issues" / "1.json").unlink()
        self.refused(["control", LOOP], "issue #1: not found", ["0.5"])
        self.assertEqual(self.w.script.read_text(), "0.5\n")

    def test_twin_missing_issue_without_apply(self):
        (self.w.dir / "issues" / "1.json").unlink()
        self.assertEqual(self.w.control("0.5", apply=False)[0], 0)

    def test_frozen(self):
        self.w.gh.create_issue("[FREEZE] Stop everything", "", ["factory:freeze"])
        self.refused(["control", LOOP], "frozen", ["0.5"])
        self.refused(["control", LOOP], "frozen", ["0.5"], apply=False)

    def test_twin_a_closed_freeze_issue(self):
        number = self.w.gh.create_issue("[FREEZE] Stop everything", "", ["factory:freeze"])
        self.w.gh.close_issue(number)
        self.assertEqual(self.w.control("0.5")[0], 0)

    def test_a_bad_ref_is_a_git_error(self):
        code, out, err = self.w.lab("run", LOOP, "no-such-ref", script=["0.5"])
        self.assertEqual((code, out), (2, ""))
        self.assertTrue(err.startswith("lab: git rev-parse: exit "), err)

    def test_usage(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as raised:
            cmd_lab.main(["measure", LOOP])
        self.assertEqual(raised.exception.code, 2)
        self.assertTrue(err.getvalue().startswith("usage: factory lab"))


class CommandTest(unittest.TestCase):
    """The command as `kit/factory` and `python3 kit/lib/cmd_lab.py` run it."""

    def test_help(self):
        done = subprocess.run([sys.executable, str(ROOT / "kit" / "lib" / "cmd_lab.py"), "--help"],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(done.returncode, 0)
        self.assertTrue(done.stdout.startswith("usage: factory lab"), done.stdout)
        self.assertEqual(cmd_lab.__doc__.split("\n")[0],
                         "factory lab control|run <loop> [<ref>] [--root DIR] [--apply]: one no-op control or "
                         "one experiment of a lab loop.")

    def test_listed(self):
        done = subprocess.run([sys.executable, str(ROOT / "kit" / "factory"), "help"],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertIn("  lab\n", done.stdout)


class DeterministicTest(unittest.TestCase):
    """Behaviour 11: one script, two fresh worlds, the same bytes."""

    def play(self):
        w = World(self)
        out = [w.baseline()]
        sha = w.ref(files={"score.txt": "0.9\n"})
        for script in (["0.6", "0.61"], ["0.6", "0.52"], ["0.46"], ["floor"], ["tree", "tree"]):
            out.append(w.run(sha, *script))
        out.append(w.control("0.7"))
        return out, w.results(), w.log_text(), [p["body"] for p in w.posts()]

    def test_two_worlds(self):
        first, second = self.play(), self.play()
        self.assertEqual(first, second)
        self.assertEqual(len(first[3]), 6)


if __name__ == "__main__":
    unittest.main()
