"""The production room's orchestrator and operator role files (brief sections 3 to 6)."""

import fnmatch
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import frontmatter, markers, roles  # noqa: E402
from kit.lib.frontmatter import as_list  # noqa: E402

KIT = ROOT / "kit"
FACTORY = KIT / "factory"

# The production room's roles. No pipeline entry names their stages yet: these are the brief's section 6 table, in order.
ROLES = {
    "line-producer": ("orchestrator", "change the story",
                      "intake, assets, rigging, layout, animation, simulation, lighting, render, "
                      "conform, cross-shot"),
    "render-wrangler": ("operator", "write or change a scene script",
                        "assets, rigging, layout, animation, simulation, lighting, render, conform"),
}
# A write glob may never reach these: the kit, the ledger, the manifest alone, the rules.
OFF_LIMITS = ("kit/lib/roles.py", ".factory/roles/x.md", "ledger/spend.tsv", "media/manifest.tsv",
              "rules/production-rules.md")

HEADINGS = ["Read first", "Task", "What you produce", "You may not", "Markers", "Result",
            "Provenance", "Blocked", "Rules"]
AUTHORITY = ("the rules of power, the charter, the production rules, the canon ledger, the episode "
             "ledger, the role file, the task.")
ALL_VERBS = sorted(set(markers.ROLE_VERBS) | set(markers.CHANNEL_VERBS)
                   | {v for verbs in markers.PROGRAM_VERBS.values() for v in verbs}
                   | {v for verbs in markers.EXTRA_VERBS.values() for v in verbs})
FORBIDDEN = re.compile(r"(claude|opus|sonnet|gpt|--model|sk-|/Users/)")
MAX_LINES = 120
BRANCHED = ("operator",)

# What one role's body must say besides the shared rules.
SPECIFIC = {
    "line-producer": ("Ruling after three rounds", "Pitch selection", "A story question",
                      "A blocked worker", "`RULING`", "`SELECTED #<pr>`", "the showrunner answers it",
                      "what was decided", "charter clause", "how to overrule", "cannot be rejected"),
    "render-wrangler": ("exactly the keys `ticket`, `tool`, `inputs`, `expected` and `caller`",
                        "`caller`: `render-wrangler`", "the job, never you",
                        "You copy, shorten or add no line of a scene script.",
                        "You call no tool, do not run `factory gate` or `factory run`, and hold no "
                        "credential", "checked by `dailies-reviewer`",
                        "Do not repair a scene script to avoid blocking."),
}


def expected_meta(role_id):
    kind, may_not, stage = ROLES[role_id]
    if kind == "orchestrator":
        return {"role": role_id, "room": "production", "kind": kind, "analog": "line producer",
                "stage": stage,
                "reads": "bible/**, episodes/**, production/**, registry/**, playbook/**",
                "may_not": may_not, "model": "maker", "family": "any", "tools": "Read, Grep, Glob",
                "knowledge": "skills, memory-read, search", "secrets": "", "gated": "no"}
    return {"role": role_id, "room": "production", "kind": kind, "analog": "render wrangler",
            "stage": stage, "writes": "jobs/**",
            "reads": "bible/**, episodes/**, production/**, media/manifest.tsv",
            "may_not": may_not, "model": "maker", "family": "any",
            "tools": "Read, Write, Grep, Glob, Bash", "knowledge": "", "secrets": "", "gated": "yes"}


def sections(body):
    """[(heading, text)] for every second-level heading, in order."""
    parts = re.split(r"(?m)^## (.+)$", body)
    return list(zip(parts[1::2], parts[2::2]))


def own_problems(role_id, text):
    """What this ticket's rules find wrong with one role file. Empty is clean."""
    kind = ROLES[role_id][0]
    try:
        meta, body = frontmatter.parse(text)
    except frontmatter.FrontMatterError as error:
        return [str(error)]
    problems = []
    expected = expected_meta(role_id)
    keys = set(roles.ROLE_REQUIRED) | set(expected)
    problems += [f"unexpected key {k!r}" for k in sorted(set(meta) - keys)]
    problems += [f"missing key {k!r}" for k in sorted(keys - set(meta))]
    for key, value in expected.items():
        if key in meta and meta[key] != value:
            problems.append(f"{key} is {meta[key]!r}, expected {value!r}")
    if "bible/**" not in as_list(meta.get("reads")):
        problems.append("reads lacks bible/**")
    if not meta.get("analog") or re.search(r"[,.;]", meta["analog"]):
        problems.append(f"analog {meta.get('analog')!r} is not one phrase")
    for glob in as_list(meta.get("writes")):
        for path in OFF_LIMITS:
            if fnmatch.fnmatchcase(path, glob):
                problems.append(f"writes {glob!r} reaches {path}")

    if len(text.splitlines()) > MAX_LINES:
        problems.append(f"{len(text.splitlines())} lines, more than {MAX_LINES}")
    if FORBIDDEN.search(text):
        problems.append(f"names a model, flag or path: {FORBIDDEN.search(text).group(0)!r}")

    found = set(re.findall(r"\{\{([^{}]*)\}\}", body))
    for name in roles.PLACEHOLDERS:
        if name not in found:
            problems.append(f"missing placeholder {{{{{name}}}}}")
    if kind in BRANCHED and "BRANCH" not in found:
        problems.append("missing placeholder {{BRANCH}}")
    if kind not in BRANCHED and "BRANCH" in found:
        problems.append("has placeholder {{BRANCH}}")
    if "PR" in found:
        problems.append("has placeholder {{PR}}")

    parts = sections(body)
    if [h for h, _ in parts] != HEADINGS:
        problems.append(f"headings {[h for h, _ in parts]} are not {HEADINGS}")
    part = {heading: " ".join(text.split()) for heading, text in parts}

    if not part.get("Read first", "").endswith(AUTHORITY):
        problems.append("Read first does not end with the order of authority")
    task = ("TASK", "TICKET", "REPO", "WORKTREE") + (("BRANCH",) if kind in BRANCHED else ())
    for name in task:
        if f"{{{{{name}}}}}" not in part.get("Task", ""):
            problems.append(f"Task lacks {{{{{name}}}}}")
    if part.get("Rules", "") != "{{RULES}}":
        problems.append("Rules is not {{RULES}}")
    if expected["may_not"] not in part.get("You may not", ""):
        problems.append(f"You may not lacks {expected['may_not']!r}")

    named = {v for v in ALL_VERBS
             if re.search(rf"(?<![A-Za-z]){re.escape(v)}(?![A-Za-z])", part.get("Markers", ""))}
    if named != set(markers.MAY_POST[kind]):
        problems.append(f"Markers names {sorted(named)}, expected {sorted(markers.MAY_POST[kind])}")

    result = part.get("Result", "")
    for field in ("`marker`", "`summary`", "`evidence`", "`blocked`"):
        if field not in result:
            problems.append(f"Result lacks {field}")
    if "A memory advises; a file binds." not in part.get("Provenance", ""):
        problems.append("Provenance lacks 'A memory advises; a file binds.'")
    for field in ("`missing`", "`tried`", "`resume_from`"):
        if field not in part.get("Blocked", ""):
            problems.append(f"Blocked lacks {field}")

    for phrase in SPECIFIC.get(role_id, ()):
        if phrase not in " ".join(body.split()):
            problems.append(f"body lacks {phrase!r}")
    return problems


class KitCopy:
    """A temporary copy of the shipped kit, to seed one defect in."""

    def __init__(self, test):
        self.test = test
        tmp = tempfile.mkdtemp()
        test.addCleanup(shutil.rmtree, tmp)
        self.dir = Path(tmp) / "kit"
        shutil.copytree(KIT, self.dir, ignore=shutil.ignore_patterns("__pycache__"))

    def path(self, role_id):
        return self.dir / "roles" / f"{role_id}.md"

    def edit(self, role_id, old, new):
        self.test.assertTrue(self.path(role_id).is_file(), f"roles/{role_id}.md is not shipped")
        text = self.path(role_id).read_text(encoding="utf-8")
        self.test.assertIn(old, text)
        self.path(role_id).write_text(text.replace(old, new, 1), encoding="utf-8")

    def own(self, role_id):
        return own_problems(role_id, self.path(role_id).read_text(encoding="utf-8"))


def run(*args, cwd=ROOT):
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True, cwd=cwd)


class ShippedFilesTest(unittest.TestCase):
    def test_shipped_kit_validates(self):
        self.assertEqual(roles.validate(KIT), [])

    def test_each_file_meets_the_ticket(self):
        for role_id in ROLES:
            with self.subTest(role=role_id):
                path = KIT / "roles" / f"{role_id}.md"
                self.assertTrue(path.is_file(), f"roles/{role_id}.md is not shipped")
                self.assertEqual(own_problems(role_id, path.read_text(encoding="utf-8")), [])


class ProductionRoomTest(unittest.TestCase):
    def test_the_room_has_one_orchestrator(self):
        loaded = roles.load_roles(KIT / "roles")
        for role_id in ROLES:
            self.assertTrue(role_id in loaded, f"{role_id} is not a role")
            self.assertEqual(loaded[role_id]["meta"].get("room"), "production")
        orchestrators = [r for r, d in sorted(loaded.items())
                         if d["meta"].get("room") == "production"
                         and d["meta"].get("kind") == "orchestrator"]
        self.assertEqual(orchestrators, ["line-producer"])

    def test_line_producer_in_the_story_room(self):
        kit = KitCopy(self)
        kit.edit("line-producer", "room: production", "room: story")
        self.assertIn("room is 'story', expected 'production'", kit.own("line-producer"))
        self.assertEqual(roles.validate(kit.dir), [])

    def test_line_producer_with_another_may_not(self):
        kit = KitCopy(self)
        kit.edit("line-producer", "may_not: change the story", "may_not: lock in another room")
        self.assertIn("may_not is 'lock in another room', expected 'change the story'",
                      kit.own("line-producer"))

    def test_line_producer_answers_the_story_question(self):
        kit = KitCopy(self)
        kit.edit("line-producer", "the showrunner answers it", "you answer it")
        self.assertIn("body lacks 'the showrunner answers it'", kit.own("line-producer"))

    def test_render_wrangler_not_gated(self):
        kit = KitCopy(self)
        kit.edit("render-wrangler", "gated: yes", "gated: no")
        self.assertIn("roles/render-wrangler.md: gated must be 'yes' for kind operator",
                      roles.validate(kit.dir))

    def test_render_wrangler_at_render_only(self):
        kit = KitCopy(self)
        kit.edit("render-wrangler", "stage: " + ROLES["render-wrangler"][2], "stage: render")
        self.assertTrue(any(p.startswith("stage is 'render', expected")
                            for p in kit.own("render-wrangler")), kit.own("render-wrangler"))

    def test_render_wrangler_with_checked_by(self):
        kit = KitCopy(self)
        kit.edit("render-wrangler", "writes: jobs/**\n",
                 "writes: jobs/**\nchecked_by: dailies-reviewer\n")
        self.assertIn("unexpected key 'checked_by'", kit.own("render-wrangler"))
        self.assertEqual(roles.validate(kit.dir), [])

    def test_render_wrangler_writes_production(self):
        kit = KitCopy(self)
        kit.edit("render-wrangler", "writes: jobs/**", "writes: jobs/**, production/**")
        self.assertIn("writes is 'jobs/**, production/**', expected 'jobs/**'",
                      kit.own("render-wrangler"))

    def test_init_installs_them_with_the_production_room(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        story = Path(tmp) / "story"
        story.mkdir()
        result = run(FACTORY, "init", story, "--rooms", "story")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((story / ".factory" / "roles" / "showrunner.md").is_file())
        for role_id in ROLES:
            self.assertFalse((story / ".factory" / "roles" / f"{role_id}.md").exists(), role_id)
        both = Path(tmp) / "both"
        both.mkdir()
        result = run(FACTORY, "init", both, "--rooms", "production,story")
        self.assertEqual(result.returncode, 0, result.stderr)
        for role_id in ROLES:
            self.assertTrue((both / ".factory" / "roles" / f"{role_id}.md").is_file(), role_id)
        check = run(both / ".factory" / "factory", "roles", "--check", "--kit", both / ".factory")
        self.assertEqual((check.stdout, check.returncode), ("OK\n", 0), check.stderr)


if __name__ == "__main__":
    unittest.main()
