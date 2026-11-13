"""kit/lib/retention.py and `factory retention`: a recorded retention curve mapped onto the beat IDs of a post."""

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import cmd_retention, retention  # noqa: E402

SNAPSHOT = ROOT / "tests" / "fixtures" / "lint_telemetry" / "clean" / "distribution" / "ep01" / "youtube" / "snapshot.json"
HEADER = "time post episode platform post_id visibility sha256 job pull head beats".split()
CLEAN = "B1=0-7.7,B2=7.7-17.7"
EVENTS = [{"shot": "Q1-1", "beat": "B1", "start": 0, "duration": 4.5},
          {"shot": "Q1-2", "beat": "B1", "start": 4.5, "duration": 3.2},
          {"shot": "Q1-3", "beat": "B2", "start": 7.7, "duration": 10}]
OUT = "distribution/ep01/youtube/retention.json"
FILE = ('{"beats": [{"beat": "B1", "end": 7.7, "first": 1, "last": 43, "retention": 0.89, "start": 0}, '
        '{"beat": "B2", "end": 17.7, "first": 44, "last": 100, "retention": 0.645, "start": 7.7}], '
        '"length": 17.7, "post": "ep01/youtube", "post_id": "inventedVid", '
        '"snapshot_sha256": "a0e416e293e6ee324abd97e5b51b112e2c685ab4bf19885b906e19c6d4dd5993"}')
NO_B2 = "beat B2 has no time range in the beat map of post ep01/youtube"


def points():
    """The points of #169's clean fixture: 1 - k/200 for k 1 to 98, then two nulls."""
    return [round(1 - k / 200, 3) for k in range(1, 99)] + [None, None]


def line(cells):
    return "\t".join(cells) + "\n"


def cells(post="ep01/youtube", beats=CLEAN, post_id="inventedVid"):
    ep, platform = post.split("/")
    return ["2026-11-13T12:00:00Z", post, ep, platform, post_id, "private", "0" * 64, "0" * 64, "4", "0" * 40, beats]


class Retention(unittest.TestCase):
    def tree(self, beats=CLEAN, events=EVENTS, curve=None, snapshot=None, registry=None):
        """A tree in a temporary directory: the registry row of ep01/youtube, its edit list and its snapshot."""
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        (root / "registry").mkdir()
        (root / "episodes/ep01/reel").mkdir(parents=True)
        (root / "distribution/ep01/youtube").mkdir(parents=True)
        (root / "registry/posts.tsv").write_text(
            registry if registry is not None else line(HEADER) + line(cells(beats=beats)), encoding="utf-8")
        (root / "episodes/ep01/reel/edit-list.json").write_text(
            json.dumps({"fps": 24, "bible": "A-1", "events": events}) + "\n", encoding="utf-8")
        if snapshot is None:
            snapshot = json.dumps({"metrics": {"audienceWatchRatio": points() if curve is None else curve},
                                   "video": "inventedVid"}) + "\n"
        (root / "distribution/ep01/youtube/snapshot.json").write_text(snapshot, encoding="utf-8")
        return root

    def run_cmd(self, root, post="ep01/youtube"):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_retention.main([post, "--root", str(root)])
        return code, out.getvalue(), err.getvalue()

    def beats(self, root):
        data = json.loads((root / OUT).read_text(encoding="utf-8"))
        return [(b["beat"], b["first"], b["last"], b["retention"]) for b in data["beats"]]

    def assertWritten(self, root, beats, n=None):
        code, out, err = self.run_cmd(root)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, f"retention ep01/youtube: {n or len(beats)} beats written to {OUT}\n")
        self.assertEqual(self.beats(root), beats)

    def assertRefused(self, root, *messages, post="ep01/youtube"):
        code, out, err = self.run_cmd(root, post)
        self.assertEqual((code, out, err), (1, "", "".join(f"retention: {m}\n" for m in messages)))
        self.assertFalse((root / OUT).exists())

    # Behaviour 1
    def test_default_tree(self):
        self.assertWritten(self.tree(), [("B1", 1, 43, 0.89), ("B2", 44, 100, 0.645)])

    def test_recorded_curve(self):
        root = self.tree()
        shutil.copyfile(SNAPSHOT, root / "distribution/ep01/youtube/snapshot.json")
        self.assertWritten(root, [("B1", 1, 43, 0.89), ("B2", 44, 100, 0.645)])
        self.assertEqual((root / OUT).read_text(encoding="utf-8"),
                         json.dumps(json.loads(FILE), indent=1, sort_keys=True) + "\n")

    # Behaviour 2
    def test_beat_with_no_time_range(self):
        for beats in ("B1=0-7.7", "B1=0-7.7,B2=", "B1=0-7.7,B2", "B1=0-7.7,B2=x-y", "B1=0-7.7,B2=7.7-7.7",
                      "B1=0-7.7,B2=17.7-7.7"):
            with self.subTest(beats=beats):
                self.assertRefused(self.tree(beats), NO_B2)

    def test_empty_map(self):
        self.assertRefused(self.tree("-"), "beat B1 has no time range in the beat map of post ep01/youtube", NO_B2)

    # Behaviour 3
    def test_beat_named_twice(self):
        self.assertRefused(self.tree("B1=0-7.7,B1=0-7.7,B2=7.7-17.7"),
                           "beat B1 is named twice in the beat map of post ep01/youtube")

    def test_beat_not_in_edit_list(self):
        not_in = "beat B3 of the beat map of post ep01/youtube is not in episodes/ep01/reel/edit-list.json"
        self.assertRefused(self.tree("B1=0-7.7,B2=7.7-17.7,B3=17.7-20"), not_in)
        self.assertRefused(self.tree("B1=0-7.7,B3=17.7-20"), NO_B2, not_in)

    # Behaviour 4
    def test_argument_is_not_a_post(self):
        self.assertRefused(self.tree(), "post ep01 is not <ep>/<platform>", post="ep01")

    def test_no_registry(self):
        root = self.tree()
        (root / "registry/posts.tsv").unlink()
        self.assertRefused(root, "registry/posts.tsv is missing")

    def test_header_without_beats(self):
        header = HEADER[:-1]
        self.assertRefused(self.tree(registry=line(header) + line(cells()[:-1])), "registry/posts.tsv has no column beats")

    def test_no_row(self):
        self.assertRefused(self.tree(), "registry/posts.tsv has no row for post ep02/youtube", post="ep02/youtube")

    def test_no_snapshot(self):
        root = self.tree()
        (root / "distribution/ep01/youtube/snapshot.json").unlink()
        self.assertRefused(root, "distribution/ep01/youtube/snapshot.json is missing")

    def test_snapshot_not_json(self):
        self.assertRefused(self.tree(snapshot="{"), "distribution/ep01/youtube/snapshot.json is not JSON")

    def test_points(self):
        message = "distribution/ep01/youtube/snapshot.json has no metrics.audienceWatchRatio of 100 points"
        for curve in (points()[:99], [True] + points()[1:], [-0.5] + points()[1:]):
            with self.subTest(curve=curve[:1]):
                self.assertRefused(self.tree(curve=curve), message)

    def test_video_is_not_post_id(self):
        snapshot = json.dumps({"metrics": {"audienceWatchRatio": points()}, "video": "otherVid123"})
        self.assertRefused(self.tree(snapshot=snapshot),
                           'snapshot video "otherVid123" is not post_id inventedVid of registry/posts.tsv')

    def test_no_edit_list(self):
        root = self.tree()
        (root / "episodes/ep01/reel/edit-list.json").unlink()
        self.assertRefused(root, "episodes/ep01/reel/edit-list.json is missing")

    def test_edit_list_without_events(self):
        self.assertRefused(self.tree(events=[]), "episodes/ep01/reel/edit-list.json is not {events: [{beat, ...}]}")

    def test_last_row_stands_and_short_line_is_skipped(self):
        registry = line(HEADER) + line(cells(beats="-")) + line(cells()) + line(cells(beats="-")[:-1])
        self.assertWritten(self.tree(registry=registry), [("B1", 1, 43, 0.89), ("B2", 44, 100, 0.645)])

    # Behaviour 5
    BOUNDARY = [{"shot": "S1", "beat": "B1", "start": 0, "duration": 10},
                {"shot": "S2", "beat": "B2", "start": 10, "duration": 9.7},
                {"shot": "S3", "beat": "B3", "start": 19.7, "duration": 0.3}]

    def test_boundaries(self):
        root = self.tree("B1=0-10,B2=10-19.7,B3=19.7-20", events=self.BOUNDARY)
        self.assertWritten(root, [("B1", 1, 50, 0.8725), ("B2", 51, 98, 0.6275), ("B3", 99, 100, None)])

    # Behaviour 6
    def test_beat_with_no_point(self):
        events = EVENTS[:2] + [{"shot": "Q1-3", "beat": "B2", "start": 7.7, "duration": 0.05},
                               {"shot": "Q1-4", "beat": "B3", "start": 7.75, "duration": 9.95}]
        root = self.tree("B1=0-7.7,B2=7.7-7.75,B3=7.75-17.7", events=events)
        self.assertWritten(root, [("B1", 1, 43, 0.89), ("B2", None, None, None), ("B3", 44, 100, 0.645)])

    def test_zero_is_kept(self):
        curve = points()
        curve[98] = 0
        root = self.tree("B1=0-10,B2=10-19.7,B3=19.7-20", events=self.BOUNDARY, curve=curve)
        self.assertWritten(root, [("B1", 1, 50, 0.8725), ("B2", 51, 98, 0.6275), ("B3", 99, 100, 0.0)])

    # Behaviour 7
    def test_edit_list_order(self):
        events = [dict(EVENTS[2], start=0), dict(EVENTS[0], start=10), dict(EVENTS[1], start=14.5)]
        root = self.tree("B1=10-17.7,B2=0-10", events=events)
        self.assertWritten(root, [("B2", 1, 56, 0.8575), ("B1", 57, 100, 0.6125)])

    def test_two_runs_are_byte_identical(self):
        root = self.tree()
        self.assertEqual(self.run_cmd(root)[0], 0)
        first = (root / OUT).read_bytes()
        self.assertEqual(self.run_cmd(root)[0], 0)
        self.assertEqual((root / OUT).read_bytes(), first)
        self.assertEqual(first, retention.build(root, "ep01/youtube").encode("utf-8"))

    def test_refusal_leaves_old_file(self):
        root = self.tree("B1=0-7.7")
        (root / OUT).write_text("old\n", encoding="utf-8")
        code, out, err = self.run_cmd(root)
        self.assertEqual((code, err), (1, f"retention: {NO_B2}\n"))
        self.assertEqual((root / OUT).read_text(encoding="utf-8"), "old\n")

    def test_help(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as raised:
            cmd_retention.main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("write retention per beat ID for one live post", out.getvalue())

    def test_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            cmd_retention.main([])
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
