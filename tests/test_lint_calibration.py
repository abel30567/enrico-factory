"""calibration: the lint of the judge calibration set (ISSUE-188), on trees built in temporary directories."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import calibration, lint  # noqa: E402

SHA = "a0e416e293e6ee324abd97e5b51b112e2c685ab4bf19885b906e19c6d4dd5993"
REGISTRY_HEADER = "time\tpost\tepisode\tplatform\tpost_id\tvisibility\tsha256\tjob\tpull\thead\tbeats"
EDIT_LIST = {"fps": 24, "bible": "A-1", "events": [
    {"shot": "Q1-1", "beat": "B1", "start": 0, "duration": 4.5},
    {"shot": "Q1-2", "beat": "B1", "start": 4.5, "duration": 3.2},
    {"shot": "Q1-3", "beat": "B2", "start": 7.7, "duration": 10}]}
RETENTION = {"beats": [{"beat": "B1", "end": 7.7, "first": 1, "last": 43, "retention": 0.89, "start": 0},
                       {"beat": "B2", "end": 17.7, "first": 44, "last": 100, "retention": 0.645, "start": 7.7}],
             "length": 17.7, "post": "ep01/youtube", "post_id": "inventedVid", "snapshot_sha256": SHA}
HEADER = "\t".join(calibration.HEADER)
B1 = ["ep01/youtube", "inventedVid", "B1", "0", "7.7", "0.89", SHA]
B2 = ["ep01/youtube", "inventedVid", "B2", "7.7", "17.7", "0.645", SHA]
P = calibration.PATH


def registry_row(post="ep01/youtube", post_id="inventedVid"):
    ep, platform = post.split("/")
    return "\t".join(["2026-11-13T12:00:00Z", post, ep, platform, post_id, "private", "0" * 64, "0" * 64, "4",
                      "0" * 40, "B1=0-7.7,B2=7.7-17.7"])


def write(root, rel, text):
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(text.encode("utf-8"))


def row(base, **edit):
    cells = dict(zip(calibration.HEADER, base))
    cells.update(edit)
    return "\t".join(cells[c] for c in calibration.HEADER)


class Case(unittest.TestCase):
    def tree(self, rows=None, header=HEADER, registry=None):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        if registry is None:
            registry = REGISTRY_HEADER + "\n" + registry_row() + "\n"
        write(root, "registry/posts.tsv", registry)
        write(root, "episodes/ep01/reel/edit-list.json", json.dumps(EDIT_LIST) + "\n")
        write(root, "distribution/ep01/youtube/retention.json", json.dumps(RETENTION, indent=1, sort_keys=True) + "\n")
        lines = [header] + (rows if rows is not None else ["\t".join(B1), "\t".join(B2)])
        write(root, P, "\n".join(lines) + "\n")
        return root

    def findings(self, root):
        r = lint.run(root, ["calibration"])["calibration"]
        self.assertNotEqual(r.status, lint.ERROR, r.reason)
        return [(f.line, f.rule, f.message) for f in r.findings]

    def passes(self, root):
        self.assertEqual(lint.text(lint.run(root, ["calibration"])), "calibration PASS\n")


REG_MISSING = "post ep01/youtube has no row in registry/posts.tsv"
NO_POST = [(2, "calibration-post", REG_MISSING), (3, "calibration-post", REG_MISSING)]


class Post(Case):
    def test_clean(self):
        self.passes(self.tree())

    def test_no_registry(self):
        root = self.tree()
        (root / "registry/posts.tsv").unlink()
        self.assertEqual(self.findings(root), NO_POST)

    def test_no_post_id_column(self):
        root = self.tree(registry=REGISTRY_HEADER.replace("post_id", "postid") + "\n" + registry_row() + "\n")
        self.assertEqual(self.findings(root), NO_POST)

    def test_post_with_no_registry_row(self):
        root = self.tree(rows=[row(B1, post="ep02/youtube")])
        self.assertIn((2, "calibration-post", "post ep02/youtube has no row in registry/posts.tsv"),
                      self.findings(root))

    def test_post_id_not_of_the_post(self):
        root = self.tree(rows=[row(B1, post_id="otherVid123"), "\t".join(B2)])
        self.assertEqual(self.findings(root), [(2, "calibration-post",
                         "post_id otherVid123 is not a post_id of post ep01/youtube in registry/posts.tsv")])

    def test_every_post_id_of_the_post_counts(self):
        registry = REGISTRY_HEADER + "\n" + registry_row() + "\n" + registry_row(post_id="inventedVid2") + "\n"
        self.passes(self.tree(rows=["\t".join(B1), row(B2, post_id="inventedVid2")], registry=registry))

    def test_registry_line_one_cell_short_is_not_a_row(self):
        short = registry_row().rsplit("\t", 1)[0]
        root = self.tree(registry=REGISTRY_HEADER + "\n" + short + "\n")
        self.assertEqual(self.findings(root), NO_POST)


B3 = "beat B3 is not a beat ID of episodes/ep01/reel/edit-list.json"


class Beat(Case):
    def test_beat_not_in_the_edit_list(self):
        root = self.tree(rows=["\t".join(B1), row(B2, beat="B3")])
        self.assertEqual(self.findings(root), [(3, "calibration-beat", B3)])

    def test_no_edit_list(self):
        root = self.tree()
        (root / "episodes/ep01/reel/edit-list.json").unlink()
        self.assertEqual([f[:2] for f in self.findings(root)], [(2, "calibration-beat"), (3, "calibration-beat")])
        self.assertEqual(self.findings(root)[0][2], "beat B1 is not a beat ID of episodes/ep01/reel/edit-list.json")

    def test_edit_list_not_an_object(self):
        for text in ("[]\n", "{\n", '{"events": {}}\n'):
            with self.subTest(text=text):
                root = self.tree()
                write(root, "episodes/ep01/reel/edit-list.json", text)
                self.assertEqual([f[:2] for f in self.findings(root)],
                                 [(2, "calibration-beat"), (3, "calibration-beat")])

    def test_beat_named_by_two_events(self):
        self.passes(self.tree(rows=["\t".join(B1)]))


class Shape(Case):
    def test_header_not_the_header(self):
        root = self.tree(header=HEADER.replace("post_id", "postid"), rows=["x"])
        self.assertEqual(self.findings(root), [(1, "calibration-header", calibration.HEADER_MESSAGE)])

    def test_empty_file(self):
        root = self.tree()
        write(root, P, "")
        self.assertEqual(self.findings(root), [(1, "calibration-header",
                         "header is not post, post_id, beat, start, end, retention, snapshot_sha256")])

    def test_six_cells(self):
        root = self.tree(rows=["\t".join(B1[:6]), "\t".join(B2)])
        self.assertEqual(self.findings(root), [(2, "calibration-row", "row does not have the 7 cells of the header")])

    def test_post_not_a_post(self):
        root = self.tree(rows=[row(B1, post="ep01", start="x"), "\t".join(B2)])
        self.assertEqual(self.findings(root), [(2, "calibration-row", "post ep01 is not <ep>/<platform>")])

    def test_start_not_a_number(self):
        root = self.tree(rows=[row(B1, start="x"), "\t".join(B2)])
        self.assertEqual(self.findings(root), [(2, "calibration-row", "start x is not a number of seconds")])

    def test_end_not_a_number(self):
        root = self.tree(rows=[row(B1, end="1e3"), "\t".join(B2)])
        self.assertEqual(self.findings(root), [(2, "calibration-row", "end 1e3 is not a number of seconds")])

    def test_end_equal_to_start(self):
        root = self.tree(rows=[row(B1, start="7.7", end="7.70"), "\t".join(B2)])
        self.assertEqual(self.findings(root), [(2, "calibration-row", "end 7.70 is not after start 7.7")])

    def test_retention_not_a_number(self):
        root = self.tree(rows=[row(B1, retention="1.5x"), "\t".join(B2)])
        self.assertEqual(self.findings(root), [(2, "calibration-row", "retention 1.5x is not a number or -")])

    def test_upper_case_sha(self):
        root = self.tree(rows=[row(B1, snapshot_sha256=SHA.upper()), "\t".join(B2)])
        self.assertEqual(self.findings(root), [(2, "calibration-row",
                         f"snapshot_sha256 {SHA.upper()} is not 64 lower-case hex characters")])

    def test_repeated_post_and_beat(self):
        root = self.tree(rows=["\t".join(B1), "\t".join(B2), row(B1, retention="0.5")])
        self.assertEqual(self.findings(root), [(4, "calibration-row",
                         "beat B1 of post ep01/youtube is repeated (first on line 2)")])

    def test_retention_above_one_and_dash_pass(self):
        self.passes(self.tree(rows=[row(B1, retention="1.2"), row(B2, retention="-")]))


class Scope(Case):
    def test_no_calibration_file(self):
        root = self.tree()
        (root / P).unlink()
        self.passes(root)

    def test_other_files_under_calibration_are_not_read(self):
        root = self.tree()
        (root / P).unlink()
        write(root, "calibration/notes.md", "post\tnot a row\n\x00")
        self.passes(root)

    def test_text_is_deterministic(self):
        root = self.tree(rows=[row(B1, beat="B3", post_id="otherVid123"), row(B2, start="x"), "\t".join(B2)])
        first = lint.text(lint.run(root, ["calibration"]))
        self.assertIn("calibration FAIL", first)
        self.assertEqual(lint.text(lint.run(root, ["calibration"])), first)


if __name__ == "__main__":
    unittest.main()
