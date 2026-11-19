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

from kit.lib import cmd_gate, gitstate, github, jobs, ledger, lockrules, production, steps, tickets  # noqa: E402

GATE = ROOT / "tests" / "fixtures" / "gate"
FETCHER = ROOT / "tests" / "fixtures" / "jobs" / "fetcher.md"
KIT = ROOT / "kit"
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

    def ran(self, job, verdict, ticket=TICKET):
        """A ``run`` row of ``job`` appended on `trunk`, as ``factory run`` writes it."""
        text = gitstate.show(self.root, "trunk", ledger.PATH) or ledger.HEADER + "\n"
        row = {"time": NOW, "kind": "run", "hash": digest(job), "ticket": ticket, "episode": "ep01",
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
        return jobs.covering(p, self.w.root, pull, ticket, "fetcher", REQUEST)

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
        self.assertIsNone(jobs.covering(p, self.w.root, pull, ticket, "fetcher", REQUEST))


class OperatorInputsTest(unittest.TestCase):
    """ISSUE-620: at a stage the filing lists the inputs of (``assets``, ``rigging``), a job covers its request only
    when the paths of its inputs are that list, in order. Ticket #3 is read as a ticket of that stage for an asset;
    the canon sheet and the ledger are committed on `trunk`."""

    UNIT = "wren"
    SHEET = "bible/canon/characters/wren.md"
    LEDGER = "bible/canon/ledger.tsv"
    MANIFEST = "media/manifest.tsv"

    def setUp(self):
        self.w = World(self)

    def with_sheet(self):
        self.w.on_trunk(self.SHEET, "Wren.\n")
        self.w.on_trunk(self.LEDGER, "id\tfact\n")

    def job(self, paths):
        return job_for(inputs=[{"path": REQUEST, "sha256": sha(REQUEST_TEXT)},
                               *({"path": path, "sha256": "0" * 64} for path in paths)])

    def covering(self, paths, stage="assets", unit=UNIT):
        job = self.job(paths)
        self.w.on_branch({REQUEST: REQUEST_TEXT, JOB: json.dumps(job)})
        p, _, ticket, pull = self.w.load()
        return jobs.covering(p, self.w.root, pull, {**ticket, "stage": stage, "unit": unit}, "fetcher", REQUEST)

    def test_a_stale_assets_job_does_not_cover(self):
        self.with_sheet()
        self.assertIsNone(self.covering([self.MANIFEST, self.SHEET]))

    def test_the_assets_job_with_the_ledger_covers(self):
        self.with_sheet()
        job = self.job([self.MANIFEST, self.SHEET, self.LEDGER])
        self.assertEqual(self.covering([self.MANIFEST, self.SHEET, self.LEDGER]),
                         {"path": JOB, "request": REQUEST, "job": job, "hash": digest(job)})

    def test_the_right_paths_in_another_order_do_not_cover(self):
        self.with_sheet()
        self.assertIsNone(self.covering([self.SHEET, self.MANIFEST, self.LEDGER]))

    def test_the_request_not_first_does_not_cover(self):
        self.with_sheet()
        job = job_for(inputs=[{"path": self.MANIFEST, "sha256": "0" * 64},
                              {"path": REQUEST, "sha256": sha(REQUEST_TEXT)},
                              {"path": self.SHEET, "sha256": "0" * 64}, {"path": self.LEDGER, "sha256": "0" * 64}])
        self.w.on_branch({REQUEST: REQUEST_TEXT, JOB: json.dumps(job)})
        p, _, ticket, pull = self.w.load()
        self.assertIsNone(jobs.covering(p, self.w.root, pull, {**ticket, "stage": "assets", "unit": self.UNIT},
                                        "fetcher", REQUEST))

    def test_a_prop(self):
        self.assertIsNotNone(self.covering([self.MANIFEST]))
        self.w = World(self)
        self.assertIsNone(self.covering([self.MANIFEST, self.SHEET, self.LEDGER]))

    def test_rigging(self):
        state = f"production/assets/{self.UNIT}/assets/state.json"
        self.with_sheet()
        self.assertIsNotNone(self.covering([state], stage="rigging"))
        self.w = World(self)
        self.with_sheet()
        self.assertIsNone(self.covering([], stage="rigging"))

    def test_of_reports_a_stale_job_uncovered(self):
        self.with_sheet()
        self.w.on_branch({REQUEST: REQUEST_TEXT, JOB: json.dumps(self.job([self.MANIFEST, self.SHEET]))})
        p, state, ticket, pull = self.w.load()
        found = jobs.of(p, self.w.root, state, {**ticket, "stage": "assets", "unit": self.UNIT}, pull, role="fetcher")
        self.assertIn(REQUEST, found["uncovered"])
        self.assertEqual(found["jobs"], [])


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

    def test_a_pending_run_has_not_run(self):
        job = job_for()
        self.w.on_branch({REQUEST: REQUEST_TEXT, JOB: json.dumps(job)})
        self.w.verdicts()
        self.assertEqual(self.w.gate()[0], 0)
        self.w.ran(job, "DONE")
        state = self.w.root.parent / "state"
        (self.w.root / "production.local.env").write_text(f"STATE_DIR={state}\n")
        [found] = self.w.of()["jobs"]
        self.assertEqual((found["run"], found["run_row"]["verdict"]), ("DONE", "DONE"))
        mark = state / "jobs" / digest(job) / "pending"
        mark.parent.mkdir(parents=True)
        mark.write_text("")
        [found] = self.w.of()["jobs"]
        self.assertEqual((found["run"], found["run_row"]), (None, None))
        mark.unlink()
        [found] = self.w.of()["jobs"]
        self.assertEqual(found["run"], "DONE")

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

    def test_a_failed_job_rewritten_with_an_attempt(self):
        job = job_for()
        self.w.on_branch({REQUEST: REQUEST_TEXT, JOB: json.dumps(job)})
        self.w.verdicts()
        self.assertEqual(self.w.gate()[0], 0)
        self.w.ran(job, "FAILED")
        [found] = self.w.of()["jobs"]
        self.assertEqual((found["gate"], found["run"]), ("PASS", "FAILED"))
        retry = job_for(attempt=2)
        self.w.on_branch({JOB: json.dumps(retry)})
        found = self.w.of()
        self.assertEqual(([(j["hash"], j["gate"], j["run"]) for j in found["jobs"]], found["uncovered"]),
                         ([(digest(retry), None, None)], []))
        self.assertEqual(self.w.gate(), (1, f"gate job {digest(retry)}: STOP cap - reason verdicts\n", ""))
        self.w.verdicts()
        self.assertEqual(self.w.gate()[0], 0)
        [found] = self.w.of()["jobs"]
        self.assertEqual((found["hash"], found["gate"], found["run"]), (digest(retry), "PASS", None))


class PlatformTest(unittest.TestCase):
    """ISSUE-392: at an operator-only stage the operator of a pull is the one whose ``writes`` hold the job of a
    request on it, and rule 7 takes the request of a ``DONE`` job inside the stage's ``paths``.

    The `render` entry gets the ``maker`` and ``paths`` of the shipped `distribution/post` entry (or of
    `distribution/window`) and the real channel and telemetry roles."""

    UPLOAD = '{"video": "media:v1", "captions": "media:c1"}\n'

    def world(self, stage="post"):
        w = World(self)
        for name in ("channel-youtube", "channel-tiktok", "channel-instagram", "telemetry-puller"):
            shutil.copy(KIT / "roles" / f"{name}.md", w.root / ".factory" / "roles" / f"{name}.md")
        shipped = next(e for e in json.loads((KIT / "pipeline.json").read_text())
                       if e["room"] == "distribution" and e["stage"] == stage)
        path = w.root / ".factory" / "pipeline.json"
        entries = json.loads(path.read_text())
        for entry in entries:
            if entry["stage"] == "render":
                entry.update(maker=shipped["maker"], paths=shipped["paths"], after="")
        path.write_text(json.dumps(entries, indent=1) + "\n")
        w.git("add", "-A")
        w.git("commit", "-q", "-m", "an operator-only stage")
        return w

    def post(self, platform="tiktok", name="upload", stage="post", job=True, run="DONE", text=None):
        """The world with the request ``distribution/ep01/<platform>/requests/<name>.json`` on pull #4, its job
        by the platform's channel (by `telemetry-puller` at the window) and the job's run row."""
        w = self.world(stage)
        request = f"distribution/ep01/{platform}/requests/{name}.json"
        caller = "telemetry-puller" if stage == "window" else f"channel-{platform}"
        text = text or self.UPLOAD
        files = {request: text}
        made = job_for(request=request, text=text, caller=caller)
        if job:
            files["jobs/" + request] = json.dumps(made)
        w.on_branch(files)
        if job and run:
            w.ran(made, run)
        return w, request, caller

    def rule7(self, w, role):
        p, state, ticket, pull = w.load()
        return lockrules._writes(p, "production", role, pull["git"]["changed"],
                                 lockrules.operator_paths(p, state, ticket, pull, w.root))

    def test_the_operator_of_a_pull(self):
        for platform in ("tiktok", "youtube", "instagram"):
            with self.subTest(platform):
                w, request, caller = self.post(platform)
                found = w.of()
                self.assertEqual((found["operator"], found["requests"], found["uncovered"]),
                                 (caller, [request], []))
                self.assertEqual([j["run"] for j in found["jobs"]], ["DONE"])

    def test_a_pull_with_only_a_job_file_has_no_operator(self):
        w = self.world()
        w.on_branch({"jobs/distribution/ep01/tiktok/requests/upload.json": json.dumps(job_for())})
        self.assertIsNone(w.of())
        p, _, ticket, pull = w.load()
        entry = production.stage(p, ticket["room"], ticket["stage"])
        self.assertIsNone(jobs.operator_of(p, w.root, entry, pull))

    def test_a_request_is_for_one_operator(self):
        w, request, _ = self.post()
        p, state, ticket, pull = w.load()
        self.assertIsNone(jobs.of(p, w.root, state, ticket, pull, role="channel-youtube"))
        self.assertEqual(jobs.requests(p, w.root, pull, "channel-youtube"), [])
        self.assertEqual(jobs.requests(p, w.root, pull, "channel-tiktok"), [request])

    def test_rule_7_takes_the_covered_request(self):
        w, _, caller = self.post()
        self.assertEqual(self.rule7(w, caller), "")
        w, _, caller = self.post("youtube", "window", "window")
        self.assertEqual(self.rule7(w, caller), "")

    def test_rule_7_seeded(self):
        stray = "distribution/ep01/tiktok/requests/stray.json"
        w, _, _ = self.post()
        w.on_branch({stray: "{}\n"})
        self.assertEqual(self.rule7(w, "channel-tiktok"),
                         f"outside the writes of channel-tiktok: {stray}; outside room production: {stray}")
        upload = "distribution/ep01/tiktok/requests/upload.json"
        w, _, _ = self.post(run="FAILED")
        self.assertEqual(self.rule7(w, "channel-tiktok"), f"outside the writes of channel-tiktok: {upload}")
        w, _, _ = self.post()
        w.on_branch({upload: self.UPLOAD.replace("media:v1", "media:v2")})
        self.assertEqual(self.rule7(w, "channel-tiktok"), f"outside the writes of channel-tiktok: {upload}")
        public = "distribution/ep01/tiktok/requests/public.json"
        w, _, _ = self.post(name="public")
        self.assertEqual([j["run"] for j in w.of()["jobs"]], ["DONE"])
        self.assertEqual(self.rule7(w, "channel-tiktok"),
                         f"outside the writes of channel-tiktok: {public}; outside room production: {public}")
        stray = "distribution/ep01/youtube/requests/stray.json"
        w, _, _ = self.post("youtube", "window", "window")
        w.on_branch({stray: "{}\n"})
        self.assertEqual(self.rule7(w, "telemetry-puller"),
                         f"outside the writes of telemetry-puller: {stray}; outside room production: {stray}")

    def retried(self, *runs):
        """The world with the upload request on pull #4, its job file naming job A and an earlier job B of the
        same request, a ``DONE`` run row of A, the run rows ``runs`` (verdict, ticket) of B on `trunk`, and a
        branch manifest that adds a row of A and a row of B."""
        w = self.world()
        request = "distribution/ep01/tiktok/requests/upload.json"
        earlier = job_for(request=request, text=self.UPLOAD, caller="channel-tiktok", expected={"compute_min": 4, "usd": 1})
        made = job_for(request=request, text=self.UPLOAD, caller="channel-tiktok")
        for verdict, ticket in runs:
            w.ran(earlier, verdict, ticket)
        w.ran(made, "DONE")
        base = gitstate.show(w.root, "trunk", gitstate.MANIFEST)
        manifest = (base or lockrules.MANIFEST_HEADER + "\n") + "".join(
            f"media:{name}\t{sha(name)}\t{len(name)}\tjob {digest(job)}\n" for name, job in (("b1", earlier), ("a1", made)))
        w.on_branch({request: self.UPLOAD, "jobs/" + request: json.dumps(made), gitstate.MANIFEST: manifest})
        return w

    def test_rule_7_takes_the_manifest_rows_of_a_retried_request(self):
        self.assertEqual(self.rule7(self.retried(("DONE", TICKET)), "channel-tiktok"), "")

    def assert_manifest_refused(self, *runs):
        manifest = gitstate.MANIFEST
        self.assertEqual(self.rule7(self.retried(*runs), "channel-tiktok"),
                         f"outside the writes of channel-tiktok: {manifest}; outside room production: {manifest}")

    def test_rule_7_retried_seeded_other_ticket(self):
        self.assert_manifest_refused(("DONE", TICKET + 1))

    def test_rule_7_retried_seeded_last_verdict_not_done(self):
        self.assert_manifest_refused(("DONE", TICKET), ("FAILED", TICKET))

    def test_rule_7_retried_seeded_no_run(self):
        self.assert_manifest_refused()

    def test_the_tick_launches_the_pulls_operator(self):
        for platform in ("tiktok", "youtube"):
            with self.subTest(platform):
                w, _, caller = self.post(platform, job=False)
                p, state, ticket, _ = w.load()
                self.assertEqual(steps.next_step(p, state, ticket, w.root, NOW),
                                 {"state": "jobs", "step": "launch", "role": caller, "pull": PULL,
                                  "reason": f"1 request(s) on #{PULL} have no job"})
        w, _, _ = self.post()
        p, _, ticket, pull = w.load()
        entry = production.stage(p, ticket["room"], ticket["stage"])
        self.assertEqual(steps._maker_of(p, entry, pull, w.root), "channel-tiktok")


class RestageTest(unittest.TestCase):
    """ISSUE-628: a ``DONE`` job one of whose staged outputs, a name in both its tool's ``STAGE_FILES`` and the
    ``outputs`` of its recorded call (``<STATE_DIR>/jobs/<hash>/result.json``), is not at the pull's head in the
    directory ``factory run`` stages into (``production/ep01/render`` for ticket #3), has not run; a job file the
    pull changes asks for its request even when the request is unchanged."""

    UNIT_DIR = "production/ep01/render"

    def setUp(self):
        self.w = World(self)
        self.state = self.w.root.parent / "state"
        with (self.w.root / ".git" / "info" / "exclude").open("a") as exclude:
            exclude.write("production.local.env\n")
        (self.w.root / "production.local.env").write_text(f"STATE_DIR={self.state}\n")

    def done(self, tool, staged, outputs=None):
        """A ``DONE`` job of ``tool`` with ``staged`` at the head and, unless ``outputs`` is None, the record of a
        call that output those names."""
        job = job_for(tool=tool)
        self.w.on_branch({REQUEST: REQUEST_TEXT, JOB: json.dumps(job),
                          **{f"{self.UNIT_DIR}/{name}": "{}\n" for name in staged}})
        self.w.ran(job, "DONE")
        if outputs is not None:
            path = self.state / "jobs" / digest(job) / "result.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                "compute_min": "5", "reason": None, "usd": "1", "verdict": "DONE",
                "outputs": [{"name": name, "sha256": "0" * 64, "bytes": 3} for name in outputs]}))
        return job

    def test_missing_staged_outputs(self):
        job = self.done("model3d", ("state.json", "manifest.json"), ("manifest.json", "model.glb", "state.json"))
        [found] = self.w.of()["jobs"]
        self.assertEqual((found["hash"], found["run"]), (digest(job), "DONE"))
        self.w.on_branch({f"{self.UNIT_DIR}/state.json": None})
        [found] = self.w.of()["jobs"]
        self.assertIsNone(found["run"])
        self.assertEqual(found["run_row"]["verdict"], "DONE")

    def test_nothing_staged(self):
        self.done("model3d", (), ("manifest.json", "model.glb", "state.json"))
        [found] = self.w.of()["jobs"]
        self.assertEqual((found["run"], found["run_row"]["verdict"]), (None, "DONE"))

    def test_a_stage_file_the_call_did_not_output(self):
        self.done("model3d", ("manifest.json",), ("manifest.json", "model.glb"))
        [found] = self.w.of()["jobs"]
        self.assertEqual(found["run"], "DONE")

    def test_no_record(self):
        self.done("model3d", ())
        [found] = self.w.of()["jobs"]
        self.assertEqual(found["run"], "DONE")

    def test_a_record_whose_outputs_are_not_a_list(self):
        job = self.done("model3d", (), ())
        (self.state / "jobs" / digest(job) / "result.json").write_text(json.dumps({"outputs": 1, "verdict": "DONE"}))
        [found] = self.w.of()["jobs"]
        self.assertEqual(found["run"], "DONE")

    def test_tool_without_staged_files(self):
        self.done("voice", (), ("voice.wav",))
        [found] = self.w.of()["jobs"]
        self.assertEqual(found["run"], "DONE")

    def test_reasked_job_on_an_unchanged_request(self):
        job = job_for()
        self.w.on_trunk(REQUEST, REQUEST_TEXT)
        self.w.on_trunk(JOB, json.dumps(job))
        self.w.git("branch", "-f", self.w.ref(PULL), "trunk")
        self.w.on_branch({"frames/ep01/notes.md": "x\n"})
        p, _, _, pull = self.w.load()
        self.assertEqual(jobs.requests(p, self.w.root, pull, "fetcher"), [])
        retry = job_for(attempt=2)
        self.w.on_branch({JOB: json.dumps(retry)})
        p, _, _, pull = self.w.load()
        self.assertNotIn(REQUEST, pull["git"]["changed"])
        self.assertEqual(jobs.requests(p, self.w.root, pull, "fetcher"), [REQUEST])
        self.assertEqual([j["hash"] for j in self.w.of()["jobs"]], [digest(retry)])

    def test_a_request_and_its_job_file_once(self):
        self.w.on_branch({REQUEST: REQUEST_TEXT, JOB: json.dumps(job_for())})
        p, _, _, pull = self.w.load()
        self.assertEqual(jobs.requests(p, self.w.root, pull, "fetcher"), [REQUEST])


if __name__ == "__main__":
    unittest.main()
