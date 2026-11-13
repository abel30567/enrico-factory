"""variant: the platform gate (ISSUE-166), on copies of tests/fixtures/lint_variant/clean with one edit each."""

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

from kit.lib import lint, production, roles  # noqa: E402

KIT = ROOT / "kit"
FIX = ROOT / "tests" / "fixtures" / "lint_variant"
R = "post/ep01/youtube/shorts/variant.json"
S = " (rulebook/youtube.json shorts)"
RULEBOOK = "rulebook/youtube.json"
CUE = '{"text": "The lamps go out.", "start": 0.5, "end": 2.5, "box": [0.1, 0.6, 0.8, 0.7]}'
POST_MAKERS = "platform-editor, reframe-artist, caption-writer, cta-writer, thumbnail-artist"


def factory(*args):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, str(KIT / "factory"), *map(str, args)], capture_output=True,
                          text=True, env=env, cwd=ROOT, stdin=subprocess.DEVNULL)


def read(root, rel):
    return json.loads((root / rel).read_text(encoding="utf-8"))


def write(root, rel, data):
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    text = data if isinstance(data, str) else json.dumps(data, indent=2) + "\n"
    (root / rel).write_text(text, encoding="utf-8")


def blockers(*messages, cid="variant", line=1):
    return [f"B{n} {cid} {R}:{line} {m}" for n, m in enumerate(messages, 1)]


class Variant(unittest.TestCase):
    def tempdir(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def copy(self, base=FIX / "clean"):
        root = self.tempdir() / "tree"
        shutil.copytree(base, root)
        return root

    def replaced(self, old, new, rel=R):
        """A copy of clean/ with `old` replaced by `new` in place in `rel`, so line numbers stay."""
        root = self.copy()
        text = (root / rel).read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, old)
        write(root, rel, text.replace(old, new))
        return root

    def edited(self, rel, edit):
        """A copy of clean/ where `edit` changes the parsed `rel`."""
        root = self.copy()
        data = read(root, rel)
        write(root, rel, edit(data) or data)
        return root

    def with_variant(self, **keys):
        """A copy of clean/ whose record has `keys` set; a value of None removes the key."""
        def edit(data):
            for key, value in keys.items():
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value
        return self.edited(R, edit)

    def lines(self, root, *args):
        p = factory("lint", "--root", root, *args)
        return p.returncode, [line for line in p.stdout.splitlines() if line.startswith("B")]

    def assertFails(self, root, expected, *args):
        self.assertEqual(self.lines(root, *args), (1, expected))

    def assertError(self, root, reason):
        p = factory("lint", "--root", root, "--only", "variant")
        self.assertEqual((p.returncode, p.stdout), (2, f"variant ERROR {reason}\n"))

    def test_b1_clean_trees_pass_every_check(self):
        for tree in (FIX / "clean", ROOT / "tests" / "fixtures" / "lint_brand" / "clean"):
            with self.subTest(tree=tree.name):
                p = factory("lint", "--root", tree)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertIn("variant PASS", p.stdout.splitlines())
                for line in p.stdout.splitlines():
                    self.assertTrue(line.endswith(" PASS"), line)

    def test_b2_the_seven_checks(self):
        cases = [
            (('"seconds": 42', '"seconds": 75'), blockers("file runs 75 s, outside [1, 60]" + S)),
            (('"aspect": "9:16"', '"aspect": "16:9"'), blockers("aspect is 16:9, not one of 9:16" + S)),
            (("[0.1, 0.6, 0.8, 0.7]", "[0.1, 0.6, 0.8, 0.9]"),
             blockers("caption 1 [0.1, 0.6, 0.8, 0.9] is outside the caption zone of rulebook/youtube.json "
                      "(shorts, 9:16)")),
            ((f'"captions": [{CUE}]', '"captions": []'), blockers("the variant has no captions")),
            (('"status.containsSyntheticMedia": true', '"status.containsSyntheticMedia": false'),
             blockers("upload sets status.containsSyntheticMedia to false, not true "
                      "(rulebook/youtube.json disclosure)")),
            (('"Wick lights the last lamp in the Low Lanes"', '"The harbour keeper lights the last lamp"'),
             blockers("title gives away 'the harbour keeper' (episodes/ep01/intent-packet.json)",
                      cid="spoiler", line=2)),
            (('["Follow Lanternfall for the next street."]', '["Tag a friend who loves Lanternfall."]'),
             blockers(r"cta matches bait pattern /\btag a friend\b/", cid="spoiler", line=4)),
        ]
        for (old, new), expected in cases:
            with self.subTest(new):
                self.assertFails(self.replaced(old, new), expected)

    def test_b3_the_other_messages(self):
        def upload(**fields):
            def edit(data):
                for key, value in fields.items():
                    if value is None:
                        del data["upload"][key]
                    else:
                        data["upload"][key] = value
            return self.edited(R, edit)

        def file(**fields):
            return self.edited(R, lambda data: data["file"].update(fields))

        cue = {"text": " ", "start": 40, "end": 45, "box": [0.1, 0.6, 0.8, 0.7]}
        cases = [
            (file(width=720), ["file is 720x1920, not 1080x1920 for 9:16" + S]),
            (upload(**{"status.containsSyntheticMedia": None}),
             ["upload does not set status.containsSyntheticMedia (rulebook/youtube.json disclosure)"]),
            (upload(**{"status.containsSyntheticMedia": 1}),
             ["upload sets status.containsSyntheticMedia to 1, not true (rulebook/youtube.json disclosure)"]),
            (upload(**{"status.privacyStatus": "public"}),
             ['upload sets status.privacyStatus to "public", not "private" (rulebook/youtube.json policy)']),
            (self.with_variant(title="W" * 61), ["title has 61 characters, more than 60" + S]),
            (self.with_variant(caption="W" * 151), ["caption has 151 characters, more than 150" + S]),
            (self.with_variant(captions=[cue]),
             ["caption 1 has no text", "caption 1 runs from 40 to 45 s, outside the file's 42 s"]),
            (file(codec="hevc", container="mov", fps=120, bytes=204857600),
             ["codec is 'hevc', not 'h264'" + S, "container is 'mov', not 'mp4'" + S,
              "file has 204857600 bytes, more than 104857600" + S, "frame rate 120 is outside [24, 60]" + S]),
        ]
        for root, messages in cases:
            with self.subTest(messages[0]):
                self.assertFails(root, blockers(*messages), "--only", "variant")

    def test_b4_records_the_check_cannot_read(self):
        root = self.edited(R, lambda data: data.update(file=None, captions="x", upload=[], aspect=916))
        self.assertFails(root, blockers(
            "aspect is not a string such as '9:16'", "captions is not a list of {text, start, end, box}",
            "file is not {sha256, bytes, container, codec, width, height, fps, seconds}", "upload is not an object"),
            "--only", "variant")
        root = self.with_variant(captions=None, upload=None)
        self.assertFails(root, blockers("captions is not a list of {text, start, end, box}",
                                        "upload is not an object"), "--only", "variant")
        root = self.copy()
        write(root, R, [])
        self.assertFails(root, blockers("the variant is not a JSON object"), "--only", "variant")

    def test_b5_rulebook_errors(self):
        root = self.copy()
        (root / RULEBOOK).unlink()
        self.assertError(root, "rulebook/youtube.json does not exist")

        def kinds(edit):
            return self.edited(RULEBOOK, lambda data: edit(data["kinds"]["shorts"]))

        def no_shorts(data):
            data["kinds"] = {"long": data["kinds"]["long"]}
        self.assertError(self.edited(RULEBOOK, no_shorts), "rulebook/youtube.json has no kind 'shorts'")
        self.assertError(kinds(lambda k: k["codec"].update(value=None)),
                         "rulebook/youtube.json: kinds.shorts.codec has no value")
        self.assertError(kinds(lambda k: k["caption_zone"].update(value={"16:9": {}})),
                         "rulebook/youtube.json: kinds.shorts.caption_zone has no value for 9:16")
        self.assertError(self.edited(RULEBOOK, lambda data: data["disclosure"].update(value=None)),
                         "rulebook/youtube.json: disclosure has no value")
        self.assertError(self.edited(RULEBOOK, lambda data: data.update(policy=[{"field": "x"}])),
                         "rulebook/youtube.json: policy is not a list of values with a field")
        for platform, kind in (("youtube", "long"), ("tiktok", "video"), ("instagram", "reels")):
            with self.subTest(platform=platform, kind=kind):
                root = self.tempdir() / "tree"
                shutil.copytree(FIX / "first", root)
                record = (FIX / "clean" / R).read_text(encoding="utf-8")
                write(root, f"post/ep01/{platform}/{kind}/variant.json", record)
                self.assertError(root, f"rulebook/{platform}.json: kinds.{kind}.container has no value")
        p = factory("lint", "--root", FIX / "first")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("references PASS", p.stdout.splitlines())
        self.assertIn("variant PASS", p.stdout.splitlines())

    def test_b6_files_that_are_not_records_pass(self):
        for tree in sorted((ROOT / "tests" / "fixtures" / "lint_spoiler").iterdir()):
            with self.subTest(tree=tree.name):
                result = lint.run(tree, only=["variant"])["variant"]
                self.assertEqual(result.status, lint.PASS, result.findings or result.reason)
        root = self.tempdir() / "tree"
        write(root, "post/ep01/youtube/shorts/caption.json", read(FIX / "clean", R))
        result = lint.run(root, only=["variant"])["variant"]
        self.assertEqual(result.status, lint.PASS, result.findings or result.reason)

    def test_b7_the_variant_entry(self):
        self.assertEqual(roles.validate(KIT), [])
        post = [e for e in roles.load_pipeline(KIT / "pipeline.json") if e["room"] == "post"]
        self.assertEqual([e["stage"] for e in post], ["variant"])
        brief = (ROOT / "docs" / "system-brief.md").read_text(encoding="utf-8")
        row = next(line for line in brief.splitlines() if line.startswith("| post | `variant` |"))
        self.assertEqual(row.split("|")[4].strip(), POST_MAKERS)
        self.assertEqual(", ".join(post[0]["maker"]), POST_MAKERS)
        for rooms, last in ((None, None), ("story,post", "variant")):
            with self.subTest(rooms=rooms):
                target = self.tempdir() / "prod"
                target.mkdir()
                p = factory("init", target, *(["--rooms", rooms] if rooms else []))
                self.assertEqual(p.returncode, 0, p.stderr)
                stages = production.stages(production.load(target), "post")
                self.assertEqual(stages[-1]["stage"] if stages else None, last)

    def test_b8_output_is_deterministic(self):
        root = self.edited(R, lambda data: data["file"].update(codec="hevc", container="mov", fps=120,
                                                                 bytes=204857600))
        first = factory("lint", "--root", root, "--only", "variant")
        second = factory("lint", "--root", root, "--only", "variant")
        self.assertEqual(first.returncode, 1)
        self.assertEqual(first.stdout.encode(), second.stdout.encode())


if __name__ == "__main__":
    unittest.main()
