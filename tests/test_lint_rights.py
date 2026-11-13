"""rights: the rights file, the origin of canon sheets, the not-ours list and the speakers."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lint import LintCase, lint  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "lint_rights"
HEADER = "path\twork\tholder\tbasis\tnote\n"
AMA = "sources/ama/**\tAma\tthe owner\town\t\n"
PLATES = "media:lighthouse-*\tLighthouse plates\tthe owner\tlicence\t\n"
TOBI = "bible/canon/characters/tobi.md"
PLACE = "bible/canon/places/lighthouse.md"
NOT_OURS = "'Wickabel' is on research/not-ours.tsv:2"


def lines_json(*speakers):
    return "[" + ", ".join(f'{{"id": "L{n}", "scene": "S1", "speaker": "{s}", "text": "Hi."}}'
                           for n, s in enumerate(speakers, 1)) + "]\n"


class Rights(LintCase):
    CHECKS = ("rights",)

    def variant(self, files, base=FIX / "clean"):
        return super().variant(files, base=base)

    def rights(self, root):
        return lint.run(root, only=["rights"])["rights"]

    def found(self, root):
        return [(f.path, f.line, f.rule, f.message) for f in self.rights(root).findings]

    def assertPasses(self, root):
        result = self.rights(root)
        self.assertEqual(result.status, lint.PASS, (result.findings, result.reason))

    def test_clean_passes(self):
        self.assertPasses(FIX / "clean")

    # Behaviour 1: the rights file.

    def test_rights_rows(self):
        root = self.variant({"sources/rights.tsv": HEADER + AMA + PLATES + "x/**\tX\t \town\t\n" + "y/**\tY\tthem\tborrowed\t\n"
                             + "z/**\tZ\tthem\tOwn\t\n" + "w/**\tW\t\tborrowed\t\n"})
        self.assertEqual(self.found(root), [
            ("sources/rights.tsv", 4, "rights-row", "holder is empty"),
            ("sources/rights.tsv", 5, "rights-row", "basis 'borrowed' is not own, licence, permission or public-domain"),
            ("sources/rights.tsv", 6, "rights-row", "basis 'Own' is not own, licence, permission or public-domain"),
            ("sources/rights.tsv", 7, "rights-row", "basis 'borrowed' is not own, licence, permission or public-domain"),
            ("sources/rights.tsv", 7, "rights-row", "holder is empty")])

    def test_header_without_a_column(self):
        root = self.variant({"sources/rights.tsv": "path\twork\tbasis\tnote\n" + "x/**\tX\tborrowed\t\n"})
        self.assertEqual([f for f in self.found(root) if f[2] == "rights-row"],
                         [("sources/rights.tsv", 1, "rights-row", "header has no holder column")])
        root = self.variant({"sources/rights.tsv": "path\twork\tbasis\n"})
        self.assertEqual([f for f in self.found(root) if f[2] == "rights-row"],
                         [("sources/rights.tsv", 1, "rights-row", "header has no holder or note column")])

    def test_padded_basis_passes(self):
        self.assertPasses(self.variant({"sources/rights.tsv": HEADER + AMA + PLATES.replace("licence", " public-domain ")}))

    # Behaviour 2: origin: source.

    def test_origin_source(self):
        root = self.variant({TOBI: "Tobi.\norigin: source\n", PLACE: "Light.\n\norigin: source sources/tobi/notes.md\n"})
        self.assertEqual(self.found(root), [
            ("bible/canon/characters/tobi.md", 2, "origin", "origin: source cites no file or media id"),
            ("bible/canon/places/lighthouse.md", 3, "origin",
             "sources/tobi/notes.md is not covered by a row of sources/rights.tsv")])

    def test_without_the_rights_file_every_source_sheet_fails(self):
        root = self.variant({})
        (root / "sources/rights.tsv").unlink()
        self.assertEqual(self.found(root), [
            ("bible/canon/characters/ama.md", 2, "origin",
             "sources/ama/notes.md is not covered by a row of sources/rights.tsv"),
            ("bible/canon/places/lighthouse.md", 2, "origin",
             "media:lighthouse-ref is not covered by a row of sources/rights.tsv")])

    def test_words_after_the_second_are_not_read(self):
        self.assertPasses(self.variant({PLACE: "Light.\norigin: source media:lighthouse-ref extra words\n"}))

    # Behaviour 3: origin: house.

    def test_origin_house(self):
        for line, message in (("origin: house", "origin: house names no amendment"),
                              ("origin: house A1", "A1 is not the id A-<n> of an amendment"),
                              ("origin: house A-2", "amendment A-2: bible/amendments/A-2.json is missing")):
            with self.subTest(line):
                root = self.variant({TOBI: f"Tobi.\n{line}\n"})
                self.assertEqual(self.found(root), [(TOBI, 2, "origin", message)])

    def test_an_origin_that_is_not_source_or_house_is_not_ours_to_report(self):
        self.assertPasses(self.variant({TOBI: "Tobi.\norigin: borrowed\n", PLACE: "Light.\norigin:\n"}))

    # Behaviour 4: the not-ours list.

    def test_listed_name(self):
        root = self.variant({"episodes/ep01/script.md": "Intro.\nHe saw wickabel on the pier.\n",
                             "post/ep01/title.json": '{\n "title": "Wickabel\'s pier"\n}\n'})
        self.assertEqual(self.found(root), [("episodes/ep01/script.md", 2, "not-ours", NOT_OURS),
                                            ("post/ep01/title.json", 2, "not-ours", NOT_OURS)])

    def test_every_checked_tree_and_every_name(self):
        root = self.variant({"bible/style/look.md": "Skarrowfen colours, Wickabel ears.\n",
                             "bible/canon/places/lighthouse.md": "Like Skarrowfen.\norigin: source media:lighthouse-ref\n",
                             "distribution/ep01/upload.json": '{"title": "SKARROWFEN"}\n'})
        self.assertEqual(self.found(root), [
            ("bible/canon/places/lighthouse.md", 1, "not-ours", "'Skarrowfen' is on research/not-ours.tsv:3"),
            ("bible/style/look.md", 1, "not-ours", "'Skarrowfen' is on research/not-ours.tsv:3"),
            ("bible/style/look.md", 1, "not-ours", NOT_OURS),
            ("distribution/ep01/upload.json", 1, "not-ours", "'Skarrowfen' is on research/not-ours.tsv:3")])

    def test_whole_words_only_and_unchecked_paths(self):
        root = self.variant({"episodes/ep01/script.md": "Two Wickabels on the pier.\n",
                             "research/trends/more.md": "Wickabel\n", "sources/ama/more.md": "Wickabel\n",
                             "bible/charter.md": "Wickabel\n", "rules/production-rules.md": "Wickabel\n",
                             "episodes/ep01/raw.bin": b"Wickabel \xff\n"})
        self.assertPasses(root)

    def test_list_without_a_name_column(self):
        root = self.variant({"research/not-ours.tsv": "who\tholder\tlink\nWickabel\tthem\thttps://example.invalid/1\n",
                             "episodes/ep01/script.md": "Wickabel.\n"})
        self.assertEqual(self.found(root), [("research/not-ours.tsv", 1, "not-ours", "header has no name column")])

    # Behaviour 5: speakers.

    def test_speaker_without_a_sheet(self):
        root = self.variant({"episodes/ep01/lines.json": lines_json("AMA", "NELL", "NELL")})
        self.assertEqual(self.found(root), [("episodes/ep01/lines.json", 1, "speakers",
                                             "speaker NELL has no bible/canon/characters/nell.md")])

    def test_speaker_is_trimmed_and_lower_cased(self):
        self.assertPasses(self.variant({"episodes/ep01/lines.json": lines_json(" Ama ", "TOBI")}))

    def test_lines_that_are_not_a_list_are_skipped(self):
        self.assertPasses(self.variant({"episodes/ep01/lines.json": '{"speaker": "NELL"}\n'}))

    # Behaviour 6: absent files.

    def test_empty_tree_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "bible").mkdir()
            (Path(tmp) / "bible" / "charter.md").write_text("Wickabel\n")
            self.assertPasses(Path(tmp))

    def test_factory_init_production_lists_rights_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            done = subprocess.run([sys.executable, str(ROOT / "kit" / "factory"), "init", tmp],
                                  capture_output=True, text=True, env=env)
            self.assertEqual(done.returncode, 0, done.stderr)
            done = subprocess.run([sys.executable, str(Path(tmp) / ".factory" / "factory"), "lint"],
                                  capture_output=True, text=True, cwd=tmp, env=env)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertIn("rights PASS", done.stdout.splitlines())

    # Behaviour 7: deterministic output.

    def test_output_is_deterministic(self):
        root = self.variant({"sources/rights.tsv": HEADER + AMA + PLATES.replace("licence", "borrowed"),
                             TOBI: "Tobi.\norigin: source sources/tobi/notes.md\n",
                             PLACE: "Light.\norigin: house A-2\n",
                             "episodes/ep01/lines.json": lines_json("NELL", "AMA", "ZED"),
                             "episodes/ep01/script.md": "Intro.\nHe saw wickabel and Skarrowfen.\n"})
        first = lint.text(lint.run(root, only=["rights"]))
        self.assertEqual(lint.text(lint.run(root, only=["rights"])), first)
        self.assertTrue(first.startswith("rights FAIL 7 findings\n"), first)


class Refusal(unittest.TestCase):
    """`rights.refusal`, the reason the gate refuses a job."""

    LIST = "name\tholder\tlink\nWickabel\tthem\thttps://example.invalid/1\nSkarrowfen\tthem\thttps://example.invalid/2\n"

    def refusal(self, texts, list_text=LIST):
        from kit.lib.lints import rights
        asked = []

        def text_of(path):
            asked.append(path)
            return texts.get(path)
        return rights.refusal(text_of, list_text, [{"path": p, "sha256": "0"} for p in texts]), asked

    def test_first_input_and_first_name(self):
        reason, _ = self.refusal({"a.json": "fine", "b.json": "skarrowfen and Wickabel", "c.json": "Wickabel"})
        self.assertEqual(reason, "input b.json names 'Wickabel' (research/not-ours.tsv:2)")

    def test_unread_paths_missing_inputs_and_no_list(self):
        reason, asked = self.refusal({"bible/charter.md": "Wickabel", "research/x.md": "Wickabel",
                                      "sources/y.md": "Wickabel", "gone.json": None, "z.json": "Wickabels"})
        self.assertEqual((reason, asked), (None, ["gone.json", "z.json"]))
        self.assertEqual(self.refusal({"a.json": "Wickabel"}, None)[0], None)
        self.assertEqual(self.refusal({"a.json": "Wickabel \udcff"})[0], None)

    def test_list_without_a_name_column(self):
        self.assertEqual(self.refusal({"a.json": "fine"}, "who\nWickabel\n")[0],
                         "research/not-ours.tsv has no name column")


if __name__ == "__main__":
    unittest.main()
