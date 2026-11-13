"""animation: rules state (1), motion (2), lipsync (3)."""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lint import LintCase, lint  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "lint_animation"
EP = "production/ep01"


def state_rel(shot, ep=EP):
    return f"{ep}/{shot}/animation/state.json"


def dump(data):
    return json.dumps(data, indent=2) + "\n"


def clean_state(shot):
    return json.loads((FIX / "clean" / state_rel(shot)).read_text(encoding="utf-8"))


class Animation(LintCase):
    CHECKS = ("assets", "canon", "conform", "cross-shot", "intake", "kit", "markers", "media", "provenance",
              "references", "spoiler", "structure")

    def animation(self, root):
        return lint.run(root, only=["animation"])["animation"]

    def found(self, root):
        result = self.animation(root)
        self.assertNotEqual(result.status, lint.ERROR, result.reason)
        return [(f.path, f.line, f.rule, f.message) for f in result.findings]

    def edit(self, change, shot="Q1-1"):
        """The clean fixture with `change` applied to the state of `shot`."""
        data = clean_state(shot)
        change(data)
        return self.variant({state_rel(shot): dump(data)}, base=FIX / "clean")

    def test_1_clean_passes_every_check(self):
        result = self.animation(FIX / "clean")
        self.assertEqual(result.status, lint.PASS, result.findings)
        others = lint.run(FIX / "clean", only=list(self.CHECKS))
        self.assertEqual({cid: r.status for cid, r in others.items()},
                         {cid: lint.PASS for cid in self.CHECKS})

    def test_2_seeded_defects(self):
        cases = (
            ("foot-slide", "motion", "actor ama: foot_slide_mm 12.5 (at most 5)"),
            ("pop", "motion", "actor ama: max_step_deg 47.5 (at most 30)"),
            ("interpenetration", "motion", "actor ama: penetration_mm 8.25 (at most 5)"),
            ("lip-sync", "lipsync", "line L3 (actor ama): offset_frames 3 (from -1 to 1)"),
        )
        for name, rule, message in cases:
            with self.subTest(name):
                self.assertOnly(FIX / name, "animation", [(state_rel("Q1-1"), 1, rule)])
                self.assertEqual(self.animation(FIX / name).findings[0].message, message)

    def test_3_limits(self):
        def ama(key, value):
            return lambda d: d["motion"][0].__setitem__(key, value)

        cases = (("foot_slide_mm", 5, 5.01, 5), ("penetration_mm", 5.0, 5.5, 5), ("float_mm", 5, 6, 5),
                 ("max_step_deg", 30, 30.5, 30), ("limit_violations", 0, 2, 0))
        for key, at, over, limit in cases:
            with self.subTest(key=key):
                self.assertEqual(self.found(self.edit(ama(key, at))), [])
                self.assertEqual(self.found(self.edit(ama(key, over))), [
                    (state_rel("Q1-1"), 1, "motion", f"actor ama: {key} {over} (at most {limit})")])
        self.assertEqual(self.found(self.edit(ama("limit_violations", 2)))[0][3],
                         "actor ama: limit_violations 2 (at most 0)")
        for value in (-2, 2):
            with self.subTest(offset_frames=value):
                root = self.edit(lambda d: d["lipsync"][1].__setitem__("offset_frames", value))
                self.assertEqual(self.found(root), [
                    (state_rel("Q1-1"), 1, "lipsync",
                     f"line L2 (actor tobi): offset_frames {value} (from -1 to 1)")])
        self.assertEqual([e["offset_frames"] for e in clean_state("Q1-1")["lipsync"]], [0, -1, 1])
        self.assertEqual(self.found(FIX / "clean"), [])

    def test_4_state_defects(self):
        motion = "is not {actor, foot_slide_mm, penetration_mm, float_mm, limit_violations, max_step_deg}"
        lipsync = "is not {line, actor, offset_frames}"

        def tobi(key, value=None):
            if value is None:
                return lambda d: d["motion"][1].pop(key)
            return lambda d: d["motion"][1].__setitem__(key, value)

        def no_float_slides(d):
            d["motion"][1].pop("float_mm")
            d["motion"][1]["foot_slide_mm"] = 50

        cases = (
            (lambda d: d.__setitem__("stage", "layout"), "stage is not 'animation'"),
            (lambda d: d.__setitem__("shot", "Q1-2"), "shot is not 'Q1-1'"),
            (lambda d: d.pop("motion"), "motion is not a list"),
            (lambda d: d.__setitem__("lipsync", None), "lipsync is not a list"),
            (tobi("float_mm"), f"motion[1] {motion}"),
            (tobi("penetration_mm", -1), f"motion[1] {motion}"),
            (tobi("limit_violations", 1.0), f"motion[1] {motion}"),
            (tobi("max_step_deg", True), f"motion[1] {motion}"),
            (no_float_slides, f"motion[1] {motion}"),
            (lambda d: d["lipsync"][0].__setitem__("offset_frames", 0.5), f"lipsync[0] {lipsync}"),
            (lambda d: d["lipsync"][0].pop("actor"), f"lipsync[0] {lipsync}"),
            (lambda d: d["motion"].append(copy.deepcopy(d["motion"][0])),
             "actor ama is listed twice in motion"),
            (lambda d: d["lipsync"].append(copy.deepcopy(d["lipsync"][0])),
             "line L1 is listed twice in lipsync"),
        )
        for change, message in cases:
            with self.subTest(message):
                self.assertEqual(self.found(self.edit(change)), [(state_rel("Q1-1"), 1, "state", message)])
        self.assertEqual(self.found(self.edit(lambda d: d.pop("actors"))), [])
        self.assertEqual(self.found(self.edit(lambda d: d.__setitem__("motion", []))), [])

    def test_5_state_not_json_is_error(self):
        root = self.variant({state_rel("Q1-2"): "{\n  \"shot\": \n"}, base=FIX / "clean")
        result = self.animation(root)
        self.assertEqual(result.status, lint.ERROR)
        self.assertTrue(result.reason.startswith(f"cannot parse {state_rel('Q1-2')}:"), result.reason)

    def test_6_output_is_deterministic(self):
        q1_1, q1_2 = clean_state("Q1-1"), clean_state("Q1-2")
        q1_1["stage"] = "layout"
        q1_1["lipsync"][2]["offset_frames"] = -4
        q1_2["motion"][0]["float_mm"] = 7
        ep02 = clean_state("Q1-1")
        ep02["shot"] = "Q3-1"
        ep02["motion"][1]["max_step_deg"] = 31
        root = self.variant({state_rel("Q1-1"): dump(q1_1), state_rel("Q1-2"): dump(q1_2),
                             state_rel("Q3-1", "production/ep02"): dump(ep02)}, base=FIX / "clean")
        first = lint.text(lint.run(root, only=["animation"]))
        self.assertEqual({f.rule for f in self.animation(root).findings}, {"state", "motion", "lipsync"})
        self.assertEqual({f.path for f in self.animation(root).findings},
                         {state_rel("Q1-1"), state_rel("Q1-2"), state_rel("Q3-1", "production/ep02")})
        self.assertEqual(lint.text(lint.run(root, only=["animation"])), first)


if __name__ == "__main__":
    unittest.main()
