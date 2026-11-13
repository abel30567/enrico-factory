"""render: the shot's frame count, no black, NaN or text frame, re-renders inside tolerance."""

import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lint import ROOT, LintCase, cli, lint  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "lint_render"
CLEAN = FIX / "clean"
SHOTS = "production/ep01/shots.json"
R1, R2 = "production/ep01/Q1-1/render/render.json", "production/ep01/Q1-2/render/render.json"
NO_ROW = f"shot Q1-1 has no row {{shot, first, last}} in {SHOTS}"
DEFECTS = {
    "short_count": ("count", "count 11, the shot has 12 frames (0 to 11)"),
    "black_frame": ("black", "black frames: 7"),
    "nan_frame": ("nan", "NaN or infinite frames: 3"),
    "engine_text": ("text", "engine text on frame 4: 'LIGHTING NEEDS TO BE REBUILT'"),
    "low_rerender": ("rerender", "re-render of frame 6 scores 9.82, under 9.9"),
}


def load(rel=R1):
    return json.loads((CLEAN / rel).read_text(encoding="utf-8"))


def dump(data):
    return json.dumps(data, indent=2) + "\n"


def render(**changes):
    """The clean render with `changes` set; a value of None removes the key."""
    data = load()
    for key, value in changes.items():
        if value is None:
            del data[key]
        else:
            data[key] = value
    return dump(data)


def rerender(**changes):
    value = load()["rerender"]
    value.update(changes)
    return value


class Render(LintCase):
    CHECKS = ()  # rule tests look at render alone

    def render(self, root):
        return lint.run(root, only=["render"])["render"]

    def tree(self, files=None, drop=()):
        root = self.variant(files or {}, base=CLEAN)
        for rel in drop:
            (root / rel).unlink()
        return root

    def findings(self, files=None, drop=()):
        r = self.render(self.tree(files, drop))
        self.assertNotEqual(r.status, lint.ERROR, r.reason)
        return [(f.path, f.line, f.rule, f.message) for f in r.findings]

    def assertPasses(self, files=None, drop=()):
        self.assertEqual(self.findings(files, drop), [])

    # Behaviour 1
    def test_clean_case_passes_every_check(self):
        p = cli("--root", str(CLEAN))
        out = p.stdout.decode()
        self.assertEqual(p.returncode, 0, out + p.stderr.decode())
        self.assertIn("render PASS\n", out)
        self.assertTrue(all(line.endswith(" PASS") for line in out.splitlines()), out)

    def test_tree_without_production_passes(self):
        root = self.variant({}, base=CLEAN)
        shutil.rmtree(root / "production")
        self.assertEqual(self.render(root).status, lint.PASS)

    # Behaviour 2
    def test_each_defect_directory_fails_with_its_one_finding(self):
        for case, (rule, message) in DEFECTS.items():
            with self.subTest(case=case):
                r = self.render(FIX / case)
                self.assertEqual(r.status, lint.FAIL, r.reason)
                self.assertEqual([(f.path, f.line, f.rule, f.message) for f in r.findings],
                                 [(R1, 1, rule, message)])
                p = cli("--root", str(FIX / case), "--only", "render")
                self.assertEqual(p.returncode, 1)
                self.assertEqual(p.stdout.decode(), f"render FAIL 1 finding\nB1 render {R1}:1 {message}\n")

    def test_each_defect_directory_passes_every_other_check(self):
        for case in DEFECTS:
            with self.subTest(case=case):
                for cid, r in lint.run(FIX / case).items():
                    if cid != "render":
                        self.assertEqual(r.status, lint.PASS, f"{cid}: {r.findings} {r.reason}")

    # Behaviour 3
    def test_shape_findings(self):
        cases = {
            "count removed": (render(count=None), "count is missing or not an integer"),
            "count 12.0": (render(count=12.0), "count is missing or not an integer"),
            "count true": (render(count=True), "count is missing or not an integer"),
            "black removed": (render(black=None), "black is missing or not a list of frames"),
            "nan [1.5]": (render(nan=[1.5]), "nan is missing or not a list of frames"),
            "text no text": (render(text=[{"frame": 1}]), "text is missing or not a list of {frame, text}"),
            "rerender removed": (render(rerender=None), "rerender is missing or not {frames, metric, scores}"),
            "rerender frames empty": (render(rerender=rerender(frames=[])),
                                      "rerender is missing or not {frames, metric, scores}"),
            "one score fewer": (render(rerender=rerender(scores=[9.97, 9.95])),
                                "rerender is missing or not {frames, metric, scores}"),
            "score NaN": (render(rerender=rerender(scores=[9.97, float("nan"), 9.96])),
                          "rerender is missing or not {frames, metric, scores}"),
            "a JSON list": ("[]\n", "not an object {fps, frames, count, ...}"),
        }
        for name, (body, message) in cases.items():
            with self.subTest(name):
                self.assertEqual(self.findings({R1: body}), [(R1, 1, "render", message)])

    def test_nan_score_is_written_as_json_nan(self):
        body = render(rerender=rerender(scores=[9.97, float("nan"), 9.96]))
        self.assertIn("NaN", body)

    def test_integer_score_passes(self):
        self.assertPasses({R1: render(rerender=rerender(scores=[9.97, 10, 9.96]))})

    # Behaviour 4
    def test_shots_missing(self):
        self.assertEqual(self.findings(drop=[SHOTS]), [(R1, 1, "shots", f"{SHOTS} is missing")])

    def test_shots_without_the_row(self):
        body = dump([{"shot": "Q1-2", "first": 12, "last": 35}])
        self.assertEqual(self.findings({SHOTS: body}), [(R1, 1, "shots", NO_ROW)])

    def test_empty_shots_compares_no_count(self):
        self.assertEqual(self.findings({SHOTS: "[]\n", R1: render(count=1)}), [(R1, 1, "shots", NO_ROW)])

    def test_row_with_last_below_first(self):
        body = dump([{"shot": "Q1-1", "first": 11, "last": 0}, {"shot": "Q1-2", "first": 12, "last": 35}])
        self.assertEqual(self.findings({SHOTS: body}), [(R1, 1, "shots", NO_ROW)])

    # Behaviour 5
    def test_black_frames_distinct_and_sorted(self):
        self.assertEqual(self.findings({R1: render(black=[9, 2, 9])}), [(R1, 1, "black", "black frames: 2, 9")])

    def test_text_one_finding_per_entry_whitespace_collapsed(self):
        body = render(text=[{"frame": 4, "text": "LIGHTING\n  NEEDS"}, {"frame": 10, "text": "PREVIEW"}])
        self.assertEqual(self.findings({R1: body}), [
            (R1, 1, "text", "engine text on frame 10: 'PREVIEW'"),
            (R1, 1, "text", "engine text on frame 4: 'LIGHTING NEEDS'"),
        ])

    def test_score_at_tolerance_passes_and_below_fails(self):
        self.assertPasses({R1: render(rerender=rerender(scores=[9.97, 9.9, 9.96]))})
        self.assertEqual(self.findings({R1: render(rerender=rerender(scores=[9.97, 9.89, 9.96]))}),
                         [(R1, 1, "rerender", "re-render of frame 6 scores 9.89, under 9.9")])

    # Behaviour 6
    def test_lighting_and_asset_renders_are_not_read(self):
        bad = render(black=[7])
        self.assertPasses({"production/ep01/Q1-1/lighting/render.json": bad,
                           "production/assets/fox/render/render.json": bad})

    def test_unparseable_render_is_error(self):
        r = self.render(self.tree({"production/ep01/Q1-9/render/render.json": "{\n"}))
        self.assertEqual(r.status, lint.ERROR)
        self.assertEqual(r.reason, "cannot parse production/ep01/Q1-9/render/render.json:2: "
                                   "Expecting property name enclosed in double quotes")

    def test_git_ignored_render_is_not_read(self):
        root = self.tree({"production/ep01/Q1-9/render/render.json": "{\n"})
        self.git_init(root, ignore="production/ep01/Q1-9/\n")
        self.assertEqual(self.render(root).status, lint.PASS)

    # Behaviour 7
    def test_second_shot_matches_its_row(self):
        self.assertPasses({R2: render(count=24)})

    def test_second_shot_with_wrong_count(self):
        self.assertEqual(self.findings({R2: render(count=12)}),
                         [(R2, 1, "count", "count 12, the shot has 24 frames (12 to 35)")])

    # Behaviour 8
    def test_output_is_deterministic(self):
        root = self.tree({R1: render(count=11, black=[7], nan=[3],
                                     text=[{"frame": 4, "text": "LIGHTING NEEDS TO BE REBUILT"}],
                                     rerender=rerender(scores=[9.97, 9.82, 9.96]))})
        a = cli("--root", str(root), "--only", "render")
        b = cli("--root", str(root), "--only", "render")
        self.assertEqual(a.returncode, 1)
        self.assertEqual(a.stdout, b.stdout)
        self.assertIn(b"render FAIL 5 findings\n", a.stdout)


if __name__ == "__main__":
    unittest.main()
