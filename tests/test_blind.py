"""Tests for kit/lib/blind.py and kit/lib/cmd_blind.py.

GitHub is the recorded directory tests/fixtures/blind/github/ (copied, never changed in place). The git
repository is built in a temporary directory from tests/fixtures/blind/production/, with a branch for
each open pull, and its STATE_DIR is a temporary directory written to production.local.env.

Recorded: ticket #1 (board, ep01/Q1) merged as #3; ticket #2 (same unit) open as #4; ticket #5 (script,
ep01, a stage that is not blind) merged as #6; ticket #7 (same unit) open as #8.
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import blind, cmd_blind, gitstate, github, production, tickets  # noqa: E402
from kit.lib.blind import BlindError  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "blind"
NOW = "2026-11-11T12:00:00Z"
SHOT1 = "episodes/ep01/sequences/Q1/shots/Q1-1.json"
SHOT2 = "episodes/ep01/sequences/Q1/shots/Q1-2.json"
SCRIPT = "episodes/ep01/script.md"
MANIFEST = "media/manifest.tsv"
BRANCH = "feat/BD-2-redo"
NEW_SHOT1 = '{\n "id": "Q1-1",\n "duration": 2.5,\n "action": "The lantern dips and catches the reeds."\n}\n'
NEW_SHOT2 = '{\n "id": "Q1-2",\n "duration": 1.5,\n "action": "A heron looks up."\n}\n'
SHOT3 = "episodes/ep01/sequences/Q1/shots/Q1-3.json"
ORIGIN_SHOT3 = '{\n "id": "Q1-3",\n "duration": 2,\n "action": "The reeds part."\n}\n'
CONTEXT = {
    "bible/charter.md": "# Charter\n\nA quiet pond at dusk.\n",
    "bible/canon/ledger.tsv": "fact\tsince\nthe heron is grey\tep01\n",
    "bible/canon/characters/heron.md": "# Heron\n\nGrey, patient.\n",
    "bible/canon/places/pond.md": "# Pond\n\nReeds on the east bank.\n",
    "bible/style/characters/heron.md": "# Heron, drawn\n\nLong neck, two lines.\n",
    "episodes/ep01/beats.md": "# Beats\n\n1. The lantern dips.\n",
    "episodes/ep01/ledger.json": '{\n "facts": []\n}\n',
    "notes/plan.md": "# Plan\n\nNothing a judge reads.\n",
}


def blinding(salt, pull, head):
    digest = hashlib.sha256(salt + b"\n" + str(pull).encode() + b"\n" + head.encode())
    return digest.hexdigest()[:12], "new" if digest.digest()[0] % 2 == 0 else "old"


def files(tree):
    """{tree-relative posix path: bytes} of every file under `tree`."""
    return {path.relative_to(tree).as_posix(): path.read_bytes()
            for path in sorted(Path(tree).rglob("*")) if path.is_file()}


class World:
    """The production repository on branch `trunk`, its state directory and a recorded GitHub, all temporary."""

    def __init__(self, test, base_shots=True, pitch_judge=False):
        tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, tmp)
        self.root = tmp / "show"
        self.state_dir = tmp / "state"
        self.dir = tmp / "github"
        patch = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                                             "FACTORY_GITHUB": str(self.dir), "FACTORY_NOW": NOW})
        patch.start()
        test.addCleanup(patch.stop)
        shutil.copytree(FIX / "production", self.root)
        shutil.copytree(FIX / "github", self.dir)
        (self.root / "production.local.env").write_text(f"STATE_DIR={self.state_dir}\n")
        if not base_shots:
            shutil.rmtree(self.root / "episodes/ep01/sequences")
        if pitch_judge:
            shutil.copy(ROOT / "kit" / "roles" / "pitch-judge.md", self.root / ".factory" / "roles" / "pitch-judge.md")
            pipeline = self.root / ".factory" / "pipeline.json"
            entries = json.loads(pipeline.read_text())
            next(e for e in entries if e["stage"] == "board")["judge"] = ["critic", "pitch-judge"]
            pipeline.write_text(json.dumps(entries))
            for rel, text in CONTEXT.items():
                self.write(rel, text)
        self.git("init", "-q", "-b", "trunk")
        self.commit("the production")
        self.git("checkout", "-q", "-b", BRANCH)
        self.write(SHOT1, NEW_SHOT1)
        self.write(SHOT2, NEW_SHOT2)
        self.write(MANIFEST, "path\tsha256\nmedia/ep01/Q1-1.png\t" + "a" * 64 + "\n")
        self.write(SCRIPT, "# Episode one\n\nThe lantern dips.\n")
        self.commit("rework the lantern sequence")
        self.git("checkout", "-q", "-b", "feat/SC-2-redo", "trunk")
        self.write(SCRIPT, "# Episode one, again\n")
        self.commit("rework the script")
        self.git("checkout", "-q", "trunk")
        self.load()

    def git(self, *args):
        return gitstate.git(self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", *args)

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def load(self):
        self.p = production.load(self.root)
        self.state = tickets.load(self.p, github.Recorded(self.dir), self.root)
        return self.state

    def set_record(self, rel, **fields):
        path = self.dir / rel
        path.write_text(json.dumps({**json.loads(path.read_text()), **fields}, indent=1, sort_keys=True) + "\n")
        self.load()

    def prepare(self, number=4):
        return blind.prepare(self.p, self.state, self.root, self.state.pulls[number])

    def head(self, ref=BRANCH):
        return gitstate.head(self.root, ref)

    def blind_dir(self):
        return self.state_dir / "blind"

    def homes(self):
        """The pair directories under the state directory, sorted."""
        if not self.blind_dir().is_dir():
            return []
        return sorted(p.name for p in self.blind_dir().iterdir() if p.is_dir())

    def set_salt(self, salt):
        self.blind_dir().mkdir(parents=True, exist_ok=True)
        (self.blind_dir() / "salt").write_bytes(salt)

    def run(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_blind.main([*argv, "--root", str(self.root)])
        return code, out.getvalue(), err.getvalue()

    def origin(self, behind):
        """A bare `origin` whose `trunk` has one more commit, adding ``SHOT3``, which the pull's maker merged
        into ``BRANCH``; with ``behind`` the local `trunk` stays at the commit before it. Origin's tip."""
        remote = self.root.parent / "origin.git"
        gitstate.git(self.root.parent, "init", "-q", "--bare", str(remote))
        self.git("remote", "add", "origin", str(remote))
        start = gitstate.head(self.root, "trunk")
        self.write(SHOT3, ORIGIN_SHOT3)
        self.commit("a shot merged on GitHub from another clone")
        self.git("push", "-q", "origin", "trunk")
        self.git("checkout", "-q", BRANCH)
        self.git("merge", "-q", "--no-ff", "--no-edit", "trunk")
        self.git("checkout", "-q", "trunk")
        if behind:
            self.git("reset", "-q", "--hard", start)
        self.load()
        return gitstate.head(remote, "trunk")


def salt_for(a_side, pull, head):
    """A salt chosen by the test that gives `A = a_side` for this pull and head."""
    for i in range(256):
        salt = bytes([i]) * 32
        if blinding(salt, pull, head)[1] == a_side:
            return salt
    raise AssertionError("no salt found")


class PrepareTest(unittest.TestCase):
    def test_clean_pair(self):
        w = World(self)
        key = w.prepare()
        home = w.state_dir / "blind" / f"4-{key['id']}"
        pair = home / "pair"
        self.assertTrue((pair / "A").is_dir() and (pair / "B").is_dir())
        self.assertEqual(sorted(p.name for p in pair.iterdir()), ["A", "B"])
        self.assertEqual(sorted(p.name for p in home.iterdir()), ["key.json", "pair"])
        base_shot = (FIX / "production" / SHOT1).read_bytes()
        base_script = (FIX / "production" / SCRIPT).read_bytes()
        expected = {"old": {SHOT1: base_shot, SCRIPT: base_script},
                    "new": {SHOT1: NEW_SHOT1.encode(), SHOT2: NEW_SHOT2.encode(), SCRIPT: base_script}}
        self.assertEqual(files(pair / "A"), expected[key["A"]])
        self.assertEqual(files(pair / "B"), expected[key["B"]])
        self.assertEqual({key["A"], key["B"]}, {"old", "new"})
        self.assertEqual(blind.pair_dir(w.p, 4, w.head()), pair)
        self.assertEqual(blind.key_path(w.p, 4, w.head()), home / "key.json")
        on_disk = json.loads((home / "key.json").read_text())
        self.assertEqual(on_disk, key)
        self.assertEqual(sorted(key), ["A", "B", "base", "created_at", "head", "id", "paths", "pull", "reads"])
        self.assertEqual(key["pull"], 4)
        self.assertEqual(key["head"], w.head())
        self.assertEqual(key["base"], w.head("trunk"))
        self.assertEqual(key["paths"], ["episodes/*/sequences/**"])
        self.assertEqual(key["reads"], ["episodes/**"])
        self.assertEqual(key["created_at"], NOW)
        self.assertEqual(stat.S_IMODE((home / "key.json").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(w.blind_dir().stat().st_mode), 0o700)
        salt = w.blind_dir() / "salt"
        self.assertEqual(stat.S_IMODE(salt.stat().st_mode), 0o600)
        self.assertEqual(len(salt.read_bytes()), 32)
        self.assertEqual(sorted(p.name for p in w.blind_dir().iterdir()), sorted(["salt", home.name]))

    def test_fixed_salt_gives_the_computed_id_and_assignment(self):
        w = World(self)
        salt = b"a salt the test chose, 32 bytes!"
        w.set_salt(salt)
        key = w.prepare()
        blinding_id, a_side = blinding(salt, 4, w.head())
        self.assertEqual(key["id"], blinding_id)
        self.assertEqual(key["A"], a_side)
        self.assertEqual(w.homes(), [f"4-{blinding_id}"])

    def test_two_salts_give_both_assignments(self):
        for a_side in ("new", "old"):
            with self.subTest(a_side=a_side):
                w = World(self)
                salt = salt_for(a_side, 4, w.head())
                w.set_salt(salt)
                key = w.prepare()
                self.assertEqual((key["A"], key["B"]), (a_side, {"new": "old", "old": "new"}[a_side]))
                self.assertEqual(key["id"], blinding(salt, 4, w.head())[0])
                new_letter = "A" if a_side == "new" else "B"
                self.assertIn(SHOT2, files(blind.pair_dir(w.p, 4, w.head()) / new_letter))


class LeakTest(unittest.TestCase):
    def setUp(self):
        self.w = World(self)
        self.key = self.w.prepare()
        self.pair = blind.pair_dir(self.w.p, 4, self.w.head())
        head, base = self.w.head(), self.w.head("trunk")
        self.secrets = [head, base, head[:7], base[:7], BRANCH, BRANCH.rsplit("/", 1)[1]]

    def test_no_sha_or_branch_in_the_pair(self):
        for path in [self.pair, *sorted(self.pair.rglob("*"))]:
            for secret in self.secrets:
                for part in path.parts:
                    self.assertNotIn(secret, part, path)
                if path.is_file():
                    self.assertNotIn(secret.encode(), path.read_bytes(), path)

    def test_no_side_name_in_the_pair(self):
        for path in sorted(self.pair.rglob("*")):
            for part in path.relative_to(self.pair).parts:
                self.assertNotIn(part, ("old", "new"), path)

    def test_every_modification_time_is_zero(self):
        for path in [self.pair, *sorted(self.pair.rglob("*"))]:
            self.assertEqual(path.lstat().st_mtime, 0, path)

    def test_manifest_git_and_other_stage_files_are_absent(self):
        self.assertNotEqual(gitstate.show(self.w.root, BRANCH, MANIFEST), gitstate.show(self.w.root, "trunk", MANIFEST))
        self.assertNotEqual(gitstate.show(self.w.root, BRANCH, SCRIPT), gitstate.show(self.w.root, "trunk", SCRIPT))
        base_script = gitstate.show(self.w.root, "trunk", SCRIPT).encode()
        for letter in ("A", "B"):
            side = files(self.pair / letter)
            self.assertNotIn(MANIFEST, side)
            self.assertEqual(side[SCRIPT], base_script)
            self.assertFalse((self.pair / letter / "media").exists())
            self.assertFalse(any(part == ".git" for p in side for part in p.split("/")))
            self.assertTrue(all(production.matches(p, self.key["paths"]) or production.matches(p, self.key["reads"])
                                for p in side))

    def test_manifest_is_left_out_even_when_the_paths_match_it(self):
        w = World(self)
        pipeline = w.root / ".factory" / "pipeline.json"
        entries = json.loads(pipeline.read_text())
        entries[1]["paths"] = ["episodes/*/sequences/**", "media/**"]
        pipeline.write_text(json.dumps(entries))
        w.load()
        key = w.prepare()
        pair = w.state_dir / "blind" / f"4-{key['id']}" / "pair"
        for letter in ("A", "B"):
            self.assertNotIn(MANIFEST, files(pair / letter))


class RefusalTest(unittest.TestCase):
    def assert_refused(self, w, number, message):
        with self.assertRaises(BlindError) as caught:
            w.prepare(number)
        self.assertEqual(str(caught.exception), message)
        self.assertEqual(w.homes(), [])
        self.assertFalse(blind.has_key(w.p, number, w.state.pulls[number].get("git", {}).get("head", "0" * 40)))

    def test_clean_twin(self):
        w = World(self)
        self.assertTrue(blind.old_exists(w.p, w.state, w.state.pulls[4]))
        w.prepare()
        self.assertEqual(len(w.homes()), 1)

    def test_stage_not_blind(self):
        w = World(self)
        self.assertTrue(tickets.is_locked(w.state, "story", "script", "ep01"))
        self.assertFalse(blind.old_exists(w.p, w.state, w.state.pulls[8]))
        self.assert_refused(w, 8, "no locked version to compare")
        self.assertFalse(w.blind_dir().exists())

    def test_first_version(self):
        w = World(self)
        w.set_record("pulls/3.json", state="closed", merged_at=None)
        self.assertFalse(blind.old_exists(w.p, w.state, w.state.pulls[4]))
        self.assert_refused(w, 4, "no locked version to compare")
        self.assertFalse(w.blind_dir().exists())

    def test_closed_pull(self):
        w = World(self)
        w.set_record("pulls/4.json", state="closed")
        self.assert_refused(w, 4, "pull request #4 is not open")
        self.assertFalse(w.blind_dir().exists())

    def test_old_side_without_stage_files(self):
        w = World(self, base_shots=False)
        self.assertTrue(blind.old_exists(w.p, w.state, w.state.pulls[4]))
        self.assert_refused(w, 4, "no locked version to compare")
        self.assertEqual(sorted(p.name for p in w.blind_dir().iterdir()), ["salt"])

    def test_old_side_without_stage_files_but_context(self):
        w = World(self, base_shots=False, pitch_judge=True)
        self.assertTrue(blind.old_exists(w.p, w.state, w.state.pulls[4]))
        self.assert_refused(w, 4, "no locked version to compare")
        self.assertEqual(sorted(p.name for p in w.blind_dir().iterdir()), ["salt"])

    def test_frozen(self):
        w = World(self)
        (w.dir / "issues" / "9.json").write_text(json.dumps(
            {"number": 9, "title": "Freeze", "body": "", "state": "open", "labels": ["factory:freeze"],
             "created_at": "2026-11-05T09:00:00Z"}))
        code, out, err = w.run("4")
        self.assertEqual((code, out, err), (2, "", "blind: frozen\n"))
        self.assertFalse(w.state_dir.exists())


def outside(tree, paths):
    """{tree-relative posix path: (bytes or link target, mode bits)} of every path under `tree` not matching `paths`."""
    found = {}
    for path in sorted(Path(tree).rglob("*")):
        rel = path.relative_to(tree).as_posix()
        if production.matches(rel, paths):
            continue
        content = os.readlink(path) if path.is_symlink() else None if path.is_dir() else path.read_bytes()
        found[rel] = (content, stat.S_IMODE(path.lstat().st_mode))
    return found


class ContextTest(unittest.TestCase):
    """ISSUE-256: the files the stage's judges read outside its ``paths`` are in the pair, from the old side."""

    def pair(self, w):
        key = w.prepare()
        return key, blind.pair_dir(w.p, 4, w.head())

    def test_read_first_of_the_pitch_judge_is_on_both_sides(self):
        text = (ROOT / "kit" / "roles" / "pitch-judge.md").read_text()
        read_first = text.split("## Read first", 1)[1].split("\n## ", 1)[0]
        for name in ("episodes/<ep>/script.md", "beats.md", "ledger.json", "bible/canon/characters/*.md",
                     "bible/canon/places/*.md", "bible/style/**", "bible/charter.md", "bible/canon/ledger.tsv"):
            self.assertIn(f"`{name}`", read_first)
        w = World(self, pitch_judge=True)
        key, pair = self.pair(w)
        self.assertEqual(key["reads"], ["bible/**", "episodes/**"])
        a, b = files(pair / "A"), files(pair / "B")
        for rel in [SCRIPT, *(rel for rel in CONTEXT if rel != "notes/plan.md")]:
            base = gitstate.show(w.root, "trunk", rel).encode()
            self.assertEqual(a.get(rel), base, rel)
            self.assertEqual(b.get(rel), base, rel)

    def test_only_stage_files_differ(self):
        w = World(self, pitch_judge=True)
        key, pair = self.pair(w)
        self.assertEqual(outside(pair / "A", key["paths"]), outside(pair / "B", key["paths"]))
        self.assertTrue(outside(pair / "A", key["paths"]))
        self.assertNotEqual(files(pair / "A"), files(pair / "B"))

    def test_a_file_the_head_changed_outside_the_paths_comes_from_the_old_side(self):
        w = World(self)
        base, changed = gitstate.show(w.root, "trunk", SCRIPT), gitstate.show(w.root, BRANCH, SCRIPT)
        self.assertNotEqual(base, changed)
        key, pair = self.pair(w)
        for letter in ("A", "B"):
            self.assertEqual(files(pair / letter)[SCRIPT], base.encode(), letter)

    def test_a_file_no_judge_reads_is_absent(self):
        w = World(self, pitch_judge=True)
        self.assertIsNotNone(gitstate.show(w.root, "trunk", "notes/plan.md"))
        self.assertIsNotNone(gitstate.show(w.root, BRANCH, "notes/plan.md"))
        key, pair = self.pair(w)
        for letter in ("A", "B"):
            side = files(pair / letter)
            self.assertNotIn("notes/plan.md", side)
            self.assertNotIn(".factory/pipeline.json", side)
            self.assertFalse((pair / letter / "notes").exists())
            self.assertFalse((pair / letter / ".factory").exists())

    def test_manifest_is_left_out_even_when_a_judge_reads_it(self):
        w = World(self)
        critic = w.root / ".factory" / "roles" / "critic.md"
        critic.write_text(critic.read_text().replace("reads: episodes/**\n", "reads: episodes/**, media/**\n"))
        w.load()
        key, pair = self.pair(w)
        self.assertEqual(key["reads"], ["episodes/**", "media/**"])
        for letter in ("A", "B"):
            self.assertNotIn(MANIFEST, files(pair / letter))
            self.assertFalse((pair / letter / "media").exists())

    def test_context_pair_leaks_nothing(self):
        w = World(self, pitch_judge=True)
        key, pair = self.pair(w)
        head, base = w.head(), w.head("trunk")
        secrets = [head, base, head[:7], base[:7], BRANCH, BRANCH.rsplit("/", 1)[1]]
        for path in [pair, *sorted(pair.rglob("*"))]:
            self.assertEqual(path.lstat().st_mtime, 0, path)
            for secret in secrets:
                for part in path.parts:
                    self.assertNotIn(secret, part, path)
                if path.is_file():
                    self.assertNotIn(secret.encode(), path.read_bytes(), path)
            if path != pair:
                for part in path.relative_to(pair).parts:
                    self.assertNotIn(part, ("old", "new"), path)


class IdempotenceTest(unittest.TestCase):
    def test_second_run_rewrites_nothing(self):
        w = World(self)
        key = w.prepare()
        key_file = blind.key_path(w.p, 4, w.head())
        before = key_file.read_bytes()
        shot = next(p for p in sorted(blind.pair_dir(w.p, 4, w.head()).rglob("*")) if p.is_file())
        inode = shot.stat().st_ino
        with mock.patch.dict(os.environ, {"FACTORY_NOW": "2026-11-12T08:00:00Z"}):
            again = w.prepare()
        self.assertEqual(again, key)
        self.assertEqual(key_file.read_bytes(), before)
        self.assertEqual(shot.stat().st_ino, inode)
        self.assertEqual(shot.lstat().st_mtime, 0)

    def test_new_head_gets_a_new_directory(self):
        w = World(self)
        first = w.prepare()
        w.git("checkout", "-q", BRANCH)
        w.write(SHOT2, NEW_SHOT2.replace("looks up", "takes flight"))
        w.commit("a better heron")
        w.git("checkout", "-q", "trunk")
        w.load()
        second = w.prepare()
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(second["head"], w.head())
        self.assertEqual(w.homes(), sorted([f"4-{first['id']}", f"4-{second['id']}"]))
        self.assertTrue((w.blind_dir() / f"4-{first['id']}" / "key.json").is_file())

    def test_an_incomplete_directory_is_replaced(self):
        w = World(self)
        salt = b"s" * 32
        w.set_salt(salt)
        home = w.blind_dir() / f"4-{blinding(salt, 4, w.head())[0]}"
        (home / "pair" / "A").mkdir(parents=True)
        (home / "pair" / "A" / "stray.txt").write_text("left by a run that stopped\n")
        self.assertFalse(blind.has_key(w.p, 4, w.head()))
        key = w.prepare()
        self.assertNotIn("stray.txt", files(home / "pair" / "A"))
        self.assertEqual(json.loads((home / "key.json").read_text()), key)


class ResolveTest(unittest.TestCase):
    def test_every_verb_for_both_assignments(self):
        for a_side, b_side in (("new", "old"), ("old", "new")):
            with self.subTest(a_side=a_side):
                w = World(self)
                w.set_salt(salt_for(a_side, 4, w.head()))
                w.prepare()
                head = w.head()
                self.assertEqual(blind.resolve(w.p, 4, head, "PREFERS A"), a_side)
                self.assertEqual(blind.resolve(w.p, 4, head, "PREFERS B"), b_side)
                self.assertEqual(blind.resolve(w.p, 4, head, "NO PREFERENCE"), "none")
                with self.assertRaises(BlindError):
                    blind.resolve(w.p, 4, head, "PREFERS C")
                with self.assertRaises(BlindError):
                    blind.resolve(w.p, 4, head, "PASS")

    def test_no_key(self):
        w = World(self)
        with self.assertRaises(BlindError):
            blind.resolve(w.p, 4, w.head(), "PREFERS A")
        self.assertFalse(w.state_dir.exists())
        w.prepare()
        with self.assertRaises(BlindError):
            blind.resolve(w.p, 4, "0" * 40, "PREFERS A")
        with self.assertRaises(BlindError):
            blind.resolve(w.p, 8, w.head("feat/SC-2-redo"), "NO PREFERENCE")


class HasKeyTest(unittest.TestCase):
    def test_false_before_true_after(self):
        w = World(self)
        self.assertFalse(blind.has_key(w.p, 4, w.head()))
        self.assertIsNone(blind.pair_dir(w.p, 4, w.head()))
        self.assertFalse(w.state_dir.exists())
        w.prepare()
        self.assertTrue(blind.has_key(w.p, 4, w.head()))
        self.assertFalse(blind.has_key(w.p, 4, w.head("trunk")))
        self.assertFalse(blind.has_key(w.p, 8, w.head()))

    def test_no_salt_creates_no_file(self):
        w = World(self)
        w.state_dir.mkdir()
        (w.state_dir / "blind").mkdir()
        self.assertFalse(blind.has_key(w.p, 4, w.head()))
        self.assertIsNone(blind.pair_dir(w.p, 4, w.head()))
        self.assertEqual(list(w.state_dir.rglob("*")), [w.state_dir / "blind"])


class CommandTest(unittest.TestCase):
    def test_prints_the_id_and_the_pair_only(self):
        w = World(self)
        log = w.dir / "log.jsonl"
        log.write_text('{"number": 1, "op": "post"}\n')
        code, out, err = w.run("4")
        self.assertEqual((code, err), (0, ""))
        key = json.loads(blind.key_path(w.p, 4, w.head()).read_text())
        pair = blind.pair_dir(w.p, 4, w.head())
        self.assertEqual(out, f"blind 4: id {key['id']} pair {pair}\n")
        # The temporary state directory's random name is not the command's to choose.
        own = out.replace(str(w.state_dir), "")
        self.assertNotIn("old", own)
        self.assertNotIn("new", own)
        self.assertEqual(log.read_text(), '{"number": 1, "op": "post"}\n')
        self.assertEqual(w.run("4"), (0, out, ""))

    def test_log_stays_absent(self):
        w = World(self)
        self.assertEqual(w.run("4")[0], 0)
        self.assertFalse((w.dir / "log.jsonl").exists())

    def test_not_a_tracked_pull(self):
        w = World(self)
        self.assertEqual(w.run("99"), (1, "", "blind: #99 is not a pull request of a tracked ticket\n"))
        self.assertFalse(w.state_dir.exists())

    def test_blind_error_exits_1(self):
        w = World(self)
        self.assertEqual(w.run("8"), (1, "", "blind: no locked version to compare\n"))

    def test_production_error_exits_2(self):
        w = World(self)
        (w.root / "production.local.env").unlink()
        self.assertEqual(w.run("4"), (2, "", "blind: setting STATE_DIR is empty\n"))

    def test_github_error_exits_2(self):
        w = World(self)
        with mock.patch.dict(os.environ, {"FACTORY_GITHUB": str(w.root / "absent")}):
            self.assertEqual(w.run("4"), (2, "", "blind: connect: FACTORY_GITHUB is not a directory\n"))

    def test_git_error_exits_2(self):
        w = World(self)
        w.git("branch", "-q", "-D", "feat/SC-2-redo")
        code, out, err = w.run("4")
        self.assertEqual((code, out), (2, ""))
        self.assertTrue(err.startswith("blind: git "), err)

    def test_runs_through_factory(self):
        w = World(self)
        proc = subprocess.run([sys.executable, str(ROOT / "kit" / "factory"), "blind", "4", "--root", str(w.root)],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))
        self.assertEqual(proc.stdout, f"blind 4: id {blind.prepare(w.p, w.load(), w.root, w.state.pulls[4])['id']} "
                                      f"pair {blind.pair_dir(w.p, 4, w.head())}\n")


class CountingSync(github.Recorded):
    """A recorded GitHub that records each base ``sync_base`` is called with; with ``fail`` it raises."""

    def __init__(self, directory, fail=False):
        super().__init__(directory)
        self.fail = fail
        self.synced = []

    def sync_base(self, root, base_ref):
        self.synced.append(base_ref)
        if self.fail:
            raise github.GitHubError(f"sync_base {base_ref}: git fetch failed (exit 128)")
        return super().sync_base(root, base_ref)


class GitSync(github.Recorded):
    """A recorded GitHub whose ``sync_base`` is ``Gh.sync_base``: it starts only `git`, on the clone's `origin`."""

    def sync_base(self, root, base_ref):
        return github.Gh("example/show").sync_base(root, base_ref)


class SyncBaseTest(unittest.TestCase):
    """ISSUE-228: ``factory blind`` first brings the local base up to GitHub's."""

    def setUp(self):
        self.w = World(self)

    def run_with(self, gh, *argv):
        with mock.patch.object(github, "connect", return_value=gh):
            return self.w.run(*argv)

    def snapshot(self):
        """The recorded GitHub's bytes, the production's refs and the file names under the state directory."""
        w = self.w
        return ({str(path.relative_to(w.dir)): path.read_bytes()
                 for path in sorted(w.dir.rglob("*")) if path.is_file()},
                w.git("for-each-ref", "--format=%(refname) %(objectname)"),
                sorted(str(path.relative_to(w.state_dir)) for path in w.state_dir.rglob("*")))

    def old_side(self):
        key = json.loads(blind.key_path(self.w.p, 4, self.w.head()).read_text())
        old = "A" if key["A"] == "old" else "B"
        return files(blind.pair_dir(self.w.p, 4, self.w.head()) / old)

    def test_blind_syncs_once(self):
        gh = CountingSync(self.w.dir)
        self.assertEqual(self.run_with(gh, "4")[0], 0)
        self.assertEqual(gh.synced, ["trunk"])

    def test_a_failed_sync_writes_nothing(self):
        gh = CountingSync(self.w.dir, fail=True)
        before = self.snapshot()
        self.assertEqual(self.run_with(gh, "4"), (2, "", "blind: sync_base trunk: git fetch failed (exit 128)\n"))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(gh.synced, ["trunk"])

    def test_stale_clone_pairs_against_the_synced_base(self):
        remote = self.w.origin(behind=True)
        self.assertNotEqual(self.w.head("trunk"), remote)
        self.assertIsNone(gitstate.show(self.w.root, "trunk", SHOT3))
        code, out, err = self.run_with(GitSync(self.w.dir), "4")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.old_side().get(SHOT3), ORIGIN_SHOT3.encode())
        self.assertEqual(self.w.head("trunk"), remote)

    def test_clean_twin_pairs_against_the_local_base(self):
        remote = self.w.origin(behind=False)
        self.assertEqual(self.w.head("trunk"), remote)
        code, out, err = self.run_with(GitSync(self.w.dir), "4")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.old_side().get(SHOT3), gitstate.show(self.w.root, remote, SHOT3).encode())
        self.assertEqual(self.w.head("trunk"), remote)


if __name__ == "__main__":
    unittest.main()
