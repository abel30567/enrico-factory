"""Tests for the tool `render` (kit/lib/tools/render.py), the node drivers (kit/lib/node.py) and the split of
a render job's outputs into stage files and asset store bytes in `factory run` (kit/lib/cmd_run.py).

Everything runs through `cmd_run.main` in the `World` of tests/test_run.py, with the real kit/lib/tools and
kit/resources.json, and the fake asset store `AssetStore` of tests/test_assets.py on 127.0.0.1. The node is
`FakeNode`, added to `node.PROVIDERS` as `fake`: it logs each call, its `run` copies
tests/fixtures/render/node/* into `out` (``{job}`` replaced by the hash and ``{input}`` by the first input's
sha256) and its `stop` returns 0.35 USD and 12 minutes. ``FakeNode.plan`` makes a call raise an error whose
text is the node key, `Unavailable`, `SystemExit` or `KeyboardInterrupt`. Nothing reaches the network.
"""

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from kit.lib import cmd_run, gitstate, ledger, lint, node, production, resources  # noqa: E402
from test_assets import KEY_ID, REGION, SECRET, AssetStore  # noqa: E402
from test_run import BRANCH, FIX, NOW, TICKET, World, line  # noqa: E402

NODE_FIX = ROOT / "tests" / "fixtures" / "render" / "node"
NODE_KEY = "seeded-node-key-3f9a61c2"
STAGE_DIR = "production/ep01/render"
STAGE_PATHS = [f"{STAGE_DIR}/manifest.json", f"{STAGE_DIR}/render.json"]
FRAMES = ["frame-0000.bin", "frame-0001.bin"]


class FakeNode:
    """A render node that logs each call to ``FakeNode.calls`` and follows ``FakeNode.plan``."""

    calls, plan = [], {}

    def __init__(self, key_file):
        self.key_file = key_file

    def _step(self, name):
        FakeNode.calls.append(name)
        what = FakeNode.plan.get(name)
        key = Path(self.key_file).read_text()
        if what == "error":
            raise RuntimeError(key)
        if what == "unavailable":
            raise node.Unavailable(key)
        if what == "exit":
            raise SystemExit(key)
        if what == "interrupt":
            raise KeyboardInterrupt(key)

    def start(self):
        self._step("start")

    def run(self, job):
        for path in sorted(NODE_FIX.iterdir()):
            data = path.read_bytes().replace(b"{job}", job["hash"].encode())
            data = data.replace(b"{input}", job["inputs"][0]["sha256"].encode())
            (Path(job["out"]) / path.name).write_bytes(data)
        self._step("run")

    def stop(self):
        self._step("stop")
        return {"usd": 0.35, "compute_min": 12}


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.w = w = World(self)
        for patch in (mock.patch.object(cmd_run, "TOOLS_DIR", ROOT / "kit" / "lib" / "tools"),
                      mock.patch.object(resources, "REGISTER", ROOT / "kit" / "resources.json"),
                      mock.patch.dict(node.PROVIDERS, {"fake": FakeNode}),
                      mock.patch.object(FakeNode, "calls", []),
                      mock.patch.object(FakeNode, "plan", {})):
            patch.start()
            self.addCleanup(patch.stop)
        self.store = AssetStore(self)
        self.settings(RENDER_PROVIDER="fake")
        self.write_key("render.node", NODE_KEY)
        self.write_key("store.assets", f"{KEY_ID}\n{SECRET}\n{REGION}\n")
        text = json.dumps({"scene": "an invented scene"}, indent=1) + "\n"
        w.commit("scripts/render.json", text)
        self.input_sha = hashlib.sha256(text.encode()).hexdigest()
        self.job = w.job("render.json", tool="render", inputs=[{"path": "scripts/render.json", "sha256": self.input_sha}])
        self.digest = w.passed(self.job)
        self.branch = w.head(BRANCH)

    # -- the world

    def settings(self, **changes):
        self.w.local(STATE_DIR=self.w.state, SECRETS_DIR=self.w.secrets, ASSET_STORE=self.store.url("/b"),
                     **changes)

    def write_key(self, name, text):
        path = self.w.secrets / name
        path.write_text(text)
        path.chmod(0o600)

    def render(self, apply=True):
        code, out, err = self.w.run(self.job, apply=apply)
        for secret in (NODE_KEY, KEY_ID, SECRET, REGION):
            self.assertNotIn(secret, out + err)
        return code, out, err

    def output(self, name):
        data = (NODE_FIX / name).read_bytes().replace(b"{job}", self.digest.encode())
        return data.replace(b"{input}", self.input_sha.encode())

    def puts(self):
        return [r["path"].rsplit("/", 1)[1] for r in self.store.requests if r["method"] == "PUT"]

    def rows(self):
        return [(r["verdict"], r["usd"], r["compute_min"], r["tool"]) for r in self.w.rows("run")]

    def down_issues(self):
        return [entry for entry in self.w.log() if entry["op"] == "create_issue"]

    # -- the checks

    def assert_clean(self, puts=True):
        w, digest = self.w, self.digest
        trunk, log = w.head(), w.log()
        self.assertEqual(self.render(), (0, line(digest, "DONE", "0.35", "12") + "\n", ""))
        self.assertEqual(FakeNode.calls, ["start", "run", "stop"])
        self.assertEqual(self.rows(), [("DONE", "0.35", "12", "render")])
        # One commit by `factory` on the pull's branch, adding exactly the two stage files.
        self.assertEqual(w.git("rev-list", f"{self.branch}..{BRANCH}").split(), [w.head(BRANCH)])
        self.assertEqual(w.git("log", "-1", "--format=%an|%s|%P", BRANCH).strip(),
                         f"factory|stage files: job {digest[:12]}|{self.branch}")
        self.assertEqual(w.git("diff", "--name-status", self.branch, BRANCH).split(),
                         ["A", STAGE_PATHS[0], "A", STAGE_PATHS[1]])
        for rel in STAGE_PATHS:
            name = rel.rsplit("/", 1)[1]
            self.assertEqual(w.git("cat-file", "blob", f"{BRANCH}:{rel}").encode(), self.output(name))
            self.assertEqual((w.job_dir(digest) / "out" / name).read_bytes(), self.output(name))
        # The frames are in the store under their sha256, put in name order, and not in git.
        held = {hashlib.sha256(self.output(n)).hexdigest(): self.output(n) for n in FRAMES}
        self.assertEqual(self.store.objects, held)
        if puts:
            self.assertEqual(self.puts(), [hashlib.sha256(self.output(n)).hexdigest() for n in FRAMES])
        self.assertIsNone(w.manifest())
        tree = w.tmp / "exported"
        gitstate.export(w.root, BRANCH, tree)
        result = lint.run(tree, ["media"])["media"]
        self.assertEqual((result.status, result.findings), ("PASS", []))
        self.assertEqual(w.git("diff", "--name-only", trunk, "trunk").split(), [ledger.PATH])
        self.assertEqual(w.log()[len(log):], [{"op": "push", "number": None, "ref": BRANCH},
                                              {"op": "push", "number": None, "ref": "trunk"}])

    def assert_failed(self, reason, usd, compute_min):
        self.assertEqual(self.render(), (1, line(self.digest, "FAILED", usd, compute_min) + "\n",
                                      f"run: tool render failed: {reason}\n"))
        self.assertEqual(self.rows(), [("FAILED", usd, compute_min, "render")])
        self.assertEqual(self.w.head(BRANCH), self.branch)
        self.assertEqual((self.store.objects, self.puts()), ({}, []))
        self.assertIsNone(self.w.manifest())

    # 1
    def test_clean(self):
        self.assert_clean()

    def test_stage_file_already_there_is_written_over(self):
        tree = self.w.tmp / "maker"
        gitstate.add_worktree(self.w.root, tree, BRANCH, branch=BRANCH)
        (tree / STAGE_DIR).mkdir(parents=True)
        (tree / STAGE_PATHS[0]).write_text('{"job": "an older job"}\n')
        gitstate.git(tree, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", "add", "-A")
        gitstate.git(tree, "-c", "user.name=tester", "-c", "user.email=tester@example.invalid", "commit", "-q",
                     "-m", "an older render")
        gitstate.remove_worktree(self.w.root, tree)
        older = self.w.head(BRANCH)
        self.assertEqual(self.render()[0], 0)
        self.assertEqual(self.w.git("diff", "--name-status", older, BRANCH).split(),
                         ["M", STAGE_PATHS[0], "A", STAGE_PATHS[1]])
        self.assertEqual(self.w.git("cat-file", "blob", f"{BRANCH}:{STAGE_PATHS[0]}").encode(),
                         self.output("manifest.json"))

    # 2
    def test_job_fails_on_the_node(self):
        FakeNode.plan["run"] = "error"
        self.assert_failed("job failed on the node", "0.35", "12")
        self.assertEqual(FakeNode.calls, ["start", "run", "stop"])

    # 3
    def test_start_fails(self):
        FakeNode.plan["start"] = "error"
        self.assert_failed("node did not start", "0.35", "12")
        self.assertEqual(FakeNode.calls, ["start", "stop"])

    # 4
    def test_stop_fails_after_a_clean_run(self):
        FakeNode.plan["stop"] = "error"
        self.assert_failed("node did not stop", "2.5", "30")
        self.assertEqual(FakeNode.calls, ["start", "run", "stop"])

    def test_stop_fails_after_a_failed_run(self):
        FakeNode.plan.update(run="error", stop="error")
        self.assert_failed("node did not stop", "2.5", "30")
        self.assertEqual(FakeNode.calls, ["start", "run", "stop"])

    def test_stop_fails_after_no_node(self):
        FakeNode.plan.update(start="unavailable", stop="error")
        self.assert_failed("node did not stop", "2.5", "30")
        self.assertEqual(FakeNode.calls, ["start", "stop"])
        self.assertEqual(self.down_issues(), [])

    # 5
    def test_system_exit_in_run(self):
        FakeNode.plan["run"] = "exit"
        self.assert_failed("tool crashed", "2.5", "30")
        self.assertEqual(FakeNode.calls, ["start", "run", "stop"])

    def test_system_exit_in_start(self):
        FakeNode.plan["start"] = "exit"
        self.assert_failed("tool crashed", "2.5", "30")
        self.assertEqual(FakeNode.calls, ["start", "stop"])

    def test_keyboard_interrupt_in_run(self):
        FakeNode.plan["run"] = "interrupt"
        with self.assertRaises(KeyboardInterrupt):
            self.render()
        self.assertEqual(FakeNode.calls, ["start", "run", "stop"])
        self.assertEqual(self.rows(), [])
        FakeNode.plan.clear()
        self.assert_failed("no result", "2.5", "30")
        self.assertEqual(FakeNode.calls, ["start", "run", "stop"])

    def test_keyboard_interrupt_held_when_stop_fails(self):
        FakeNode.plan.update(run="interrupt", stop="error")
        with self.assertRaises(KeyboardInterrupt):
            self.render()
        self.assertEqual(FakeNode.calls, ["start", "run", "stop"])

    # 6
    def assert_down(self, calls):
        self.assertEqual(self.render(), (2, "", "run: resource render.node is down (#5)\n"))
        self.assertEqual(FakeNode.calls, calls)
        self.assertEqual(self.rows(), [])
        self.assertFalse(self.w.job_dir(self.digest).exists())
        self.assertEqual(self.w.head(BRANCH), self.branch)
        [entry] = self.down_issues()
        self.assertEqual((entry["number"], entry["title"], entry["labels"]),
                         (5, "[DOWN] render.node", ["resource:down"]))

    def test_no_node(self):
        FakeNode.plan["start"] = "unavailable"
        self.assert_down(["start", "stop"])
        FakeNode.calls.clear()
        self.assertEqual(self.render(), (2, "", "run: resource render.node is down (#5)\n"))
        self.assertEqual(FakeNode.calls, [])
        self.assertEqual(len(self.down_issues()), 1)
        # Twin: the issue closed, the same job runs clean under the same PASS.
        FakeNode.plan.clear()
        self.w.edit("issues", 5, state="closed")
        self.assert_clean()

    # 7
    def test_no_driver(self):
        self.settings(RENDER_PROVIDER="other")
        self.assert_down([])

    # 8
    def test_store_not_usable(self):
        (self.w.secrets / "store.assets").unlink()
        message = "run: resource store.assets is not usable: secret store.assets: missing\n"
        for apply in (False, True):
            with self.subTest(apply=apply):
                self.assertEqual(self.render(apply), (2, "", message))
        self.assertEqual(FakeNode.calls, [])
        self.assertFalse(self.w.job_dir(self.digest).exists())
        self.assertEqual(self.rows(), [])

    def test_dry_run(self):
        self.assertEqual(self.render(apply=False), (0, f"run job {self.digest}: would call render\n", ""))
        self.assertEqual(FakeNode.calls, [])

    # 9
    def test_store_refuses_after_the_call(self):
        self.store.status["PUT"] = (500, b"")
        self.assertEqual(self.render(), (2, "", "run: the asset store did not take frame-0000.bin: store answered 500\n"))
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.w.head(BRANCH), self.branch)
        self.assertEqual(FakeNode.calls, ["start", "run", "stop"])
        # Twin: the store fixed, the call is recorded from result.json with no call.
        del self.store.status["PUT"]
        self.assert_clean(puts=False)
        self.assertEqual(FakeNode.calls, ["start", "run", "stop"])

    def test_frame_already_held_is_not_sent_again(self):
        first = self.output(FRAMES[0])
        self.store.objects[hashlib.sha256(first).hexdigest()] = first
        self.assert_clean(puts=False)
        self.assertEqual(self.puts(), [hashlib.sha256(self.output(FRAMES[1])).hexdigest()])

    # 10
    def test_ticket_without_a_production_directory(self):
        self.w.edit("issues", TICKET, labels=["room:production", "stage:render", "tracked", "unit:ep01/Q1-1"])
        message = "run: unit ep01/Q1-1 of stage production/render has no production directory\n"
        for apply in (False, True):
            with self.subTest(apply=apply):
                self.assertEqual(self.render(apply), (2, "", message))
        self.assertEqual(FakeNode.calls, [])
        self.assertFalse(self.w.job_dir(self.digest).exists())
        self.assertEqual(self.rows(), [])


class NodeTest(unittest.TestCase):
    def test_the_kit_ships_no_driver(self):
        self.assertEqual(node.PROVIDERS, {})
        self.assertIsNone(node.connect("fake", "render.node"))
        with mock.patch.dict(node.PROVIDERS, {"fake": FakeNode}):
            self.assertEqual(node.connect("fake", "render.node").key_file, "render.node")


class UnitDirTest(unittest.TestCase):
    def unit_dir(self, kind, unit, stage="render"):
        with mock.patch.object(production, "stage", return_value={"stage": stage, "unit": kind}) as found:
            result = cmd_run._unit_dir("p", "production", stage, unit)
        found.assert_called_once_with("p", "production", stage)
        return result

    def test_directories(self):
        self.assertEqual(self.unit_dir("shot", "ep01/Q1-1"), "production/ep01/Q1-1/render")
        self.assertEqual(self.unit_dir("episode", "ep01"), "production/ep01/render")
        self.assertEqual(self.unit_dir("asset", "fox"), "production/assets/fox/render")

    def test_refused(self):
        for kind, unit in (("shot", "ep01"), ("shot", "../x"), ("shot", "ep01/../x"), ("episode", "../x"),
                           ("episode", "ep01/Q1-1"), ("asset", "a/b"), ("asset", ".fox"), ("asset", ""),
                           ("sequence", "ep01/Q1"), ("sequence", "ep01"), ("sequence", "Q1"), (None, "ep01")):
            with self.subTest(kind=kind, unit=unit):
                self.assertIsNone(self.unit_dir(kind, unit))

    def test_unknown_stage(self):
        with mock.patch.object(production, "stage", return_value=None):
            self.assertIsNone(cmd_run._unit_dir("p", "production", "render", "ep01"))


class ToolShapeTest(unittest.TestCase):
    def test_render_tool(self):
        tool = cmd_run._load_tool("render")
        self.assertEqual((tool.RESOURCE, tool.REASONS, tool.STAGE_FILES), (
            "render.node", ("node did not start", "job failed on the node", "node did not stop"),
            ("manifest.json", "mix.json", "render.json", "rerun.json", "state.json")))

    def test_stage_files_must_be_a_tuple_of_strings(self):
        tools = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tools)
        text = (FIX / "tools" / "fake.py").read_text()
        with mock.patch.object(cmd_run, "TOOLS_DIR", tools):
            for value in ('["state.json"]', '("state.json", 1)', '"state.json"', "None"):
                with self.subTest(value=value):
                    (tools / "fake.py").write_text(text + f"\nSTAGE_FILES = {value}\n")
                    with self.assertRaises(cmd_run.Refused) as refused:
                        cmd_run._load_tool("fake")
                    self.assertEqual(str(refused.exception), "tool fake does not follow the tool interface")
            (tools / "fake.py").write_text(text + '\nSTAGE_FILES = ("state.json",)\n')
            self.assertEqual(cmd_run._load_tool("fake").STAGE_FILES, ("state.json",))


if __name__ == "__main__":
    unittest.main()
