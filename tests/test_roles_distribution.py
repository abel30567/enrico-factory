"""The distribution room's orchestrator and channel operator role files (brief sections 3, 4 and 8)."""

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import frontmatter, markers, production, roles  # noqa: E402
from kit.lib.frontmatter import as_list  # noqa: E402

KIT = ROOT / "kit"
FACTORY = KIT / "factory"

DESK = "channel-desk"
PLATFORMS = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram"}
OPERATORS = [f"channel-{p}" for p in PLATFORMS]
ROLE_IDS = [DESK] + OPERATORS

HEADINGS = ["Read first", "Task", "What you produce", "You may not", "Markers", "Result",
            "Provenance", "Blocked", "Rules"]
AUTHORITY = ("the rules of power, the charter, the production rules, the canon ledger, the episode "
             "ledger, the role file, the task.")
ALL_VERBS = sorted(set(markers.ROLE_VERBS) | set(markers.CHANNEL_VERBS)
                   | {v for verbs in markers.PROGRAM_VERBS.values() for v in verbs}
                   | {v for verbs in markers.EXTRA_VERBS.values() for v in verbs})
FORBIDDEN = re.compile(r"(claude|opus|sonnet|gpt|--model|sk-|/Users/)")
MAX_LINES = 120
KEYS = roles.ROLE_REQUIRED + roles.ROLE_OPTIONAL

DESK_MAY_NOT = ("edit another room's work; lock in another room; make a post public outside its "
                "gated job")
OPERATOR_MAY_NOT = ("upload a file other than the gated variant; make a post public outside its "
                    "gated job")
DESK_PHRASES = (
    "**Ruling after three rounds.**", "**A blocked worker.**",
    "A Blocker that names a third party's character or work is never ruled away.",
    "A post goes public only through its own gated job, after the platform has finished processing "
    "it and the publish check has passed.",
)
OPERATOR_PHRASES = (
    "one job file per request, at `jobs/` followed by the request's path: a JSON object with exactly "
    "the keys `ticket`, `tool`, `inputs`, `expected` and `caller`",
    "`inputs`: first the request as `{path, sha256}`, then the variant it names as `{path, sha256}`",
    "`caller`: `channel-<p>`",
    "Release has three steps: a private upload, a check that <Name> finished processing the post and "
    "kept its captions and disclosure, then the public switch, which is its own gated job.",
    "The gate checks your job and a program makes the call: you never reach <Name> yourself.",
    "Do not repair a request to avoid blocking.",
    "`distribution/<ep>/<p>/requests/upload.json`",
    "`distribution/<ep>/<p>/requests/public.json`",
)
PLATFORM_PHRASES = {
    "youtube": ("The upload job sets the disclosure field `containsSyntheticMedia`.",),
    "tiktok": ("The upload job sets the disclosure field `is_aigc`. `TIKTOK_POSTING` in "
               "`production.env` is `inbox`, `private` or `direct`: say which in `summary`. In "
               "`inbox` and `private` the owner makes the post public, and no public switch is asked "
               "for.",),
    "instagram": ("The upload job sets the disclosure field `is_ai_generated`. Instagram has no "
                  "private upload: the task names the account the upload goes to.",),
}


def expected(role_id):
    """{"meta", "markers", "phrases"}: the front matter (in key order), the marker verbs its Markers
    section names, and the phrases its body holds word for word."""
    if role_id == DESK:
        meta = {"role": DESK, "room": "distribution", "kind": "orchestrator",
                "analog": "head of distribution", "stage": "post, window",
                "reads": "post/**, distribution/**, rulebook/**, episodes/*/intent-packet.json, "
                         "media/manifest.tsv",
                "may_not": DESK_MAY_NOT, "model": "maker", "family": "any",
                "tools": "Read, Grep, Glob", "knowledge": "skills, memory-read, search",
                "secrets": "", "gated": "no"}
        verbs = ["BLOCKED", "LAUNCH", "RULING", "SELECTED"]
        phrases = DESK_PHRASES + (DESK_MAY_NOT,)
        return {"meta": meta, "markers": verbs, "phrases": list(phrases)}
    p = role_id[len("channel-"):]
    name = PLATFORMS[p]
    meta = {"role": role_id, "room": "distribution", "kind": "operator",
            "analog": f"{name} channel manager", "stage": "post",
            "writes": f"jobs/distribution/*/{p}/requests/*.json",
            "reads": "post/**, distribution/**, rulebook/**, media/manifest.tsv",
            "actor": f"CHANNEL:{p.upper()}", "may_not": OPERATOR_MAY_NOT, "model": "maker",
            "family": "any", "tools": "Read, Write, Grep, Glob, Bash", "knowledge": "",
            "secrets": "", "gated": "yes"}
    verbs = ["ADDRESSED", "BLOCKED", "POSTED", "READY", "VERIFIED"]
    phrases = [s.replace("<p>", p).replace("<Name>", name) for s in OPERATOR_PHRASES]
    phrases += list(PLATFORM_PHRASES[p]) + [OPERATOR_MAY_NOT]
    return {"meta": meta, "markers": verbs, "phrases": phrases}


def sections(body):
    """[(heading, text)] for every second-level heading, in order."""
    parts = re.split(r"(?m)^## (.+)$", body)
    return list(zip(parts[1::2], parts[2::2]))


def job_path(p):
    return f"jobs/distribution/ep01/{p}/requests/upload.json"


def own_problems(role_id, text):
    """What this ticket's rules find wrong with one role file, unprefixed. Empty is clean."""
    try:
        meta, body = frontmatter.parse(text)
    except frontmatter.FrontMatterError as error:
        return [str(error)]
    want = expected(role_id)
    kind = want["meta"]["kind"]
    found = []
    for key in KEYS:
        if meta.get(key) != want["meta"].get(key):
            found.append(f"{key} is {meta.get(key)!r}, expected {want['meta'].get(key)!r}")
    writes = as_list(meta.get("writes"))
    for p in PLATFORMS:
        reaches = production.matches(job_path(p), writes)
        if reaches and role_id != f"channel-{p}":
            found.append(f"writes reaches {job_path(p)}")
        if not reaches and role_id == f"channel-{p}":
            found.append(f"writes misses {job_path(p)}")

    lines = len(text.splitlines())
    if lines > MAX_LINES:
        found.append(f"{lines} lines, more than {MAX_LINES}")
    if FORBIDDEN.search(text):
        found.append(f"names a model, flag or path: {FORBIDDEN.search(text).group(0)!r}")

    placeholders = set(re.findall(r"\{\{([^{}]*)\}\}", body))
    for name in roles.PLACEHOLDERS:
        if name not in placeholders:
            found.append(f"missing placeholder {{{{{name}}}}}")
    if kind == "operator" and "BRANCH" not in placeholders:
        found.append("missing placeholder {{BRANCH}}")
    if kind != "operator" and "BRANCH" in placeholders:
        found.append("has placeholder {{BRANCH}}")
    if "PR" in placeholders:
        found.append("has placeholder {{PR}}")

    parts = sections(body)
    if [h for h, _ in parts] != HEADINGS:
        found.append(f"headings {[h for h, _ in parts]} are not {HEADINGS}")
    part = {heading: " ".join(text.split()) for heading, text in parts}
    if not part.get("Read first", "").endswith(AUTHORITY):
        found.append("Read first does not end with the order of authority")
    task = ("TASK", "TICKET", "REPO", "WORKTREE") + (("BRANCH",) if kind == "operator" else ())
    for name in task:
        if f"{{{{{name}}}}}" not in part.get("Task", ""):
            found.append(f"Task lacks {{{{{name}}}}}")
    if part.get("Rules", "") != "{{RULES}}":
        found.append("Rules is not {{RULES}}")
    if not part.get("You may not", "").startswith("- " + want["meta"]["may_not"]):
        found.append(f"You may not lacks {want['meta']['may_not']!r}")

    named = sorted(v for v in ALL_VERBS
                   if re.search(rf"(?<![A-Za-z]){re.escape(v)}(?![A-Za-z])", part.get("Markers", "")))
    if named != want["markers"]:
        found.append(f"Markers names {named}, expected {want['markers']}")

    fields = ["`marker`", "`summary`", "`evidence`", "`blocked`"]
    fields += ["`addressed`"] if kind == "operator" else []
    for field in fields:
        if field not in part.get("Result", ""):
            found.append(f"Result lacks {field}")
    if "A memory advises; a file binds." not in part.get("Provenance", ""):
        found.append("Provenance lacks 'A memory advises; a file binds.'")
    for field in ("`missing`", "`tried`", "`resume_from`"):
        if field not in part.get("Blocked", ""):
            found.append(f"Blocked lacks {field}")

    whole = " ".join(body.split())
    for phrase in want["phrases"]:
        if phrase not in whole:
            found.append(f"body lacks {phrase!r}")
    return found


def problems(kit_dir):
    """Every finding on the distribution role files under kit_dir, sorted. Empty is clean."""
    directory = Path(kit_dir) / "roles"
    found = []
    for role_id in ROLE_IDS:
        path = directory / f"{role_id}.md"
        if not path.is_file():
            found.append(f"{role_id}: no file roles/{role_id}.md")
            continue
        found += [f"{role_id}: {p}" for p in own_problems(role_id, path.read_text(encoding="utf-8"))]
    desks = []
    for path in sorted(directory.glob("*.md")):
        try:
            meta, _ = frontmatter.parse(path.read_text(encoding="utf-8"))
        except frontmatter.FrontMatterError:
            continue
        if meta.get("room") == "distribution" and meta.get("kind") == "orchestrator":
            desks.append(path.stem)
    if desks != [DESK]:
        found.append(f"room distribution has the orchestrators {desks}, not {[DESK]}")
    return sorted(found)


def without(text, phrase):
    """text with every occurrence of phrase removed, across any line break between its words."""
    pattern = r"\s+".join(re.escape(word) for word in phrase.split())
    return re.sub(pattern, "", text)


def python(*args, cwd=ROOT):
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True, cwd=cwd)


class KitCopy:
    """A temporary copy of the shipped kit, to seed one defect in."""

    def __init__(self, test):
        tmp = tempfile.mkdtemp()
        test.addCleanup(shutil.rmtree, tmp)
        self.dir = Path(tmp) / "kit"
        shutil.copytree(KIT, self.dir, ignore=shutil.ignore_patterns("__pycache__"))

    def path(self, role_id):
        return self.dir / "roles" / f"{role_id}.md"

    def text(self, role_id):
        return self.path(role_id).read_text(encoding="utf-8")

    def edit(self, role_id, old, new):
        text = self.text(role_id)
        assert old in text, old
        self.path(role_id).write_text(text.replace(old, new, 1), encoding="utf-8")


class ShippedTest(unittest.TestCase):
    def test_shipped_kit_has_no_problems(self):
        self.assertEqual(problems(KIT), [])

    def test_kit_without_the_files_has_five_findings(self):
        kit = KitCopy(self)
        for role_id in ROLE_IDS:
            kit.path(role_id).unlink()
        self.assertEqual(problems(kit.dir), sorted(
            [f"{r}: no file roles/{r}.md" for r in ROLE_IDS]
            + ["room distribution has the orchestrators [], not ['channel-desk']"]))

    def test_factory_roles_checks_and_lists_the_room(self):
        check = python(FACTORY, "roles", "--check")
        self.assertEqual((check.returncode, check.stdout.strip()), (0, "OK"))
        listing = python(FACTORY, "roles")
        self.assertEqual(listing.returncode, 0)
        lines = listing.stdout.splitlines()
        self.assertIn("distribution:", lines)
        start = lines.index("distribution:")
        block = lines[start + 1:]
        block = block[:next((i for i, line in enumerate(block) if not line.startswith("  ")), len(block))]
        channel = ["  channel-desk (orchestrator)", "  channel-instagram (operator)",
                   "  channel-tiktok (operator)", "  channel-youtube (operator)"]
        self.assertEqual([line for line in block if line in channel], channel)

    def test_operators_may_post_and_parse_their_channel_verbs(self):
        loaded = roles.load_roles(KIT / "roles")
        for role_id in OPERATORS:
            actor = roles.actor(loaded[role_id]["meta"])
            self.assertEqual(actor, "CHANNEL:" + role_id[len("channel-"):].upper())
            for verb in ("POSTED", "VERIFIED"):
                with self.subTest(role=role_id, verb=verb):
                    self.assertTrue(markers.may_post("operator", verb, actor))
                    marker = markers.parse(f"[{actor}] {verb}")
                    self.assertEqual((marker.actor, marker.verb), (actor, verb))

    def test_init_installs_the_room_only_when_asked(self):
        for rooms, wanted in ((None, []), ("story,distribution", sorted(f"{r}.md" for r in ROLE_IDS))):
            with self.subTest(rooms=rooms):
                target = Path(tempfile.mkdtemp())
                self.addCleanup(shutil.rmtree, target)
                args = [FACTORY, "init", target] + (["--rooms", rooms] if rooms else [])
                result = python(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                installed = sorted(p.name for p in (target / ".factory" / "roles").glob("*.md")
                                   if p.name.startswith("channel-"))
                self.assertEqual(installed, wanted)


class SeededDefectTest(unittest.TestCase):
    def test_operator_without_actor(self):
        kit = KitCopy(self)
        kit.edit("channel-youtube", "actor: CHANNEL:YOUTUBE\n", "")
        self.assertEqual(problems(kit.dir),
                         ["channel-youtube: actor is None, expected 'CHANNEL:YOUTUBE'"])

    def test_operator_writing_every_job(self):
        kit = KitCopy(self)
        kit.edit("channel-youtube", "writes: jobs/distribution/*/youtube/requests/*.json",
                 "writes: jobs/**")
        self.assertEqual(problems(kit.dir), [
            "channel-youtube: writes is 'jobs/**', expected 'jobs/distribution/*/youtube/requests/*.json'",
            "channel-youtube: writes reaches jobs/distribution/ep01/instagram/requests/upload.json",
            "channel-youtube: writes reaches jobs/distribution/ep01/tiktok/requests/upload.json"])

    def test_operator_with_a_renamed_marker(self):
        kit = KitCopy(self)
        kit.edit("channel-tiktok", "- `POSTED`: ", "- `UPLOADED`: ")
        self.assertEqual(problems(kit.dir), [
            "channel-tiktok: Markers names ['ADDRESSED', 'BLOCKED', 'READY', 'VERIFIED'], "
            "expected ['ADDRESSED', 'BLOCKED', 'POSTED', 'READY', 'VERIFIED']"])

    def test_roles_validate_refuses_an_ungated_operator_and_a_channel_desk(self):
        kit = KitCopy(self)
        kit.edit("channel-instagram", "gated: yes", "gated: no")
        self.assertIn("roles/channel-instagram.md: gated must be 'yes' for kind operator",
                      roles.validate(kit.dir))
        kit = KitCopy(self)
        kit.edit(DESK, "gated: no\n", "gated: no\nactor: CHANNEL:DESK\n")
        self.assertIn("roles/channel-desk.md: actor 'CHANNEL:DESK' is a channel, allowed only for "
                      "kind operator", roles.validate(kit.dir))

    def test_a_second_orchestrator(self):
        kit = KitCopy(self)
        shutil.copy(kit.path(DESK), kit.path("release-desk"))
        self.assertEqual(problems(kit.dir), [
            "room distribution has the orchestrators ['channel-desk', 'release-desk'], "
            "not ['channel-desk']"])

    def test_a_file_over_120_lines(self):
        kit = KitCopy(self)
        kit.edit(DESK, "{{RULES}}\n", "{{RULES}}\n" + "\n" * 120)
        found = problems(kit.dir)
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].startswith("channel-desk: ") and found[0].endswith("more than 120"))

    def test_each_phrase_removed(self):
        for role_id in ROLE_IDS:
            for phrase in expected(role_id)["phrases"]:
                with self.subTest(role=role_id, phrase=phrase):
                    kit = KitCopy(self)
                    meta_end = kit.text(role_id).index("\n---\n", 4) + 5
                    text = kit.text(role_id)
                    body = without(text[meta_end:], phrase)
                    self.assertNotEqual(body, text[meta_end:])
                    kit.path(role_id).write_text(text[:meta_end] + body, encoding="utf-8")
                    found = problems(kit.dir)
                    self.assertIn(f"{role_id}: body lacks {phrase!r}", found)
                    self.assertEqual([f for f in found if not f.startswith(f"{role_id}: ")], [])


if __name__ == "__main__":
    unittest.main()
