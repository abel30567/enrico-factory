import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import roles  # noqa: E402

KIT = ROOT / "kit"
FACTORY = KIT / "factory"
CLEAN = ROOT / "tests" / "fixtures" / "roles" / "clean"


def run(*args):
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True,
                          cwd=ROOT)


class KitCopy:
    """A temporary copy of the clean kit, with helpers that seed one defect."""

    def __init__(self, test):
        tmp = tempfile.mkdtemp()
        test.addCleanup(shutil.rmtree, tmp)
        self.dir = Path(tmp) / "kit"
        shutil.copytree(CLEAN, self.dir)

    def set_key(self, name, key, value):
        path = self.dir / name
        text = path.read_text()
        new, count = re.subn(rf"(?m)^{key}:.*$", f"{key}: {value}", text, count=1)
        assert count == 1, (name, key)
        path.write_text(new)

    def add_key(self, name, line):
        path = self.dir / name
        path.write_text(path.read_text().replace("---\n", f"---\n{line}\n", 1))

    def drop_key(self, name, key):
        path = self.dir / name
        new, count = re.subn(rf"(?m)^{key}:.*\n", "", path.read_text(), count=1)
        assert count == 1, (name, key)
        path.write_text(new)

    def replace(self, name, old, new):
        path = self.dir / name
        text = path.read_text()
        assert old in text, (name, old)
        path.write_text(text.replace(old, new))

    def edit_pipeline(self, change):
        path = self.dir / "pipeline.json"
        entries = json.loads(path.read_text())
        change(entries)
        path.write_text(json.dumps(entries))

    def validate(self, lint_names=None):
        return roles.validate(self.dir, lint_names)


class ValidateTest(unittest.TestCase):
    def assertOnly(self, problems, file, subject):
        """Exactly one problem, naming the file and the rule's subject."""
        self.assertEqual(len(problems), 1, problems)
        self.assertTrue(problems[0].startswith(file + ": "), problems)
        self.assertIn(subject, problems[0])

    def check(self, seed, file, subject):
        kit = KitCopy(self)
        seed(kit)
        self.assertOnly(kit.validate(), file, subject)

    def test_clean_fixture_is_valid(self):
        self.assertEqual(roles.validate(CLEAN), [])

    def test_clean_fixture_has_every_kind_two_formats_three_stages(self):
        loaded = roles.load_roles(CLEAN / "roles")
        self.assertEqual({doc["meta"]["kind"] for doc in loaded.values()}, set(roles.KINDS))
        self.assertEqual(len(roles.load_formats(CLEAN / "formats")), 2)
        self.assertEqual(len(roles.load_pipeline(CLEAN / "pipeline.json")), 3)

    def test_unparsable_file_is_reported(self):
        self.check(lambda k: k.add_key("roles/watcher.md", "no colon"), "roles/watcher.md",
                   "line 2: no colon")

    def test_rule_1_role_name_and_keys(self):
        cases = [
            (lambda k: k.set_key("roles/writer.md", "role", "author"), "role 'author'"),
            (lambda k: k.drop_key("roles/writer.md", "analog"), "missing key 'analog'"),
            (lambda k: k.add_key("roles/writer.md", "colour: blue"), "unknown key 'colour'"),
        ]
        for seed, subject in cases:
            with self.subTest(subject):
                self.check(seed, "roles/writer.md", subject)

    def test_actor_on_a_clean_kit_passes(self):
        kit = KitCopy(self)
        kit.add_key("roles/watcher.md", "actor: ANALYST")
        kit.add_key("roles/panel.md", "actor: BRAINTRUST")
        kit.add_key("roles/uploader.md", "actor: CHANNEL:TUBE")
        kit.add_key("roles/writer.md", "actor: QUILL-2")
        self.assertEqual(kit.validate(), [])

    def test_actor_malformed(self):
        for value in ("analyst", "Analyst", "2ND", "CHANNEL:tube", "CHANNEL:", "CHANNEL:-X", "A B",
                      "", "[ANALYST]"):
            with self.subTest(value):
                self.check(lambda k: k.add_key("roles/watcher.md", f"actor: {value}"),
                           "roles/watcher.md", f"actor {value!r} is not [A-Z][A-Z0-9-]*")

    def test_actor_of_a_program(self):
        for value in ("LINT", "PRODUCER", "PLATFORM-GATE", "AUTORESEARCH"):
            with self.subTest(value):
                self.check(lambda k: k.add_key("roles/watcher.md", f"actor: {value}"),
                           "roles/watcher.md", f"actor {value!r} is a program's")

    def test_actor_channel_on_a_role_that_is_not_an_operator(self):
        for file, kind in (("watcher", "analyst"), ("writer", "maker"), ("editor", "checker"),
                           ("panel", "judge"), ("chief", "orchestrator")):
            with self.subTest(kind):
                self.check(lambda k: k.add_key(f"roles/{file}.md", "actor: CHANNEL:TUBE"),
                           f"roles/{file}.md", "actor 'CHANNEL:TUBE' is a channel, allowed only for "
                                               "kind operator")

    def test_actor_defaults_to_the_role_id_in_upper_case(self):
        self.assertEqual(roles.actor({"role": "story-editor", "kind": "checker"}), "STORY-EDITOR")
        self.assertEqual(roles.actor({"role": "watcher", "actor": "ANALYST"}), "ANALYST")
        self.assertEqual(roles.actor({"role": "tube", "actor": "CHANNEL:TUBE"}), "CHANNEL:TUBE")

    def test_rule_2_room_and_kind(self):
        self.check(lambda k: k.set_key("roles/chief.md", "room", "kitchen"),
                   "roles/chief.md", "room 'kitchen'")
        kit = KitCopy(self)
        kit.set_key("roles/watcher.md", "kind", "wizard")
        self.assertOnly(kit.validate(), "roles/watcher.md", "kind 'wizard'")

    def test_rule_3_model_family_gated(self):
        cases = [
            ("roles/editor.md", "model", "large", "model 'large'"),
            ("roles/editor.md", "family", "same", "family 'same'"),
            ("roles/editor.md", "gated", "maybe", "gated 'maybe'"),
            ("roles/uploader.md", "gated", "no", "gated must be 'yes' for kind operator"),
            ("roles/writer.md", "gated", "yes", "gated must be 'no' for kind maker"),
        ]
        for file, key, value, subject in cases:
            with self.subTest(subject):
                self.check(lambda k: k.set_key(file, key, value), file, subject)

    def test_rule_4_writes_and_checked_by(self):
        cases = [
            ("roles/writer.md", "writes", "", "writes is empty for kind maker"),
            ("roles/uploader.md", "writes", "", "writes is empty for kind operator"),
            ("roles/writer.md", "checked_by", "", "checked_by is empty for kind maker"),
            ("roles/writer.md", "checked_by", "editor, ghost", "checked_by 'ghost' is not a role"),
            ("roles/writer.md", "checked_by", "watcher",
             "checked_by 'watcher' has kind analyst, not checker or judge"),
        ]
        for file, key, value, subject in cases:
            with self.subTest(subject):
                self.check(lambda k: k.set_key(file, key, value), file, subject)

    def test_rule_5_tools(self):
        self.check(lambda k: k.set_key("roles/editor.md", "tools", "Read, Telnet"),
                   "roles/editor.md", "tools 'Telnet'")

    def test_rule_6_knowledge(self):
        self.check(lambda k: k.set_key("roles/editor.md", "knowledge", "skills, telepathy"),
                   "roles/editor.md", "knowledge 'telepathy'")
        self.check(lambda k: k.set_key("roles/panel.md", "knowledge", "skills"),
                   "roles/panel.md", "knowledge must be empty for kind judge")

    def test_rule_7_placeholders(self):
        cases = [
            ("roles/chief.md", "{{RULES}}", "the rules", "missing placeholder {{RULES}}"),
            ("roles/writer.md", "{{BRANCH}}", "main", "missing placeholder {{BRANCH}}"),
            ("roles/uploader.md", "{{BRANCH}}", "main", "missing placeholder {{BRANCH}}"),
            ("roles/editor.md", "{{PR}}", "the PR", "missing placeholder {{PR}}"),
            ("roles/panel.md", "{{PR}}", "the PR", "missing placeholder {{PR}}"),
            ("roles/watcher.md", "{{TASK}}", "{{TASK}} {{SECRET}}", "placeholder {{SECRET}}"),
            ("roles/writer.md", "{{BRANCH}}", "{{BRANCH}} {{PR}}",
             "placeholder {{PR}} not allowed for kind maker"),
            ("roles/editor.md", "{{PR}}", "{{PR}} {{BRANCH}}",
             "placeholder {{BRANCH}} not allowed for kind checker"),
        ]
        for file, old, new, subject in cases:
            with self.subTest(f"{file} {subject}"):
                self.check(lambda k: k.replace(file, old, new), file, subject)

    def test_rule_8_formats(self):
        file = "formats/tiny-two-beat.md"
        cases = [
            ("format", "tiny", "format 'tiny' does not match the file name"),
            ("runtime", "15-10", "runtime '15-10'"),
            ("runtime", "ten", "runtime 'ten'"),
            ("shots", "4", "shots '4'"),
            ("beats", "", "beats is empty"),
            ("skips", "polish, nowhere", "skips 'nowhere' is not a stage"),
        ]
        for key, value, subject in cases:
            with self.subTest(subject):
                self.check(lambda k: k.set_key(file, key, value), file, subject)
        self.check(lambda k: k.drop_key(file, "shots"), file, "missing key 'shots'")

    def test_rule_9_pipeline_entries(self):
        def drop_unit(e):
            del e[0]["unit"]

        def duplicate_stage(e):
            e.append(dict(e[2]))

        def after_later(e):
            e[0]["after"] = "polish"

        def after_other_room(e):
            e[2]["after"] = "draft"

        def pitches(value):
            return lambda e: e[0].__setitem__("pitches", value)

        def paths(value):
            return lambda e: e[0].__setitem__("paths", value)

        cases = [
            (drop_unit, "story/draft", "missing key 'unit'"),
            (duplicate_stage, "distribution/publish",
             "stage 'publish' is not unique in room distribution"),
            (after_later, "story/draft", "after 'polish' is not an earlier stage"),
            (after_other_room, "distribution/publish", "after 'draft' is not an earlier stage"),
            (pitches(0), "story/draft", "pitches 0"),
            (pitches("2"), "story/draft", "pitches '2'"),
            (pitches(True), "story/draft", "pitches True"),
            (paths([]), "story/draft", "paths must be a non-empty list"),
            (paths("drafts/**"), "story/draft", "paths must be a non-empty list"),
        ]
        for change, stage, subject in cases:
            with self.subTest(subject):
                self.check(lambda k: k.edit_pipeline(change), f"pipeline.json: {stage}", subject)

    def test_rule_10_stage_roles(self):
        def setter(index, key, value):
            return lambda e: e[index].__setitem__(key, value)

        cases = [
            (setter(0, "maker", "editor"), "story/draft",
             "maker 'editor' has kind checker, not maker or operator"),
            (setter(1, "maker", ["writer", "ghost"]), "story/polish", "maker 'ghost' is not a role"),
            (setter(0, "checker", "panel"), "story/draft", "checker 'panel' has kind judge, not checker"),
            (setter(0, "judge", ["editor"]), "story/draft", "judge 'editor' has kind checker, not judge"),
            (setter(0, "judge", 7), "story/draft", "judge must be a string or a list of strings"),
        ]
        for change, stage, subject in cases:
            with self.subTest(subject):
                self.check(lambda k: k.edit_pipeline(change), f"pipeline.json: {stage}", subject)

    def test_empty_maker_is_allowed(self):
        kit = KitCopy(self)
        kit.edit_pipeline(lambda e: e[2].__setitem__("maker", ""))
        self.assertEqual(kit.validate(), [])

    def test_rule_11_lints(self):
        kit = KitCopy(self)
        (kit.dir / "lib" / "lints" / "spelling.py").unlink()
        self.assertOnly(kit.validate(), "pipeline.json: story/polish", "lint 'spelling' is not a lint")
        # An explicit list replaces the lints directory.
        self.assertEqual(KitCopy(self).validate(["wordcount", "spelling"]), [])
        self.assertOnly(KitCopy(self).validate(["wordcount"]), "pipeline.json: story/polish",
                        "lint 'spelling' is not a lint")

    def test_pipeline_values_of_any_json_type_are_problems(self):
        def setter(index, key, value):
            return lambda e: e[index].__setitem__(key, value)

        cases = [
            (setter(0, "stage", ["x"]), "entry 1: stage must be a string"),
            (setter(0, "room", {"a": 1}), "entry 1: room must be a string"),
            (setter(0, "stage", None), "entry 1: stage must be a string"),
            (setter(0, "room", 3), "entry 1: room must be a string"),
            (setter(1, "after", ["draft"]), "story/polish: after must be a string"),
            (setter(1, "after", {"a": 1}), "story/polish: after must be a string"),
            (setter(1, "after", None), "story/polish: after must be a string"),
            (setter(0, "unit", 1), "story/draft: unit must be a string"),
            (setter(0, "blind", "no"), "story/draft: blind must be true or false"),
            (setter(0, "maker", None), "story/draft: maker must be a string or a list of strings"),
            (setter(0, "maker", {"a": 1}), "story/draft: maker must be a string or a list of strings"),
            (setter(0, "checker", [1]), "story/draft: checker must be a string or a list of strings"),
            (setter(0, "judge", 2.5), "story/draft: judge must be a string or a list of strings"),
            (setter(0, "lint", None), "story/draft: lint must be a list of lint ids"),
            (setter(0, "lint", "wordcount"), "story/draft: lint must be a list of lint ids"),
            (setter(0, "lint", {"a": 1}), "story/draft: lint must be a list of lint ids"),
            (setter(1, "lint", [["wordcount"]]), "story/polish: lint must be a list of lint ids"),
            (lambda e: e.__setitem__(1, "polish"), "entry 2: must be an object, not 'polish'"),
            (lambda e: e.__setitem__(1, None), "entry 2: must be an object, not None"),
            (lambda e: e.__setitem__(1, [1]), "entry 2: must be an object, not [1]"),
        ]
        for change, subject in cases:
            with self.subTest(subject):
                kit = KitCopy(self)
                kit.edit_pipeline(change)
                self.assertIn(f"pipeline.json: {subject}", kit.validate())

    def test_pipeline_top_level_must_be_a_list(self):
        for value in ({"room": "story"}, "story", 3, None):
            with self.subTest(value):
                kit = KitCopy(self)
                (kit.dir / "pipeline.json").write_text(json.dumps(value))
                self.assertIn("pipeline.json: must be a JSON list of objects", kit.validate())

    def test_odd_front_matter_values_are_problems(self):
        cases = [
            ("roles/writer.md", "room", "[story, post]", "room '[story, post]' is not one of"),
            ("roles/writer.md", "checked_by", "{}", "checked_by '{}' is not a role"),
            ("roles/writer.md", "tools", "null", "tools 'null' is not one of"),
            ("roles/writer.md", "gated", "", "gated '' is not one of"),
            ("formats/tiny-two-beat.md", "runtime", "[10, 15]", "runtime '[10, 15]' is not min-max"),
            ("formats/tiny-two-beat.md", "shots", "", "shots '' is not min-max"),
            ("formats/tiny-two-beat.md", "skips", "{polish}", "skips '{polish}' is not a stage"),
        ]
        for file, key, value, subject in cases:
            with self.subTest(subject):
                self.check(lambda k: k.set_key(file, key, value), file, subject)

    def test_unreadable_role_and_format_files_are_problems(self):
        kit = KitCopy(self)
        (kit.dir / "roles" / "writer.md").write_bytes(b"---\nrole: \xff\n---\n")
        (kit.dir / "formats" / "odd.md").mkdir()
        problems = kit.validate()
        self.assertTrue(any(p.startswith("roles/writer.md: cannot read: ") for p in problems),
                        problems)
        self.assertIn("formats/odd.md: cannot read: Is a directory", problems)

    def test_roles_check_on_malformed_pipeline_exits_1(self):
        kit = KitCopy(self)
        kit.edit_pipeline(lambda e: e[0].__setitem__("room", {"a": 1}))
        result = run(FACTORY, "roles", "--check", "--kit", kit.dir)
        self.assertEqual(result.returncode, 1)
        self.assertIn("pipeline.json: entry 1: room must be a string\n", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_problems_are_sorted(self):
        kit = KitCopy(self)
        kit.set_key("roles/writer.md", "room", "kitchen")
        kit.set_key("roles/chief.md", "room", "attic")
        kit.set_key("formats/tiny-two-beat.md", "shots", "x")
        problems = kit.validate()
        self.assertEqual(len(problems), 3)
        self.assertEqual(problems, sorted(problems))


BRIEF_FORMATS = {
    "short-hook-payoff": ("15-20", ["hook", "payoff"], ["outline"]),
    "short-four-act": ("30-45", ["introduction", "development", "twist", "conclusion"], ["outline"]),
    "short-circle": ("45-60", ["comfort and want", "crossing and search", "finding and price",
                               "return and change"], ["outline"]),
    "serial-microdrama": ("60-90", ["pickup", "event", "value change", "open question"], ["outline"]),
    "episode-circle": ("180-660", ["you", "need", "go", "search", "find", "take", "return",
                                   "change"], []),
    "episode-twelve-beat": ("420-660", [f"beat {i}" for i in range(1, 13)], []),
    "episode-acts": ("900-1200", ["teaser", "act 1", "act 2", "act 3", "tag"], []),
}

SM = ["structure", "media"]
BRIEF_STAGES = [
    ("research", ["provenance", "references", "media"]),
    ("bible", ["provenance", "references", "canon", "media"]),
    ("premise", SM), ("beats", SM), ("outline", SM), ("script", SM), ("sequences", SM),
    ("style", ["references", "media"]),
    ("board", ["structure", "provenance", "references", "media"]),
    ("reel", SM),
]


class ShippedKitTest(unittest.TestCase):
    def test_formats_match_the_brief(self):
        formats = roles.load_formats(KIT / "formats")
        self.assertEqual(set(formats), set(BRIEF_FORMATS))
        for name, (runtime, beats, skips) in BRIEF_FORMATS.items():
            meta = formats[name]["meta"]
            with self.subTest(name):
                self.assertEqual(meta["runtime"], runtime)
                self.assertEqual(roles.as_list(meta["beats"]), beats)
                self.assertEqual(roles.as_list(meta["skips"]), skips)
                sentences = re.findall(r"[.!?](\s|$)", formats[name]["body"].split("\n\n", 1)[1])
                self.assertTrue(3 <= len(sentences) <= 6, (name, len(sentences)))

    def test_pipeline_has_the_story_stages(self):
        entries = roles.load_pipeline(KIT / "pipeline.json")
        self.assertEqual([(e["room"], e["stage"], e["lint"]) for e in entries],
                         [("story", stage, lint) for stage, lint in BRIEF_STAGES])
        by_stage = {e["stage"]: e for e in entries}
        self.assertEqual(by_stage["premise"]["pitches"], 3)
        self.assertIs(by_stage["board"]["blind"], True)
        self.assertEqual([e["stage"] for e in entries if e["blind"]], ["board"])
        self.assertEqual([e["stage"] for e in entries if e["pitches"] != 1], ["premise"])

    def test_shipped_kit_lacks_only_roles_and_lints(self):
        problems = roles.validate(KIT)
        self.assertTrue(problems)
        for problem in problems:
            self.assertRegex(problem, r"^pipeline\.json: story/[a-z]+: "
                                      r"(maker|checker|judge|lint) '[a-z-]+' is not a (role|lint)$")


class CommandLineTest(unittest.TestCase):
    def test_factory_is_executable(self):
        self.assertTrue(os.access(FACTORY, os.X_OK))

    def test_help_lists_roles(self):
        for args in (["help"], []):
            result = run(FACTORY, *args)
            self.assertEqual(result.returncode, 0)
            self.assertIn("  roles\n", result.stdout)

    def test_unknown_command_exits_2(self):
        result = run(FACTORY, "nope")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown command 'nope'", result.stderr)

    def test_new_command_is_discovered_by_file_name(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        (tmp / "kit" / "lib").mkdir(parents=True)
        shutil.copy2(FACTORY, tmp / "kit" / "factory")
        (tmp / "kit" / "lib" / "cmd_x.py").write_text(
            "def main(argv):\n    print('x ran', argv)\n    return 3\n")
        self.assertIn("  x\n", run(tmp / "kit" / "factory", "help").stdout)
        result = run(tmp / "kit" / "factory", "x", "a", "b")
        self.assertEqual((result.returncode, result.stdout), (3, "x ran ['a', 'b']\n"))

    def test_roles_check_on_clean_fixture(self):
        result = run(FACTORY, "roles", "--check", "--kit", CLEAN)
        self.assertEqual((result.returncode, result.stdout), (0, "OK\n"))

    def test_roles_check_reports_problems(self):
        kit = KitCopy(self)
        kit.set_key("roles/editor.md", "tools", "Telnet")
        result = run(FACTORY, "roles", "--check", "--kit", kit.dir)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout,
                         "roles/editor.md: tools 'Telnet' is not one of "
                         "Read, Edit, Write, Grep, Glob, Bash, WebSearch, WebFetch\n")

    def test_missing_kit_directory_exits_2(self):
        missing = Path(tempfile.mkdtemp()) / "nope"
        self.addCleanup(shutil.rmtree, missing.parent)
        for args in ([], ["--check"]):
            with self.subTest(args):
                result = run(FACTORY, "roles", *args, "--kit", missing)
                self.assertEqual((result.returncode, result.stdout), (2, ""))
                self.assertEqual(result.stderr, f"--kit {missing}: not a directory\n")

    def test_roles_lists_by_room(self):
        result = run(FACTORY, "roles", "--kit", CLEAN)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout,
                         "distribution:\n  uploader (operator)\n  watcher (analyst)\n"
                         "story:\n  chief (orchestrator)\n  editor (checker)\n  panel (judge)\n"
                         "  writer (maker)\n")

    def test_command_runs_as_a_script(self):
        result = run(KIT / "lib" / "cmd_roles.py", "--check", "--kit", CLEAN)
        self.assertEqual((result.returncode, result.stdout), (0, "OK\n"))

    def test_output_is_deterministic(self):
        first = run(FACTORY, "roles", "--check")
        second = run(FACTORY, "roles", "--check")
        self.assertEqual(first.returncode, 1)
        self.assertEqual(first.stdout, second.stdout)


if __name__ == "__main__":
    unittest.main()
