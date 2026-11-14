"""Tests for the shipped corpus cases of the ten production checks. No network, no model."""

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHIPPED = ROOT / "kit" / "evals" / "golden" / "corpus"

sys.path.insert(0, str(ROOT))
from kit.lib import cmd_corpus  # noqa: E402

RULES = {
    "intake": ["depends", "edit-list", "frames", "shots"],
    "assets": ["budgets", "naming", "references", "scale", "shape", "skeleton"],
    "rigging": ["interpenetration", "limits", "range-of-motion", "rig-hash", "shape"],
    "layout": ["depends", "duration", "framing", "lens", "line", "safe-zone", "state"],
    "animation": ["lipsync", "motion", "state"],
    "simulation": ["bounds", "depends", "hashes", "state"],
    "lighting": ["clipping", "color", "exposure", "key", "render", "state", "time-of-day"],
    "render": ["black", "count", "nan", "render", "rerender", "shots", "text"],
    "conform": ["captions", "depends", "edit-list", "loudness", "mix", "true-peak"],
    "cross-shot": ["cut", "shots", "state"],
}
LINES = {"animation": 5, "assets": 6, "conform": 6, "cross-shot": 4, "intake": 4,
         "layout": 7, "lighting": 7, "publish": 9, "render": 7, "rigging": 5, "simulation": 4}


def missing(corpus: Path) -> list[str]:
    """`<check>: no clean case` and `<check>: no seeded case` for each check directory lacking one."""
    out = []
    for check, cases in sorted(cmd_corpus.load(corpus, str(corpus)).items()):
        kinds = {case["kind"] for _, case in cases.values()}
        out += [f"{check}: no {kind} case" for kind in ("clean", "seeded") if kind not in kinds]
    return out


class ProductionCorpusTest(unittest.TestCase):
    def test_every_check_has_a_clean_and_a_seeded_case(self):
        cases = cmd_corpus.load(SHIPPED, str(SHIPPED))
        self.assertLessEqual(set(LINES), set(cases))
        self.assertEqual(missing(SHIPPED), [])
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "corpus"
            shutil.copytree(SHIPPED, copy)
            shutil.rmtree(copy / "intake" / "01-clean")
            for case in (copy / "render").iterdir():
                if case.name != "01-clean":
                    shutil.rmtree(case)
            self.assertEqual(missing(copy), ["intake: no clean case", "render: no seeded case"])

    def test_factory_corpus_lists_the_ten_checks(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cmd_corpus.main([])
        self.assertEqual(code, 0)
        lines = out.getvalue().splitlines()
        for check, n in LINES.items():
            self.assertIn(f"{check} PASS caught {n}/{n} clean 1/1", lines)
        self.assertRegex(lines[-1], r"^corpus PASS caught (\d+)/\1 share \S+ clean (\d+)/\2$")

    def test_one_seeded_case_per_rule(self):
        cases = cmd_corpus.load(SHIPPED, str(SHIPPED))
        for check, rules in RULES.items():
            found = {f.rule for path, case in cases.get(check, {}).values() if case["kind"] == "seeded"
                     for f in cmd_corpus.run_case(check, path).findings}
            self.assertEqual(sorted(found), rules, check)

    def test_no_python_file_in_the_trees(self):
        for check in sorted(LINES):
            self.assertTrue((SHIPPED / check).is_dir(), check)
            self.assertEqual(list((SHIPPED / check).rglob("*.py")), [], check)


if __name__ == "__main__":
    unittest.main()
