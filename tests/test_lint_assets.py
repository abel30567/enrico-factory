"""assets: rules 1 to 8."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lint import LintCase, lint  # noqa: E402

from kit.lib.lints import assets as assets_lint  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "lint_assets"
STATE = "production/assets/ama/assets/state.json"
LEDGER = "bible/canon/ledger.tsv"
HEADER = "subject\tattribute\tvalue\ttag\tsource\n"
AMA_ROW = "ama\theight\t1.62 m\tinvented\t\n"
SCENE = "production/assets/ama/assets/scene.py"
SCRIPT = "import unreal\n"
ALONE = [(SCENE, 1, "depends", f"production/assets/ama/assets/{name}.json is missing and no request is under "
                               "production/assets/ama/assets/requests/") for name in ("manifest", "state")]
CLEAN = json.loads((FIX / "clean" / STATE).read_text(encoding="utf-8"))
MEDIA = "media/manifest.tsv"
AMA_SHEET = "bible/style/characters/ama.md"
HARBOUR_SHEET = "bible/style/places/harbour.md"


def request(asset, *refs, **changes):
    """`production/assets/<asset>/assets/requests/model3d.json` and a request of ``refs`` for it."""
    body = dict({"for": asset, "prompt": f"{asset}, standing, as drawn", "refs": list(refs)}, **changes)
    return f"production/assets/{asset}/assets/requests/model3d.json", json.dumps(body, indent=1) + "\n"


def sheet(title, *ids):
    """A model sheet citing ``ids`` as links to `media:<id>`."""
    return f"# {title}\n\n" + "".join(f"- [{i}](media:{i})\n" for i in ids)


def manifest(*ids):
    return "id\tsha256\tbytes\tsource\n" + "".join(f"{i}\t{'0' * 64}\t10\tupload\n" for i in ids)


SUBJECTS = {AMA_SHEET: sheet("Ama", "ama-ref"), HARBOUR_SHEET: sheet("The harbour", "harbour-ref", "harbour-pier"),
            MEDIA: manifest("ama-ref", "harbour-ref", "harbour-pier", "lantern-ref", "harbour-sketch")}


def state(**changes):
    """The clean state with `changes` set over it, a key set to `...` removed."""
    data = dict(CLEAN, **changes)
    return json.dumps({k: v for k, v in data.items() if v is not ...}, indent=2) + "\n"


def bones(*pairs):
    return {"bones": [{"name": n, "parent": p} for n, p in pairs], "weights_normalised": True}


class Assets(LintCase):
    maxDiff = None
    def assets(self, root):
        return lint.run(root, only=["assets"])["assets"]

    def assertGives(self, root, messages, path=STATE):
        """`assets` FAILs with exactly `messages`, each on line 1 of `path`."""
        result = self.assets(root)
        self.assertEqual(result.status, lint.FAIL, result.reason)
        self.assertEqual([(f.path, f.line, f.message) for f in result.findings],
                         [(path, 1, m) for m in messages])
        return result

    def assertPasses(self, root):
        result = self.assets(root)
        self.assertEqual(result.status, lint.PASS, f"{result.findings} {result.reason}")

    def test_1_clean_passes(self):
        self.assertPasses(FIX / "clean")
        self.assertEqual(lint.text(lint.run(FIX / "clean", only=["assets"])), "assets PASS\n")

    def test_2_on_disk_defects(self):
        cases = {
            "r2_wrong_id": ("naming", "asset id tobi is not the directory name ama"),
            "r4_over_budget": ("budgets", "100001 triangles, over the character budget of 100000"),
            "r5_height_off": ("scale", "height_m 1.8 is not within 10% of 1.62 m (bible/canon/ledger.tsv:2)"),
            "r6_two_roots": ("skeleton", "skeleton has 2 roots, not one"),
        }
        for name, (rule, message) in cases.items():
            with self.subTest(name):
                result = self.assertGives(FIX / name, [message])
                self.assertEqual(result.findings[0].rule, rule)
                self.assertEqual(lint.blockers({"assets": result}),
                                 [f"B1 assets {STATE}:1 {message}"])

    def test_3_shape(self):
        textures = [{"name": "ama_body", "width": 4096}]
        cases = [
            ("[]\n", "not {asset, kind, sha256, triangles, materials, textures, height_m, skeleton}"),
            (state(materials=...), "materials is missing"),
            (state(kind="creature"), "kind is not 'character', 'set' or 'prop'"),
            (state(sha256="ABC"), "sha256 is not 64 lower-case hex characters"),
            (state(triangles=True), "triangles is not a whole number"),
            (state(triangles=10.5), "triangles is not a whole number"),
            (state(textures=textures), "textures is not a list of {name, width, height}"),
            (state(height_m="1.7"), "height_m is not a positive number"),
            (state(skeleton={"bones": []}), "skeleton is not null or {bones, weights_normalised}"),
            (state(kind="creature", triangles=200000), "kind is not 'character', 'set' or 'prop'"),
        ]
        for body, message in cases:
            with self.subTest(message=message, body=body[:60]):
                result = self.assertGives(self.variant({STATE: body}, base=FIX / "clean"), [message])
                self.assertEqual(result.findings[0].rule, "shape")

    def test_4_naming(self):
        root = self.variant({STATE: state(asset="Ama_2")}, base=FIX / "clean")
        result = self.assertGives(root, ["asset id Ama_2 does not match [a-z0-9][a-z0-9-]*"])
        self.assertEqual(result.findings[0].rule, "naming")

    def test_5_references(self):
        root = self.variant({}, base=FIX / "clean")
        (root / "bible/canon/characters/ama.md").unlink()
        self.assertGives(root, ["character ama has no bible/canon/characters/ama.md"])
        pier = "production/assets/pier/assets/state.json"
        root = self.variant({pier: state(asset="pier", kind="set", skeleton=None)}, base=FIX / "clean")
        self.assertGives(root, ["set pier has no bible/canon/places/pier.md"], path=pier)
        harbour = "production/assets/harbour/assets/state.json"
        self.assertPasses(self.variant({harbour: state(asset="harbour", kind="set", skeleton=None)},
                                       base=FIX / "clean"))
        lamp = "production/assets/lamp/assets/state.json"
        self.assertPasses(self.variant({lamp: state(asset="lamp", kind="prop", triangles=20000)},
                                       base=FIX / "clean"))

    def test_6_budgets(self):
        lamp = "production/assets/lamp/assets/state.json"
        root = self.variant({lamp: state(asset="lamp", kind="prop", triangles=20001)}, base=FIX / "clean")
        result = self.assertGives(root, ["20001 triangles, over the prop budget of 20000"], path=lamp)
        self.assertEqual(result.findings[0].rule, "budgets")
        harbour = "production/assets/harbour/assets/state.json"
        self.assertPasses(self.variant({harbour: state(asset="harbour", kind="set", triangles=1000000)},
                                       base=FIX / "clean"))
        textures = [{"name": "b", "width": 8192, "height": 4096}, {"name": "a", "width": 4096, "height": 4097}]
        root = self.variant({STATE: state(textures=textures)}, base=FIX / "clean")
        self.assertGives(root, ["texture a is 4096x4097, over 4096 pixels a side",
                                "texture b is 8192x4096, over 4096 pixels a side"])

    def test_7_scale(self):
        no_row = "no height row for ama in bible/canon/ledger.tsv"
        self.assertGives(self.variant({LEDGER: HEADER}, base=FIX / "clean"), [no_row])
        root = self.variant({}, base=FIX / "clean")
        (root / LEDGER).unlink()
        result = self.assertGives(root, [no_row])
        self.assertEqual(result.findings[0].rule, "scale")
        root = self.variant({LEDGER: HEADER + "Ama\t Height \t5 ft 4\tinvented\t\n"}, base=FIX / "clean")
        self.assertGives(root, ["height '5 ft 4' of ama (bible/canon/ledger.tsv:2) is not '<number> m'"])
        self.assertPasses(self.variant({STATE: state(height_m=1.78)}, base=FIX / "clean"))
        self.assertPasses(self.variant({LEDGER: HEADER + "ama\theight\t1.62m\tinvented\t\n"}, base=FIX / "clean"))
        two = HEADER + "ama\theight\t2 m\tinvented\t\n"
        self.assertPasses(self.variant({LEDGER: two, STATE: state(height_m=2.2)}, base=FIX / "clean"))
        self.assertGives(self.variant({LEDGER: two, STATE: state(height_m=2.21)}, base=FIX / "clean"),
                         ["height_m 2.21 is not within 10% of 2 m (bible/canon/ledger.tsv:2)"])
        harbour = "production/assets/harbour/assets/state.json"
        body = state(asset="harbour", kind="set", height_m=12.0, skeleton=None)
        root = self.variant({harbour: body, LEDGER: HEADER + AMA_ROW + "harbour\theight\t20 m\tinvented\t\n"},
                            base=FIX / "clean")
        self.assertGives(root, ["height_m 12 is not within 10% of 20 m (bible/canon/ledger.tsv:3)"], path=harbour)
        self.assertPasses(self.variant({harbour: body}, base=FIX / "clean"))

    def test_8_skeleton(self):
        self.assertPasses(self.variant({STATE: state(skeleton=None)}, base=FIX / "clean"))
        cases = [
            (state(skeleton=bones(("root", None), ("spine", "root"), ("head", "spine"), ("spine", "root"))),
             "bone name spine repeats"),
            (state(skeleton=bones(("root", "head"), ("spine", "root"), ("head", "spine"))),
             "skeleton has 0 roots, not one"),
            (state(skeleton=dict(CLEAN["skeleton"], weights_normalised=False)), "skin weights are not normalised"),
        ]
        for body, message in cases:
            with self.subTest(message):
                result = self.assertGives(self.variant({STATE: body}, base=FIX / "clean"), [message])
                self.assertEqual(result.findings[0].rule, "skeleton")
        lamp = "production/assets/lamp/assets/state.json"
        self.assertPasses(self.variant({lamp: state(asset="lamp", kind="prop", triangles=100, skeleton=None)},
                                       base=FIX / "clean"))

    def test_8_skeleton_problems(self):
        self.assertEqual(assets_lint.skeleton_problems(CLEAN["skeleton"]), [])
        two = bones(("b", None), ("a", None), ("a", "b"), ("b", "a"))
        self.assertEqual(assets_lint.skeleton_problems(dict(two, weights_normalised=False)),
                         ["skeleton has 2 roots, not one", "bone name a repeats", "bone name b repeats",
                          "skin weights are not normalised"])

    def test_9_other_files(self):
        root = self.variant({"production/assets/ama/rigging/state.json": "{",
                             "production/ep01/s01/layout/state.json": "{"}, base=FIX / "clean")
        self.assertPasses(root)
        result = self.assets(self.variant({STATE: "{"}, base=FIX / "clean"))
        self.assertEqual(result.status, lint.ERROR)
        self.assertTrue(result.reason.startswith(f"cannot parse {STATE}"), result.reason)

    def test_10_deterministic(self):
        tobi = "production/assets/tobi/assets/state.json"
        lamp = "production/assets/lamp/assets/state.json"
        root = self.variant({
            STATE: state(asset="Ama_2"),
            "production/assets/x/assets/state.json": state(asset="y"),
            tobi: state(asset="tobi", triangles=100001, height_m=3.0,
                        textures=[{"name": "z", "width": 9000, "height": 1}],
                        skeleton=bones(("a", None), ("b", None), ("b", "a"))),
            lamp: state(asset="lamp", kind="prop", triangles=20001),
            LEDGER: HEADER + AMA_ROW + "tobi\theight\t1.5 m\tinvented\t\n",
        }, base=FIX / "clean")
        first = lint.text(lint.run(root, only=["assets"]))
        self.assertEqual(first, lint.text(lint.run(root, only=["assets"])))
        rules = {f.rule for f in lint.run(root, only=["assets"])["assets"].findings}
        self.assertEqual(rules, {"naming", "references", "budgets", "scale", "skeleton"})


    def found(self, files, drop=()):
        """The (path, line, rule, message) of `assets` on the clean fixture with `files` over it, less `drop`."""
        root = self.variant(files, base=FIX / "clean")
        for rel in drop:
            (root / rel).unlink()
        return [(f.path, f.line, f.rule, f.message) for f in self.assets(root).findings]

    def test_job_files_1_scene_alone(self):
        self.assertEqual(self.found({SCENE: SCRIPT}, drop=[STATE]), ALONE)

    def test_job_files_2_request(self):
        d = "production/assets/ama/assets"
        self.assertEqual(self.found({SCENE: SCRIPT, f"{d}/requests/job.json": "{}\n"}, drop=[STATE]), [])
        for request in (f"{d}/requests/job.txt", f"{d}/requests/old/job.json",
                        "production/assets/lamp/assets/requests/job.json"):
            with self.subTest(request):
                self.assertEqual(self.found({SCENE: SCRIPT, request: "{}\n"}, drop=[STATE]), ALONE)

    def test_job_files_3_after_a_run(self):
        d = "production/assets/ama/assets"
        self.assertEqual(self.found({SCENE: SCRIPT}), [(SCENE, 1, "depends", f"{d}/manifest.json is missing")])
        files = {SCENE: SCRIPT, f"{d}/requests/job.json": "{}\n", f"{d}/manifest.json": "{}\n"}
        self.assertEqual(self.found(files, drop=[STATE]), [(SCENE, 1, "depends", f"{STATE} is missing")])

    def test_job_files_4_complete(self):
        self.assertEqual(self.found({SCENE: SCRIPT, "production/assets/ama/assets/manifest.json": "{}\n"}), [])

    def test_job_files_5_other_directories(self):
        for rel in ("production/assets/tobi/rigging/scene.py", "production/assets/tobi/assets/old/scene.py",
                    "production/ep01/Q1-9/assets/scene.py", "assets/tobi/assets/scene.py"):
            with self.subTest(rel):
                self.assertEqual(self.found({rel: SCRIPT}), [])

    def test_job_files_6_deterministic(self):
        bo = "production/assets/bo/assets/scene.py"
        files = {SCENE: SCRIPT, bo: SCRIPT}
        found = self.found(files, drop=[STATE])
        self.assertEqual(found, ALONE + [(bo, 1, "depends", m.replace("/ama/", "/bo/")) for *_, m in ALONE])
        self.assertEqual(self.found(files, drop=[STATE]), found)

    def requests(self, *files, drop=()):
        """The (path, rule, message) of `assets` on the clean fixture with the model sheets, the manifest and
        ``files`` (``(path, text)`` pairs) over it, less ``drop``; every finding on line 1."""
        found = self.found(dict(SUBJECTS, **dict(files)), drop=drop)
        self.assertEqual({line for _, line, _, _ in found} - {1}, set())
        return [(path, rule, message) for path, _, rule, message in found]

    def test_requests_1_shape(self):
        rel, body = request("ama", "media:ama-ref")
        self.assertEqual(self.requests((rel, body)), [])
        self.assertEqual(self.requests((rel, body), drop=[STATE]), [])
        cases = [
            ("[]\n", "request is not {for, prompt, refs}"),
            (request("ama", "media:ama-ref", **{"for": "tobi"})[1], "request for tobi is not for asset ama"),
            (request("ama")[1], "request names no reference image"),
            (request("ama", 7)[1], "refs[0] is not media:<id>"),
            (request("ama", "media:ama-sketch")[1], "refs[0] media:ama-sketch has no row in media/manifest.tsv"),
        ]
        for text, message in cases:
            with self.subTest(message):
                self.assertEqual(self.requests((rel, text)), [(rel, "requests", message)])
        self.assertEqual(self.requests((rel, request("ama", "media:ama-ref", **{"for": 7})[1]), drop=[STATE]),
                         [(rel, "requests", "request for 7 is not for asset ama")])
        not_a_request = [(rel, "requests", "request is not {for, prompt, refs}")]
        for text in ("{", json.dumps({"for": "ama", "refs": []}),
                     json.dumps({"for": "ama", "prompt": "", "refs": "x"})):
            with self.subTest(text):
                self.assertEqual(self.requests((rel, text)), not_a_request)
        self.assertEqual(self.requests((rel, request("tobi")[1])),
                         [(rel, "requests", "request names no reference image")])

    def test_requests_2_another_subject(self):
        rel, body = request("harbour", "media:ama-ref")
        result = self.assertGives(self.variant(dict(SUBJECTS, **{rel: body}), base=FIX / "clean"), [
            "refs[0] media:ama-ref is cited by bible/style/characters/ama.md, not by bible/style/places/harbour.md"],
            path=rel)
        self.assertEqual(lint.blockers({"assets": result}), [
            f"B1 assets {rel}:1 refs[0] media:ama-ref is cited by bible/style/characters/ama.md, "
            "not by bible/style/places/harbour.md"])
        self.assertEqual(self.requests(request("harbour", "media:harbour-ref", "media:harbour-sketch")), [
            (rel, "requests", "refs[1] media:harbour-sketch is not cited by bible/style/places/harbour.md")])
        self.assertEqual(self.requests(request("harbour", "media:harbour-ref", "media:harbour-pier")), [])

    def test_requests_3_no_sheet_and_props(self):
        rel, body = request("ama", "media:ama-ref", "media:harbour-ref", "media:ama-sketch")
        self.assertEqual(self.requests((rel, body), drop=[AMA_SHEET]), [
            (rel, "requests", "character ama has no model sheet bible/style/characters/ama.md"),
            (rel, "requests", "refs[2] media:ama-sketch has no row in media/manifest.tsv")])
        lantern, body = request("lantern", "media:ama-ref")
        self.assertEqual(self.requests((lantern, body)), [
            (lantern, "requests",
             "refs[0] media:ama-ref is cited by bible/style/characters/ama.md, the model sheet of another subject")])
        self.assertEqual(self.requests(request("lantern", "media:lantern-ref")), [])

    def test_requests_4_other_files_and_order(self):
        for rel in ("production/assets/ama/assets/requests/rig.json", "production/assets/ama/requests/model3d.json",
                    "production/assets/ama/assets/requests/old/model3d.json"):
            with self.subTest(rel):
                self.assertEqual(self.requests((rel, "[]\n")), [])
        files = [request("lantern", "media:ama-ref"), request("harbour", "media:ama-ref", **{"for": "ama"}),
                 ("bible/style/characters/wickabel.md", sheet("Wickabel of Skarrowfen", "ama-ref"))]
        found = self.requests(*files)
        self.assertEqual(found, [
            (files[1][0], "requests", "refs[0] media:ama-ref is cited by bible/style/characters/ama.md, "
                                      "not by bible/style/places/harbour.md"),
            (files[1][0], "requests", "request for ama is not for asset harbour"),
            (files[0][0], "requests", "refs[0] media:ama-ref is cited by bible/style/characters/ama.md, "
                                      "the model sheet of another subject")])
        root = self.variant(dict(SUBJECTS, **dict(files)), base=FIX / "clean")
        self.assertEqual([f.message for f in assets_lint.check(root) if f.path == files[1][0]], [
            "request for ama is not for asset harbour",
            "refs[0] media:ama-ref is cited by bible/style/characters/ama.md, not by bible/style/places/harbour.md"])
        self.assertEqual(self.requests(*files), found)


if __name__ == "__main__":
    unittest.main()
