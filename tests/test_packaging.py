"""Tests for kit/lib/packaging.py and kit/lib/cmd_packaging.py: `factory packaging assign` and `measure`, end to end.

The production is tests/fixtures/packaging/production, made into a git repository on branch `trunk` in a
temporary directory; GitHub is a copy of tests/fixtures/packaging/github. Each window is recorded: the test
writes the snapshot with `telemetry.snapshot` from the answers of tests/fixtures/tool_youtube_analytics/ and
commits it on `trunk`, one commit per post. Every test sets ``FACTORY_GITHUB`` and ``FACTORY_NOW``: nothing
reaches the network or ``gh``.
"""

import contextlib
import copy
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

from kit.lib import cmd_packaging, gitstate, github, lab, lint, packaging  # noqa: E402
from kit.lib.lints import telemetry  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "packaging"
ANSWERS = ROOT / "tests" / "fixtures" / "tool_youtube_analytics"
NOW = "2026-11-13T09:00:00Z"
LOOP = "hook-youtube"
LOOP_FILE = "lab/packaging/hook-youtube.json"
BASELINE = (30, 32, 28, 31, 29)
CONTROLS = ("ep01", "ep02", "ep03", "ep04", "ep05")
THRESHOLD_LINE = "packaging hook-youtube threshold 3.1622: twice the noise floor 1.5811 over 5 controls, best 30.0"
IDENTITY = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")
SHAPE = "is not {better, controls, drift_every, metric, platform, ticket}"


def body(name):
    return json.loads((ANSWERS / f"{name}.json").read_text())["body"]


def totals(duration):
    """A totals table shaped as totals-ok.json's body, with ``duration``; without rows when it is None."""
    table = body("totals-ok")
    if duration is None:
        del table["rows"]
    else:
        table["rows"] = [[1200, duration, 72.5]]
    return table


def window(duration, platform="youtube"):
    return telemetry.snapshot(platform, "inventedVid", "2026-11-13", "2026-11-19",
                              [totals(duration), body("retention-ok")])


class World:
    """A production repository on branch `trunk` and a recorded GitHub, both temporary."""

    def __init__(self, test):
        self.test = test
        tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, tmp)
        self.root = tmp / "show"
        self.dir = tmp / "github"
        patch = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                                             "FACTORY_GITHUB": str(self.dir), "FACTORY_NOW": NOW})
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

    def put(self, rel, text):
        """Commit ``text`` at ``rel`` on `trunk`."""
        self.write(rel, text)
        self.commit("put " + rel)

    def loop_file(self, name, **changes):
        """Commit on `trunk` a copy of the clean loop file named ``name``, with ``changes``."""
        spec = json.loads((FIX / "production" / LOOP_FILE).read_text())
        spec.update(changes)
        self.put(f"lab/packaging/{name}.json", json.dumps(spec))

    def experiment(self, change):
        self.put(f"lab/experiments/{change}.md", f"# {change}\n\nThe setting changed is the caption's `hook`.\n")

    def window(self, ep, duration, platform="youtube"):
        """The recorded window of ``ep``, committed on `trunk`."""
        self.put(f"distribution/{ep}/youtube/snapshot.json", window(duration, platform))

    def packaging(self, *argv, apply=True):
        """``(exit, stdout, stderr)`` of ``factory packaging`` run in-process."""
        out, err = io.StringIO(), io.StringIO()
        full = list(argv) + ["--root", str(self.root)] + (["--apply"] if apply else [])
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_packaging.main(full)
        return code, out.getvalue(), err.getvalue()

    def assign(self, ep, change, loop=LOOP, apply=True):
        result = self.packaging("assign", loop, ep, change, apply=apply)
        self.test.assertEqual(result[0], 0, result)
        return result

    def measure(self, loop=LOOP, apply=True):
        code, out, err = self.packaging("measure", loop, apply=apply)
        self.test.assertEqual((code, err), (0, ""), out)
        return out

    def baseline(self, loop=LOOP, eps=CONTROLS, values=BASELINE):
        for ep in eps:
            self.assign(ep, "-", loop)
        for ep, value in zip(eps, values):
            self.window(ep, value)
        return self.measure(loop)

    def rows(self, loop=LOOP):
        text = gitstate.show(self.root, "trunk", lab.PATH)
        rows = [] if text is None else lab.read(text)
        return [r for r in rows if r["loop"] == loop]

    def assignments(self):
        return gitstate.show(self.root, "trunk", packaging.PATH)

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

    def commit_of(self, n):
        """The commit that wrote line ``n`` of the assignment log."""
        out = gitstate.git(self.root, "blame", "-l", "-s", "--root", self.trunk(), "--", packaging.PATH)
        return out.splitlines()[n - 1].split()[0]


class PackagingTest(unittest.TestCase):
    def setUp(self):
        self.w = World(self)

    def assertRefused(self, result, message):
        code, out, err = result
        self.assertEqual((code, out, err), (2, "", f"packaging: {message}\n"))

    def assertNothingWritten(self, trunk, posts=0):
        self.assertEqual(self.w.trunk(), trunk)
        self.assertEqual(len(self.w.posts()), posts)

    def keep(self, second=34.5):
        """Behaviour 1's script: the baseline, then question-hook on ep06 at 35 and ep07 at ``second``."""
        for ep in CONTROLS:
            self.w.assign(ep, "-")
        self.w.assign("ep06", "question-hook")
        self.w.assign("ep07", "question-hook")
        for ep, value in zip(CONTROLS, BASELINE):
            self.w.window(ep, value)
        baseline = self.w.measure()
        self.w.window("ep06", 35)
        waits = self.w.measure()
        self.w.window("ep07", second)
        return baseline, waits, self.w.measure()


class DoneWhenTest(PackagingTest):
    """Behaviour 1: a change assigned to two posts, read on their windows, KEEP."""

    def test_keep(self):
        baseline, waits, out = self.keep()
        lines = baseline.splitlines()
        self.assertEqual(len(lines), 7, baseline)
        for n, (ep, value) in enumerate(zip(CONTROLS, ("30.0", "32.0", "28.0", "31.0", "29.0"))):
            self.assertEqual(lines[n], f"packaging hook-youtube control {ep}/youtube: - values {value} best - "
                                       "threshold -")
        self.assertEqual(lines[5], THRESHOLD_LINE)
        self.assertEqual(lines[6], "packaging hook-youtube run ep06/youtube: waits: no frozen window")
        self.assertEqual(waits, "packaging hook-youtube run ep06/youtube: waits: question-hook beats the best by 5.0; "
                                "a second post confirms it\n")
        self.assertEqual(out, "packaging hook-youtube run ep06/youtube,ep07/youtube: KEEP values 35.0,34.5 best 30.0 "
                              "threshold 3.1622\n")
        commit = self.w.commit_of(7)
        row = self.w.rows()[-1]
        self.assertEqual([row[c] for c in lab.COLUMNS],
                         [NOW, LOOP, "run", commit, "35.0,34.5", "30.0", "3.1622", "KEEP"])
        self.assertEqual(len(self.w.rows()), 6)
        posts = self.w.posts()
        self.assertEqual(len(posts), 1)
        self.assertEqual((posts[0]["number"], posts[0]["role"]), (1, "program:lab"))
        self.assertEqual(posts[0]["body"], "\n".join([
            "[AUTORESEARCH] KEEP",
            "",
            "loop: hook-youtube",
            "metric: averageViewDuration on youtube (higher is better)",
            "change: question-hook",
            "posts: ep06/youtube, ep07/youtube",
            f"commit: {commit}",
            "runs: 35.0, 34.5",
            "best: 30.0",
            "noise floor: 1.5811 over 5 controls",
            "threshold: 3.1622",
            f"results: {NOW}\thook-youtube\trun\t{commit}\t35.0,34.5\t30.0\t3.1622\tKEEP",
        ]) + "\n")
        self.assertNotIn("reviewed head", posts[0]["body"])
        self.assertEqual(lab.standing(self.w.rows(), LOOP, {"controls": 5})["best"], 34.75)

    def test_the_control_rows_name_their_assignment(self):
        self.w.baseline()
        self.assertEqual([r["commit"] for r in self.w.rows()], [self.w.commit_of(n) for n in range(2, 7)])
        self.assertEqual({(r["kind"], r["best"], r["threshold"], r["verdict"]) for r in self.w.rows()},
                         {("control", "-", "-", "-")})
        self.assertEqual(self.w.posts(), [])

    def test_twin_a_second_post_inside_the_threshold_is_noise(self):
        _, _, out = self.keep(second=31)
        self.assertEqual(out, "packaging hook-youtube run ep06/youtube,ep07/youtube: NOISE values 35.0,31.0 best 30.0 "
                              "threshold 3.1622\n")
        self.assertEqual(self.w.posts()[-1]["body"].split("\n")[0], "[AUTORESEARCH] NOISE")


class DiscardTest(PackagingTest):
    """Behaviour 2: one value is enough to lose."""

    def test_discard_after_the_keep(self):
        self.keep()
        self.w.assign("ep08", "cold-open")
        self.w.window("ep08", 25)
        self.assertEqual(self.w.measure(), "packaging hook-youtube run ep08/youtube: DISCARD values 25.0 best 34.75 "
                                           "threshold 3.1622\n")
        self.assertEqual(self.w.posts()[-1]["body"].split("\n")[0], "[AUTORESEARCH] DISCARD")

    def test_the_edges_are_noise_with_one_value(self):
        self.w.baseline()
        for ep, change, value in (("ep06", "question-hook", 33.1622), ("ep07", "cold-open", 32)):
            self.w.assign(ep, change)
            self.w.window(ep, value)
            self.assertEqual(self.w.measure(), f"packaging hook-youtube run {ep}/youtube: NOISE values "
                                               f"{lab.number(value)} best 30.0 threshold 3.1622\n")


class LowerTest(PackagingTest):
    """Behaviour 3: lower is better."""

    def test_lower_is_better(self):
        self.w.loop_file("hook-short", better="lower")
        self.w.baseline("hook-short")
        self.w.assign("ep06", "question-hook", "hook-short")
        self.w.assign("ep07", "question-hook", "hook-short")
        self.w.window("ep06", 26)
        self.w.window("ep07", 26.5)
        self.assertIn(": KEEP values 26.0,26.5 best 30.0 ", self.w.measure("hook-short"))
        self.w.assign("ep08", "cold-open", "hook-short")
        self.w.window("ep08", 35)
        self.assertIn(": DISCARD values 35.0 best 26.25 ", self.w.measure("hook-short"))
        self.assertEqual(self.w.posts()[0]["body"].split("\n")[3],
                         "metric: averageViewDuration on youtube (lower is better)")


class ControlsTest(PackagingTest):
    """Behaviour 4: controls set the threshold, and a no-op control is due every drift_every runs."""

    def test_four_controls_and_a_frozen_change_waits(self):
        self.w.baseline(eps=CONTROLS[:4], values=BASELINE[:4])
        self.w.assign("ep06", "question-hook")
        self.w.window("ep06", 35)
        trunk = self.w.trunk()
        self.assertEqual(self.w.measure(), "packaging hook-youtube run ep06/youtube: waits: 4 of 5 no-op controls\n")
        self.assertNothingWritten(trunk)

    def ten_runs(self):
        self.w.baseline()
        for n in range(6, 16):
            change, ep = f"change-{n}", f"ep{n:02d}"
            self.w.experiment(change)
            self.w.assign(ep, change)
            self.w.window(ep, 30)
        out = self.w.measure()
        self.assertEqual(out.count(": NOISE values 30.0 best 30.0 threshold 3.1622\n"), 10, out)

    def test_a_control_is_due_after_ten_runs(self):
        self.ten_runs()
        self.w.assign("ep16", "question-hook")
        self.w.window("ep16", 30)
        trunk, posts = self.w.trunk(), len(self.w.posts())
        self.assertEqual(self.w.measure(), "packaging hook-youtube run ep16/youtube: waits: a no-op control is due "
                                           "after 10 runs\n")
        self.assertNothingWritten(trunk, posts)
        self.w.assign("ep17", "-")
        self.w.window("ep17", 31)
        self.assertEqual(self.w.measure(), "packaging hook-youtube run ep16/youtube: waits: a no-op control is due "
                                           "after 10 runs\n"
                                           "packaging hook-youtube control ep17/youtube: - values 31.0 best 30.0 "
                                           "threshold 3.1622\n")
        self.assertEqual(len(self.w.posts()), posts)
        self.assertEqual(self.w.measure(), "packaging hook-youtube run ep16/youtube: NOISE values 30.0 best 30.0 "
                                           "threshold 3.1622\n")

    def test_drift_rebaselines(self):
        self.w.baseline()
        self.w.assign("ep06", "-")
        self.w.window("ep06", 40)
        self.assertEqual(self.w.measure(), "packaging hook-youtube control ep06/youtube: REBASELINE values 40.0 "
                                           "best 30.0 threshold 3.1622\n")
        body = self.w.posts()[-1]["body"]
        self.assertEqual(body.split("\n")[:5], ["[AUTORESEARCH] REBASELINE", "", "loop: hook-youtube",
                                                "metric: averageViewDuration on youtube (higher is better)",
                                                "change: -"])
        self.assertIn("posts: ep06/youtube\n", body)
        self.w.assign("ep07", "question-hook")
        self.w.window("ep07", 35)
        self.assertEqual(self.w.measure(), "packaging hook-youtube run ep07/youtube: waits: 0 of 5 no-op controls\n")


class EmptyTest(PackagingTest):
    """Behaviour 5: empty is not zero, and a window that is not a snapshot is skipped."""

    def test_a_null_window_is_skipped(self):
        self.w.assign("ep01", "-")
        self.w.window("ep01", None)
        trunk = self.w.trunk()
        self.assertEqual(self.w.measure(), "packaging hook-youtube control ep01/youtube: skipped: the window holds no "
                                           "averageViewDuration\n")
        self.assertNothingWritten(trunk)

    def test_a_null_point_is_skipped(self):
        self.w.loop_file("hold-end", metric="audienceWatchRatio:99")
        self.w.assign("ep01", "-", "hold-end")
        self.w.window("ep01", 30)
        self.assertEqual(self.w.measure("hold-end"), "packaging hold-end control ep01/youtube: skipped: the window "
                                                     "holds no audienceWatchRatio:99\n")
        self.assertEqual(self.w.rows("hold-end"), [])

    def test_twin_a_point_is_read(self):
        self.w.loop_file("hold-early", metric="audienceWatchRatio:5")
        self.w.assign("ep01", "-", "hold-early")
        self.w.window("ep01", 30)
        point = json.loads(window(30))["metrics"]["audienceWatchRatio"][4]
        self.assertEqual(point, 0.975)
        self.assertEqual(self.w.measure("hold-early"), "packaging hold-early control ep01/youtube: - values 0.975 "
                                                       "best - threshold -\n")

    def test_a_skipped_post_gives_no_value(self):
        self.w.baseline()
        self.w.assign("ep06", "question-hook")
        self.w.assign("ep07", "question-hook")
        self.w.window("ep06", None)
        self.w.window("ep07", 25)
        self.assertEqual(self.w.measure(), "packaging hook-youtube run ep06/youtube: skipped: the window holds no "
                                           "averageViewDuration\n"
                                           "packaging hook-youtube run ep07/youtube: DISCARD values 25.0 best 30.0 "
                                           "threshold 3.1622\n")
        self.assertEqual(self.w.rows()[-1]["commit"], self.w.commit_of(8))

    def test_defect_another_platform(self):
        self.w.assign("ep01", "-")
        self.w.window("ep01", 30, platform="tiktok")
        self.assertEqual(self.w.measure(), "packaging hook-youtube control ep01/youtube: skipped: "
                                           "distribution/ep01/youtube/snapshot.json is not a snapshot of youtube\n")
        self.assertEqual(self.w.rows(), [])

    def test_defect_not_snapshots(self):
        good = json.loads(window(30))
        self.w.loop_file("hold-early", metric="audienceWatchRatio:5")
        cases = []
        for name, value in (("averageViewDuration", "30"), ("averageViewDuration", True),
                            ("averageViewDuration", -1), ("averageViewDuration", 10 ** 400),
                            ("audienceWatchRatio", good["metrics"]["audienceWatchRatio"][:99]),
                            ("audienceWatchRatio", good["metrics"]["audienceWatchRatio"][:4] + [10 ** 400]
                             + good["metrics"]["audienceWatchRatio"][5:])):
            data = copy.deepcopy(good)
            data["metrics"][name] = value
            cases.append((LOOP if name == "averageViewDuration" else "hold-early", json.dumps(data)))
        no_duration = copy.deepcopy(good)
        del no_duration["metrics"]["averageViewDuration"]
        no_metrics = dict(good, metrics=None)
        cases += [(LOOP, json.dumps(no_duration)), (LOOP, json.dumps(no_metrics)), (LOOP, "not json"), (LOOP, "[]")]
        for n, (loop, text) in enumerate(cases, 1):
            ep = f"ep{n:02d}"
            with self.subTest(n=n):
                self.w.assign(ep, "-", loop)
                self.w.put(f"distribution/{ep}/youtube/snapshot.json", text)
                last = self.w.measure(loop).splitlines()[-1]
                self.assertEqual(last, f"packaging {loop} control {ep}/youtube: skipped: "
                                       f"distribution/{ep}/youtube/snapshot.json is not a snapshot of youtube")
        self.assertEqual(self.w.rows() + self.w.rows("hold-early"), [])


class OneChangePerPostTest(PackagingTest):
    """Behaviour 6: each post carries one change, a change is on at most two posts of one loop."""

    def refused(self, argv, message):
        trunk = self.w.trunk()
        self.assertRefused(self.w.packaging(*argv), message)
        self.assertNothingWritten(trunk)

    def test_a_post_is_assigned_once(self):
        self.w.assign("ep07", "-")
        self.w.assign("ep08", "question-hook")
        self.refused(["assign", LOOP, "ep08", "cold-open"],
                     "ep08/youtube already carries a change: lab/assignments.tsv line 3")
        self.w.loop_file("hook-short", better="lower")
        self.refused(["assign", "hook-short", "ep08", "-"],
                     "ep08/youtube already carries a change: lab/assignments.tsv line 3")

    def test_a_change_is_on_two_posts(self):
        self.w.assign("ep06", "question-hook")
        self.w.assign("ep07", "question-hook")
        self.refused(["assign", LOOP, "ep08", "question-hook"], "question-hook is already on 2 posts")

    def test_a_change_belongs_to_one_loop(self):
        self.w.loop_file("hook-short", better="lower")
        self.w.assign("ep06", "question-hook")
        self.refused(["assign", "hook-short", "ep07", "question-hook"],
                     "question-hook is a change of loop hook-youtube")

    def test_before_the_window(self):
        self.w.window("ep09", 30)
        self.refused(["assign", LOOP, "ep09", "-"],
                     "distribution/ep09/youtube/snapshot.json is on trunk: a change is assigned before the window")

    def test_not_an_episode_id(self):
        self.refused(["assign", LOOP, "Ep9", "-"], "'Ep9' is not an episode id")
        self.refused(["assign", LOOP, "../ep9", "-"], "'../ep9' is not an episode id")

    def test_no_experiment_file(self):
        self.refused(["assign", LOOP, "ep09", "nothing"], "lab/experiments/nothing.md is not on trunk")

    def test_twin_the_clean_assignment(self):
        self.assertEqual(self.w.packaging("assign", LOOP, "ep09", "-"),
                         (0, "packaging hook-youtube assign ep09/youtube: -\n", ""))
        self.assertEqual(self.w.assignments(), f"time\tloop\tpost\tchange\n{NOW}\thook-youtube\tep09/youtube\t-\n")
        self.assertEqual(self.w.packaging("assign", LOOP, "ep10", "question-hook")[1],
                         "packaging hook-youtube assign ep10/youtube: question-hook\n")


class OwnedLogsTest(PackagingTest):
    """Behaviour 7: only `factory packaging --apply` writes the two logs, on the base, as `factory`."""

    def test_without_apply_nothing_is_written(self):
        trunk = self.w.trunk()
        dry = self.w.assign("ep06", "question-hook", apply=False)
        self.assertNothingWritten(trunk)
        self.assertIsNone(self.w.assignments())
        self.assertEqual(self.w.assign("ep06", "question-hook"), dry)

    def test_measure_without_apply(self):
        for ep in CONTROLS:
            self.w.assign(ep, "-")
        self.w.assign("ep06", "question-hook")
        self.w.assign("ep07", "question-hook")
        for ep, value in zip(CONTROLS + ("ep06", "ep07"), BASELINE + (35, 34.5)):
            self.w.window(ep, value)
        trunk = self.w.trunk()
        dry = self.w.measure(apply=False)
        self.assertNothingWritten(trunk)
        self.assertIsNone(self.w.results())
        self.assertEqual(self.w.measure(), dry)
        self.assertTrue(dry.endswith(THRESHOLD_LINE + "\npackaging hook-youtube run ep06/youtube,ep07/youtube: KEEP "
                                     "values 35.0,34.5 best 30.0 threshold 3.1622\n"), dry)

    def test_with_apply_one_commit_per_row(self):
        before = self.w.trunk()
        self.w.assign("ep06", "question-hook")
        self.assertEqual(gitstate.git(self.w.root, "rev-parse", "trunk~1").strip(), before)
        shown = gitstate.git(self.w.root, "log", "-1", "--format=%an%n%s", "--name-only", "trunk")
        self.assertEqual(shown, "factory\nlab: hook-youtube assign ep06/youtube question-hook\n\nlab/assignments.tsv\n")
        self.assertEqual(self.w.log_text(), '{"number": null, "op": "push", "ref": "trunk"}\n')

    def test_with_apply_the_rows_are_commits_of_factory(self):
        self.keep()
        log = gitstate.git(self.w.root, "log", "--format=%an %s", "trunk").splitlines()
        self.assertEqual(log[0], "factory lab: hook-youtube run KEEP")
        self.assertEqual(log.count("factory lab: hook-youtube control -"), 5)
        self.assertEqual(sum(1 for entry in log if entry.startswith("factory lab: hook-youtube assign ")), 7)
        files = gitstate.git(self.w.root, "log", "-1", "--format=", "--name-only", "trunk")
        self.assertEqual(files, "lab/results.tsv\n")

    def test_defect_a_working_tree_log_that_differs(self):
        self.w.assign("ep06", "-")
        self.w.write(packaging.PATH, packaging.HEADER + "\n")
        trunk = self.w.trunk()
        self.assertRefused(self.w.packaging("assign", LOOP, "ep07", "-"), "lab/assignments.tsv differs from the base")
        self.assertNothingWritten(trunk)

    def test_defect_a_staged_file(self):
        self.w.assign("ep01", "-")
        self.w.window("ep01", 30)
        self.w.write("notes.md", "x\n")
        self.w.git("add", "notes.md")
        trunk = self.w.trunk()
        message = "the checkout is not on trunk with a clean index"
        self.assertRefused(self.w.packaging("assign", LOOP, "ep07", "-"), message)
        self.assertRefused(self.w.packaging("measure", LOOP), message)
        self.assertNothingWritten(trunk)

    def test_defect_a_working_tree_results_log_that_differs(self):
        self.w.baseline(eps=CONTROLS[:1], values=BASELINE[:1])
        self.w.assign("ep02", "-")
        self.w.window("ep02", 30)
        self.w.write(lab.PATH, lab.HEADER + "\n")
        trunk = self.w.trunk()
        self.assertRefused(self.w.packaging("measure", LOOP), "lab/results.tsv differs from the base")
        self.assertNothingWritten(trunk)

    def test_defect_not_on_the_base(self):
        self.w.git("checkout", "-q", "-b", "other")
        trunk = self.w.trunk()
        self.assertRefused(self.w.packaging("assign", LOOP, "ep07", "-"),
                           "the checkout is not on trunk with a clean index")
        self.assertNothingWritten(trunk)


class MeasuredOnceTest(PackagingTest):
    """Behaviour 8: a unit with a row is not measured again."""

    def test_a_second_measure(self):
        self.keep()
        trunk = self.w.trunk()
        self.assertEqual(self.w.measure(), "packaging hook-youtube: nothing to measure\n")
        self.assertNothingWritten(trunk, 1)

    def test_nothing_assigned(self):
        self.assertEqual(self.w.measure(), "packaging hook-youtube: nothing to measure\n")

    def test_a_window_that_lands_later(self):
        self.w.baseline()
        self.w.assign("ep06", "question-hook")
        self.w.assign("ep07", "-")
        self.w.window("ep07", 31)
        self.assertEqual(self.w.measure(), "packaging hook-youtube run ep06/youtube: waits: no frozen window\n"
                                           "packaging hook-youtube control ep07/youtube: - values 31.0 best 30.0 "
                                           "threshold 3.1622\n")
        self.w.window("ep06", 20)
        self.assertEqual(self.w.measure(), "packaging hook-youtube run ep06/youtube: DISCARD values 20.0 best 30.0 "
                                           "threshold 3.1622\n")
        self.assertEqual(self.w.measure(), "packaging hook-youtube: nothing to measure\n")
        self.assertEqual(len(self.w.rows()), 7)


class BaseNotDiskTest(PackagingTest):
    """Behaviour 9: the loop file, the experiment files and the snapshots are read on the base."""

    def test_a_loop_file_on_disk_only(self):
        self.w.write("lab/packaging/hook-short.json", (FIX / "production" / LOOP_FILE).read_text())
        self.assertRefused(self.w.packaging("measure", "hook-short"), "lab/packaging/hook-short.json is not on trunk")

    def test_an_experiment_file_on_disk_only(self):
        self.w.write("lab/experiments/split-title.md", "# Split title\n")
        self.assertRefused(self.w.packaging("assign", LOOP, "ep06", "split-title"),
                           "lab/experiments/split-title.md is not on trunk")

    def test_a_snapshot_on_disk_only(self):
        self.w.assign("ep01", "-")
        self.w.write("distribution/ep01/youtube/snapshot.json", window(30))
        self.assertEqual(self.w.measure(), "packaging hook-youtube control ep01/youtube: waits: no frozen window\n")
        self.assertEqual(self.w.packaging("assign", LOOP, "ep02", "-")[0], 0)

    def test_a_log_on_disk_only(self):
        self.w.assign("ep01", "-")
        self.w.write(packaging.PATH, "not a log\n")
        self.w.write(lab.PATH, "not a log\n")
        self.assertEqual(self.w.measure(apply=False),
                         "packaging hook-youtube control ep01/youtube: waits: no frozen window\n")


class RefusalTest(PackagingTest):
    """Behaviour 10: every refusal exits 2 and writes nothing."""

    def refused(self, argv, message, apply=True):
        trunk, posts = self.w.trunk(), len(self.w.posts())
        self.assertRefused(self.w.packaging(*argv, apply=apply), message)
        self.assertNothingWritten(trunk, posts)

    def test_not_a_loop_id(self):
        self.refused(["measure", "Hook_YouTube"], "'Hook_YouTube' is not a loop id")
        self.refused(["assign", "../x", "ep01", "-"], "'../x' is not a loop id")

    def test_no_loop_file(self):
        self.refused(["measure", "hook-short"], "lab/packaging/hook-short.json is not on trunk")

    def test_bad_loop_files(self):
        shape = "lab/packaging/bad.json " + SHAPE
        for changes in ({"better": "more"}, {"controls": 4}, {"controls": 11}, {"controls": True},
                        {"drift_every": 9}, {"drift_every": 16}, {"metric": "views"},
                        {"metric": "audienceWatchRatio:0"}, {"metric": "audienceWatchRatio:101"},
                        {"metric": "audienceWatchRatio:05"}, {"metric": "audienceWatchRatio"},
                        {"platform": "tiktok"}, {"ticket": 0}, {"ticket": "1"}, {"ticket": True}, {"extra": 1}):
            with self.subTest(changes=changes):
                self.w.loop_file("bad", **changes)
                self.refused(["measure", "bad"], shape)
        self.w.put("lab/packaging/bad.json", "not json")
        self.refused(["measure", "bad"], shape)
        spec = json.loads((FIX / "production" / LOOP_FILE).read_text())
        del spec["ticket"]
        self.w.put("lab/packaging/bad.json", json.dumps(spec))
        self.refused(["assign", "bad", "ep01", "-"], shape)

    def test_twin_the_edges_of_a_loop_file(self):
        for n, changes in enumerate(({"metric": "audienceWatchRatio:1"}, {"metric": "audienceWatchRatio:100"},
                                     {"controls": 10, "drift_every": 15, "better": "lower"})):
            self.w.loop_file(f"edge-{n}", **changes)
            self.assertEqual(self.w.measure(f"edge-{n}"), f"packaging edge-{n}: nothing to measure\n")

    def test_also_a_lab_loop(self):
        self.w.put("lab/loops/hook-youtube.json", "{}\n")
        self.refused(["measure", LOOP], "hook-youtube is also a lab loop: lab/loops/hook-youtube.json is on trunk")

    def test_not_a_change_id(self):
        self.refused(["assign", LOOP, "ep01", "Question_Hook"], "'Question_Hook' is not a change id")
        self.refused(["assign", LOOP, "ep01", "question--hook"], "'question--hook' is not a change id")

    def test_bad_header(self):
        self.w.put(packaging.PATH, "time\tloop\n")
        message = "assignments line 1: the header is not time loop post change"
        self.refused(["assign", LOOP, "ep01", "-"], message)
        self.refused(["measure", LOOP], message)

    def test_bad_columns(self):
        self.w.put(packaging.PATH, packaging.HEADER + "\n" + "a\tb\tc\n")
        self.refused(["measure", LOOP], "assignments line 2: 3 columns, not 4")

    def test_bad_results_log(self):
        self.w.put(lab.PATH, "time\tloop\n")
        self.refused(["measure", LOOP], "results line 1: the header is not "
                                        "time loop kind commit values best threshold verdict")

    def test_usage(self):
        self.refused(["assign", LOOP, "ep01"], "assign needs <ep> and <change>")
        self.refused(["assign", LOOP], "assign needs <ep> and <change>")
        self.refused(["measure", LOOP, "ep01"], "measure takes only <loop>")
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as raised:
            cmd_packaging.main(["run", LOOP])
        self.assertEqual(raised.exception.code, 2)
        self.assertTrue(err.getvalue().startswith("usage: factory packaging"))

    def test_missing_issue(self):
        self.w.assign("ep01", "-")
        self.w.window("ep01", 30)
        (self.w.dir / "issues" / "1.json").unlink()
        self.refused(["measure", LOOP], "issue #1: not found")

    def test_twin_missing_issue_without_apply(self):
        (self.w.dir / "issues" / "1.json").unlink()
        self.assertEqual(self.w.measure(apply=False), "packaging hook-youtube: nothing to measure\n")

    def test_frozen(self):
        self.w.gh.create_issue("[FREEZE] Stop everything", "", ["factory:freeze"])
        for apply in (True, False):
            self.refused(["measure", LOOP], "frozen", apply=apply)
            self.refused(["assign", LOOP, "ep01", "-"], "frozen", apply=apply)

    def test_twin_a_closed_freeze_issue(self):
        number = self.w.gh.create_issue("[FREEZE] Stop everything", "", ["factory:freeze"])
        self.w.gh.close_issue(number)
        self.assertEqual(self.w.packaging("assign", LOOP, "ep01", "-")[0], 0)


class CommandTest(unittest.TestCase):
    """The command as `kit/factory` and `python3 kit/lib/cmd_packaging.py` run it."""

    def test_help(self):
        done = subprocess.run([sys.executable, str(ROOT / "kit" / "lib" / "cmd_packaging.py"), "--help"],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(done.returncode, 0)
        self.assertTrue(done.stdout.startswith("usage: factory packaging"), done.stdout)
        self.assertEqual(cmd_packaging.__doc__.split("\n")[0],
                         "factory packaging assign <loop> <ep> <change>|measure <loop> [--root DIR] [--apply]: "
                         "the live packaging loop, one change per post, measured on its frozen window.")

    def test_listed(self):
        done = subprocess.run([sys.executable, str(ROOT / "kit" / "factory"), "help"],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertIn("  packaging\n", done.stdout)


class DeterministicTest(unittest.TestCase):
    """Behaviour 11: one script, two fresh worlds, the same bytes."""

    def play(self):
        w = World(self)
        out = [w.baseline()]
        for ep, change in (("ep06", "question-hook"), ("ep07", "question-hook"), ("ep08", "cold-open"),
                           ("ep09", "-")):
            out.append(w.assign(ep, change))
        for ep, value in (("ep06", 35), ("ep07", 34.5), ("ep08", 25), ("ep09", 40)):
            w.window(ep, value)
            out.append(w.measure())
        return out, w.assignments(), w.results(), w.log_text(), [p["body"] for p in w.posts()]

    def test_two_worlds(self):
        first, second = self.play(), self.play()
        self.assertEqual(first, second)
        self.assertEqual([b.split("\n")[0] for b in first[4]],
                         ["[AUTORESEARCH] KEEP", "[AUTORESEARCH] DISCARD", "[AUTORESEARCH] REBASELINE"])


class WindowsTest(unittest.TestCase):
    """Behaviour 12: the recorded windows are #169's snapshots."""

    def test_a_recorded_window_passes_the_telemetry_lint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "distribution" / "ep07" / "youtube" / "snapshot.json"
            path.parent.mkdir(parents=True)
            path.write_text(window(34.5))
            result = lint.run(Path(tmp), ["telemetry"])["telemetry"]
            self.assertEqual((result.status, result.findings), (lint.PASS, []))

    def test_a_null_window_passes_the_telemetry_lint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "distribution" / "ep01" / "youtube" / "snapshot.json"
            path.parent.mkdir(parents=True)
            path.write_text(window(None))
            self.assertEqual(lint.run(Path(tmp), ["telemetry"])["telemetry"].status, lint.PASS)
            self.assertIsNone(json.loads(window(None))["metrics"]["averageViewDuration"])


if __name__ == "__main__":
    unittest.main()
