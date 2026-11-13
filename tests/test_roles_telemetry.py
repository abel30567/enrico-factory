"""The distribution room's telemetry operator and analyst role files (brief sections 4 and 8)."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import frontmatter, markers, roles  # noqa: E402

KIT = ROOT / "kit"
ROLE_IDS = ["telemetry-puller", "theory-judge", "community-analyst"]

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
EMPTY_IS_MISSING = "An empty telemetry field is missing data, never zero."
NO_FIX = "it never says what to change"

PULLER_MAY_NOT = "run a job without a producer gate PASS on that exact job"
ANALYST_MAY_NOT = "prescribe fixes (symptoms only)"

META = {
    "telemetry-puller": {
        "role": "telemetry-puller", "room": "distribution", "kind": "operator",
        "analog": "audience data coordinator", "stage": "window", "writes": "jobs/**",
        "reads": "registry/posts.tsv, distribution/**, media/manifest.tsv", "may_not": PULLER_MAY_NOT,
        "model": "maker", "family": "any", "tools": "Read, Write, Grep, Glob, Bash", "knowledge": "",
        "secrets": "", "gated": "yes"},
    "theory-judge": {
        "role": "theory-judge", "room": "distribution", "kind": "analyst",
        "analog": "audience research lead", "stage": "window", "writes": "",
        "reads": "bible/**, episodes/**, registry/posts.tsv, distribution/**, calibration/**",
        "may_not": ANALYST_MAY_NOT, "model": "maker", "family": "any", "tools": "Read, Grep, Glob",
        "knowledge": "skills, memory-read", "secrets": "", "gated": "no", "actor": "ANALYST"},
    "community-analyst": {
        "role": "community-analyst", "room": "distribution", "kind": "analyst",
        "analog": "community manager", "stage": "", "writes": "",
        "reads": "bible/**, episodes/**, registry/posts.tsv, distribution/**",
        "may_not": ANALYST_MAY_NOT, "model": "maker", "family": "any", "tools": "Read, Grep, Glob",
        "knowledge": "skills, memory-read", "secrets": "", "gated": "no", "actor": "ANALYST"},
}
PHRASES = {
    "telemetry-puller": (
        "one job file per request, at the path the task names",
        "exactly the keys `ticket`, `tool`, `inputs`, `expected` and `caller`",
        "`caller`: `telemetry-puller`",
        EMPTY_IS_MISSING,
        "You call no tool, do not run `factory gate` or `factory run`, and hold no credential",
    ),
    "theory-judge": ("held or not held", EMPTY_IS_MISSING, NO_FIX),
    "community-analyst": ("each with a count and the beats it names", NO_FIX),
}


def sections(body):
    """[(heading, text)] for every second-level heading, in order."""
    parts = re.split(r"(?m)^## (.+)$", body)
    return list(zip(parts[1::2], parts[2::2]))


def problems(role_id, text):
    """What ruling 8 finds wrong with one role file, unprefixed. Empty is clean."""
    try:
        meta, body = frontmatter.parse(text)
    except frontmatter.FrontMatterError as error:
        return [str(error)]
    want = META[role_id]
    kind = want["kind"]
    found = []
    for key in KEYS:
        if meta.get(key) != want.get(key):
            found.append(f"{key} is {meta.get(key)!r}, expected {want.get(key)!r}")
    if set(meta) == set(want) and list(meta) != list(want):
        found.append(f"keys are in the order {list(meta)}, expected {list(want)}")

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

    named = sorted(v for v in ALL_VERBS
                   if re.search(rf"(?<![A-Za-z]){re.escape(v)}(?![A-Za-z])", part.get("Markers", "")))
    expected = sorted(markers.MAY_POST[kind])
    if named != expected:
        found.append(f"Markers names {named}, expected {expected}")
    elif not part.get("Markers", "").endswith("No other marker."):
        found.append("Markers does not end with 'No other marker.'")

    whole = " ".join(body.split())
    for phrase in ("A memory advises; a file binds.", want["may_not"]) + PHRASES[role_id]:
        if phrase not in whole:
            found.append(f"body lacks {phrase!r}")
    return found


def shipped(role_id):
    return (KIT / "roles" / f"{role_id}.md").read_text(encoding="utf-8")


def seeded(role_id, old, new):
    text = shipped(role_id)
    assert old in text, old
    return text.replace(old, new, 1)


class Shipped(unittest.TestCase):
    """Behaviour 6: the three shipped files are clean, and the kit validates."""

    def test_each_file_has_no_problems(self):
        for role_id in ROLE_IDS:
            with self.subTest(role=role_id):
                self.assertEqual(problems(role_id, shipped(role_id)), [])

    def test_kit_validates(self):
        self.assertEqual(roles.validate(KIT), [])


class Seeded(unittest.TestCase):
    """Behaviour 6: one seeded defect, one problem."""

    def test_empty_is_not_zero(self):
        text = seeded("theory-judge", "never zero.", "zero.")
        self.assertEqual(problems("theory-judge", text), [f"body lacks {EMPTY_IS_MISSING!r}"])

    def test_another_marker(self):
        text = seeded("community-analyst", "No other marker.", "Or `APPROVED`.")
        self.assertEqual(problems("community-analyst", text),
                         ["Markers names ['APPROVED', 'BLOCKED', 'SYMPTOMS'], expected ['BLOCKED', 'SYMPTOMS']"])

    def test_not_gated(self):
        text = seeded("telemetry-puller", "gated: yes", "gated: no")
        self.assertEqual(problems("telemetry-puller", text), ["gated is 'no', expected 'yes'"])

    def test_markers_without_the_closing_line(self):
        text = seeded("theory-judge", "No other marker.", "")
        self.assertEqual(problems("theory-judge", text), ["Markers does not end with 'No other marker.'"])

    def test_an_analyst_with_a_branch(self):
        text = seeded("community-analyst", "change nothing.", "change nothing on {{BRANCH}}.")
        self.assertEqual(problems("community-analyst", text), ["has placeholder {{BRANCH}}"])


if __name__ == "__main__":
    unittest.main()
