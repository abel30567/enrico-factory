import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kit.lib import github
from kit.lib.github import Gh, GitHubError, Recorded, connect

FIXTURES = Path(__file__).parent / "fixtures" / "github"
CLEAN = FIXTURES / "clean"
NOW = "2026-11-11T12:00:00Z"
REPO = "owner/project"
HERMETIC_GIT = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

_real_run = subprocess.run
_gh_calls = []
_patch = None


def _guarded_run(argv, *args, **kwargs):
    if argv and argv[0] == "gh":
        _gh_calls.append(list(argv))
        raise AssertionError(f"a test started gh: {argv}")
    return _real_run(argv, *args, **kwargs)


def setUpModule():
    global _patch
    _patch = mock.patch("subprocess.run", _guarded_run)
    _patch.start()


def tearDownModule():
    _patch.stop()
    if _gh_calls:
        raise AssertionError(f"tests started gh: {_gh_calls}")


def gh_fixture(name):
    return (FIXTURES / "gh" / name).read_text()


def tree(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def log_lines(root: Path) -> list:
    path = root / "log.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        env = mock.patch.dict(os.environ, {"FACTORY_NOW": NOW, **HERMETIC_GIT})
        env.start()
        self.addCleanup(env.stop)

    def copy_clean(self, name="gh-state") -> Path:
        d = self.tmp / name
        shutil.copytree(CLEAN, d)
        return d


class RecordedReadTest(TmpCase):
    def setUp(self):
        super().setUp()
        self.d = self.copy_clean()
        self.before = tree(self.d)
        self.r = Recorded(self.d)

    def tearDown(self):
        self.assertEqual(tree(self.d), self.before, "a read wrote a file")

    def test_issues(self):
        self.assertEqual([i["number"] for i in self.r.issues()], [1])
        self.assertEqual([i["number"] for i in self.r.issues("closed")], [2])
        self.assertEqual([i["number"] for i in self.r.issues("all")], [1, 2])
        self.assertEqual(self.r.issues()[0], {
            "number": 1, "title": "Draft the opening scene", "body": "The lighthouse keeper finds the map.\n",
            "state": "open", "labels": ["stage:script", "tracked"], "created_at": "2026-11-01T09:00:00Z"})

    def test_pulls(self):
        self.assertEqual([p["number"] for p in self.r.pulls()], [3])
        self.assertEqual(self.r.pulls("merged"), [])
        self.assertEqual(self.r.pulls("closed"), [])
        self.assertEqual(sorted(self.r.pulls("all")[0]), sorted(
            ["number", "title", "body", "state", "labels", "head_ref", "base_ref", "ticket", "created_at", "merged_at"]))
        self.assertEqual(self.r.pulls("all")[0]["ticket"], 1)

    def test_bad_state(self):
        with self.assertRaises(GitHubError):
            self.r.issues("merged")
        with self.assertRaises(GitHubError):
            self.r.pulls("draft")

    def test_issue_and_pull(self):
        self.assertEqual(self.r.issue(2)["state"], "closed")
        self.assertEqual(self.r.pull(3)["head_ref"], "feat/1-opening")
        for get, n in ((self.r.issue, 3), (self.r.issue, 9), (self.r.pull, 1)):
            with self.assertRaises(GitHubError) as e:
                get(n)
            self.assertIn(f"#{n}", str(e.exception))

    def test_comments(self):
        got = self.r.comments(3)
        self.assertEqual([c["id"] for c in got], [2, 3])
        self.assertEqual(got[1], {"id": 3, "created_at": "2026-11-02T10:05:00Z", "role": "program:lint",
                                  "body": "[LINT] PASS\nall checks pass\n"})
        self.assertEqual(self.r.comments(2), [])


class RecordedWriteTest(TmpCase):
    def setUp(self):
        super().setUp()
        self.d = self.copy_clean()
        self.r = Recorded(self.d)

    def file(self, rel):
        return json.loads((self.d / rel).read_text())

    def test_post(self):
        c = self.r.post(3, "[WRITER] ADDRESSED\nfixed", "writer")
        self.assertEqual(c, {"id": 4, "created_at": NOW, "role": "writer", "body": "[WRITER] ADDRESSED\nfixed\n"})
        self.assertEqual(self.file("comments/3.json")[-1], c)
        self.assertEqual(self.r.comments(3)[-1], c)
        self.assertEqual(log_lines(self.d), [
            {"op": "post", "number": 3, "body": "[WRITER] ADDRESSED\nfixed", "role": "writer"}])
        c2 = self.r.post(2, "note\n", "program:lint")
        self.assertEqual((c2["id"], c2["body"]), (5, "note\n"))
        self.assertEqual(self.file("comments/2.json"), [c2])

    def test_post_requires_role_and_target(self):
        for role in ("", None, 3):
            with self.assertRaises(GitHubError):
                self.r.post(3, "x", role)
        with self.assertRaises(GitHubError):
            self.r.post(9, "x", "writer")
        self.assertFalse((self.d / "log.jsonl").exists())

    def test_create_issue(self):
        n = self.r.create_issue("New scene", "Body.", ["tracked", "b", "tracked"])
        self.assertEqual(n, 4)
        self.assertEqual(self.file("issues/4.json"), {
            "number": 4, "title": "New scene", "body": "Body.", "state": "open",
            "labels": ["b", "tracked"], "created_at": NOW})
        self.assertEqual(log_lines(self.d), [{"op": "create_issue", "number": 4, "title": "New scene",
                                              "body": "Body.", "labels": ["tracked", "b", "tracked"]}])
        text = (self.d / "issues/4.json").read_text()
        self.assertEqual(text, json.dumps(self.file("issues/4.json"), indent=1, sort_keys=True) + "\n")

    def test_close_issue(self):
        self.r.close_issue(1)
        self.assertEqual(self.file("issues/1.json")["state"], "closed")
        self.assertEqual(log_lines(self.d), [{"op": "close_issue", "number": 1}])
        with self.assertRaises(GitHubError):
            self.r.close_issue(3)

    def test_labels(self):
        self.r.add_label(1, "a-new")
        self.assertEqual(self.file("issues/1.json")["labels"], ["a-new", "stage:script", "tracked"])
        self.r.add_label(3, "risky")
        self.assertEqual(self.file("pulls/3.json")["labels"], ["risky"])
        self.r.remove_label(1, "tracked")
        self.assertEqual(self.file("issues/1.json")["labels"], ["a-new", "stage:script"])
        self.r.remove_label(1, "never-there")
        self.assertEqual(self.file("issues/1.json")["labels"], ["a-new", "stage:script"])
        self.assertEqual([(x["op"], x["number"], x["label"]) for x in log_lines(self.d)], [
            ("add_label", 1, "a-new"), ("add_label", 3, "risky"),
            ("remove_label", 1, "tracked"), ("remove_label", 1, "never-there")])

    def test_open_pull(self):
        n = self.r.open_pull("feat/2-font", "main", "Font", "Why\nTicket: #2\nTicket: #7")
        self.assertEqual(n, 4)
        self.assertEqual(self.file("pulls/4.json"), {
            "number": 4, "title": "Font", "body": "Why\nTicket: #2\nTicket: #7", "state": "open", "labels": [],
            "head_ref": "feat/2-font", "base_ref": "main", "ticket": 2, "created_at": NOW, "merged_at": None})
        self.assertEqual(log_lines(self.d), [{"op": "open_pull", "number": 4, "head_ref": "feat/2-font",
                                              "base_ref": "main", "title": "Font",
                                              "body": "Why\nTicket: #2\nTicket: #7"}])
        self.assertEqual(self.r.open_pull("feat/x", "main", "t", "Ticket: one\nTicket: #2 extra"), 5)
        self.assertIsNone(self.file("pulls/5.json")["ticket"])

    def test_open_pull_twice_is_one_pull(self):
        a = self.r.open_pull("feat/2-font", "main", "Font", "Ticket: #2")
        b = self.r.open_pull("feat/2-font", "main", "Font again", "Ticket: #2")
        self.assertEqual(a, b)
        self.assertEqual(sorted(p.name for p in (self.d / "pulls").iterdir()), ["3.json", "4.json"])
        self.assertEqual(self.r.open_pull("feat/1-opening", "main", "dup", "x"), 3)
        self.assertEqual(len(log_lines(self.d)), 1)

    def test_push_only_logs(self):
        before = tree(self.d)
        self.r.push(self.tmp / "anywhere", "feat/1-opening")
        after = tree(self.d)
        self.assertEqual(after.pop("log.jsonl"), b'{"number": null, "op": "push", "ref": "feat/1-opening"}\n')
        self.assertEqual(after, before)

    def test_subdirectories_created_on_first_write(self):
        d = self.tmp / "empty"
        d.mkdir()
        r = Recorded(d)
        self.assertEqual((r.issues("all"), r.pulls("all"), r.comments(1)), ([], [], []))
        self.assertEqual(r.create_issue("t", "b", []), 1)
        self.assertEqual(r.open_pull("h", "main", "t", "b"), 2)
        self.assertEqual(r.post(1, "x", "writer")["id"], 1)
        self.assertEqual(sorted(tree(d)), ["comments/1.json", "issues/1.json", "log.jsonl", "pulls/2.json"])

    def test_now_falls_back_to_utc(self):
        with mock.patch.dict(os.environ):
            del os.environ["FACTORY_NOW"]
            n = self.r.create_issue("t", "b", [])
        self.assertRegex(self.file(f"issues/{n}.json")["created_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


class RecordedDeterminismTest(TmpCase):
    def writes(self, r: Recorded):
        r.create_issue("Second scene", "Body two.", ["tracked"])
        r.post(1, "[ORCHESTRATOR] RULING\nkeep it", "orchestrator")
        r.add_label(4, "stage:script")
        n = r.open_pull("feat/4-second", "main", "Second scene", "Ticket: #4\n")
        r.post(n, "[WRITER] READY", "writer")
        r.remove_label(1, "tracked")
        r.remove_label(1, "absent")
        r.push(self.tmp, "feat/4-second")
        r.close_issue(2)
        r.post(3, "[LINT] FAIL\nB1 missing tag", "program:lint")

    def test_same_calls_same_bytes(self):
        a, b = self.copy_clean("a"), self.copy_clean("b")
        self.writes(Recorded(a))
        self.writes(Recorded(b))
        ta, tb = tree(a), tree(b)
        self.assertEqual(len(log_lines(a)), 10)
        self.assertEqual(sorted(ta), sorted(tb))
        for rel in ta:
            self.assertEqual(ta[rel], tb[rel], rel)
        self.assertNotIn(".tmp", "".join(ta))


class RecordedMergeTest(TmpCase):
    def git(self, *args, check=True):
        done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t.invalid", "-C", str(self.repo),
                               *args], capture_output=True, text=True)
        if check and done.returncode != 0:
            raise AssertionError(done.stderr)
        return done.stdout.strip()

    def setUp(self):
        super().setUp()
        self.d = self.copy_clean()
        self.r = Recorded(self.d)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        (self.repo / "scene.md").write_text("one\ntwo\n")
        self.git("add", "scene.md")
        self.git("commit", "-q", "-m", "base")
        self.git("checkout", "-q", "-b", "feat/1-opening")
        (self.repo / "scene.md").write_text("one\ntwo changed\n")
        self.git("commit", "-q", "-am", "change")
        self.tip = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        self.base = self.git("rev-parse", "HEAD")

    def assert_refused(self, fragment):
        with self.assertRaises(GitHubError) as e:
            self.r.merge(self.repo, 3, self.tip)
        self.assertIn("merge #3", str(e.exception))
        self.assertIn(fragment, str(e.exception))
        self.assertEqual(self.r.pull(3)["state"], json.loads((CLEAN / "pulls/3.json").read_text())["state"])
        return e.exception

    def test_merges_and_returns_new_head(self):
        sha = self.r.merge(self.repo, 3, self.tip)
        self.assertEqual(sha, self.git("rev-parse", "HEAD"))
        self.assertEqual(self.git("rev-parse", "HEAD^1", "HEAD^2").split(), [self.base, self.tip])
        self.assertEqual(self.git("log", "-1", "--format=%s|%an|%ae|%aI|%cI"),
                         "Merge pull request #3|factory|factory@factory.invalid|"
                         "2026-11-11T12:00:00Z|2026-11-11T12:00:00Z".replace("Z", "+00:00"))
        pull = self.r.pull(3)
        self.assertEqual((pull["state"], pull["merged_at"]), ("merged", NOW))
        self.assertEqual(log_lines(self.d), [{"op": "merge", "number": 3, "head_sha": self.tip}])
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_refuses_pull_not_open(self):
        p = json.loads((self.d / "pulls/3.json").read_text())
        (self.d / "pulls/3.json").write_text(json.dumps({**p, "state": "closed"}))
        with self.assertRaises(GitHubError) as e:
            self.r.merge(self.repo, 3, self.tip)
        self.assertIn("not open", str(e.exception))
        self.assertEqual(self.git("rev-parse", "HEAD"), self.base)

    def test_refuses_tip_not_head_sha(self):
        with self.assertRaises(GitHubError) as e:
            self.r.merge(self.repo, 3, self.base)
        self.assertIn("merge #3: branch tip of feat/1-opening is not " + self.base, str(e.exception))
        self.assertEqual(self.git("rev-parse", "HEAD"), self.base)
        self.assertEqual(self.r.pull(3)["state"], "open")

    def test_refuses_checked_out_branch_not_base(self):
        self.git("checkout", "-q", "feat/1-opening")
        e = self.assert_refused("checked-out branch is not the base main")
        self.assertNotIn("branch tip", str(e))

    def test_refuses_dirty_index(self):
        (self.repo / "other.md").write_text("staged\n")
        self.git("add", "other.md")
        self.assert_refused("index is not clean")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.base)

    def test_conflict_aborts_and_leaves_pull_open(self):
        (self.repo / "scene.md").write_text("one\ntwo on main\n")
        self.git("commit", "-q", "-am", "main moves")
        head = self.git("rev-parse", "HEAD")
        with self.assertRaises(GitHubError) as e:
            self.r.merge(self.repo, 3, self.tip)
        self.assertIn("git merge of feat/1-opening failed (exit 1)", str(e.exception))
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.git("rev-parse", "-q", "--verify", "MERGE_HEAD", check=False), "")
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.r.pull(3)["state"], "open")
        self.assertEqual(log_lines(self.d), [])


class FakeRun:
    """Records each call and answers from a queue of (returncode, stdout, stderr)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        code, out, err = self.answers.pop(0)
        return subprocess.CompletedProcess(argv, code, out, err)

    def argvs(self):
        return [argv for argv, _ in self.calls]

    def stdin(self, n=0):
        return json.loads(self.calls[n][1]["input"])


def ok(out=""):
    return (0, out, "")


class GhTest(unittest.TestCase):
    def gh(self, *answers):
        self.fake = FakeRun(*answers)
        return Gh(REPO, run=self.fake)

    def tearDown(self):
        for argv, kwargs in getattr(self, "fake", FakeRun()).calls:
            self.assertEqual(set(kwargs), {"input", "capture_output", "text"})
            self.assertTrue(kwargs["capture_output"] and kwargs["text"])
            self.assertIsInstance(argv, list)

    def test_issues(self):
        got = self.gh(ok(gh_fixture("issue_list.json"))).issues("all")
        self.assertEqual(self.fake.argvs(), [["gh", "issue", "list", "-R", REPO, "--state", "all", "--limit", "1000",
                                              "--json", "number,title,body,state,labels,createdAt"]])
        self.assertEqual(self.fake.calls[0][1]["input"], "")
        self.assertEqual(got, [
            {"number": 1, "title": "Draft the opening scene", "body": "The keeper.", "state": "open",
             "labels": ["stage:script", "tracked"], "created_at": "2026-11-01T09:00:00Z"},
            {"number": 2, "title": "Pick a title card font", "body": "Two options.", "state": "closed",
             "labels": ["tracked"], "created_at": "2026-11-01T09:30:00Z"}])
        self.assertEqual([i["number"] for i in self.gh(ok(gh_fixture("issue_list.json"))).issues("closed")], [2])
        self.assertEqual(self.fake.argvs()[0][6], "closed")

    def test_pulls(self):
        got = self.gh(ok(gh_fixture("pr_list.json"))).pulls("all")
        self.assertEqual(self.fake.argvs(), [[
            "gh", "pr", "list", "-R", REPO, "--state", "all", "--limit", "1000", "--json",
            "number,title,body,state,labels,headRefName,baseRefName,createdAt,mergedAt"]])
        self.assertEqual([(p["number"], p["state"], p["ticket"]) for p in got],
                         [(3, "open", 1), (4, "merged", None), (5, "closed", 2)])
        self.assertEqual(got[1], {
            "number": 4, "title": "Merged fix", "body": "No ticket here", "state": "merged", "labels": [],
            "head_ref": "feat/4-fix", "base_ref": "main", "ticket": None, "created_at": "2026-11-02T11:00:00Z",
            "merged_at": "2026-11-02T12:00:00Z"})
        self.assertEqual(got[0]["labels"], ["risky"])
        # gh's "closed" also lists merged pulls; the adapter keeps only state "closed".
        self.assertEqual([p["number"] for p in self.gh(ok(gh_fixture("pr_list.json"))).pulls("closed")], [5])

    def test_bad_state_runs_nothing(self):
        g = self.gh()
        with self.assertRaises(GitHubError):
            g.issues("merged")
        self.assertEqual(self.fake.calls, [])

    def test_issue(self):
        got = self.gh(ok(gh_fixture("issue_view.json"))).issue(1)
        self.assertEqual(self.fake.argvs(), [["gh", "issue", "view", "1", "-R", REPO, "--json",
                                              "number,title,body,state,labels,createdAt"]])
        self.assertEqual(got["labels"], ["stage:script", "tracked"])
        self.assertEqual(got["state"], "open")

    def test_pull(self):
        got = self.gh(ok(gh_fixture("pr_view.json"))).pull(3)
        self.assertEqual(self.fake.argvs(), [["gh", "pr", "view", "3", "-R", REPO, "--json",
                                              "number,title,body,state,labels,headRefName,baseRefName,createdAt,mergedAt"]])
        self.assertEqual(got, {
            "number": 3, "title": "Opening scene, first draft", "body": "Intro line\nTicket: #1\nTicket: #9",
            "state": "open", "labels": ["risky"], "head_ref": "feat/1-opening", "base_ref": "main", "ticket": 1,
            "created_at": "2026-11-02T10:00:00Z", "merged_at": None})

    def test_unknown_number(self):
        with self.assertRaises(GitHubError) as e:
            self.gh((1, "", "GraphQL: Could not resolve to an issue")).issue(99)
        self.assertEqual(str(e.exception), "issue #99: exit 1")

    def test_comments(self):
        got = self.gh(ok(gh_fixture("comments_pages.json"))).comments(3)
        self.assertEqual(self.fake.argvs(), [["gh", "api", "--paginate", "--slurp",
                                              f"repos/{REPO}/issues/3/comments"]])
        self.assertEqual(got, [
            {"id": 10, "created_at": "2026-11-02T10:01:00Z", "role": "", "body": "a person's note, no role line"},
            {"id": 20, "created_at": "2026-11-02T10:05:00Z", "role": "writer",
             "body": "[WRITER] READY\nhead 1111111111111111111111111111111111111111\n"},
            {"id": 30, "created_at": "2026-11-02T10:05:00Z", "role": "program:lint",
             "body": "[LINT] PASS\nall checks pass\n"}])

    def test_comment_without_role_line(self):
        page = [[{"id": 1, "created_at": NOW, "body": "plain\n<!-- other -->"}]]
        got = self.gh(ok(json.dumps(page))).comments(1)
        self.assertEqual(got, [{"id": 1, "created_at": NOW, "role": "", "body": "plain\n<!-- other -->"}])

    def test_post(self):
        answer = {"id": 7, "created_at": NOW, "body": "[WRITER] READY\nx\n<!-- factory:role writer -->"}
        got = self.gh(ok(json.dumps(answer))).post(3, "[WRITER] READY\nx", "writer")
        self.assertEqual(self.fake.argvs(), [["gh", "api", "-X", "POST", f"repos/{REPO}/issues/3/comments",
                                              "--input", "-"]])
        self.assertEqual(self.fake.stdin(), {"body": "[WRITER] READY\nx\n<!-- factory:role writer -->"})
        self.assertEqual(got, {"id": 7, "created_at": NOW, "role": "writer", "body": "[WRITER] READY\nx\n"})
        self.gh(ok(json.dumps(answer))).post(3, "ends\n", "program:lint")
        self.assertEqual(self.fake.stdin(), {"body": "ends\n<!-- factory:role program:lint -->"})

    def test_post_rejects_bad_role_before_running(self):
        g = self.gh()
        for role in ("", "Writer", "a b", "x\n-->", None):
            with self.assertRaises(GitHubError):
                g.post(3, "x", role)
        self.assertEqual(self.fake.calls, [])

    def test_post_then_comments_round_trip_matches_recorded(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"FACTORY_NOW": NOW}):
            shutil.copytree(CLEAN, Path(tmp) / "s")
            for body in ("[WRITER] READY\nline", "[WRITER] READY\nline\n", "", "a\n\n"):
                with self.subTest(body=body):
                    g = self.gh(ok(json.dumps({"id": 7, "created_at": NOW, "body": "placeholder"})))
                    g.post(3, body, "program:lint")
                    sent = self.fake.stdin()["body"]
                    g = self.gh(ok(json.dumps([[{"id": 7, "created_at": NOW, "body": sent}]])))
                    back = g.comments(3)[0]
                    want = Recorded(Path(tmp) / "s").post(3, body, "program:lint")
                    self.assertEqual((back["role"], back["body"]), (want["role"], want["body"]))

    def test_create_issue(self):
        n = self.gh(ok('{"number": 12, "title": "t"}')).create_issue("Title", "Body text", ["tracked"])
        self.assertEqual(n, 12)
        self.assertEqual(self.fake.argvs(), [["gh", "api", "-X", "POST", f"repos/{REPO}/issues", "--input", "-"]])
        self.assertEqual(self.fake.stdin(), {"title": "Title", "body": "Body text", "labels": ["tracked"]})

    def test_close_issue(self):
        self.assertIsNone(self.gh(ok('{"number": 1}')).close_issue(1))
        self.assertEqual(self.fake.argvs(), [["gh", "api", "-X", "PATCH", f"repos/{REPO}/issues/1", "--input", "-"]])
        self.assertEqual(self.fake.stdin(), {"state": "closed"})

    def test_add_label(self):
        self.assertIsNone(self.gh(ok("[]")).add_label(1, "stage:script"))
        self.assertEqual(self.fake.argvs(), [["gh", "api", "-X", "POST", f"repos/{REPO}/issues/1/labels",
                                              "--input", "-"]])
        self.assertEqual(self.fake.stdin(), {"labels": ["stage:script"]})

    def test_remove_label(self):
        self.assertIsNone(self.gh(ok("[]")).remove_label(1, "stage:script / a b"))
        self.assertEqual(self.fake.argvs(), [["gh", "api", "-X", "DELETE",
                                              f"repos/{REPO}/issues/1/labels/stage%3Ascript%20%2F%20a%20b"]])
        self.assertEqual(self.fake.calls[0][1]["input"], "")

    def test_remove_absent_label_is_not_an_error(self):
        missing = (1, '{"message":"Label does not exist","documentation_url":"x","status":"404"}',
                   "gh: Label does not exist (HTTP 404)")
        self.assertIsNone(self.gh(missing).remove_label(1, "gone"))
        for answer in ((1, '{"message":"Forbidden","status":"403"}', "HTTP 403"), (1, "not json", "boom")):
            with self.assertRaises(GitHubError) as e:
                self.gh(answer).remove_label(1, "x")
            self.assertEqual(str(e.exception), "remove_label #1: exit 1")

    def test_open_pull_creates(self):
        n = self.gh(ok(gh_fixture("pr_list.json")), ok('{"number": 6}')).open_pull(
            "feat/2-font", "main", "Font", "Ticket: #2")
        self.assertEqual(n, 6)
        self.assertEqual(self.fake.argvs()[1], ["gh", "api", "-X", "POST", f"repos/{REPO}/pulls", "--input", "-"])
        self.assertEqual(self.fake.argvs()[0][:8], ["gh", "pr", "list", "-R", REPO, "--state", "open", "--limit"])
        self.assertEqual(self.fake.stdin(1), {"title": "Font", "body": "Ticket: #2", "head": "feat/2-font",
                                              "base": "main"})

    def test_open_pull_reuses_open_head(self):
        n = self.gh(ok(gh_fixture("pr_list.json"))).open_pull("feat/1-opening", "main", "x", "y")
        self.assertEqual(n, 3)
        self.assertEqual(len(self.fake.calls), 1)

    def test_push(self):
        self.assertIsNone(self.gh(ok()).push("/work/repo", "feat/1-opening"))
        self.assertEqual(self.fake.argvs(), [["git", "-C", "/work/repo", "push", "origin", "feat/1-opening"]])

    def test_merge(self):
        sha = self.gh(ok(gh_fixture("pr_view.json")), ok(gh_fixture("merge.json"))).merge(
            "/work/repo", 3, "a" * 40)
        self.assertEqual(sha, "9" * 40)
        self.assertEqual(self.fake.argvs(), [
            ["gh", "pr", "view", "3", "-R", REPO, "--json",
             "number,title,body,state,labels,headRefName,baseRefName,createdAt,mergedAt"],
            ["gh", "api", "-X", "PUT", f"repos/{REPO}/pulls/3/merge", "--input", "-"]])
        self.assertEqual(self.fake.stdin(1), {"sha": "a" * 40, "merge_method": "merge"})

    def test_merge_refused_by_server(self):
        g = self.gh(ok(gh_fixture("pr_view.json")), (1, '{"message":"Head branch was modified"}', "HTTP 409"))
        with self.assertRaises(GitHubError) as e:
            g.merge("/work/repo", 3, "a" * 40)
        self.assertEqual(str(e.exception), "merge #3: exit 1")
        self.assertEqual(len(self.fake.calls), 2)

    def test_no_body_text_in_arguments(self):
        secret = "BODY-TEXT-must-stay-on-stdin"
        g = self.gh(*[ok('{"number": 1, "id": 1, "created_at": "x", "body": ""}')] * 3,
                    ok("[]"), ok('{"number": 2}'))
        g.post(1, secret, "writer")
        g.create_issue(secret, secret, [secret])
        g.add_label(1, secret)
        g.open_pull("feat/x", "main", secret, secret)
        for argv in self.fake.argvs():
            self.assertFalse([a for a in argv if secret in a], argv)
        self.assertTrue(all(secret in kwargs["input"] for _, kwargs in self.fake.calls if kwargs["input"]))

    def test_error_message_never_holds_stderr(self):
        leak = "ghp_DISTINCTIVE_stderr_text_0123"
        calls = [lambda g: g.issues(), lambda g: g.pull(3), lambda g: g.comments(3),
                 lambda g: g.post(3, "x", "writer"), lambda g: g.create_issue("t", "b", []),
                 lambda g: g.close_issue(3), lambda g: g.add_label(3, "l"), lambda g: g.remove_label(3, "l"),
                 lambda g: g.push("/r", "main")]
        for call in calls:
            with self.assertRaises(GitHubError) as e:
                call(self.gh((1, leak, leak)))
            self.assertNotIn(leak, str(e.exception))
            self.assertIn("exit 1", str(e.exception))

    def test_environment_passes_through_unchanged(self):
        self.gh(ok("[]")).issues()
        self.assertNotIn("env", self.fake.calls[0][1])


class GhMergeNotOpenTest(unittest.TestCase):
    """ISSUE-227 behaviour 2: a pull that is not open is refused before GitHub is asked to merge it."""

    def test_closed_and_merged_pulls_are_refused_after_one_read(self):
        for state in ("closed", "merged"):
            with self.subTest(state=state):
                fake = FakeRun(ok(gh_fixture("pr_view.json").replace('"OPEN"', f'"{state.upper()}"')))
                with self.assertRaises(GitHubError) as e:
                    Gh(REPO, run=fake).merge("/work/repo", 3, "a" * 40)
                self.assertEqual(str(e.exception), f"merge #3: pull is {state}, not open")
                self.assertEqual([argv[:3] for argv in fake.argvs()], [["gh", "pr", "view"]])


class SyncCase(TmpCase):
    """A bare ``origin`` for GitHub, ``seed`` for GitHub's side of a merge, ``prod`` for the production's clone."""

    def git(self, where, *args, check=True):
        done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t.invalid", "-C", str(where), *args],
                              capture_output=True, text=True)
        if check and done.returncode != 0:
            raise AssertionError(done.stderr)
        return done.stdout.strip()

    def setUp(self):
        super().setUp()
        self.origin, self.seed, self.prod = self.tmp / "origin.git", self.tmp / "seed", self.tmp / "prod"
        self.git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.git(self.tmp, "clone", "-q", str(self.origin), str(self.seed))
        self.git(self.seed, "symbolic-ref", "HEAD", "refs/heads/main")
        (self.seed / "scene.md").write_text("one\n")
        self.git(self.seed, "add", "scene.md")
        self.git(self.seed, "commit", "-q", "-m", "base")
        self.git(self.seed, "push", "-q", "origin", "main")
        self.git(self.seed, "checkout", "-q", "-b", "feat/1-opening")
        (self.seed / "scene.md").write_text("one\ntwo\n")
        (self.seed / "merged.md").write_text("from the pull\n")
        self.git(self.seed, "add", "scene.md", "merged.md")
        self.git(self.seed, "commit", "-q", "-m", "change")
        self.git(self.seed, "push", "-q", "origin", "feat/1-opening")
        self.pull_head = self.git(self.seed, "rev-parse", "HEAD")
        self.git(self.seed, "checkout", "-q", "main")
        self.git(self.tmp, "clone", "-q", str(self.origin), str(self.prod))
        self.base = self.git(self.prod, "rev-parse", "refs/heads/main")

    def github_merges(self) -> str:
        """GitHub merges pull 3 into main with a merge commit; its sha."""
        self.git(self.seed, "merge", "-q", "--no-ff", "-m", "Merge pull request #3", "feat/1-opening")
        self.git(self.seed, "push", "-q", "origin", "main")
        return self.git(self.seed, "rev-parse", "main")

    def local_commit(self) -> str:
        (self.prod / "ledger.md").write_text("a row GitHub lacks\n")
        self.git(self.prod, "add", "ledger.md")
        self.git(self.prod, "commit", "-q", "-m", "ledger")
        return self.git(self.prod, "rev-parse", "HEAD")

    def branches(self):
        return self.git(self.prod, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads")

    def status(self):
        return self.git(self.prod, "status", "--porcelain")

    def sync(self, base="main"):
        return Gh(REPO).sync_base(self.prod, base)


class GhSyncBaseTest(SyncCase):
    """ISSUE-227 behaviours 3 to 7: ``Gh.sync_base`` on real git."""

    def test_base_checked_out(self):
        merge = self.github_merges()
        self.assertEqual(self.sync(), merge)
        self.assertEqual(self.git(self.prod, "rev-parse", "refs/heads/main", "HEAD").split(), [merge, merge])
        self.assertEqual((self.prod / "merged.md").read_text(), "from the pull\n")
        self.assertEqual(self.status(), "")
        self.assertEqual(self.git(self.prod, "symbolic-ref", "--short", "HEAD"), "main")

    def assert_moved_base_only(self, checkout):
        self.git(self.prod, "checkout", "-q", *checkout)
        head = self.git(self.prod, "rev-parse", "HEAD")
        merge = self.github_merges()
        self.assertEqual(self.sync(), merge)
        self.assertEqual(self.git(self.prod, "rev-parse", "refs/heads/main"), merge)
        self.assertEqual(self.git(self.prod, "rev-parse", "HEAD"), head)
        self.assertEqual(self.status(), "")

    def test_another_branch_checked_out(self):
        self.assert_moved_base_only(["-b", "work"])
        self.assertEqual(self.git(self.prod, "symbolic-ref", "--short", "HEAD"), "work")

    def test_detached_head(self):
        self.assert_moved_base_only(["--detach"])
        self.assertEqual(self.git(self.prod, "symbolic-ref", "-q", "HEAD", check=False), "")

    def test_nothing_new_on_github(self):
        before = self.branches()
        self.assertEqual(self.sync(), self.base)
        self.assertEqual(self.branches(), before)

    def test_local_base_only_ahead(self):
        ahead = self.local_commit()
        before = self.branches()
        self.assertEqual(self.sync(), ahead)
        self.assertEqual(self.git(self.prod, "rev-parse", "refs/heads/main"), ahead)
        self.assertEqual(self.branches(), before)

    def test_untracked_file_is_kept(self):
        (self.prod / "notes.txt").write_text("mine\n")
        merge = self.github_merges()
        self.assertEqual(self.sync(), merge)
        self.assertEqual(self.git(self.prod, "rev-parse", "HEAD"), merge)
        self.assertEqual((self.prod / "notes.txt").read_text(), "mine\n")
        self.assertEqual(self.status(), "?? notes.txt")

    def assert_refused(self, message):
        before = (self.branches(), self.status())
        with self.assertRaises(GitHubError) as e:
            self.sync()
        self.assertEqual(str(e.exception), message)
        for path in {str(self.tmp), os.path.realpath(self.tmp)}:
            self.assertNotIn(path, str(e.exception))
        self.assertEqual((self.branches(), self.status()), before)

    def test_diverged_with_the_base_checked_out(self):
        self.local_commit()
        self.github_merges()
        self.assert_refused("sync_base main: the local branch holds 1 commit(s) GitHub does not have")

    def test_diverged_with_another_branch_checked_out(self):
        self.local_commit()
        self.git(self.prod, "checkout", "-q", "-b", "work")
        self.github_merges()
        self.assert_refused("sync_base main: the local branch holds 1 commit(s) GitHub does not have")

    def test_modified_file_the_merge_changes(self):
        self.github_merges()
        (self.prod / "scene.md").write_text("local text\n")
        self.assert_refused("sync_base main: git merge failed (exit 1)")
        self.assertEqual((self.prod / "scene.md").read_text(), "local text\n")

    def test_no_remote_origin(self):
        self.github_merges()
        self.git(self.prod, "remote", "remove", "origin")
        self.assert_refused("sync_base main: git fetch failed (exit 128)")


class RecordedSyncBaseTest(SyncCase):
    """ISSUE-227 behaviour 8."""

    def test_returns_the_local_tip_and_logs_nothing(self):
        d = self.copy_clean()
        before = tree(d)
        r = Recorded(d)
        self.assertEqual(r.sync_base(self.prod, "main"), self.base)
        self.assertEqual(log_lines(d), [])
        with self.assertRaises(GitHubError) as e:
            r.sync_base(self.prod, "trunk")
        self.assertEqual(str(e.exception), "sync_base trunk: git rev-parse failed (exit 1)")
        self.assertEqual(tree(d), before)


class SameOnBothFormsTest(SyncCase):
    """ISSUE-227 behaviour 9: ``merge`` then ``sync_base`` leave the same base on ``Gh`` and on ``Recorded``."""

    def test_merge_then_sync(self):
        twin = self.tmp / "twin"
        self.git(self.tmp, "clone", "-q", str(self.origin), str(twin))
        self.git(twin, "branch", "-q", "feat/1-opening", "origin/feat/1-opening")
        started = []

        def run(argv, **kwargs):
            started.append(argv[:3])
            if argv[0] != "gh":
                return subprocess.run(argv, **kwargs)
            if argv[1:3] == ["pr", "view"]:
                return subprocess.CompletedProcess(argv, 0, gh_fixture("pr_view.json"), "")
            self.assertEqual(json.loads(kwargs["input"])["sha"], self.pull_head)
            answer = {"sha": self.github_merges(), "merged": True, "message": "Pull Request successfully merged"}
            return subprocess.CompletedProcess(argv, 0, json.dumps(answer), "")

        g = Gh(REPO, run=run)
        sha = g.merge(self.prod, 3, self.pull_head)
        self.assertEqual([argv[0] for argv in started], ["gh", "gh"])
        self.assertEqual(self.git(self.prod, "rev-parse", "refs/heads/main"), self.base)
        self.assertEqual(g.sync_base(self.prod, "main"), sha)
        self.assertEqual(self.git(self.prod, "rev-parse", "refs/heads/main", "HEAD").split(), [sha, sha])
        self.assertEqual(self.git(self.prod, "rev-parse", f"{sha}^1", f"{sha}^2").split(),
                         [self.base, self.pull_head])
        self.assertEqual(self.status(), "")

        r = Recorded(self.copy_clean())
        recorded = r.merge(twin, 3, self.pull_head)
        self.assertEqual(r.sync_base(twin, "main"), recorded)
        self.assertEqual(self.git(twin, "rev-parse", "refs/heads/main", "HEAD").split(), [recorded, recorded])
        self.assertEqual(self.git(twin, "rev-parse", f"{recorded}^1", f"{recorded}^2").split(),
                         [self.base, self.pull_head])
        self.assertEqual(self.git(twin, "rev-parse", f"{recorded}^{{tree}}"),
                         self.git(self.prod, "rev-parse", f"{sha}^{{tree}}"))


class ConnectTest(unittest.TestCase):
    def test_factory_github_gives_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = connect({"REPO": REPO}, env={"FACTORY_GITHUB": tmp})
            self.assertIsInstance(got, Recorded)
            self.assertEqual(got.dir, Path(tmp))

    def test_missing_directory_raises_without_the_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "nope-DISTINCT")
            with self.assertRaises(GitHubError) as e:
                connect({"REPO": REPO}, env={"FACTORY_GITHUB": missing})
            self.assertNotIn("DISTINCT", str(e.exception))

    def test_repo_gives_gh_never_run(self):
        with mock.patch.object(github.subprocess, "run", side_effect=AssertionError("ran")) as run:
            for env in ({}, {"FACTORY_GITHUB": ""}):
                got = connect({"REPO": REPO}, env=env)
                self.assertIsInstance(got, Gh)
                self.assertEqual(got.repo, REPO)
            run.assert_not_called()

    def test_no_repo_raises(self):
        for settings in ({}, {"REPO": ""}):
            with self.assertRaises(GitHubError) as e:
                connect(settings, env={})
            self.assertEqual(str(e.exception), "setting REPO is empty")

    def test_env_defaults_to_os_environ(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"FACTORY_GITHUB": tmp}):
            self.assertIsInstance(connect({}), Recorded)
        with mock.patch.dict(os.environ, {"FACTORY_GITHUB": ""}):
            self.assertIsInstance(connect({"REPO": REPO}), Gh)


class NoGhStartedTest(unittest.TestCase):
    def test_subprocess_run_is_guarded_for_the_whole_module(self):
        self.assertIs(subprocess.run, _guarded_run)
        with self.assertRaises(AssertionError):
            Gh(REPO).issues()
        self.assertEqual(_gh_calls.pop(), ["gh", "issue", "list", "-R", REPO, "--state", "open", "--limit",
                                           "1000", "--json", "number,title,body,state,labels,createdAt"])
        self.assertEqual(_gh_calls, [])


if __name__ == "__main__":
    unittest.main()
