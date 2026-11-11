"""structure: rules 1 to 10 of an episode's files."""

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lint import ROOT, LintCase, cli, lint  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "lint_structure"
CLEAN = FIX / "clean"
E1, E2 = "episodes/ep01", "episodes/ep02"  # short-hook-payoff (skips outline), episode-acts
INPUT = re.compile(r"^episodes/[^/]+/(premise\.md|beats\.md|outline\.md|script\.md|lines\.json"
                   r"|sequences/.+\.json|reel/edit-list\.json)$")


def read(rel):
    return (CLEAN / rel).read_text(encoding="utf-8")


def load(rel):
    return json.loads(read(rel))


def dump(data):
    return json.dumps(data, indent=2) + "\n"


class Structure(LintCase):
    CHECKS = ()  # rule tests look at structure alone

    def structure(self, root):
        return lint.run(root, only=["structure"])["structure"]

    def tree(self, files=None, drop=()):
        """A copy of the clean fixture with `files` written over it and `drop` removed."""
        root = self.variant(files or {}, base=CLEAN)
        for rel in drop:
            (root / rel).unlink()
        return root

    def edit(self, rel, old, new):
        text = read(rel)
        self.assertIn(old, text)
        return {rel: text.replace(old, new, 1)}

    def fails(self, expected, files=None, drop=()):
        self.assertOnly(self.tree(files, drop), "structure", expected)

    # Clean trees

    def test_clean_passes_in_two_formats(self):
        result = self.structure(CLEAN)
        self.assertEqual(result.status, lint.PASS, result.findings)
        self.assertIn("format: short-hook-payoff", read(f"{E1}/premise.md"))
        self.assertIn("skips: outline", (ROOT / "kit/formats/short-hook-payoff.md").read_text())
        self.assertFalse((CLEAN / E1 / "outline.md").exists())
        self.assertIn("format: episode-acts", read(f"{E2}/premise.md"))
        self.assertTrue((CLEAN / E2 / "outline.md").exists())
        for ep in (E1, E2):
            self.assertTrue((CLEAN / ep / "reel/edit-list.json").exists())

    def test_cli_clean_exits_0(self):
        p = cli("--root", str(CLEAN), "--only", "structure")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stdout.decode(), "structure PASS\n")

    def test_root_without_inputs_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.structure(Path(tmp)).status, lint.PASS)
        roots = [d for area in sorted((ROOT / "tests" / "fixtures").iterdir()) if area.is_dir()
                 for d in sorted(area.iterdir()) if d.is_dir() and area != FIX]
        checked = 0
        for root in roots:
            with self.subTest(root.relative_to(ROOT).as_posix()):
                result = self.structure(root)
                self.assertNotEqual(result.status, lint.ERROR, result.reason)
                if not any(INPUT.match(rel) for rel in lint.walk(root)):
                    self.assertEqual(result.status, lint.PASS, result.findings)
                    checked += 1
        self.assertGreater(checked, 5)

    def test_premise_alone_and_premise_with_beats_pass(self):
        for ep in (E1, E2):
            for keep in (("premise.md",), ("premise.md", "beats.md")):
                with self.subTest(ep=ep, keep=keep):
                    root = self.tree()
                    for path in sorted((root / ep).rglob("*"), reverse=True):
                        rel = path.relative_to(root / ep).as_posix()
                        if path.is_file() and rel not in keep:
                            path.unlink()
                    self.assertEqual(self.structure(root).status, lint.PASS, self.structure(root).findings)

    def test_invalid_json_or_unreadable_file_is_error(self):
        for rel in (f"{E1}/lines.json", f"{E1}/sequences/Q1/sequence.json", f"{E1}/sequences/Q1/shots/Q1-1.json",
                    f"{E2}/reel/edit-list.json"):
            with self.subTest(rel):
                result = self.structure(self.tree({rel: "{not json\n"}))
                self.assertEqual(result.status, lint.ERROR)
                self.assertIn(rel, result.reason)
        result = self.structure(self.tree({f"{E2}/beats.md": b"## B1 teaser\n\xff\n"}))
        self.assertEqual(result.status, lint.ERROR)
        self.assertIn(f"{E2}/beats.md", result.reason)

    # One test per rule

    def test_rule1_premise_front_matter_and_known_format(self):
        p = f"{E1}/premise.md"
        cases = [
            ({p: "The lamp goes dark.\n"}, 1),
            (self.edit(p, "logline: The lamp fails at dusk and Ama rows out to light the bay.\n", ""), 1),
            (self.edit(p, "format: short-hook-payoff", "format: short-mystery"), 2),
        ]
        for files, line in cases:
            with self.subTest(line=line, files=files):
                self.fails([(p, line, "premise")], files)

    def test_rule2_beats_are_the_formats_beats_in_order(self):
        b = f"{E2}/beats.md"
        swapped = read(b).replace("## B2 act 1", "## B2 act 2").replace("## B3 act 2", "## B3 act 1")
        cases = [
            ({b: swapped}, [(b, 8, "beats"), (b, 13, "beats")]),
            # an extra beat is also a beat no scene and no edit-list event names
            ({b: read(b) + "\n## B6 coda\nduration: 10\n"},
             [(b, 28, "beats"), (f"{E2}/outline.md", 1, "scene-beats"), (f"{E2}/reel/edit-list.json", 1, "edit-list")]),
            (self.edit(b, "## B5 tag", "## B5 tag\n## B5 tag"), [(b, 23, "durations"), (b, 24, "beats"), (b, 24, "beats")]),
            # a missing beat leaves the script naming a beat beats.md no longer has
            ({f"{E1}/beats.md": read(f"{E1}/beats.md").split("## B2")[0].replace("5", "15")},
             [(f"{E1}/beats.md", 1, "beats"), (f"{E1}/script.md", 10, "scene-beats")]),
        ]
        for files, expected in cases:
            with self.subTest(expected):
                self.fails(expected, files)
        found = self.structure(self.tree({b: swapped})).findings
        self.assertEqual(found[0].message, "B2 is 'act 2'; beat 2 of episode-acts is 'act 1'")

    def test_rule3_each_beat_has_a_duration_and_the_sum_is_inside_the_runtime(self):
        b = f"{E1}/beats.md"
        cases = [
            (self.edit(b, "duration: 5\n", ""), 5),
            (self.edit(b, "duration: 5", "duration: 0"), 4),
            (self.edit(b, "duration: 5", "duration: -5"), 4),
            (self.edit(b, "duration: 5", "duration: five"), 4),
            (self.edit(b, "duration: 12", "duration: 20"), 1),
            (self.edit(b, "duration: 12", "duration: 9.5"), 1),
        ]
        for files, line in cases:
            with self.subTest(files=files):
                self.fails([(b, line, "durations")], files)
        self.assertIn("durations sum to 25 s, outside the runtime 15-20 s",
                      self.structure(self.tree(self.edit(b, "duration: 12", "duration: 20"))).findings[0].message)
        self.assertEqual(self.structure(self.tree(self.edit(b, "duration: 12", "duration: 10.5"))).status, lint.PASS)

    def test_rule4_outline_scenes_name_existing_beats_and_cover_every_beat(self):
        o = f"{E2}/outline.md"
        cases = [
            (self.edit(o, "beats: B3", "beats: B3, B9"), [(o, 9, "scene-beats")]),
            # a beat dropped from a scene also leaves the shot of that beat outside its scene
            (self.edit(o, "beats: B4, B5", "beats: B4"),
             [(o, 1, "scene-beats"), (f"{E2}/sequences/Q2/shots/Q2-2.json", 1, "shots")]),
            (self.edit(o, "beats: B3\n", ""),
             [(o, 1, "scene-beats"), (o, 10, "scene-beats"), (f"{E2}/sequences/Q1/shots/Q1-3.json", 1, "shots")]),
            # a misnumbered scene no longer matches the script, and the first S3 now owns S3's shots
            (self.edit(o, "## S2 The storm", "## S3 The storm"),
             [(o, 8, "scene-beats"), (f"{E2}/script.md", 8, "script-scenes"),
              (f"{E2}/sequences/Q2/shots/Q2-1.json", 1, "shots"), (f"{E2}/sequences/Q2/shots/Q2-2.json", 1, "shots")]),
            # without an outline the script's beats: lines carry rule 4
            (self.edit(f"{E1}/script.md", "beats: B2", "beats: B1"),
             [(f"{E1}/script.md", 1, "scene-beats"), (f"{E1}/sequences/Q1/shots/Q1-2.json", 1, "shots")]),
        ]
        for files, expected in cases:
            with self.subTest(expected):
                self.fails(expected, files)

    def test_rule5_script_has_the_outlines_scenes_in_order(self):
        s, q2 = f"{E2}/script.md", f"{E2}/sequences/Q2/sequence.json"
        head, s2, s3 = re.split(r"(?=## S2 |## S3 )", read(s))
        self.fails([(s, 8, "script-scenes"), (s, 13, "script-scenes")], {s: head + s3 + "\n" + s2.rstrip("\n") + "\n"})
        self.fails([(s, 17, "script-scenes")], {s: read(s) + "\n## S4 Coda\n", q2: dump({"id": "Q2", "scenes": ["S3", "S4"]})})

    def test_rule6_lines_json_holds_exactly_the_scripts_lines(self):
        j = f"{E1}/lines.json"
        lines = load(j)
        cases = [
            [dict(lines[0], text="The lamp is lit."), *lines[1:]],
            [lines[0], dict(lines[1], speaker="AMA"), lines[2]],
            [lines[0], dict(lines[1], scene="S2"), lines[2]],
            lines[:2],
            [*lines, {"id": "L4", "scene": "S2", "speaker": "AMA", "text": "Row."}],
            [*lines, lines[2]],
            [{"id": "L1"}],
        ]
        for data in cases:
            with self.subTest(data):
                self.fails([(j, 1, "lines")], {j: dump(data)})
        self.fails([(j, 1, "lines"), (f"{E1}/script.md", 12, "lines")],
                   self.edit(f"{E1}/script.md", "L3 AMA", "L2 AMA"))

    def test_rule7_every_scene_is_in_exactly_one_sequence(self):
        q1, q2 = f"{E2}/sequences/Q1/sequence.json", f"{E2}/sequences/Q2/sequence.json"
        cases = [
            ({q2: dump({"id": "Q2", "scenes": ["S3", "S2"]})}, [(q2, 1, "sequences")]),
            ({q1: dump({"id": "Q1", "scenes": ["S1"]})},
             [(f"{E2}/script.md", 8, "sequences"), (f"{E2}/sequences/Q1/shots/Q1-3.json", 1, "shots")]),
            ({q2: dump({"id": "Q2", "scenes": ["S3", "S4"]})}, [(q2, 1, "sequences")]),
            ({q2: dump({"id": "Q2"})},
             [(f"{E2}/script.md", 12, "sequences"), (q2, 1, "sequences"),
              (f"{E2}/sequences/Q2/shots/Q2-1.json", 1, "shots"), (f"{E2}/sequences/Q2/shots/Q2-2.json", 1, "shots")]),
        ]
        for files, expected in cases:
            with self.subTest(expected):
                self.fails(expected, files)

    def test_rule8_shot_keys_scene_beat_duration_and_bible_ids(self):
        rel = f"{E2}/sequences/Q1/shots/Q1-2.json"
        shot = load(rel)
        bad = [{k: v for k, v in shot.items() if k != "camera"},
               dict(shot, scene="S3"),
               dict(shot, beat="B3"),
               dict(shot, duration=0),
               dict(shot, duration="300"),
               dict(shot, characters=["ama", "nobody"]),
               dict(shot, characters="ama"),
               dict(shot, set="moon")]
        for data in bad:
            with self.subTest(data):
                self.fails([(rel, 1, "shots")], {rel: dump(data)})
        found = self.structure(self.tree({rel: dump(dict(shot, characters=["nobody"]))})).findings
        self.assertEqual(found[0].message, "character nobody has no bible/canon/characters/nobody.md")

    def test_rule9_edit_list_order_shots_beats_and_runtime(self):
        rel = f"{E1}/reel/edit-list.json"
        reel = load(rel)
        first, second = reel["events"]
        bad = [dict(reel, events=[first, dict(second, start=6)]),
               dict(reel, events=[first, dict(second, start=4)]),
               dict(reel, events=[dict(first, start=1), dict(second, start=6)]),
               dict(reel, events=[second, first]),
               dict(reel, events=[first, dict(second, shot="Q1-9")]),
               dict(reel, events=[first, dict(second, beat="B1")]),
               dict(reel, events=[first, dict(second, duration=20)]),
               dict(reel, events=[first, dict(second, duration=0)]),
               {"events": reel["events"]}]
        for data in bad:
            with self.subTest(data):
                result = self.structure(self.tree({rel: dump(data)}))
                self.assertEqual(result.status, lint.FAIL, result.reason)
                self.assertEqual({(f.path, f.line, f.rule) for f in result.findings}, {(rel, 1, "edit-list")})
        self.fails([(rel, 1, "edit-list")], {rel: dump(dict(reel, events=[first, dict(second, shot="Q1-9")]))})

    def test_rule10_a_later_file_needs_the_earlier_one(self):
        cases = [
            ([f"{E1}/beats.md"], [(f"{E1}/script.md", 1, "depends")]),
            ([f"{E1}/lines.json"], [(f"{E1}/script.md", 1, "depends")]),
            ([f"{E1}/premise.md"], [(f"{E1}/beats.md", 1, "depends")]),
            ([f"{E2}/sequences/Q2/sequence.json"],
             [(f"{E2}/script.md", 12, "sequences"), (f"{E2}/sequences/Q2/shots/Q2-1.json", 1, "depends"),
              (f"{E2}/sequences/Q2/shots/Q2-2.json", 1, "depends")]),
            # episode-acts does not skip outline: its script needs one
            ([f"{E2}/outline.md"], [(f"{E2}/script.md", 1, "depends")]),
        ]
        for drop, expected in cases:
            with self.subTest(drop):
                self.fails(expected, drop=drop)
        root = self.tree()
        for path in sorted((root / E1 / "sequences").rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        self.assertOnly(root, "structure", [(f"{E1}/reel/edit-list.json", 1, "depends"),
                                            (f"{E1}/reel/edit-list.json", 1, "edit-list"),
                                            (f"{E1}/reel/edit-list.json", 1, "edit-list")])

    def test_output_is_deterministic(self):
        root = self.tree(self.edit(f"{E2}/outline.md", "beats: B3", "beats: B3, B9"),
                         drop=[f"{E1}/lines.json"])
        a = cli("--root", str(root), "--only", "structure", "--json")
        b = cli("--root", str(root), "--only", "structure", "--json")
        self.assertEqual(a.stdout, b.stdout)
        self.assertEqual(a.returncode, 1)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    unittest.main()
