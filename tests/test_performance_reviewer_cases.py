"""The performance reviewer's eval suite (kit/evals/performance-reviewer/): one baseline shot, one seed a case.

Nothing here runs a model or reaches the network.
"""

import ast
import json
import shutil
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from kit.lib import evals, lint, production

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "kit"
ROLE = "performance-reviewer"
SUITE = KIT / "evals" / ROLE
PIPELINE = json.loads((KIT / "pipeline.json").read_text(encoding="utf-8"))
SHOT = "episodes/ep01/sequences/Q1/shots/Q1-2.json"
STATE = "production/ep01/Q1-2/animation/state.json"
SCENE = "production/ep01/Q1-2/animation/scene.py"
MANIFEST = "production/ep01/Q1-2/animation/manifest.json"
IGNORED = {f"{SHOT} set", f"{MANIFEST} job"}
ITEM_KEYS = ("id", "line", "actor")
SEEDS = {
    "01-boathouse": [f"{STATE} actors.lantern", f"{STATE} actors.wen", f"{STATE} framing.wen", f"{SCENE} KEYS"],
    "02-ferry-steps": [f"{STATE} lipsync.L1", f"{STATE} lipsync.L2"],
    "03-salt-store": [f"{STATE} lipsync.L2"],
    "04-net-loft": [f"{STATE} actors.dax", f"{SCENE} KEYS"],
    "05-tide-mill": [f"{STATE} motion.wen"],
    "06-reed-bank": [f"{STATE} actors.lantern"],
    "07-gull-rock": [f"{STATE} lipsync.L1"],
    "08-lamp-house": ["bible/canon/characters/tobi.md", f"{SHOT} characters", f"{STATE} actors.tobi", f"{SCENE} KEYS"],
    "09-cod-wharf": [f"{STATE} lipsync.L3"],
}
CLEAN = ("02-ferry-steps", "05-tide-mill", "08-lamp-house")
NAMES = {"01-boathouse": "Wen", "04-net-loft": "Dax", "06-reed-bank": "Lantern"}
MIN_FRAMES = 96
MIN_STEP_M = 0.5
MAX_HANDOVER_M = 0.25


def trees():
    return {p.name: p / "tree" for p in sorted(SUITE.iterdir()) if (p / "tree").is_dir()}


def keyed(value):
    """A dict as is; a list of objects as a dict keyed by each item's id, else line, else actor; else None."""
    if isinstance(value, dict):
        return value
    if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
        for key in ITEM_KEYS:
            if all(key in v for v in value):
                grouped = {}
                for item in value:
                    grouped.setdefault(item[key], []).append(item)
                return grouped
    return None


def scene_parts(text):
    """`KEYS`, `LINES` (as literals) and the rest of the source of a scene.py."""
    lines = text.splitlines(keepends=True)
    parts, drop = {}, set()
    for node in ast.parse(text).body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id in ("KEYS", "LINES")):
            parts[node.targets[0].id] = repr(ast.literal_eval(node.value))
            drop.update(range(node.lineno - 1, node.end_lineno))
    parts["code"] = "".join(line for i, line in enumerate(lines) if i not in drop)
    return parts


def labels(tree):
    """Every label of a tree and its value, as a comparable string."""
    found = {}
    for path in sorted(p for p in tree.rglob("*") if p.is_file()):
        rel = path.relative_to(tree).as_posix()
        text = path.read_text(encoding="utf-8")
        if rel.endswith(".py"):
            found.update({f"{rel} {k}": v for k, v in scene_parts(text).items()})
            continue
        top = keyed(json.loads(text)) if rel.endswith(".json") else None
        if top is None:
            found[rel] = text
            continue
        for k1, v1 in top.items():
            sub = keyed(v1)
            if sub is None:
                found[f"{rel} {k1}"] = json.dumps(v1)
            else:
                found.update({f"{rel} {k1}.{k2}": json.dumps(v2) for k2, v2 in sub.items()})
    return {k: v for k, v in found.items() if k not in IGNORED}


def differences(found):
    """Per case, the sorted labels whose value is not the one more than half of the trees hold."""
    every = sorted(set().union(*found.values()))
    out = {case: [] for case in found}
    for label in every:
        counts = Counter(values.get(label) for values in found.values())
        value, count = counts.most_common(1)[0]
        if count * 2 <= len(found):
            raise AssertionError(f"no baseline for {label}")
        for case, values in found.items():
            if values.get(label) != value:
                out[case].append(label)
    return out


def shot_messages(tree):
    """What keeps the shot of a tree from being played as the shot file says, in a fixed order."""
    shot = json.loads((tree / SHOT).read_text(encoding="utf-8"))
    state = json.loads((tree / STATE).read_text(encoding="utf-8"))
    first, last = state["frames"]["first"], state["frames"]["last"]
    actors = {a["id"]: a for a in state["actors"]}
    messages = []
    if last - first + 1 != shot["duration"] * state["fps"]:
        messages.append("frames do not hold the shot's duration")
    elif last - first + 1 < MIN_FRAMES:
        messages.append("the shot is shorter than 4 seconds")
    wen = actors["wen"]
    if abs(wen["end"]["location"][0] - wen["start"]["location"][0]) < MIN_STEP_M:
        messages.append("wen does not step")
    lantern = actors.get("lantern")
    if (lantern is None
            or abs(lantern["end"]["location"][0] - actors["dax"]["end"]["location"][0]) > MAX_HANDOVER_M):
        messages.append("the lantern does not end at dax")
    starts = [line[2] for line in ast.literal_eval(scene_parts((tree / SCENE).read_text(encoding="utf-8"))
                                                   .get("LINES", "[]"))]
    if not starts or len(set(starts)) != len(starts) or any(not first <= s <= last for s in starts):
        messages.append("the lines do not start on distinct frames of the shot")
    return messages


def stage_lints():
    return sorted({check for entry in PIPELINE if ROLE in production.stage_roles(entry, "checker")
                   for check in entry["lint"]})


def notes(text):
    return {"marker": "NOTES", "summary": "Notes.",
            "notes": [{"id": "B1", "severity": "Blocker", "text": text, "evidence": STATE}]}


def edit_state(tree, change):
    path = tree / STATE
    state = json.loads(path.read_text(encoding="utf-8"))
    change(state)
    path.write_text(json.dumps(state), encoding="utf-8")


def end_at(actor, location):
    def change(state):
        next(a for a in state["actors"] if a["id"] == actor)["end"]["location"] = location
    return change


class PerformanceReviewerCasesTest(unittest.TestCase):
    def test_each_case_differs_from_the_baseline_only_in_its_seed(self):
        found = differences({case: labels(tree) for case, tree in trees().items()})
        self.assertEqual(sorted(found), sorted(SEEDS))
        for case, seed in SEEDS.items():
            self.assertEqual(found[case], sorted(seed), case)

    def test_trees_pass_the_stage_lints(self):
        only = stage_lints()
        self.assertEqual(only, ["animation"])
        for case, tree in trees().items():
            results = lint.run(tree, only=only)
            self.assertEqual(lint.overall(results), lint.PASS, f"{case}: {lint.text(results)}")
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "tree"
            shutil.copytree(trees()["02-ferry-steps"], tree)
            edit_state(tree, lambda state: state["lipsync"][0].update(offset_frames=48))
            results = lint.run(tree, only=only)
            self.assertEqual(lint.overall(results), lint.FAIL)
            self.assertIn("offset_frames 48 (from -1 to 1)", lint.text(results))

    def test_clean_cases_play_the_shot(self):
        for case in CLEAN:
            self.assertEqual(shot_messages(trees()[case]), [], case)

    def test_a_seeded_shot_is_refused(self):
        defects = (
            (end_at("lantern", [0.8, 0, 1]), "the lantern does not end at dax"),
            (end_at("wen", [0, 0, 0]), "wen does not step"),
            (lambda state: state.update(frames={"first": 12, "last": 23}), "frames do not hold the shot's duration"),
        )
        for change, message in defects:
            with tempfile.TemporaryDirectory() as tmp:
                tree = Path(tmp) / "tree"
                shutil.copytree(trees()["02-ferry-steps"], tree)
                edit_state(tree, change)
                self.assertEqual(shot_messages(tree)[:1], [message])

    def test_catches_match_the_name_as_written(self):
        cases = {c.id: c for c in evals.load(KIT, ROLE)}
        for case_id, name in NAMES.items():
            case = cases[case_id]
            for written in (name, name.lower()):
                self.assertTrue(evals.grade(case, notes(f"{written} is the defect."))["pass"], f"{case_id}: {written}")
            self.assertFalse(evals.grade(case, notes("Z99 is the defect."))["pass"], case_id)


if __name__ == "__main__":
    unittest.main()
