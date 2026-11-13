"""The production room's checker role files (brief sections 4 and 6, harness section 5)."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import frontmatter, markers, roles  # noqa: E402
from kit.lib.frontmatter import as_list  # noqa: E402

KIT = ROOT / "kit"
PIPELINE = roles.load_pipeline(KIT / "pipeline.json")

# role id: (room, kind, family, may_not). may_not is the brief's section 4 cell, word for word.
ROLES = {
    "deformation-reviewer": ("production", "checker", "any", "fix the work"),
    "cinematography-reviewer": ("production", "checker", "any", "fix the work"),
    "performance-reviewer": ("production", "checker", "any", "fix the work"),
    "dailies-reviewer": ("production", "checker", "any", "fix the work"),
    "mix-reviewer": ("production", "checker", "any", "fix the work"),
}
# Stages the brief's table gives roles that no pipeline entry names.
UNNAMED_STAGES = {
    "deformation-reviewer": ["rigging"],
    "cinematography-reviewer": ["layout"],
    "performance-reviewer": ["animation"],
    "dailies-reviewer": ["render"],
    "mix-reviewer": ["conform"],
}

HEADINGS = ["Read first", "Task", "Rubric", "You may not", "Markers", "Result", "Provenance",
            "Blocked", "Rules"]
AUTHORITY = ("the rules of power, the charter, the production rules, the canon ledger, the episode "
             "ledger, the role file, the task.")
ALL_VERBS = sorted(set(markers.ROLE_VERBS) | set(markers.CHANNEL_VERBS)
                   | {v for verbs in markers.PROGRAM_VERBS.values() for v in verbs}
                   | {v for verbs in markers.EXTRA_VERBS.values() for v in verbs})
FORBIDDEN = re.compile(r"(claude|opus|sonnet|gpt|--model|sk-|/Users/)")
MAX_LINES = 120
FETCH = "fetch a file from a store or run a program"


def names(value):
    return [value] if isinstance(value, str) and value else list(value or [])


def expected_stages(role_id):
    named = [e["stage"] for e in PIPELINE
             if role_id in names(e["checker"]) + names(e["judge"])]
    return named or UNNAMED_STAGES[role_id]


def sections(body):
    """[(heading, text)] for every second-level heading, in order."""
    parts = re.split(r"(?m)^## (.+)$", body)
    return list(zip(parts[1::2], parts[2::2]))


def own_problems(role_id, text):
    """What this ticket's rules find wrong with one role file. Empty is clean."""
    room, kind, family, may_not = ROLES[role_id]
    try:
        meta, body = frontmatter.parse(text)
    except frontmatter.FrontMatterError as error:
        return [str(error)]
    problems = []
    keys = set(roles.ROLE_REQUIRED) | {"may_not"}
    problems += [f"unexpected key {k!r}" for k in sorted(set(meta) - keys)]
    problems += [f"missing key {k!r}" for k in sorted(keys - set(meta))]
    expected = {
        "role": role_id, "room": room, "kind": kind, "family": family, "may_not": may_not,
        "model": "checker", "secrets": "", "gated": "no",
        "stage": ", ".join(expected_stages(role_id)),
        "tools": "Read, Grep, Glob",
        "knowledge": "skills, memory-read",
    }
    for key, value in expected.items():
        if key in meta and meta[key] != value:
            problems.append(f"{key} is {meta[key]!r}, expected {value!r}")
    reads = as_list(meta.get("reads"))
    if not {"bible/**", "episodes/**"} <= set(reads):
        problems.append(f"reads {reads} lacks bible/** or episodes/**")

    if len(text.splitlines()) > MAX_LINES:
        problems.append(f"{len(text.splitlines())} lines, more than {MAX_LINES}")
    if FORBIDDEN.search(text):
        problems.append(f"names a model, flag or path: {FORBIDDEN.search(text).group(0)!r}")

    found = set(re.findall(r"\{\{([^{}]*)\}\}", body))
    for name in roles.PLACEHOLDERS + ("PR",):
        if name not in found:
            problems.append(f"missing placeholder {{{{{name}}}}}")
    if "BRANCH" in found:
        problems.append("has placeholder {{BRANCH}}")

    parts = sections(body)
    if [h for h, _ in parts] != HEADINGS:
        problems.append(f"headings {[h for h, _ in parts]} are not {HEADINGS}")
    raw = dict(parts)
    part = {heading: " ".join(text.split()) for heading, text in parts}

    if not part.get("Read first", "").strip().endswith(AUTHORITY):
        problems.append("Read first does not end with the order of authority")
    for name in ("TASK", "TICKET", "PR", "REPO", "WORKTREE"):
        if f"{{{{{name}}}}}" not in part.get("Task", ""):
            problems.append(f"Task lacks {{{{{name}}}}}")
    if part.get("Rules", "").strip() != "{{RULES}}":
        problems.append("Rules is not {{RULES}}")

    criteria = re.split(r"(?m)^\d+\. ", raw.get("Rubric", ""))[1:]
    if len(criteria) < 5:
        problems.append(f"rubric has {len(criteria)} criteria, fewer than 5")
    for number, criterion in enumerate(criteria, 1):
        if "Blocker:" not in criterion or "Issue:" not in criterion:
            problems.append(f"rubric criterion {number} lacks a Blocker or an Issue")

    forbid = part.get("You may not", "")
    for phrase in (may_not, "fix the work", "write", "contact GitHub"):
        if phrase not in forbid:
            problems.append(f"You may not lacks {phrase!r}")

    allowed = set(markers.MAY_POST[kind])
    named = {v for v in ALL_VERBS
             if re.search(rf"(?<![A-Za-z]){re.escape(v)}(?![A-Za-z])", part.get("Markers", ""))}
    if named != allowed:
        problems.append(f"Markers names {sorted(named)}, expected {sorted(allowed)}")

    result = part.get("Result", "")
    for field in ("`marker`", "`evidence`", "`notes`", "`B<n>`", "`I<n>`", "`severity`",
                  "`blocked`"):
        if field not in result:
            problems.append(f"Result lacks {field}")
    if "REPEAT <its earlier note id>:" not in result:
        problems.append("Result lacks the REPEAT rule")

    if room == "production":
        if "production/**" not in reads:
            problems.append(f"reads {reads} lacks production/**")
        media = (f"of `outputs` in `{meta.get('stage')}/manifest.json` from the asset store to "
                 "`review/<name>`. You cannot fetch a file yourself.")
        if media not in part.get("Read first", ""):
            problems.append("Read first does not say how the media reach review/")
        if FETCH not in forbid:
            problems.append(f"You may not lacks {FETCH!r}")

    if "A memory advises; a file binds." not in part.get("Provenance", ""):
        problems.append("Provenance lacks 'A memory advises; a file binds.'")
    for field in ("`missing`", "`tried`", "`resume_from`"):
        if field not in part.get("Blocked", ""):
            problems.append(f"Blocked lacks {field}")
    return problems


def shipped(role_id):
    return (KIT / "roles" / f"{role_id}.md").read_text(encoding="utf-8")


def edited(test, role_id, old, new):
    text = shipped(role_id)
    test.assertIn(old, text)
    return own_problems(role_id, text.replace(old, new, 1))


class ShippedFilesTest(unittest.TestCase):
    def test_every_file_is_shipped(self):
        files = {p.stem for p in (KIT / "roles").glob("*.md")}
        self.assertLessEqual(set(ROLES), files)

    def test_validate_names_none_of_the_five(self):
        problems = roles.validate(KIT)
        for role_id in ROLES:
            with self.subTest(role=role_id):
                self.assertEqual([p for p in problems if p.startswith(f"roles/{role_id}.md:")
                                  or f"'{role_id}'" in p], [])

    def test_each_file_meets_the_ticket(self):
        for role_id in ROLES:
            with self.subTest(role=role_id):
                self.assertEqual(own_problems(role_id, shipped(role_id)), [])

    def test_no_model_flag_or_path_in_the_five(self):
        for role_id in ROLES:
            with self.subTest(role=role_id):
                self.assertIsNone(FORBIDDEN.search(shipped(role_id)))


class SeededDefectTest(unittest.TestCase):
    def test_production_checker_without_media(self):
        self.assertEqual(edited(self, "dailies-reviewer", "You cannot fetch a file yourself.", ""),
                         ["Read first does not say how the media reach review/"])

    def test_production_checker_with_the_wrong_stage(self):
        own = edited(self, "performance-reviewer", "stage: animation", "stage: layout")
        self.assertIn("stage is 'layout', expected 'animation'", own)
        self.assertIn("Read first does not say how the media reach review/", own)

    def test_production_checker_in_the_story_room(self):
        self.assertEqual(edited(self, "mix-reviewer", "room: production", "room: story"),
                         ["room is 'story', expected 'production'"])

    def test_production_checker_without_production_reads_or_fetch_rule(self):
        text = shipped("deformation-reviewer")
        for old, new in ((", production/**", ""), (f"- {FETCH}: you hold no key and no shell.",
                                                     "- fetch")):
            self.assertIn(old, text)
            text = text.replace(old, new, 1)
        self.assertEqual(own_problems("deformation-reviewer", text),
                         ["reads ['bible/**', 'episodes/**'] lacks production/**",
                          f"You may not lacks {FETCH!r}"])


if __name__ == "__main__":
    unittest.main()
