"""The rights rule in the role files (brief sections 4 and 16, "Rights")."""

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import frontmatter  # noqa: E402
from kit.lib.frontmatter import as_list  # noqa: E402

ROLES = ROOT / "kit" / "roles"
NOT_OURS = "`research/not-ours.tsv`"
# (role id, section, phrase): the section holds the phrase once its whitespace is normalised.
PINS = [
    ("canon-keeper", "What you produce", "gets its origin"),
    ("canon-keeper", "What you produce",
     "Write `house` only in an amendment whose `why` says what it was designed from"),
    ("canon-reviewer", "Read first", NOT_OURS),
    ("look-reviewer", "Read first", NOT_OURS),
    ("art-director", "You may not", "a living artist"),
    ("story-writer", "What you produce", "has a sheet under `bible/canon/`"),
    ("story-artist", "What you produce", "only subjects that have a canon sheet"),
    ("researcher", "What you produce", NOT_OURS),
    ("researcher", "What you produce", "`name`, `holder` and `link`"),
]
# Each of these roles has one numbered Rubric criterion that holds every phrase.
CRITERION = (NOT_OURS, "third party's", "Blocker:")
CRITERION_ROLES = ("canon-reviewer", "look-reviewer")


def invented():
    """The invented third party of the eval cases, read from their `research/not-ours.tsv`: every
    `name`, and every two adjacent words of a `holder`. None may appear in a role file."""
    found = set()
    for path in sorted((ROOT / "kit" / "evals").glob("*/*/tree/research/not-ours.tsv")):
        for row in path.read_text(encoding="utf-8").splitlines()[1:]:
            name, holder, _ = row.split("\t")
            words = holder.split()
            found |= {name} | {" ".join(words[i:i + 2]) for i in range(len(words) - 1)}
    return sorted(found)


INVENTED = invented()


def normal(text):
    return " ".join(text.split())


def sections(body):
    parts = re.split(r"(?m)^## (.+)$", body)
    return dict(zip(parts[1::2], parts[2::2]))


def problems(roles_dir):
    """What the rights rule finds missing in the role files under roles_dir. Empty is clean."""
    found = []
    parsed = {}
    for role in sorted({r for r, _, _ in PINS} | set(CRITERION_ROLES)):
        parsed[role] = frontmatter.parse((roles_dir / f"{role}.md").read_text(encoding="utf-8"))
    for role, section, phrase in PINS:
        if phrase not in normal(sections(parsed[role][1]).get(section, "")):
            found.append(f"{role}: {section} lacks {phrase!r}")
    for role in CRITERION_ROLES:
        rubric = sections(parsed[role][1]).get("Rubric", "")
        criteria = [normal(c) for c in re.split(r"(?m)^\d+\. ", rubric)[1:]]
        if not any(all(p in c for p in CRITERION) for c in criteria):
            found.append(f"{role}: no Rubric criterion holds {CRITERION}")
    if "research/**" not in as_list(parsed["look-reviewer"][0].get("reads")):
        found.append("look-reviewer: reads lacks research/**")
    for path in sorted(roles_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        found += [f"{path.name} names {name!r}" for name in INVENTED if name in text]
    return found


def without(text, phrase):
    """text with every occurrence of phrase removed, across any line break between its words."""
    pattern = r"\s+".join(re.escape(word) for word in phrase.split())
    return re.sub(pattern, "", text)


class RightsTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)

    def copy(self, role, change):
        """A copy of the role files with change applied to one of them."""
        roles = Path(tempfile.mkdtemp(dir=self.dir))
        shutil.copytree(ROLES, roles, dirs_exist_ok=True)
        path = roles / f"{role}.md"
        text = path.read_text(encoding="utf-8")
        mutated = change(text)
        self.assertNotEqual(mutated, text)
        path.write_text(mutated, encoding="utf-8")
        return roles

    def test_shipped_role_files_say_the_rule(self):
        self.assertEqual(problems(ROLES), [])

    def test_shipped_role_files_stay_at_or_under_120_lines(self):
        for role in sorted({r for r, _, _ in PINS}):
            with self.subTest(role=role):
                lines = (ROLES / f"{role}.md").read_text(encoding="utf-8").splitlines()
                self.assertLessEqual(len(lines), 120)

    def test_removing_any_pinned_phrase_fails(self):
        for role, section, phrase in PINS:
            with self.subTest(role=role, phrase=phrase):
                roles = self.copy(role, lambda text: without(text, phrase))
                self.assertIn(f"{role}: {section} lacks {phrase!r}", problems(roles))

    def test_removing_a_phrase_from_the_criterion_fails(self):
        def strip(phrase):
            def change(text):
                head, _, rest = text.partition("\n## Rubric")
                rubric, sep, tail = rest.partition("\n## ")
                criteria = re.split(r"(?m)^(?=\d+\. )", rubric)
                criteria = [without(c, phrase) if NOT_OURS in normal(c) else c for c in criteria]
                return head + "\n## Rubric" + "".join(criteria) + sep + tail
            return change
        for role in CRITERION_ROLES:
            for phrase in CRITERION:
                with self.subTest(role=role, phrase=phrase):
                    roles = self.copy(role, strip(phrase))
                    self.assertIn(f"{role}: no Rubric criterion holds {CRITERION}", problems(roles))

    def test_reads_without_research_fails(self):
        roles = self.copy("look-reviewer", lambda text: text.replace(" research/**,", "", 1))
        self.assertEqual(problems(roles), ["look-reviewer: reads lacks research/**"])

    def test_the_invented_names_come_from_the_cases(self):
        self.assertEqual(len(INVENTED), 5)

    def test_an_invented_name_in_a_role_file_fails(self):
        for name in INVENTED:
            with self.subTest(name=name):
                roles = self.copy("story-writer", lambda text: text + f"\nLike {name}.\n")
                self.assertEqual(problems(roles), [f"story-writer.md names {name!r}"])

if __name__ == "__main__":
    unittest.main()
