"""voices: every stored voice is one the voice tool reads, and every speaker has one."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lint import LintCase, lint  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "lint_voices"
AMA = "bible/style/voices/ama.json"
NELL = "bible/style/voices/nell.json"
VALID = '{"voice_id": "q7Ama0voice0id0x1234"}\n'
BAD = 'the stored voice file is not {"voice_id": <id>}'
NOT_A_VOICE_FILE = "the voice tool reads only bible/style/voices/<character>.json, <character> of a-z, 0-9 and -"


def lines_json(*speakers):
    return "[" + ", ".join(f'{{"id": "L{n}", "scene": "S1", "speaker": "{s}", "text": "Hi."}}'
                           for n, s in enumerate(speakers, 1)) + "]\n"


class Voices(LintCase):
    CHECKS = ("voices",)

    def variant(self, files, base=FIX / "clean"):
        return super().variant(files, base=base)

    def voices(self, root):
        return lint.run(root, only=["voices"])["voices"]

    def found(self, root):
        result = self.voices(root)
        self.assertNotEqual(result.status, lint.ERROR, result.reason)
        return [(f.path, f.line, f.rule, f.message) for f in result.findings]

    def assertPasses(self, root):
        result = self.voices(root)
        self.assertEqual(result.status, lint.PASS, (result.findings, result.reason))

    def test_clean_passes(self):
        self.assertPasses(FIX / "clean")

    # Behaviour 1: a stored voice the voice tool would refuse.

    def test_bad_voice_file(self):
        for body in ('{"voice": "q7Ama0voice0id0x1234"}\n', '{"voice_id": "q7Ama0voice0id0x1234", "name": "Ama"}\n',
                     '{"voice_id": 7}\n', '{"voice_id": "not an id"}\n', '{"voice_id": ""}\n', '["q7Ama"]\n',
                     "voice_id: q7Ama\n", b"\xff\xfe"):
            with self.subTest(body=body):
                self.assertEqual(self.found(self.variant({AMA: body})), [(AMA, 1, "voice-file", BAD)])

    # Behaviour 2: a speaker with no stored voice; the finding is on the voice file.

    def test_speaker_without_a_voice(self):
        root = self.variant({"episodes/ep01/lines.json": lines_json("Ama", "Tobi", "Nell", " NELL ")})
        self.assertEqual(self.found(root), [
            (NELL, 1, "speakers", "speaker  NELL  of episodes/ep01/lines.json has no stored voice"),
            (NELL, 1, "speakers", "speaker Nell of episodes/ep01/lines.json has no stored voice")])

    def test_speaker_with_a_voice_passes(self):
        self.assertPasses(self.variant({"episodes/ep01/lines.json": lines_json("Ama", "Tobi", "Nell", " NELL "),
                                        NELL: '{"voice_id": "q7Nell0voice0id0x123"}\n'}))

    # Behaviour 3: a speaker whose name makes no voice file name.

    def test_speaker_without_a_file_name(self):
        root = self.variant({"episodes/ep01/lines.json": lines_json("Ama", "Tobi", "Old Man")})
        self.assertEqual(self.found(root), [
            ("episodes/ep01/lines.json", 1, "speakers",
             "speaker Old Man has no voice file name: 'old man' is not of a-z, 0-9 and -")])

    # Behaviour 4: a file under bible/style/voices/ the voice tool never reads.

    def test_misnamed_voice_file(self):
        for rel in ("bible/style/voices/old_man.json", "bible/style/voices/ama.txt", "bible/style/voices/old/ama.json"):
            with self.subTest(rel=rel):
                self.assertEqual(self.found(self.variant({rel: VALID})), [(rel, 1, "voice-file", NOT_A_VOICE_FILE)])

    def test_misnamed_voice_file_shape_is_not_read(self):
        rel = "bible/style/voices/old_man.json"
        self.assertEqual(self.found(self.variant({rel: "not json"})), [(rel, 1, "voice-file", NOT_A_VOICE_FILE)])

    # Behaviour 5: a lines file that is not a list of lines is structure's finding.

    def test_lines_not_a_list_passes(self):
        for body in ('{"L1": "Ama"}\n', "not json\n", b"\xff\xfe"):
            with self.subTest(body=body):
                self.assertPasses(self.variant({"episodes/ep01/lines.json": body}))

    # Behaviour 6: an unused voice, and a tree with neither.

    def test_unused_voice_passes(self):
        self.assertPasses(self.variant({NELL: '{"voice_id": "q7Nell0voice0id0x123"}\n'}))

    def test_tree_without_lines_or_voices_passes(self):
        root = self.variant({})
        for rel in ("episodes/ep01/lines.json", AMA, "bible/style/voices/tobi.json"):
            (root / rel).unlink()
        self.assertPasses(root)

    # Behaviour 7: the same tree, the same text.

    def test_output_is_deterministic(self):
        root = self.variant({"episodes/ep01/lines.json": lines_json("Ama", "Nell", "Old Man"),
                             "bible/style/voices/tobi.json": '{"voice_id": "q7Tobi0voice0id0x123", "name": "Tobi"}\n',
                             "bible/style/voices/old_man.json": VALID})
        first = lint.text(lint.run(root, only=["voices"]))
        self.assertEqual(first, lint.text(lint.run(root, only=["voices"])))
        self.assertEqual(first.splitlines()[0], "voices FAIL 4 findings")


if __name__ == "__main__":
    unittest.main()
