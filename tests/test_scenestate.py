"""Tests for the sun and the light temperatures of the scene state (#494): `kit/lib/scenestate.py` read by
the `lighting` lint. Trees are written in a temporary directory from the golden `lit-render` scene.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import golden, lint, scenestate  # noqa: E402

SCENE = ROOT / "tests/fixtures/engine/lit-render/scene.json"
RENDER = ROOT / "tests/fixtures/golden_levels/lit-render/render.json"
JOB = {"shot": "Q1-1", "stage": "lighting", "fps": 24, "first": 0, "last": 23, "width": 640, "height": 360}
KEY = {"luma": 0.42, "color": [0.4, 0.42, 0.44], "sun": {"min_elevation_deg": 30, "max_elevation_deg": 60}}
STATE_REL = "production/ep01/Q1-1/lighting/state.json"
RENDER_REL = "production/ep01/Q1-1/lighting/render.json"
KEY_REL = "bible/style/keys/ep01/Q1.json"


def scene() -> dict:
    return json.loads(SCENE.read_text(encoding="utf-8"))


def light(scene_: dict, label: str) -> dict:
    return next(row for row in scene_["lights"] if row["label"] == label)


def state(scene_: dict) -> dict:
    return scenestate.state(scene_, JOB, golden.UNITS["lit-render"])


class StateTest(unittest.TestCase):
    def raises(self, scene_, message):
        with self.assertRaises(scenestate.SceneError) as caught:
            state(scene_)
        self.assertEqual(str(caught.exception), message)

    def test_1_sun_of_the_golden_scene(self):
        self.assertEqual(state(scene())["sun"], {"elevation_deg": 45, "azimuth_deg": 210})
        with self.subTest(twin="no directional light"):
            indoors = scene()
            indoors["lights"] = [row for row in indoors["lights"] if row["label"] != "key"]
            self.assertIsNone(state(indoors)["sun"])

    def test_2_directions(self):
        for rotation, sun in (([0, 20, -90], {"elevation_deg": -20, "azimuth_deg": 90}),
                              ([0, -10, 180], {"elevation_deg": 10, "azimuth_deg": 0})):
            with self.subTest(rotation=rotation):
                turned = scene()
                light(turned, "key")["rotation"] = rotation
                self.assertEqual(state(turned)["sun"], sun)

    def test_3_two_suns(self):
        two = scene()
        light(two, "fill")["class"] = "DirectionalLight"
        self.raises(two, "lights fill and key are both directional; a scene has one sun")

    def test_4_temperature_from_the_colour(self):
        bare = scene()
        for row in bare["lights"]:
            row["temperature_k"] = None
        self.assertEqual([row["temperature_k"] for row in state(bare)["lights"]], [6503.5, 6503.5])

    def test_5_no_temperature_to_find(self):
        for color, shown in (([0.0, 0.25, 1.0], "[0, 0.25, 1]"), ([0.0, 0.0, 0.0], "[0, 0, 0]")):
            with self.subTest(color=color):
                blue = scene()
                light(blue, "fill").update(temperature_k=None, color=color)
                self.raises(blue, f"light fill has no temperature and its color {shown} has none")

    def test_8_deterministic(self):
        self.assertEqual(scenestate.dumps(state(scene())), scenestate.dumps(state(scene())))


class LintTest(unittest.TestCase):
    def tree(self, scene_: dict, key: bool = True) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "tree"
        (root / STATE_REL).parent.mkdir(parents=True)
        (root / STATE_REL).write_text(scenestate.dumps(state(scene_)), encoding="utf-8")
        shutil.copyfile(RENDER, root / RENDER_REL)
        if key:
            (root / KEY_REL).parent.mkdir(parents=True)
            (root / KEY_REL).write_text(json.dumps(KEY, indent=2) + "\n", encoding="utf-8")
        return root

    def findings(self, root: Path) -> list:
        return [(f.path, f.line, f.rule, f.message) for f in lint.run(root, only=["lighting"])["lighting"].findings]

    def test_6_lint_passes_on_the_golden_scene_with_its_key(self):
        root = self.tree(scene())
        self.assertEqual(lint.overall(lint.run(root, only=["lighting"])), lint.PASS, self.findings(root))
        with self.subTest(defect="no key"):
            self.assertEqual(self.findings(self.tree(scene(), key=False)),
                             [(RENDER_REL, 1, "key", f"no key {KEY_REL} for sequence Q1")])
        with self.subTest(defect="no sun"):
            indoors = scene()
            indoors["lights"] = [row for row in indoors["lights"] if row["label"] != "key"]
            self.assertEqual(self.findings(self.tree(indoors)),
                             [(STATE_REL, 1, "time-of-day",
                               "sun is null but the key of Q1 wants one from 30 to 60 degrees")])

    def test_7_lint_passes_when_no_light_has_a_temperature(self):
        bare = scene()
        for row in bare["lights"]:
            row["temperature_k"] = None
        root = self.tree(bare)
        self.assertEqual(lint.overall(lint.run(root, only=["lighting"])), lint.PASS, self.findings(root))


if __name__ == "__main__":
    unittest.main()
