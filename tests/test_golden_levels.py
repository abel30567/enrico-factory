"""The golden test levels in kit/evals/golden/ and `kit.lib.golden`, checked without the engine.

The recorded files are hand-written fixtures: nothing here runs or imports the engine.
"""

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "kit"
GOLDEN = KIT / "evals" / "golden"
FIX = ROOT / "tests" / "fixtures" / "golden_levels"

sys.path.insert(0, str(ROOT))
from kit.lib import frontmatter, golden, production  # noqa: E402

UNITS = ("lit-render", "prop-drop", "two-shot/Q1-1", "two-shot/Q1-2")
CLOCK = "reads the clock: "
RANDOM = "uses a random number without a seed: "


class GoldenTest(unittest.TestCase):
    def tempdir(self) -> Path:
        path = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, path)
        return path

    def shipped(self) -> Path:
        """A copy of the shipped scripts, not recorded."""
        root = self.tempdir() / "golden"
        for unit in UNITS:
            (root / unit).mkdir(parents=True)
            shutil.copy(GOLDEN / unit / "scene.py", root / unit / "scene.py")
        return root

    def fixture(self, *units) -> Path:
        """A copy of the shipped scripts with the recorded fixture files of `units`, not sealed."""
        root = self.shipped()
        for unit in units:
            for path in (FIX / unit).iterdir():
                shutil.copy(path, root / unit / path.name)
        return root

    @staticmethod
    def seal(unit: Path):
        """Write the true sha256 and bytes of `scene.py` and of each output into the manifest."""
        manifest = json.loads((unit / "manifest.json").read_text(encoding="utf-8"))
        for row in manifest["inputs"]:
            if row["path"] == "scene.py":
                row["sha256"] = hashlib.sha256((unit / "scene.py").read_bytes()).hexdigest()
        for row in manifest["outputs"]:
            data = (unit / row["name"]).read_bytes()
            row["sha256"], row["bytes"] = hashlib.sha256(data).hexdigest(), len(data)
        (unit / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def edit(path: Path, change):
        value = json.loads(path.read_text(encoding="utf-8"))
        change(value)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def sealed(self, unit: str, name: str | None = None, change=None) -> Path:
        """The fixture of `unit`, `change` applied to its file `name`, then sealed."""
        root = self.fixture(unit)
        if change is not None:
            self.edit(root / unit / name, change)
        self.seal(root / unit)
        return root

    def append(self, line: str, unit: str = "prop-drop") -> tuple[list[str], int]:
        """The findings on `unit` with `line` appended to its script, and that line's number."""
        root = self.shipped()
        script = root / unit / "scene.py"
        text = script.read_text(encoding="utf-8") + line + "\n"
        script.write_text(text, encoding="utf-8")
        return golden.unit_problems(root, unit), len(text.splitlines())


class ShippedLevelsTest(GoldenTest):
    def test_shipped_levels(self):
        loaded = "unreal" in sys.modules
        first = golden.check(GOLDEN)
        self.assertEqual(first, [])
        self.assertEqual(golden.check(GOLDEN), first)
        self.assertEqual("unreal" in sys.modules, loaded)
        self.assertNotIn("unreal", sys.modules)
        self.assertEqual(sorted(golden.UNITS), list(UNITS))
        for unit in UNITS:
            self.assertTrue(golden.recorded(GOLDEN / unit), unit)
        root = self.shipped()
        (root / "prop-drop" / "notes.txt").write_text("x\n", encoding="utf-8")
        self.assertEqual(golden.check(root), ["prop-drop/notes.txt is not a file of a golden level"])
        root = self.shipped()
        (root / "lit-render" / "scene.py").unlink()
        self.assertEqual(golden.check(root), ["lit-render/scene.py missing"])

    def test_shipped_recordings(self):
        root = self.shipped()
        for unit in UNITS:
            for path in (GOLDEN / unit).iterdir():
                shutil.copy(path, root / unit / path.name)
        self.assertEqual(golden.check(root), [])
        for unit in UNITS:
            with (root / unit / "state.json").open("a", encoding="utf-8") as state:
                state.write("\n")
        self.assertEqual(golden.check(root), [
            f"{unit}/manifest.json does not record state.json at its sha256 and size" for unit in UNITS])


class ScriptRulesTest(GoldenTest):
    CASES = (
        ("import requests", "imports requests, which is not unreal or the standard library"),
        ("import socket", "imports socket, a network module"),
        ("import time", CLOCK + "time"),
        ("from datetime import datetime", CLOCK + "datetime"),
        ("x = unreal.DateTime.now()", CLOCK + "now"),
        ("import uuid", RANDOM + "uuid"),
        ("x = random.random()", RANDOM + "random.random"),
        ("r = random.Random()", RANDOM + "random.Random"),
        ('p = "/tmp/out.png"', "names a path outside the job directory: '/tmp/out.png'"),
        ('q = "../x"', "names a path outside the job directory: '../x'"),
        ('s = "out/state.json"', "names the state file state.json, which only the job writes"),
        ('a = "asset:prop"', "names a production asset: 'asset:prop'"),
        ("b = unreal.BlueprintFunctionLibrary", "uses a Blueprint: BlueprintFunctionLibrary"),
        ('exec("1")', "runs code it does not show: exec"),
        ("import importlib", "runs code it does not show: importlib"),
        ("def (:", "is not Python 3"),
    )

    def test_one_defect_per_rule(self):
        for line, message in self.CASES:
            with self.subTest(line=line):
                found, n = self.append(line)
                self.assertEqual(found, [f"prop-drop/scene.py:{n} {message}"])

    def test_twins(self):
        for line in ("import random\nr = random.Random(118)", 'm = "/Engine/BasicShapes/Sphere.Sphere"',
                     'f = "out/frame.png"'):
            with self.subTest(line=line):
                self.assertEqual(self.append(line)[0], [])

    def test_script_problems_without_level(self):
        text = (GOLDEN / "prop-drop" / "scene.py").read_text(encoding="utf-8")
        self.assertEqual(golden.script_problems(text, "s.py"), [])
        self.assertEqual(golden.script_problems("import time\n", "s.py"), ["s.py:1 reads the clock: time"])
        self.assertEqual(golden.script_problems("import time\n", "s.py", {}), [
            "s.py:1 has no LEVEL literal", "s.py:1 reads the clock: time"])


class LevelTest(GoldenTest):
    def test_level(self):
        root = self.shipped()
        script = root / "prop-drop" / "scene.py"
        lines = script.read_text(encoding="utf-8").splitlines(keepends=True)
        start = next(i for i, line in enumerate(lines) if line.startswith("LEVEL = {"))
        end = next(i for i in range(start, len(lines)) if lines[i] == "}\n")
        script.write_text("".join(lines[:start] + lines[end + 1:]), encoding="utf-8")
        self.assertEqual(golden.check(root), ["prop-drop/scene.py:1 has no LEVEL literal"])

        root = self.shipped()
        script = root / "prop-drop" / "scene.py"
        text = script.read_text(encoding="utf-8")
        self.assertEqual(text.count('"fps": 24,'), 1)
        script.write_text(text.replace('"fps": 24,', '"fps": 25,'), encoding="utf-8")
        self.assertEqual(golden.check(root), [f"prop-drop/scene.py:{start + 1} LEVEL differs from golden.UNITS at fps"])

        root = self.shipped()
        first = (root / "two-shot/Q1-1" / "scene.py").read_text(encoding="utf-8")
        second = root / "two-shot/Q1-2" / "scene.py"
        camera = next(line for line in first.splitlines(keepends=True) if line.startswith('    "camera": '))
        lines = second.read_text(encoding="utf-8").splitlines(keepends=True)
        at = next(i for i, line in enumerate(lines) if line.startswith('    "camera": '))
        self.assertNotEqual(lines[at], camera)
        lines[at] = camera
        second.write_text("".join(lines), encoding="utf-8")
        start = next(i for i, line in enumerate(lines) if line.startswith("LEVEL = {"))
        self.assertEqual(golden.check(root), [f"two-shot/Q1-2/scene.py:{start + 1} LEVEL differs from golden.UNITS at camera"])


class RecordedTest(GoldenTest):
    def test_clean_fixtures(self):
        root = self.fixture(*UNITS)
        for unit in UNITS:
            self.seal(root / unit)
            self.assertTrue(golden.recorded(root / unit), unit)
            self.assertEqual(golden.unit_problems(root, unit), [], unit)
        self.assertEqual(golden.check(root), [])

    def test_recorded_or_not(self):
        root = self.sealed("prop-drop")
        (root / "prop-drop" / "render.json").unlink()
        self.assertEqual(golden.check(root), ["prop-drop is partly recorded: render.json missing"])
        root = self.sealed("lit-render")
        (root / "lit-render" / "rerun.json").write_text("{}\n", encoding="utf-8")
        self.assertEqual(golden.check(root), ["lit-render/rerun.json is not a file of a golden level"])
        root = self.fixture("prop-drop")
        (root / "prop-drop" / "state.json").write_text("[]\n", encoding="utf-8")
        self.seal(root / "prop-drop")
        self.assertEqual(golden.check(root), ["prop-drop/state.json is not a JSON object"])


class ManifestTest(GoldenTest):
    def test_manifest_shape(self):
        def seeds(m):
            m["seeds"] = [0]

        def size(m):
            m["outputs"][0]["bytes"] = str(m["outputs"][0]["bytes"])

        def job(m):
            m["job"] = m["job"][:63]

        for change in (seeds, size, job):
            with self.subTest(change=change.__name__):
                root = self.sealed("prop-drop")
                self.edit(root / "prop-drop" / "manifest.json", change)
                self.assertEqual(golden.check(root),
                                 ["prop-drop/manifest.json is not {job, engine, flags, inputs, seeds, outputs}"])


class HashTest(GoldenTest):
    def test_hashes(self):
        root = self.sealed("prop-drop")
        with (root / "prop-drop" / "scene.py").open("a", encoding="utf-8") as handle:
            handle.write("# a comment\n")
        self.assertEqual(golden.check(root), ["prop-drop/manifest.json does not record scene.py at its sha256"])
        root = self.sealed("prop-drop")
        with (root / "prop-drop" / "state.json").open("a", encoding="utf-8") as handle:
            handle.write("\n")
        self.assertEqual(golden.check(root),
                         ["prop-drop/manifest.json does not record state.json at its sha256 and size"])


class StateValuesTest(GoldenTest):
    def test_state_values(self):
        def actor(s):
            next(a for a in s["actors"] if a["id"] == "prop")["id"] = "cube"

        cases = (
            ("prop-drop", "state.json", lambda s: s.pop("lipsync"), "prop-drop/state.json lacks lipsync"),
            ("prop-drop", "state.json", lambda s: s.update(fps=25), "prop-drop/state.json fps is 25, not 24"),
            ("prop-drop", "state.json", lambda s: s["render"].update(lumen=True),
             "prop-drop/state.json render.lumen is true, not false"),
            ("prop-drop", "state.json", actor,
             'prop-drop/state.json actors[].id is ["cube", "floor"], not ["floor", "prop"]'),
            ("prop-drop", "rerun.json", lambda s: s.update(stage="layout"),
             'prop-drop/rerun.json stage is "layout", not "simulation"'),
            ("two-shot/Q1-2", "state.json", lambda s: s.update(frames={"first": 0, "last": 23}),
             'two-shot/Q1-2/state.json frames is {"first": 0, "last": 23}, not {"first": 24, "last": 47}'),
        )
        for unit, name, change, line in cases:
            with self.subTest(line=line):
                self.assertEqual(golden.check(self.sealed(unit, name, change)), [line])


class RenderValuesTest(GoldenTest):
    def test_render_values(self):
        cases = (
            ("prop-drop", lambda r: r.update(count=47), "prop-drop/render.json count is 47, not 48"),
            ("lit-render", lambda r: r.pop("rerender"), "lit-render/render.json lacks rerender"),
            ("prop-drop", lambda r: r.update(rerender={"frames": [0], "metric": "ssim", "scores": [1.0]}),
             "prop-drop/render.json holds rerender, which only lit-render records"),
        )
        for unit, change, line in cases:
            with self.subTest(line=line):
                self.assertEqual(golden.check(self.sealed(unit, "render.json", change)), [line])


class FrozenTest(unittest.TestCase):
    def test_no_role_writes_a_level(self):
        probes = ("kit/evals/golden/prop-drop/scene.py", "kit/evals/golden/two-shot/Q1-1/state.json",
                  ".factory/evals/golden/lit-render/render.json")
        for path in sorted((KIT / "roles").glob("*.md")):
            meta, _ = frontmatter.parse(path.read_text(encoding="utf-8"))
            writes = frontmatter.as_list(meta.get("writes"))
            for probe in probes:
                self.assertFalse(production.matches(probe, writes), f"{path.name} writes {probe}")
        self.assertTrue(production.matches(probes[0], ["kit/evals/**"]))


if __name__ == "__main__":
    unittest.main()
