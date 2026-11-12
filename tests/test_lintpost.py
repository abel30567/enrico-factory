"""Tests for `factory lint --post <pr>` (kit/lib/lintpost.py and kit/lib/cmd_lint.py).

GitHub is a recorded directory copied from tests/fixtures/lintpost/github/. The git repository is built in a
temporary directory from tests/fixtures/lintpost/production/ on branch `trunk`, with one branch per pull
carrying the files of tests/fixtures/lintpost/branch/ (or defect/, for a seeded defect).
"""

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import cmd_lint, gitstate, github, lint, lintpost, markers, production  # noqa: E402
from kit.lib.lintpost import PostError  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "lintpost"
REVIEWED = re.compile(r"reviewed head [0-9a-f]{40} manifest ([0-9a-f]{64}|none)")


class World:
    """A production repository and a recorded GitHub directory, both temporary."""

    def __init__(self, test, overlay="branch"):
        patch = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                                             "FACTORY_NOW": "2026-11-11T11:00:00Z"})
        patch.start()
        test.addCleanup(patch.stop)
        tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, tmp)
        self.root = tmp / "show"
        shutil.copytree(FIX / "production", self.root)
        self.git("init", "-q", "-b", "trunk")
        self.commit("the production")
        self.dir = tmp / "github"
        shutil.copytree(FIX / "github", self.dir)
        for path in sorted((self.dir / "pulls").glob("*.json")):
            ref = json.loads(path.read_text())["head_ref"]
            self.git("checkout", "-q", "-b", ref, "trunk")
            self.overlay(overlay)
            self.commit("work on " + ref)
        self.git("checkout", "-q", "trunk")
        self.gh = github.Recorded(self.dir)

    def git(self, *args):
        return gitstate.git(self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", *args)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def overlay(self, name):
        shutil.copytree(FIX / name, self.root, dirs_exist_ok=True)

    def p(self):
        return production.load(self.root)

    def head(self, number):
        return gitstate.head(self.root, self.pull(number)["head_ref"])

    def pull(self, number):
        return json.loads((self.dir / "pulls" / f"{number}.json").read_text())

    def set_pull(self, number, **fields):
        self._write(f"pulls/{number}.json", {**self.pull(number), **fields})

    def _write(self, rel, data):
        (self.dir / rel).write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")

    def comments_raw(self, number):
        return (self.dir / "comments" / f"{number}.json").read_text()

    def add_comment(self, number, body, role, created_at):
        comments = json.loads(self.comments_raw(number))
        comments.append({"id": max(c["id"] for c in comments) + 1, "created_at": created_at,
                         "role": role, "body": body})
        self._write(f"comments/{number}.json", comments)

    def freeze(self, state):
        self._write("issues/9.json", {"body": "Stop.\n", "created_at": "2026-11-11T08:30:00Z",
                                      "labels": ["factory:freeze"], "number": 9, "state": state,
                                      "title": "Freeze"})

    def lint(self, *args):
        """`factory lint ...` in process: (exit code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"FACTORY_GITHUB": str(self.dir)}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_lint.main(["--root", str(self.root), *args])
        return code, out.getvalue(), err.getvalue()


def lines(body):
    return body.splitlines()


class Post(unittest.TestCase):
    def test_clean_head_posts_one_counting_pass(self):
        w = World(self)
        code, out, err = w.lint("--post", "2")
        self.assertEqual((code, err), (0, ""))
        comments = w.gh.comments(2)
        self.assertEqual(len(comments), 2)
        posted = comments[-1]
        self.assertEqual(posted["role"], "program:lint")
        self.assertEqual(lines(posted["body"])[0], "[LINT] PASS")
        self.assertEqual(posted["body"], out)
        head = w.head(2)
        manifest = gitstate.manifest(w.root, "trunk", w.pull(2)["head_ref"])
        self.assertRegex(manifest, r"^[0-9a-f]{64}$")
        self.assertEqual(lines(posted["body"])[-1], f"reviewed head {head} manifest {manifest}")
        self.assertEqual(markers.reviewed(posted["body"]), (head, manifest))
        pull = w.gh.pull(2)
        state = gitstate.pull_state(w.root, pull, comments)
        entry = production.stage(w.p(), "story", "research")
        self.assertEqual(markers.counts(posted, state, entry, w.p().kinds), (True, "counts"))

    def test_body_is_the_marker_then_the_stage_line_then_reviewed(self):
        w = World(self)
        body, results = lintpost.run(w.p(), w.gh, w.root, 2)
        head = w.head(2)
        manifest = gitstate.manifest(w.root, "trunk", w.pull(2)["head_ref"])
        self.assertEqual(body, lint.marker(results) + "\nChecks required by stage research: provenance\n"
                         f"reviewed head {head} manifest {manifest}\n")
        self.assertEqual(sorted(results), ["provenance"])

    def test_factory_command_runs_post(self):
        w = World(self)
        env = dict(os.environ, FACTORY_GITHUB=str(w.dir), PYTHONDONTWRITEBYTECODE="1")
        done = subprocess.run([sys.executable, str(ROOT / "kit" / "factory"), "lint", "--post", "2"],
                              capture_output=True, text=True, env=env, cwd=w.root)
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(done.stdout.splitlines()[0], "[LINT] PASS")
        self.assertEqual(w.gh.comments(2)[-1]["body"], done.stdout)

    def test_seeded_defect_on_head_fails_with_a_blocker(self):
        w = World(self, overlay="defect")
        code, out, _ = w.lint("--post", "2")
        self.assertEqual(code, 1)
        body = w.gh.comments(2)[-1]["body"]
        self.assertEqual(lines(body)[0], "[LINT] FAIL")
        self.assertIn("| provenance | FAIL | 1 finding |", lines(body))
        self.assertTrue(any(line.startswith("- B1 provenance research/topic-a.md:4 ") for line in lines(body)))
        self.assertRegex(lines(body)[-1], REVIEWED)

    def test_working_directory_defect_is_not_checked(self):
        w = World(self)
        w.git("checkout", "-q", w.pull(2)["head_ref"])
        shutil.copytree(FIX / "defect", w.root, dirs_exist_ok=True)
        (w.root / "research" / "untracked.md").write_text("- An untagged claim.\n")
        self.assertEqual(lint.run(w.root, only=["provenance"])["provenance"].status, lint.FAIL)
        code, out, _ = w.lint("--post", "2")
        self.assertEqual(code, 0)
        self.assertEqual(lines(w.gh.comments(2)[-1]["body"])[0], "[LINT] PASS")

    def test_check_the_running_kit_lacks_is_error(self):
        w = World(self)
        self.assertTrue((w.root / ".factory" / "lib" / "lints" / "pacing.py").is_file())
        self.assertNotIn("pacing", lint.discover())
        code, out, _ = w.lint("--post", "6")
        self.assertEqual(code, 2)
        body = w.gh.comments(6)[-1]["body"]
        self.assertEqual(lines(body)[0], "[LINT] ERROR")
        self.assertIn("| pacing | ERROR | no such check |", lines(body))
        self.assertIn("| provenance | PASS |  |", lines(body))
        self.assertIn("Checks required by stage digest: provenance, pacing", lines(body))

    def test_temporary_tree_is_removed_after_a_run_and_after_a_raise(self):
        w = World(self)
        seen = []
        real = lint.run

        def spy(root, only=None):
            seen.append(Path(root))
            self.assertTrue((Path(root) / "research" / "topic-a.md").is_file())
            return real(root, only)

        with mock.patch.object(lintpost.lint, "run", spy):
            lintpost.run(w.p(), w.gh, w.root, 2)

        def boom(root, only=None):
            seen.append(Path(root))
            raise RuntimeError("boom")

        with mock.patch.object(lintpost.lint, "run", boom):
            with self.assertRaises(RuntimeError):
                lintpost.run(w.p(), w.gh, w.root, 2)
        self.assertEqual(len(seen), 2)
        for tree in seen:
            self.assertFalse(tree.exists(), tree)
            self.assertNotEqual(tree, w.root)


class Idempotent(unittest.TestCase):
    def test_second_run_on_the_same_head_posts_nothing(self):
        w = World(self)
        self.assertEqual(w.lint("--post", "2")[0], 0)
        before = w.comments_raw(2)
        code, out, err = w.lint("--post", "2")
        self.assertEqual((code, out), (0, ""))
        self.assertEqual(err, f"lint: already posted for {w.head(2)[:12]}\n")
        self.assertEqual(w.comments_raw(2), before)

    def test_second_run_exits_with_the_first_verdict(self):
        for overlay, pr, expected in (("defect", 2, 1), ("branch", 6, 2)):
            with self.subTest(overlay=overlay, pr=pr):
                w = World(self, overlay=overlay)
                self.assertEqual(w.lint("--post", str(pr))[0], expected)
                code, _, err = w.lint("--post", str(pr))
                self.assertEqual(code, expected)
                self.assertTrue(err.startswith("lint: already posted for "), err)
                self.assertEqual(len(w.gh.comments(pr)), 2)

    def test_new_commit_and_new_ready_post_again(self):
        w = World(self)
        self.assertEqual(w.lint("--post", "2")[0], 0)
        first = w.head(2)
        w.git("checkout", "-q", w.pull(2)["head_ref"])
        (w.root / "research" / "topic-b.md").write_text("- The ferry runs twice a day. [invented]\n")
        w.commit("more research")
        w.git("checkout", "-q", "trunk")
        w.add_comment(2, "[SCOUT] READY\n", "scout", "2026-11-11T12:00:00Z")
        with mock.patch.dict(os.environ, {"FACTORY_NOW": "2026-11-11T13:00:00Z"}):
            code, _, err = w.lint("--post", "2")
        self.assertEqual((code, err), (0, ""))
        lint_comments = [c for c in w.gh.comments(2) if c["role"] == "program:lint"]
        self.assertEqual(len(lint_comments), 2)
        self.assertIn(first, lint_comments[0]["body"])
        self.assertIn(w.head(2), lint_comments[1]["body"])
        self.assertNotEqual(first, w.head(2))

    def test_new_ready_on_the_same_head_posts_again(self):
        w = World(self)
        w.lint("--post", "2")
        w.add_comment(2, "[SCOUT] READY\n", "scout", "2026-11-11T12:00:00Z")
        with mock.patch.dict(os.environ, {"FACTORY_NOW": "2026-11-11T13:00:00Z"}):
            code, _, err = w.lint("--post", "2")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(len([c for c in w.gh.comments(2) if c["role"] == "program:lint"]), 2)

    def test_a_lint_marker_from_another_role_is_not_read(self):
        w = World(self)
        body, _ = lintpost.run(w.p(), w.gh, w.root, 2)
        w.add_comment(2, body, "scout", "2026-11-11T10:30:00Z")
        code, _, err = w.lint("--post", "2")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(w.gh.comments(2)[-1]["role"], "program:lint")


class Refusals(unittest.TestCase):
    """Each refusal exits 2 with one stderr line and adds no comment; its clean twin posts."""

    def refuse(self, setup, args, pr, message):
        w = World(self)
        setup(w)
        before = w.comments_raw(pr)
        code, out, err = w.lint(*args)
        self.assertEqual((code, out, err), (2, "", message + "\n"))
        self.assertEqual(w.comments_raw(pr), before)

    def twin(self, setup, pr):
        w = World(self)
        setup(w)
        before = w.comments_raw(pr)
        code, out, err = w.lint("--post", str(pr))
        self.assertEqual((code, err), (0, ""))
        self.assertNotEqual(w.comments_raw(pr), before)

    def test_closed_pull(self):
        self.refuse(lambda w: w.set_pull(2, state="closed"), ["--post", "2"], 2,
                    "lint: pull request #2 is not open")
        self.twin(lambda w: w.set_pull(2, state="open"), 2)

    def test_pull_without_ticket_line(self):
        self.refuse(lambda w: w.set_pull(2, body="Work.\n", ticket=None), ["--post", "2"], 2,
                    "lint: pull request #2 names no tracked ticket")
        self.twin(lambda w: w.set_pull(2, body="Work.\nTicket: #1\n", ticket=1), 2)

    def test_ticket_that_is_not_tracked(self):
        def untrack(w):
            issue = json.loads((w.dir / "issues" / "1.json").read_text())
            w._write("issues/1.json", {**issue, "labels": [l for l in issue["labels"] if l != "tracked"]})
        self.refuse(untrack, ["--post", "2"], 2, "lint: pull request #2 names no tracked ticket")

    def test_stage_with_empty_lint_list(self):
        def with_lint(names):
            def setup(w):
                path = w.root / ".factory" / "pipeline.json"
                pipeline = json.loads(path.read_text())
                for entry in pipeline:
                    if entry["stage"] == "notes":
                        entry["lint"] = names
                path.write_text(json.dumps(pipeline))
            return setup
        self.refuse(with_lint([]), ["--post", "4"], 4, "lint: stage notes names no lint")
        self.twin(with_lint(["provenance"]), 4)

    def test_post_with_only(self):
        self.refuse(lambda w: None, ["--post", "2", "--only", "provenance"], 2,
                    "lint: --post cannot be combined with --only or --json")
        self.twin(lambda w: None, 2)

    def test_post_with_json(self):
        self.refuse(lambda w: None, ["--post", "2", "--json"], 2,
                    "lint: --post cannot be combined with --only or --json")
        self.twin(lambda w: None, 2)

    def test_frozen_production(self):
        self.refuse(lambda w: w.freeze("open"), ["--post", "2"], 2, "lint: frozen")
        self.twin(lambda w: w.freeze("closed"), 2)

    def test_stage_not_in_pipeline(self):
        def elsewhere(w):
            issue = json.loads((w.dir / "issues" / "1.json").read_text())
            labels = sorted([l for l in issue["labels"] if not l.startswith("stage:")] + ["stage:render"])
            w._write("issues/1.json", {**issue, "labels": labels})
        self.refuse(elsewhere, ["--post", "2"], 2, "lint: stage story/render is not in the pipeline")

    def test_missing_pull_is_a_github_error(self):
        self.refuse(lambda w: None, ["--post", "99"], 2, "lint: pull #99: not found")

    def test_no_production_is_a_production_error(self):
        w = World(self)
        (w.root / "production.env").unlink()
        code, out, err = w.lint("--post", "2")
        self.assertEqual((code, out, err), (2, "", "lint: no production.env at or above the start directory\n"))

    def test_run_raises_post_error(self):
        w = World(self)
        w.set_pull(2, state="merged")
        with self.assertRaises(PostError):
            lintpost.run(w.p(), w.gh, w.root, 2)


class PlainLint(unittest.TestCase):
    def test_plain_lint_ignores_a_freeze_and_loads_no_production(self):
        w = World(self)
        w.freeze("open")
        with mock.patch.object(production, "load", side_effect=AssertionError("loaded")), \
                mock.patch.object(github, "connect", side_effect=AssertionError("connected")):
            code, out, err = w.lint("--only", "provenance")
        self.assertEqual((code, out, err), (0, "provenance PASS\n", ""))


if __name__ == "__main__":
    unittest.main()
