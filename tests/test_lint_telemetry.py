"""Tests for kit/lib/lints/telemetry.py: a window's snapshot, and empty is not zero.

Each defect is made in a temporary copy of tests/fixtures/lint_telemetry/clean, the snapshot the tool
`youtube-analytics` writes for the recorded answers of tests/fixtures/tool_youtube_analytics/.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import lint  # noqa: E402
from kit.lib.lints import telemetry  # noqa: E402

CLEAN = ROOT / "tests" / "fixtures" / "lint_telemetry" / "clean"
ANSWERS = ROOT / "tests" / "fixtures" / "tool_youtube_analytics"
SNAPSHOT = "distribution/ep01/youtube/snapshot.json"
ZERO = "is 0, but the answers hold no value for it: empty is not zero"


def body(name):
    return json.loads((ANSWERS / f"{name}.json").read_text())["body"]


def clean():
    return json.loads((CLEAN / SNAPSHOT).read_text())


class TelemetryLintTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        shutil.copytree(CLEAN, self.root, dirs_exist_ok=True)
        self.path = self.root / SNAPSHOT

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, data, rel=SNAPSHOT):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")

    def edit(self, change):
        data = clean()
        change(data)
        self.write(data)

    def result(self):
        return lint.run(self.root, ["telemetry"])["telemetry"]

    def findings(self):
        result = self.result()
        self.assertNotEqual(result.status, lint.ERROR, result.reason)
        return [(f.path, f.line, f.rule, f.message) for f in result.findings]

    def assertOne(self, rule, message):
        self.assertEqual(self.findings(), [(SNAPSHOT, 1, rule, message)])

    def test_clean_passes(self):
        self.assertEqual(self.result().status, lint.PASS)

    def test_snapshot_of_the_fixture_answers_is_the_clean_fixture(self):
        text = telemetry.snapshot("youtube", "inventedVid", "2026-11-13", "2026-11-19",
                                  [body("totals-ok"), body("retention-ok")])
        self.assertEqual(text, (CLEAN / SNAPSHOT).read_text())

    def test_clean_metrics_end_with_two_empty_points(self):
        points = clean()["metrics"]["audienceWatchRatio"]
        self.assertEqual(len(points), 100)
        self.assertEqual(points[98:], [None, None])
        self.assertEqual(points[0], 0.995)

    def test_empty_totals_are_null_and_pass(self):
        answers = [body("totals-empty"), body("retention-ok")]
        self.path.write_text(telemetry.snapshot("youtube", "inventedVid", "2026-11-13", "2026-11-19", answers))
        metrics = json.loads(self.path.read_text())["metrics"]
        self.assertEqual([metrics[k] for k in ("views", "averageViewDuration", "averageViewPercentage")],
                         [None, None, None])
        self.assertEqual(self.result().status, lint.PASS)

    def test_empty_views_written_as_zero_fails(self):
        answers = [body("totals-empty"), body("retention-ok")]
        data = json.loads(telemetry.snapshot("youtube", "inventedVid", "2026-11-13", "2026-11-19", answers))
        data["metrics"]["views"] = 0
        self.write(data)
        self.assertOne("zero", f"views {ZERO}")

    def test_empty_point_written_as_zero_fails(self):
        self.edit(lambda d: d["metrics"]["audienceWatchRatio"].__setitem__(98, 0))
        self.assertOne("zero", f"audienceWatchRatio point 99 {ZERO}")

    def test_empty_point_written_as_float_zero_fails(self):
        self.edit(lambda d: d["metrics"]["audienceWatchRatio"].__setitem__(99, 0.0))
        self.assertOne("zero", f"audienceWatchRatio point 100 {ZERO}")

    def test_a_zero_the_answers_hold_passes(self):
        def change(data):
            data["answers"][0]["rows"][0][0] = 0
            data["metrics"]["views"] = 0
        self.edit(change)
        self.assertEqual(self.result().status, lint.PASS)

    def test_other_platform_has_no_schema(self):
        moved = self.root / "distribution/ep01/tiktok/snapshot.json"
        moved.parent.mkdir(parents=True)
        self.path.rename(moved)
        self.assertEqual(self.findings(), [("distribution/ep01/tiktok/snapshot.json", 1, "snapshot",
                                            "no telemetry schema for platform 'tiktok'")])

    def test_platform_other_than_the_directory(self):
        self.edit(lambda d: d.__setitem__("platform", "tiktok"))
        self.assertOne("snapshot", "platform is \"tiktok\", the directory says 'youtube'")

    def test_bad_start_and_video(self):
        self.edit(lambda d: d.update(start="soon", video=""))
        self.assertEqual(self.findings(), [(SNAPSHOT, 1, "snapshot", "start is not a date YYYY-MM-DD"),
                                           (SNAPSHOT, 1, "snapshot", "video is not a video id")])

    def test_answers_not_two_tables(self):
        self.edit(lambda d: d.__setitem__("answers", []))
        self.assertOne("answers", "answers is not a list of two result tables {columnHeaders, rows}")

    def test_metrics_without_views(self):
        self.edit(lambda d: d["metrics"].pop("views"))
        self.assertOne("metrics", "metrics is not an object "
                                  "{audienceWatchRatio, averageViewDuration, averageViewPercentage, views}")

    def test_point_removed(self):
        self.edit(lambda d: d["metrics"]["audienceWatchRatio"].pop())
        self.assertOne("metrics", "audienceWatchRatio is not a list of 100 points")

    def test_boolean_views(self):
        self.edit(lambda d: d["metrics"].__setitem__("views", True))
        self.assertOne("metrics", "views is true, the answers give 1200")

    def test_other_views(self):
        self.edit(lambda d: d["metrics"].__setitem__("views", 1100))
        self.assertOne("metrics", "views is 1100, the answers give 1200")

    def test_not_an_object(self):
        self.write([])
        self.assertOne("snapshot", "not an object {platform, video, start, end, answers, metrics}")

    def test_not_json_is_error(self):
        self.path.write_text("{not json\n")
        self.assertEqual(self.result().status, lint.ERROR)

    def test_request_alone_passes(self):
        self.path.unlink()
        self.write({"end": "2026-11-19", "start": "2026-11-13", "video": "inventedVid"},
                   "distribution/ep01/youtube/requests/window.json")
        self.assertEqual(self.result().status, lint.PASS)


class MetricsTest(unittest.TestCase):
    def test_not_two_tables(self):
        for answers in (None, [], [body("totals-ok")], [body("totals-ok"), {"rows": []}],
                        [body("totals-ok"), {"columnHeaders": [], "rows": [1]}]):
            self.assertIsNone(telemetry.metrics("youtube", answers), answers)

    def test_unknown_platform(self):
        self.assertIsNone(telemetry.metrics("tiktok", [body("totals-ok"), body("retention-ok")]))

    def test_values(self):
        metrics = telemetry.metrics("youtube", [body("totals-ok"), body("retention-ok")])
        self.assertEqual((metrics["views"], metrics["averageViewDuration"], metrics["averageViewPercentage"]),
                         (1200, 31, 72.5))
        self.assertEqual(metrics["audienceWatchRatio"][:2], [0.995, 0.99])

    def test_two_rows_of_totals_give_null(self):
        totals = body("totals-ok")
        totals["rows"].append([1, 2, 3])
        metrics = telemetry.metrics("youtube", [totals, body("retention-ok")])
        self.assertEqual(metrics["views"], None)

    def test_negative_or_boolean_values_give_null(self):
        totals = body("totals-ok")
        totals["rows"] = [[-1, True, "72.5"]]
        metrics = telemetry.metrics("youtube", [totals, body("retention-ok")])
        self.assertEqual([metrics["views"], metrics["averageViewDuration"], metrics["averageViewPercentage"]],
                         [None, None, None])


if __name__ == "__main__":
    unittest.main()
