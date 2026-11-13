"""The production room's eight maker role files (brief sections 4, 6 and 16)."""

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_roles_making import (AUTHORITY, FORBIDDEN, HEADINGS, MAKER_RULES, MAKER_TOOLS,  # noqa: E402
                               MAX_LINES, KitCopy, anchor, sections)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import frontmatter, production, roles  # noqa: E402
from kit.lib.frontmatter import as_list  # noqa: E402

KIT = ROOT / "kit"
FACTORY = KIT / "factory"

# role id: (analog, stage, writes, checked_by), ruling 2 of the ticket.
MAKERS = {
    "asset-builder": ("asset modeller", "assets", "production/assets/*/assets/scene.py",
                      "look-reviewer"),
    "rigger": ("character rigger", "rigging", "production/assets/*/rigging/scene.py",
               "deformation-reviewer"),
    "layout-artist": ("layout artist", "layout", "production/*/*/layout/scene.py",
                      "cinematography-reviewer"),
    "animator": ("character animator", "animation", "production/*/*/animation/scene.py",
                 "performance-reviewer"),
    "sim-artist": ("effects artist", "simulation", "production/*/*/simulation/scene.py",
                   "dailies-reviewer"),
    "lighter": ("lighting artist", "lighting", "production/*/*/lighting/scene.py",
                "look-reviewer"),
    "sound-mixer": ("re-recording mixer", "conform", "production/*/conform/scene.py",
                    "mix-reviewer"),
    "conform-editor": ("online editor", "conform",
                       "production/*/conform/edit-list.json, production/*/conform/captions.json",
                       "mix-reviewer"),
}
MAY_NOT = "change the locked story package"
COMMON = {"room": "production", "kind": "maker", "reads": "bible/**, episodes/**, production/**",
          "may_not": MAY_NOT, "model": "maker", "family": "any", "tools": MAKER_TOOLS,
          "knowledge": "skills, memory-read", "secrets": "", "gated": "no"}
# Files no maker writes: the job's, the intake's, a request, the story and the kit.
NOT_THEIRS = ("production/ep01/shots.json", "production/ep01/s01/layout/state.json",
              "production/ep01/s01/layout/manifest.json", "production/ep01/s01/lighting/render.json",
              "production/ep01/s01/simulation/rerun.json", "production/ep01/conform/mix.json",
              "production/assets/fox/assets/state.json", "production/ep01/conform/requests/a.json",
              "episodes/ep01/reel/edit-list.json", "bible/style/characters/fox.md",
              "media/manifest.tsv", ".factory/roles/x.md")
ENGINE = "You do not run the engine."
LINT_BLOCKER = "`[LINT]` blocker"
VERBS = ("READY", "ADDRESSED", "BLOCKED")


def expected_meta(role_id):
    analog, stage, writes, checked_by = MAKERS[role_id]
    return {"role": role_id, **COMMON, "analog": analog, "stage": stage, "writes": writes,
            "checked_by": checked_by}


def example(glob):
    """A glob of ruling 2 with `fox` for an asset, `ep01` for an episode and `s01` for a shot."""
    if glob.startswith("production/assets/"):
        return glob.replace("*", "fox")
    return glob.replace("*", "ep01", 1).replace("*", "s01", 1)


def writers(kit_dir, path):
    """The sorted ids of the eight whose `writes` match `path`."""
    loaded = roles.load_roles(Path(kit_dir) / "roles")
    return sorted(r for r in MAKERS if r in loaded
                  and production.matches(path, as_list(loaded[r]["meta"].get("writes"))))


def own_problems(role_id, text):
    """What this ticket's rules find wrong with one role file. Empty is clean."""
    try:
        meta, body = frontmatter.parse(text)
    except frontmatter.FrontMatterError as error:
        return [str(error)]
    problems = []
    expected = expected_meta(role_id)
    problems += [f"unexpected key {k!r}" for k in sorted(set(meta) - set(expected))]
    problems += [f"missing key {k!r}" for k in sorted(set(expected) - set(meta))]
    for key, value in expected.items():
        if key in meta and meta[key] != value:
            problems.append(f"{key} is {meta[key]!r}, expected {value!r}")
    for glob in as_list(meta.get("writes")):
        for path in NOT_THEIRS:
            if production.matches(path, [glob]):
                problems.append(f"writes {glob!r} reaches {path}")

    lines = len(text.splitlines())
    if lines > MAX_LINES:
        problems.append(f"{lines} lines, more than {MAX_LINES}")
    if FORBIDDEN.search(text):
        problems.append(f"names a model, flag or path: {FORBIDDEN.search(text).group(0)!r}")

    parts = sections(body)
    if [h for h, _ in parts] != HEADINGS:
        problems.append(f"headings {[h for h, _ in parts]} are not {HEADINGS}")
    part = {heading: " ".join(text.split()) for heading, text in parts}
    if not part.get("Read first", "").endswith(AUTHORITY):
        problems.append("Read first does not end with the order of authority")

    whole = " ".join(body.split())
    for phrase in MAKER_RULES + (ENGINE, LINT_BLOCKER):
        if phrase not in whole:
            problems.append(f"body lacks {phrase!r}")
    for glob in as_list(expected["writes"]):
        if anchor(glob) not in part.get("What you produce", ""):
            problems.append(f"What you produce does not name {anchor(glob)!r}")
    if MAY_NOT not in part.get("You may not", ""):
        problems.append(f"You may not lacks {MAY_NOT!r}")
    marks = part.get("Markers", "")
    if roles.actor({"role": role_id}) not in marks:
        problems.append("Markers does not name the actor")
    for verb in VERBS:
        if f"`{verb}`" not in marks:
            problems.append(f"Markers lacks {verb}")
    return problems


def shipped(role_id):
    return (KIT / "roles" / f"{role_id}.md").read_text(encoding="utf-8")


def factory(*args, cwd=ROOT, program=FACTORY):
    return subprocess.run([sys.executable, str(program), *args], capture_output=True, text=True,
                          cwd=cwd)


class ProductionMakersTest(unittest.TestCase):
    def test_1_each_file_meets_the_ticket(self):
        for role_id in MAKERS:
            with self.subTest(role=role_id):
                self.assertEqual(own_problems(role_id, shipped(role_id)), [])
        kit = KitCopy(self)
        kit.edit("layout-artist", "writes: production/*/*/layout/scene.py",
                 "writes: production/*/*/layout/**")
        self.assertIn("writes 'production/*/*/layout/**' reaches "
                      "production/ep01/s01/layout/state.json",
                      own_problems("layout-artist", kit.path("layout-artist").read_text()))

    def test_2_shipped_kit_validates(self):
        self.assertEqual(roles.validate(KIT), [])
        kit = KitCopy(self)
        kit.edit("sim-artist", "checked_by: dailies-reviewer", "checked_by: no-such-role")
        self.assertIn("roles/sim-artist.md: checked_by 'no-such-role' is not a role",
                      roles.validate(kit.dir))
        kit = KitCopy(self)
        kit.edit("lighter", "checked_by: look-reviewer", "checked_by: animator")
        self.assertIn("roles/lighter.md: checked_by 'animator' has kind maker, not checker or "
                      "judge", roles.validate(kit.dir))

    def test_3_each_file_has_one_writer(self):
        for role_id, (_, _, writes, _) in MAKERS.items():
            for glob in as_list(writes):
                with self.subTest(glob=glob):
                    self.assertEqual(writers(KIT, example(glob)), [role_id])
        kit = KitCopy(self)
        kit.edit("rigger", "writes: production/assets/*/rigging/scene.py",
                 "writes: production/assets/*/*/scene.py")
        self.assertEqual(writers(kit.dir, "production/assets/fox/assets/scene.py"),
                         ["asset-builder", "rigger"])

    def test_4_may_not_reworded(self):
        kit = KitCopy(self)
        kit.edit("animator", f"may_not: {MAY_NOT}", "may_not: change the story")
        self.assertIn(f"may_not is 'change the story', expected {MAY_NOT!r}",
                      own_problems("animator", kit.path("animator").read_text()))

    def test_5_captions_unnamed(self):
        kit = KitCopy(self)
        kit.edit("conform-editor", "`production/<ep>/conform/captions.json`",
                 "`production/<ep>/conform/cues.json`")
        self.assertIn("What you produce does not name 'conform/captions.json'",
                      own_problems("conform-editor", kit.path("conform-editor").read_text()))

    def test_6_engine_rule_removed_and_length(self):
        kit = KitCopy(self)
        kit.edit("sound-mixer", ENGINE, "")
        self.assertIn(f"body lacks {ENGINE!r}",
                      own_problems("sound-mixer", kit.path("sound-mixer").read_text()))
        kit.edit("animator", "{{RULES}}", "{{RULES}}" + "\n" * MAX_LINES)
        own = own_problems("animator", kit.path("animator").read_text())
        self.assertTrue([p for p in own if re.fullmatch(rf"\d+ lines, more than {MAX_LINES}", p)],
                        own)

    def test_7_factory_lists_the_eight_under_production(self):
        result = factory("roles")
        self.assertEqual(result.returncode, 0, result.stderr)
        room, listed = None, []
        for line in result.stdout.splitlines():
            if not line.startswith(" "):
                room = line
            elif room == "production:":
                listed.append(line)
        for role_id in MAKERS:
            with self.subTest(role=role_id):
                self.assertIn(f"  {role_id} (maker)", listed)

    def test_8_init_installs_the_eight_with_production(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        bare, full = Path(tmp) / "bare", Path(tmp) / "full"
        bare.mkdir()
        full.mkdir()
        self.assertEqual(factory("init", str(bare)).returncode, 0)
        self.assertEqual({p.stem for p in (bare / ".factory" / "roles").glob("*.md")} & set(MAKERS),
                         set())
        result = factory("init", str(full), "--rooms", "story,production")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLessEqual(set(MAKERS),
                             {p.stem for p in (full / ".factory" / "roles").glob("*.md")})
        lint = factory("lint", cwd=full, program=full / ".factory" / "factory")
        self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)


if __name__ == "__main__":
    unittest.main()
