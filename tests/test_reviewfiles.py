"""Tests for kit/lib/reviewfiles.py: a checker's launch places the files of its stage's job in ``review/``.

The production and GitHub are those of tests/test_launch.py (``World``); the asset store is the signed fake
S3 store of tests/test_assets.py (``AssetStore``) on 127.0.0.1, with its key in a temporary ``SECRETS_DIR``.
Pull #7 (ticket #6, stage beats, unit ep02) is given a job by a commit on its branch of
``production/ep02/beats/manifest.json``; nothing reaches another host.
"""

import hashlib
import json
import os
import stat
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from kit.lib import gitstate  # noqa: E402
from kit.lib.launch import LaunchError  # noqa: E402
from test_assets import KEY_ID, REGION, SECRET, AssetStore  # noqa: E402
from test_launch import FailingPost, World  # noqa: E402
from test_media import free_port  # noqa: E402

BRANCH = "beats/BT-2-drafter-1"
UNIT = "production/ep02/beats"
MANIFEST = f"{UNIT}/manifest.json"
JOB = "ab" * 32
POSE = b"\x89PNG\r\n\x1a\ninvented pose frame 01"  # 30 bytes
OTHER = b"\x89PNG\r\n\x1a\ninvented POSE FRAME 01"  # the same length as POSE
STATE = b'{"invented": "stage state"}\n'
PANEL = b"\x89PNG\r\n\x1a\ninvented panel bytes"
DOWN = "[DOWN] store.assets"
BAD = f"review files: {MANIFEST} is not {{job, outputs}} with outputs of {{name, sha256, bytes}}"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def output(name, data):
    return {"name": name, "sha256": sha(data), "bytes": len(data)}


class Review(unittest.TestCase):
    """A world whose pull #7 may carry a job's manifest, and an asset store that holds what ``serve`` puts in it."""

    def setUp(self):
        self.w = World(self)
        self.store = AssetStore(self)
        self.secrets = self.w.tmp / "secrets"
        self.secrets.mkdir(mode=0o700)
        key = self.secrets / "store.assets"
        key.write_text(f"{KEY_ID}\n{SECRET}\n{REGION}\n")
        key.chmod(0o600)
        self.settings()

    def settings(self, **values):
        self.w.local(**{"ASSET_STORE": self.store.url("/b"), "SECRETS_DIR": str(self.secrets), **values})

    def serve(self, *items):
        for data in items:
            self.store.objects[sha(data)] = data

    def commit(self, files):
        """Commit on pull #7's branch, from a worktree of its own, ``{path: bytes or text}``."""
        tree = self.w.tmp / "commit"
        self.w.git("worktree", "add", "-q", str(tree), BRANCH)
        for rel, data in files.items():
            path = tree / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        identity = ("-c", "user.name=tester", "-c", "user.email=tester@example.invalid")
        gitstate.git(tree, "add", "-A")
        gitstate.git(tree, *identity, "commit", "-q", "-m", "a job's manifest")
        self.w.git("worktree", "remove", str(tree))

    def manifest(self, outputs, job=JOB, extra=None):
        return {MANIFEST: json.dumps({"job": job, "outputs": outputs, "tool": "invented"}), **(extra or {})}

    def clean(self, extra=None):
        """The job of Behaviour 1: ``pose-01.png`` in the store and ``state.json`` committed."""
        self.commit(self.manifest([output("state.json", STATE), output("pose-01.png", POSE)],
                                  extra={f"{UNIT}/state.json": STATE, **(extra or {})}))

    def job_file(self, data):
        out = self.w.state / "jobs" / JOB / "out"
        out.mkdir(parents=True)
        (out / "pose-01.png").write_bytes(data)

    def prompt(self, run):
        return (run.dir / "prompt.md").read_text()

    def listed(self, directory):
        return sorted(p.name for p in directory.iterdir()) if directory.exists() else []

    def runs(self):
        return self.listed(self.w.state / "runs")

    def trees(self):
        return self.listed(self.w.state / "worktrees")

    def gets(self):
        return [(r["method"], r["path"]) for r in self.store.requests]

    def downs(self):
        return [i for i in self.w.gh.issues("open") if i["title"] == DOWN]

    def block(self, *lines):
        head = self.w.head(BRANCH)
        return "\n".join([f"Task: Review pull request #7 at head {head}.", "",
                          "Files of the job of pull request #7, from production/ep02/beats/manifest.json, "
                          "in review/ of the worktree:", *lines]) + "\n"

    def refused(self, message, role="reviewer"):
        before = self.w.snapshot()
        with self.assertRaises(LaunchError) as caught:
            self.w.prepare(role, 7)
        self.assertEqual(str(caught.exception), message)
        self.assertEqual(self.w.snapshot(), before)
        self.assertEqual(self.runs(), [])
        self.assertEqual(self.trees(), [])


class B1Clean(Review):
    def test_clean_the_store_file_is_placed_and_the_stage_file_named(self):
        self.serve(POSE)
        self.clean()
        run = self.w.prepare("reviewer", 7)
        review = run.worktree / "review"
        self.assertEqual((review / "pose-01.png").read_bytes(), POSE)
        self.assertEqual(mode(review), 0o700)
        self.assertEqual(mode(review / "pose-01.png"), 0o600)
        self.assertEqual(self.listed(review), ["pose-01.png"])
        self.assertEqual(self.gets(), [("GET", f"/b/{sha(POSE)}")])
        self.assertIn(self.block("- review/pose-01.png: 30 bytes", "In git, not fetched:",
                                 "- production/ep02/beats/state.json"), self.prompt(run))
        self.assertEqual(self.runs(), ["reviewer-7-1"])
        self.assertEqual(gitstate.git(run.worktree, "status", "--porcelain"), "?? review/\n")

    def test_every_output_in_git_lists_none(self):
        self.commit(self.manifest([output("state.json", STATE)], extra={f"{UNIT}/state.json": STATE}))
        run = self.w.prepare("reviewer", 7)
        self.assertIn(self.block("- none", "In git, not fetched:", "- production/ep02/beats/state.json"),
                      self.prompt(run))
        self.assertFalse((run.worktree / "review").exists())
        self.assertEqual(self.store.requests, [])


class B2JobFile(Review):
    def test_clean_from_the_job_output_with_no_store(self):
        self.settings(SECRETS_DIR="/nowhere/review", ASSET_STORE="")
        self.job_file(POSE)
        self.clean()
        run = self.w.prepare("reviewer", 7)
        self.assertEqual((run.worktree / "review" / "pose-01.png").read_bytes(), POSE)
        self.assertEqual(mode(run.worktree / "review" / "pose-01.png"), 0o600)
        self.assertEqual(self.store.requests, [])

    def test_seeded_other_bytes_in_the_job_output_come_from_the_store(self):
        self.job_file(OTHER)
        self.serve(POSE)
        self.clean()
        run = self.w.prepare("reviewer", 7)
        self.assertEqual((run.worktree / "review" / "pose-01.png").read_bytes(), POSE)
        self.assertEqual(self.gets(), [("GET", f"/b/{sha(POSE)}")])


class B3Missing(Review):
    def test_no_such_object(self):
        self.clean()
        self.refused("review files: pose-01.png: store has no such object")
        self.assertEqual(self.downs(), [])
        self.assertEqual(len(self.store.requests), 1)

    def test_other_bytes_served(self):
        self.serve(POSE)
        self.store.serve[sha(POSE)] = OTHER
        self.clean()
        self.refused("review files: pose-01.png: store serves other bytes")
        self.assertEqual(self.downs(), [])


class B4Outage(Review):
    def outage(self):
        self.clean()
        with self.assertRaises(LaunchError) as caught:
            self.w.prepare("reviewer", 7)
        downs = self.downs()
        self.assertEqual(len(downs), 1)
        self.assertIn("resource:down", downs[0]["labels"])
        self.assertEqual(str(caught.exception), f"resource store.assets is down (#{downs[0]['number']})")
        self.assertEqual(self.runs(), [])
        self.assertEqual(self.trees(), [])
        self.store.requests.clear()
        with self.assertRaises(LaunchError) as again:
            self.w.prepare("reviewer", 7)
        self.assertEqual(str(again.exception), str(caught.exception))
        self.assertEqual(self.store.requests, [])
        self.assertEqual(len(self.downs()), 1)

    def test_closed_port(self):
        self.settings(ASSET_STORE=f"http://127.0.0.1:{free_port()}/b")
        self.outage()

    def test_store_answers_500(self):
        self.serve(POSE)
        self.store.status["GET"] = (500, b"")
        self.outage()


class B5Settings(Review):
    def test_store_setting_empty(self):
        self.settings(ASSET_STORE="")
        self.clean()
        self.refused("review files: resource store.assets is not usable: setting ASSET_STORE is empty")
        self.assertEqual(self.store.requests, [])
        self.assertEqual(self.downs(), [])

    def test_no_key_file(self):
        (self.secrets / "store.assets").unlink()
        self.clean()
        self.refused("review files: resource store.assets is not usable: secret store.assets: missing")
        self.assertEqual(self.store.requests, [])
        self.assertEqual(self.downs(), [])


class B6BadManifest(Review):
    def bad(self, files):
        self.serve(POSE)
        self.commit(files)
        self.refused(BAD)
        self.assertEqual(self.store.requests, [])

    def test_job_not_a_digest(self):
        self.bad({MANIFEST: json.dumps({"job": "x", "outputs": []})})

    def test_bytes_a_bool(self):
        self.bad(self.manifest([{**output("pose-01.png", POSE), "bytes": True}]))

    def test_two_outputs_named_alike(self):
        self.bad(self.manifest([output("pose-01.png", POSE), output("pose-01.png", OTHER)]))

    def test_name_with_a_directory(self):
        self.bad(self.manifest([{**output("pose-01.png", POSE), "name": "frames/pose-01.png"}]))

    def test_not_json(self):
        self.bad({MANIFEST: "{not json"})


class B7Undo(Review):
    def test_failed_post_leaves_nothing(self):
        self.serve(POSE)
        self.clean()
        self.w.gh = FailingPost(self.w.dir)
        with self.assertRaisesRegex(LaunchError, r"^could not post LAUNCH on #7$"):
            self.w.prepare("reviewer", 7)
        self.assertEqual(self.gets(), [("GET", f"/b/{sha(POSE)}")])
        self.assertEqual(self.runs(), [])
        self.assertEqual(self.trees(), [])
        self.assertNotIn("reviewer-7-1", self.w.worktrees())

    def test_the_tree_has_review(self):
        self.serve(POSE)
        self.clean(extra={"review/x.md": "invented\n"})
        self.refused("review files: the pull's tree has review/")


class B8NoChange(Review):
    def test_no_manifest_task_as_today(self):
        run = self.w.prepare("reviewer", 7)
        self.assertIn(f"Task: Review pull request #7 at head {self.w.head(BRANCH)}.\n\nRules:", self.prompt(run))
        self.assertFalse((run.worktree / "review").exists())
        self.assertEqual(self.runs(), ["reviewer-7-1"])

    def test_a_maker_gets_nothing(self):
        self.serve(POSE)
        self.clean()
        run = self.w.prepare("drafter", 7)
        self.assertFalse((run.worktree / "review").exists())
        self.assertNotIn("Files of the job", self.prompt(run))
        self.assertEqual(self.store.requests, [])

    def test_a_leftover_is_not_adopted_by_a_checker(self):
        stale = self.w.state / "runs" / ".review-reviewer-7-1"
        stale.mkdir(parents=True)
        (stale / "planted.txt").write_text("stale\n")
        run = self.w.prepare("reviewer", 7)
        self.assertFalse((run.worktree / "review").exists())
        self.assertNotIn("Files of the job", self.prompt(run))
        self.assertFalse(stale.exists())

    def test_a_leftover_is_not_adopted_by_a_maker(self):
        stale = self.w.state / "runs" / ".review-drafter-7-2"
        stale.mkdir(parents=True)
        (stale / "planted.txt").write_text("stale\n")
        run = self.w.prepare("drafter", 7)
        self.assertFalse((run.worktree / "review").exists())
        self.assertNotIn("Files of the job", self.prompt(run))

    def test_a_task_file_keeps_its_text(self):
        task = self.w.tmp / "task.md"
        text = "Check the tide.\n\n\n"
        task.write_text(text)
        run = self.w.prepare("reviewer", 7, task_file=task)
        self.assertIn("Task: " + text + "\n\nRules:", self.prompt(run))


class B9BothDirectories(Review):
    def test_media_and_review_are_apart(self):
        path = self.w.root / ".factory" / "roles" / "reviewer.md"
        path.write_text(path.read_text().replace("gated: no\n", "gated: no\nmedia: cited\n"))
        self.settings()
        digest = sha(b"an invented picture job")
        out = self.w.state / "jobs" / digest / "out"
        out.mkdir(parents=True)
        (out / "panel.png").write_bytes(PANEL)
        mid = f"{digest[:16]}-panel.png"
        self.serve(POSE)
        self.clean(extra={
            "episodes/ep02/beats.md": f"The panel: media:{mid}\n",
            "media/manifest.tsv": f"id\tsha256\tbytes\tsource\n{mid}\t{sha(PANEL)}\t{len(PANEL)}\tjob {digest}\n",
        })
        run = self.w.prepare("reviewer", 7)
        self.assertEqual(self.listed(run.dir / "media"), [mid])
        self.assertEqual((run.dir / "media" / mid).read_bytes(), PANEL)
        self.assertEqual(self.listed(run.worktree / "review"), ["pose-01.png"])
        self.assertEqual((run.worktree / "review" / "pose-01.png").read_bytes(), POSE)
        head = self.w.head(BRANCH)
        self.assertIn("\n".join([
            f"Task: Review pull request #7 at head {head}.", "",
            "Pictures cited by the files of pull request #7, outside the worktree; open each with Read:",
            f"- media:{mid}: {run.dir}/media/{mid}", "",
            "Files of the job of pull request #7, from production/ep02/beats/manifest.json, in review/ of the "
            "worktree:",
            "- review/pose-01.png: 30 bytes", "In git, not fetched:", "- production/ep02/beats/state.json"]) + "\n",
            self.prompt(run))
        self.assertEqual(self.runs(), ["reviewer-7-1"])


if __name__ == "__main__":
    unittest.main()
