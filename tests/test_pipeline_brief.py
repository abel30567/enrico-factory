"""The shipped `kit/pipeline.json` matches docs/system-brief.md sections 6 and 16."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import roles  # noqa: E402

PIPELINE = ROOT / "kit" / "pipeline.json"
BRIEF = ROOT / "docs" / "system-brief.md"
BRIEF_STAGES = ["research", "bible", "premise", "beats", "outline", "script", "sequences", "style",
                "board", "reel", "retro"]
RETRO = {"room": "story", "stage": "retro", "unit": "episode", "after": "", "maker": "retro-agent", "lint": [],
         "checker": "", "judge": "held-out-judge", "blind": False, "pitches": 1,
         "paths": ["playbook/**", "lab/proposals/**"]}


def bad_afters(entries):
    """(room, stage, after) for every `after` that is neither empty nor an earlier entry of its room."""
    seen, bad = set(), []
    for entry in entries:
        room, after = entry["room"], entry["after"]
        if after and (room, after) not in seen:
            bad.append((room, entry["stage"], after))
        seen.add((room, entry["stage"]))
    return bad


def story_makers(brief_text):
    """{stage: Maker cell} for every `| story |` row of section 6, without a trailing `, <n> pitches`."""
    section = re.search(r"(?ms)^## 6\. Stages$(.*?)^## ", brief_text).group(1)
    cells = [[c.strip() for c in line.strip("|").split("|")]
             for line in section.splitlines() if line.startswith("| story |")]
    return {row[1].strip("`"): re.sub(r", \d+ pitches$", "", row[3]) for row in cells}


def maker_mismatches(entries, brief_text):
    """(stage, brief cell, pipeline names) for every story stage whose two makers differ."""
    brief = story_makers(brief_text)
    bad = []
    for e in entries:
        if e["room"] != "story":
            continue
        makers = [e["maker"]] if isinstance(e["maker"], str) else e["maker"]
        if brief.get(e["stage"]) != ", ".join(makers):
            bad.append((e["stage"], brief.get(e["stage"]), ", ".join(makers)))
    return bad


def production_rows(brief_text):
    """{stage: (unit, maker, judged_by)} for every `| production |` row of section 6; a cell starting `none`
    is "", and a trailing ` (...)` is cut."""
    section = re.search(r"(?ms)^## 6\. Stages$(.*?)^## ", brief_text).group(1)
    rows = {}
    for line in section.splitlines():
        if not line.startswith("| production |"):
            continue
        cells = ["" if c.strip().startswith("none") else re.sub(r" \([^()]*\)$", "", c.strip())
                 for c in line.strip().strip("|").split("|")]
        rows[cells[1].strip("`")] = (cells[2], cells[3], cells[5])
    return rows


def production_mismatches(entries, brief_text):
    """(stage, key, brief, pipeline) for every production entry whose unit, maker or checker differs."""
    brief = production_rows(brief_text)
    bad = []
    for e in entries:
        if e["room"] != "production":
            continue
        unit, maker, checker = brief.get(e["stage"], (None, None, None))
        for key, want in (("unit", unit), ("maker", maker), ("checker", checker)):
            value = e[key] if key == "unit" else ", ".join([e[key]] if isinstance(e[key], str) else e[key])
            if want != value:
                bad.append((e["stage"], key, want, value))
    return bad


def entry(room, stage, after):
    return {"room": room, "stage": stage, "after": after}


class ShippedPipelineTest(unittest.TestCase):
    def setUp(self):
        self.entries = roles.load_pipeline(PIPELINE)
        self.by_stage = {e["stage"]: e for e in self.entries}

    def test_stage_names_in_order(self):
        self.assertEqual([e["stage"] for e in self.entries if e["room"] == "story"], BRIEF_STAGES)

    def test_sequences_writes_sequence_json(self):
        self.assertEqual(self.by_stage["sequences"]["paths"], ["episodes/*/sequences/*/sequence.json"])

    def test_board_comes_after_sequences(self):
        self.assertEqual(self.by_stage["board"]["after"], "sequences")

    def test_reel_is_checked_by_the_story_editor(self):
        self.assertEqual(self.by_stage["reel"]["checker"], "story-editor")

    def test_every_after_names_an_earlier_entry_of_the_same_room(self):
        self.assertEqual(bad_afters(self.entries), [])

    def test_maker_place_equals_the_brief_maker_column(self):
        brief = BRIEF.read_text(encoding="utf-8")
        self.assertEqual(sorted(story_makers(brief)), sorted(BRIEF_STAGES))
        self.assertEqual(maker_mismatches(self.entries, brief), [])

    def test_a_single_maker_where_the_brief_names_the_wrangler(self):
        brief = BRIEF.read_text(encoding="utf-8")
        single = [{**e, "maker": e["maker"][0]} if isinstance(e["maker"], list) else e for e in self.entries]
        self.assertEqual(maker_mismatches(single, brief), [
            ("style", "art-director, media-wrangler", "art-director"),
            ("board", "story-artist, media-wrangler", "story-artist"),
            ("reel", "editorial, media-wrangler", "editorial")])

    def test_reel_and_board_are_the_blind_story_stages(self):
        self.assertEqual([e["stage"] for e in self.entries if e["room"] == "story" and e["blind"]],
                         ["board", "reel"])

    def test_retro_is_the_entry_the_gaming_monitor_files(self):
        self.assertEqual(self.by_stage["retro"], RETRO)

    def test_a_story_stage_the_brief_does_not_name(self):
        brief = re.sub(r"(?m)^\| story \| `retro` \|.*\n", "", BRIEF.read_text(encoding="utf-8"))
        self.assertEqual(maker_mismatches(self.entries, brief), [("retro", None, "retro-agent")])


class ProductionPipelineTest(unittest.TestCase):
    def setUp(self):
        self.entries = roles.load_pipeline(PIPELINE)
        self.brief = BRIEF.read_text(encoding="utf-8")

    def test_stage_names_in_order(self):
        self.assertEqual([e["stage"] for e in self.entries if e["room"] == "production"],
                         ["intake", "assets", "rigging", "layout", "animation", "simulation", "lighting",
                          "cross-shot", "render", "conform"])

    def test_shipped_entries_match_the_brief(self):
        self.assertEqual(sorted(production_rows(self.brief)),
                         sorted(e["stage"] for e in self.entries if e["room"] == "production"))
        self.assertEqual(production_mismatches(self.entries, self.brief), [])

    def test_a_checker_the_brief_does_not_name(self):
        seeded = [{**e, "checker": "dailies-reviewer"} if e["stage"] == "simulation" else e for e in self.entries]
        self.assertEqual(production_mismatches(seeded, self.brief),
                         [("simulation", "checker", "", "dailies-reviewer")])


class BadAftersTest(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(bad_afters([entry("a", "one", ""), entry("a", "two", "one"),
                                     entry("b", "one", ""), entry("a", "three", "one")]), [])

    def test_after_names_a_later_entry(self):
        self.assertEqual(bad_afters([entry("a", "one", "two"), entry("a", "two", "")]),
                         [("a", "one", "two")])

    def test_after_names_an_entry_of_another_room(self):
        self.assertEqual(bad_afters([entry("a", "one", ""), entry("b", "two", "one")]),
                         [("b", "two", "one")])

    def test_after_names_no_entry(self):
        self.assertEqual(bad_afters([entry("a", "one", ""), entry("a", "two", "zero")]),
                         [("a", "two", "zero")])

    def test_after_names_its_own_entry(self):
        self.assertEqual(bad_afters([entry("a", "one", "one")]), [("a", "one", "one")])


if __name__ == "__main__":
    unittest.main()
