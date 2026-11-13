"""simulation: rules 1 to 4 on a shot's two simulation runs, `state.json` and `rerun.json`."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lint import ROOT, LintCase, lint  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "lint_simulation"
CLEAN = FIX / "clean"
UNIT = "production/ep01/Q1-1"
STATE, RERUN = f"{UNIT}/simulation/state.json", f"{UNIT}/simulation/rerun.json"


def load(rel, base=CLEAN):
    return json.loads((base / rel).read_text(encoding="utf-8"))


def dump(data):
    return json.dumps(data, indent=2) + "\n"


def spray(**keys):
    """The clean scene state with the `spray` cache's keys replaced (a value of None removes the key)."""
    data = load(STATE)
    cache = data["caches"][1]
    for key, value in keys.items():
        if value is None:
            cache.pop(key, None)
        else:
            cache[key] = value
    return data


class Simulation(LintCase):
    CHECKS = ("canon", "media", "provenance", "references", "spoiler", "structure")

    def result(self, root):
        return lint.run(root, only=["simulation"])["simulation"]

    def assertPass(self, root):
        r = self.result(root)
        self.assertEqual(r.status, lint.PASS, f"{r.findings} {r.reason}")

    def assertFindings(self, root, expected):
        """`simulation` FAILs with exactly `expected` (path, line, rule, message)."""
        r = self.result(root)
        self.assertEqual(r.status, lint.FAIL, r.reason)
        self.assertEqual([(f.path, f.line, f.rule, f.message) for f in r.findings], expected)

    def variant(self, files, base=CLEAN, remove=()):
        root = super().variant(files, base)
        for rel in remove:
            (root / rel).unlink()
        return root

    # Behaviour 1
    def test_clean_passes(self):
        self.assertPass(CLEAN)
        self.assertEqual((CLEAN / STATE).read_bytes(), (CLEAN / RERUN).read_bytes())
        results = lint.run(CLEAN, only=["simulation", "kit", "markers", *self.CHECKS])
        self.assertEqual({k: r.status for k, r in results.items()}, dict.fromkeys(results, lint.PASS))

    def test_tree_without_run_files_passes(self):
        self.assertPass(self.variant({}, remove=(STATE, RERUN)))
        self.assertPass(ROOT / "tests" / "fixtures" / "lint_structure" / "clean")

    # Behaviour 2
    def test_rule3_hashes_differ(self):
        self.assertOnly(FIX / "r3_hashes_differ", "simulation", [(STATE, 1, "hashes")])
        self.assertFindings(FIX / "r3_hashes_differ", [
            (STATE, 1, "hashes", f"cache 'spray': sha256 differs from {RERUN}")])

    # Behaviour 3
    def test_rule4_over_speed(self):
        self.assertOnly(FIX / "r4_over_speed", "simulation", [(STATE, 1, "bounds")])
        self.assertFindings(FIX / "r4_over_speed", [
            (STATE, 1, "bounds", "cache 'spray': max_speed_m_s 31.0 is above 30")])

    # Behaviour 4
    def test_rule1_depends(self):
        self.assertFindings(self.variant({}, remove=(RERUN,)), [(STATE, 1, "depends", f"{RERUN} is missing")])
        self.assertFindings(self.variant({}, base=FIX / "r4_over_speed", remove=(RERUN,)), [
            (STATE, 1, "bounds", "cache 'spray': max_speed_m_s 31.0 is above 30"),
            (STATE, 1, "depends", f"{RERUN} is missing")])
        self.assertFindings(self.variant({}, remove=(STATE,)), [(RERUN, 1, "depends", f"{STATE} is missing")])

    def test_rule1_lone_file_is_still_checked_for_state(self):
        root = self.variant({RERUN: dump(dict(load(RERUN), stage="lighting"))}, remove=(STATE,))
        self.assertFindings(root, [(RERUN, 1, "depends", f"{STATE} is missing"),
                                   (RERUN, 1, "state", "stage is not 'simulation'")])

    # Behaviour 5
    def test_rule2_state(self):
        clean = load(RERUN)
        cases = {
            "not an object": ([clean], "not a scene state object"),
            "shot": (dict(clean, shot="Q1-2"), "shot is not 'Q1-1'"),
            "stage": (dict(clean, stage="lighting"), "stage is not 'simulation'"),
            "caches": (dict(clean, caches={}), "caches is not a list"),
            "entry": (dict(clean, caches=[clean["caches"][0], 1]), "cache 2 is not an object"),
            "id": (spray(id=""), "cache 2 id is not a string"),
            "sha256": (spray(sha256=clean["caches"][1]["sha256"].upper()),
                       "cache 2 sha256 is not 64 lowercase hex digits"),
            "max_speed_m_s": (spray(max_speed_m_s=True), "cache 2 max_speed_m_s is not a number"),
            "energy_j": (spray(energy_j={"first": 1}), "cache 2 energy_j is not {first, last} with two numbers"),
            "gravity_m_s2": (spray(gravity_m_s2=None), "cache 2 gravity_m_s2 is not a number or null"),
            "repeated": (dict(clean, caches=clean["caches"] + [clean["caches"][0]]), "cache 3 id 'cape' is repeated"),
        }
        for name, (data, message) in cases.items():
            with self.subTest(name):
                self.assertFindings(self.variant({RERUN: dump(data)}), [(RERUN, 1, "state", message)])

    def test_rule2_entry_gets_first_finding_only(self):
        data = spray(id="", sha256="x", max_speed_m_s="fast", energy_j=None, gravity_m_s2="down")
        self.assertFindings(self.variant({RERUN: dump(data)}), [(RERUN, 1, "state", "cache 2 id is not a string")])

    def test_rule2_other_keys_ignored_and_no_caches_pass(self):
        data = load(STATE)
        data["solver"] = "alpha"
        data["caches"][0]["solver"] = "alpha"
        self.assertPass(self.variant({STATE: dump(data), RERUN: dump(data)}))
        empty = dump(dict(load(STATE), caches=[]))
        self.assertPass(self.variant({STATE: empty, RERUN: empty}))

    # Behaviour 6
    def test_rule2_nan_is_not_a_number(self):
        text = (CLEAN / STATE).read_text(encoding="utf-8").replace('"max_speed_m_s": 12.0', '"max_speed_m_s": NaN')
        self.assertIn("NaN", text)
        self.assertFindings(self.variant({STATE: text}), [(STATE, 1, "state", "cache 2 max_speed_m_s is not a number")])

    # Behaviour 7
    def test_rule3_cache_missing_from_a_run(self):
        clean = load(RERUN)
        root = self.variant({RERUN: dump(dict(clean, caches=clean["caches"][:1]))})
        self.assertFindings(root, [(STATE, 1, "hashes", f"cache 'spray' is not in {RERUN}")])
        drip = dict(clean["caches"][0], id="drip")
        root = self.variant({RERUN: dump(dict(clean, caches=clean["caches"] + [drip]))})
        self.assertFindings(root, [(RERUN, 1, "hashes", f"cache 'drip' is not in {STATE}")])

    def test_rule3_caches_matched_by_id(self):
        clean = load(RERUN)
        self.assertPass(self.variant({RERUN: dump(dict(clean, caches=clean["caches"][::-1]))}))

    # Behaviour 8
    def test_rule4_bounds_edges_pass(self):
        for name, keys in {"speed": {"max_speed_m_s": 30}, "energy": {"energy_j": {"first": 10, "last": 11}},
                           "gravity low": {"gravity_m_s2": 8.829}, "gravity high": {"gravity_m_s2": 10.791}}.items():
            with self.subTest(name):
                data = dump(spray(**keys))
                self.assertPass(self.variant({STATE: data, RERUN: data}))

    def test_rule4_bounds_defects(self):
        cases = {
            "speed": ({"max_speed_m_s": 30.01}, "cache 'spray': max_speed_m_s 30.01 is above 30"),
            "energy": ({"energy_j": {"first": 10, "last": 11.01}},
                       "cache 'spray': energy_j.last 11.01 is above 1.1 times first 10"),
            "gravity low": ({"gravity_m_s2": 8.8}, "cache 'spray': gravity_m_s2 8.8 is not within 10% of 9.81"),
            "gravity high": ({"gravity_m_s2": 10.8}, "cache 'spray': gravity_m_s2 10.8 is not within 10% of 9.81"),
        }
        for name, (keys, message) in cases.items():
            with self.subTest(name):
                data = dump(spray(**keys))
                self.assertFindings(self.variant({STATE: data, RERUN: data}), [(STATE, 1, "bounds", message)])

    def test_rule4_negative_energy_is_the_brief_words(self):
        data = dump(spray(energy_j={"first": -10, "last": -10}))
        self.assertFindings(self.variant({STATE: data, RERUN: data}), [
            (STATE, 1, "bounds", "cache 'spray': energy_j.last -10 is above 1.1 times first -10")])

    # Behaviour 9
    def test_rule4_reads_state_only(self):
        self.assertPass(self.variant({RERUN: dump(spray(max_speed_m_s=31.0))}))
        rerun = dump(dict(load(RERUN, FIX / "r4_over_speed"), stage="lighting"))
        self.assertFindings(self.variant({RERUN: rerun}, base=FIX / "r4_over_speed"), [
            (RERUN, 1, "state", "stage is not 'simulation'"),
            (STATE, 1, "bounds", "cache 'spray': max_speed_m_s 31.0 is above 30")])

    def test_rule4_state_finding_in_state_hides_bounds(self):
        data = dict(load(STATE, FIX / "r4_over_speed"), shot="Q1-2")
        self.assertFindings(self.variant({STATE: dump(data)}, base=FIX / "r4_over_speed"), [
            (STATE, 1, "state", "shot is not 'Q1-1'")])

    # Behaviour 10
    def test_asset_run_files_are_not_read(self):
        root = self.variant({"production/assets/rope/simulation/state.json": "{",
                             "production/assets/rope/simulation/rerun.json": "{"})
        self.assertPass(root)

    def test_unparsable_run_file_is_error(self):
        r = self.result(self.variant({RERUN: "{"}))
        self.assertEqual(r.status, lint.ERROR)
        self.assertTrue(r.reason.startswith(f"cannot parse {RERUN}:"), r.reason)

    def test_only_files_git_would_commit_are_read(self):
        root = self.variant({RERUN: "{"})
        self.git_init(root, ignore="rerun.json\n")
        self.assertFindings(root, [(STATE, 1, "depends", f"{RERUN} is missing")])

    # Behaviour 11
    def test_units_in_sorted_order_and_output_deterministic(self):
        r3 = FIX / "r3_hashes_differ"
        ep02 = {rel.replace("ep01", "ep02"): (r3 / rel).read_text(encoding="utf-8") for rel in (STATE, RERUN)}
        root = self.variant(ep02, base=r3)
        expected = [(f"production/{ep}/Q1-1/simulation/state.json", 1, "hashes",
                     f"cache 'spray': sha256 differs from production/{ep}/Q1-1/simulation/rerun.json")
                    for ep in ("ep01", "ep02")]
        self.assertFindings(root, expected)
        first = lint.text(lint.run(root, only=["simulation"]))
        self.assertEqual(first, lint.text(lint.run(root, only=["simulation"])))


if __name__ == "__main__":
    unittest.main()
