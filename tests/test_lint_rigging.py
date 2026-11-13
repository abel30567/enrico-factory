"""rigging: rules shape (1), range-of-motion (2), limits (3), interpenetration (4), rig-hash (5)."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lint import LintCase, lint  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "lint_rigging"
STATE = "production/assets/ama/rigging/state.json"
MANIFEST = "production/assets/ama/rigging/manifest.json"
CLEAN_SHA = "4c38cd80ea7201eafbd6956768ae36543fe77b3f32e09069e73adc45f70bf5e0"
SHAPE_OF = "not {asset, rig_sha256, joints, rom}"
JOINTS_SHAPE = "joints is not a list of {name, min, max}, each [x, y, z] degrees"
OUTPUTS_SHAPE = "outputs is not a list of {name, sha256, bytes}"
NOT_AN_OUTPUT = f"rig_sha256 is not the sha256 of an output of {MANIFEST}"


def dump(data):
    return json.dumps(data, indent=2) + "\n"


def clean(rel=STATE):
    return json.loads((FIX / "clean" / rel).read_text(encoding="utf-8"))


def joint(data, name):
    return next(j for j in data["joints"] if j["name"] == name)


class Rigging(LintCase):
    CHECKS = ("assets", "canon", "conform", "cross-shot", "intake", "kit", "markers", "media",
              "provenance", "references", "spoiler", "structure")

    def rigging(self, root):
        return lint.run(root, only=["rigging"])["rigging"]

    def found(self, root):
        result = self.rigging(root)
        self.assertNotEqual(result.status, lint.ERROR, result.reason)
        return [(f.path, f.line, f.rule, f.message) for f in result.findings]

    def edit(self, change, base="clean"):
        """The `base` fixture with `change` applied to its state."""
        data = clean()
        change(data)
        return self.variant({STATE: dump(data)}, base=FIX / base)

    def without(self, *rels):
        root = self.variant({}, base=FIX / "clean")
        for rel in rels:
            (root / rel).unlink()
        return root

    def assertGives(self, root, *expected):
        """`rigging` FAILs with exactly `expected` (rule, message) on line 1 of the state."""
        self.assertEqual(self.found(root), [(STATE, 1, rule, message) for rule, message in expected])

    def assertPasses(self, root):
        result = self.rigging(root)
        self.assertEqual(result.status, lint.PASS, f"{result.findings} {result.reason}")

    def test_1_clean_passes_every_check(self):
        self.assertPasses(FIX / "clean")
        others = lint.run(FIX / "clean", only=list(self.CHECKS))
        self.assertEqual({cid: r.status for cid, r in others.items()}, {cid: lint.PASS for cid in self.CHECKS})

    def test_2_on_disk_defects(self):
        for name, rule, message in (
                ("r2_no_poses", "range-of-motion", "rom.poses is 0: no pose was tested"),
                ("r3_limit_violation", "limits", "rom.limit_violations is 1, not 0"),
                ("r4_penetration", "interpenetration", "penetration 5.5 mm is over 5 mm"),
                ("r5_rig_hash", "rig-hash", NOT_AN_OUTPUT)):
            with self.subTest(fixture=name):
                self.assertOnly(FIX / name, "rigging", [(STATE, 1, rule)])
                self.assertGives(FIX / name, (rule, message))

    def test_3_shape(self):
        def put(key, value):
            return lambda d: d.__setitem__(key, value)

        def rom(key, value):
            return lambda d: d["rom"].__setitem__(key, value)

        def joint_key(key, value):
            return lambda d: joint(d, "lowerarm_l").__setitem__(key, value)

        cases = [("rig_sha256", put("rig_sha256", "ABC"), "rig_sha256 is not 64 lower-case hex characters"),
                 ("upper", put("rig_sha256", CLEAN_SHA.upper()), "rig_sha256 is not 64 lower-case hex characters"),
                 ("asset 7", put("asset", 7), "asset is not a string"),
                 ("asset tobi", put("asset", "tobi"), "asset is not 'ama'"),
                 ("joints {}", put("joints", {}), JOINTS_SHAPE),
                 ("min", joint_key("min", [1, 2]), JOINTS_SHAPE),
                 ("max", joint_key("max", ["a", 1, 2]), JOINTS_SHAPE),
                 ("rom []", put("rom", []), "rom is not {poses, limit_violations, penetration_mm}"),
                 ("poses removed", lambda d: d["rom"].pop("poses"), "rom.poses is missing"),
                 ("poses true", rom("poses", True), "rom.poses is not a whole number"),
                 ("poses 2.5", rom("poses", 2.5), "rom.poses is not a whole number"),
                 ("limit_violations -1", rom("limit_violations", -1), "rom.limit_violations is not a whole number"),
                 ("penetration '5'", rom("penetration_mm", "5"), "rom.penetration_mm is not a number at or above 0"),
                 ("penetration -1", rom("penetration_mm", -1), "rom.penetration_mm is not a number at or above 0")]
        cases += [(f"{key} removed", lambda d, key=key: d.pop(key), f"{key} is missing")
                  for key in ("asset", "rig_sha256", "joints", "rom")]
        for name, change, message in cases:
            with self.subTest(name):
                self.assertGives(self.edit(change), ("shape", message))
        with self.subTest("list"):
            self.assertGives(self.variant({STATE: dump([clean()])}, base=FIX / "clean"), ("shape", SHAPE_OF))

        def two(d):
            d.pop("joints")
            d["asset"] = "tobi"
        self.assertGives(self.edit(two), ("shape", "asset is not 'ama'"), ("shape", "joints is missing"))

        def masked(d):
            d["rom"].update(poses=0, limit_violations=3, penetration_mm=9)
            d["rig_sha256"] = "x"
        self.assertGives(self.edit(masked), ("shape", "rig_sha256 is not 64 lower-case hex characters"))

    def test_4_range_of_motion(self):
        self.assertGives(FIX / "r2_no_poses", ("range-of-motion", "rom.poses is 0: no pose was tested"))
        self.assertPasses(self.edit(lambda d: d["rom"].__setitem__("poses", 1)))

    def test_5_limits(self):
        self.assertGives(self.edit(lambda d: d.__setitem__("joints", [])), ("limits", "joints is empty"))

        def repeats(d):
            d["joints"] += [joint(d, "head"), joint(d, "spine_01"), joint(d, "spine_01")]
        self.assertGives(self.edit(repeats), ("limits", "joint name head repeats"),
                         ("limits", "joint name spine_01 repeats"))

        def limits(lo, hi):
            return lambda d: joint(d, "lowerarm_l").update(min=lo, max=hi)
        self.assertGives(self.edit(limits([0, 150, 1], [0, 145, 0])),
                         ("limits", "joint lowerarm_l: min is above max on y, z"))
        self.assertPasses(self.edit(limits([10, 10, 10], [10, 10, 10])))

    def test_6_interpenetration(self):
        def rom(**values):
            return lambda d: d["rom"].update(values)
        self.assertGives(self.edit(rom(penetration_mm=5.001)),
                         ("interpenetration", "penetration 5.001 mm is over 5 mm"))
        self.assertGives(self.edit(rom(limit_violations=3, penetration_mm=7.25)),
                         ("interpenetration", "penetration 7.25 mm is over 5 mm"),
                         ("limits", "rom.limit_violations is 3, not 0"))
        self.assertPasses(self.edit(rom(penetration_mm=5.0)))

    def test_7_rig_hash(self):
        self.assertGives(self.without(MANIFEST), ("rig-hash", f"{MANIFEST} is missing"))
        for body in ([], {}, {"outputs": [{"name": "x"}]}):
            with self.subTest(manifest=body):
                root = self.variant({MANIFEST: dump(body)}, base=FIX / "clean")
                self.assertEqual(self.found(root), [(MANIFEST, 1, "rig-hash", OUTPUTS_SHAPE)])
        self.assertGives(self.edit(lambda d: d.__setitem__("rig_sha256", "0" * 64)), ("rig-hash", NOT_AN_OUTPUT))
        manifest = clean(MANIFEST)
        manifest["outputs"].insert(0, {"name": "ama_rig_proxy.uasset", "sha256": "1" * 64, "bytes": 4096})
        self.assertPasses(self.variant({MANIFEST: dump(manifest)}, base=FIX / "clean"))

    def test_8_other_files(self):
        self.assertPasses(self.variant({"production/ep01/s01/rigging/state.json": "{",
                                        "assets/ama/rigging/state.json": "{"}, base=FIX / "clean"))
        self.assertPasses(self.without(STATE))
        for rel in (STATE, MANIFEST):
            with self.subTest(rel):
                result = self.rigging(self.variant({rel: "{"}, base=FIX / "clean"))
                self.assertEqual(result.status, lint.ERROR)
                self.assertTrue(result.reason.startswith(f"cannot parse {rel}:1:"), result.reason)

    def test_9_two_assets_deterministic(self):
        tobi = "production/assets/tobi/rigging/state.json"
        body = (FIX / "clean" / STATE).read_text(encoding="utf-8").replace('"ama"', '"tobi"')
        root = self.variant({tobi: body}, base=FIX / "clean")
        self.assertEqual(self.found(root),
                         [(tobi, 1, "rig-hash", "production/assets/tobi/rigging/manifest.json is missing")])

        data = clean()
        data["rom"].update(poses=0, limit_violations=2, penetration_mm=6)
        data["joints"].append(joint(data, "neck"))
        data["rig_sha256"] = "0" * 64
        root = self.variant({STATE: dump(data), tobi: dump(dict(data, asset="tobi"))}, base=FIX / "clean")
        result = self.rigging(root)
        self.assertEqual({f.rule for f in result.findings},
                         {"range-of-motion", "limits", "interpenetration", "rig-hash"})
        self.assertEqual({f.path for f in result.findings}, {STATE, tobi})
        first = lint.text(lint.run(root, only=["rigging"]))
        self.assertEqual(lint.text(lint.run(root, only=["rigging"])), first)


if __name__ == "__main__":
    unittest.main()
