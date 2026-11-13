"""The story room's orchestrator, maker, operator and analyst role files (brief sections 4 and 5)."""

import fnmatch
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import frontmatter, lint, markers, production, roles  # noqa: E402
from kit.lib.frontmatter import as_list  # noqa: E402

KIT = ROOT / "kit"
FACTORY = KIT / "factory"
PIPELINE = roles.load_pipeline(KIT / "pipeline.json")
STORY = [e for e in PIPELINE if e["room"] == "story"]

MAKER_TOOLS = "Read, Edit, Write, Grep, Glob, Bash"
WEB = ", WebSearch, WebFetch"

# role id: (kind, may_not, checked_by). may_not and checked_by are the brief's section 4 cells.
ROLES = {
    "showrunner": ("orchestrator",
                   "score work; edit the charter without a vote; reject a note raised twice", ""),
    "retro-agent": ("maker", "ship a change that has not passed the replay suite", "held-out-judge"),
    "researcher": ("maker", "write story; skip the source and evidence fields", "fact-checker"),
    "trend-scout": ("analyst", "propose canon changes", ""),
    "audience-analyst": ("analyst", "prescribe fixes (symptoms only)", ""),
    "canon-keeper": ("maker", "change canon outside an amendment", "canon-reviewer"),
    "story-writer": ("maker", "contradict the canon ledger", "story-editor"),
    "script-writer": ("maker", "contradict the canon ledger", "story-editor"),
    "head-of-story": ("maker", "lock scenes", "continuity-supervisor"),
    "story-artist": ("maker", "change the style lock", "continuity-supervisor, pitch-judge"),
    "art-director": ("maker", "restyle mid-episode without an amendment", "look-reviewer"),
    "editorial": ("maker", "drop a beat silently", "story-editor"),
    "media-wrangler": ("operator", "write or change a prompt", ""),
}
# The one maker no pipeline entry names yet: its stage and writes come from the ticket.
UNNAMED = {"retro-agent": ("retro", "playbook/**, lab/proposals/**")}
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
MAKER_RULES = ("Write only inside your `writes`", "dropped by the launcher",
               "Do not commit, push or open pull requests", "do not run `gh`")
MAX_LINES = 120
BRANCHED = ("maker", "operator")


def names(value):
    return [value] if isinstance(value, str) and value else list(value or [])


def maker_entries(role_id):
    return [e for e in STORY if role_id in names(e["maker"])]


def expected_meta(role_id):
    kind, may_not, checked_by = ROLES[role_id]
    meta = {"role": role_id, "room": "story", "kind": kind, "may_not": may_not, "model": "maker",
            "family": "any", "secrets": "", "gated": "no"}
    if kind == "orchestrator":
        meta.update(stage=", ".join(e["stage"] for e in STORY), tools="Read, Grep, Glob",
                    knowledge="skills, memory-read, search")
    elif kind == "operator":
        meta.update(stage=", ".join(e["stage"] for e in maker_entries(role_id)), writes="jobs/**",
                    tools="Read, Write, Grep, Glob, Bash", knowledge="", gated="yes")
    elif kind == "maker":
        entries = maker_entries(role_id)
        stage, writes = UNNAMED.get(role_id, (
            ", ".join(e["stage"] for e in entries),
            ", ".join(p for e in entries for p in e["paths"])))
        web = role_id == "researcher"
        meta.update(stage=stage, writes=writes, checked_by=checked_by,
                    tools=MAKER_TOOLS + (WEB if web else ""),
                    knowledge="skills, memory-read" + (", search" if web else ""))
    else:
        meta.update(stage="", writes="", actor="ANALYST",
                    tools="Read, Grep, Glob" + (WEB if role_id == "trend-scout" else ""))
    return meta


def sections(body):
    """[(heading, text)] for every second-level heading, in order."""
    parts = re.split(r"(?m)^## (.+)$", body)
    return list(zip(parts[1::2], parts[2::2]))


def anchor(glob):
    """The fixed tail of a write glob, as a body would name it: `premise.md`, `shots/`."""
    return glob[:-2].rsplit("*/", 1)[-1] if glob.endswith("**") else glob.rsplit("*/", 1)[-1]


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
    fields = ["`marker`", "`summary`", "`evidence`", "`blocked`"]
    if kind == "maker":
        fields += ["acceptance criterion", "`addressed`", "`changed`", "`rebutted`"]
    if kind == "analyst":
        fields += ["`notes`"]
    for field in fields:
        if field not in result:
            problems.append(f"Result lacks {field}")
    if "A memory advises; a file binds." not in part.get("Provenance", ""):
        problems.append("Provenance lacks 'A memory advises; a file binds.'")
    for field in ("`missing`", "`tried`", "`resume_from`"):
        if field not in part.get("Blocked", ""):
            problems.append(f"Blocked lacks {field}")

    produce = part.get("What you produce", "")
    if kind == "maker":
        whole = " ".join(body.split())
        for phrase in MAKER_RULES:
            if phrase not in whole:
                problems.append(f"body lacks {phrase!r}")
        for glob in as_list(expected["writes"]):
            if anchor(glob) not in produce:
                problems.append(f"What you produce does not name {anchor(glob)!r}")
        for lint in sorted({l for e in maker_entries(role_id) for l in e["lint"]}):
            if f"`{lint}`" not in produce:
                problems.append(f"What you produce does not name lint {lint!r}")
    for phrase in SPECIFIC.get(role_id, ()):
        if phrase not in " ".join(body.split()):
            problems.append(f"body lacks {phrase!r}")
    return problems


# The words a maker's request and the media wrangler's Blocked section share (rulings 5 to 8 of #95).
RULE_6 = "A request that shows a character or a place names at least one reference image of it"
REQUEST = ("`prompt` the whole prompt text", "`refs` reference images, each `media:<id>`",
           "The media wrangler turns it into a job.", RULE_6, "you are launched again")
VOICE = ("No tool makes voice or music yet: until the task names one, write no request and cut the "
         "reel from panels, timing and the line text.")

# What one role's body must say besides the shared rules.
SPECIFIC = {
    "story-writer": ("A premise names a format (`format:` front matter) from `.factory/formats/`",),
    "script-writer": ("Every spoken line is in `lines.json`",),
    "showrunner": ("Ruling after three rounds", "Pitch selection", "Pivot triage",
                   "A blocked worker", "`RULING`", "`SELECTED #<pr>`", "what was decided",
                   "charter clause", "how to overrule", "cannot be rejected"),
    "audience-analyst": ("never says what to change",),
    "art-director": ("`bible/style/requests/<id>.json`", "`{for, prompt, refs}`", "`for` the sheet",
                     *REQUEST, "A model sheet or a design image of a new subject",
                     "cite its `media:<id>` in the sheet"),
    "story-artist": ("`episodes/<ep>/sequences/<Q>/shots/requests/<id>.json`", "`{for, prompt, refs}`",
                     "`for` the shot id", *REQUEST,
                     "A panel is only an image already in `media/manifest.tsv`.",
                     "a subject with no reference is designed in the `style` stage first",
                     "cite its `media:<id>` as the shot's `panel`"),
    "editorial": ("`episodes/<ep>/reel/requests/<id>.json`", "`{for, prompt, refs}`",
                  "`for` what it is for", *REQUEST[:3],
                  "Scratch voice and temp score are media files in `media/manifest.tsv`, referenced by id.",
                  VOICE),
    "media-wrangler": ("`{for, prompt, refs}`", "`for` is what it is for (the sheet, the shot id)",
                       "`prompt` the whole prompt text", "`refs` its reference images, each `media:<id>`",
                       "one job file per request, at the path the task names",
                       "exactly the keys `ticket`, `tool`, `inputs`, `expected` and `caller`",
                       "the request file as `{path, sha256}`",
                       "You copy, shorten or add no word of a prompt.",
                       "You call no tool, do not run `factory gate` or `factory run`, and hold no "
                       "credential", "the request is not `{for, prompt, refs}`",
                       "a `media:<id>` it names is not in `media/manifest.tsv`", RULE_6,
                       "it asks for voice or music", "Do not repair a request to avoid blocking."),
}


def coverage_problems(kit_dir):
    """Story pipeline entries whose paths their maker's writes miss, or whose maker's checked_by
    names a role that is not a checker or a judge. An operator in `maker` holds none of the paths."""
    loaded = roles.load_roles(Path(kit_dir) / "roles")
    problems = []
    for entry in roles.load_pipeline(Path(kit_dir) / "pipeline.json"):
        if entry["room"] != "story":
            continue
        for maker in names(entry["maker"]):
            meta = loaded[maker]["meta"] if maker in loaded else {}
            if meta.get("kind") == "operator":
                continue
            writes = as_list(meta.get("writes"))
            for path in entry["paths"]:
                if path not in writes:
                    problems.append(f"{entry['stage']}: {maker} writes lacks {path}")
            for name in as_list(meta.get("checked_by")):
                if loaded.get(name, {}).get("meta", {}).get("kind") not in ("checker", "judge"):
                    problems.append(f"{entry['stage']}: {maker} checked_by {name} is not a "
                                    "checker or judge")
    return problems


class KitCopy:
    """A temporary copy of the shipped kit, to seed one defect in."""

    def __init__(self, test):
        tmp = tempfile.mkdtemp()
        test.addCleanup(shutil.rmtree, tmp)
        self.dir = Path(tmp) / "kit"
        shutil.copytree(KIT, self.dir, ignore=shutil.ignore_patterns("__pycache__"))

    def path(self, role_id):
        return self.dir / "roles" / f"{role_id}.md"

    def edit(self, role_id, old, new):
        text = self.path(role_id).read_text(encoding="utf-8")
        assert old in text, old
        self.path(role_id).write_text(text.replace(old, new, 1), encoding="utf-8")

    def own(self, role_id):
        return own_problems(role_id, self.path(role_id).read_text(encoding="utf-8"))


class ShippedFilesTest(unittest.TestCase):
    def test_every_file_is_shipped(self):
        shipped = {p.stem for p in (KIT / "roles").glob("*.md")}
        self.assertLessEqual(set(ROLES), shipped)

    def test_shipped_kit_validates(self):
        self.assertEqual(roles.validate(KIT), [])

    def test_each_file_meets_the_ticket(self):
        for role_id in ROLES:
            with self.subTest(role=role_id):
                text = (KIT / "roles" / f"{role_id}.md").read_text(encoding="utf-8")
                self.assertEqual(own_problems(role_id, text), [])

    def test_every_story_stage_is_written_and_checked(self):
        self.assertEqual(coverage_problems(KIT), [])
        makers = {m for e in STORY for m in names(e["maker"])}
        self.assertEqual(makers | set(UNNAMED), {r for r, v in ROLES.items() if v[0] in BRANCHED})

    def test_factory_lists_25_story_and_studio_roles(self):
        result = subprocess.run([sys.executable, str(FACTORY), "roles"], capture_output=True,
                                text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0)
        lines = result.stdout.splitlines()
        self.assertEqual([l for l in lines if not l.startswith("  ")], ["story:", "studio:"])
        self.assertEqual(len([l for l in lines if l.startswith("  ")]), 25)
        self.assertIn("  media-wrangler (operator)", lines)

    def test_no_model_flag_or_path_in_any_role_file(self):
        for path in sorted((KIT / "roles").glob("*.md")):
            with self.subTest(file=path.name):
                self.assertIsNone(FORBIDDEN.search(path.read_text(encoding="utf-8")))


class SeededDefectTest(unittest.TestCase):
    def test_maker_without_branch(self):
        kit = KitCopy(self)
        kit.edit("story-writer", "on branch {{BRANCH}}", "on its branch")
        self.assertIn("roles/story-writer.md: body is missing placeholder {{BRANCH}}",
                      roles.validate(kit.dir))
        self.assertIn("missing placeholder {{BRANCH}}", kit.own("story-writer"))

    def test_writes_kit(self):
        kit = KitCopy(self)
        kit.edit("researcher", "writes: research/**", "writes: kit/**")
        self.assertIn("writes 'kit/**' reaches kit/lib/roles.py", kit.own("researcher"))
        self.assertIn("research: researcher writes lacks research/**", coverage_problems(kit.dir))

    def test_writes_manifest_or_rules(self):
        for glob in ("media/manifest.tsv", "rules/**", ".factory/**", "ledger/**"):
            with self.subTest(glob=glob):
                kit = KitCopy(self)
                kit.edit("editorial", "writes: episodes/*/reel/**",
                         f"writes: episodes/*/reel/**, {glob}")
                self.assertTrue(any(p.startswith(f"writes {glob!r} reaches")
                                    for p in kit.own("editorial")))

    def test_checked_by_unknown_role(self):
        kit = KitCopy(self)
        kit.edit("editorial", "checked_by: story-editor", "checked_by: no-such-role")
        self.assertIn("roles/editorial.md: checked_by 'no-such-role' is not a role",
                      roles.validate(kit.dir))
        self.assertIn("reel: editorial checked_by no-such-role is not a checker or judge",
                      coverage_problems(kit.dir))

    def test_checked_by_a_maker(self):
        kit = KitCopy(self)
        kit.edit("head-of-story", "checked_by: continuity-supervisor", "checked_by: editorial")
        self.assertIn("roles/head-of-story.md: checked_by 'editorial' has kind maker, not checker "
                      "or judge", roles.validate(kit.dir))

    def test_writes_missing_a_stage_path(self):
        kit = KitCopy(self)
        kit.edit("script-writer", ", episodes/*/ledger.json\n", "\n")
        self.assertIn("script: script-writer writes lacks episodes/*/ledger.json",
                      coverage_problems(kit.dir))

    def test_pr_placeholder(self):
        kit = KitCopy(self)
        kit.edit("canon-keeper", "{{TASK}}", "{{TASK}} {{PR}}")
        self.assertIn("has placeholder {{PR}}", kit.own("canon-keeper"))
        self.assertIn("roles/canon-keeper.md: body has placeholder {{PR}} not allowed for kind "
                      "maker", roles.validate(kit.dir))

    def test_analyst_with_branch(self):
        kit = KitCopy(self)
        kit.edit("trend-scout", "{{TASK}}", "{{TASK}} {{BRANCH}}")
        self.assertIn("has placeholder {{BRANCH}}", kit.own("trend-scout"))

    def test_marker_of_another_kind(self):
        kit = KitCopy(self)
        kit.edit("audience-analyst", "No other marker.", "Or `APPROVED`.")
        self.assertIn("Markers names ['APPROVED', 'BLOCKED', 'SYMPTOMS'], "
                      "expected ['BLOCKED', 'SYMPTOMS']", kit.own("audience-analyst"))

    def test_showrunner_without_launch(self):
        kit = KitCopy(self)
        kit.edit("showrunner", "`LAUNCH` is also posted", "A start is also posted")
        self.assertTrue(any(p.startswith("Markers names") for p in kit.own("showrunner")))

    def test_may_not_reworded(self):
        kit = KitCopy(self)
        kit.edit("head-of-story", "may_not: lock scenes", "may_not: lock")
        self.assertIn("may_not is 'lock', expected 'lock scenes'", kit.own("head-of-story"))

    def test_headings_out_of_order(self):
        kit = KitCopy(self)
        kit.edit("art-director", "## What you produce\n", "## Output\n")
        self.assertTrue(any(p.startswith("headings ") for p in kit.own("art-director")))

    def test_maker_rule_removed(self):
        kit = KitCopy(self)
        kit.edit("story-artist", "dropped by the launcher", "kept")
        self.assertIn("body lacks 'dropped by the launcher'", kit.own("story-artist"))

    def test_lint_unnamed(self):
        kit = KitCopy(self)
        kit.edit("story-artist", "`provenance`, ", "")
        self.assertIn("What you produce does not name lint 'provenance'", kit.own("story-artist"))

    def test_model_name_and_length(self):
        kit = KitCopy(self)
        kit.edit("retro-agent", "{{RULES}}", "{{RULES}} --model x" + "\n" * MAX_LINES)
        own = kit.own("retro-agent")
        self.assertIn("names a model, flag or path: '--model'", own)
        self.assertTrue(any(p.endswith(f"more than {MAX_LINES}") for p in own))


PACKET = "episodes/ep01/intent-packet.json"


def packet_writers(kit_dir):
    """The roles whose `writes` and the stages whose `paths` reach an episode's intent packet."""
    loaded = roles.load_roles(Path(kit_dir) / "roles")
    writers = sorted(r for r, doc in loaded.items()
                     if production.matches(PACKET, as_list(doc["meta"].get("writes"))))
    stages = sorted(e["stage"] for e in roles.load_pipeline(Path(kit_dir) / "pipeline.json")
                    if production.matches(PACKET, e["paths"]))
    return writers, stages


class IntentPacketTest(unittest.TestCase):
    """Behaviour 6 of ISSUE-97: editorial writes the packet, in the reel stage, and nobody else."""

    def test_only_editorial_at_the_reel_writes_the_packet(self):
        self.assertEqual(packet_writers(KIT), (["editorial"], ["reel"]))

    def test_the_old_reel_writes_cannot_write_the_packet(self):
        kit = KitCopy(self)
        kit.edit("editorial", "writes: episodes/*/reel/**, episodes/*/intent-packet.json",
                 "writes: episodes/*/reel/**")
        self.assertEqual(packet_writers(kit.dir), ([], ["reel"]))
        self.assertIn("reel: editorial writes lacks episodes/*/intent-packet.json", coverage_problems(kit.dir))

    def test_another_role_with_the_glob_is_seen(self):
        kit = KitCopy(self)
        kit.edit("script-writer", "episodes/*/ledger.json\n",
                 "episodes/*/ledger.json, episodes/*/intent-packet.json\n")
        self.assertEqual(packet_writers(kit.dir), (["editorial", "script-writer"], ["reel"]))


def edit_pipeline(kit, old, new):
    path = kit.dir / "pipeline.json"
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, old
    path.write_text(text.replace(old, new), encoding="utf-8")


class MediaWranglerTest(unittest.TestCase):
    """Behaviours 1 to 3 of ISSUE-95: the operator validates, is held like a maker, binds no path."""

    def test_kit_validates_with_the_operator(self):
        self.assertEqual(roles.validate(KIT), [])
        self.assertEqual(roles.load_roles(KIT / "roles")["media-wrangler"]["meta"]["kind"], "operator")
        for stage, maker in (("style", "art-director"), ("board", "story-artist"), ("reel", "editorial")):
            entry = next(e for e in PIPELINE if e["stage"] == stage)
            self.assertEqual(entry["maker"], [maker, "media-wrangler"], stage)

    def test_operator_not_gated(self):
        kit = KitCopy(self)
        kit.edit("media-wrangler", "gated: yes", "gated: no")
        self.assertIn("roles/media-wrangler.md: gated must be 'yes' for kind operator",
                      roles.validate(kit.dir))

    def test_operator_file_removed(self):
        kit = KitCopy(self)
        kit.path("media-wrangler").unlink()
        self.assertIn("pipeline.json: story/style: maker 'media-wrangler' is not a role",
                      roles.validate(kit.dir))

    def test_operator_file_meets_the_ticket(self):
        text = (KIT / "roles" / "media-wrangler.md").read_text(encoding="utf-8")
        self.assertEqual(own_problems("media-wrangler", text), [])

    def test_operator_writes_a_maker_path(self):
        kit = KitCopy(self)
        kit.edit("media-wrangler", "writes: jobs/**", "writes: jobs/**, episodes/*/reel/**")
        self.assertIn("writes is 'jobs/**, episodes/*/reel/**', expected 'jobs/**'",
                      kit.own("media-wrangler"))

    def test_operator_without_branch(self):
        kit = KitCopy(self)
        kit.edit("media-wrangler", "on branch {{BRANCH}}", "on its branch")
        own = kit.own("media-wrangler")
        self.assertIn("missing placeholder {{BRANCH}}", own)
        self.assertIn("Task lacks {{BRANCH}}", own)

    def test_operator_marker_of_another_kind(self):
        kit = KitCopy(self)
        kit.edit("media-wrangler", "No other marker.", "Or `APPROVED`.")
        self.assertIn("Markers names ['ADDRESSED', 'APPROVED', 'BLOCKED', 'READY'], "
                      "expected ['ADDRESSED', 'BLOCKED', 'READY']", kit.own("media-wrangler"))

    def test_operator_blocked_without_the_reference_rule(self):
        kit = KitCopy(self)
        kit.edit("media-wrangler", "it breaks the rule\n\"A request that shows a character or a place "
                 "names at least one reference image of it\" (the\nimported source art or an approved "
                 "model sheet)", "it breaks a rule")
        self.assertIn(f"body lacks {RULE_6!r}", kit.own("media-wrangler"))

    def test_coverage_binds_makers_only(self):
        self.assertEqual(coverage_problems(KIT), [])

    def test_coverage_of_a_second_maker(self):
        kit = KitCopy(self)
        edit_pipeline(kit, '"maker": ["story-artist", "media-wrangler"]',
                      '"maker": ["story-artist", "editorial"]')
        self.assertIn("board: editorial writes lacks episodes/*/sequences/*/shots/**",
                      coverage_problems(kit.dir))


class MakerRequestTest(unittest.TestCase):
    """Behaviour 5 of ISSUE-95: each maker file says how it asks for generated media."""

    def test_each_maker_file_carries_the_request(self):
        for role_id in ("art-director", "story-artist", "editorial"):
            with self.subTest(role=role_id):
                text = (KIT / "roles" / f"{role_id}.md").read_text(encoding="utf-8")
                self.assertEqual(own_problems(role_id, text), [])
                self.assertLessEqual(len(text.splitlines()), MAX_LINES)
                self.assertNotIn("generate or upload", text)

    def test_story_artist_without_the_shape(self):
        kit = KitCopy(self)
        kit.edit("story-artist", "`{for, prompt, refs}`", "a request")
        self.assertIn("body lacks '`{for, prompt, refs}`'", kit.own("story-artist"))

    def test_art_director_without_the_path(self):
        kit = KitCopy(self)
        kit.edit("art-director", "`bible/style/requests/<id>.json`", "a file")
        self.assertIn("body lacks '`bible/style/requests/<id>.json`'", kit.own("art-director"))

    def test_editorial_without_images_only(self):
        kit = KitCopy(self)
        kit.edit("editorial", "No tool makes voice or music yet", "A tool makes voice or music")
        self.assertIn(f"body lacks {VOICE!r}", kit.own("editorial"))


MANIFEST = ("id\tsha256\tbytes\tsource\n"
            "fox-ref\t" + "a" * 64 + "\t2048\timported source art\n")
REQUESTS = {"bible/style/requests/r1.json": "art-director",
            "episodes/ep01/sequences/Q1/shots/requests/r1.json": "story-artist",
            "episodes/ep01/reel/requests/r1.json": "editorial"}
REQUEST_LINTS = ["structure", "provenance", "references", "media"]


class RequestTreeTest(unittest.TestCase):
    """Behaviour 6 of ISSUE-95: a request at each place of ruling 4 rides the pull request and the
    lints as they are today."""

    def tree(self, ref="fox-ref", places=tuple(REQUESTS)):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        root = Path(tmp) / "show"
        shutil.copytree(ROOT / "tests" / "fixtures" / "lint_structure" / "clean", root)
        with (root / "media" / "manifest.tsv").open("a", encoding="utf-8") as manifest:
            manifest.write(MANIFEST.splitlines(keepends=True)[1])
        request = {"for": "Q1-1", "prompt": "The fox at the harbour wall, dusk.", "refs": [f"media:{ref}"]}
        for rel in places:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(json.dumps(request, indent=1) + "\n", encoding="utf-8")
        return root

    def test_requests_pass_the_lints(self):
        results = lint.run(self.tree(), only=REQUEST_LINTS)
        self.assertEqual({k: (r.status, r.findings) for k, r in results.items()},
                         {k: (lint.PASS, []) for k in REQUEST_LINTS})

    def test_each_request_is_its_makers_alone(self):
        loaded = roles.load_roles(KIT / "roles")
        for path, maker in REQUESTS.items():
            with self.subTest(path=path):
                writers = sorted(r for r, doc in loaded.items()
                                 if production.matches(path, as_list(doc["meta"].get("writes"))))
                self.assertEqual(writers, [maker])

    def test_reference_missing_from_the_manifest(self):
        result = lint.run(self.tree(ref="nope"), only=["references"])["references"]
        self.assertEqual(result.status, lint.FAIL)
        self.assertEqual(sorted({f.message for f in result.findings}),
                         ["media:nope is not in media/manifest.tsv"])
        self.assertEqual(sorted(f.path for f in result.findings), sorted(REQUESTS))

    def test_request_directly_in_shots_is_read_as_a_shot(self):
        root = self.tree(places=("episodes/ep01/sequences/Q1/shots/r2.json",))
        self.assertEqual(lint.run(root, only=["structure"])["structure"].status, lint.FAIL)


if __name__ == "__main__":
    unittest.main()
