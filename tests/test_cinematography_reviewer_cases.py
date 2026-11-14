"""The cases of kit/evals/cinematography-reviewer/: framing boxes that follow their cameras, and catches
that read a role's notes as written.

A box is what the camera sees of an upright block 0.3 m wide, centred on the actor's location x, from
its location z to z + height_m. Every camera looks along +y with +x to the right and +z up.
"""

import copy
import json
import unittest
from pathlib import Path

from kit.lib import evals, lint

ROOT = Path(__file__).resolve().parent.parent
SUITE = ROOT / "kit" / "evals" / "cinematography-reviewer"
CASES = tuple(sorted(p.name for p in SUITE.iterdir() if p.is_dir()))
SHOTS = ("Q1-1", "Q1-2")
CROP = (0.342, 0.658)
LENS_LINE = "- Lenses from 24 to 85 mm."
CROP_LINE = ("- Every shot also plays in the centred 9:16 crop of its 16:9 frame, from 0.342 to 0.658 of its "
             "width: keep every character inside that crop.")


def state(case, shot):
    path = SUITE / case / "tree" / "production" / "ep01" / shot / "layout" / "state.json"
    return json.loads(path.read_text(encoding="utf-8"))


def characters(doc):
    return [a["id"] for a in doc["actors"] if a["kind"] == "character"]


def box(doc, actor, frame):
    """The box ``[left, top, right, bottom]`` the camera of ``doc`` sees of ``actor`` on ``frame``."""
    cam = doc["camera"]
    f = cam["focal_mm"]["min"]
    w, h = cam["sensor_mm"]
    first, last = doc["frames"]["first"], doc["frames"]["last"]
    t = (frame - first) / (last - first)
    start, end = cam["start"]["location"], cam["end"]["location"]
    cx, cy, cz = (s + t * (e - s) for s, e in zip(start, end))
    a = next(a for a in doc["actors"] if a["id"] == actor)
    x, y, z = a["start"]["location"]
    d = y - cy

    def u(value):
        return 0.5 + f * (value - cx) / (w * d)

    def v(value):
        return 0.5 - f * (value - cz) / (h * d)

    return [round(float(min(1.0, max(0.0, q))), 3) for q in (u(x - 0.15), v(z + a["height_m"]), u(x + 0.15), v(z))]


def box_messages(doc, where):
    """One message per framing entry with any value more than 0.001 from the box of its camera."""
    messages = []
    for entry in doc["framing"]:
        want = box(doc, entry["actor"], entry["frame"])
        if any(abs(g - w) > 0.001 for g, w in zip(entry["box"], want)) or len(entry["box"]) != 4:
            messages.append(f"{where}: frame {entry['frame']} {entry['actor']} box {entry['box']} is not {want}")
    return messages


def pairs(doc):
    return [(e["frame"], e["actor"]) for e in doc["framing"]]


def every_pair(doc):
    return [(n, a) for n in range(doc["frames"]["first"], doc["frames"]["last"] + 1) for a in characters(doc)]


def first_boxes(doc):
    first = doc["frames"]["first"]
    return [(e["actor"], e["box"]) for e in doc["framing"] if e["frame"] == first]


class BoxesTest(unittest.TestCase):
    def test_every_box_follows_its_camera(self):
        messages = [m for case in CASES for shot in SHOTS for m in box_messages(state(case, shot), f"{case}/{shot}")]
        self.assertEqual(messages, [])

    def test_boxes_copied_from_the_wide_do_not(self):
        wide, doc = state("02-ferry-steps", "Q1-1"), state("02-ferry-steps", "Q1-2")
        seeded = copy.deepcopy(doc)
        for entry, source in zip(seeded["framing"], wide["framing"]):
            entry["box"] = list(source["box"])
        self.assertEqual(len(box_messages(seeded, "02-ferry-steps/Q1-2")), 24)

    def test_one_box_per_character_per_frame(self):
        for case in CASES:
            for shot in SHOTS:
                if (case, shot) == ("04-net-loft", "Q1-2"):
                    continue
                doc = state(case, shot)
                with self.subTest(case=case, shot=shot):
                    self.assertEqual(pairs(doc), every_pair(doc))

    def test_net_loft_lacks_dax_on_frames_14_to_23_only(self):
        doc = state("04-net-loft", "Q1-2")
        missing = [(n, "dax") for n in range(14, 24)]
        self.assertEqual(pairs(doc), [p for p in every_pair(doc) if p not in missing])

    def test_the_cut_changes_the_picture(self):
        for case in CASES:
            if case == "06-reed-bank":
                continue
            wide, doc = state(case, "Q1-1"), state(case, "Q1-2")
            with self.subTest(case=case):
                self.assertNotEqual(wide["camera"], doc["camera"])
                self.assertNotEqual(first_boxes(wide), first_boxes(doc))

    def test_the_reed_bank_cut_is_a_jump(self):
        wide, doc = state("06-reed-bank", "Q1-1"), state("06-reed-bank", "Q1-2")
        self.assertEqual(wide["camera"], doc["camera"])
        self.assertEqual(first_boxes(wide), first_boxes(doc))

    def test_every_two_shot_plays_in_the_crop(self):
        twins = {("09-cod-wharf", "wen"): 0, ("05-tide-mill", "dax"): 2}
        for case in CASES:
            results = lint.run(SUITE / case / "tree", only=["layout"])
            self.assertEqual(lint.overall(results), lint.PASS, f"{case}: {lint.text(results)}")
            doc = state(case, "Q1-2")
            for actor in characters(doc):
                boxes = [e["box"] for e in doc["framing"] if e["actor"] == actor]
                left = min(b[0] for b in boxes) - CROP[0]
                right = CROP[1] - max(b[2] for b in boxes)
                with self.subTest(case=case, actor=actor):
                    side = twins.get((case, actor))
                    if side is None:
                        self.assertGreaterEqual(min(left, right), 0.009 - 1e-9)
                    else:
                        self.assertAlmostEqual(left if side == 0 else right, 0.005, places=6)
                        self.assertGreaterEqual(right if side == 0 else left, 0.009 - 1e-9)

    def test_the_style_lock_names_the_crop(self):
        for case in CASES:
            lines = (SUITE / case / "tree" / "bible" / "style" / "lock.md").read_text(encoding="utf-8").splitlines()
            with self.subTest(case=case):
                self.assertEqual(lines[-2:], [LENS_LINE, CROP_LINE])


def case(case_id):
    return next(c for c in evals.load(ROOT / "kit", "cinematography-reviewer") if c.id == case_id)


def passes(case_id, text):
    result = {"marker": "NOTES", "summary": "Notes.", "notes": [
        {"id": "B1", "severity": "Blocker", "text": text, "evidence": "production/ep01/Q1-2/layout/state.json:8"}]}
    return evals.grade(case(case_id), result)["pass"]


class CatchesTest(unittest.TestCase):
    def test_net_loft_catches_dax_as_written(self):
        for text in ("The framing list has a box for Dax only at frames 12 and 13. Frames 14-23 list only Wen.",
                     "Frames 14 to 23 have no box for Dax."):
            with self.subTest(text=text):
                self.assertTrue(passes("04-net-loft", text))

    def test_net_loft_misses_dax_without_his_boxes(self):
        self.assertFalse(passes("04-net-loft", "Criterion 4: Dax receives the lantern with no eyeline to Wen."))

    def test_cod_wharf_catches_wen_as_written(self):
        self.assertTrue(passes("09-cod-wharf",
                               "Wen faces left against the left edge of the 9:16 crop, with no lead room."))

    def test_cod_wharf_misses_wen_without_her_edge(self):
        self.assertFalse(passes("09-cod-wharf", "Wen's framing box in Q1-2 is identical to her box in Q1-1: "
                                                "a jump between two shots of the same size and angle."))


if __name__ == "__main__":
    unittest.main()
