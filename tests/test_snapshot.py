"""Tests for kit/lib/snapshot.py: the snapshot of a watched topic in the worktree of `researcher` and
`trend-scout`, written at launch, outside git, and removed when the run ends (brief section 16)."""

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import cmd_launch, github, gitstate, production, snapshot  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "snapshot"
FAKE = ROOT / "tests" / "fixtures" / "harness" / "fake-harness"
FACTORY = ROOT / "kit" / "factory"
NOW = "2026-11-12T12:00:00Z"
TOKEN = "tok-snapshot-0d4e-do-not-print"
ACCOUNTS = ("acct-one", "acct-two")
BRANCH = "research/RS-1-researcher-1"
WORKTREE = "researcher-1-1"
CARDS = ["research/trends/harbour-tales.tiktok.tsv", "research/trends/harbour-tales.youtube.tsv"]
SNAPSHOT = [".snapshot/tiktok.ranking.txt", ".snapshot/tiktok.read.json",
            ".snapshot/youtube.ranking.txt", ".snapshot/youtube.read.json"]
CURRENT = Path("reads") / "harbour-tales" / "youtube" / "20261112T090000Z.json"
STEP = {"match": "You are the researcher",
        "result": {"marker": "READY", "summary": "The harbour-tales trends are judged.",
                   "evidence": [{"criterion": "topic researched", "result": "pass",
                                 "command": "cat research/harbour-tales.md"}]},
        "files": {"research/harbour-tales.md": "# harbour-tales\n\n- A lighthouse that hums. [inferred]\n"}}


class World:
    """The snapshot production on branch `trunk`, a recorded GitHub, the listing harness, a state directory."""

    def __init__(self, test):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        test.addCleanup(shutil.rmtree, self.tmp)
        self.github = self.tmp / "github"
        patch = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                                             "FACTORY_NOW": NOW, "FACTORY_GITHUB": str(self.github)})
        patch.start()
        test.addCleanup(patch.stop)
        os.environ.pop("FACTORY_LAUNCH_WAIT", None)
        self.root = self.tmp / "show"
        shutil.copytree(FIX / "production", self.root)
        self.git("init", "-q", "-b", "trunk")
        self.commit("the production")
        shutil.copytree(FIX / "github", self.github)
        self.harness = self.tmp / "harness"
        self.harness.mkdir()
        for path in (FAKE, FIX / "listing-harness"):
            shutil.copy2(path, self.harness / path.name)
        (self.harness / "fake-harness.json").write_text(json.dumps([STEP]) + "\n")
        secrets = self.tmp / "secrets"
        (secrets / "inference.primary").mkdir(parents=True)
        for name in ACCOUNTS:
            path = secrets / "inference.primary" / name
            path.write_text(TOKEN + "\n")
            path.chmod(0o600)
        self.state = self.tmp / "state"
        shutil.copytree(FIX / "state", self.state)
        values = {"STATE_DIR": self.state, "SECRETS_DIR": secrets, "HARNESS": self.harness / "listing-harness",
                  "MODEL_MAKER": "maker-model-1", "MODEL_CHECKER": "checker-model-1",
                  "ACCOUNTS_MAKER": ", ".join(ACCOUNTS), "ACCOUNTS_CHECKER": ", ".join(ACCOUNTS)}
        (self.root / "production.local.env").write_text("".join(f"{k}={v}\n" for k, v in values.items()))
        self.p = production.load(self.root)
        self.gh = github.Recorded(self.github)

    def git(self, *args):
        return gitstate.git(self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", *args)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def launch(self):
        """``(exit code, stdout, stderr)`` of ``factory launch researcher 1 --root <root> --wait``."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_launch.main(["researcher", "1", "--root", str(self.root), "--wait"])
        return code, out.getvalue(), err.getvalue()

    def listing(self):
        path = self.harness / "listing.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def posted(self):
        """The bodies posted as `researcher` during the test, on any issue or pull."""
        return [c["body"] for path in sorted((self.github / "comments").glob("*.json"))
                for c in json.loads(path.read_text()) if c["role"] == "researcher" and c["created_at"] == NOW]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class WorkerSeesIt(unittest.TestCase):
    """Behaviour 1."""

    def test_the_snapshot_and_both_cards(self):
        w = World(self)
        code, out, err = w.launch()
        self.assertEqual((code, err), (0, ""), out)
        [seen] = w.listing()
        self.assertEqual([f for f in seen["files"] if f.startswith(".snapshot/")], SNAPSHOT)
        self.assertEqual([f for f in seen["files"] if f.startswith("research/trends/")], CARDS)
        self.assertEqual(seen["sha256"][".snapshot/youtube.read.json"], sha256(w.state / CURRENT))
        ranked = subprocess.run([sys.executable, str(FACTORY), "trends", "harbour-tales", "youtube",
                                 "--root", str(w.root)], capture_output=True, timeout=60, check=True).stdout
        self.assertEqual(seen["sha256"][".snapshot/youtube.ranking.txt"], hashlib.sha256(ranked).hexdigest())

        # Twins: the topic no longer watched, then a platform with no read.
        w = World(self)
        (w.root / "research" / "topics" / "harbour-tales.json").unlink()
        w.commit("the topic is no longer watched")
        code, out, err = w.launch()
        self.assertEqual((code, err), (0, ""), out)
        [seen] = w.listing()
        self.assertEqual([f for f in seen["files"] if f.startswith((".snapshot/", "research/trends/"))], [])

        w = World(self)
        shutil.rmtree(w.state / "reads" / "harbour-tales" / "tiktok")
        code, out, err = w.launch()
        self.assertEqual((code, err), (0, ""), out)
        [seen] = w.listing()
        self.assertEqual([f for f in seen["files"] if f.startswith(".snapshot/")],
                         [".snapshot/youtube.ranking.txt", ".snapshot/youtube.read.json"])
        self.assertEqual([f for f in seen["files"] if f.startswith("research/trends/")], CARDS[1:])


class RemovedInNoCommit(unittest.TestCase):
    """Behaviours 2 and 3."""

    def test_removed_and_dropped_none(self):
        w = World(self)
        code, out, err = w.launch()
        self.assertEqual((code, err), (0, ""), out)
        self.assertFalse((w.state / "worktrees" / WORKTREE / ".snapshot").exists())
        [body] = w.posted()
        self.assertEqual(body.split("\n")[0], "[RESEARCHER] READY")
        self.assertIn("Dropped (outside writes): none\n", body)
        names = w.git("log", "--all", "--name-only", "--format=").split()
        self.assertEqual([n for n in names if n.startswith(".snapshot")], [])

        w = World(self)
        with mock.patch.object(snapshot, "remove", lambda worktree: None):
            code, out, err = w.launch()
        self.assertEqual((code, err), (0, ""), out)
        [body] = w.posted()
        self.assertIn("Dropped (outside writes): " + ", ".join(SNAPSHOT) + "\n", body)

    def test_no_count_in_git(self):
        w = World(self)
        code, out, err = w.launch()
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(sorted(w.git("show", "--name-only", "--format=", BRANCH).split()),
                         ["research/harbour-tales.md"] + CARDS)
        shown = w.git("show", BRANCH)
        read = json.loads((w.state / CURRENT).read_text())
        for video in read["videos"]:
            self.assertNotIn(video["title"], shown)
            for key in ("views", "likes"):
                if video[key] is not None:
                    self.assertNotRegex(shown, rf"\b{video[key]}\b", f"{video['id']} {key}")


class BadReadRefuses(unittest.TestCase):
    """Behaviour 4; its twin is behaviour 1."""

    def test_not_an_object(self):
        w = World(self)
        (w.state / CURRENT).write_text("[]\n")
        code, out, err = w.launch()
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, "launch: snapshot: trends: reads/harbour-tales/youtube/20261112T090000Z.json: "
                              "not a JSON object\n")
        self.assertFalse((w.state / "worktrees").exists())
        self.assertFalse((w.state / "runs").exists())
        self.assertEqual(w.git("branch", "--list", "research/*").strip(), "")
        self.assertEqual(w.gh.comments(1), [])
        self.assertEqual(w.listing(), [])
        self.assertNotIn(str(w.state), out + err)


class OnlyTheTwoRoles(unittest.TestCase):
    """Behaviour 5."""

    def test_empty(self):
        w = World(self)
        self.assertEqual(snapshot.ROLES, ("researcher", "trend-scout"))
        self.assertEqual(snapshot.build(w.p, "critic", "harbour-tales"), {})
        self.assertEqual(snapshot.build(w.p, "researcher", "no-such-topic"), {})
        self.assertEqual(sorted(snapshot.build(w.p, "researcher", "harbour-tales")),
                         [name.split("/")[1] for name in SNAPSHOT])


if __name__ == "__main__":
    unittest.main()
