"""The trend-scout's role file names the snapshot's files by path, and every case tree holds them (ISSUE-442).

The role has no shell, and a file search can miss a directory whose name starts with a dot: the role file
names `.snapshot/<platform>.ranking.txt` and `.snapshot/<platform>.read.json` and says to open them by path.
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

from kit.lib import evals, trends  # noqa: E402

KIT = ROOT / "kit"
ROLE_FILE = KIT / "roles" / "trend-scout.md"
FIX = ROOT / "tests" / "fixtures" / "trend_scout_paths"
CASE_01 = KIT / "evals" / "trend-scout" / "01-quay-lanterns" / "tree"
START = "When the task names a watched topic"
PARTS = (
    "`.snapshot/<platform>.ranking.txt`",
    "`.snapshot/<platform>.read.json`",
    "by path with Read",
    "only when Read of those paths fails",
)


def _paragraph(text):
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(START):
            block = []
            for item in lines[i:]:
                if not item.strip():
                    break
                block.append(item)
            return re.sub(r"\s+", " ", " ".join(block))
    return ""


def problems(text):
    """One `snapshot: ` string per part the snapshot paragraph of a role file lacks; [] when it holds all."""
    paragraph = _paragraph(text)
    return [f"snapshot: missing {part}" for part in PARTS if part not in paragraph]


def missing(tree):
    """The snapshot files a case tree's topic files name and the tree lacks, in topic and platform order."""
    tree = Path(tree)
    topics = sorted((tree / "research" / "topics").glob("*.json"))
    if not topics:
        return ["research/topics: no topic file"]
    found = []
    for path in topics:
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in sorted(k for k in data if k != "topic"):
            if key not in trends.PLATFORMS:
                found.append(f"research/topics/{path.name}: unknown platform {key}")
                continue
            for name in (f".snapshot/{key}.ranking.txt", f".snapshot/{key}.read.json"):
                if not (tree / name).is_file():
                    found.append(name)
    return found


class SnapshotPathsTest(unittest.TestCase):
    def test_the_role_file_is_clean(self):
        self.assertEqual(problems(ROLE_FILE.read_text(encoding="utf-8")), [])

    def test_the_old_wording_is_found(self):
        found = problems((FIX / "old-wording.md").read_text(encoding="utf-8"))
        self.assertEqual(len(found), 4, found)
        self.assertTrue(all(item.startswith("snapshot: ") for item in found), found)

    def test_every_case_tree_holds_its_snapshot(self):
        cases = evals.load(KIT, "trend-scout")
        self.assertEqual(len(cases), 3)
        for case in cases:
            with self.subTest(case=case.id):
                self.assertEqual(missing(case.path / "tree"), [])

    def test_a_tree_without_its_snapshot_is_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fresh():
                tree = Path(tmp) / "tree"
                if tree.exists():
                    shutil.rmtree(tree)
                shutil.copytree(CASE_01, tree)
                return tree

            tree = fresh()
            (tree / ".snapshot" / "youtube.ranking.txt").unlink()
            self.assertEqual(missing(tree), [".snapshot/youtube.ranking.txt"])

            tree = fresh()
            shutil.rmtree(tree / ".snapshot")
            self.assertEqual(missing(tree), [".snapshot/youtube.ranking.txt", ".snapshot/youtube.read.json"])

            tree = fresh()
            topic = next((tree / "research" / "topics").glob("*.json"))
            data = json.loads(topic.read_text(encoding="utf-8"))
            data["tiktok"] = {}
            topic.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(missing(tree), [".snapshot/tiktok.ranking.txt", ".snapshot/tiktok.read.json"])

            tree = fresh()
            for item in (tree / "research" / "topics").iterdir():
                item.unlink()
            self.assertEqual(missing(tree), ["research/topics: no topic file"])


if __name__ == "__main__":
    unittest.main()
