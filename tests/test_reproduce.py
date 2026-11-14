"""Tests for kit/lib/reproduce.py and kit/lib/cmd_reproduce.py: `factory reproduce`, the exact render reproducibility check.

The four runs under tests/fixtures/reproduce/ are hand-written in the shapes of brief section 16: `first`, its
clean twin `second` (differs only in keys that are not compared), `state-differs` and `render-differs`. Every
other seeded case is made here in a temporary copy. Nothing reaches the network, `gh` or a model.
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import cmd_reproduce, reproduce  # noqa: E402

FIX = ROOT / "tests/fixtures/reproduce"
FIRST = str(FIX / "first")
SECOND = str(FIX / "second")
NOTE_2 = "note render.json frames_sha256: 2 of 12 frame hashes differ; frames are not compared exactly"
NOTE_0 = "note render.json frames_sha256: 0 of 12 frame hashes differ; frames are not compared exactly"


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cmd_reproduce.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Copies(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def copy(self, name, as_name=None):
        dest = self.tmp / (as_name or name)
        shutil.copytree(FIX / name, dest)
        return dest

    @staticmethod
    def edit(path, change):
        value = json.loads(path.read_text())
        change(value)
        path.write_text(json.dumps(value))


class CleanPair(unittest.TestCase):
    """Behaviour 1."""

    def test_clean_twin_is_equal(self):
        self.assertEqual(run(FIRST, SECOND), (0, f"reproduce EQUAL\n{NOTE_2}\n", ""))

    def test_same_run_twice_is_equal_with_no_hash_differing(self):
        self.assertEqual(run(FIRST, FIRST), (0, f"reproduce EQUAL\n{NOTE_0}\n", ""))
        self.assertEqual(run(FIRST + "/", FIRST), (0, f"reproduce EQUAL\n{NOTE_0}\n", ""))

    def test_twin_really_differs_in_uncompared_keys(self):
        a = json.loads((FIX / "first/render.json").read_text())
        b = json.loads((FIX / "second/render.json").read_text())
        for key in ("frames_sha256", "sequence_sha256", "samples", "rerender"):
            self.assertNotEqual(a[key], b[key], key)
        self.assertNotEqual((FIX / "first/state.json").read_text(), (FIX / "second/state.json").read_text())


class SeededPairs(unittest.TestCase):
    """Behaviours 2 and 3."""

    def test_state_differs(self):
        self.assertEqual(run(FIRST, str(FIX / "state-differs")),
                         (1, "reproduce DIFFERENT 1 difference\n"
                             "state.json render.spatial_samples: 8 in first, 4 in second\n" f"{NOTE_2}\n", ""))

    def test_render_differs(self):
        self.assertEqual(run(FIRST, str(FIX / "render-differs")),
                         (1, "reproduce DIFFERENT 1 difference\n"
                             "render.json black[0]: absent in first, 7 in second\n" f"{NOTE_2}\n", ""))


class RestOfState(Copies):
    """Behaviours 4 and 8."""

    def seeded(self):
        run_dir = self.copy("second", "seeded")

        def change(state):
            state["actors"][2]["end"]["location"][2] = 0.01
            state["caches"].append({"id": "x"})
            del state["sun"]
            state["extra"] = 1
        self.edit(run_dir / "state.json", change)
        return str(run_dir)

    def test_every_key_of_the_state_is_compared(self):
        code, out, err = run(FIRST, self.seeded())
        self.assertEqual((code, err), (1, ""))
        self.assertEqual(out.splitlines(), [
            "reproduce DIFFERENT 4 differences",
            "state.json actors[2].end.location[2]: 0 in first, 0.01 in second",
            'state.json caches[0]: absent in first, {"id": "x"} in second',
            "state.json extra: absent in first, 1 in second",
            "state.json sun: null in first, absent in second",
            NOTE_2,
        ])

    def test_output_is_deterministic_and_swaps_with_the_arguments(self):
        seeded = self.seeded()
        self.assertEqual(run(FIRST, seeded), run(FIRST, seeded))
        self.assertEqual(run(seeded, FIRST)[1].splitlines(), [
            "reproduce DIFFERENT 4 differences",
            "state.json actors[2].end.location[2]: 0.01 in first, 0 in second",
            'state.json caches[0]: {"id": "x"} in first, absent in second',
            "state.json extra: 1 in first, absent in second",
            "state.json sun: absent in first, null in second",
            NOTE_2,
        ])
        self.assertEqual(run(str(FIX / "state-differs"), FIRST)[1].splitlines()[1],
                         "state.json render.spatial_samples: 4 in first, 8 in second")


class Types(Copies):
    """Behaviour 5."""

    def test_integer_and_float_differ(self):
        run_dir = self.copy("state-differs")

        def scale(value):
            def change(state):
                state["actors"][0]["scale"][0] = value
            return change
        self.edit(run_dir / "state.json", scale(1.0))
        self.assertEqual(run(FIRST, str(run_dir)), (1, "reproduce DIFFERENT 2 differences\n"
                                                       "state.json actors[0].scale[0]: 1 in first, 1.0 in second\n"
                                                       "state.json render.spatial_samples: 8 in first, 4 in second\n"
                                                       f"{NOTE_2}\n", ""))
        self.edit(run_dir / "state.json", scale(1))
        self.assertEqual(run(FIRST, str(run_dir))[1].splitlines()[:2],
                         ["reproduce DIFFERENT 1 difference",
                          "state.json render.spatial_samples: 8 in first, 4 in second"])

    def test_differences_on_json_values(self):
        self.assertEqual(reproduce.differences(True, 1, "x"), ["x: true in first, 1 in second"])
        self.assertEqual(reproduce.differences({"b": [1], "a": 2}, {"a": 2, "b": [1]}), [])
        self.assertEqual(reproduce.differences({"a": [1, 2]}, {"a": [1]}), ["a[1]: 2 in first, absent in second"])
        self.assertEqual(reproduce.differences({"a": "é"}, {"a": None}), ['a: "é" in first, null in second'])


class RenderKeys(Copies):
    """Behaviour 6."""

    def test_compared_keys_of_render_json(self):
        run_dir = self.copy("second")

        def change(render):
            render["count"] = 11
            render["text"] = [{"frame": 3, "text": "LIGHTING NEEDS TO BE REBUILT"}]
        self.edit(run_dir / "render.json", change)
        code, out, err = run(FIRST, str(run_dir))
        self.assertEqual((code, err), (1, ""))
        self.assertEqual(out.splitlines(), [
            "reproduce DIFFERENT 2 differences",
            "render.json count: 12 in first, 11 in second",
            'render.json text[0]: absent in first, {"frame": 3, "text": "LIGHTING NEEDS TO BE REBUILT"} in second',
            NOTE_2,
        ])

    def test_other_keys_of_render_json_are_not_compared(self):
        run_dir = self.copy("second")

        def change(render):
            del render["rerender"]
            render["extra"] = 1
        self.edit(run_dir / "render.json", change)
        self.assertEqual(run(FIRST, str(run_dir)), (0, f"reproduce EQUAL\n{NOTE_2}\n", ""))

    def test_render_lines_come_before_state_lines(self):
        run_dir = self.copy("state-differs")
        self.edit(run_dir / "render.json", lambda render: render.update(black=[7]))
        self.assertEqual(run(FIRST, str(run_dir))[1].splitlines()[1:3],
                         ["render.json black[0]: absent in first, 7 in second",
                          "state.json render.spatial_samples: 8 in first, 4 in second"])

    def test_note_counts_hashes_missing_on_one_side(self):
        run_dir = self.copy("first")
        self.edit(run_dir / "render.json", lambda render: render["frames_sha256"].extend(["x", "y"]))
        self.assertEqual(run(FIRST, str(run_dir))[1].splitlines()[-1],
                         "note render.json frames_sha256: 2 of 14 frame hashes differ; frames are not compared exactly")


class Refusals(Copies):
    """Behaviour 7."""

    def refused(self, first, second, lines):
        self.assertEqual(run(str(first), str(second)), (2, "", "".join(f"reproduce: {line}\n" for line in lines)))

    def test_missing_directory(self):
        missing = self.tmp / "nowhere"
        self.refused(FIRST, f"{missing}/", [f"{missing}/render.json is missing", f"{missing}/state.json is missing"])

    def test_nan_in_state(self):
        run_dir = self.copy("second")
        path = run_dir / "state.json"
        path.write_text(path.read_text().replace('"height_m":30', '"height_m":NaN', 1))
        self.assertIn("NaN", path.read_text())
        self.refused(FIRST, run_dir, [f"{path} is not JSON"])

    def test_not_json_and_not_utf8(self):
        run_dir = self.copy("second")
        (run_dir / "render.json").write_text("{")
        (run_dir / "state.json").write_bytes(b'{"shot": "\xff"}')
        self.refused(FIRST, run_dir, [f"{run_dir}/render.json is not JSON", f"{run_dir}/state.json is not JSON"])

    def test_render_json_not_an_object(self):
        run_dir = self.copy("second")
        (run_dir / "render.json").write_text("[1]")
        self.refused(FIRST, run_dir, [f"{run_dir}/render.json is not a JSON object"])

    def test_lumen_not_a_boolean(self):
        run_dir = self.copy("second")
        self.edit(run_dir / "state.json", lambda state: state["render"].update(lumen=1))
        self.refused(FIRST, run_dir, [f"{run_dir}/state.json: render is not {{lumen, tsr, spatial_samples, "
                                      "temporal_samples, warmup_frames, width, height} with two booleans, then integers"])

    def test_warmup_frames_not_an_integer(self):
        run_dir = self.copy("second")
        self.edit(run_dir / "state.json", lambda state: state["render"].update(warmup_frames=32.0))
        self.refused(FIRST, run_dir, [f"{run_dir}/state.json: render is not {{lumen, tsr, spatial_samples, "
                                      "temporal_samples, warmup_frames, width, height} with two booleans, then integers"])

    def test_frames_sha256_missing(self):
        run_dir = self.copy("second")
        self.edit(run_dir / "render.json", lambda render: render.pop("frames_sha256"))
        self.refused(FIRST, run_dir, [f"{run_dir}/render.json: frames_sha256 is missing or not a list of strings"])

    def test_shot_missing(self):
        run_dir = self.copy("second")
        self.edit(run_dir / "state.json", lambda state: state.pop("shot"))
        self.refused(FIRST, run_dir, [f"{run_dir}/state.json: shot is missing or not a string"])

    def test_every_render_problem_in_order(self):
        run_dir = self.copy("second")

        def change(render):
            for key in ("count", "fps", "height", "width"):
                render[key] = True
            render["frames"] = {"first": 0}
            render["black"] = [1.0]
            render["nan"] = None
            render["text"] = [{"frame": 1}]
            render["frames_sha256"] = [1]
        self.edit(run_dir / "render.json", change)
        label = f"{run_dir}/render.json"
        self.refused(FIRST, run_dir, [f"{label}: count is missing or not an integer",
                                      f"{label}: fps is missing or not an integer",
                                      f"{label}: height is missing or not an integer",
                                      f"{label}: width is missing or not an integer",
                                      f"{label}: frames is missing or not {{first, last}} with two integers",
                                      f"{label}: black is missing or not a list of frames",
                                      f"{label}: nan is missing or not a list of frames",
                                      f"{label}: text is missing or not a list of {{frame, text}}",
                                      f"{label}: frames_sha256 is missing or not a list of strings"])

    def test_problems_in_both_runs_print_the_first_run_first(self):
        a, b = self.copy("first", "a"), self.copy("second", "b")
        for run_dir in (a, b):
            self.edit(run_dir / "state.json", lambda state: state.update(shot=1, stage=None))
        (b / "render.json").unlink()
        self.refused(a, b, [f"{b}/render.json is missing",
                            f"{a}/state.json: shot is missing or not a string",
                            f"{a}/state.json: stage is missing or not a string",
                            f"{b}/state.json: shot is missing or not a string",
                            f"{b}/state.json: stage is missing or not a string"])

    def test_compare_raises_with_the_problems(self):
        with self.assertRaises(reproduce.ReproduceError) as caught:
            reproduce.compare(FIRST, str(self.tmp / "nowhere"))
        self.assertEqual(caught.exception.problems, [f"{self.tmp}/nowhere/render.json is missing",
                                                     f"{self.tmp}/nowhere/state.json is missing"])

    def test_one_argument_is_a_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            cmd_reproduce.main([FIRST])
        self.assertEqual(caught.exception.code, 2)


class Entrypoints(unittest.TestCase):
    """Behaviour 9."""

    def python(self, *argv):
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        done = subprocess.run([sys.executable, *argv], cwd=ROOT, capture_output=True, text=True, env=env)
        return done.returncode, done.stdout, done.stderr

    def test_factory_and_module_give_the_same_output(self):
        for first, second in ((FIRST, SECOND), (FIRST, str(FIX / "state-differs")), (FIRST, "/nonexistent")):
            expected = run(first, second)
            self.assertEqual(self.python("kit/factory", "reproduce", first, second), expected)
            self.assertEqual(self.python("kit/lib/cmd_reproduce.py", first, second), expected)

    def test_one_argument_exits_2(self):
        self.assertEqual(self.python("kit/factory", "reproduce", FIRST)[0], 2)
        self.assertEqual(self.python("kit/lib/cmd_reproduce.py", FIRST)[0], 2)

    def test_help_lists_reproduce(self):
        code, out, _ = self.python("kit/factory", "help")
        self.assertEqual(code, 0)
        self.assertIn("  reproduce\n", out)


if __name__ == "__main__":
    unittest.main()
