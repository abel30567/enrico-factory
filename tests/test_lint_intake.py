"""intake: an episode's shot list equals its locked edit list frame for frame."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lint import ROOT, LintCase, cli, lint  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "lint_intake"
CLEAN = FIX / "clean"
DURATION_CHANGED = FIX / "duration_changed"
R1, R2 = "episodes/ep01/reel/edit-list.json", "episodes/ep02/reel/edit-list.json"
S1, S2 = "production/ep01/shots.json", "production/ep02/shots.json"
NOT_ROW = "row 1 is not {shot, first, last} with a string and two whole numbers"


def load(rel):
    return json.loads((CLEAN / rel).read_text(encoding="utf-8"))


def dump(data):
    return json.dumps(data, indent=2) + "\n"


class Intake(LintCase):
    CHECKS = ()

    def intake(self, root):
        return lint.run(root, only=["intake"])["intake"]

    def tree(self, files=None, drop=()):
        """A copy of the clean fixture with `files` written over it and `drop` removed."""
        root = self.variant(files or {}, base=CLEAN)
        for rel in drop:
            (root / rel).unlink()
        return root

    def says(self, expected, files=None, drop=(), root=None):
        """intake FAILs with exactly `expected` (path, line, rule, message)."""
        result = self.intake(root or self.tree(files, drop))
        self.assertEqual(result.status, lint.FAIL, result.reason)
        self.assertEqual([(f.path, f.line, f.rule, f.message) for f in result.findings], expected)

    def passes(self, files=None, drop=(), root=None):
        result = self.intake(root or self.tree(files, drop))
        self.assertEqual(result.status, lint.PASS, f"{result.findings} {result.reason}")

    def rows(self, change):
        """ep01's shot list with `change(rows)` applied."""
        rows = load(S1)
        change(rows)
        return {S1: dump(rows)}

    def events(self, change, rel=R1):
        data = load(rel)
        change(data["events"])
        return {rel: dump(data)}

    # 1. Clean

    def test_clean_passes(self):
        self.passes(root=CLEAN)
        p = cli("--root", str(CLEAN), "--only", "intake")
        self.assertEqual((p.returncode, p.stdout.decode()), (0, "intake PASS\n"), p.stderr.decode())

    # 2. Frames

    def test_duration_changed_fails_on_the_cli(self):
        p = cli("--root", str(DURATION_CHANGED), "--only", "intake")
        self.assertEqual(p.stdout.decode(), "intake FAIL 1 finding\nB1 intake production/ep01/shots.json:1 "
                                             "row 2 (Q1-2): frames 60-143, the edit list gives 60-131\n")
        self.assertEqual(p.returncode, 1, p.stderr.decode())

    def test_duration_changed_in_the_edit_list(self):
        self.says([(S1, 1, "frames", "row 2 (Q1-2): frames 60-131, the edit list gives 60-143")],
                  self.events(lambda e: e[1].update(duration=3.5)))

    # 3. Depends

    def test_missing_edit_list(self):
        self.says([(S1, 1, "depends", f"{R1} is missing")], drop=[R1])

    def test_no_shot_lists_pass(self):
        self.passes(drop=[S1, S2])
        with tempfile.TemporaryDirectory() as tmp:
            self.passes(root=Path(tmp))

    def test_assets_is_not_an_episode(self):
        self.passes({"production/assets/shots.json": "{"})

    # 4. Length

    def test_too_few_rows(self):
        self.says([(S1, 1, "shots", f"2 rows for 3 events of {R1}")], self.rows(lambda r: r.pop()))

    def test_too_many_rows(self):
        self.says([(S1, 1, "shots", f"4 rows for 3 events of {R1}")], self.rows(lambda r: r.append(dict(r[2]))))

    # 5. Shots

    def test_swapped_shots(self):
        def swap(r):
            r[0]["shot"], r[1]["shot"] = r[1]["shot"], r[0]["shot"]
        self.says([(S1, 1, "shots", "row 1: shot Q1-2, event 1 of the edit list is shot Q1-1"),
                   (S1, 1, "shots", "row 2: shot Q1-1, event 2 of the edit list is shot Q1-2")], self.rows(swap))

    # 6. Row shape

    def test_malformed_rows(self):
        cases = {
            "no last": lambda r: r[0].pop("last"),
            "shot a number": lambda r: r[0].update(shot=1),
            "first a float": lambda r: r[0].update(first=0.0),
            "first a bool": lambda r: r[0].update(first=False),
            "a list": lambda r: r.__setitem__(0, ["Q1-1", 0, 59]),
        }
        for name, change in cases.items():
            with self.subTest(name):
                self.says([(S1, 1, "shots", NOT_ROW)], self.rows(change))

    def test_not_a_list(self):
        self.says([(S1, 1, "shots", "not a list of {shot, first, last}")], {S1: dump({"shots": []})})

    def test_extra_key_is_ignored(self):
        self.passes(self.rows(lambda r: r[0].update(note="hold")))

    # 7. Whole frames

    def test_not_whole_frames(self):
        def change(e):
            e[1]["start"] = 2.51
            e[2]["duration"] = 4.26
        self.says([(R1, 1, "edit-list", "event 2: start 2.51 s at 24 fps is not a whole frame"),
                   (R1, 1, "edit-list", "event 3: duration 4.26 s at 24 fps is not a whole number of frames")],
                  self.events(change))

    def test_whole_within_the_tolerance(self):
        self.assertNotEqual(0.28 * 25, 7)  # ep02's first event ends between frames 6 and 7
        self.passes(drop=[S1])

    # 8. Edit list shape

    def test_edit_list_without_fps(self):
        self.says([(R1, 1, "edit-list", "not {fps, events: [...]} with fps above zero")], {R1: dump({"fps": 0})})

    def test_event_without_duration(self):
        self.says([(R1, 1, "edit-list", "event 1 is not {shot, start, duration} with a duration above zero")],
                  self.events(lambda e: e[0].update(duration=0)))

    def test_edit_list_not_json(self):
        result = self.intake(self.tree({R1: "{"}))
        self.assertEqual(result.status, lint.ERROR)
        self.assertIn(f"cannot parse {R1}:", result.reason)


if __name__ == "__main__":
    unittest.main()
