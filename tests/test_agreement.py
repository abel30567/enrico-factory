"""Tests for kit/lib/agreement.py and kit/lib/cmd_agreement.py: `factory agreement`, the judge calibration metric.

Each tree is built in a temporary directory: four held-out episodes `ep02` to `ep05` of `youtube` with known
real retention (0.62, 0.48, 0.35, 0.21), a calibration set holding only `ep01`, and six verdicts of
`audience-sim` of which one is wrong. The lab test runs the command as the metric of a loop through
`factory lab`, on a copy of tests/fixtures/lab. Nothing reaches the network, `gh` or a model.
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import agreement, cmd_agreement, cmd_lab, gitstate, lab  # noqa: E402

LOOP = "judge-calibration"
HELD_OUT = f"lab/held-out/{LOOP}.json"
VERDICTS = f"lab/verdicts/{LOOP}.tsv"
TSV = "calibration/retention.tsv"
ROLE = ".factory/roles/audience-sim.md"
RETENTION = {"ep02": 0.62, "ep03": 0.48, "ep04": 0.35, "ep05": 0.21}
SHA = "0" * 64
TSV_HEADER = "post\tpost_id\tbeat\tstart\tend\tretention\tsnapshot_sha256\n"
EP01_ROW = f"ep01/youtube\tinventedVid\tB1\t0\t7.7\t0.89\t{SHA}\n"
EP03_ROW = f"ep03/youtube\tinventedVid\tB2\t5\t10\t0.48\t{SHA}\n"
HEADER = "judge\ta\tb\tmarker\n"
# The last verdict is wrong: ep04 (0.35) has the higher retention.
PAIRS = [("ep02", "ep03", "PREFERS A"), ("ep02", "ep04", "PREFERS A"), ("ep05", "ep02", "PREFERS B"),
         ("ep03", "ep04", "PREFERS A"), ("ep03", "ep05", "PREFERS A"), ("ep04", "ep05", "PREFERS B")]
FLIP = {"PREFERS A": "PREFERS B", "PREFERS B": "PREFERS A"}
RIGHT = PAIRS[:5] + [("ep04", "ep05", "PREFERS A")]
REVERSED = [(a, b, FLIP[m]) for a, b, m in RIGHT]
OUT_1 = f"agreement {LOOP} audience-sim: 5 of 6 pairs agree with real retention, 0 of equal retention not counted\n"
IDENTITY = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")
NOW = "2026-11-13T09:00:00Z"
FIX = ROOT / "tests" / "fixtures" / "lab"


def verdicts_text(pairs):
    return HEADER + "".join(f"audience-sim\t{a}\t{b}\t{m}\n" for a, b, m in pairs)


def retention_json(ep, last):
    data = {"beats": [{"beat": "B1", "end": 5, "first": 1, "last": 50, "retention": 0.9, "start": 0},
                      {"beat": "B2", "end": 10, "first": 51, "last": 100, "retention": last, "start": 5}],
            "length": 10, "post": f"{ep}/youtube", "post_id": "inventedVid", "snapshot_sha256": SHA}
    return json.dumps(data, indent=1, sort_keys=True) + "\n"


def criterion_one(root, retention=None):
    """Write criterion 1's tree under ``root``."""
    files = {HELD_OUT: json.dumps({"episodes": sorted(RETENTION), "judge": "audience-sim", "platform": "youtube"})
             + "\n", TSV: TSV_HEADER + EP01_ROW, VERDICTS: verdicts_text(PAIRS)}
    for ep, value in sorted((retention or RETENTION).items()):
        files[f"distribution/{ep}/youtube/retention.json"] = retention_json(ep, value)
    for rel, text in files.items():
        write(root, rel, text)


def write(root, rel, text):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


class AgreementTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        os.environ.pop("FACTORY_LAB_TREE", None)
        self.root = self.tree()
        criterion_one(self.root)

    def tree(self):
        path = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, path)
        return path

    def write(self, rel, text, root=None):
        return write(root or self.root, rel, text)

    def run_main(self, *argv, root=True):
        out, err = io.StringIO(), io.StringIO()
        full = [LOOP] + (["--root", str(self.root)] if root else []) + list(argv)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_agreement.main(full)
        return code, out.getvalue(), err.getvalue()

    def assertValue(self, line, value, *argv):
        self.assertEqual(self.run_main(*argv), (0, line + value + "\n", ""))

    def assertRefused(self, message, *argv):
        self.assertEqual(self.run_main(*argv), (2, "", f"agreement: {message}\n"))


class KnownRankingTest(AgreementTest):
    """Behaviour 1: the known ranking."""

    def test_five_of_six(self):
        self.assertValue(OUT_1, "0.8333")

    def test_all_right(self):
        self.write(VERDICTS, verdicts_text(RIGHT))
        self.assertValue(OUT_1.replace("5 of 6", "6 of 6"), "1.0")

    def test_all_reversed(self):
        self.write(VERDICTS, verdicts_text(REVERSED))
        self.assertValue(OUT_1.replace("5 of 6", "0 of 6"), "0.0")

    def test_no_preference_is_counted_and_never_agrees(self):
        self.write(VERDICTS, verdicts_text(PAIRS[:5] + [("ep04", "ep05", "NO PREFERENCE")]))
        self.assertValue(OUT_1, "0.8333")


class GuardTest(AgreementTest):
    """Behaviour 3: the tuned role never reads held-out retention."""

    def test_a_held_out_row_in_the_calibration_set(self):
        self.write(TSV, TSV_HEADER + EP01_ROW + EP03_ROW)
        self.assertRefused(f"{TSV} line 3: post ep03/youtube is a held-out episode of loop {LOOP}")

    def test_twin_the_ep01_row_only(self):
        self.write(TSV, TSV_HEADER + EP01_ROW)
        self.assertValue(OUT_1, "0.8333")

    def test_a_role_reading_distribution(self):
        role = (ROOT / "kit/roles/audience-sim.md").read_text(encoding="utf-8")
        text = role.replace("reads: bible/**, episodes/**, calibration/**\n",
                            "reads: bible/**, episodes/**, calibration/**, distribution/**\n")
        self.assertNotEqual(text, role)
        self.write(ROLE, text)
        self.assertRefused(f"{ROLE} reads distribution/ep02/youtube/retention.json, "
                           "the retention of a held-out episode")

    def test_twin_the_unchanged_role(self):
        shutil.copy(ROOT / "kit/roles/audience-sim.md", self.write(ROLE, ""))
        self.assertValue(OUT_1, "0.8333")

    def test_twin_a_role_reading_a_tuning_episode(self):
        self.write(ROLE, "---\nrole: audience-sim\nreads: episodes/**, distribution/ep01/**\n---\n")
        self.assertValue(OUT_1, "0.8333")

    def test_twin_no_role_file(self):
        self.assertFalse((self.root / ROLE).exists())
        self.assertValue(OUT_1, "0.8333")

    def test_a_role_file_without_front_matter(self):
        self.write(ROLE, "no front matter\n")
        self.assertRefused(f"{ROLE}: line 1: missing opening ---")


class TiesTest(AgreementTest):
    """Behaviour 4: a verdict on two episodes of equal retention is not counted."""

    def test_one_tie(self):
        criterion_one(self.root, dict(RETENTION, ep04=0.48))
        self.assertValue(f"agreement {LOOP} audience-sim: 4 of 5 pairs agree with real retention, "
                         "1 of equal retention not counted\n", "0.8")

    def test_every_retention_equal(self):
        criterion_one(self.root, {ep: 0.5 for ep in RETENTION})
        self.assertRefused(f"{VERDICTS}: no verdict on two held-out episodes of different retention")


class HeldOutTest(AgreementTest):
    """Behaviour 5: the held-out list."""

    SHAPE = f"{HELD_OUT} is not {{episodes, judge, platform}}"

    def spec(self, **changes):
        data = {"episodes": ["ep02", "ep03", "ep04", "ep05"], "judge": "audience-sim", "platform": "youtube"}
        data.update(changes)
        self.write(HELD_OUT, json.dumps(data))

    def test_missing(self):
        (self.root / HELD_OUT).unlink()
        self.assertRefused(f"{HELD_OUT} is missing")

    def test_not_json(self):
        self.write(HELD_OUT, "{")
        self.assertRefused(self.SHAPE)

    def test_one_episode(self):
        self.spec(episodes=["ep02"])
        self.assertRefused(self.SHAPE)

    def test_a_repeated_episode(self):
        self.spec(episodes=["ep02", "ep03", "ep02"])
        self.assertRefused(self.SHAPE)

    def test_an_extra_key(self):
        self.spec(extra=1)
        self.assertRefused(self.SHAPE)

    def test_a_judge_that_is_not_a_role_id(self):
        self.spec(judge="Audience")
        self.assertRefused(self.SHAPE)

    def test_held_out_directly(self):
        self.assertEqual(agreement.held_out(self.root, LOOP),
                         {"episodes": ["ep02", "ep03", "ep04", "ep05"], "judge": "audience-sim",
                          "platform": "youtube"})
        self.spec(judge="Audience")
        with self.assertRaises(agreement.AgreementError) as caught:
            agreement.held_out(self.root, LOOP)
        self.assertEqual(str(caught.exception), self.SHAPE)


class RetentionTest(AgreementTest):
    """Behaviour 6: real retention, read under the root only."""

    def test_a_missing_retention_json(self):
        (self.root / "distribution/ep05/youtube/retention.json").unlink()
        self.assertRefused("distribution/ep05/youtube/retention.json is missing")

    def test_a_null_last_beat(self):
        self.write("distribution/ep05/youtube/retention.json", retention_json("ep05", None))
        self.assertRefused("post ep05/youtube has no retention on its last beat B2")

    def test_retention_directly(self):
        self.assertEqual(agreement.retention(self.root, "ep03/youtube"), Decimal("0.48"))
        with self.assertRaises(agreement.AgreementError) as caught:
            agreement.retention(self.root, "ep09/youtube")
        self.assertEqual(str(caught.exception), "distribution/ep09/youtube/retention.json is missing")

    def test_retention_is_read_under_the_root_only(self):
        tree = self.tree()
        self.write(VERDICTS, verdicts_text(PAIRS), root=tree)
        (self.root / VERDICTS).unlink()
        self.assertValue(OUT_1, "0.8333", "--tree", str(tree))


class VerdictsTest(AgreementTest):
    """Behaviour 7: the verdicts."""

    def lines(self, *extra, header=HEADER):
        self.write(VERDICTS, header + "".join(line + "\n" for line in extra))

    def test_missing(self):
        (self.root / VERDICTS).unlink()
        self.assertRefused(f"{VERDICTS} is missing")

    def test_header(self):
        self.lines("audience-sim\tep02\tep03\tPREFERS A", header="judge\tb\ta\tmarker\n")
        self.assertRefused(f"{VERDICTS} line 1: header is not judge, a, b, marker")

    def test_cells(self):
        self.lines("audience-sim\tep02\tep03\tPREFERS A", "audience-sim\tep02\tPREFERS A")
        self.assertRefused(f"{VERDICTS} line 3: 3 cells, not 4")

    def test_judge(self):
        self.lines("theory-judge\tep02\tep03\tPREFERS A")
        self.assertRefused(f"{VERDICTS} line 2: judge theory-judge is not the tuned judge audience-sim")

    def test_a_not_held_out(self):
        self.lines("audience-sim\tep01\tep03\tPREFERS A")
        self.assertRefused(f"{VERDICTS} line 2: episode ep01 is not held out")

    def test_b_not_held_out(self):
        self.lines("audience-sim\tep02\tep01\tPREFERS A")
        self.assertRefused(f"{VERDICTS} line 2: episode ep01 is not held out")

    def test_a_equal_to_b(self):
        self.lines("audience-sim\tep02\tep02\tPREFERS A")
        self.assertRefused(f"{VERDICTS} line 2: a and b are both ep02")

    def test_marker(self):
        self.lines("audience-sim\tep02\tep03\tBLOCKED")
        self.assertRefused(f"{VERDICTS} line 2: marker BLOCKED is not PREFERS A, PREFERS B or NO PREFERENCE")

    def test_a_repeated_pair(self):
        self.lines("audience-sim\tep03\tep02\tPREFERS B", "audience-sim\tep04\tep05\tPREFERS A",
                   "audience-sim\tep02\tep03\tPREFERS A")
        self.assertRefused(f"{VERDICTS} line 4: the pair ep02, ep03 is repeated (first on line 2)")

    def test_twin_a_pair_written_the_other_way(self):
        self.write(VERDICTS, verdicts_text([("ep03", "ep02", "PREFERS B")] + PAIRS[1:]))
        self.assertValue(OUT_1, "0.8333")


class NeverExitOneTest(AgreementTest):
    """Behaviour 8: a file that cannot be decoded is a refusal, exit 2."""

    def test_undecodable_files(self):
        shutil.copy(ROOT / "kit/roles/audience-sim.md", self.write(ROLE, ""))
        for rel in (VERDICTS, HELD_OUT, ROLE, TSV):
            with self.subTest(rel=rel):
                saved = (self.root / rel).read_bytes()
                self.write(rel, b"\xff")
                self.assertRefused(f"{rel} cannot be read")
                self.write(rel, saved)
                self.assertValue(OUT_1, "0.8333")


class WhichTreeTest(AgreementTest):
    """Behaviour 9: `--tree`, else `FACTORY_LAB_TREE`, else the root."""

    def setUp(self):
        super().setUp()
        self.env_tree = self.tree()
        self.write(VERDICTS, verdicts_text(RIGHT), root=self.env_tree)
        self.flag_tree = self.tree()
        self.write(VERDICTS, verdicts_text(REVERSED), root=self.flag_tree)

    def test_tree_wins_over_the_environment(self):
        os.environ["FACTORY_LAB_TREE"] = str(self.env_tree)
        self.assertValue(OUT_1.replace("5 of 6", "0 of 6"), "0.0", "--tree", str(self.flag_tree))

    def test_the_environment_wins_over_the_root(self):
        os.environ["FACTORY_LAB_TREE"] = str(self.env_tree)
        self.assertValue(OUT_1.replace("5 of 6", "6 of 6"), "1.0")

    def test_an_empty_environment_is_the_root(self):
        os.environ["FACTORY_LAB_TREE"] = ""
        self.assertValue(OUT_1, "0.8333")

    def test_a_tree_that_is_not_a_directory(self):
        self.assertRefused("the tree measured is not a directory", "--tree", str(self.root / "nowhere"))


class InterfaceTest(AgreementTest):
    """Behaviour 10: the interface."""

    def call(self, argv):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cmd_agreement.main(argv)

    def test_help_and_usage(self):
        with self.assertRaises(SystemExit) as caught:
            self.call(["--help"])
        self.assertEqual(caught.exception.code, 0)
        with self.assertRaises(SystemExit) as caught:
            self.call([])
        self.assertEqual(caught.exception.code, 2)

    def test_a_bad_loop_id(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_agreement.main(["Bad", "--root", str(self.root)])
        self.assertEqual((code, out.getvalue(), err.getvalue()), (2, "", "agreement: 'Bad' is not a loop id\n"))

    def test_deterministic(self):
        first = self.run_main()
        self.assertEqual(first, self.run_main())
        self.assertEqual(first[0], 0)


class LabTest(unittest.TestCase):
    """Behaviour 2: the refusal through the lab."""

    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        self.root = tmp / "show"
        self.dir = tmp / "github"
        patch = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                                             "FACTORY_GITHUB": str(self.dir), "FACTORY_NOW": NOW})
        patch.start()
        self.addCleanup(patch.stop)
        for name in IDENTITY + ("FACTORY_LAB_TREE",):
            os.environ.pop(name, None)
        shutil.copytree(FIX / "production", self.root)
        criterion_one(self.root)
        loop = {"metric": "agreement", "better": "higher",
                "command": [sys.executable, str(ROOT / "kit" / "factory"), "agreement", LOOP],
                "controls": 5, "drift_every": 10, "ticket": 1}
        write(self.root, f"lab/loops/{LOOP}.json", json.dumps(loop))
        self.git("init", "-q", "-b", "trunk")
        self.commit("the production")
        shutil.copytree(FIX / "github", self.dir)

    def git(self, *args):
        return gitstate.git(self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", *args,
                            env={"GIT_AUTHOR_DATE": NOW, "GIT_COMMITTER_DATE": NOW})

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def ref(self, name, files):
        self.git("checkout", "-q", "-b", name, "trunk")
        for rel, text in sorted(files.items()):
            write(self.root, rel, text)
        self.commit("experiment " + name)
        sha = gitstate.head(self.root, name)
        self.git("checkout", "-q", "trunk")
        return sha

    def lab(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_lab.main(list(argv) + ["--root", str(self.root), "--apply"])
        return code, out.getvalue(), err.getvalue()

    def posts(self):
        path = self.dir / "log.jsonl"
        log = [json.loads(l) for l in path.read_text().splitlines()] if path.is_file() else []
        return [entry for entry in log if entry["op"] == "post"]

    def baseline(self):
        for _ in range(5):
            code, out, err = self.lab("control", LOOP)
            self.assertEqual((code, err), (0, ""), out)
        return out

    def test_a_held_out_row_is_refused_through_the_lab(self):
        out = self.baseline()
        self.assertTrue(out.endswith(f"lab {LOOP} threshold 0.0: twice the noise floor 0.0 over 5 controls, "
                                     "best 0.8333\n"), out)
        sha = self.ref("leak", {TSV: TSV_HEADER + EP01_ROW + EP03_ROW})
        trunk = gitstate.head(self.root, "trunk")
        results = gitstate.show(self.root, "trunk", lab.PATH)
        self.assertEqual(self.lab("run", LOOP, sha), (2, "", f"lab: {LOOP}: the metric exited 2\n"))
        self.assertEqual(gitstate.head(self.root, "trunk"), trunk)
        self.assertEqual(gitstate.show(self.root, "trunk", lab.PATH), results)
        self.assertEqual(self.posts(), [])

    def test_twin_six_right_verdicts_are_kept(self):
        self.baseline()
        sha = self.ref("right", {VERDICTS: verdicts_text(RIGHT)})
        code, out, err = self.lab("run", LOOP, sha)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, f"lab {LOOP} run {sha}: KEEP values 1.0,1.0 best 0.8333 threshold 0.0\n")
        self.assertEqual(len(self.posts()), 1)


if __name__ == "__main__":
    unittest.main()
