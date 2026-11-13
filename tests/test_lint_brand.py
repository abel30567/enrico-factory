"""brand: rulings 2 to 11 of ISSUE-260, on copies of tests/fixtures/lint_brand/clean with one edit each."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import lint  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "lint_brand"
VARIANT = "post/ep01/youtube/shorts/variant.json"
BRAND = "brand/brand.json"
RULEBOOK = "rulebook/youtube.json"


def factory_lint(root, *args):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, str(ROOT / "kit" / "factory"), "lint", "--root", str(root), *args],
                          capture_output=True, text=True, env=env, cwd=ROOT)


def read(root, rel):
    return json.loads((root / rel).read_text(encoding="utf-8"))


def write(root, rel, data):
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    text = data if isinstance(data, str) else json.dumps(data, indent=2) + "\n"
    (root / rel).write_text(text, encoding="utf-8")


class Brand(unittest.TestCase):
    def copy(self, base="clean"):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "tree"
        shutil.copytree(FIX / base, root)
        return root

    def edited(self, variant=None, brand=None, rulebook=None):
        """A copy of clean/ where each given function edits the parsed variant, brand or rulebook."""
        root = self.copy()
        for rel, edit in ((VARIANT, variant), (BRAND, brand), (RULEBOOK, rulebook)):
            if edit is not None:
                data = read(root, rel)
                write(root, rel, edit(data) or data)
        return root

    def with_variant(self, **keys):
        """A copy of clean/ whose variant has `keys` set; a value of None removes the key."""
        def edit(data):
            for key, value in keys.items():
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value
        return self.edited(variant=edit)

    def brand(self, root):
        return lint.run(root, only=["brand"])["brand"]

    def messages(self, root):
        result = self.brand(root)
        self.assertNotEqual(result.status, lint.ERROR, result.reason)
        return [(f.rule, f.message) for f in result.findings]

    def assertPasses(self, root):
        result = self.brand(root)
        self.assertEqual(result.status, lint.PASS, result.findings or result.reason)

    def assertError(self, root, reason):
        result = self.brand(root)
        self.assertEqual((result.status, result.reason), (lint.ERROR, reason))

    def test_b1_clean_passes_brand_and_every_check(self):
        self.assertPasses(FIX / "clean")
        p = factory_lint(FIX / "clean")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        lines = p.stdout.splitlines()
        self.assertIn("brand PASS", lines)
        for line in lines:
            self.assertTrue(line.endswith(" PASS"), line)

    def test_b2_spelling(self):
        self.assertEqual([(f.line, f.rule, f.message) for f in self.brand(FIX / "d1_spelling").findings], [
            (2, "spelling", "title spells 'Lanternfall' as 'Lanterfall'"),
            (3, "spelling", "caption spells '@lanternfall' as '@lanternfal'")])
        for word, term in (("LANTERNFALL", "Lanternfall"), ("Lanternfalls", "Lanternfall"),
                           ("Lantrenfall", "Lanternfall"), ("wick", "Wick")):
            with self.subTest(word):
                root = self.with_variant(title=f"{word} lights the last lamp")
                self.assertEqual(self.messages(root), [("spelling", f"title spells {term!r} as {word!r}")])
        self.assertPasses(self.with_variant(title="Wiik lights the last lamp"))
        self.assertPasses(self.with_variant(title="The Low Lanes wait for Wick"))
        caption = read(FIX / "clean", VARIANT)["caption"] + " @lanternfall.show"
        self.assertPasses(self.with_variant(caption=caption))

    def test_b3_hashtags_and_never(self):
        self.assertEqual([(f.line, f.rule, f.message) for f in self.brand(FIX / "d2_words").findings], [
            (1, "hashtag", "hashtag '#Wick' is missing (brand/brand.json platforms.youtube.hashtags)"),
            (4, "never", "cta uses 'epic' (brand/brand.json words.never)")])
        description = read(FIX / "clean", VARIANT)["description"] + " #Epic"
        self.assertEqual(self.messages(self.with_variant(description=description)),
                         [("never", "description uses 'epic' (brand/brand.json words.never)")])
        self.assertEqual(self.messages(self.with_variant(title="Must-See: Wick lights the last lamp")),
                         [("never", "title uses 'must-see' (brand/brand.json words.never)")])
        caption = read(FIX / "clean", VARIANT)["caption"].replace("#Lanternfall", "#lanternfall")
        self.assertPasses(self.with_variant(caption=caption))

    def test_b4_end_card(self):
        self.assertEqual(self.messages(FIX / "d3_end_card"), [
            ("end-card", "end card lasts 1.5 s, outside [2, 4] (brand/brand.json end_card.duration)")])
        self.assertEqual(self.messages(self.with_variant(end_card={"mark": "end-card", "seconds": 5})), [
            ("end-card", "end card lasts 5 s, outside [2, 4] (brand/brand.json end_card.duration)")])
        for end_card in (None, {"mark": "logo", "seconds": 3}):
            with self.subTest(end_card):
                root = self.with_variant(end_card=end_card)
                self.assertEqual(self.messages(root), [("end-card", "end card 'end-card' is missing")])
        for seconds in (2, 4):
            with self.subTest(seconds):
                self.assertPasses(self.with_variant(end_card={"mark": "end-card", "seconds": seconds}))

    def test_b5_corner(self):
        self.assertEqual(self.messages(FIX / "d4_corner"), [
            ("corner", "corner mark [0.01, 0.01, 0.13, 0.0775] is outside the mark zone of rulebook/youtube.json "
                       "(shorts, 9:16)")])
        root = self.with_variant(corner={"mark": "corner", "box": [0.84, 0.04, 0.96, 0.1075]})
        self.assertEqual(self.messages(root), [("corner", "corner mark is in the top-right corner, not top-left")])
        self.assertEqual(self.messages(self.with_variant(corner=None)),
                         [("corner", "corner mark 'corner' is missing")])
        self.assertPasses(self.with_variant(corner={"mark": "corner", "box": [0.02, 0.02, 0.14, 0.0875]}))

    def test_b6_rulebook_errors(self):
        root = self.copy()
        (root / RULEBOOK).unlink()
        self.assertError(root, "rulebook/youtube.json does not exist")

        def no_shorts(data):
            data["kinds"] = {"long": data["kinds"]["shorts"]}
        self.assertError(self.edited(rulebook=no_shorts), "rulebook/youtube.json has no kind 'shorts'")

        def no_value(data):
            data["kinds"]["shorts"]["mark_zone"]["value"] = None
        self.assertError(self.edited(rulebook=no_value), "rulebook/youtube.json: kinds.shorts.mark_zone has no value")
        self.assertError(self.with_variant(aspect="16:9"),
                         "rulebook/youtube.json: kinds.shorts.mark_zone has no value for 16:9")
        root = self.with_variant(corner=None)
        (root / RULEBOOK).unlink()
        self.assertEqual(self.messages(root), [("corner", "corner mark 'corner' is missing")])

    def test_b7_palette(self):
        self.assertEqual(self.messages(FIX / "d5_palette"), [
            ("palette", "caption color '#FFFFFF' is not in the palette (brand/brand.json palette)")])
        self.assertPasses(self.with_variant(caption_colors=["#f7f1e3"]))

    def test_b8_records_the_check_cannot_read(self):
        root = self.copy()
        write(root, VARIANT, [])
        self.assertEqual(self.messages(root), [("variant", "the variant is not a JSON object")])
        for keys, message in (
                ({"end_card": {"mark": "end-card", "seconds": "3"}}, "end_card.seconds is not a number"),
                ({"corner": {"mark": "corner", "box": [0.16, 0.04, 0.04, 0.1075]}},
                 "corner.box is not [left, top, right, bottom] in fractions of the frame"),
                ({"aspect": "916"}, "aspect is not a string such as '9:16'"),
                ({"caption_colors": "#F7F1E3"}, "caption_colors is not a list of #rrggbb colors")):
            with self.subTest(message):
                self.assertEqual(self.messages(self.with_variant(**keys)), [("variant", message)])
        root = self.with_variant(caption_colors=["#FFFFFF", "#FFFFFF"])
        self.assertEqual(len(self.messages(root)), 1)

        def no_tiktok(data):
            del data["platforms"]["tiktok"]
        root = self.edited(brand=no_tiktok)
        write(root, "post/ep01/tiktok/video/variant.json", read(root, VARIANT))
        rulebook = read(root, RULEBOOK)
        rulebook["kinds"] = {"video": rulebook["kinds"]["shorts"]}
        write(root, "rulebook/tiktok.json", rulebook)
        found = [(f.path, f.rule, f.message) for f in self.brand(root).findings]
        self.assertEqual(found, [("post/ep01/tiktok/video/variant.json", "platform",
                                  "brand/brand.json has no platform 'tiktok'")])

    def test_b9_brand_file(self):
        root = self.copy()
        (root / BRAND).unlink()
        (root / RULEBOOK).unlink()
        write(root, VARIANT, "{not json\n")
        self.assertPasses(root)

        def orange(data):
            data["palette"]["ember"] = "orange"
        self.assertError(self.edited(brand=orange),
                         'brand/brand.json: palette is not as brief section 16, "Brand kit", gives it')

        def badge(data):
            data["corner"]["mark"] = "badge"
        self.assertError(self.edited(brand=badge),
                         'brand/brand.json: corner is not as brief section 16, "Brand kit", gives it')

    def test_b10_output_is_deterministic(self):
        root = self.with_variant(title="Lanterfall: Must-See Wick", caption="@lanternfal", aspect="9:16",
                                 end_card={"mark": "end-card", "seconds": 5},
                                 corner={"mark": "corner", "box": [0.84, 0.01, 0.99, 0.1]},
                                 caption_colors=["#FFFFFF", "#000000"])
        write(root, "post/ep02/youtube/shorts/variant.json", {"end_card": {"mark": "end-card", "seconds": "x"}})
        write(root, "post/ep02/twitch/clip/variant.json", {})
        rules = {f.rule for f in self.brand(root).findings}
        self.assertEqual(rules, {"spelling", "never", "hashtag", "end-card", "corner", "palette", "variant",
                                 "platform"})
        first, second = factory_lint(root, "--only", "brand"), factory_lint(root, "--only", "brand")
        self.assertEqual(first.returncode, 1, first.stdout + first.stderr)
        self.assertEqual(first.stdout, second.stdout)


if __name__ == "__main__":
    unittest.main()
