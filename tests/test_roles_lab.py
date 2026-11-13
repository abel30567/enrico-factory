"""The lab's orchestrator and maker role files (brief sections 3, 4 and 13)."""

import fnmatch
import re
import subprocess
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_roles_making import (ALL_VERBS, AUTHORITY, FORBIDDEN, HEADINGS, MAKER_RULES,  # noqa: E402
                               MAX_LINES, OFF_LIMITS, KitCopy, anchor, sections)

from kit.lib import frontmatter, markers, production, roles  # noqa: E402
from kit.lib.frontmatter import as_list  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "kit"
FACTORY = KIT / "factory"
LAB_ENTRIES = [e for e in roles.load_pipeline(KIT / "pipeline.json") if e["room"] == "lab"]

MAKER_TOOLS = "Read, Edit, Write, Grep, Glob, Bash"
# role id: (kind, analog, writes, may_not, tools, knowledge). The table of ruling 2 of #172.
LAB = {
    "lab-director": ("orchestrator", "research lab director", None,
                     "change a room directly; lock in another room", "Read, Grep, Glob",
                     "skills, memory-read, skills-write"),
    "hypothesis-writer": ("maker", "research hypothesis writer", "lab/hypotheses/**",
                          "change a hypothesis after its test has started", MAKER_TOOLS,
                          "skills, memory-read"),
    "experimenter": ("maker", "research engineer", "lab/experiments/**",
                     "change the metric, the harness of the experiment, the tests, the judges or "
                     "the keep threshold", MAKER_TOOLS, "skills, memory-read"),
    "metric-proposer": ("maker", "metrics researcher", "lab/metric-amendments/**",
                        "change the metric of a running loop", MAKER_TOOLS, "skills, memory-read"),
    "method-researcher": ("maker", "methods researcher", "lab/methods/**",
                          "skip the source and evidence fields",
                          MAKER_TOOLS + ", WebSearch, WebFetch", "skills, memory-read, search"),
}
READS = "lab/**, playbook/**, calibration/**, episodes/**, .factory/**"
# No write glob of a lab role reaches a log a program appends or a suite (ruling 4 of #172).
LAB_OFF_LIMITS = OFF_LIMITS + ("lab/results.tsv", ".factory/evals/x", "kit/evals/x")
BRANCHED = ("maker", "operator")

FROZEN = ("Frozen for the experimenter: the metric, the harness of the experiment, the tests, the "
          "judges and the keep threshold.")
SHARED = (FROZEN, "The lab never changes a room directly.", "`lab/results.tsv` is appended by a program")
# What one role's body must say besides the shared sentences (rulings 5 and 6 of #172).
SPECIFIC = {
    "lab-director": ("Ruling after three rounds", "Hypothesis selection", "A blocked worker",
                     "The verdicts `KEEP`, `NOISE`, `DISCARD` and `REBASELINE` are posted by a "
                     "program as `[AUTORESEARCH]`; you never return one.", "`SELECTED #<pr>`"),
    "hypothesis-writer": ("written before any run",),
    "experimenter": ("one change", "the exact new text or setting"),
    "metric-proposer": ("A loop never changes its own metric.",
                        "predicts real outcomes better on held-out episodes"),
    "method-researcher": ("with its source and an evidence table",),
}


def expected_meta(role_id):
    kind, analog, writes, may_not, tools, knowledge = LAB[role_id]
    meta = {"role": role_id, "room": "lab", "kind": kind, "analog": analog, "stage": ""}
    if writes is not None:
        meta["writes"] = writes
    meta["reads"] = READS
    if kind == "maker":
        meta["checked_by"] = "held-out-judge"
    meta.update(may_not=may_not, model="maker", family="any", tools=tools, knowledge=knowledge,
                secrets="", gated="no")
    return meta


def lab_problems(role_id, text):
    """What the rules of #172 find wrong with one lab role file. Empty is clean."""
    kind = LAB[role_id][0]
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
    for glob in as_list(meta.get("writes")):
        for path in LAB_OFF_LIMITS:
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
    for field in fields:
        if field not in result:
            problems.append(f"Result lacks {field}")
    if "A memory advises; a file binds." not in part.get("Provenance", ""):
        problems.append("Provenance lacks 'A memory advises; a file binds.'")
    for field in ("`missing`", "`tried`", "`resume_from`"):
        if field not in part.get("Blocked", ""):
            problems.append(f"Blocked lacks {field}")

    whole = " ".join(body.split())
    produce = part.get("What you produce", "")
    phrases = SHARED + SPECIFIC[role_id] + (MAKER_RULES if kind == "maker" else ())
    for phrase in phrases:
        if phrase not in whole:
            problems.append(f"body lacks {phrase!r}")
    if kind == "maker":
        for glob in as_list(expected["writes"]):
            if anchor(glob) not in produce:
                problems.append(f"What you produce does not name {anchor(glob)!r}")
        for lint in sorted({l for e in LAB_ENTRIES if role_id in as_list(e["maker"])
                            for l in e["lint"]}):
            if f"`{lint}`" not in produce:
                problems.append(f"What you produce does not name lint {lint!r}")
    return problems


def lab(kit, role_id):
    return lab_problems(role_id, kit.path(role_id).read_text(encoding="utf-8"))


def room_orchestrator(kit_dir, room):
    return production.orchestrator(
        types.SimpleNamespace(roles=roles.load_roles(Path(kit_dir) / "roles")), room)


class ShippedLabFilesTest(unittest.TestCase):
    def test_each_lab_file_meets_the_ticket(self):
        for role_id in LAB:
            with self.subTest(role=role_id):
                text = (KIT / "roles" / f"{role_id}.md").read_text(encoding="utf-8")
                self.assertEqual(lab_problems(role_id, text), [])

    def test_kit_validates_and_the_lab_has_one_orchestrator(self):
        self.assertEqual(roles.validate(KIT), [])
        loaded = roles.load_roles(KIT / "roles")
        self.assertEqual(sorted(r for r, d in loaded.items() if d["meta"]["room"] == "lab"),
                         sorted(LAB))
        self.assertEqual(room_orchestrator(KIT, "lab"), "lab-director")

    def test_factory_lists_the_lab_room(self):
        result = subprocess.run([sys.executable, str(FACTORY), "roles"], capture_output=True,
                                text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0)
        lines = result.stdout.splitlines()
        start = lines.index("lab:")
        self.assertEqual(lines[start:start + 6], [
            "lab:", "  experimenter (maker)", "  hypothesis-writer (maker)",
            "  lab-director (orchestrator)", "  method-researcher (maker)",
            "  metric-proposer (maker)"])


class SeededLabDefectTest(unittest.TestCase):
    def test_frozen_sentence_missing(self):
        kit = KitCopy(self)
        kit.edit("experimenter", "Frozen for the experimenter:", "Fixed for the experimenter:")
        self.assertEqual(lab(kit, "experimenter"), [f"body lacks {FROZEN!r}"])

    def test_writes_reaching_the_results_log_or_a_suite(self):
        kit = KitCopy(self)
        kit.edit("experimenter", "writes: lab/experiments/**", "writes: lab/**")
        self.assertIn("writes 'lab/**' reaches lab/results.tsv", lab(kit, "experimenter"))
        kit.edit("metric-proposer", "writes: lab/metric-amendments/**",
                 "writes: lab/metric-amendments/**, .factory/**")
        self.assertIn("writes '.factory/**' reaches .factory/evals/x", lab(kit, "metric-proposer"))

    def test_director_names_a_program_verdict(self):
        kit = KitCopy(self)
        kit.edit("lab-director", "- `BLOCKED`: you cannot decide",
                 "- `KEEP`: the run won.\n- `BLOCKED`: you cannot decide")
        self.assertEqual(lab(kit, "lab-director"), [
            "Markers names ['BLOCKED', 'KEEP', 'LAUNCH', 'RULING', 'SELECTED'], "
            "expected ['BLOCKED', 'LAUNCH', 'RULING', 'SELECTED']"])

    def test_director_with_a_program_actor(self):
        kit = KitCopy(self)
        kit.edit("lab-director", "gated: no\n", "gated: no\nactor: AUTORESEARCH\n")
        self.assertIn("roles/lab-director.md: actor 'AUTORESEARCH' is a program's, not a role's",
                      roles.validate(kit.dir))

    def test_maker_without_checked_by(self):
        kit = KitCopy(self)
        kit.edit("hypothesis-writer", "checked_by: held-out-judge\n", "")
        self.assertIn("roles/hypothesis-writer.md: checked_by is empty for kind maker",
                      roles.validate(kit.dir))
        self.assertIn("missing key 'checked_by'", lab(kit, "hypothesis-writer"))

    def test_may_not_changed(self):
        kit = KitCopy(self)
        kit.edit("metric-proposer", "may_not: change the metric of a running loop",
                 "may_not: change a metric")
        self.assertIn("may_not is 'change a metric', expected 'change the metric of a running loop'",
                      lab(kit, "metric-proposer"))

    def test_two_lab_orchestrators(self):
        kit = KitCopy(self)
        text = kit.path("lab-director").read_text(encoding="utf-8")
        kit.path("lab-chief").write_text(text.replace("role: lab-director", "role: lab-chief", 1),
                                         encoding="utf-8")
        with self.assertRaises(production.ProductionError) as caught:
            room_orchestrator(kit.dir, "lab")
        self.assertEqual(str(caught.exception), "room lab has 2 orchestrator role(s), not 1")

    def test_too_long(self):
        kit = KitCopy(self)
        kit.edit("method-researcher", "{{RULES}}", "{{RULES}}" + "\n" * 120)
        problems = lab(kit, "method-researcher")
        self.assertTrue(any(p.endswith("more than 120") for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
