"""lighting: rules render (1), state (2), key (3), exposure (4), color (5), clipping (6), time-of-day (7)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lint import ROOT, LintCase, lint  # noqa: E402
from kit.lib.lints import lighting  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "lint_lighting"
CROSS = Path(__file__).resolve().parent / "fixtures" / "lint_cross-shot"
EP = "production/ep01"
Q1, Q2 = "bible/style/keys/ep01/Q1.json", "bible/style/keys/ep01/Q2.json"


def render_rel(shot):
    return f"{EP}/{shot}/lighting/render.json"


def state_rel(shot):
    return f"{EP}/{shot}/lighting/state.json"


def dump(data):
    return json.dumps(data, indent=2) + "\n"


def clean(rel):
    return json.loads((FIX / "clean" / rel).read_text(encoding="utf-8"))


def drop(key):
    def change(d):
        del d[key]
    return change


def set_sample(i, key, value):
    return lambda d: d["samples"][i].__setitem__(key, value)


class Lighting(LintCase):
    CHECKS = ("canon", "kit", "markers", "media", "provenance", "references", "spoiler", "structure")

    def light(self, root):
        return lint.run(root, only=["lighting"])["lighting"]

    def found(self, root):
        result = self.light(root)
        self.assertNotEqual(result.status, lint.ERROR, result.reason)
        return [(f.path, f.line, f.rule, f.message) for f in result.findings]

    def edit(self, *changes):
        """The clean fixture with each (rel, change) applied; a change of None removes the file."""
        files, gone = {}, []
        for rel, change in changes:
            if change is None:
                gone.append(rel)
                continue
            data = json.loads(files[rel]) if rel in files else clean(rel)
            data = change(data) or data  # a change returns a new document or edits this one
            files[rel] = dump(data)
        root = self.variant(files, base=FIX / "clean")
        for rel in gone:
            (root / rel).unlink()
        return root

    def test_1_clean_and_defect_trees(self):
        result = self.light(FIX / "clean")
        self.assertEqual(result.status, lint.PASS, result.findings)
        others = lint.run(FIX / "clean", only=list(self.CHECKS))
        self.assertEqual({cid: r.status for cid, r in others.items()}, dict.fromkeys(self.CHECKS, lint.PASS))
        cases = {
            "exposure": (render_rel("Q1-2"), "frame 167: luma 0.46 is 15.0% from the key's 0.4 (at most 10%)"),
            "color": (render_rel("Q1-2"), "frame 167: color is 4.57 delta-E 2000 from the key's (at most 1)"),
            "clipping": (render_rel("Q2-1"), "frame 168: 0.004 of the pixels clipped (at most 0.001)"),
            "time-of-day": (state_rel("Q1-1"), "sun at 35 degrees is outside the key of Q1, 2 to 12 degrees"),
        }
        for tree, (rel, message) in cases.items():
            with self.subTest(tree):
                self.assertOnly(FIX / tree, "lighting", [(rel, 1, tree)])
                self.assertEqual(self.light(FIX / tree).findings[0].message, message)

    def test_2_limits(self):
        r = render_rel("Q1-2")
        twin = self.edit((r, set_sample(1, "luma", 0.44)), (r, set_sample(0, "clipped", 0.001)),
                         (r, set_sample(0, "color", [0.306, 0.22, 0.15])))
        self.assertEqual(self.found(twin), [])
        self.assertAlmostEqual(lighting.delta_e_2000(lighting.lab([0.3, 0.22, 0.15]),
                                                     lighting.lab([0.306, 0.22, 0.15])), 0.6436, places=4)
        cases = (
            ((r, set_sample(1, "luma", 0.4404)), "exposure",
             "frame 167: luma 0.4404 is 10.1% from the key's 0.4 (at most 10%)"),
            ((r, set_sample(0, "clipped", 0.0011)), "clipping", "frame 96: 0.0011 of the pixels clipped (at most 0.001)"),
            ((r, set_sample(0, "color", [0.3, 0.215, 0.15])), "color",
             "frame 96: color is 1.12 delta-E 2000 from the key's (at most 1)"),
        )
        for change, rule, message in cases:
            with self.subTest(message):
                self.assertEqual(self.found(self.edit(change)), [(r, 1, rule, message)])
        r2 = render_rel("Q2-1")
        self.assertEqual(self.found(self.edit((r2, set_sample(0, "luma", 0.2)))), [
            (r2, 1, "exposure", "frame 168: luma 0.2 is 20.0% from the key's 0.25 (at most 10%)")])

    def test_3_time_of_day(self):
        s1, s2 = state_rel("Q1-1"), state_rel("Q2-1")

        def sun(value):
            return lambda d: d.__setitem__("sun", value)

        self.assertEqual(self.found(self.edit((s1, sun({"elevation_deg": 12, "azimuth_deg": 260})))), [])
        cases = (
            ((s1, sun({"elevation_deg": 12.5, "azimuth_deg": 260})), s1,
             "sun at 12.5 degrees is outside the key of Q1, 2 to 12 degrees"),
            ((s1, sun(None)), s1, "sun is null but the key of Q1 wants one from 2 to 12 degrees"),
            ((s2, sun({"elevation_deg": 40, "azimuth_deg": 90})), s2, "sun is set but the key of Q2 has no sun"),
        )
        for change, rel, message in cases:
            with self.subTest(message):
                self.assertEqual(self.found(self.edit(change)), [(rel, 1, "time-of-day", message)])

    def test_4_delta_e_2000(self):
        pairs = (
            ((50, 2.6772, -79.7751), (50, 0, -82.7485), 2.0425),
            ((50, -1.3802, -84.2814), (50, 0, -82.7485), 1.0),
            ((50, 0, 0), (50, -1, 2), 2.3669),
            ((50, 2.49, -0.001), (50, -2.49, 0.0009), 7.1792),
            ((50, 2.49, -0.001), (50, -2.49, 0.0011), 7.2195),
            ((50, -0.001, 2.49), (50, 0.0009, -2.49), 4.8045),
            ((50, 2.5, 0), (73, 25, -18), 27.1492),
            ((50, 2.5, 0), (61, -5, 29), 22.8977),
            ((50, 2.5, 0), (56, -27, -3), 31.903),
            ((50, 2.5, 0), (50, 3.1736, 0.5854), 1.0),
            ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
            ((63.0109, -31.0961, -5.8663), (62.8187, -29.7946, -4.0864), 1.263),
            ((2.0776, 0.0795, -1.135), (0.9033, -0.0636, -0.5514), 0.9082),
        )
        for a, b, value in pairs:
            with self.subTest(a=a, b=b):
                self.assertEqual(round(lighting.delta_e_2000(a, b), 4), value)
                self.assertEqual(round(lighting.delta_e_2000(b, a), 4), value)
        for got, want in zip(lighting.lab([1, 1, 1]), (100, 0, 0)):
            self.assertAlmostEqual(got, want, places=4)
        self.assertEqual(round(lighting.lab([0.18] * 3)[0], 4), 49.4961)

    def test_5_render_shapes(self):
        r = render_rel("Q1-2")
        sample = "is not {frame, luma, clipped, color} with values from 0 to 1"
        cases = (
            (lambda d: d.__setitem__("samples", []), "samples is empty"),
            (drop("samples"), "samples is not a list"),
            (set_sample(0, "luma", 1.5), f"samples[0] {sample}"),
            (set_sample(1, "color", [0.1, 0.2]), f"samples[1] {sample}"),
            (set_sample(0, "frame", True), f"samples[0] {sample}"),
            (lambda d: [d], "not a JSON object"),
        )
        for change, message in cases:
            with self.subTest(message):
                self.assertEqual(self.found(self.edit((r, change))), [(r, 1, "render", message)])

        def extra(d):
            d["samples"][0]["exposure_ev"] = 0.5
            d["note"] = "preview"
        self.assertEqual(self.found(self.edit((r, extra))), [])

    def test_6_state_shapes(self):
        r, s = render_rel("Q1-2"), state_rel("Q1-2")
        self.assertEqual(self.found(self.edit((s, None))), [(r, 1, "state", f"{s} is missing")])
        cases = (
            (lambda d: d.__setitem__("shot", "Q1-9"), "shot is not 'Q1-2'"),
            (lambda d: d.__setitem__("stage", "layout"), "stage is not 'lighting'"),
            (drop("lights"), "lights is not a list"),
            (lambda d: d["lights"][0].__setitem__("unit", "lm"),
             "lights[0] is not {id, kind, intensity, unit, color, temperature_k}"),
            (lambda d: d.__setitem__("sun", {"elevation_deg": 5}), "sun is not null or {elevation_deg, azimuth_deg}"),
            (drop("sun"), "sun is not null or {elevation_deg, azimuth_deg}"),
        )
        for change, message in cases:
            with self.subTest(message):
                self.assertEqual(self.found(self.edit((s, change))), [(s, 1, "state", message)])
        both = self.edit((s, lambda d: d.__setitem__("stage", "layout")), (r, set_sample(1, "luma", 0.46)))
        self.assertEqual(self.found(both), [
            (r, 1, "exposure", "frame 167: luma 0.46 is 15.0% from the key's 0.4 (at most 10%)"),
            (s, 1, "state", "stage is not 'lighting'")])

    def test_7_keys(self):
        no_key = [(render_rel("Q2-1"), 1, "key", f"no key {Q2} for sequence Q2")]
        self.assertEqual(self.found(self.edit((Q2, None))), no_key)
        self.assertEqual(self.found(self.edit((Q2, None), (render_rel("Q2-1"), set_sample(0, "luma", 0.9)))), no_key)

        def band(d):
            d["sun"] = {"min_elevation_deg": 12, "max_elevation_deg": 2}
        cases = (
            ("sun absent", drop("sun")),
            ("luma 0", lambda d: d.__setitem__("luma", 0)),
            ("luma 1.2", lambda d: d.__setitem__("luma", 1.2)),
            ("color of two", lambda d: d.__setitem__("color", [0.3, 0.22])),
            ("band 12 to 2", band),
            ("a list", lambda d: [d]),
        )
        for name, change in cases:
            with self.subTest(name):
                root = self.edit((Q1, change), (render_rel("Q1-1"), set_sample(0, "luma", 0.9)),
                                 (state_rel("Q1-1"), lambda d: d.__setitem__("sun", None)))
                self.assertEqual(self.found(root), [(Q1, 1, "key", "not {luma, color, sun}")])
        self.assertEqual(self.found(self.edit((Q1, lambda d: d.__setitem__("note", "dusk")))), [])

    def test_8_which_files(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        (Path(tmp.name) / "notes.md").write_text("no production\n", encoding="utf-8")
        self.assertEqual(self.found(Path(tmp.name)), [])
        for tree in sorted(CROSS.iterdir()):
            with self.subTest(tree.name):
                self.assertEqual(self.found(tree), [])
        body = (FIX / "clean" / render_rel("Q1-1")).read_text(encoding="utf-8")
        self.assertEqual(self.found(self.variant({"production/assets/boat/lighting/render.json": body},
                                                 base=FIX / "clean")), [])
        intro = f"{EP}/intro/lighting/render.json"
        self.assertEqual(self.found(self.variant({intro: body}, base=FIX / "clean")),
                         [(intro, 1, "render", "shot 'intro' is not <sequence>-<n>")])

    def test_9_not_json_is_error(self):
        for rel in (render_rel("Q1-2"), state_rel("Q1-2"), Q1):
            with self.subTest(rel):
                result = self.light(self.variant({rel: "{\n  \"luma\": \n"}, base=FIX / "clean"))
                self.assertEqual(result.status, lint.ERROR)
                self.assertTrue(result.reason.startswith(f"cannot parse {rel}:"), result.reason)

    def test_10_output_is_deterministic(self):
        r1, r2 = render_rel("Q1-1"), render_rel("Q1-2")
        root = self.edit(
            (r1, lambda d: drop("color")(d["samples"][0])),
            (state_rel("Q1-1"), lambda d: d.__setitem__("stage", "layout")),
            (r2, set_sample(0, "luma", 0.46)), (r2, set_sample(0, "color", [0.3, 0.2, 0.15])),
            (r2, set_sample(1, "clipped", 0.01)),
            (state_rel("Q1-2"), lambda d: d.__setitem__("sun", {"elevation_deg": 40, "azimuth_deg": 0})),
            (Q2, None))
        rules = {f.rule for f in self.light(root).findings}
        self.assertEqual(rules, {"render", "state", "key", "exposure", "color", "clipping", "time-of-day"})
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        cmd = [sys.executable, str(ROOT / "kit" / "factory"), "lint", "--root", str(root), "--only", "lighting",
               "--json"]
        first, second = (subprocess.run(cmd, capture_output=True, env=env, cwd=ROOT) for _ in range(2))
        self.assertEqual(first.returncode, 1, first.stderr)
        self.assertEqual(first.stdout, second.stdout)


if __name__ == "__main__":
    unittest.main()
