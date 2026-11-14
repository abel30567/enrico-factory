"""The showrunner's evidence ``result`` is the bare verb (ISSUE-441).

The ruling grader (``evals._grade_ruling``) matches each note's ``result`` exactly, so the role file
must say that ``result`` is one of five verbs with nothing after it and that the reason goes in
``summary``. Recorded answers show that a bare verb passes a ruling case and a verb with a reason
fails it. Nothing here runs a model.
"""

import json
import re
import unittest
from pathlib import Path

from kit.lib import evals

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "kit"
ROLE_FILE = KIT / "roles" / "showrunner.md"
FIXTURES = ROOT / "tests" / "fixtures" / "showrunner_result"
CASES = ("01-lamplighters", "02-lamp-street")
VERBS = ("acted on", "rejected", "selected", "approved", "sent on")
PARTS = ("exactly one of",) + tuple(f"`{verb}`" for verb in VERBS) + ("with nothing after it", "summary")


def problems(text):
    """One string per part the ``evidence`` bullet of a role file lacks; empty when it holds them all."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("- `evidence`:")), None)
    if start is None:
        return ["result: no `evidence` bullet"]
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("- ")), len(lines))
    bullet = re.sub(r"\s+", " ", " ".join(lines[start:end]))
    return [f"result: no {part}" for part in PARTS if part not in bullet]


class ResultVerbTest(unittest.TestCase):
    def setUp(self):
        self.cases = {case.id: case for case in evals.load(KIT, "showrunner")}

    def answer(self, case_id, kind):
        return json.loads((FIXTURES / f"{case_id}.{kind}.json").read_text(encoding="utf-8"))

    def test_the_role_file_is_clean(self):
        self.assertEqual(problems(ROLE_FILE.read_text(encoding="utf-8")), [])

    def test_the_old_wording_is_found(self):
        found = problems((FIXTURES / "old-wording.md").read_text(encoding="utf-8"))
        self.assertTrue(found)
        self.assertTrue(any("exactly one of" in problem for problem in found), found)

    def test_a_bare_verb_passes(self):
        for case_id in CASES:
            with self.subTest(case=case_id):
                graded = evals.grade(self.cases[case_id], self.answer(case_id, "bare"))
                self.assertTrue(graded["pass"], graded)
                self.assertEqual(graded["detail"], "every note ruled as given")

    def test_a_reason_after_the_verb_fails(self):
        for case_id in CASES:
            with self.subTest(case=case_id):
                case = self.cases[case_id]
                graded = evals.grade(case, self.answer(case_id, "reason"))
                self.assertFalse(graded["pass"], graded)
                self.assertEqual(graded["detail"], "wrong or no ruling: " + ", ".join(sorted(case.spec["results"])))


if __name__ == "__main__":
    unittest.main()
