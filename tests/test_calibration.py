"""factory calibration: the judge calibration rows of one live post (ISSUE-188), on trees built in temporary
directories."""

import contextlib
import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import calibration, cmd_calibration, lint  # noqa: E402

SHA = "a0e416e293e6ee324abd97e5b51b112e2c685ab4bf19885b906e19c6d4dd5993"
REGISTRY_HEADER = "time\tpost\tepisode\tplatform\tpost_id\tvisibility\tsha256\tjob\tpull\thead\tbeats"
REGISTRY_ROW = "\t".join(["2026-11-13T12:00:00Z", "ep01/youtube", "ep01", "youtube", "inventedVid", "private",
                          "0" * 64, "0" * 64, "4", "0" * 40, "B1=0-7.7,B2=7.7-17.7"])
EDIT_LIST = {"fps": 24, "bible": "A-1", "events": [
    {"shot": "Q1-1", "beat": "B1", "start": 0, "duration": 4.5},
    {"shot": "Q1-2", "beat": "B1", "start": 4.5, "duration": 3.2},
    {"shot": "Q1-3", "beat": "B2", "start": 7.7, "duration": 10}]}
RETENTION = {"beats": [{"beat": "B1", "end": 7.7, "first": 1, "last": 43, "retention": 0.89, "start": 0},
                       {"beat": "B2", "end": 17.7, "first": 44, "last": 100, "retention": 0.645, "start": 7.7}],
             "length": 17.7, "post": "ep01/youtube", "post_id": "inventedVid", "snapshot_sha256": SHA}
RETENTION_SHA256 = "49f3b62c56e7ebe12e4541d0bb389eae93009a5de6d39a7c2b8fcfe06bda1458"
TSV_SHA256 = "8890fdf1156b56d87d3221eec20efc315ea86316b9854b2d384b37237e2a7c19"
RJ = "distribution/ep01/youtube/retention.json"
CLEAN = ("\t".join(calibration.HEADER) + "\n"
         + f"ep01/youtube\tinventedVid\tB1\t0\t7.7\t0.89\t{SHA}\n"
         + f"ep01/youtube\tinventedVid\tB2\t7.7\t17.7\t0.645\t{SHA}\n")
SHAPE = f"{RJ} is not {{beats: [{{beat, start, end, retention}}], post, post_id, snapshot_sha256}}"


def write(root, rel, text):
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(text.encode("utf-8"))


def retention_text(data):
    return json.dumps(data, indent=1, sort_keys=True) + "\n"


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cmd_calibration.main(list(map(str, argv)))
    return code, out.getvalue(), err.getvalue()


class Case(unittest.TestCase):
    def tree(self, retention=RETENTION):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        write(root, "registry/posts.tsv", REGISTRY_HEADER + "\n" + REGISTRY_ROW + "\n")
        write(root, "episodes/ep01/reel/edit-list.json", json.dumps(EDIT_LIST) + "\n")
        if retention is not None:
            write(root, RJ, retention if isinstance(retention, str) else retention_text(retention))
        return root

    def tsv(self, root):
        return (root / calibration.PATH).read_bytes().decode("utf-8")

    def lint(self, root):
        return lint.text(lint.run(root, ["calibration"]))


class DoneWhen(Case):
    def test_clean_tree_writes_the_rows_and_the_lint_passes(self):
        root = self.tree()
        self.assertEqual(hashlib.sha256((root / RJ).read_bytes()).hexdigest(), RETENTION_SHA256)
        code, out, err = run("ep01/youtube", "--root", root)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, "calibration ep01/youtube: 2 rows written to calibration/retention.tsv\n")
        self.assertEqual(self.tsv(root), CLEAN)
        self.assertEqual(hashlib.sha256(self.tsv(root).encode()).hexdigest(), TSV_SHA256)
        self.assertEqual(self.lint(root), "calibration PASS\n")

    def test_a_row_with_no_beat_id_behind_it_fails(self):
        root = self.tree()
        run("ep01/youtube", "--root", root)
        with open(root / calibration.PATH, "a", encoding="utf-8") as f:
            f.write(f"ep01/youtube\tinventedVid\tB3\t17.7\t20\t-\t{SHA}\n")
        results = lint.run(root, ["calibration"])
        self.assertEqual([(f.line, f.rule) for f in results["calibration"].findings], [(4, "calibration-beat")])
        self.assertEqual(lint.text(results), "calibration FAIL 1 finding\nB1 calibration calibration/retention.tsv:4 "
                                             "beat B3 is not a beat ID of episodes/ep01/reel/edit-list.json\n")

    def test_a_row_with_no_post_behind_it_fails(self):
        root = self.tree()
        run("ep01/youtube", "--root", root)
        write(root, "registry/posts.tsv", REGISTRY_HEADER + "\n")
        results = lint.run(root, ["calibration"])
        self.assertEqual([(f.line, f.rule) for f in results["calibration"].findings],
                         [(2, "calibration-post"), (3, "calibration-post")])
        missing = "post ep01/youtube has no row in registry/posts.tsv"
        self.assertEqual(lint.text(results), "calibration FAIL 2 findings\n"
                         f"B1 calibration calibration/retention.tsv:2 {missing}\n"
                         f"B2 calibration calibration/retention.tsv:3 {missing}\n")


def beat(**edit):
    data = json.loads(json.dumps(RETENTION))
    data["beats"][0].update(edit)
    return data


def top(**edit):
    data = json.loads(json.dumps(RETENTION))
    data.update(edit)
    return data


class Refusals(Case):
    def refused(self, root, argument, message, old=None):
        if old is not None:
            write(root, calibration.PATH, old)
        code, out, err = run(argument, "--root", root)
        self.assertEqual((code, out, err), (1, "", f"calibration: {message}\n"))
        if old is None:
            self.assertFalse((root / "calibration").exists())
        else:
            self.assertEqual(self.tsv(root), old)

    def test_argument_not_a_post(self):
        self.refused(self.tree(), "ep01", "post ep01 is not <ep>/<platform>")

    def test_no_retention_file(self):
        self.refused(self.tree(None), "ep01/youtube", f"{RJ} is missing")

    def test_not_json(self):
        self.refused(self.tree("{"), "ep01/youtube", f"{RJ} is not JSON")

    def test_no_beats(self):
        self.refused(self.tree(top(beats=[])), "ep01/youtube", SHAPE)

    def test_beat_not_a_string(self):
        self.refused(self.tree(beat(beat=1)), "ep01/youtube", SHAPE)

    def test_start_a_boolean(self):
        self.refused(self.tree(beat(start=True)), "ep01/youtube", SHAPE)

    def test_negative_retention(self):
        self.refused(self.tree(beat(retention=-0.5)), "ep01/youtube", SHAPE)

    def test_numbers_the_lint_would_reject(self):
        for edit in ({"retention": 1e-07}, {"start": -0.0}):
            with self.subTest(edit=edit):
                self.refused(self.tree(beat(**edit)), "ep01/youtube", SHAPE)

    def test_tab_in_post_id(self):
        self.refused(self.tree(top(post_id="invented\tVid")), "ep01/youtube", SHAPE)

    def test_upper_case_sha(self):
        self.refused(self.tree(top(snapshot_sha256=SHA.upper())), "ep01/youtube", SHAPE)

    def test_missing_key(self):
        data = top()
        del data["snapshot_sha256"]
        self.refused(self.tree(data), "ep01/youtube", SHAPE)

    def test_file_of_another_post(self):
        self.refused(self.tree(top(post="ep01/tiktok")), "ep01/youtube",
                     f'{RJ} is for post "ep01/tiktok", not ep01/youtube')

    def test_old_file_with_another_header(self):
        old = "postid\tpost_id\tbeat\tstart\tend\tretention\tsnapshot_sha256\n"
        self.refused(self.tree(), "ep01/youtube", "calibration/retention.tsv: header is not post, post_id, beat, "
                     "start, end, retention, snapshot_sha256", old=old)

    def test_header_message_is_the_lints(self):
        self.assertEqual(calibration.HEADER_MESSAGE,
                         "header is not post, post_id, beat, start, end, retention, snapshot_sha256")


class EmptyIsNotZero(Case):
    def test_null_retention_writes_a_dash(self):
        root = self.tree(beat(retention=None))
        self.assertEqual(run("ep01/youtube", "--root", root)[0], 0)
        self.assertEqual(self.tsv(root).splitlines()[1], f"ep01/youtube\tinventedVid\tB1\t0\t7.7\t-\t{SHA}")
        self.assertEqual(self.lint(root), "calibration PASS\n")

    def test_zero_retention_writes_zero(self):
        root = self.tree(beat(retention=0))
        self.assertEqual(run("ep01/youtube", "--root", root)[0], 0)
        self.assertEqual(self.tsv(root).splitlines()[1], f"ep01/youtube\tinventedVid\tB1\t0\t7.7\t0\t{SHA}")
        self.assertEqual(self.lint(root), "calibration PASS\n")


IG = [f"ep01/instagram\tinventedIg\tB1\t0\t7.7\t0.5\t{SHA}", f"ep01/instagram\tinventedIg\tB2\t7.7\t17.7\t-\t{SHA}"]


class Merge(Case):
    def test_rows_of_another_post_are_kept(self):
        root = self.tree()
        write(root, calibration.PATH, "\t".join(calibration.HEADER) + "\n" + "\n".join(IG) + "\n")
        self.assertEqual(run("ep01/youtube", "--root", root)[0], 0)
        first = self.tsv(root)
        self.assertEqual(first, "\t".join(calibration.HEADER) + "\n" + "\n".join(IG) + "\n" + CLEAN.split("\n", 1)[1])
        self.assertEqual(run("ep01/youtube", "--root", root)[0], 0)
        self.assertEqual(self.tsv(root), first)

    def test_either_order_gives_the_same_bytes(self):
        texts = []
        for order in (("ep01/youtube", "ep01/instagram"), ("ep01/instagram", "ep01/youtube")):
            root = self.tree()
            write(root, "distribution/ep01/instagram/retention.json",
                  retention_text(top(post="ep01/instagram", post_id="inventedIg")))
            for post in order:
                self.assertEqual(run(post, "--root", root)[0], 0)
            texts.append(self.tsv(root))
        self.assertEqual(texts[0], texts[1])
        self.assertEqual([line.split("\t")[0] for line in texts[0].splitlines()[1:]],
                         ["ep01/instagram"] * 2 + ["ep01/youtube"] * 2)

    def test_an_old_row_of_the_post_is_replaced(self):
        root = self.tree()
        write(root, calibration.PATH, "\t".join(calibration.HEADER) + "\n"
              + f"ep01/youtube\toldVid\tB9\t0\t1\t0.1\t{SHA}\n")
        self.assertEqual(run("ep01/youtube", "--root", root)[0], 0)
        self.assertEqual(self.tsv(root), CLEAN)


class Interface(unittest.TestCase):
    def test_help_exits_0(self):
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as e:
            cmd_calibration.main(["--help"])
        self.assertEqual(e.exception.code, 0)

    def test_no_argument_exits_2(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
            cmd_calibration.main([])
        self.assertEqual(e.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
