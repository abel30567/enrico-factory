"""media: rules 7 and 8. Binary and oversized files are built at test time, never committed."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lint import FIX, LintCase, lint  # noqa: E402

SHA = "bde335e7ea12ab2dca8b8245998e6827aeaf7b1426046652a6ac34c4ec3a93ce"
HEADER = "id\tsha256\tbytes\tsource\n"
MIB = 1024 * 1024


class Media(LintCase):
    def test_clean_passes(self):
        self.assertEqual(lint.run(FIX / "clean", only=["media"])["media"].status, lint.PASS)

    def test_rule7_media_bytes(self):
        cases = {
            "extension": ({"art/sketch.PNG": "text, but a media extension\n"}, ("art/sketch.PNG", 1)),
            "nul byte": ({"data/blob.dat": b"line one\nline two\x00\n"}, ("data/blob.dat", 2)),
            "oversized": ({"data/big.txt": "a" * (MIB + 1)}, ("data/big.txt", 1)),
        }
        for name, (files, (path, line)) in cases.items():
            with self.subTest(name):
                self.assertOnly(self.variant(files), "media", [(path, line, "media-bytes")])

    def test_rule7_limits_and_git_skipped(self):
        root = self.variant({"data/exact.txt": "a" * MIB, ".git/objects/sketch.png": b"\x89PNG\x00",
                             "data/late-nul.txt": b"a" * 8192 + b"\x00"})
        self.assertEqual(lint.run(root, only=["media"])["media"].status, lint.PASS)

    def test_rule8_duplicate_id(self):
        self.assertOnly(FIX / "r8_manifest", "media", [("media/manifest.tsv", 3, "manifest-id")])

    def test_rule8_manifest_rows(self):
        cases = {
            "header": ("id\tsha\tbytes\tsource\n", 1, "manifest-header"),
            "three fields": (HEADER + f"a\t{SHA}\t10\n", 2, "manifest-row"),
            "empty field": (HEADER + f"a\t{SHA}\t10\t \n", 2, "manifest-row"),
            "upper-case sha": (HEADER + f"a\t{SHA.upper()}\t10\tjob\n", 2, "manifest-sha256"),
            "short sha": (HEADER + f"a\t{SHA[:63]}\t10\tjob\n", 2, "manifest-sha256"),
            "zero bytes": (HEADER + f"a\t{SHA}\t0\tjob\n", 2, "manifest-bytes"),
            "text bytes": (HEADER + f"a\t{SHA}\t2k\tjob\n", 2, "manifest-bytes"),
        }
        for name, (body, line, rule) in cases.items():
            with self.subTest(name):
                root = self.variant({"media/manifest.tsv": body}, base="r8_manifest")
                self.assertOnly(root, "media", [("media/manifest.tsv", line, rule)])


if __name__ == "__main__":
    unittest.main()
