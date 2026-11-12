"""The amendment's `class` in the canon keeper and canon reviewer role files (brief section 9, rule 6).

Lock rule 6 (``kit/lib/lockrules.py``) refuses an amendment whose ``class`` is not one of
``lockrules.AMENDMENT_CLASSES`` and asks a vote for ``lockrules.VOTE_CLASSES``. The maker's role file has to
say so and show a valid example; the checker's role file has to check it.

Fixtures under tests/fixtures/amendment_class/ are copies of a role file with one defect each.
"""

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import lockrules  # noqa: E402

KEEPER = ROOT / "kit" / "roles" / "canon-keeper.md"
REVIEWER = ROOT / "kit" / "roles" / "canon-reviewer.md"
FIX = ROOT / "tests" / "fixtures" / "amendment_class"

_SECTION = re.compile(r"(?m)^## (.+)$")
_JSON = re.compile(r"(?ms)^[ \t]*```json[ \t]*\n(.*?)^[ \t]*```")


def _section(text, heading):
    parts = _SECTION.split(text)
    return dict(zip(parts[1::2], parts[2::2])).get(heading, "")


def problems(keeper_text, classes, vote_classes) -> list[str]:
    """What the canon keeper's role file gets wrong about the amendment's class. Empty is clean."""
    found = []
    produce = " ".join(_section(keeper_text, "What you produce").split())
    sentences = re.split(r"(?<=\.)\s+", produce)
    for name in classes:
        if f"`{name}`" not in produce:
            found.append(f"class {name!r} is not named in backticks in What you produce")
        elif name in vote_classes and not any(f"`{name}`" in s and "vote" in s for s in sentences):
            found.append(f"class {name!r} is not said to need a vote")
    examples = _JSON.findall(keeper_text)
    if not examples:
        found.append("no fenced json example")
    for example in examples:
        try:
            doc = json.loads(example)
        except ValueError:
            doc = None
        if not isinstance(doc, dict):
            found.append("the json example is not a JSON object")
        elif "class" not in doc:
            found.append("the json example has no class")
        elif doc["class"] not in classes:
            found.append(f"the json example's class {doc['class']!r} is not one of {', '.join(classes)}")
    return sorted(found)


def check(text):
    return problems(text, lockrules.AMENDMENT_CLASSES, lockrules.VOTE_CLASSES)


class KeeperTest(unittest.TestCase):
    def test_clean(self):
        self.assertEqual([], check(KEEPER.read_text(encoding="utf-8")))

    def test_seeded(self):
        seeded = {
            "keeper_arc_not_named.md": "class 'arc' is not named in backticks in What you produce",
            "keeper_no_json_example.md": "no fenced json example",
            "keeper_example_list.md": "the json example is not a JSON object",
            "keeper_example_no_class.md": "the json example has no class",
            "keeper_example_class_style.md": "the json example's class 'style' is not one of charter, arc, canon",
            "keeper_arc_vote_not_said.md": "class 'arc' is not said to need a vote",
        }
        for name, problem in seeded.items():
            with self.subTest(fixture=name):
                self.assertEqual([problem], check((FIX / name).read_text(encoding="utf-8")))

    def test_drift_a_new_class_fails_until_named(self):
        text = KEEPER.read_text(encoding="utf-8")
        self.assertEqual(["class 'season' is not named in backticks in What you produce"],
                         problems(text, lockrules.AMENDMENT_CLASSES + ("season",), lockrules.VOTE_CLASSES))

    def test_example_passes_the_lock_class_check(self):
        # lockrules has no public function that reads one amendment's class (it is inside the private
        # _amendment), so this reads the written file the way rule 6 does and checks the same tuple.
        example = _JSON.findall(KEEPER.read_text(encoding="utf-8"))[0]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bible" / "amendments" / "A-1.json"
            path.parent.mkdir(parents=True)
            path.write_text(example, encoding="utf-8")
            doc = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsInstance(doc, dict)
        self.assertIn(doc.get("class"), lockrules.AMENDMENT_CLASSES)


class ReviewerTest(unittest.TestCase):
    def test_one_numbered_check_names_class_values_and_a_blocker(self):
        cases = {REVIEWER: True, FIX / "reviewer_no_class_check.md": False}
        needed = ["`class`"] + [f"`{name}`" for name in lockrules.AMENDMENT_CLASSES] + ["Blocker:"]
        for path, expected in cases.items():
            with self.subTest(file=path.name):
                rubric = _section(path.read_text(encoding="utf-8"), "Rubric")
                items = re.split(r"(?m)^\d+\. ", rubric)[1:]
                self.assertIs(expected, any(all(n in item for n in needed) for item in items))


if __name__ == "__main__":
    unittest.main()
