"""The eval trees agree with the kit they are graded against (ISSUE-254).

Every `tree/`, `old/` and `new/` of a case under ``kit/evals/<role>/<case>/`` holds amendments as
``bible/amendments/A-<n>.json`` with a ``class`` (brief section 7, ``lockrules.AMENDMENT_CLASSES``), its
sheets' ``origin: house A-<n>`` names an amendment of the tree, its ``task.md`` names files of the tree,
and an ``episodes/ep01`` is episode one. The serial format says what episode one picks up, and the three
roles whose Blocked asked for a proof of lock say that the merge is the lock.

Seeded tests copy one case, or one role file, into a temporary directory and plant one defect there.
"""

import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import lockrules  # noqa: E402

EVALS = ROOT / "kit" / "evals"
ROLES = ROOT / "kit" / "roles"
FORMAT = ROOT / "kit" / "formats" / "serial-microdrama.md"

LOCK_ROLES = ("editorial.md", "head-of-story.md", "story-artist.md")
TREE_NAMES = ("new", "old", "tree")

FIRST_EPISODE = ("In a serial's first episode there is no cliffhanger yet, so the pickup states "
                 "the situation the serial opens on.")
MERGE_IS_THE_LOCK = ("A file of an earlier stage in your worktree is locked: it reached the base "
                     "branch by a merged pull request, and that merge is the lock. No file records a "
                     "lock; do not ask for one.")

_AMENDMENT = re.compile(r"A-[0-9]+\.json")
_HOUSE = re.compile(r"(?m)^origin: house (A-[0-9]+)[ \t]*$")
_TASK_PATH = re.compile(r"bible/amendments/\S+")
_LATER = re.compile(r"(?i)\b(last time|previously|episode (two|three|four|five|six|seven|eight|nine|ten))\b")
_HEADING = re.compile(r"(?m)^## ")


def _joined(text):
    return " ".join(text.split())


def _trees(evals):
    for case in sorted(p for p in evals.glob("*/*") if p.is_dir()):
        for name in TREE_NAMES:
            tree = case / name
            if tree.is_dir():
                yield case, tree, f"{case.parent.name}/{case.name}/{name}"


def tree_messages(evals):
    """Amendment names and keys, house lines and task paths of every tree under `evals`."""
    out = []
    for case, tree, where in _trees(evals):
        amendments = tree / "bible" / "amendments"
        for path in sorted(p for p in amendments.rglob("*") if p.is_file()) if amendments.is_dir() else []:
            name = path.relative_to(amendments).as_posix()
            if not _AMENDMENT.fullmatch(name):
                out.append(f"{where}: bible/amendments/{name} is not A-<n>.json")
                continue
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                doc = None
            if (not isinstance(doc, dict) or sorted(doc) != ["changes", "class", "evidence", "why"]
                    or doc["class"] not in lockrules.AMENDMENT_CLASSES):
                out.append(f"{where}: bible/amendments/{name} lacks a class or a key")
        task = case / "task.md"
        if tree.name == "tree" and task.is_file():
            for path in _TASK_PATH.findall(task.read_text(encoding="utf-8")):
                path = path.rstrip(".")
                if not (tree / path).exists():
                    out.append(f"{where}: task.md names {path}, which is not there")
    return out


def house_messages(evals):
    """Every `origin: house A-<n>` of a sheet whose tree holds no `A-<n>.json`, golden trees included."""
    out = []
    for case in sorted(p.parent for p in evals.rglob("case.json")):
        for name in TREE_NAMES:
            tree = case / name
            if not tree.is_dir():
                continue
            where = tree.relative_to(evals).as_posix()
            for sheet in sorted((tree / "bible" / "canon").glob("*/*.md")):
                for n in _HOUSE.findall(sheet.read_text(encoding="utf-8")):
                    if not (tree / "bible" / "amendments" / f"{n}.json").is_file():
                        out.append(f"{where}: {sheet.relative_to(tree).as_posix()} names {n}, which is not there")
    return out


def episode_messages(evals):
    """Every `episodes/ep01/*.md` that calls itself a later episode."""
    out = []
    for _case, tree, where in _trees(evals):
        for path in sorted((tree / "episodes" / "ep01").glob("*.md")):
            match = _LATER.search(path.read_text(encoding="utf-8"))
            if match:
                out.append(f"{where}: {path.relative_to(tree).as_posix()}: {match.group(0)}")
    return out


def blocked_section(text):
    start = re.search(r"(?m)^## Blocked[ \t]*$", text)
    if not start:
        return ""
    rest = text[start.end():]
    end = _HEADING.search(rest)
    return rest[:end.start()] if end else rest


def role_messages(roles):
    """The Blocked section of each of LOCK_ROLES under `roles`, and of no other file."""
    out = []
    for name in LOCK_ROLES:
        section = _joined(blocked_section((roles / name).read_text(encoding="utf-8")))
        if MERGE_IS_THE_LOCK not in section:
            out.append(f"{name}: the Blocked section does not say that the merge is the lock")
        if "not locked" in section:
            out.append(f"{name}: the Blocked section holds not locked")
    return out


def _copy_case(tmp, role, case):
    evals = Path(tmp) / "evals"
    shutil.copytree(EVALS / role / case, evals / role / case)
    return evals


class TreesAgreeTest(unittest.TestCase):

    def test_clean_amendments_classes_house_lines_and_tasks(self):
        self.assertEqual(tree_messages(EVALS), [])

    def test_seeded_amendment_not_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            evals = _copy_case(tmp, "canon-reviewer", "01-lamplighters")
            amendments = evals / "canon-reviewer" / "01-lamplighters" / "tree" / "bible" / "amendments"
            (amendments / "A-2.json").rename(amendments / "A2-mara-childhood.md")
            self.assertEqual(tree_messages(evals), [
                "canon-reviewer/01-lamplighters/tree: bible/amendments/A2-mara-childhood.md is not A-<n>.json",
                "canon-reviewer/01-lamplighters/tree: task.md names bible/amendments/A-2.json, which is not there",
            ])

    def test_seeded_amendment_without_class(self):
        with tempfile.TemporaryDirectory() as tmp:
            evals = _copy_case(tmp, "canon-reviewer", "02-orchard-house")
            path = evals / "canon-reviewer" / "02-orchard-house" / "tree" / "bible" / "amendments" / "A-2.json"
            doc = json.loads(path.read_text(encoding="utf-8"))
            del doc["class"]
            path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
            self.assertEqual(tree_messages(evals), [
                "canon-reviewer/02-orchard-house/tree: bible/amendments/A-2.json lacks a class or a key",
            ])

    def test_seeded_house_line_names_an_absent_amendment(self):
        with tempfile.TemporaryDirectory() as tmp:
            evals = _copy_case(tmp, "canon-reviewer", "05-salt-marsh")
            sheet = evals / "canon-reviewer" / "05-salt-marsh" / "tree" / "bible" / "canon" / "characters" / "marsh-light.md"
            text = sheet.read_text(encoding="utf-8")
            self.assertIn("origin: house A-2\n", text)
            sheet.write_text(text.replace("origin: house A-2\n", "origin: house A-3\n"), encoding="utf-8")
            self.assertEqual(tree_messages(evals), [])
            self.assertEqual(house_messages(evals), [
                "canon-reviewer/05-salt-marsh/tree: bible/canon/characters/marsh-light.md names A-3, which is not there",
            ])

    def test_clean_every_house_line_names_an_amendment_of_its_tree(self):
        self.assertEqual(house_messages(EVALS), [])

    def test_seeded_tree_without_its_amendment(self):
        with tempfile.TemporaryDirectory() as tmp:
            evals = _copy_case(tmp, "story-writer", "02-fox-lane")
            (evals / "story-writer" / "02-fox-lane" / "tree" / "bible" / "amendments" / "A-1.json").unlink()
            self.assertEqual(house_messages(evals), [
                f"story-writer/02-fox-lane/tree: bible/canon/{rel} names A-1, which is not there"
                for rel in ("characters/fox.md", "characters/hilde.md", "characters/tam.md", "places/fox-lane.md")
            ])

    def test_seeded_golden_tree_without_its_amendment(self):
        with tempfile.TemporaryDirectory() as tmp:
            evals = _copy_case(tmp, "golden/corpus/assets", "01-clean")
            shutil.rmtree(evals / "golden" / "corpus" / "assets" / "01-clean" / "tree" / "bible" / "amendments")
            self.assertEqual(house_messages(evals), [
                "golden/corpus/assets/01-clean/tree: bible/canon/characters/ama.md names A-1, which is not there",
                "golden/corpus/assets/01-clean/tree: bible/canon/places/harbour.md names A-1, which is not there",
            ])

    def test_clean_every_ep01_is_episode_one(self):
        self.assertEqual(episode_messages(EVALS), [])

    def test_seeded_ep01_picks_up_a_last_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            evals = _copy_case(tmp, "story-editor", "06-dune-radio")
            beats = evals / "story-editor" / "06-dune-radio" / "tree" / "episodes" / "ep01" / "beats.md"
            text = beats.read_text(encoding="utf-8")
            self.assertIn("Three nights ago", text)
            beats.write_text(text.replace("Three nights ago", "Last time:"), encoding="utf-8")
            self.assertEqual(episode_messages(evals), [
                "story-editor/06-dune-radio/tree: episodes/ep01/beats.md: Last time",
            ])

    def test_clean_format_says_what_episode_one_picks_up(self):
        self.assertIn(FIRST_EPISODE, _joined(FORMAT.read_text(encoding="utf-8")))

    def test_clean_roles_do_not_ask_for_a_proof_of_lock(self):
        self.assertEqual(role_messages(ROLES), [])

    def test_seeded_role_asks_for_a_proof_of_lock(self):
        main_text = ("Return `BLOCKED` only when you cannot do the work from the files: a board of the episode is not\n"
                     "locked, or the cut cannot fit the runtime without dropping a beat. Fill\n"
                     "`blocked` with:")
        with tempfile.TemporaryDirectory() as tmp:
            roles = Path(tmp) / "roles"
            roles.mkdir()
            for name in LOCK_ROLES:
                shutil.copy(ROLES / name, roles / name)
            path = roles / "editorial.md"
            text = path.read_text(encoding="utf-8")
            seeded, n = re.subn(r"(?ms)^Return `BLOCKED`.*?`blocked` with:", lambda _m: main_text, text, count=1)
            self.assertEqual(n, 1)
            path.write_text(seeded, encoding="utf-8")
            self.assertIn("not locked", _joined(blocked_section(seeded)))
            self.assertEqual(role_messages(roles), [
                "editorial.md: the Blocked section does not say that the merge is the lock",
                "editorial.md: the Blocked section holds not locked",
            ])


if __name__ == "__main__":
    unittest.main()
