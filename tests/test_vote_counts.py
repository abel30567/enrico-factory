"""Tests that a `[BRAINTRUST] VOTE` the launcher posts counts as a verdict (ISSUE-73).

`finish.render` ends a comment with `reviewed head <sha> manifest <m>` exactly when `markers.is_verdict`
calls its first line a verdict, so the vote carries the line `markers.counts` asks for, and a stage whose
judge is `braintrust` locks through `factory tick` after one braintrust launch.

The world: a production made by `factory init --rooms story`, the pipeline of
tests/fixtures/vote_counts/pipeline.json (`bible` with judge `braintrust`, then `premise`), one ticket
(tests/fixtures/vote_counts/github/issues/1.json), the Recorded adapter and the fake harness with the three
steps of tests/fixtures/vote_counts/fake-harness.json. Three ticks give the state "voted": the keeper's
READY, the reviewer's APPROVED and the braintrust's VOTE on pull 2.
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import (cmd_init, cmd_lock, cmd_roles, cmd_tick, finish, gitstate, github,  # noqa: E402
                     harness, markers, production, tickets)

FIX = ROOT / "tests" / "fixtures" / "vote_counts"
FAKE = ROOT / "tests" / "fixtures" / "harness" / "fake-harness"
HEAD = "a1" * 20
NOT_A_VERDICT = (False, "marker: not a lint, checker or judge verdict")
NO_LINE = (False, "reviewed: no reviewed head line")
VOTE_BODY = ("[BRAINTRUST] VOTE 6-1\n\nSeven seats voted.\n\n- seat 1: it fits the arc\n- seat 7: too early\n"
             "Launch: 2-1\nreviewed head {head} manifest none\n")
TOTALS = "tick: {} steps, {} need, {} wait, {} failed\n"

EVIDENCE = [{"criterion": "done", "result": "pass", "command": "cat"}]
# verb -> (actor, kind, result besides marker and summary)
RESULTS = {
    "READY": ("CANON-KEEPER", "maker", {"evidence": EVIDENCE}),
    "ADDRESSED": ("CANON-KEEPER", "maker", {"evidence": EVIDENCE, "addressed": [
        {"note": "B1", "by": "canon-reviewer", "action": "changed", "text": "Fixed."}]}),
    "BLOCKED": ("CANON-KEEPER", "maker", {"evidence": EVIDENCE, "blocked": {
        "missing": "a name", "tried": "the canon", "resume_from": "bible/A-1-canon-keeper-1"}}),
    "APPROVED": ("CANON-REVIEWER", "checker", {"evidence": EVIDENCE}),
    "NOTES": ("CANON-REVIEWER", "checker", {"evidence": EVIDENCE, "notes": [
        {"id": "B1", "severity": "Blocker", "text": "Contradicts the canon.", "evidence": "line 2"}]}),
    "PREFERS A": ("BRAINTRUST", "judge", {"prefers": {"choice": "A", "cited": []}}),
    "PREFERS B": ("BRAINTRUST", "judge", {"prefers": {"choice": "B", "cited": []}}),
    "NO PREFERENCE": ("BRAINTRUST", "judge", {"prefers": {"choice": "none", "cited": []}}),
    "VOTE": ("BRAINTRUST", "judge", {"vote": {"for": 6, "against": 1, "reasons": ["seat 1: it fits"]}}),
    "SYMPTOMS": ("ANALYST", "analyst", {"notes": [
        {"id": "I1", "severity": "Issue", "text": "Viewers left early.", "evidence": "retention"}]}),
    "RULING": ("SHOWRUNNER", "orchestrator", {}),
    "SELECTED": ("SHOWRUNNER", "orchestrator", {"selected": 4}),
}
ROLES = {"canon-keeper": "maker", "canon-reviewer": "checker", "braintrust": "judge", "analyst": "analyst",
         "showrunner": "orchestrator"}
STAGE = {"maker": "canon-keeper", "checker": "canon-reviewer", "judge": "braintrust", "lint": []}


def run_for(actor, kind):
    return types.SimpleNamespace(meta={"actor": actor, "kind": kind, "blind": False, "pull": 2, "head_sha": HEAD,
                                       "branch": "bible/A-1-canon-keeper-1", "id": "2-1"})


def body_for(verb):
    actor, kind, extra = RESULTS[verb]
    result = {"marker": verb, "summary": "Done.", **extra}
    return finish.render(None, run_for(actor, kind), result, None, [] if kind == "maker" else None, HEAD, "none")


def counts_alone(body):
    c = {"id": 1, "created_at": "2026-11-11T12:03:00Z", "body": body}
    pr = {"head": HEAD, "manifest": "none", "commits": [{"sha": HEAD, "parents": []}], "base_shas": [],
          "comments": [c]}
    return markers.counts(c, pr, STAGE, ROLES)


def last_line(body):
    return body.rstrip("\n").split("\n")[-1]


class RenderAndCounts(unittest.TestCase):
    """Behaviour 3: what `render` writes and what `counts` reads agree, for every verb a result may carry."""

    def test_every_verb(self):
        for verb in harness.MARKERS:
            with self.subTest(verb=verb):
                actor, kind, extra = RESULTS[verb]
                self.assertEqual(harness.validate_result(kind, actor, {"marker": verb, "summary": "Done.", **extra}),
                                 [])
                body = body_for(verb)
                m = markers.parse(body)
                self.assertIsNotNone(m)
                if markers.is_verdict(m):
                    self.assertEqual(markers.reviewed(body), (HEAD, "none"))
                    self.assertEqual(counts_alone(body), (True, "counts"))
                else:
                    self.assertIsNone(markers.reviewed(body))
                    self.assertEqual(last_line(body), f"head {HEAD} manifest none")
                    self.assertEqual(counts_alone(body), NOT_A_VERDICT)

    def test_fixed_lists(self):
        for verb in ("APPROVED", "NOTES", "PREFERS A", "PREFERS B", "NO PREFERENCE", "VOTE"):
            self.assertTrue(markers.is_verdict(markers.parse(body_for(verb))), verb)
        for verb in ("READY", "ADDRESSED", "BLOCKED"):
            self.assertFalse(markers.is_verdict(markers.parse(body_for(verb))), verb)

    def test_the_launchers_blocked(self):
        body = finish.render(None, run_for("BRAINTRUST", "judge"), None, "a valid result (timed out)", None, HEAD,
                             "none")
        self.assertEqual(body.split("\n")[0], "[BRAINTRUST] BLOCKED")
        self.assertEqual(last_line(body), f"head {HEAD} manifest none")

    def test_finish_reads_the_predicate(self):
        with mock.patch.object(markers, "is_verdict", lambda m: m is not None and m.verb == "READY"):
            self.assertEqual(last_line(body_for("READY")), f"reviewed head {HEAD} manifest none")
            self.assertEqual(last_line(body_for("APPROVED")), f"head {HEAD} manifest none")

    def test_counts_reads_the_predicate(self):
        body = body_for("VOTE")
        self.assertEqual(counts_alone(body), (True, "counts"))
        with mock.patch.object(markers, "is_verdict", lambda m: False):
            self.assertEqual(counts_alone(body), NOT_A_VERDICT)


class World:
    """The production of the module docstring, ticked to the state voted."""

    def __init__(self, case):
        self.case = case
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        case.addCleanup(shutil.rmtree, self.tmp)
        self.minute = 0
        self.show = self.tmp / "show"
        self.github = self.tmp / "github"
        for sub in ("show", "github/issues", "github/pulls", "github/comments", "state", "harness", "secrets"):
            (self.tmp / sub).mkdir(parents=True)
        env = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "FACTORY_GITHUB": str(self.github),
               "FACTORY_LAUNCH_WAIT": "1", "FACTORY_NOW": self.now()}
        patch = mock.patch.dict(os.environ, env)
        patch.start()
        case.addCleanup(patch.stop)

        self.git("init", "-q", "-b", "main")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            case.assertEqual(cmd_init.main([str(self.show), "--rooms", "story"]), 0)
        kit = self.show / ".factory"
        shutil.copyfile(FIX / "pipeline.json", kit / "pipeline.json")
        for path in sorted((kit / "formats").iterdir()):
            if path.name != "episode-circle.md":
                path.unlink()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cmd_roles.main(["--kit", str(kit), "--check"])
        case.assertEqual((code, out.getvalue().strip()), (0, "OK"))

        env_file = self.show / "production.env"
        values = {"REPO": "fixture/show", "MODEL_MAKER": "fake-maker", "MODEL_CHECKER": "fake-checker",
                  "MODEL_OTHER": "fake-other"}
        lines = [f"{line.split('=', 1)[0]}={values[line.split('=', 1)[0]]}" if line.split("=", 1)[0] in values
                 else line for line in env_file.read_text().splitlines()]
        env_file.write_text("\n".join(lines) + "\n")
        fake = self.tmp / "harness" / "fake-harness"
        shutil.copy2(FAKE, fake)
        shutil.copyfile(FIX / "fake-harness.json", self.tmp / "harness" / "fake-harness.json")
        token = self.tmp / "secrets" / "inference.primary" / "acct-one"
        token.parent.mkdir()
        token.write_text("token-for-tests\n")
        token.chmod(0o600)
        local = {"STATE_DIR": self.tmp / "state", "SECRETS_DIR": self.tmp / "secrets", "HARNESS": fake,
                 "ACCOUNTS_MAKER": "acct-one", "ACCOUNTS_CHECKER": "acct-one", "ACCOUNTS_OTHER": "acct-one",
                 "SANDBOX": "off", "GATEWAY": "off"}
        (self.show / "production.local.env").write_text("".join(f"{k}={v}\n" for k, v in local.items()))
        self.commit(self.show, "the production")
        shutil.copyfile(FIX / "github" / "issues" / "1.json", self.github / "issues" / "1.json")
        self.gh = github.Recorded(self.github)

        for _ in range(3):
            self.ticked("--apply")
        found = [(c["id"], c["body"].split("\n")[0]) for n in (1, 2) for c in self.gh.comments(n)]
        case.assertEqual(found, [(1, "[SHOWRUNNER] LAUNCH canon-keeper 1-1"), (2, "[CANON-KEEPER] READY"),
                                 (3, "[SHOWRUNNER] LAUNCH canon-reviewer 2-1"), (4, "[CANON-REVIEWER] APPROVED"),
                                 (5, "[SHOWRUNNER] LAUNCH braintrust 2-1"), (6, "[BRAINTRUST] VOTE 6-1")])

    # -- time, git and the recorded GitHub

    def now(self):
        return f"2026-11-11T12:{self.minute:02d}:00Z"

    def tock(self):
        self.minute += 1
        os.environ["FACTORY_NOW"] = self.now()

    def git(self, *args, cwd=None):
        return gitstate.git(cwd or self.show, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid",
                            *args)

    def commit(self, where, message):
        # Empty the index first, so `add` hashes every file instead of trusting a copied fixture's mtime.
        self.git("rm", "-r", "-q", "--cached", "--ignore-unmatch", ".", cwd=where)
        self.git("add", "-A", cwd=where)
        self.git("commit", "-q", "-m", message, cwd=where)

    def post(self, number, body, role):
        self.tock()
        return self.gh.post(number, body, role)

    def rewrite(self, comment_id, body):
        path = self.github / "comments" / "2.json"
        found = json.loads(path.read_text())
        for c in found:
            if c["id"] == comment_id:
                c["body"] = body
        path.write_text(json.dumps(found, indent=1, sort_keys=True) + "\n")

    def store(self):
        return {p.relative_to(self.github).as_posix(): p.read_bytes()
                for p in sorted(self.github.rglob("*")) if p.is_file()}

    def ref(self):
        return self.gh.pull(2)["head_ref"]

    def head(self):
        return gitstate.head(self.show, self.ref())

    # -- commands and state

    def run(self, command, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = command.main([*args, "--root", str(self.show)])
        return code, out.getvalue(), err.getvalue()

    def ticked(self, *args):
        self.tock()
        code, out, err = self.run(cmd_tick, *args)
        self.case.assertEqual((code, err), (0, ""), out)
        return out

    def lock(self):
        return self.run(cmd_lock)[1]

    def state(self):
        p = production.load(self.show)
        state = tickets.load(p, github.Recorded(self.github), self.show)
        return p, state.pulls[2], production.stage(p, "story", "bible")

    def vote(self):
        [vote] = [c for c in self.gh.comments(2) if c["id"] == 6]
        return vote

    def vote_counts(self):
        p, pull, entry = self.state()
        return markers.counts(self.vote(), pull["git"], entry, p.kinds), pull["summary"]


def old_body(body):
    """``body`` with its last line as `finish` wrote it before ISSUE-73: ``head ...`` without ``reviewed``."""
    lines = body.rstrip("\n").split("\n")
    assert lines[-1].startswith("reviewed head ")
    return "\n".join(lines[:-1] + [lines[-1][len("reviewed "):]]) + "\n"


class LauncherVote(unittest.TestCase):
    """Behaviour 4: the vote the launcher posts carries the line and counts."""

    def test_the_vote_counts_and_the_pull_is_lockable(self):
        w = World(self)
        p, pull, entry = w.state()
        vote = w.vote()
        head = gitstate.head(w.show, pull["head_ref"])
        self.assertEqual(vote["role"], "braintrust")
        self.assertEqual(vote["body"], VOTE_BODY.format(head=head))
        self.assertEqual(markers.counts(vote, pull["git"], entry, p.kinds), (True, "counts"))
        self.assertEqual(pull["summary"]["judges"], {"braintrust": {
            "id": 6, "created_at": "2026-11-11T12:03:00Z", "actor": "BRAINTRUST", "verb": "VOTE"}})
        self.assertEqual(w.lock(), "PR #2 #1 story/bible A-1: LOCKABLE\n")

    def test_seeded_a_vote_without_the_line_does_not_count(self):
        w = World(self)
        p, pull, entry = w.state()
        vote = dict(w.vote())
        vote["body"] = old_body(vote["body"])
        self.assertEqual(markers.counts(vote, pull["git"], entry, p.kinds), NO_LINE)


class Tick(unittest.TestCase):
    """Behaviour 5: tick launches the braintrust once and moves on."""

    def test_tick_four_locks_and_tick_five_does_nothing(self):
        w = World(self)
        head = w.head()
        out = w.ticked("--apply")
        self.assertEqual(out, "STEP #1 lock PR #2\n" + TOTALS.format(1, 0, 0, 0))
        self.assertEqual(w.gh.pull(2)["state"], "merged")
        self.assertEqual(w.gh.issue(1)["state"], "closed")
        comments = w.gh.comments(2)
        launches = [c for c in comments if c["body"].startswith("[SHOWRUNNER] LAUNCH braintrust")]
        self.assertEqual(len(launches), 1)
        last = comments[-1]
        self.assertEqual((last["id"], last["role"], last["body"]), (
            7, "program:lock", f"[SHOWRUNNER] LOCK\n\nlocked head {head} manifest none\n"
                               "relied on: canon-reviewer APPROVED #4; braintrust VOTE #6\n"))
        self.assertEqual(w.git("status", "--porcelain"), "")

        before = w.store()
        self.assertEqual(w.ticked("--apply"), TOTALS.format(0, 0, 0, 0))
        self.assertEqual(w.store(), before)

    def test_seeded_a_vote_without_the_line_is_launched_again(self):
        w = World(self)
        w.rewrite(6, old_body(w.vote()["body"]))
        self.assertEqual(w.lock(), "PR #2 #1 story/bible A-1: NO 1 rule(s)\n"
                                   "  rule 3 judges: judge braintrust has no counting verdict\n")
        self.assertEqual(w.ticked().splitlines()[0], "STEP #1 launch braintrust on PR #2")


class Stale(unittest.TestCase):
    """Behaviour 6: a stale vote still does not count."""

    def test_a_vote_older_than_the_makers_addressed(self):
        w = World(self)
        w.post(2, "[CANON-KEEPER] ADDRESSED\n\nChanged a word.\n", "canon-keeper")
        found, summary = w.vote_counts()
        self.assertEqual(found, (False, "order: not newer than the maker's last ADDRESSED (comment 7)"))
        self.assertIsNone(summary["judges"]["braintrust"])

    def test_a_vote_on_an_older_head(self):
        w = World(self)
        ref, old = w.ref(), w.head()
        tree = w.tmp / "later"
        w.git("worktree", "add", "-q", "--detach", str(tree), old)
        (tree / "bible" / "canon" / "later.md").write_text("# Later\n\nThe lamp is lit at dusk.\n")
        w.commit(tree, "a later commit")
        new = gitstate.head(tree)
        w.git("worktree", "remove", str(tree))
        w.git("update-ref", f"refs/heads/{ref}", new, old)
        self.assertEqual(w.git("rev-list", "--parents", "-n", "1", new).split(), [new, old])
        found, summary = w.vote_counts()
        self.assertEqual(found, (False, f"head: commit {new} after {old} is not a merge of the base"))
        self.assertIsNone(summary["judges"]["braintrust"])
        self.assertEqual(w.ticked().splitlines()[0], "STEP #1 launch canon-reviewer on PR #2")


if __name__ == "__main__":
    unittest.main()
