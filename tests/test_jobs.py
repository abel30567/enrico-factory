"""Tests for kit/lib/jobs.py: the requests and jobs of a pull request at a stage with an operator.

The production is tests/fixtures/gate/production with the operator tests/fixtures/jobs/fetcher.md added
and the stage `render` made `["painter", "fetcher"]`, a git repository on branch `trunk` in a temporary
directory with one branch per recorded pull. GitHub is a copy of tests/fixtures/gate/github: ticket #3
(render, ep01) has the open pull #4. A test commits a request and its job on the pull's branch. Nothing
reaches the network.
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import cmd_gate, gitstate, github, jobs, ledger, production, tickets  # noqa: E402

GATE = ROOT / "tests" / "fixtures" / "gate"
FETCHER = ROOT / "tests" / "fixtures" / "jobs" / "fetcher.md"
NOW = "2026-11-02T09:00:00Z"
TICKET, PULL, OTHER_PULL = 3, 4, 2
REQUEST = "frames/ep01/requests/r1.json"
JOB = "jobs/" + REQUEST
REQUEST_TEXT = '{"for": "shot-1", "prompt": "a lantern on the pier", "refs": []}\n'


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def job_for(request=REQUEST, text=REQUEST_TEXT, **changes):
    job = {"caller": "fetcher", "expected": {"compute_min": 5, "usd": 1}, "inputs": [{"path": request, "sha256": sha(text)}],
           "ticket": TICKET, "tool": "renderbox"}
    job.update(changes)
    return job


def digest(job):
    return hashlib.sha256(json.dumps(job, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class World:
    def __init__(self, test):
        tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, tmp)
        self.root = tmp / "show"
        self.dir = tmp / "github"
        patch = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                                             "FACTORY_GITHUB": str(self.dir), "FACTORY_NOW": NOW})
        patch.start()
        test.addCleanup(patch.stop)
        shutil.copytree(GATE / "production", self.root)
        shutil.copy(FETCHER, self.root / ".factory" / "roles" / "fetcher.md")
        path = self.root / ".factory" / "pipeline.json"
        entries = json.loads(path.read_text())
        for entry in entries:
            if entry["stage"] == "render":
                entry["maker"] = ["painter", "fetcher"]
        path.write_text(json.dumps(entries, indent=1) + "\n")
        self.git("init", "-q", "-b", "trunk")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "the production")
        shutil.copytree(GATE / "github", self.dir)
        for number in (OTHER_PULL, PULL):
            ref = self.ref(number)
            self.git("branch", ref, "trunk")
        self.gh = github.Recorded(self.dir)
        self.comment_id = 100

    def git(self, *args):
        return gitstate.git(self.root, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", *args)

    def ref(self, number):
        return json.loads((self.dir / "pulls" / f"{number}.json").read_text())["head_ref"]

    def on_branch(self, files, number=PULL, ref=None):
        """Commit ``files`` (a text, or None to delete) on the pull's branch."""
        self.git("checkout", "-q", ref or self.ref(number))
        for rel, text in files.items():
            path = self.root / rel
            if text is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "files")
        self.git("checkout", "-q", "trunk")

    def on_trunk(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "on trunk")

    def record(self, number, role, body):
        path = self.dir / "comments" / f"{number}.json"
        found = json.loads(path.read_text()) if path.is_file() else []
        self.comment_id += 1
        found.append({"body": body, "created_at": "2026-11-01T12:%02d:00Z" % (self.comment_id - 100),
                      "id": self.comment_id, "role": role})
        path.write_text(json.dumps(found, indent=1) + "\n")

    def verdicts(self):
        head = gitstate.head(self.root, self.ref(PULL))
        self.record(PULL, "program:lint", f"[LINT] PASS\nstructure: PASS\nreviewed head {head} manifest none\n")
        self.record(PULL, "inspector", f"[INSPECTOR] APPROVED\nfine\nreviewed head {head} manifest none\n")

    def gate(self, path=JOB):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_gate.main([path, "--pull", str(PULL), "--root", str(self.root), "--apply"])
        return code, out.getvalue(), err.getvalue()

    def ran(self, job, verdict):
        """A ``run`` row of ``job`` appended on `trunk`, as ``factory run`` writes it."""
        text = gitstate.show(self.root, "trunk", ledger.PATH) or ledger.HEADER + "\n"
        row = {"time": NOW, "kind": "run", "hash": digest(job), "ticket": TICKET, "episode": "ep01",
               "tool": "renderbox", "usd": "1", "compute_min": "5", "caller": "fetcher", "verdict": verdict,
               "cap": "-"}
        self.on_trunk(ledger.PATH, text + ledger.line(row) + "\n")

    def load(self):
        p = production.load(self.root)
        state = tickets.load(p, self.gh, self.root)
        return p, state, state.tickets[TICKET], state.pulls[PULL]

    def of(self):
        p, state, ticket, pull = self.load()
        return jobs.of(p, self.root, state, ticket, pull)


class OperatorTest(unittest.TestCase):
    def test_the_first_operator_of_the_maker_place(self):
        w = World(self)
        p = production.load(w.root)
        self.assertEqual(jobs.operator(p, production.stage(p, "production", "render")), "fetcher")

    def test_a_stage_with_no_operator(self):
        w = World(self)
        p = production.load(w.root)
        self.assertIsNone(jobs.operator(p, production.stage(p, "production", "frames")))

    def test_job_path(self):
        self.assertEqual(jobs.job_path(REQUEST), JOB)


class RequestsTest(unittest.TestCase):
    def setUp(self):
        self.w = World(self)

    def requests(self):
        p, _, _, pull = self.w.load()
        return jobs.requests(p, self.w.root, pull, "fetcher")

    def test_a_request(self):
        self.w.on_branch({REQUEST: REQUEST_TEXT, "frames/ep01/requests/a0.json": REQUEST_TEXT,
                          "requests/top.json": "{}\n"})
        self.assertEqual(self.requests(), ["frames/ep01/requests/a0.json", REQUEST, "requests/top.json"])

    def test_not_requests(self):
        self.w.on_branch({"frames/ep01/requests/deep/r1.json": "{}\n", "frames/ep01/requests.json": "{}\n",
                          "frames/ep01/requests/r1.md": "x\n", "frames/ep01/myrequests/r1.json": "{}\n"})
        self.assertEqual(self.requests(), [])

    def test_a_path_under_the_operators_writes(self):
        self.w.on_branch({JOB: "{}\n"})
        self.assertEqual(self.requests(), [])

    def test_a_request_on_the_base_only(self):
        self.w.on_trunk(REQUEST, REQUEST_TEXT)
        self.assertEqual(self.requests(), [])

    def test_a_request_deleted_on_the_branch(self):
        self.w.on_trunk(REQUEST, REQUEST_TEXT)
        self.w.git("branch", "-f", self.w.ref(PULL), "trunk")
        self.w.on_branch({REQUEST: None})
        p, _, _, pull = self.w.load()
        self.assertIn(REQUEST, pull["git"]["changed"])
        self.assertEqual(self.requests(), [])


class CoveringTest(unittest.TestCase):
    def setUp(self):
        self.w = World(self)

    def covering(self, job_text, request_text=REQUEST_TEXT):
        files = {REQUEST: request_text}
        if job_text is not None:
            files[JOB] = job_text
        self.w.on_branch(files)
        p, _, ticket, pull = self.w.load()
        return jobs.covering(self.w.root, pull, ticket, "fetcher", REQUEST)

    def test_covered(self):
        job = job_for()
        found = self.covering(json.dumps(job, indent=1))
        self.assertEqual(found, {"path": JOB, "request": REQUEST, "job": job, "hash": digest(job)})

    def test_more_inputs_after_the_request(self):
        job = job_for()
        job["inputs"].append({"path": "frames/ep01/shot-1.md", "sha256": "0" * 64})
        self.assertEqual(self.covering(json.dumps(job))["hash"], digest(job))

    def test_seeded(self):
        cases = {
            "missing file": None,
            "not JSON": "{",
            "wrong shape": json.dumps({**job_for(), "extra": 1}),
            "another ticket": json.dumps(job_for(ticket=1)),
            "another caller": json.dumps(job_for(caller="painter")),
            "stale input hash": json.dumps(job_for(text="an older request\n")),
            "another first input": json.dumps(job_for(request="frames/ep01/shot-1.md")),
            "no input": json.dumps(job_for(inputs=[])),
        }
        for name, text in cases.items():
            with self.subTest(name):
                self.w = World(self)
                self.assertIsNone(self.covering(text))

    def test_request_changed_after_the_job(self):
        self.assertIsNotNone(self.covering(json.dumps(job_for())))
        self.w.on_branch({REQUEST: REQUEST_TEXT + " "})
        p, _, ticket, pull = self.w.load()
        self.assertIsNone(jobs.covering(self.w.root, pull, ticket, "fetcher", REQUEST))


class OfTest(unittest.TestCase):
    def setUp(self):
        self.w = World(self)

    def test_no_request(self):
        self.assertIsNone(self.w.of())

    def test_a_stage_with_no_operator(self):
        self.w.on_branch({REQUEST: REQUEST_TEXT})
        path = self.w.dir / "issues" / f"{TICKET}.json"
        issue = json.loads(path.read_text())
        issue["labels"] = ["room:production", "stage:frames", "tracked", "unit:ep01"]
        path.write_text(json.dumps(issue))
        self.assertIsNone(self.w.of())

    def test_uncovered_then_covered(self):
        second = "frames/ep01/requests/r2.json"
        self.w.on_branch({REQUEST: REQUEST_TEXT, second: "{}\n"})
        self.assertEqual(self.w.of(), {"operator": "fetcher", "requests": [REQUEST, second],
                                       "uncovered": [REQUEST, second], "jobs": []})
        one, two = job_for(), job_for(request=second, text="{}\n")
        self.w.on_branch({JOB: json.dumps(one), "jobs/" + second: json.dumps(two)})
        found = self.w.of()
        self.assertEqual(found["uncovered"], [])
        self.assertEqual([(j["path"], j["hash"], j["gate"], j["run"]) for j in found["jobs"]],
                         [(JOB, digest(one), None, None), ("jobs/" + second, digest(two), None, None)])

    def test_gate_and_run_states(self):
        job = job_for()
        self.w.on_branch({REQUEST: REQUEST_TEXT, JOB: json.dumps(job)})
        self.w.verdicts()
        self.assertEqual(self.w.gate()[0], 0)
        [found] = self.w.of()["jobs"]
        self.assertEqual((found["gate"], found["run"]), ("PASS", None))
        self.assertTrue(found["gate_comment"]["body"].startswith("[PRODUCER] GATE PASS\n"))
        self.w.ran(job, "FAILED")
        [found] = self.w.of()["jobs"]
        self.assertEqual((found["gate"], found["run"]), ("PASS", "FAILED"))
        self.w.ran(job, "DONE")
        [found] = self.w.of()["jobs"]
        self.assertEqual((found["run"], found["run_row"]["verdict"]), ("DONE", "DONE"))

    def test_stop(self):
        job = job_for(expected={"compute_min": 5, "usd": 1000})
        self.w.on_branch({REQUEST: REQUEST_TEXT, JOB: json.dumps(job)})
        self.w.verdicts()
        self.assertEqual(self.w.gate()[0], 1)
        [found] = self.w.of()["jobs"]
        self.assertEqual(found["gate"], "STOP")

    def test_a_pass_comment_without_its_ledger_row_does_not_count(self):
        job = job_for()
        self.w.on_branch({REQUEST: REQUEST_TEXT, JOB: json.dumps(job)})
        self.w.record(TICKET, "program:gate", f"[PRODUCER] GATE PASS\ncovers: job {digest(job)}\n"
                                              "cap: -  reason: within caps\n")
        [found] = self.w.of()["jobs"]
        self.assertIsNone(found["gate"])

    def test_a_changed_job_has_a_new_hash_and_no_pass(self):
        job = job_for()
        self.w.on_branch({REQUEST: REQUEST_TEXT, JOB: json.dumps(job)})
        self.w.verdicts()
        self.assertEqual(self.w.gate()[0], 0)
        changed = job_for(expected={"compute_min": 6, "usd": 1})
        self.w.on_branch({JOB: json.dumps(changed)})
        [found] = self.w.of()["jobs"]
        self.assertEqual((found["hash"], found["gate"]), (digest(changed), None))


if __name__ == "__main__":
    unittest.main()
