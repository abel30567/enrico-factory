"""layout: rules 1 to 7."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lint import LintCase, lint  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "lint_layout"
STATE = "production/ep01/Q1-1/layout/state.json"
SHOTS = "production/ep01/shots.json"
SHOT = "episodes/ep01/sequences/Q1/shots/Q1-1.json"
UNREAD = ("motion", "lipsync", "caches", "lights", "sun", "render")


def load(rel, base="clean"):
    return json.loads((FIX / base / rel).read_text(encoding="utf-8"))


def dump(data):
    return json.dumps(data, indent=2) + "\n"


class Layout(LintCase):
    CHECKS = ("canon", "kit", "markers", "media", "provenance", "references", "spoiler")

    def layout(self, root):
        return lint.run(root, only=["layout"])["layout"]

    def messages(self, root):
        return [f.message for f in self.layout(root).findings]

    def state(self, change):
        """A copy of clean/ whose state is the clean state after `change(state)`."""
        state = load(STATE)
        change(state)
        return self.variant({STATE: dump(state)}, base=FIX / "clean")

    def assertPasses(self, root):
        result = self.layout(root)
        self.assertEqual(result.status, lint.PASS, f"{result.findings} {result.reason}")

    def assertFinding(self, root, rule, message):
        self.assertOnly(root, "layout", [(STATE, 1, rule)])
        self.assertEqual(self.messages(root), [message])

    # 1
    def test_clean_passes_layout_and_every_check_of_checks(self):
        self.assertPasses(FIX / "clean")
        results = lint.run(FIX / "clean", only=self.CHECKS)
        self.assertEqual({k: r.status for k, r in results.items()}, dict.fromkeys(self.CHECKS, lint.PASS))

    def test_empty_tree_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertPasses(Path(tmp))

    # 2
    def test_rule1_lens(self):
        self.assertFinding(FIX / "r1_lens", "lens", "focal length 12-50 mm is outside 18-135 mm")
        root = self.state(lambda s: s["camera"]["focal_mm"].update(max=136))
        self.assertFinding(root, "lens", "focal length 35-136 mm is outside 18-135 mm")

    def test_rule1_both_bounds_pass(self):
        self.assertPasses(self.state(lambda s: s["camera"]["focal_mm"].update(min=18, max=135)))

    # 3
    def test_rule2_framing(self):
        self.assertFinding(FIX / "r2_framing", "framing", "character tobi has no framing box")

    def test_rule2_a_set_needs_no_box(self):
        def drop(s):
            s["framing"] = [e for e in s["framing"] if e["actor"] != "lighthouse"]
        self.assertPasses(self.state(drop))

    # 4
    def test_rule3_safe_zone(self):
        self.assertFinding(FIX / "r3_safe_zone", "safe-zone",
                           "character tobi leaves the 9:16 safe zone (left 0.342, right 0.658) "
                           "on 1 of its 2 boxes, first on frame 119")

    def test_rule3_left_edge(self):
        root = self.state(lambda s: s["framing"][0]["box"].__setitem__(0, 0.341))
        self.assertFinding(root, "safe-zone", "character ama leaves the 9:16 safe zone (left 0.342, right 0.658) "
                                              "on 1 of its 2 boxes, first on frame 0")

    def test_rule3_both_bounds_and_the_set_pass(self):
        self.assertIn({"frame": 0, "actor": "lighthouse", "box": [0, 0, 1, 1]}, load(STATE)["framing"])
        self.assertPasses(FIX / "clean")
        root = self.state(lambda s: s["framing"][0].update(box=[0.342, 0, 0.658, 1]))
        self.assertPasses(root)

    # 5
    def test_rule4_line(self):
        self.assertFinding(FIX / "r4_line", "line", "camera crosses the 180-degree line on frames: 60")
        root = self.state(lambda s: s.update(line_flips=[90, 60, 60]))
        self.assertFinding(root, "line", "camera crosses the 180-degree line on frames: 60, 90")

    # 6
    def test_rule5_duration(self):
        self.assertFinding(FIX / "r5_duration", "duration",
                           "frames 0-95 do not match 0-119 in production/ep01/shots.json")
        rows = load(SHOTS)
        rows[0]["first"] = 1
        root = self.variant({SHOTS: dump(rows)}, base=FIX / "clean")
        self.assertFinding(root, "duration", "frames 0-119 do not match 1-119 in production/ep01/shots.json")

    def test_rule5_shot_file_duration_is_not_compared(self):
        shot = load(SHOT)
        shot["duration"] = 9
        self.assertPasses(self.variant({SHOT: dump(shot)}, base=FIX / "clean"))

    # 7
    def test_rule6_state_shape(self):
        cases = [
            (lambda s: s.update(shot="Q1-2"), "shot is 'Q1-2', not the directory's 'Q1-1'"),
            (lambda s: s.update(stage="render"), "stage is not 'layout'"),
            (lambda s: s.update(fps=0), "fps is not a whole number above zero"),
            (lambda s: s.update(fps=True), "fps is not a whole number above zero"),
            (lambda s: s.update(fps=24.0), "fps is not a whole number above zero"),
            (lambda s: s["frames"].update(last="119"), "frames is not {first, last} with whole numbers"),
            (lambda s: s["actors"][2].update(kind="building"), "actors is not a list of {id, kind, ...}"),
            (lambda s: s["camera"]["focal_mm"].pop("min"), "camera is not {focal_mm: {min, max}, ...} with numbers"),
            (lambda s: s["framing"][1].update(box=[0.5, 0.4, 0.6]),
             "framing is not a list of {frame, actor, box: [left, top, right, bottom]}"),
            (lambda s: s.update(line_flips=["60"]), "line_flips is not a list of frame numbers"),
        ]
        for key in ("shot", "stage", "fps", "frames", "actors", "camera", "framing", "line_flips"):
            cases.append(((lambda k: lambda s: s.pop(k))(key), f"missing key '{key}'"))
        for change, message in cases:
            with self.subTest(message):
                self.assertFinding(self.state(change), "state", message)

    def test_rule6_a_state_that_is_a_list(self):
        root = self.variant({STATE: "[]\n"}, base=FIX / "clean")
        self.assertFinding(root, "state", "not a JSON object")

    def test_rule6_unread_keys_removed_passes(self):
        def drop(s):
            for key in UNREAD:
                s.pop(key)
        self.assertPasses(self.state(drop))

    # 8
    def test_rule7_depends(self):
        rows = [r for r in load(SHOTS) if r["shot"] != "Q1-1"]
        cases = [({SHOTS: dump({"shot": "Q1-1", "first": 0, "last": 119})},
                  "production/ep01/shots.json has no row for Q1-1"),
                 ({SHOTS: dump(rows)}, "production/ep01/shots.json has no row for Q1-1")]
        for files, message in cases:
            with self.subTest(message):
                self.assertFinding(self.variant(files, base=FIX / "clean"), "depends", message)
        for rel, message in ((SHOTS, "production/ep01/shots.json is missing"),
                             (SHOT, "episodes/ep01/sequences/*/shots/Q1-1.json is missing")):
            with self.subTest(message):
                root = self.variant({}, base=FIX / "clean")
                (root / rel).unlink()
                self.assertEqual(self.layout(root).status, lint.FAIL)
                self.assertEqual([(f.rule, f.message) for f in self.layout(root).findings], [("depends", message)])

    def test_rule7_no_shot_file_skips_framing(self):
        root = self.variant({}, base=FIX / "r2_framing")
        (root / SHOT).unlink()
        self.assertEqual([(f.rule, f.message) for f in self.layout(root).findings],
                         [("depends", "episodes/ep01/sequences/*/shots/Q1-1.json is missing")])

    # 9
    def test_state_that_is_not_json_is_error(self):
        result = self.layout(self.variant({STATE: "{"}, base=FIX / "clean"))
        self.assertEqual(result.status, lint.ERROR)
        self.assertTrue(result.reason.startswith("cannot parse production/ep01/Q1-1/layout/state.json:1:"),
                        result.reason)

    # 10
    def test_two_shots_sorted_and_deterministic(self):
        second = load(STATE)
        second.update(shot="Q1-2", frames={"first": 120, "last": 400}, line_flips=[200])
        second["camera"]["focal_mm"]["max"] = 200
        shot = load(SHOT)
        shot["id"] = "Q1-2"
        root = self.variant({"production/ep01/Q1-2/layout/state.json": dump(second),
                             "episodes/ep01/sequences/Q1/shots/Q1-2.json": dump(shot),
                             STATE: dump(dict(load(STATE), line_flips=[60]))}, base=FIX / "clean")
        result = self.layout(root)
        q2 = "production/ep01/Q1-2/layout/state.json"
        self.assertEqual([(f.path, f.rule) for f in result.findings],
                         [(STATE, "line"), (q2, "duration"), (q2, "lens"), (q2, "line")])
        self.assertEqual(result.findings, sorted(result.findings))
        first = lint.to_json(lint.run(root, only=["layout"]))
        self.assertEqual(first, lint.to_json(lint.run(root, only=["layout"])))


if __name__ == "__main__":
    unittest.main()
