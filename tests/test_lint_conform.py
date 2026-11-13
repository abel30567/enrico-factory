"""conform: loudness, true peak, captions aligned with the mix, the conformed edit list equal to the locked one."""

import filecmp
import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lint import ROOT, LintCase, cli, lint  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "lint_conform"
CLEAN = FIX / "clean"
LINES, REEL = "episodes/ep01/lines.json", "episodes/ep01/reel/edit-list.json"
CAPTIONS, EDIT, MIX = (f"production/ep01/conform/{name}.json" for name in ("captions", "edit-list", "mix"))
DEFECTS = {
    "loud": f"B1 conform {MIX}:1 integrated loudness -11.5 LUFS is outside -15 to -13 LUFS",
    "peak": f"B1 conform {MIX}:1 true peak -0.4 dBTP is above -1 dBTP",
    "late-cue": f"B1 conform {CAPTIONS}:1 L2: cue 75-110 is more than 2 frames from 72-110 in {MIX}",
    "moved-event": f"B1 conform {EDIT}:1 event 2: start is 5.5, {REEL} has 5",
}
NO_EVENT = "is not {shot, beat, start, duration}"
BAD_CUE = "is not {line, text, start, end} with frames 0 <= start <= end"


def load(rel, base=CLEAN):
    return json.loads((base / rel).read_text(encoding="utf-8"))


def dump(data):
    return json.dumps(data, indent=2) + "\n"


def tree_files(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


class Replace:
    def __init__(self, fn):
        self.fn = fn


class Conform(LintCase):
    CHECKS = ()  # every test runs conform alone

    def conform(self, root):
        return lint.run(root, only=["conform"])["conform"]

    def tree(self, edits=None, drop=()):
        """A copy of clean/ with `edits` applied and `drop` removed.

        `edits` maps a path to a function that changes the parsed file in place, or to `Replace(fn)`,
        where `fn` returns the new content.
        """
        files = {}
        for rel, fn in (edits or {}).items():
            data = load(rel)
            if isinstance(fn, Replace):
                data = fn.fn(data)
            else:
                fn(data)
            files[rel] = dump(data)
        root = self.variant(files, base=CLEAN)
        for rel in drop:
            path = root / rel
            shutil.rmtree(path) if path.is_dir() else path.unlink()
        return root

    def says(self, expected, edits=None, drop=(), root=None):
        """conform FAILs with exactly `expected` (path, rule, message), all on line 1."""
        result = self.conform(root or self.tree(edits, drop))
        self.assertEqual(result.status, lint.FAIL, result.reason)
        self.assertEqual([(f.path, f.line, f.rule, f.message) for f in result.findings],
                         sorted((path, 1, rule, message) for path, rule, message in expected))

    def passes(self, edits=None, drop=(), root=None):
        result = self.conform(root or self.tree(edits, drop))
        self.assertEqual(result.status, lint.PASS, (result.findings, result.reason))

    # Behaviour 1

    def test_clean_passes_and_each_defect_fails_with_its_line(self):
        p = cli("--root", str(CLEAN), "--only", "conform")
        self.assertEqual((p.returncode, p.stdout.decode()), (0, "conform PASS\n"), p.stderr)
        for name, line in DEFECTS.items():
            with self.subTest(name):
                p = cli("--root", str(FIX / name), "--only", "conform")
                self.assertEqual((p.returncode, p.stdout.decode()), (1, f"conform FAIL 1 finding\n{line}\n"),
                                 p.stderr)
                self.assertEqual(tree_files(FIX / name), tree_files(CLEAN))
                differ = [rel for rel in tree_files(CLEAN)
                          if not filecmp.cmp(CLEAN / rel, FIX / name / rel, shallow=False)]
                self.assertEqual(len(differ), 1, differ)

    def test_clean_copies_the_structure_fixture(self):
        src = ROOT / "tests" / "fixtures" / "lint_structure" / "clean"
        for rel, other in ((LINES, LINES), (REEL, REEL), (EDIT, REEL)):
            self.assertEqual((CLEAN / rel).read_bytes(), (src / other).read_bytes(), rel)

    # Behaviour 2: bounds

    def test_bound_loudness_minus_13_passes(self):
        self.passes({MIX: lambda d: d.update(loudness_lufs=-13)})

    def test_bound_loudness_minus_15_passes(self):
        self.passes({MIX: lambda d: d.update(loudness_lufs=-15)})

    def test_bound_true_peak_minus_1_passes(self):
        self.passes({MIX: lambda d: d.update(true_peak_dbtp=-1)})

    def test_bound_l2_cue_70_to_112_passes(self):
        self.passes({CAPTIONS: lambda d: d[1].update(start=70, end=112)})

    def test_bound_loudness_minus_15_5(self):
        self.says([(MIX, "loudness", "integrated loudness -15.5 LUFS is outside -15 to -13 LUFS")],
                  {MIX: lambda d: d.update(loudness_lufs=-15.5)})

    def test_bound_loudness_minus_12_9(self):
        self.says([(MIX, "loudness", "integrated loudness -12.9 LUFS is outside -15 to -13 LUFS")],
                  {MIX: lambda d: d.update(loudness_lufs=-12.9)})

    def test_bound_true_peak_minus_0_9(self):
        self.says([(MIX, "true-peak", "true peak -0.9 dBTP is above -1 dBTP")],
                  {MIX: lambda d: d.update(true_peak_dbtp=-0.9)})

    def test_bound_l3_cue_end_213(self):
        self.says([(CAPTIONS, "captions", f"L3: cue 150-213 is more than 2 frames from 150-210 in {MIX}")],
                  {CAPTIONS: lambda d: d[2].update(end=213)})

    # Behaviour 3: edit list

    def edit_says(self, message, edits):
        self.says([(EDIT, "edit-list", message)], edits)

    def test_edit_list_fps_25(self):
        self.edit_says(f"fps is 25, {REEL} has 24", {EDIT: lambda d: d.update(fps=25)})

    def test_edit_list_shot_changed(self):
        self.edit_says(f"event 1: shot is 'Q1-9', {REEL} has 'Q1-1'", {EDIT: lambda d: d["events"][0].update(shot="Q1-9")})

    def test_edit_list_duration_a_string(self):
        self.edit_says(f"event 2: duration is '12', {REEL} has 12",
                       {EDIT: lambda d: d["events"][1].update(duration="12")})

    def test_edit_list_last_event_removed(self):
        self.edit_says(f"events count is 1, {REEL} has 2", {EDIT: lambda d: d["events"].pop()})

    def test_edit_list_event_without_beat(self):
        self.edit_says(f"event 1 {NO_EVENT}", {EDIT: lambda d: d["events"][0].pop("beat")})

    def test_edit_list_conformed_a_list(self):
        self.edit_says("not {fps, events: [...]} with fps above zero", {EDIT: Replace(lambda d: d["events"])})

    def test_edit_list_locked_events_a_string(self):
        self.edit_says(f"{REEL} is not {{fps, events: [...]}}: nothing to compare",
                       {REEL: lambda d: d.update(events="Q1-1 Q1-2")})

    def test_edit_list_equal_values_and_no_bible_pass(self):
        def edit(data):
            data["events"][1]["start"] = 5.0
            del data["bible"]
        root = self.tree({EDIT: edit})
        self.assertIn('"start": 5.0', (root / EDIT).read_text(encoding="utf-8"))
        self.passes(root=root)

    # Behaviour 4: mix shape

    def test_mix_loudness_absent(self):
        self.says([(MIX, "mix", "loudness_lufs is not a number")], {MIX: lambda d: d.pop("loudness_lufs")})

    def test_mix_true_peak_a_bool(self):
        self.says([(MIX, "mix", "true_peak_dbtp is not a number")], {MIX: lambda d: d.update(true_peak_dbtp=True)})

    def test_mix_lines_a_dict(self):
        self.says([(MIX, "mix", "lines is not a list of {line, start, end}")],
                  {MIX: lambda d: d.update(lines={e["line"]: [e["start"], e["end"]] for e in d["lines"]})})

    def test_mix_entry_start_after_end(self):
        self.says([(MIX, "mix", "lines entry 1 is not {line, start, end} with frames 0 <= start <= end"),
                   (MIX, "mix", "lines has no entry for L1")],
                  {MIX: lambda d: d["lines"][0].update(start=5, end=2)})

    def test_mix_second_entry_for_l1(self):
        self.says([(MIX, "mix", "lines entry 4: L1 is heard already")],
                  {MIX: lambda d: d["lines"].append({"line": "L1", "start": 300, "end": 320})})

    def test_mix_l3_entry_removed(self):
        self.says([(MIX, "mix", "lines has no entry for L3")], {MIX: lambda d: d["lines"].pop()})

    def test_mix_without_stems_and_master_and_with_an_extra_key_passes(self):
        def edit(data):
            del data["stems"], data["master"]
            data["engineer"] = "nobody"
        self.passes({MIX: edit})

    # Behaviour 5: captions

    def test_captions_cue_for_l9(self):
        self.says([(CAPTIONS, "captions", f"cue 4: L9 is not a line of {LINES}")],
                  {CAPTIONS: lambda d: d.append({"line": "L9", "text": "Who is there?", "start": 220, "end": 240})})

    def test_captions_second_cue_for_l1(self):
        self.says([(CAPTIONS, "captions", "cue 4: L1 has a cue already (cue 1)")],
                  {CAPTIONS: lambda d: d.append(dict(d[0]))})

    def test_captions_l3_cue_removed(self):
        self.says([(CAPTIONS, "captions", "L3 has no cue")], {CAPTIONS: lambda d: d.pop()})

    def test_captions_text_a_number(self):
        self.says([(CAPTIONS, "captions", f"cue 1 {BAD_CUE}"), (CAPTIONS, "captions", "L1 has no cue")],
                  {CAPTIONS: lambda d: d[0].update(text=7)})

    def test_captions_start_not_a_frame(self):
        self.says([(CAPTIONS, "captions", f"cue 2 {BAD_CUE}"), (CAPTIONS, "captions", "L2 has no cue")],
                  {CAPTIONS: lambda d: d[1].update(start=1.5)})

    def test_captions_an_object(self):
        self.says([(CAPTIONS, "captions", "not a list of {line, text, start, end}")],
                  {CAPTIONS: Replace(lambda d: {"cues": d})})

    def test_captions_lines_json_an_object(self):
        self.says([(CAPTIONS, "captions", f"{LINES} is not a list of {{id, scene, speaker, text}}")],
                  {LINES: Replace(lambda d: {e["id"]: e for e in d})})

    def test_captions_text_is_not_compared(self):
        root = self.tree({CAPTIONS: lambda d: d[0].update(text="Something else entirely.")})
        self.assertNotEqual(load(CAPTIONS, root)[0]["text"], load(LINES, root)[0]["text"])
        self.passes(root=root)

    # Behaviour 6: depends

    def test_depends(self):
        self.says([(CAPTIONS, "depends", f"{LINES} is missing"), (CAPTIONS, "depends", f"{MIX} is missing")],
                  drop=(MIX, LINES))
        self.says([(MIX, "depends", f"{CAPTIONS} is missing"), (MIX, "depends", f"{EDIT} is missing")],
                  drop=(CAPTIONS, EDIT))
        self.passes(drop=("production",))
        root = self.tree(drop=("production",))
        (root / "production/assets/conform").mkdir(parents=True)
        shutil.copyfile(CLEAN / MIX, root / "production/assets/conform/mix.json")
        (root / "episodes").rename(root / "gone")
        self.passes(root=root)

    # Behaviour 7

    def test_mix_not_json_is_error(self):
        root = self.variant({MIX: "{"}, base=CLEAN)
        result = self.conform(root)
        self.assertEqual(result.status, lint.ERROR)
        self.assertEqual(result.reason, f"cannot parse {MIX}:1: Expecting property name enclosed in double quotes")

    # Behaviour 8

    def test_two_episodes_one_loud(self):
        root = self.tree()
        shutil.copytree(root / "episodes/ep01", root / "episodes/ep02")
        shutil.copytree(root / "production/ep01", root / "production/ep02")
        shutil.copyfile(FIX / "loud" / MIX, root / "production/ep02/conform/mix.json")
        mix2 = "production/ep02/conform/mix.json"
        self.says([(mix2, "loudness", "integrated loudness -11.5 LUFS is outside -15 to -13 LUFS")], root=root)

    # Behaviour 9

    def test_json_output_is_deterministic(self):
        """Every defect of Behaviour 4 at once; `lines` a dict excludes the entry defects, so it is a second tree."""
        def entries(data):
            del data["loudness_lufs"]
            data["true_peak_dbtp"] = True
            data["lines"][0].update(start=5, end=2)
            data["lines"][2] = {"line": "L2", "start": 72, "end": 110}

        def as_dict(data):
            data["lines"] = {e["line"]: [e["start"], e["end"]] for e in data["lines"]}
        for edit, count in ((entries, 6), (as_dict, 1)):
            root = self.tree({MIX: edit})
            runs = [cli("--root", str(root), "--only", "conform", "--json") for _ in range(2)]
            self.assertEqual(runs[0].returncode, 1, runs[0].stderr)
            self.assertEqual(runs[0].stdout, runs[1].stdout)
            self.assertEqual(len(json.loads(runs[0].stdout)["checks"]["conform"]["findings"]), count)
        self.says([(MIX, "mix", "loudness_lufs is not a number"), (MIX, "mix", "true_peak_dbtp is not a number"),
                   (MIX, "mix", "lines entry 1 is not {line, start, end} with frames 0 <= start <= end"),
                   (MIX, "mix", "lines entry 3: L2 is heard already"), (MIX, "mix", "lines has no entry for L1"),
                   (MIX, "mix", "lines has no entry for L3")], {MIX: entries})

    # A tree without the inputs

    def test_root_without_inputs_passes(self):
        root = self.variant({}, base=CLEAN)
        shutil.rmtree(root / "production")
        shutil.rmtree(root / "episodes")
        self.passes(root=root)


if __name__ == "__main__":
    unittest.main()
