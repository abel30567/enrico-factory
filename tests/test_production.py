import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import production  # noqa: E402
from kit.lib.production import ProductionError  # noqa: E402

CLEAN = ROOT / "tests" / "fixtures" / "production" / "clean"
SEED = "SEEDED-VALUE-7Q4Z"


def copy(test):
    """A temporary copy of the clean production; returns its root."""
    tmp = tempfile.mkdtemp()
    test.addCleanup(shutil.rmtree, tmp)
    root = Path(tmp) / "show"
    shutil.copytree(CLEAN, root)
    return root


def set_setting(root, name, value):
    path = root / "production.env"
    text = path.read_text()
    new, count = re.subn(rf"(?m)^{name}=.*$", lambda _: f"{name}={value}", text)
    if not count:
        new = text + f"{name}={value}\n"
    path.write_text(new)


class CleanTest(unittest.TestCase):
    def setUp(self):
        self.p = production.load(CLEAN)

    def test_find_root_from_inside(self):
        self.assertEqual(production.find_root(CLEAN / ".factory" / "roles"), CLEAN.resolve())
        self.assertEqual(production.find_root(CLEAN), CLEAN.resolve())

    def test_every_field(self):
        p = self.p
        self.assertEqual(p.root, CLEAN.resolve())
        self.assertEqual(p.kit, CLEAN.resolve() / ".factory")
        self.assertEqual(p.settings, {
            "REPO": "example/demo-show", "BASE": "trunk", "ROOMS": "",
            "STATE_DIR": "~/.factory-state/demo-show", "CAP_EPISODE_USD": "12.5",
            "CAP_SEASON_USD": "200", "CAP_EPISODE_COMPUTE_MIN": "", "CAP_TICKET_ITERATIONS": "3"})
        self.assertEqual(sorted(p.roles), ["chief", "editor", "writer"])
        self.assertEqual(p.roles["writer"]["file"], "roles/writer.md")
        self.assertEqual(p.roles["writer"]["meta"]["writes"], "drafts/**")
        self.assertEqual(sorted(p.formats), ["tiny-two-beat"])
        self.assertEqual(p.formats["tiny-two-beat"]["meta"]["beats"], "setup, sting")
        with open(CLEAN / ".factory" / "pipeline.json") as handle:
            self.assertEqual(p.pipeline, json.load(handle))
        self.assertEqual([e["stage"] for e in p.pipeline], ["draft", "polish"])
        self.assertEqual(p.kinds, {"chief": "orchestrator", "editor": "checker", "writer": "maker"})
        self.assertEqual(p.actors, {"chief": "CHIEF", "editor": "EDITOR", "writer": "SCRIBE"})

    def test_frozen(self):
        with self.assertRaises(Exception):
            self.p.root = Path("/")

    def test_setting(self):
        self.assertEqual(production.setting(self.p, "REPO"), "example/demo-show")
        self.assertEqual(production.setting(self.p, "ROOMS", "x"), "x")
        self.assertEqual(production.setting(self.p, "ABSENT", "d"), "d")
        with self.assertRaisesRegex(ProductionError, r"^setting ABSENT is empty$"):
            production.setting(self.p, "ABSENT")
        with self.assertRaisesRegex(ProductionError, r"^setting ROOMS is empty$"):
            production.setting(self.p, "ROOMS")

    def test_base(self):
        self.assertEqual(production.base(self.p), "trunk")
        root = copy(self)
        set_setting(root, "BASE", "")
        self.assertEqual(production.base(production.load(root)), "main")

    def test_rooms(self):
        self.assertEqual(production.rooms(self.p), ["story"])
        root = copy(self)
        set_setting(root, "ROOMS", " story , lab ,")
        self.assertEqual(production.rooms(production.load(root)), ["lab", "story"])

    def test_state_dir(self):
        self.assertEqual(production.state_dir(self.p),
                         Path(os.path.expanduser("~/.factory-state/demo-show")))
        self.assertFalse(production.state_dir(self.p).is_relative_to(CLEAN.resolve()))

    def test_stages(self):
        self.assertEqual([e["stage"] for e in production.stages(self.p, "story")], ["draft", "polish"])
        self.assertEqual(production.stages(self.p, "lab"), [])
        self.assertEqual(production.stage(self.p, "story", "polish"), self.p.pipeline[1])
        self.assertIsNone(production.stage(self.p, "story", "reel"))
        self.assertIsNone(production.stage(self.p, "lab", "draft"))

    def test_stage_roles(self):
        draft, polish = self.p.pipeline
        self.assertEqual(production.stage_roles(draft, "maker"), ["writer"])
        self.assertEqual(production.stage_roles(draft, "checker"), ["editor"])
        self.assertEqual(production.stage_roles(draft, "judge"), [])
        self.assertEqual(production.stage_roles(polish, "maker"), ["writer"])
        self.assertEqual(production.stage_roles(polish, "judge"), [])
        self.assertEqual(production.stage_roles({"judge": None}, "judge"), [])
        self.assertEqual(production.stage_roles({"judge": ["a", "b"]}, "judge"), ["a", "b"])

    def test_orchestrator(self):
        self.assertEqual(production.orchestrator(self.p, "story"), "chief")

    def test_caps(self):
        self.assertEqual(production.caps(self.p), {
            "CAP_EPISODE_USD": 12.5, "CAP_SEASON_USD": 200.0, "CAP_EPISODE_COMPUTE_MIN": None,
            "CAP_SEASON_COMPUTE_MIN": None, "CAP_TICKET_ITERATIONS": 3.0,
            "CAP_EPISODE_HOURS": None, "CAP_SEASON_HOURS": None})
        self.assertEqual(list(production.caps(self.p)), list(production.CAPS))

    def test_rules_text(self):
        power = (CLEAN / ".factory" / "rules-of-power.md").read_text()
        rules = (CLEAN / "rules" / "production-rules.md").read_text()
        text = production.rules_text(self.p)
        self.assertEqual(text, power.rstrip("\n") + "\n\n" + rules)
        self.assertIn("\n7. **The producer's caps always win.**", text)
        self.assertTrue(text.endswith("1. Every draft names the note it answers.\n"))

    def test_rules_text_without_production_rules(self):
        root = copy(self)
        (root / "rules" / "production-rules.md").unlink()
        text = production.rules_text(production.load(root))
        self.assertEqual(text, (root / ".factory" / "rules-of-power.md").read_text())

    def test_now(self):
        self.assertRegex(production.now({}), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(production.now({"FACTORY_NOW": "2026-11-11T10:20:30Z"}),
                         "2026-11-11T10:20:30Z")

    def test_now_reads_os_environ_by_default(self):
        old = os.environ.get("FACTORY_NOW")
        os.environ["FACTORY_NOW"] = "2030-01-02T03:04:05Z"
        try:
            self.assertEqual(production.now(), "2030-01-02T03:04:05Z")
        finally:
            if old is None:
                del os.environ["FACTORY_NOW"]
            else:
                os.environ["FACTORY_NOW"] = old


class RefusalTest(unittest.TestCase):
    def setUp(self):
        self.root = copy(self)

    def load(self):
        return production.load(self.root)

    def test_no_production_env_above_start(self):
        (self.root / "production.env").unlink()
        with self.assertRaisesRegex(ProductionError,
                                    r"^no production.env at or above the start directory$"):
            production.find_root(self.root / ".factory" / "roles")

    def test_no_kit(self):
        shutil.rmtree(self.root / ".factory")
        with self.assertRaisesRegex(
                ProductionError, r"^no kit in the tree: neither \.factory/ nor kit/pipeline\.json$"):
            self.load()

    def test_kit_does_not_validate(self):
        path = self.root / ".factory" / "roles" / "writer.md"
        path.write_text(path.read_text().replace("gated: no\n", ""))
        with self.assertRaisesRegex(
                ProductionError, r"^kit: 1 problem\(s\); first: roles/writer\.md: missing key 'gated'$"):
            self.load()

    def assert_state_dir_refused(self, value, message):
        set_setting(self.root, "STATE_DIR", value)
        p = self.load()
        with self.assertRaisesRegex(ProductionError, message):
            production.state_dir(p)

    def test_state_dir_empty(self):
        self.assert_state_dir_refused("", r"^setting STATE_DIR is empty$")

    def test_state_dir_relative(self):
        self.assert_state_dir_refused(
            "state/here", r"^setting STATE_DIR must be an absolute path outside the repository$")

    def test_state_dir_inside_root(self):
        self.assert_state_dir_refused(
            str(self.root / "state"),
            r"^setting STATE_DIR must be an absolute path outside the repository$")

    def test_state_dir_is_root(self):
        self.assert_state_dir_refused(
            str(self.root / "rules" / ".."),
            r"^setting STATE_DIR must be an absolute path outside the repository$")

    def test_cap_not_a_number(self):
        set_setting(self.root, "CAP_SEASON_HOURS", "ten")
        with self.assertRaisesRegex(ProductionError,
                                    r"^setting CAP_SEASON_HOURS is not a number >= 0$"):
            production.caps(self.load())

    def test_cap_negative(self):
        set_setting(self.root, "CAP_EPISODE_USD", "-1")
        with self.assertRaisesRegex(ProductionError, r"^setting CAP_EPISODE_USD is not a number >= 0$"):
            production.caps(self.load())

    def test_room_without_orchestrator(self):
        (self.root / ".factory" / "roles" / "chief.md").unlink()
        with self.assertRaisesRegex(ProductionError, r"^room story has 0 orchestrator role\(s\), not 1$"):
            production.orchestrator(self.load(), "story")

    def test_room_with_two_orchestrators(self):
        roles_dir = self.root / ".factory" / "roles"
        text = (roles_dir / "chief.md").read_text().replace("role: chief", "role: deputy")
        (roles_dir / "deputy.md").write_text(text)
        with self.assertRaisesRegex(ProductionError, r"^room story has 2 orchestrator role\(s\), not 1$"):
            production.orchestrator(self.load(), "story")

    def test_rules_of_power_missing(self):
        (self.root / ".factory" / "rules-of-power.md").unlink()
        with self.assertRaisesRegex(ProductionError, r"^\.factory/rules-of-power\.md is missing$"):
            production.rules_text(self.load())

    def test_factory_now_malformed(self):
        for value in ("2026-11-11 10:20:30Z", "2026-11-11T10:20:30", "2026-11-11T10:20:30+00:00",
                      "2026-13-11T10:20:30Z", "now"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ProductionError, r"^FACTORY_NOW is not"):
                    production.now({"FACTORY_NOW": value})


class NoValueInMessagesTest(unittest.TestCase):
    def test_seeded_values_never_appear(self):
        root = copy(self)
        set_setting(root, "STATE_DIR", str(root / SEED))
        set_setting(root, "CAP_SEASON_USD", SEED)
        p = production.load(root)
        messages = []
        for call in (production.state_dir, production.caps):
            with self.assertRaises(ProductionError) as caught:
                call(p)
            messages.append(str(caught.exception))
        self.assertEqual(len(messages), 2)
        for message in messages:
            self.assertNotIn(SEED, message)


class MatchesTest(unittest.TestCase):
    def test_single_star_stays_in_one_segment(self):
        self.assertTrue(production.matches("episodes/ep01/beats.md", ["episodes/*/beats.md"]))
        self.assertFalse(production.matches("episodes/ep01/x/beats.md", ["episodes/*/beats.md"]))

    def test_star_never_crosses_a_slash(self):
        self.assertFalse(production.matches("a/b.md", ["*.md"]))
        self.assertFalse(production.matches("a/b/c", ["a/*"]))
        self.assertTrue(production.matches("a/b", ["a/*"]))

    def test_trailing_double_star(self):
        self.assertTrue(production.matches("bible/a.md", ["bible/**"]))
        self.assertTrue(production.matches("bible/a/b/c.md", ["bible/**"]))
        self.assertFalse(production.matches("bible", ["bible/**"]))

    def test_double_star_after_stars(self):
        self.assertTrue(production.matches("episodes/e/sequences/Q1/shots/Q1-1.json",
                                           ["episodes/*/sequences/*/shots/**"]))

    def test_leading_double_star_matches_zero_segments(self):
        self.assertTrue(production.matches("x.md", ["**/x.md"]))
        self.assertTrue(production.matches("a/b/x.md", ["**/x.md"]))
        self.assertFalse(production.matches("a/y.md", ["**/x.md"]))

    def test_middle_double_star(self):
        self.assertTrue(production.matches("a/x.md", ["a/**/x.md"]))
        self.assertTrue(production.matches("a/b/c/x.md", ["a/**/x.md"]))

    def test_question_mark_and_class(self):
        self.assertTrue(production.matches("ep1/b.md", ["ep?/[ab].md"]))
        self.assertFalse(production.matches("ep1/c.md", ["ep?/[ab].md"]))

    def test_leading_dot_slash_dropped(self):
        self.assertTrue(production.matches("./bible/a.md", ["bible/*.md"]))
        self.assertTrue(production.matches("bible/a.md", ["./bible/*.md"]))

    def test_empty_glob_matches_nothing(self):
        self.assertFalse(production.matches("", [""]))
        self.assertFalse(production.matches("a.md", ["", "./"]))
        self.assertFalse(production.matches("a.md", []))

    def test_any_glob(self):
        self.assertTrue(production.matches("b/x.md", ["a/*", "b/*"]))

    def test_case_sensitive(self):
        self.assertFalse(production.matches("Bible/a.md", ["bible/*"]))


def _rules(text):
    return [re.sub(r"\s+", " ", body.replace("**", "")).strip()
            for body in re.findall(r"(?m)^[1-7]\. (.*)$", text)]


class RulesOfPowerTest(unittest.TestCase):
    def test_kit_file_equals_brief_section_2(self):
        brief = (ROOT / "docs" / "system-brief.md").read_text()
        section = re.search(r"(?ms)^## 2\. Rules of power$(.*?)^##+ ", brief).group(1)
        kit_text = (ROOT / "kit" / "rules-of-power.md").read_text()
        expected = _rules(section)
        self.assertEqual(len(expected), 7)
        self.assertEqual(_rules(kit_text), expected)
        numbers = re.findall(r"(?m)^(\d+)\. ", kit_text)
        self.assertEqual(numbers, [str(n) for n in range(1, 8)])
        self.assertTrue(kit_text.startswith("# Rules of power\n"))
        lines = [line for line in kit_text.splitlines() if line.strip()]
        self.assertEqual(len(lines), 8)


class ImportsTest(unittest.TestCase):
    def test_no_process_or_network_modules(self):
        source = (ROOT / "kit" / "lib" / "production.py").read_text()
        for name in ("subprocess", "socket", "urllib"):
            self.assertNotIn(name, source)


if __name__ == "__main__":
    unittest.main()
