"""assets: rules 1 to 6."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lint import LintCase, lint  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "lint_assets"
STATE = "production/assets/ama/assets/state.json"
LEDGER = "bible/canon/ledger.tsv"
HEADER = "subject\tattribute\tvalue\ttag\tsource\n"
AMA_ROW = "ama\theight\t1.62 m\tinvented\t\n"
CLEAN = json.loads((FIX / "clean" / STATE).read_text(encoding="utf-8"))


def state(**changes):
    """The clean state with `changes` set over it, a key set to `...` removed."""
    data = dict(CLEAN, **changes)
    return json.dumps({k: v for k, v in data.items() if v is not ...}, indent=2) + "\n"


def bones(*pairs):
    return {"bones": [{"name": n, "parent": p} for n, p in pairs], "weights_normalised": True}


class Assets(LintCase):
    def assets(self, root):
        return lint.run(root, only=["assets"])["assets"]

    def assertGives(self, root, messages, path=STATE):
        """`assets` FAILs with exactly `messages`, each on line 1 of `path`."""
        result = self.assets(root)
        self.assertEqual(result.status, lint.FAIL, result.reason)
        self.assertEqual([(f.path, f.line, f.message) for f in result.findings],
                         [(path, 1, m) for m in messages])
        return result

    def assertPasses(self, root):
        result = self.assets(root)
        self.assertEqual(result.status, lint.PASS, f"{result.findings} {result.reason}")

    def test_1_clean_passes(self):
        self.assertPasses(FIX / "clean")
        self.assertEqual(lint.text(lint.run(FIX / "clean", only=["assets"])), "assets PASS\n")

    def test_2_on_disk_defects(self):
        cases = {
            "r2_wrong_id": ("naming", "asset id tobi is not the directory name ama"),
            "r4_over_budget": ("budgets", "100001 triangles, over the character budget of 100000"),
            "r5_height_off": ("scale", "height_m 1.8 is not within 10% of 1.62 m (bible/canon/ledger.tsv:2)"),
            "r6_two_roots": ("skeleton", "skeleton has 2 roots, not one"),
        }
        for name, (rule, message) in cases.items():
            with self.subTest(name):
                result = self.assertGives(FIX / name, [message])
                self.assertEqual(result.findings[0].rule, rule)
                self.assertEqual(lint.blockers({"assets": result}),
                                 [f"B1 assets {STATE}:1 {message}"])

    def test_3_shape(self):
        textures = [{"name": "ama_body", "width": 4096}]
        cases = [
            ("[]\n", "not {asset, kind, sha256, triangles, materials, textures, height_m, skeleton}"),
            (state(materials=...), "materials is missing"),
            (state(kind="creature"), "kind is not 'character', 'set' or 'prop'"),
            (state(sha256="ABC"), "sha256 is not 64 lower-case hex characters"),
            (state(triangles=True), "triangles is not a whole number"),
            (state(triangles=10.5), "triangles is not a whole number"),
            (state(textures=textures), "textures is not a list of {name, width, height}"),
            (state(height_m="1.7"), "height_m is not a positive number"),
            (state(skeleton={"bones": []}), "skeleton is not null or {bones, weights_normalised}"),
            (state(kind="creature", triangles=200000), "kind is not 'character', 'set' or 'prop'"),
        ]
        for body, message in cases:
            with self.subTest(message=message, body=body[:60]):
                result = self.assertGives(self.variant({STATE: body}, base=FIX / "clean"), [message])
                self.assertEqual(result.findings[0].rule, "shape")

    def test_4_naming(self):
        root = self.variant({STATE: state(asset="Ama_2")}, base=FIX / "clean")
        result = self.assertGives(root, ["asset id Ama_2 does not match [a-z0-9][a-z0-9-]*"])
        self.assertEqual(result.findings[0].rule, "naming")

    def test_5_references(self):
        root = self.variant({}, base=FIX / "clean")
        (root / "bible/canon/characters/ama.md").unlink()
        self.assertGives(root, ["character ama has no bible/canon/characters/ama.md"])
        pier = "production/assets/pier/assets/state.json"
        root = self.variant({pier: state(asset="pier", kind="set", skeleton=None)}, base=FIX / "clean")
        self.assertGives(root, ["set pier has no bible/canon/places/pier.md"], path=pier)
        harbour = "production/assets/harbour/assets/state.json"
        self.assertPasses(self.variant({harbour: state(asset="harbour", kind="set", skeleton=None)},
                                       base=FIX / "clean"))
        lamp = "production/assets/lamp/assets/state.json"
        self.assertPasses(self.variant({lamp: state(asset="lamp", kind="prop", triangles=20000)},
                                       base=FIX / "clean"))

    def test_6_budgets(self):
        lamp = "production/assets/lamp/assets/state.json"
        root = self.variant({lamp: state(asset="lamp", kind="prop", triangles=20001)}, base=FIX / "clean")
        result = self.assertGives(root, ["20001 triangles, over the prop budget of 20000"], path=lamp)
        self.assertEqual(result.findings[0].rule, "budgets")
        harbour = "production/assets/harbour/assets/state.json"
        self.assertPasses(self.variant({harbour: state(asset="harbour", kind="set", triangles=1000000)},
                                       base=FIX / "clean"))
        textures = [{"name": "b", "width": 8192, "height": 4096}, {"name": "a", "width": 4096, "height": 4097}]
        root = self.variant({STATE: state(textures=textures)}, base=FIX / "clean")
        self.assertGives(root, ["texture a is 4096x4097, over 4096 pixels a side",
                                "texture b is 8192x4096, over 4096 pixels a side"])

    def test_7_scale(self):
        no_row = "no height row for ama in bible/canon/ledger.tsv"
        self.assertGives(self.variant({LEDGER: HEADER}, base=FIX / "clean"), [no_row])
        root = self.variant({}, base=FIX / "clean")
        (root / LEDGER).unlink()
        result = self.assertGives(root, [no_row])
        self.assertEqual(result.findings[0].rule, "scale")
        root = self.variant({LEDGER: HEADER + "Ama\t Height \t5 ft 4\tinvented\t\n"}, base=FIX / "clean")
        self.assertGives(root, ["height '5 ft 4' of ama (bible/canon/ledger.tsv:2) is not '<number> m'"])
        self.assertPasses(self.variant({STATE: state(height_m=1.78)}, base=FIX / "clean"))
        self.assertPasses(self.variant({LEDGER: HEADER + "ama\theight\t1.62m\tinvented\t\n"}, base=FIX / "clean"))
        two = HEADER + "ama\theight\t2 m\tinvented\t\n"
        self.assertPasses(self.variant({LEDGER: two, STATE: state(height_m=2.2)}, base=FIX / "clean"))
        self.assertGives(self.variant({LEDGER: two, STATE: state(height_m=2.21)}, base=FIX / "clean"),
                         ["height_m 2.21 is not within 10% of 2 m (bible/canon/ledger.tsv:2)"])
        harbour = "production/assets/harbour/assets/state.json"
        body = state(asset="harbour", kind="set", height_m=12.0, skeleton=None)
        root = self.variant({harbour: body, LEDGER: HEADER + AMA_ROW + "harbour\theight\t20 m\tinvented\t\n"},
                            base=FIX / "clean")
        self.assertGives(root, ["height_m 12 is not within 10% of 20 m (bible/canon/ledger.tsv:3)"], path=harbour)
        self.assertPasses(self.variant({harbour: body}, base=FIX / "clean"))

    def test_8_skeleton(self):
        cases = [
            (state(skeleton=None), "a character has no skeleton"),
            (state(skeleton=bones(("root", None), ("spine", "root"), ("head", "spine"), ("spine", "root"))),
             "bone name spine repeats"),
            (state(skeleton=bones(("root", "head"), ("spine", "root"), ("head", "spine"))),
             "skeleton has 0 roots, not one"),
            (state(skeleton=dict(CLEAN["skeleton"], weights_normalised=False)), "skin weights are not normalised"),
        ]
        for body, message in cases:
            with self.subTest(message):
                result = self.assertGives(self.variant({STATE: body}, base=FIX / "clean"), [message])
                self.assertEqual(result.findings[0].rule, "skeleton")
        lamp = "production/assets/lamp/assets/state.json"
        self.assertPasses(self.variant({lamp: state(asset="lamp", kind="prop", triangles=100, skeleton=None)},
                                       base=FIX / "clean"))

    def test_9_other_files(self):
        root = self.variant({"production/assets/ama/rigging/state.json": "{",
                             "production/ep01/s01/layout/state.json": "{"}, base=FIX / "clean")
        self.assertPasses(root)
        result = self.assets(self.variant({STATE: "{"}, base=FIX / "clean"))
        self.assertEqual(result.status, lint.ERROR)
        self.assertTrue(result.reason.startswith(f"cannot parse {STATE}"), result.reason)

    def test_10_deterministic(self):
        tobi = "production/assets/tobi/assets/state.json"
        lamp = "production/assets/lamp/assets/state.json"
        root = self.variant({
            STATE: state(asset="Ama_2"),
            "production/assets/x/assets/state.json": state(asset="y"),
            tobi: state(asset="tobi", triangles=100001, height_m=3.0,
                        textures=[{"name": "z", "width": 9000, "height": 1}],
                        skeleton=bones(("a", None), ("b", None), ("b", "a"))),
            lamp: state(asset="lamp", kind="prop", triangles=20001),
            LEDGER: HEADER + AMA_ROW + "tobi\theight\t1.5 m\tinvented\t\n",
        }, base=FIX / "clean")
        first = lint.text(lint.run(root, only=["assets"]))
        self.assertEqual(first, lint.text(lint.run(root, only=["assets"])))
        rules = {f.rule for f in lint.run(root, only=["assets"])["assets"].findings}
        self.assertEqual(rules, {"naming", "references", "budgets", "scale", "skeleton"})


if __name__ == "__main__":
    unittest.main()
