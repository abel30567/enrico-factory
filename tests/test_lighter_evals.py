"""The lighter's eval suite in kit/evals/lighter/ (brief section 13), run offline (ISSUE-565).

The `lighting` stage has no judge, so the suite measures the lint pass rate only. Recorded outputs in
tests/fixtures/story_evals/ stand in for the role: nothing here runs a model. The role is found as a maker
of the pipeline's `production/lighting` entry, not in a loop list.
The lighting lint does not read requests, so a request's shape is not graded here.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kit.lib import evals, lint

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "kit"
FIX = ROOT / "tests" / "fixtures" / "story_evals"
FAKE = ROOT / "tests" / "fixtures" / "harness" / "fake-harness"
FACTORY = ROOT / "kit" / "factory"

ENTRY = next(e for e in json.loads((KIT / "pipeline.json").read_text(encoding="utf-8"))
             if e["room"] == "production" and e["stage"] == "lighting")
ROLE = "lighter"
DIR = "production/ep01/Q1-1/lighting"
OTHER = "production/ep01/Q1-2/lighting"
SCENE = f"{DIR}/scene.py"
REQUEST = f"{DIR}/requests/render.json"
JOB = ("manifest.json", "render.json", "state.json")
READY = {"marker": "READY", "summary": "Done.",
         "evidence": [{"criterion": "stage files", "result": "written", "command": "ls"}]}


def tree_hash(path):
    digest = hashlib.sha256()
    for item in sorted(p for p in Path(path).rglob("*") if p.is_file()):
        digest.update(item.relative_to(path).as_posix().encode() + b"\0" + item.read_bytes())
    return digest.hexdigest()


def recorded(case):
    """The recorded output of the case: ``{path: text}``."""
    output = FIX / ROLE / case.id
    return {p.relative_to(output).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(output.rglob("*")) if p.is_file()}


def run_eval(files):
    """``(completed process, summary, details)`` of ``factory eval`` on the fake harness, each case's READY
    writing ``files(case)``; ``details`` the graded detail of each result."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        bin_dir, root, out = tmp / "bin", tmp / "root", tmp / "out"
        bin_dir.mkdir()
        root.mkdir()
        shutil.copy(FAKE, bin_dir / "fake-harness")
        os.chmod(bin_dir / "fake-harness", 0o755)
        steps = [{"match": f"eval {re.escape(case.id)}(?![\\w-])", "files": files(case), "result": READY}
                 for case in evals.load(KIT, ROLE)]
        (bin_dir / "fake-harness.json").write_text(json.dumps(steps), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "FACTORY_LIVE"}
        env["FACTORY_FAKE_HARNESS"] = str(bin_dir / "fake-harness")
        done = subprocess.run([sys.executable, str(FACTORY), "eval", ROLE, "--harness", "fake",
                               "--root", str(root), "--out", str(out)],
                              env=env, cwd=ROOT, capture_output=True, text=True, timeout=300)
        summary, results = out / "summary.json", out / "results.jsonl"
        return (done, json.loads(summary.read_text(encoding="utf-8")) if summary.is_file() else None,
                [json.loads(line)["detail"] for line in results.read_text(encoding="utf-8").splitlines()]
                if results.is_file() else [])


def graded(case, change=None):
    """``(grade, lint text)`` of READY on the case's tree with its recorded output, the run directory then
    edited by ``change(run_dir)``; the lint text is of the case's lint alone."""
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        shutil.copytree(case.path / "tree", run_dir)
        shutil.copytree(FIX / ROLE / case.id, run_dir, dirs_exist_ok=True)
        if change:
            change(run_dir)
        return (evals.grade(case, READY, run_dir=run_dir),
                lint.text(lint.run(run_dir, only=case.spec["lint"])))


def move(src, dst):
    def change(run_dir):
        (run_dir / dst).parent.mkdir(parents=True, exist_ok=True)
        (run_dir / src).rename(run_dir / dst)
    return change


def remove(rel):
    def change(run_dir):
        (run_dir / rel).unlink()
    return change


def write(rel, text):
    def change(run_dir):
        (run_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (run_dir / rel).write_text(text, encoding="utf-8")
    return change


def edit_request(edit):
    def change(run_dir):
        data = json.loads((run_dir / REQUEST).read_text(encoding="utf-8"))
        edit(data)
        (run_dir / REQUEST).write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    return change


def no_request(directory):
    lines = [f"B{n} lighting {directory}/scene.py:1 {directory}/{name} is missing and no request is under"
             f" {directory}/requests/" for n, name in enumerate(JOB, 1)]
    return "\n".join(["lighting FAIL 3 findings"] + lines) + "\n"


CUTS = (("request removed", remove(REQUEST), f"lint FAIL; missing {REQUEST}", no_request(DIR)),
        ("request renamed", move(REQUEST, f"{DIR}/requests/render.txt"), f"lint FAIL; missing {REQUEST}",
         no_request(DIR)),
        ("request moved", move(REQUEST, f"{DIR}/render.json"), f"lint FAIL; missing {REQUEST}",
         f"lighting FAIL 4 findings\nB1 lighting {DIR}/render.json:1 samples is not a list\n"
         f"B2 lighting {DIR}/render.json:1 {DIR}/state.json is missing\n"
         f"B3 lighting {SCENE}:1 {DIR}/manifest.json is missing\nB4 lighting {SCENE}:1 {DIR}/state.json is missing\n"),
        ("manifest written", write(f"{DIR}/manifest.json", "{}\n"), "lint FAIL",
         f"lighting FAIL 2 findings\nB1 lighting {SCENE}:1 {DIR}/render.json is missing\n"
         f"B2 lighting {SCENE}:1 {DIR}/state.json is missing\n"),
        ("scene moved", move(SCENE, f"{OTHER}/scene.py"), f"lint FAIL; missing {SCENE}", no_request(OTHER)),
        ("scene removed", remove(SCENE), f"lint PASS; missing {SCENE}", "lighting PASS\n"))
SUN = {"elevation_deg": 45, "azimuth_deg": 180}


def job(case, luma=None, sun=False):
    """A change writing the lighting job's files in ``DIR``, to the case's key unless ``luma`` or ``sun``
    is given."""
    key = json.loads((case.path / "tree" / "bible/style/keys/ep01/Q1.json").read_text(encoding="utf-8"))
    state = {"shot": "Q1-1", "stage": "lighting",
             "lights": [{"id": "key", "kind": "directional", "intensity": 1, "unit": "lux", "color": [1, 1, 1],
                         "temperature_k": 5600}],
             "sun": sun if sun is not False else (SUN if key["sun"] else None)}
    render = {"samples": [{"frame": 0, "luma": key["luma"] if luma is None else luma, "clipped": 0,
                           "color": key["color"]}]}

    def change(run_dir):
        for name, data in (("manifest.json", {}), ("state.json", state), ("render.json", render)):
            (run_dir / DIR / name).write_text(json.dumps(data) + "\n", encoding="utf-8")
    return change


NEAR_MISSES = (("refs emptied", edit_request(lambda d: d.update(refs=[]))),
               ("a fourth key", edit_request(lambda d: d.update(tool="unreal"))),
               ("another shot", edit_request(lambda d: d.update({"for": "ep01/Q1-2"}))))


class LighterEvalsTest(unittest.TestCase):
    def test_the_suite_is_the_lighting_entrys(self):
        self.assertIn(ROLE, ENTRY["maker"])
        self.assertTrue((KIT / "evals" / ROLE / "suite.json").is_file())
        self.assertFalse((KIT / "evals" / f"{ROLE}.no-suite.md").exists())
        cases = evals.load(KIT, ROLE)
        self.assertEqual(len(cases), 3)
        for case in cases:
            with self.subTest(case=case.id):
                self.assertEqual(case.kind, "replay")
                self.assertEqual(case.spec["lint"], ENTRY["lint"])
                self.assertEqual(case.spec["lint"], ["lighting"])
                self.assertEqual(case.spec["expect_files"], [SCENE, REQUEST])
                self.assertEqual(sorted(recorded(case)), sorted([SCENE, REQUEST]))

    def test_each_tree_passes_its_lints(self):
        cases = evals.load(KIT, ROLE)
        self.assertEqual(len(cases), 3)
        for case in cases:
            for cid in ("lighting", "simulation"):
                with self.subTest(case=case.id, lint=cid):
                    self.assertEqual(lint.text(lint.run(case.path / "tree", only=[cid])), f"{cid} PASS\n")
            with self.subTest(case=case.id, twin="clean"):
                grade, text = graded(case)
                self.assertTrue(grade["pass"], grade["detail"])
                self.assertEqual(grade["detail"], "lint PASS")
                self.assertEqual(text, "lighting PASS\n")

    def test_the_simulation_lint_reads_the_tree(self):
        case = next(c for c in evals.load(KIT, ROLE) if c.id == "01-orchard")
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "tree"
            shutil.copytree(case.path / "tree", tree)
            rerun = tree / "production/ep01/Q1-1/simulation/rerun.json"
            data = json.loads(rerun.read_text(encoding="utf-8"))
            data["caches"][0]["sha256"] = "0" * 64
            rerun.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            self.assertIn("B1 simulation production/ep01/Q1-1/simulation/state.json:1 cache 'pim-cloth': sha256"
                          " differs from production/ep01/Q1-1/simulation/rerun.json",
                          lint.text(lint.run(tree, only=["simulation"])).splitlines())

    def test_the_key_is_read(self):
        cases = {c.id: c for c in evals.load(KIT, ROLE)}
        self.assertEqual(len(cases), 3)
        for case in cases.values():
            with self.subTest(case=case.id, twin="clean"):
                self.assertEqual(graded(case, job(case))[1], "lighting PASS\n")
        _, text = graded(cases["01-orchard"], job(cases["01-orchard"], luma=0.54))
        self.assertIn(f"B1 lighting {DIR}/render.json:1 frame 0: luma 0.54 is 20.0% from the key's 0.45"
                      " (at most 10%)", text.splitlines())
        _, text = graded(cases["03-chapel"], job(cases["03-chapel"], sun=SUN))
        self.assertIn(f"B1 lighting {DIR}/state.json:1 sun is set but the key of Q1 has no sun", text.splitlines())

    def test_each_cut_fails(self):
        cases = evals.load(KIT, ROLE)
        self.assertEqual(len(cases), 3)
        for case in cases:
            for name, change, detail, text in CUTS:
                with self.subTest(case=case.id, cut=name):
                    grade, found = graded(case, change)
                    self.assertFalse(grade["pass"])
                    self.assertEqual(grade["detail"], detail)
                    self.assertEqual(found, text)

    def test_near_misses_pass(self):
        cases = evals.load(KIT, ROLE)
        self.assertEqual(len(cases), 3)
        for case in cases:
            for name, change in NEAR_MISSES:
                with self.subTest(case=case.id, near_miss=name):
                    grade, text = graded(case, change)
                    self.assertTrue(grade["pass"], grade["detail"])
                    self.assertEqual(grade["detail"], "lint PASS")
                    self.assertEqual(text, "lighting PASS\n")

    def test_it_runs_on_the_fake_harness(self):
        before = tree_hash(KIT / "evals")
        done, summary, found = run_eval(recorded)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "run 1: score 1.0\nnoise floor: null over 1 runs (not measured)\n")
        self.assertEqual(summary["per_run"], [{"blocked": 0, "lint_pass_rate": 1.0, "score": 1.0}])
        self.assertEqual(found, ["lint PASS"] * 3)
        self.assertEqual(tree_hash(KIT / "evals"), before)

    def test_a_withheld_output_scores_zero(self):
        before = tree_hash(KIT / "evals")
        done, _, found = run_eval(lambda c: {})
        self.assertEqual(done.stdout.splitlines()[0], "run 1: score 0.0", done.stderr)
        self.assertEqual(found, [f"lint PASS; missing {SCENE}, {REQUEST}"] * 3)
        self.assertEqual(tree_hash(KIT / "evals"), before)


if __name__ == "__main__":
    unittest.main()
