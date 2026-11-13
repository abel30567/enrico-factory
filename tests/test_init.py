"""Tests for `factory init`. No network, no git, no model."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "kit"
FIXTURE = ROOT / "tests" / "fixtures" / "init" / "kit"
sys.path.insert(0, str(ROOT))

from kit.lib import frontmatter  # noqa: E402
from kit.lib import cmd_init, finish, resources  # noqa: E402
from kit.lib.lints import canon, provenance  # noqa: E402

ROLE_ROOMS = {"chief": "story", "writer": "story", "editor": "story", "keeper": "studio",
              "uploader": "distribution", "watcher": "distribution"}
LAYOUT = [".gitignore", "bible/amendments/.gitkeep", "bible/canon/.gitkeep", "bible/canon/ledger.tsv",
          "bible/charter.md", "bible/style/.gitkeep", "brand/.gitkeep", "calibration/.gitkeep", "episodes/.gitkeep",
          "lab/.gitkeep", "ledger/.gitkeep", "media/manifest.tsv", "playbook/.gitkeep", "production.env",
          "production.local.env.example", "registry/.gitkeep", "research/.gitkeep", "rulebook/.gitkeep",
          "rules/production-rules.md", "sources/.gitkeep", "sources/rights.tsv"]
ROOM_LAYOUT = {"production": "production/.gitkeep", "post": "post/.gitkeep",
               "distribution": "distribution/.gitkeep"}


def python(*args, cwd=ROOT):
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True, cwd=cwd)


def skipped(name):
    return name.startswith(".") or name == "__pycache__" or name.endswith(".pyc")


def kit_files(kit, roles=None):
    """Files of `kit` init installs, relative to it; `roles` (a set of ids) filters roles/ and evals/."""
    out = []
    for dirpath, dirnames, filenames in os.walk(kit):
        rel = Path(dirpath).relative_to(kit)
        dirnames[:] = [d for d in dirnames if not skipped(d)]
        if roles is not None and rel == Path("evals"):
            dirnames[:] = [d for d in dirnames if d in roles]
        for name in filenames:
            if skipped(name):
                continue
            if roles is not None and rel == Path("roles") and Path(name).stem not in roles:
                continue
            out.append((rel / name).as_posix())
    return sorted(out)


def tree(root, skip=None):
    """{relative path: (kind, mode, sha256)} for every file and directory under `root`."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        if skip and rel.parts[:1] == (skip,):
            continue
        out[rel.as_posix()] = ("dir", os.stat(dirpath).st_mode, "")
        for name in filenames:
            path = Path(dirpath) / name
            out[(rel / name).as_posix()] = ("file", path.stat().st_mode,
                                            hashlib.sha256(path.read_bytes()).hexdigest())
    return out


def files_under(root):
    return sorted(p for p, (kind, _, _) in tree(root).items() if kind == "file")


def layout(rooms):
    return sorted(LAYOUT + [ROOM_LAYOUT[r] for r in rooms if r in ROOM_LAYOUT])


def roles_for(rooms):
    return {role for role, room in ROLE_ROOMS.items() if room in rooms or room == "studio"}


class InitTest(unittest.TestCase):
    def tempdir(self):
        path = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, path)
        return path

    def source_kit(self):
        """A copy of the fixture kit with this repository's `lib/` and dispatcher beside it."""
        kit = self.tempdir() / "kit"
        shutil.copytree(FIXTURE, kit)
        shutil.copytree(KIT / "lib", kit / "lib", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(KIT / "factory", kit / "factory")
        shutil.copy2(KIT / "__init__.py", kit / "__init__.py")
        (kit / "lib" / "__pycache__").mkdir()
        (kit / "lib" / "__pycache__" / "roles.cpython-3.pyc").write_bytes(b"\0compiled")
        (kit / "lib" / "stray.pyc").write_bytes(b"\0compiled")
        return kit

    def init(self, kit, *args):
        return python(kit / "factory", "init", *args)

    def production(self, kit, *args):
        target = self.tempdir()
        result = self.init(kit, target, *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return target, result

    def filtered_kit(self, kit, rooms):
        """The source kit filtered to `rooms` by hand: the expected content of `.factory/`."""
        out = self.tempdir() / "kit"
        shutil.copytree(kit, out)
        for role, room in ROLE_ROOMS.items():
            if role not in roles_for(rooms):
                (out / "roles" / f"{role}.md").unlink()
                shutil.rmtree(out / "evals" / role, ignore_errors=True)
        entries = [e for e in json.loads((out / "pipeline.json").read_text()) if e["room"] in rooms]
        (out / "pipeline.json").write_text(json.dumps(entries))
        return out


class TreeTest(InitTest):
    def test_real_kit_installs_every_path(self):
        target = self.tempdir()
        result = python(KIT / "factory", "init", target)
        self.assertEqual(result.returncode, 0, result.stderr)
        roles = {path.stem for path in (KIT / "roles").glob("*.md")
                 if frontmatter.parse(path.read_text())[0].get("room") in ("story", "studio")}
        installed = kit_files(KIT, roles)
        self.assertEqual(files_under(target), sorted([f".factory/{p}" for p in installed] + layout(["story"])))
        self.assertEqual(result.stdout, "".join(f"created {p}\n" for p in [".factory"] + layout(["story"])))
        self.assertEqual(result.stderr, "")
        lines = (KIT / "pipeline.json").read_text().splitlines()
        kept = [line.rstrip(",") for line in lines[1:-1] if json.loads(line.rstrip(","))["room"] == "story"]
        self.assertEqual((target / ".factory/pipeline.json").read_text(), "[\n" + ",\n".join(kept) + "\n]\n")
        for rel in installed:
            if rel != "pipeline.json":
                self.assertEqual((target / ".factory" / rel).read_bytes(), (KIT / rel).read_bytes(), rel)

    def test_fixture_kit_installs_every_path_with_modes(self):
        kit = self.source_kit()
        rooms = ["story", "distribution"]
        target, result = self.production(kit, "--rooms", "distribution,story")
        installed = kit_files(kit, roles_for(rooms))
        self.assertIn("roles/keeper.md", installed)
        self.assertIn("evals/uploader/case-1.md", installed)
        self.assertEqual(files_under(target), sorted([f".factory/{p}" for p in installed] + layout(rooms)))
        self.assertFalse((target / ".factory/.notes").exists())
        self.assertFalse((target / ".factory/lib/__pycache__").exists())
        self.assertFalse((target / ".factory/lib/stray.pyc").exists())
        for rel in installed:
            self.assertEqual(os.stat(target / ".factory" / rel).st_mode, os.stat(kit / rel).st_mode, rel)
        self.assertTrue(os.access(target / ".factory/factory", os.X_OK))
        self.assertIn("ROOMS=story,distribution\n", (target / "production.env").read_text())

    def test_install_without_evals_is_init_less_evals_and_prints_nothing(self):
        kit = self.source_kit()
        target, _ = self.production(kit)
        built = self.tempdir() / "run"
        done = python("-c", "import sys; from pathlib import Path; from kit.lib import cmd_init; "
                            "cmd_init.install(Path(sys.argv[1]), Path(sys.argv[2]), ['story'], evals=False)",
                      built, kit)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))
        expected = {p: v for p, v in tree(target).items() if p != "." and not p.startswith(".factory/evals")}
        self.assertTrue(any(p.startswith(".factory/evals") for p in tree(target)))
        self.assertEqual({p: v for p, v in tree(built).items() if p != "."}, expected)

    def test_layout_contents(self):
        target, _ = self.production(self.source_kit(), "--rooms", "story")
        self.assertEqual((target / ".gitignore").read_text(), "production.local.env\n__pycache__/\n")
        self.assertEqual((target / "rules/production-rules.md").read_text(), "# Production rules\n")
        self.assertEqual((target / "media/manifest.tsv").read_text(), "id\tsha256\tbytes\tsource\n")
        self.assertEqual((target / "sources/.gitkeep").read_bytes(), b"")
        self.assertEqual((target / "sources/rights.tsv").read_bytes(), b"path\twork\tholder\tbasis\tnote\n")
        env = (target / "production.env").read_text().splitlines()
        self.assertTrue(env[0].startswith("# "))
        self.assertEqual(env[1:], ["REPO=", "BASE=main", "ROOMS=story", "MEDIA_STORE=", "MODEL_MAKER=",
                                   "MODEL_CHECKER=", "MODEL_OTHER=", "CAP_EPISODE_USD=", "CAP_SEASON_USD=",
                                   "CAP_EPISODE_COMPUTE_MIN=", "CAP_SEASON_COMPUTE_MIN=",
                                   "CAP_TICKET_ITERATIONS=", "CAP_EPISODE_HOURS=", "CAP_SEASON_HOURS="])
        local = (target / "production.local.env.example").read_text().splitlines()
        self.assertTrue(local[0].startswith("# ") and "production.local.env" in local[0])
        self.assertEqual(local[1:], ["HARNESS=", "STATE_DIR=", "SECRETS_DIR=", "ACCOUNTS_MAKER=",
                                     "ACCOUNTS_CHECKER=", "PROXY_URL=", "FERMI_URL=",
                                     "SANDBOX=on", "SANDBOX_DENY=", "GATEWAY=on", "GITHUB_LOGINS="])
        self.assertFalse([line for line in local if line.startswith("ACCOUNTS_OTHER")])

    def test_two_empty_directories_give_identical_trees_and_output(self):
        kit = self.source_kit()
        first, out1 = self.production(kit, "--rooms", "story,distribution")
        second, out2 = self.production(kit, "--rooms", "story,distribution")
        self.assertEqual(tree(first), tree(second))
        self.assertEqual((out1.stdout, out1.stderr), (out2.stdout, out2.stderr))

    def test_runs_as_a_script(self):
        target = self.tempdir()
        result = python(KIT / "lib" / "cmd_init.py", target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((target / ".factory/factory").is_file())

    def test_role_that_does_not_parse_is_copied_with_a_warning(self):
        kit = self.source_kit()
        (kit / "roles" / "broken.md").write_text("no front matter here\n")
        target, result = self.production(kit, "--rooms", "story")
        self.assertIn("roles/broken.md", result.stderr)
        self.assertEqual((target / ".factory/roles/broken.md").read_text(), "no front matter here\n")


class CheckTest(InitTest):
    """Acceptance criterion 2: the installed kit checks like the source kit filtered to the rooms."""

    def assertSameCheck(self, kit, rooms):
        target, _ = self.production(kit, "--rooms", ",".join(rooms))
        installed = python(kit / "factory", "roles", "--check", "--kit", ".factory", cwd=target)
        source = python(kit / "factory", "roles", "--check", "--kit", self.filtered_kit(kit, rooms))
        self.assertEqual((installed.returncode, installed.stdout), (source.returncode, source.stdout))
        return installed, target

    def test_clean_fixture_checks_ok_and_lints_pass(self):
        kit = self.source_kit()
        for rooms in (["story", "distribution"], ["story"], ["distribution"]):
            with self.subTest(rooms=rooms):
                result, target = self.assertSameCheck(kit, rooms)
                self.assertEqual((result.returncode, result.stdout), (0, "OK\n"))
                lint = python(kit / "factory", "lint", "--root", target, "--only", "kit,media")
                self.assertEqual(lint.returncode, 0, lint.stdout)
                self.assertEqual(lint.stdout, "kit PASS\nmedia PASS\n")

    def test_seeded_defect_is_reported_only_where_its_room_is_installed(self):
        kit = self.source_kit()
        path = kit / "roles" / "uploader.md"
        path.write_text(path.read_text().replace("gated: yes", "gated: no"))
        result, _ = self.assertSameCheck(kit, ["distribution"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("roles/uploader.md: gated must be 'yes'", result.stdout)
        result, _ = self.assertSameCheck(kit, ["story"])
        self.assertEqual(result.stdout, "OK\n")

    def test_installed_dispatcher_runs_roles_check(self):
        target, _ = self.production(self.source_kit(), "--rooms", "story")
        result = python(".factory/factory", "roles", "--check", "--kit", ".factory", cwd=target)
        self.assertEqual((result.returncode, result.stdout), (0, "OK\n"), result.stderr)

    def test_installed_dispatcher_ignores_another_kit_package(self):
        target, _ = self.production(self.source_kit(), "--rooms", "story")
        other = self.tempdir()
        for package in ("kit", "kit/lib"):
            (other / package).mkdir()
            (other / package / "__init__.py").write_text("raise ImportError('the wrong kit was imported')\n")
        env = dict(os.environ, PYTHONPATH=".")
        result = subprocess.run([sys.executable, target / ".factory/factory", "roles", "--check",
                                 "--kit", target / ".factory"], capture_output=True, text=True, cwd=other, env=env)
        self.assertEqual((result.returncode, result.stdout), (0, "OK\n"), result.stderr)
        self.assertNotIn("the wrong kit", result.stderr)

    def test_source_dispatcher_roles_check_is_unchanged(self):
        dispatched = python("kit/factory", "roles", "--check")
        script = python("kit/lib/cmd_roles.py", "--check")
        self.assertEqual((dispatched.returncode, dispatched.stdout, dispatched.stderr),
                         (script.returncode, script.stdout, script.stderr))


class RoomsTest(InitTest):
    def test_one_room_installs_only_its_roles_evals_and_pipeline(self):
        kit = self.source_kit()
        for room, roles, evals in (("story", ["chief", "editor", "keeper", "writer"], ["editor", "keeper", "writer"]),
                                   ("distribution", ["keeper", "uploader", "watcher"], ["keeper", "uploader"])):
            with self.subTest(room=room):
                target, _ = self.production(kit, "--rooms", room)
                self.assertEqual(sorted(p.stem for p in (target / ".factory/roles").iterdir()), roles)
                self.assertEqual(sorted(p.name for p in (target / ".factory/evals").iterdir()), evals)
                text = (target / ".factory/pipeline.json").read_text()
                entries = json.loads(text)
                self.assertEqual([e["room"] for e in entries], [room])
                self.assertEqual(text, "[\n " + json.dumps(entries[0]) + "\n]\n")
                self.assertIn(f"ROOMS={room}\n", (target / "production.env").read_text())
                self.assertEqual((target / "distribution/.gitkeep").exists(), room == "distribution")

    def test_every_production_gets_an_empty_brand_directory(self):
        kit = self.source_kit()
        for rooms in ("story", ",".join(cmd_init.ROOMS)):
            with self.subTest(rooms=rooms):
                target, result = self.production(kit, "--rooms", rooms)
                self.assertIn("created brand/.gitkeep\n", result.stdout)
                self.assertEqual((target / "brand/.gitkeep").read_bytes(), b"")
                self.assertFalse((target / "brand/brand.json").exists())

    def test_default_room_is_story(self):
        target, _ = self.production(self.source_kit())
        self.assertIn("ROOMS=story\n", (target / "production.env").read_text())
        self.assertFalse((target / ".factory/roles/uploader.md").exists())

    def test_bad_arguments_exit_2_and_write_nothing(self):
        kit = self.source_kit()
        target = self.tempdir()
        for args in (["--rooms", "story,stage"], ["--rooms", "studio"], ["--rooms", ","]):
            with self.subTest(args=args):
                result = self.init(kit, target, *args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(list(target.iterdir()), [])
        self.assertEqual(self.init(kit, target / "missing").returncode, 2)
        (target / "file").write_text("x")
        self.assertEqual(self.init(kit, target / "file").returncode, 2)


class AgainTest(InitTest):
    def test_second_init_exits_1_and_changes_nothing(self):
        kit = self.source_kit()
        target, _ = self.production(kit, "--rooms", "story")
        before = tree(target)
        result = self.init(kit, target, "--rooms", "story,distribution")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "init: already a production (use --upgrade)\n")
        self.assertEqual(result.stdout, "")
        self.assertEqual(tree(target), before)

    def test_production_env_alone_makes_a_production(self):
        kit = self.source_kit()
        target = self.tempdir()
        (target / "production.env").write_text("REPO=\n")
        before = tree(target)
        self.assertEqual(self.init(kit, target).returncode, 1)
        self.assertEqual(tree(target), before)


class UpgradeTest(InitTest):
    def test_upgrade_replaces_the_kit_and_touches_nothing_else(self):
        kit = self.source_kit()
        target, _ = self.production(kit, "--rooms", "distribution")
        (target / ".factory/extra.md").write_text("added by hand\n")
        env = target / "production.env"
        env.write_text(env.read_text().replace("REPO=\n", "REPO=example/show\n"))
        before = tree(target, skip=".factory")
        result = self.init(kit, target, "--upgrade")
        self.assertEqual(result.returncode, 0, result.stderr)
        fresh, _ = self.production(kit, "--rooms", "distribution")
        self.assertEqual(tree(target / ".factory"), tree(fresh / ".factory"))
        self.assertEqual(result.stdout, f"upgraded .factory ({len(files_under(fresh / '.factory'))} files)\n")
        self.assertEqual(tree(target, skip=".factory"), before)
        self.assertIn("REPO=example/show\n", env.read_text())

    def test_upgrade_with_rooms_rewrites_only_the_rooms_line(self):
        kit = self.source_kit()
        target, _ = self.production(kit, "--rooms", "story")
        env = target / "production.env"
        env.write_text(env.read_text().replace("BASE=main\n", "BASE=trunk\n"))
        old = env.read_text()
        result = self.init(kit, target, "--upgrade", "--rooms", "story,distribution")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(env.read_text(), old.replace("ROOMS=story\n", "ROOMS=story,distribution\n"))
        fresh, _ = self.production(kit, "--rooms", "story,distribution")
        self.assertEqual(tree(target / ".factory"), tree(fresh / ".factory"))
        self.assertFalse((target / "distribution").exists())

    def test_upgrade_without_a_kit_exits_1(self):
        kit = self.source_kit()
        target = self.tempdir()
        (target / "production.env").write_text("ROOMS=story\n")
        before = tree(target)
        result = self.init(kit, target, "--upgrade")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(tree(target), before)

    def test_upgrade_with_an_unknown_room_in_the_settings_exits_2(self):
        kit = self.source_kit()
        target, _ = self.production(kit)
        env = target / "production.env"
        env.write_text(env.read_text().replace("ROOMS=story\n", "ROOMS=story,stage\n"))
        before = tree(target)
        self.assertEqual(self.init(kit, target, "--upgrade").returncode, 2)
        self.assertEqual(tree(target), before)


class ExistingFilesTest(InitTest):
    def test_existing_files_keep_their_content(self):
        kit = self.source_kit()
        target = self.tempdir()
        (target / ".gitignore").write_text("node_modules/\n__pycache__/")
        (target / "rules").mkdir()
        (target / "rules/production-rules.md").write_text("# Our rules\n\nNo spoilers in titles.\n")
        result = self.init(kit, target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("created .gitignore\n", result.stdout)
        self.assertNotIn("created rules/production-rules.md\n", result.stdout)
        self.assertEqual((target / "rules/production-rules.md").read_text(), "# Our rules\n\nNo spoilers in titles.\n")
        expected = "node_modules/\n__pycache__/\nproduction.local.env\n"
        self.assertEqual((target / ".gitignore").read_text(), expected)
        for _ in range(2):
            self.assertEqual(self.init(kit, target, "--upgrade").returncode, 0)
            self.assertEqual((target / ".gitignore").read_text(), expected)


class BibleTest(InitTest):
    """The charter and the canon ledger every role reads first exist from the first init."""

    CHARTER = "bible/charter.md"
    LEDGER = "bible/canon/ledger.tsv"

    def test_empty_directory_gets_a_charter_and_a_header_only_ledger(self):
        target, result = self.production(self.source_kit())
        self.assertIn(f"created {self.CHARTER}\n", result.stdout)
        self.assertIn(f"created {self.LEDGER}\n", result.stdout)
        self.assertEqual((target / self.CHARTER).read_text(), "# Charter\n")
        self.assertEqual((target / self.LEDGER).read_text(), "subject\tattribute\tvalue\ttag\tsource\n")

    def test_existing_charter_and_ledger_are_kept_byte_for_byte(self):
        target = self.tempdir()
        charter = b"# Charter\n\nA show about invented lighthouse keepers.\n"
        ledger = b"subject\tattribute\tvalue\ttag\tsource\nKeeper Ilo\teyes\tgrey\tinvented\t\n"
        (target / "bible/canon").mkdir(parents=True)
        (target / self.CHARTER).write_bytes(charter)
        (target / self.LEDGER).write_bytes(ledger)
        result = self.init(self.source_kit(), target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((target / self.CHARTER).read_bytes(), charter)
        self.assertEqual((target / self.LEDGER).read_bytes(), ledger)
        self.assertNotIn(f"created {self.CHARTER}\n", result.stdout)
        self.assertNotIn(f"created {self.LEDGER}\n", result.stdout)

    def test_created_ledger_passes_the_canon_and_provenance_lints(self):
        target, _ = self.production(self.source_kit())
        self.assertTrue((target / self.LEDGER).is_file())
        for lint in (canon, provenance):
            self.assertEqual([f for f in lint.check(target) if f.path == self.LEDGER], [], lint.__name__)
        (target / self.LEDGER).write_bytes(b"")
        self.assertIn("ledger has no header row",
                      [f.message for f in provenance.check(target) if f.path == self.LEDGER])


class SandboxDenyTest(InitTest):
    """The example names SANDBOX_DENY; left empty it denies nothing extra, filled in it is read as a list."""

    def deny(self, value):
        root = self.tempdir()
        filled = {"HARNESS": "/opt/harness", "STATE_DIR": str(root / "state"),
                  "SECRETS_DIR": str(root / "secrets"), "ACCOUNTS_MAKER": "maker-a"}
        if value is not None:
            filled["SANDBOX_DENY"] = value
        lines = []
        for line in cmd_init.LOCAL_ENV_EXAMPLE.splitlines():
            key = line.split("=", 1)[0]
            lines.append(f"{key}={filled[key]}" if key in filled else line)
        self.assertIn("SANDBOX_DENY=" + (value or ""), lines)
        (root / "production.local.env").write_text("\n".join(lines) + "\n")
        return finish._sandbox_deny(resources.settings(root))

    def test_empty_denies_nothing_and_a_list_is_read(self):
        self.assertEqual(self.deny(None), ())
        self.assertEqual(self.deny("/a/b,/c"), ("/a/b", "/c"))


class NoMachineValuesTest(InitTest):
    ALLOWED = {"BASE": "main", "SANDBOX": "on", "GATEWAY": "on"}

    def test_created_files_hold_no_path_and_no_value(self):
        target, _ = self.production(self.source_kit(), "--rooms", "story,post")
        scanned = 0
        for rel, (kind, _, _) in tree(target, skip=".factory").items():
            if kind != "file":
                continue
            text = (target / rel).read_text()
            scanned += 1
            self.assertNotRegex(text, r"/Users/|/home/|~", rel)
            self.assertNotIn(str(target), text, rel)
            for line in text.splitlines():
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                expected = "story,post" if key == "ROOMS" else self.ALLOWED.get(key, "")
                self.assertRegex(key, r"^[A-Z_]+$", (rel, line))
                self.assertEqual(value, expected, (rel, line))
        self.assertEqual(scanned, len(layout(["story", "post"])))


if __name__ == "__main__":
    unittest.main()
