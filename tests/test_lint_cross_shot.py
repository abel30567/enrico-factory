"""cross-shot: rules shots (1), state (2), cut (3)."""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lint import LintCase, lint  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "lint_cross-shot"
EP = "production/ep01"
SHOTS = f"{EP}/shots.json"
ROWS = [{"shot": "Q1-1", "first": 0, "last": 95}, {"shot": "Q1-2", "first": 96, "last": 167},
        {"shot": "Q2-1", "first": 168, "last": 239}]


def state_rel(shot):
    return f"{EP}/{shot}/lighting/state.json"


def dump(data):
    return json.dumps(data, indent=2) + "\n"


def clean_state(shot):
    return json.loads((FIX / "clean" / state_rel(shot)).read_text(encoding="utf-8"))


class CrossShot(LintCase):
    CHECKS = ("canon", "kit", "markers", "media", "provenance", "references", "spoiler", "structure")

    def cross(self, root):
        return lint.run(root, only=["cross-shot"])["cross-shot"]

    def found(self, root):
        result = self.cross(root)
        self.assertNotEqual(result.status, lint.ERROR, result.reason)
        return [(f.path, f.line, f.rule, f.message) for f in result.findings]

    def edit(self, shot, change):
        """The clean fixture with `change` applied to the state of `shot`."""
        data = clean_state(shot)
        change(data)
        return self.variant({state_rel(shot): dump(data)}, base=FIX / "clean")

    def without(self, *rels):
        root = self.variant({}, base=FIX / "clean")
        for rel in rels:
            (root / rel).unlink()
        return root

    def test_1_clean_passes_every_check(self):
        result = self.cross(FIX / "clean")
        self.assertEqual(result.status, lint.PASS, result.findings)
        others = lint.run(FIX / "clean", only=list(self.CHECKS))
        self.assertEqual({cid: r.status for cid, r in others.items()}, {cid: lint.PASS for cid in self.CHECKS})

    def test_2_moved_and_sha256_fixtures(self):
        cut = (state_rel("Q1-2"), 1, "cut")
        self.assertOnly(FIX / "moved", "cross-shot", [cut])
        self.assertEqual(self.cross(FIX / "moved").findings[0].message,
                         "actor ama: starts 25.0 mm from its end in Q1-1 (at most 10)")
        self.assertOnly(FIX / "sha256", "cross-shot", [cut])
        self.assertEqual(self.cross(FIX / "sha256").findings[0].message,
                         "actor harbour: sha256 differs from shot Q1-1")

    def test_3_limits(self):
        def start(key, value):
            return lambda d: d["actors"][0]["start"].__setitem__(key, value)

        for value, message in (([1.21, 0.5, 0], None), ([1.2105, 0.5, 0], "starts 10.5 mm from")):
            with self.subTest(location=value):
                found = self.found(self.edit("Q1-2", start("location", value)))
                self.assertEqual([m for *_, m in found],
                                 [f"actor ama: {message} its end in Q1-1 (at most 10)"] if message else [])
        for value, message in (([0, 0, 91], None), ([0, 0, 92], "starts turned 2.0 degrees from")):
            with self.subTest(rotation=value):
                found = self.found(self.edit("Q1-2", start("rotation", value)))
                self.assertEqual([m for *_, m in found],
                                 [f"actor ama: {message} its end in Q1-1 (at most 1)"] if message else [])
        a, b = clean_state("Q1-1"), clean_state("Q1-2")
        a["actors"][0]["end"]["rotation"] = [0, 0, 359.5]
        b["actors"][0]["start"]["rotation"] = [0, 0, 0.2]
        root = self.variant({state_rel("Q1-1"): dump(a), state_rel("Q1-2"): dump(b)}, base=FIX / "clean")
        self.assertEqual(self.found(root), [])

    def test_4_where_no_cut_is(self):
        def move(d):
            d["actors"][0]["start"]["location"] = [0, 0, 0]
            d["actors"][0]["start"]["rotation"] = [0, 0, 180]
        self.assertEqual(self.found(self.edit("Q2-1", move)), [])
        rows, first = [], 0
        for shot in ("Q1-1", "Q2-1", "Q1-2"):
            last = first + next(r["last"] - r["first"] for r in ROWS if r["shot"] == shot)
            rows.append({"shot": shot, "first": first, "last": last})
            first = last + 1
        states = {}
        for row in rows:
            data = clean_state(row["shot"])
            data["frames"] = {"first": row["first"], "last": row["last"]}
            states[state_rel(row["shot"])] = dump(data)
        # Q1-2 moved and its harbour changed: with Q2-1 between them, Q1-1 and Q1-2 are no cut.
        moved = json.loads(states[state_rel("Q1-2")])
        moved["actors"][0]["start"]["location"] = [1.225, 0.5, 0]
        states[state_rel("Q1-2")] = dump(moved)
        self.assertEqual(self.found(self.variant({SHOTS: dump(rows), **states}, base=FIX / "clean")), [])
        self.assertEqual(self.found(self.without(state_rel("Q1-1"))), [])
        # tobi is in Q1-2 only: moving it at its start changes nothing.
        self.assertEqual(self.found(self.edit("Q1-2", lambda d: d["actors"][1]["start"].__setitem__(
            "location", [30, 30, 30]))), [])

    def test_5_shots_json(self):
        bad_list = "not a list of {shot, first, last}"
        for body in ({"shots": ROWS}, ROWS[:1] + [{"first": 0}] + ROWS[1:]):
            with self.subTest(body=body):
                root = self.variant({SHOTS: dump(body)}, base=FIX / "clean")
                self.assertEqual(self.found(root), [(SHOTS, 1, "shots", bad_list)])
        root = self.variant({SHOTS: dump(ROWS + [{"shot": "open", "first": 240, "last": 300}])},
                            base=FIX / "clean")
        self.assertEqual(self.found(root), [(SHOTS, 1, "shots", "shot 'open' is not <sequence>-<n>")])
        self.assertEqual(self.found(self.without(SHOTS)), [
            (state_rel(shot), 1, "shots", f"shot {shot} is not a row of {SHOTS}")
            for shot in ("Q1-1", "Q1-2", "Q2-1")])

    def test_6_state_defects(self):
        def actor(i):
            return lambda d: d["actors"][i]

        def twice(d):
            d["actors"].append(copy.deepcopy(d["actors"][0]))
            d["actors"][0]["start"]["location"] = [5, 5, 5]  # a cut finding, were the state read

        shape = "is not {id, sha256, start, end} with transforms {location, rotation}"
        cases = (
            (lambda d: d.__setitem__("stage", "layout"), "stage is not 'lighting'"),
            (lambda d: d.__setitem__("shot", "Q1-1"), "shot is not 'Q1-2'"),
            (lambda d: d.pop("actors"), "actors is not a list"),
            (lambda d: d["actors"][0].__setitem__("start", {"location": [1, 2]}), f"actors[0] {shape}"),
            (lambda d: d["actors"][0]["end"].__setitem__("location", [2.0, True, 0]), f"actors[0] {shape}"),
            (lambda d: d["actors"][2].__setitem__("sha256", 7), f"actors[2] {shape}"),
            (twice, "actor ama is listed twice"),
        )
        for change, message in cases:
            with self.subTest(message):
                def defect(d, change=change):
                    d["actors"][0]["start"]["location"] = [1.225, 0.5, 0]  # no cut finding either
                    change(d)
                self.assertEqual(self.found(self.edit("Q1-2", defect)), [(state_rel("Q1-2"), 1, "state", message)])

        def no_kind(d):
            for a in d["actors"]:
                a.pop("kind")
        self.assertEqual(self.found(self.edit("Q1-2", no_kind)), [])

    def test_7_state_not_json_is_error(self):
        root = self.variant({state_rel("Q1-2"): "{\n  \"shot\": \n"}, base=FIX / "clean")
        result = self.cross(root)
        self.assertEqual(result.status, lint.ERROR)
        self.assertTrue(result.reason.startswith(f"cannot parse {state_rel('Q1-2')}:"), result.reason)

    def test_8_output_is_deterministic(self):
        q1_1, q2_1 = clean_state("Q1-1"), clean_state("Q2-1")
        q1_1["stage"] = "layout"
        q2_1["actors"].append(copy.deepcopy(q2_1["actors"][0]))
        moved = (FIX / "moved" / state_rel("Q1-2")).read_text(encoding="utf-8")
        root = self.variant({SHOTS: dump(ROWS + [{"shot": "open", "first": 240, "last": 300}]),
                             state_rel("Q1-1"): dump(q1_1), state_rel("Q2-1"): dump(q2_1),
                             "production/ep02/Q3-1/lighting/state.json": dump(clean_state("Q2-1")),
                             "production/ep00/shots.json": dump([{"shot": "Q9-1"}, {"shot": "Q9-2"}]),
                             "production/ep00/Q9-1/lighting/state.json": moved.replace("Q1-2", "Q9-1"),
                             "production/ep00/Q9-2/lighting/state.json": moved.replace("Q1-2", "Q9-2")},
                            base=FIX / "clean")
        first = lint.text(lint.run(root, only=["cross-shot"]))
        self.assertEqual({f.rule for f in self.cross(root).findings}, {"shots", "state", "cut"})
        self.assertEqual(lint.text(lint.run(root, only=["cross-shot"])), first)


if __name__ == "__main__":
    unittest.main()
