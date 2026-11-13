"""The post room's role files (brief sections 4 and 16, "Rulebook", "Rights" and "Brand kit")."""

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import frontmatter, markers, production, roles  # noqa: E402
from kit.lib.frontmatter import as_list  # noqa: E402

KIT = ROOT / "kit"
READS = "bible/**, episodes/**, production/**, post/**, rulebook/**, brand/**, research/not-ours.tsv"
MAKER = {"kind": "maker", "checked_by": "charter-judge, packaging-judge", "model": "maker",
         "family": "any", "tools": "Read, Edit, Write, Grep, Glob, Bash",
         "knowledge": "skills, memory-read"}
CHECKER = {"kind": "checker", "may_not": "fix the work", "model": "checker", "family": "any",
           "tools": "Read, Grep, Glob", "knowledge": "skills, memory-read"}
# role id: (analog, writes, may_not) of a maker, from ruling 2.
MAKERS = {
    "platform-editor": ("online editor", "post/*/*/*/cut.json", "change the story or the master"),
    "reframe-artist": ("reframing editor", "post/*/*/*/reframe.json",
                       "change the story or the master"),
    "caption-writer": ("copywriter", "post/*/*/*/caption.json",
                       "give away a spoiler of the intent packet"),
    "cta-writer": ("copywriter for calls to action", "post/*/*/*/cta.json",
                   "write a CTA that matches a bait pattern"),
    "thumbnail-artist": ("key art designer", "post/*/*/*/thumbnail.json",
                         "show a spoiler of the intent packet"),
}
CHECKERS = {"charter-judge": "standards and practices editor", "packaging-judge": "packaging editor"}


def _meta(role_id, **values):
    return {"role": role_id, "room": "post", "stage": "variant", "reads": READS, "secrets": "",
            "gated": "no", **values}


# role id: the front matter of ruling 2, every key and its value.
EXPECTED = {
    "post-supervisor": _meta(
        "post-supervisor", kind="orchestrator", analog="post-production supervisor",
        writes="rulebook/**",
        may_not="score work; rule away a Blocker on a third party's character or work; "
                "change the brand kit",
        model="maker", family="any", tools="Read, Grep, Glob",
        knowledge="skills, memory-read, search"),
    **{r: _meta(r, analog=a, writes=w, may_not=m, **MAKER) for r, (a, w, m) in MAKERS.items()},
    **{r: _meta(r, analog=a, **CHECKER) for r, a in CHECKERS.items()},
}
# The rulebook keys each maker works to (ruling 6), each named in backticks in its Read first.
RULEBOOK_KEYS = {
    "platform-editor": ("aspects", "resolution", "min_seconds", "max_seconds", "caption_zone",
                        "mark_zone"),
    "reframe-artist": ("aspects", "resolution", "min_seconds", "max_seconds", "caption_zone",
                       "mark_zone"),
    "caption-writer": ("caption_zone", "caption_max_chars", "title_max_chars"),
    "cta-writer": ("caption_max_chars", "rulebook/bait.json"),
    "thumbnail-artist": (),
}
READ_FIRST = ("`brand/brand.json`", "`brand/voice.md`", "`research/not-ours.tsv`", "`rulebook/")
# A write glob may never reach these: the brand kit, the rights files, the manifest, the kit.
OFF_LIMITS = ("brand/brand.json", "brand/voice.md", "sources/rights.tsv", "research/not-ours.tsv",
              "media/manifest.tsv", "kit/lib/roles.py", ".factory/roles/x.md")
RULEBOOK = "rulebook/example.json"

MAKER_HEADINGS = ["Read first", "Task", "What you produce", "You may not", "Markers", "Result",
                  "Provenance", "Blocked", "Rules"]
CHECKER_HEADINGS = ["Read first", "Task", "Rubric", "You may not", "Markers", "Result",
                    "Provenance", "Blocked", "Rules"]
AUTHORITY = ("the rules of power, the charter, the production rules, the canon ledger, the episode "
             "ledger, the role file, the task.")
ALL_VERBS = sorted(set(markers.ROLE_VERBS) | set(markers.CHANNEL_VERBS)
                   | {v for verbs in markers.PROGRAM_VERBS.values() for v in verbs}
                   | {v for verbs in markers.EXTRA_VERBS.values() for v in verbs})
FORBIDDEN = re.compile(r"(claude|opus|sonnet|gpt|--model|sk-|/Users/)")
MAKER_RULES = ("Write only inside your `writes`", "dropped by the launcher",
               "Do not commit, push or open pull requests", "do not run `gh`")
MAX_LINES = 120
MIN_CRITERIA = 5
# The phrase groups one rubric criterion of packaging-judge must each hold (ruling 8).
PACKAGING = (("`brand/voice.md`",), ("third party's", "`research/not-ours.tsv`"),
             ("`title_max_chars`", "`caption_max_chars`"), ("`disclosure.field`",))


def sections(body):
    """[(heading, text)] for every second-level heading, in order."""
    parts = re.split(r"(?m)^## (.+)$", body)
    return list(zip(parts[1::2], parts[2::2]))


def normal(text):
    return " ".join(text.split())


def post_room(roles_dir):
    """The sorted ids of the role files under roles_dir whose room is post."""
    found = []
    for path in sorted(Path(roles_dir).glob("*.md")):
        meta, _ = frontmatter.parse(path.read_text(encoding="utf-8"))
        if meta.get("room") == "post":
            found.append(path.stem)
    return found


def post_problems(role_id, text):
    """What this ticket's rules find wrong with one post-room role file. Empty is clean."""
    expected = EXPECTED[role_id]
    kind = expected["kind"]
    try:
        meta, body = frontmatter.parse(text)
    except frontmatter.FrontMatterError as error:
        return [str(error)]
    problems = [f"missing key {k!r}" for k in sorted(set(expected) - set(meta))]
    problems += [f"unexpected key {k!r}" for k in sorted(set(meta) - set(expected))]
    for key, value in expected.items():
        if key in meta and meta[key] != value:
            problems.append(f"{key} is {meta[key]!r}, expected {value!r}")
    writes = as_list(meta.get("writes"))
    for path in OFF_LIMITS + (() if role_id == "post-supervisor" else (RULEBOOK,)):
        if production.matches(path, writes):
            problems.append(f"writes reaches {path}")

    lines = len(text.splitlines())
    if lines > MAX_LINES:
        problems.append(f"{lines} lines, more than {MAX_LINES}")
    if FORBIDDEN.search(text):
        problems.append(f"names a model, flag or path: {FORBIDDEN.search(text).group(0)!r}")
    parts = sections(body)
    headings = CHECKER_HEADINGS if kind == "checker" else MAKER_HEADINGS
    if [h for h, _ in parts] != headings:
        problems.append(f"headings {[h for h, _ in parts]} are not {headings}")
    part = {heading: normal(section) for heading, section in parts}

    read_first = part.get("Read first", "")
    for phrase in READ_FIRST + tuple(f"`{k}`" for k in RULEBOOK_KEYS.get(role_id, ())):
        if phrase not in read_first:
            problems.append(f"Read first lacks {phrase}")
    if not read_first.endswith(AUTHORITY):
        problems.append("Read first does not end with the order of authority")
    named = {v for v in ALL_VERBS
             if re.search(rf"(?<![A-Za-z]){re.escape(v)}(?![A-Za-z])", part.get("Markers", ""))}
    if named != set(markers.MAY_POST[kind]):
        problems.append(f"Markers names {sorted(named)}, expected {sorted(markers.MAY_POST[kind])}")
    if expected["may_not"] not in part.get("You may not", ""):
        problems.append(f"You may not lacks {expected['may_not']!r}")

    if kind == "maker":
        whole = normal(body)
        for phrase in MAKER_RULES:
            if phrase not in whole:
                problems.append(f"body lacks {phrase!r}")
        target = "`post/<ep>/<platform>/<kind>/" + expected["writes"].rsplit("/", 1)[1] + "`"
        if target not in part.get("What you produce", ""):
            problems.append(f"What you produce does not name {target}")
    if kind == "checker":
        rubric = dict(parts).get("Rubric", "")
        criteria = [normal(c) for c in re.split(r"(?m)^\d+\. ", rubric)[1:]]
        if len(criteria) < MIN_CRITERIA:
            problems.append(f"rubric has {len(criteria)} criteria, fewer than {MIN_CRITERIA}")
        for number, criterion in enumerate(criteria, 1):
            if "Blocker:" not in criterion or "Issue:" not in criterion:
                problems.append(f"rubric criterion {number} lacks a Blocker or an Issue")
        if role_id == "packaging-judge":
            for group in PACKAGING:
                if not any(all(p in c for p in group) for c in criteria):
                    problems.append(f"no rubric criterion holds {' and '.join(group)}")
    return problems


class KitCopy:
    """A temporary copy of the shipped kit, to seed one defect in."""

    def __init__(self, test):
        tmp = tempfile.mkdtemp()
        test.addCleanup(shutil.rmtree, tmp)
        self.dir = Path(tmp) / "kit"
        shutil.copytree(KIT, self.dir, ignore=shutil.ignore_patterns("__pycache__"))
        self.roles = self.dir / "roles"

    def path(self, role_id):
        return self.roles / f"{role_id}.md"

    def text(self, role_id):
        return self.path(role_id).read_text(encoding="utf-8")

    def edit(self, role_id, old, new):
        text = self.text(role_id)
        assert old in text, old
        self.path(role_id).write_text(text.replace(old, new, 1), encoding="utf-8")

    def sub(self, role_id, pattern, new):
        text, count = re.subn(pattern, new, self.text(role_id), count=1)
        assert count == 1, pattern
        self.path(role_id).write_text(text, encoding="utf-8")

    def problems(self, role_id):
        return post_problems(role_id, self.text(role_id))


class ShippedFilesTest(unittest.TestCase):
    def test_each_file_meets_the_ticket(self):
        for role_id in EXPECTED:
            with self.subTest(role=role_id):
                text = (KIT / "roles" / f"{role_id}.md").read_text(encoding="utf-8")
                self.assertEqual(post_problems(role_id, text), [])

    def test_shipped_kit_validates(self):
        self.assertEqual(roles.validate(KIT), [])

    def test_post_room_is_the_eight(self):
        self.assertEqual(post_room(KIT / "roles"), sorted(EXPECTED))


class SeededDefectTest(unittest.TestCase):
    def test_post_room_without_a_file(self):
        kit = KitCopy(self)
        kit.path("cta-writer").unlink()
        self.assertNotIn("cta-writer", post_room(kit.roles))
        self.assertEqual(len(post_room(kit.roles)), 7)

    def test_post_room_with_a_ninth_file(self):
        kit = KitCopy(self)
        text = kit.text("cta-writer").replace("role: cta-writer", "role: end-card-writer", 1)
        (kit.roles / "end-card-writer.md").write_text(text, encoding="utf-8")
        self.assertEqual(len(post_room(kit.roles)), 9)

    def test_maker_writes_the_brand_kit(self):
        kit = KitCopy(self)
        kit.edit("cta-writer", "writes: post/*/*/*/cta.json", "writes: post/*/*/*/cta.json, brand/**")
        problems = kit.problems("cta-writer")
        self.assertIn("writes reaches brand/brand.json", problems)
        self.assertIn("writes reaches brand/voice.md", problems)

    def test_maker_writes_the_rulebook(self):
        kit = KitCopy(self)
        kit.edit("platform-editor", "writes: post/*/*/*/cut.json",
                 "writes: post/*/*/*/cut.json, rulebook/**")
        self.assertIn("writes reaches rulebook/example.json", kit.problems("platform-editor"))

    def test_supervisor_writes_the_rulebook(self):
        kit = KitCopy(self)
        self.assertIn("writes: rulebook/**\n", kit.text("post-supervisor"))
        self.assertEqual(kit.problems("post-supervisor"), [])

    def test_reads_without_the_brand_kit(self):
        kit = KitCopy(self)
        kit.edit("charter-judge", "rulebook/**, brand/**, ", "rulebook/**, ")
        reads = READS.replace("brand/**, ", "")
        self.assertIn(f"reads is {reads!r}, expected {READS!r}", kit.problems("charter-judge"))

    def test_read_first_without_the_voice(self):
        kit = KitCopy(self)
        kit.edit("reframe-artist", " and `brand/voice.md`", "")
        self.assertIn("Read first lacks `brand/voice.md`", kit.problems("reframe-artist"))

    def test_read_first_without_a_rulebook_key(self):
        kit = KitCopy(self)
        kit.edit("platform-editor", " and `mark_zone`", "")
        self.assertIn("Read first lacks `mark_zone`", kit.problems("platform-editor"))

    def test_read_first_without_the_order_of_authority(self):
        kit = KitCopy(self)
        kit.edit("caption-writer", "the role file, the task.", "the task.")
        self.assertIn("Read first does not end with the order of authority",
                      kit.problems("caption-writer"))

    def test_packaging_judge_without_the_third_party_criterion(self):
        kit = KitCopy(self)
        kit.sub("packaging-judge", r"(?ms)^2\. \*\*Our own characters\.\*\*.*?(?=^3\. )", "")
        problems = kit.problems("packaging-judge")
        self.assertIn("no rubric criterion holds third party's and `research/not-ours.tsv`",
                      problems)
        self.assertIn("rubric has 4 criteria, fewer than 5", problems)

    def test_packaging_judge_without_the_voice_criterion(self):
        kit = KitCopy(self)
        kit.sub("packaging-judge", r"(?ms)^1\. \*\*Voice\.\*\*.*?(?=^2\. )", "")
        self.assertIn("no rubric criterion holds `brand/voice.md`", kit.problems("packaging-judge"))

    def test_criterion_without_an_issue(self):
        kit = KitCopy(self)
        kit.sub("charter-judge", r"Issue: drift\.", "")
        self.assertIn("rubric criterion 1 lacks a Blocker or an Issue", kit.problems("charter-judge"))

    def test_checker_with_a_maker_marker(self):
        kit = KitCopy(self)
        kit.edit("charter-judge", "No other marker.", "Or `READY`.")
        self.assertIn("Markers names ['APPROVED', 'BLOCKED', 'NOTES', 'READY'], "
                      "expected ['APPROVED', 'BLOCKED', 'NOTES']", kit.problems("charter-judge"))

    def test_supervisor_without_selected(self):
        kit = KitCopy(self)
        kit.sub("post-supervisor", r"- `SELECTED`: judgment 2\..*\n", "")
        self.assertTrue(any(p.startswith("Markers names") for p in kit.problems("post-supervisor")))

    def test_may_not_reworded(self):
        kit = KitCopy(self)
        kit.edit("cta-writer", "- write a CTA that matches a bait pattern.", "- write bait.")
        self.assertIn("You may not lacks 'write a CTA that matches a bait pattern'",
                      kit.problems("cta-writer"))

    def test_too_long(self):
        kit = KitCopy(self)
        text = kit.text("thumbnail-artist")
        padding = "\n" * (MAX_LINES + 1 - len(text.splitlines()))
        kit.path("thumbnail-artist").write_text(text + padding, encoding="utf-8")
        self.assertIn("121 lines, more than 120", kit.problems("thumbnail-artist"))

    def test_names_a_model_flag(self):
        kit = KitCopy(self)
        kit.edit("thumbnail-artist", "{{TASK}}", "{{TASK}} --model x")
        self.assertIn("names a model, flag or path: '--model'", kit.problems("thumbnail-artist"))

    def test_headings_out_of_order(self):
        kit = KitCopy(self)
        kit.edit("post-supervisor", "## Markers", "## Signals")
        self.assertTrue(any(p.startswith("headings [") for p in kit.problems("post-supervisor")))

    def test_produce_without_its_file(self):
        kit = KitCopy(self)
        kit.edit("caption-writer", "`post/<ep>/<platform>/<kind>/caption.json`", "the caption file")
        self.assertIn("What you produce does not name `post/<ep>/<platform>/<kind>/caption.json`",
                      kit.problems("caption-writer"))

    def test_maker_without_a_maker_rule(self):
        kit = KitCopy(self)
        kit.sub("caption-writer", r"dropped\s+by\s+the\s+launcher", "ignored")
        self.assertIn("body lacks 'dropped by the launcher'", kit.problems("caption-writer"))

    def test_key_missing_and_unexpected(self):
        kit = KitCopy(self)
        kit.edit("packaging-judge", "may_not: fix the work\n", "writes: post/**\n")
        problems = kit.problems("packaging-judge")
        self.assertIn("missing key 'may_not'", problems)
        self.assertIn("unexpected key 'writes'", problems)

    def test_maker_checked_by_the_supervisor(self):
        kit = KitCopy(self)
        kit.edit("cta-writer", "checked_by: charter-judge, packaging-judge",
                 "checked_by: post-supervisor")
        self.assertIn("roles/cta-writer.md: checked_by 'post-supervisor' has kind orchestrator, "
                      "not checker or judge", roles.validate(kit.dir))


if __name__ == "__main__":
    unittest.main()
