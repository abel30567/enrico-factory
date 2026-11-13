"""structure: rules 1 to 11 of an episode's files, and rule 12, the shape of a bible sheet."""

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
P1, R1, R2 = f"{E1}/intent-packet.json", f"{E1}/reel/edit-list.json", f"{E2}/reel/edit-list.json"
PIPELINE = json.loads((ROOT / "kit" / "pipeline.json").read_text(encoding="utf-8"))
AMA, HARBOUR = "bible/canon/characters/ama.md", "bible/canon/places/harbour.md"
AMA_LOOK, TOBI_LOOK = "bible/style/characters/ama.md", "bible/style/characters/tobi.md"
TOBI, LIGHTHOUSE_LOOK = "bible/canon/characters/tobi.md", "bible/style/places/lighthouse.md"
NO_ORIGIN = "no line 'origin: source' or 'origin: house'"
BAD_ORIGIN = "origin is not 'source' or 'house'"
NO_DESCRIPTION = "no description before the first '## ' heading"
NO_REFERENCE = "no reference image cited as a link to media:<id>"
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

    def says(self, expected, files=None, drop=(), root=None):
        """structure FAILs with exactly `expected` (path, line, rule, message)."""
        result = self.structure(root or self.tree(files, drop))
        self.assertEqual(result.status, lint.FAIL, result.reason)
        self.assertEqual([(f.path, f.line, f.rule, f.message) for f in result.findings], expected)

    def passes(self, files=None, drop=()):
        result = self.structure(self.tree(files, drop))
        self.assertEqual(result.status, lint.PASS, result.findings)

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

    # The story package at the reel: the intent packet and the bible version (ISSUE-97)

    def test_behaviour1_a_reel_needs_its_intent_packet(self):
        for ep in (E1, E2):
            self.assertTrue((CLEAN / ep / "intent-packet.json").is_file(), ep)
            self.assertEqual(load(f"{ep}/reel/edit-list.json")["bible"], "A-1")
        self.assertTrue((CLEAN / "bible/amendments/A-1.json").is_file())
        self.assertEqual(self.structure(CLEAN).status, lint.PASS)
        self.fails([(R1, 1, "depends")], drop=[P1])
        found = self.structure(self.tree(drop=[P1])).findings
        self.assertEqual(found[0].message, f"{P1} is missing")

    def test_behaviour2_rule11_intent_packet_shape(self):
        packet = load(P1)
        self.assertEqual(sorted(packet), ["cta", "hook", "spoilers", "tone"])
        bad = [{k: v for k, v in packet.items() if k != key} for key in sorted(packet)]
        bad += [dict(packet, extra="x"), dict(packet, spoilers="a reveal"), dict(packet, hook="   "),
                dict(packet, cta=["Ask", 3]), [packet]]
        for data in bad:
            with self.subTest(data):
                self.fails([(P1, 1, "intent-packet")], {P1: dump(data)})
        found = self.structure(self.tree({P1: dump([packet])})).findings
        self.assertEqual(found[0].message, "not {hook, spoilers: [...], cta: [...], tone}")
        result = self.structure(self.tree({P1: dump(dict(packet, spoilers=[], cta=[]))}))
        self.assertEqual(result.status, lint.PASS, result.findings)

    def test_behaviour3_bible_is_an_amendment_id(self):
        reel = load(R1)
        bad = [{k: v for k, v in reel.items() if k != "bible"}] + \
            [dict(reel, bible=value) for value in (1, "", "A1", "a-1", "A-1.json")]
        for data in bad:
            with self.subTest(data):
                self.fails([(R1, 1, "edit-list")], {R1: dump(data)})
                found = self.structure(self.tree({R1: dump(data)})).findings
                self.assertEqual(found[0].message, "bible is not the id A-<n> of an amendment")
        result = self.structure(self.tree({R1: dump(dict(reel, bible="A-1"))}))
        self.assertEqual(result.status, lint.PASS, result.findings)

    def test_behaviour4_bible_names_an_amendment_in_the_tree(self):
        later = {R2: dump(dict(load(R2), bible="A-2"))}
        self.fails([(R2, 1, "edit-list")], later)
        found = self.structure(self.tree(later)).findings
        self.assertEqual(found[0].message, "bible A-2: bible/amendments/A-2.json is missing")
        root = self.tree(dict(later, **{"bible/amendments/A-2.json": read("bible/amendments/A-1.json")}))
        self.assertEqual(load(R1)["bible"], "A-1")
        result = self.structure(root)
        self.assertEqual(result.status, lint.PASS, result.findings)

    def test_behaviour5_a_packet_without_a_reel_is_checked_for_its_shape(self):
        neither = self.tree(drop=[R1, P1])
        self.assertEqual(self.structure(neither).status, lint.PASS, self.structure(neither).findings)
        packet_only = self.tree(drop=[R1])
        self.assertEqual(self.structure(packet_only).status, lint.PASS, self.structure(packet_only).findings)
        self.assertOnly(self.tree({P1: dump(dict(load(P1), extra=1))}, drop=[R1]), "structure",
                        [(P1, 1, "intent-packet")])
        result = self.structure(self.tree({P1: "{not json\n"}, drop=[R1]))
        self.assertEqual(result.status, lint.ERROR)
        self.assertIn(P1, result.reason)

    def test_behaviour7_the_reel_stage_lints_post_the_refusal(self):
        checks = next(e["lint"] for e in PIPELINE if e["stage"] == "reel")
        self.assertIn("structure", checks)
        self.assertTrue(lint.marker(lint.run(CLEAN, only=checks)).startswith("[LINT] PASS"))
        body = lint.marker(lint.run(self.tree(drop=[P1]), only=checks))
        self.assertTrue(body.startswith("[LINT] FAIL"), body)
        self.assertIn(f"- B1 structure {R1}:1 {P1} is missing", body.splitlines())

    # Bible sheets (ISSUE-235)

    def test_sheet_behaviour1_a_character_sheet_has_behaviour_and_motivation_with_text(self):
        behaviour = "## Behaviour\n\nActs and speaks as the episode files in this tree show.\n"
        motivation = "## Motivation\n\nWants what the episode files in this tree show.\n"
        text = read(AMA)
        self.assertTrue(text.endswith(f"\n{behaviour}\n{motivation}"), text)
        head = text[:-len(f"{behaviour}\n{motivation}")]
        cases = [
            (head + motivation, [(AMA, 1, "sheets", "no '## Behaviour' heading")]),
            (head + behaviour, [(AMA, 1, "sheets", "no '## Motivation' heading")]),
            (head + "## Behaviour\n\n### Voice\n\n" + motivation,
             [(AMA, 7, "sheets", "'## Behaviour' has no text under it")]),
            (head + behaviour + "\n## Motivation\n\n",
             [(AMA, 11, "sheets", "'## Motivation' has no text under it")]),
            (head + "## behaviour\n\nCalm.\n\n" + motivation, [(AMA, 1, "sheets", "no '## Behaviour' heading")]),
        ]
        for body, expected in cases:
            with self.subTest(expected):
                self.says(expected, {AMA: body})
        self.passes({AMA: f"{head}{motivation}\n{behaviour}"})
        self.passes({AMA: f"{head}{behaviour}\n## Looks\n\nTall.\n\n{motivation}"})

    def test_sheet_behaviour2_a_canon_sheet_has_one_origin_line(self):
        for rel in (AMA, HARBOUR):
            text = read(rel)
            self.assertEqual(text.splitlines()[2], "origin: house A-1", rel)
            cases = [
                (text.replace("origin: house A-1\n\n", ""), [(rel, 1, "sheets", NO_ORIGIN)]),
                (text.replace("origin: house A-1", "origin: borrowed"), [(rel, 3, "sheets", BAD_ORIGIN)]),
                (text.replace("origin: house A-1", "origin:"), [(rel, 3, "sheets", BAD_ORIGIN)]),
                (text.replace("origin: house A-1", "origin:  sourced from the letters"),
                 [(rel, 3, "sheets", BAD_ORIGIN)]),
                (text.replace("origin: house A-1\n", "origin: house A-1\norigin: source sources/harbour.md\n"),
                 [(rel, 4, "sheets", "origin is repeated (first on line 3)")]),
                (text + "\norigin: house A-2\n",
                 [(rel, len(text.splitlines()) + 2, "sheets", "origin is repeated (first on line 3)")]),
            ]
            for body, expected in cases:
                with self.subTest(rel=rel, expected=expected):
                    self.says(expected, {rel: body})
            self.passes({rel: text.replace("origin: house A-1", "origin: source sources/harbour.md")})
            self.passes({rel: text.replace("origin: house A-1\n\n", "") + "\norigin: house A-1\n"})

    def test_sheet_behaviour3_a_canon_sheet_opens_with_a_description(self):
        self.says([(HARBOUR, 1, "sheets", NO_DESCRIPTION)], {HARBOUR: "# The harbour\n\norigin: house A-1\n"})
        self.says([(HARBOUR, 1, "sheets", NO_DESCRIPTION)],
                  {HARBOUR: "# The harbour\n\norigin: house A-1\n\n## Boats\n\nTwo boats.\n"})
        ama = "# Ama\n\norigin: house A-1\n\n## Behaviour\n\nThe keeper.\n\n## Motivation\n\nThe light.\n"
        self.says([(AMA, 1, "sheets", NO_DESCRIPTION)], {AMA: ama})
        self.passes({AMA: ama.replace("origin: house A-1\n", "origin: house A-1\nThe keeper of the lighthouse.\n")})

    def test_sheet_behaviour4_a_model_sheet_links_a_reference_image(self):
        text = read(AMA_LOOK)
        self.assertIn("![ama](media:ama-ref)", text)
        cases = [
            "# Ama\n\nA tall woman in a grey coat.\n",
            "# Ama\n\nReference: media:ama-ref\n",
            "# Ama\n\nReference: `![ama](media:ama-ref)`\n",
            "# Ama\n\n```\n![ama](media:ama-ref)\n```\n",
            "# Ama\n\nReference: ![ama](images/ama.png)\n",
        ]
        for body in cases:
            with self.subTest(body):
                self.says([(AMA_LOOK, 1, "sheets", NO_REFERENCE)], {AMA_LOOK: body})
        self.passes({AMA_LOOK: text.replace("![ama](media:ama-ref)", "[ref](media:ama-ref)")})
        unknown = self.tree({AMA_LOOK: text.replace("media:ama-ref", "media:ama-sketch")})
        self.assertEqual(self.structure(unknown).status, lint.PASS)
        self.assertEqual(lint.run(unknown, only=["references"])["references"].status, lint.FAIL)

    def test_sheet_behaviour5_a_shot_needs_the_model_sheets_of_its_subjects(self):
        shots = [rel for rel in lint.walk(CLEAN) if INPUT.match(rel) and "/shots/" in rel]
        with_tobi = [rel for rel in shots if "tobi" in load(rel)["characters"]]
        at_lighthouse = [rel for rel in shots if load(rel)["set"] == "lighthouse"]
        self.assertEqual(len(with_tobi), 6)
        self.assertEqual(len(at_lighthouse), 6)
        self.says([(rel, 1, "shots", "character tobi has no bible/style/characters/tobi.md") for rel in with_tobi],
                  drop=[TOBI_LOOK])
        self.says([(rel, 1, "shots", "set 'lighthouse' has no file under bible/style/places/")
                   for rel in at_lighthouse], drop=[LIGHTHOUSE_LOOK])
        self.says([(rel, 1, "shots", "character tobi has no bible/canon/characters/tobi.md") for rel in with_tobi],
                  drop=[TOBI_LOOK, TOBI])
        self.says([(rel, 1, "shots", "set 'lighthouse' has no file under bible/canon/places/")
                   for rel in at_lighthouse], drop=[LIGHTHOUSE_LOOK, "bible/canon/places/lighthouse.md"])
        requests = self.tree({f"{E1}/sequences/Q1/shots/requests/r1.json": dump({"characters": ["nell"]})})
        self.assertEqual(self.structure(requests).status, lint.PASS, self.structure(requests).findings)

    def test_sheet_behaviour6_sheets_are_checked_without_an_episode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / AMA).parent.mkdir(parents=True)
            (root / AMA).write_text(read(AMA).replace("origin: house A-1\n\n", ""), encoding="utf-8")
            self.says([(AMA, 1, "sheets", NO_ORIGIN)], root=root)
            (root / AMA).write_text(read(AMA), encoding="utf-8")
            (root / "bible/style/requests").mkdir(parents=True)
            (root / "bible/style/requests/r1.json").write_text("{}\n", encoding="utf-8")
            (root / "bible/style/palette.md").write_text("# Palette\n", encoding="utf-8")
            (root / "bible/style/characters/old").mkdir(parents=True)
            (root / "bible/style/characters/old/ama.md").write_text("# Ama\n", encoding="utf-8")
            self.assertEqual(self.structure(root).status, lint.PASS, self.structure(root).findings)

    def test_sheet_behaviour7_a_sheet_no_shot_cites_needs_no_model_sheet(self):
        nell = read(TOBI).replace("# Tobi", "# Nell").replace("Ama's younger brother, a ferry hand.", "A net mender.")
        mill = read(HARBOUR).replace("# The harbour", "# The mill")
        self.passes({"bible/canon/characters/nell.md": nell, "bible/canon/places/mill.md": mill})
        self.says([("bible/canon/characters/nell.md", 1, "sheets", NO_ORIGIN)],
                  {"bible/canon/characters/nell.md": nell.replace("origin: house A-1\n\n", ""),
                   "bible/canon/places/mill.md": mill})

    def test_sheet_the_bible_and_style_stages_lint_the_sheets(self):
        for stage in ("bible", "style"):
            checks = next(e["lint"] for e in PIPELINE if e["stage"] == stage)
            self.assertEqual(checks[0], "structure", stage)
            self.assertTrue(lint.marker(lint.run(CLEAN, only=checks)).startswith("[LINT] PASS"), stage)
        body = lint.marker(lint.run(self.tree({AMA_LOOK: "# Ama\n"}), only=["structure", "references", "media"]))
        self.assertIn(f"- B1 structure {AMA_LOOK}:1 {NO_REFERENCE}", body.splitlines())

    def test_output_is_deterministic(self):
        root = self.tree(dict(self.edit(f"{E2}/outline.md", "beats: B3", "beats: B3, B9"),
                              **{AMA: read(AMA).replace("origin: house A-1", "origin: borrowed"),
                                 HARBOUR: "# The harbour\n\norigin: house A-1\n",
                                 TOBI: read(TOBI).replace("## Motivation", "## Wants"),
                                 AMA_LOOK: "# Ama\n"}),
                         drop=[f"{E1}/lines.json", LIGHTHOUSE_LOOK])
        a = cli("--root", str(root), "--only", "structure", "--json")
        b = cli("--root", str(root), "--only", "structure", "--json")
        self.assertEqual(a.stdout, b.stdout)
        self.assertEqual(a.returncode, 1)
        messages = {f["message"] for f in json.loads(a.stdout)["checks"]["structure"]["findings"]}
        self.assertLessEqual({BAD_ORIGIN, NO_DESCRIPTION, "no '## Motivation' heading", NO_REFERENCE,
                              "set 'lighthouse' has no file under bible/style/places/"}, messages)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    unittest.main()
