"""The story room's checker and judge role files (brief sections 4 and 5, harness section 5)."""

import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import frontmatter, lockrules, markers, roles  # noqa: E402
from kit.lib.frontmatter import as_list  # noqa: E402

KIT = ROOT / "kit"
PIPELINE = roles.load_pipeline(KIT / "pipeline.json")

# role id: (room, kind, family, may_not). may_not is the brief's section 4 cell, word for word.
ROLES = {
    "fact-checker": ("story", "checker", "any", "fix the work"),
    "canon-reviewer": ("story", "checker", "any", "fix the work"),
    "story-editor": ("story", "checker", "any", "fix the work"),
    "continuity-supervisor": ("studio", "checker", "any", "fix the work"),
    "look-reviewer": ("studio", "checker", "any", "fix the work"),
    "devils-advocate": ("story", "checker", "other", "decide"),
    "render-reviewer": ("story", "checker", "any", "change released video"),
    "pitch-judge": ("story", "judge", "other", "see makers' reasoning"),
    "braintrust": ("story", "judge", "other", "prescribe fixes; lock"),
    "audience-sim": ("story", "judge", "other", "override real telemetry"),
    "distinctiveness-judge": ("story", "judge", "other", "veto alone"),
    "held-out-judge": ("story", "judge", "other", "leak its prompts"),
}
STORY_STAGES = [e["stage"] for e in PIPELINE if e["room"] == "story"]
# Stages the brief's table gives roles that no pipeline entry names.
UNNAMED_STAGES = {"devils-advocate": STORY_STAGES, "render-reviewer": ["reel"]}

HEADINGS = ["Read first", "Task", "Rubric", "You may not", "Markers", "Result", "Provenance",
            "Blocked", "Rules"]
AUTHORITY = ("the rules of power, the charter, the production rules, the canon ledger, the episode "
             "ledger, the role file, the task.")
ALL_VERBS = sorted(set(markers.ROLE_VERBS) | set(markers.CHANNEL_VERBS)
                   | {v for verbs in markers.PROGRAM_VERBS.values() for v in verbs}
                   | {v for verbs in markers.EXTRA_VERBS.values() for v in verbs})
FORBIDDEN = re.compile(r"(claude|opus|sonnet|gpt|--model|sk-|/Users/)")
JUDGE_FORBIDDEN = re.compile(r"(?i)\b(maker|comment|pull request history|newer version is)\b")
MAX_LINES = 120
# The roles that read what is released, and the file that records a release (ISSUE-255).
RELEASED = ("canon-reviewer", "distinctiveness-judge", "render-reviewer")
UNOWNED = "registry/posts.tsv"


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
    keys = set(roles.ROLE_REQUIRED) | {"may_not"} | ({"actor"} if role_id == "braintrust" else set())
    problems += [f"unexpected key {k!r}" for k in sorted(set(meta) - keys)]
    problems += [f"missing key {k!r}" for k in sorted(keys - set(meta))]
    expected = {
        "role": role_id, "room": room, "kind": kind, "family": family, "may_not": may_not,
        "model": "checker", "secrets": "", "gated": "no",
        "stage": ", ".join(expected_stages(role_id)),
        "tools": ("Read, Grep, Glob, Bash, WebSearch, WebFetch" if role_id == "fact-checker"
                  else "Read, Grep, Glob"),
        "knowledge": "skills, memory-read" if kind == "checker" else "",
    }
    if role_id == "braintrust":
        expected["actor"] = "BRAINTRUST"
    for key, value in expected.items():
        if key in meta and meta[key] != value:
            problems.append(f"{key} is {meta[key]!r}, expected {value!r}")
    reads = as_list(meta.get("reads"))
    if not {"bible/**", "episodes/**"} <= set(reads):
        problems.append(f"reads {reads} lacks bible/** or episodes/**")
    if role_id in RELEASED and lockrules.RELEASE.format("*") not in reads:
        problems.append(f"reads lacks {lockrules.RELEASE.format('*')!r}")

    if len(text.splitlines()) > MAX_LINES:
        problems.append(f"{len(text.splitlines())} lines, more than {MAX_LINES}")
    if UNOWNED in text:
        problems.append(f"names {UNOWNED}, which no program writes")
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
    release = lockrules.RELEASE.format("<ep>")
    if role_id in RELEASED and f"`{release}`" not in part.get("Read first", ""):
        problems.append(f"Read first lacks {release!r}")
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
    if kind == "judge" and "Comparison question:" not in part.get("Rubric", ""):
        problems.append("rubric has no comparison question")

    forbid = part.get("You may not", "")
    for phrase in (may_not, "fix the work", "write", "contact GitHub"):
        if phrase not in forbid:
            problems.append(f"You may not lacks {phrase!r}")

    allowed = set(markers.MAY_POST[kind]) | ({"VOTE"} if role_id == "braintrust" else set())
    named = {v for v in ALL_VERBS
             if re.search(rf"(?<![A-Za-z]){re.escape(v)}(?![A-Za-z])", part.get("Markers", ""))}
    if named != allowed:
        problems.append(f"Markers names {sorted(named)}, expected {sorted(allowed)}")

    result = part.get("Result", "")
    for field in ("`marker`", "`evidence`", "`notes`", "`B<n>`", "`I<n>`", "`severity`",
                  "`blocked`"):
        if field not in result:
            problems.append(f"Result lacks {field}")
    if kind == "judge":
        for field in ("`prefers`", "`choice`", "`cited`"):
            if field not in result:
                problems.append(f"Result lacks {field}")
    elif "REPEAT <its earlier note id>:" not in result:
        problems.append("Result lacks the REPEAT rule")

    if "A memory advises; a file binds." not in part.get("Provenance", ""):
        problems.append("Provenance lacks 'A memory advises; a file binds.'")
    for field in ("`missing`", "`tried`", "`resume_from`"):
        if field not in part.get("Blocked", ""):
            problems.append(f"Blocked lacks {field}")

    if kind == "judge":
        outside = "".join(t for h, t in parts if h != "You may not")
        match = JUDGE_FORBIDDEN.search(outside)
        if match:
            problems.append(f"judge body mentions {match.group(0)!r}")
        for phrase in ("`A` and `B`", "not told which is newer", "Do not guess",
                       "Judge only the work in those two directories"):
            if phrase not in part.get("Task", ""):
                problems.append(f"judge Task lacks {phrase!r}")
    return problems


def stub(role_id, kind, checker):
    """A minimal valid role file for a story-room role another ticket ships."""
    lines = ["---", f"role: {role_id}", "room: story", f"kind: {kind}", f"analog: {role_id}",
             "stage:", "reads: bible/**", "model: checker", "family: any", "tools: Read",
             "knowledge:", "secrets:", "gated: no"]
    body = "{{REPO}} {{TICKET}} {{WORKTREE}} {{TASK}} {{RULES}}"
    if kind == "maker":
        lines += ["writes: stub/**", f"checked_by: {checker}"]
        body += " {{BRANCH}}"
    else:
        body += " {{PR}}"
    return "\n".join(lines + ["---", body, ""])


def missing_story_roles(kit):
    """{role id: (kind, checker)} for each story-room role the pipeline or a checked_by names
    that has no file in `kit`."""
    present = {p.stem for p in (kit / "roles").glob("*.md")}
    wanted = {}
    for entry in json.loads((kit / "pipeline.json").read_text(encoding="utf-8")):
        if entry["room"] != "story":
            continue
        checker = (names(entry["checker"]) + names(entry["judge"]))[0]
        for key, kind in (("maker", "maker"), ("checker", "checker"), ("judge", "judge")):
            for name in names(entry[key]):
                wanted.setdefault(name, (kind, checker))
    for doc in roles.load_roles(kit / "roles").values():
        if doc["meta"].get("room") in ("story", "studio"):
            for name in as_list(doc["meta"].get("checked_by")):
                wanted.setdefault(name, ("checker", ""))
    return {name: wanted[name] for name in sorted(wanted) if name not in present}


class KitCopy:
    """A temporary copy of the shipped kit with stubs for the story-room roles it lacks."""

    def __init__(self, test):
        tmp = tempfile.mkdtemp()
        test.addCleanup(shutil.rmtree, tmp)
        self.dir = Path(tmp) / "kit"
        shutil.copytree(KIT, self.dir, ignore=shutil.ignore_patterns("__pycache__"))
        self.stubbed = missing_story_roles(self.dir)
        for name, (kind, checker) in self.stubbed.items():
            (self.dir / "roles" / f"{name}.md").write_text(stub(name, kind, checker))

    def path(self, role_id):
        return self.dir / "roles" / f"{role_id}.md"

    def edit(self, role_id, old, new):
        text = self.path(role_id).read_text(encoding="utf-8")
        self.assertIn(old, text)
        self.path(role_id).write_text(text.replace(old, new, 1), encoding="utf-8")

    @staticmethod
    def assertIn(part, whole):
        assert part in whole, part

    def problems_naming(self, role_id):
        return [p for p in roles.validate(self.dir)
                if p.startswith(f"roles/{role_id}.md:") or f"'{role_id}'" in p]

    def own(self, role_id):
        return own_problems(role_id, self.path(role_id).read_text(encoding="utf-8"))


class ShippedFilesTest(unittest.TestCase):
    def test_every_file_is_shipped(self):
        shipped = {p.stem for p in (KIT / "roles").glob("*.md")}
        self.assertLessEqual(set(ROLES), shipped)

    def test_validate_names_none_of_the_twelve(self):
        kit = KitCopy(self)
        self.assertFalse(set(kit.stubbed) & set(ROLES), kit.stubbed)
        problems = roles.validate(kit.dir)
        for role_id in ROLES:
            with self.subTest(role=role_id):
                self.assertEqual(kit.problems_naming(role_id), [])
        for name in kit.stubbed:
            with self.subTest(stub=name):
                self.assertEqual([p for p in problems if p.startswith(f"roles/{name}.md:")], [])

    def test_each_file_meets_the_ticket(self):
        for role_id in ROLES:
            with self.subTest(role=role_id):
                text = (KIT / "roles" / f"{role_id}.md").read_text(encoding="utf-8")
                self.assertEqual(own_problems(role_id, text), [])

    def test_no_model_flag_or_path_in_any_role_file(self):
        for path in sorted((KIT / "roles").glob("*.md")):
            with self.subTest(file=path.name):
                self.assertIsNone(FORBIDDEN.search(path.read_text(encoding="utf-8")))


class SeededDefectTest(unittest.TestCase):
    def test_removed_rules_placeholder(self):
        kit = KitCopy(self)
        kit.edit("story-editor", "{{RULES}}", "")
        self.assertIn("roles/story-editor.md: body is missing placeholder {{RULES}}",
                      kit.problems_naming("story-editor"))
        self.assertIn("missing placeholder {{RULES}}", kit.own("story-editor"))

    def test_checker_set_to_maker(self):
        kit = KitCopy(self)
        kit.edit("canon-reviewer", "kind: checker", "kind: maker")
        self.assertIn("pipeline.json: story/bible: checker 'canon-reviewer' has kind maker, "
                      "not checker", kit.problems_naming("canon-reviewer"))
        self.assertIn("kind is 'maker', expected 'checker'", kit.own("canon-reviewer"))

    def test_judge_with_writes(self):
        kit = KitCopy(self)
        kit.edit("held-out-judge", "gated: no\n", "gated: no\nwrites: episodes/**\n")
        self.assertIn("unexpected key 'writes'", kit.own("held-out-judge"))

    def test_judge_mentions_makers(self):
        kit = KitCopy(self)
        kit.edit("pitch-judge", "## Rubric\n", "## Rubric\n\nAsk the maker why.\n")
        self.assertIn("judge body mentions 'maker'", kit.own("pitch-judge"))

    def test_judge_without_blind_instructions(self):
        kit = KitCopy(self)
        kit.edit("audience-sim", "You are not\ntold which is newer.", "")
        self.assertIn("judge Task lacks 'not told which is newer'", kit.own("audience-sim"))

    def test_headings_out_of_order(self):
        kit = KitCopy(self)
        kit.edit("look-reviewer", "## Markers\n", "## Marker list\n")
        self.assertTrue(any(p.startswith("headings ") for p in kit.own("look-reviewer")))

    def test_marker_of_another_kind(self):
        kit = KitCopy(self)
        kit.edit("fact-checker", "No other marker.", "Or `READY`.")
        self.assertIn("Markers names ['APPROVED', 'BLOCKED', 'NOTES', 'READY'], "
                      "expected ['APPROVED', 'BLOCKED', 'NOTES']", kit.own("fact-checker"))

    def test_braintrust_without_vote(self):
        kit = KitCopy(self)
        text = kit.path("braintrust").read_text(encoding="utf-8")
        kit.path("braintrust").write_text(
            re.sub(r"(?m)^- `VOTE .*\n", "", text), encoding="utf-8")
        self.assertTrue(any(p.startswith("Markers names") for p in kit.own("braintrust")))

    def test_checker_without_repeat_rule(self):
        kit = KitCopy(self)
        kit.edit("devils-advocate", "REPEAT <its earlier note id>:", "REPEAT:")
        self.assertIn("Result lacks the REPEAT rule", kit.own("devils-advocate"))

    def test_branch_placeholder_and_model_name(self):
        kit = KitCopy(self)
        kit.edit("render-reviewer", "{{TASK}}", "{{TASK}} {{BRANCH}} --model x")
        own = kit.own("render-reviewer")
        self.assertIn("has placeholder {{BRANCH}}", own)
        self.assertIn("names a model, flag or path: '--model'", own)
        self.assertIn("roles/render-reviewer.md: body has placeholder {{BRANCH}} not allowed for "
                      "kind checker", kit.problems_naming("render-reviewer"))

    def test_rubric_too_short(self):
        kit = KitCopy(self)
        text = kit.path("held-out-judge").read_text(encoding="utf-8")
        kit.path("held-out-judge").write_text(
            re.sub(r"(?ms)^[2-5]\. \*\*.*?(?=^\d\. |^\n)", "", text), encoding="utf-8")
        self.assertIn("rubric has 1 criteria, fewer than 5", kit.own("held-out-judge"))

    def test_too_long(self):
        kit = KitCopy(self)
        kit.edit("braintrust", "{{RULES}}", "{{RULES}}" + "\n" * MAX_LINES)
        self.assertTrue(any(p.endswith(f"more than {MAX_LINES}") for p in kit.own("braintrust")))

    def test_names_the_post_registry(self):
        kit = KitCopy(self)
        kit.edit("distinctiveness-judge", "`distribution/<ep>/release.json`", "`registry/posts.tsv`")
        own = kit.own("distinctiveness-judge")
        self.assertIn("names registry/posts.tsv, which no program writes", own)
        self.assertIn("Read first lacks 'distribution/<ep>/release.json'", own)

    def test_reads_without_the_release(self):
        kit = KitCopy(self)
        kit.edit("render-reviewer", ", distribution/*/release.json", "")
        self.assertEqual(kit.own("render-reviewer"), ["reads lacks 'distribution/*/release.json'"])


if __name__ == "__main__":
    unittest.main()
