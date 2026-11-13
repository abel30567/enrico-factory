"""The images of the look-reviewer suite (kit/evals/look-reviewer/<case>/media/), checked offline.

Each image stands beside its case's tree, matches the tree's manifest row, is the one draw.py draws from
the tree, and reaches the run directory through cmd_eval.place_media. Seeded defects are made in a copy
of the suite under a temporary directory.
"""

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kit.lib import cmd_eval, evals  # noqa: E402
from test_story_evals import APPROVED, FAKE, FIX, KIT  # noqa: E402

SUITE = KIT / "evals" / "look-reviewer"
CITED = re.compile(r"media:([a-z0-9-]+)")
HEIGHT = re.compile(r"(?m)^Height: (\d+) cm$")
WHITE = (255, 255, 255)


def case_dirs(suite):
    return sorted(p for p in suite.iterdir() if p.is_dir() and (p / "tree").is_dir())


def manifest(case):
    rows = (case / "tree" / "media" / "manifest.tsv").read_text(encoding="utf-8").splitlines()[1:]
    return {row.split("\t")[0]: row.split("\t") for row in rows}


def media_problems(suite):
    """One line per disagreement between the ids a case's style cites, its manifest and its media/."""
    lines = []
    for case in case_dirs(suite):
        where, rows = f"look-reviewer/{case.name}", manifest(case)
        cited = set()
        for path in (case / "tree" / "bible" / "style").rglob("*.md"):
            cited.update(CITED.findall(path.read_text(encoding="utf-8")))
        lines += [f"{where}: media:{ident} has no manifest row" for ident in sorted(cited - set(rows))]
        for ident, (_, sha, size, _) in sorted(rows.items()):
            path = case / "media" / f"{ident}.png"
            if not path.is_file():
                lines.append(f"{where}: media:{ident} has no media/{ident}.png")
            elif hashlib.sha256(path.read_bytes()).hexdigest() != sha or path.stat().st_size != int(size):
                lines.append(f"{where}: media/{ident}.png is not the bytes its manifest row names")
        names = sorted(p.name for p in (case / "media").iterdir()) if (case / "media").is_dir() else []
        lines += [f"{where}: media/{name} has no manifest row" for name in names
                  if name[:-len(".png")] not in rows or not name.endswith(".png")]
    return lines


def redraw_problems(suite):
    """The images ``draw.py`` does not draw again byte for byte, after every PNG of ``suite`` is deleted."""
    before = {str(p.relative_to(suite)): p.read_bytes() for p in suite.glob("*/media/*.png")}
    for name in before:
        (suite / name).unlink()
    done = subprocess.run([sys.executable, "-B", str(suite / "draw.py")], capture_output=True, text=True,
                          timeout=120)
    after = {str(p.relative_to(suite)): p.read_bytes() for p in suite.glob("*/media/*.png")}
    lines = [f"draw.py exit {done.returncode}: {done.stderr}"] if done.returncode else []
    lines += [name for name in sorted(set(before) | set(after)) if before.get(name) != after.get(name)]
    return lines + [str(p.relative_to(suite)) for p in suite.rglob("__pycache__")]


def decode(data):
    """``(width, height, colour type, rows of (r, g, b))`` of an 8-bit PNG whose rows all use filter 0."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    at, chunks = 8, {}
    while at < len(data):
        size, kind = struct.unpack(">I4s", data[at:at + 8])
        chunks.setdefault(kind, b"")
        chunks[kind] += data[at + 8:at + 8 + size]
        at += 12 + size
    width, height, depth, colour = struct.unpack(">IIBB", chunks[b"IHDR"][:10])
    raw, stride = zlib.decompress(chunks[b"IDAT"]), 1 + 3 * width
    rows = []
    for y in range(height):
        line = raw[y * stride:(y + 1) * stride]
        assert line[0] == 0 and depth == 8
        rows.append([tuple(line[1 + 3 * x:4 + 3 * x]) for x in range(width)])
    return width, height, colour, rows


def geometry_problems(suite):
    """The images not 480 by 400 RGB, or whose front view does not stand ``2n`` px tall on row 390."""
    lines = []
    for case in case_dirs(suite):
        sheets = case / "tree" / "bible" / "style" / "model-sheets"
        for ident in sorted(manifest(case)):
            n = int(HEIGHT.search((sheets / f"{ident[:-len('-turnaround')]}.md").read_text(encoding="utf-8"))[1])
            width, height, colour, rows = decode((case / "media" / f"{ident}.png").read_bytes())
            inked = [y for y, row in enumerate(rows) if any(p != WHITE for p in row[:160])]
            if (width, height, colour) != (480, 400, 2) or (inked[0], inked[-1]) != (390 - 2 * n, 389):
                lines.append(f"look-reviewer/{case.name}: media/{ident}.png")
    return lines


def colours(path):
    return {p for row in decode(path.read_bytes())[3] for p in row}


def tree_files(path):
    return sorted(str(p.relative_to(path)) for p in path.rglob("*") if p.is_file())


class LookReviewerMediaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.copy = self.dir / "look-reviewer"
        shutil.copytree(SUITE, self.copy, ignore=shutil.ignore_patterns("__pycache__"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_cited_ids_rows_and_files_agree(self):
        self.assertEqual(media_problems(SUITE), [])
        where = "look-reviewer/02-lantern-quay"
        sheet = self.copy / "02-lantern-quay" / "tree" / "bible" / "style" / "model-sheets" / "bo.md"
        sheet.write_text(sheet.read_text(encoding="utf-8") + "\nSee media:bo-sketch.\n", encoding="utf-8")
        self.assertEqual(media_problems(self.copy), [f"{where}: media:bo-sketch has no manifest row"])
        sheet.write_text((SUITE / sheet.relative_to(self.copy)).read_text(encoding="utf-8"), encoding="utf-8")
        image = self.copy / "02-lantern-quay" / "media" / "ivy-turnaround.png"
        image.unlink()
        self.assertEqual(media_problems(self.copy), [f"{where}: media:ivy-turnaround has no media/ivy-turnaround.png"])
        image.write_bytes((SUITE / "02-lantern-quay" / "media" / "ivy-turnaround.png").read_bytes() + b"\x00")
        self.assertEqual(media_problems(self.copy),
                         [f"{where}: media/ivy-turnaround.png is not the bytes its manifest row names"])
        shutil.copyfile(SUITE / "02-lantern-quay" / "media" / "ivy-turnaround.png", image)
        shutil.copyfile(image, image.with_name("ivy-sketch.png"))
        self.assertEqual(media_problems(self.copy), [f"{where}: media/ivy-sketch.png has no manifest row"])

    def test_images_are_the_drawn_ones(self):
        self.assertEqual(redraw_problems(self.copy), [])
        self.assertEqual(sorted(p.name for p in self.copy.rglob("__pycache__")), [])
        seeded = self.dir / "seeded"
        shutil.copytree(SUITE, seeded, ignore=shutil.ignore_patterns("__pycache__"))
        image = seeded / "05-moth-garden" / "media" / "oto-turnaround.png"
        data = bytearray(image.read_bytes())
        data[-20] ^= 1
        image.write_bytes(bytes(data))
        self.assertEqual(redraw_problems(seeded), ["05-moth-garden/media/oto-turnaround.png"])

    def test_each_image_stands_as_tall_as_its_sheet(self):
        self.assertEqual(geometry_problems(SUITE), [])
        sheet = self.copy / "05-moth-garden" / "tree" / "bible" / "style" / "model-sheets" / "ines.md"
        sheet.write_text(HEIGHT.sub("Height: 175 cm", sheet.read_text(encoding="utf-8")), encoding="utf-8")
        self.assertEqual(geometry_problems(self.copy), ["look-reviewer/05-moth-garden: media/ines-turnaround.png"])

    def test_the_seeded_image_and_its_twin(self):
        red, blue = (0xC0, 0x39, 0x2B), (0x3A, 0x6E, 0xA5)
        seeded = colours(SUITE / "12-lamp-quay" / "media" / "ivy-turnaround.png")
        twin = colours(SUITE / "02-lantern-quay" / "media" / "ivy-turnaround.png")
        self.assertIn(red, seeded)
        self.assertNotIn(blue, seeded)
        self.assertIn(blue, twin)
        self.assertNotIn(red, twin)
        a, b = SUITE / "02-lantern-quay" / "tree", SUITE / "12-lamp-quay" / "tree"
        self.assertEqual(tree_files(a), tree_files(b))
        differ = [name for name in tree_files(a) if (a / name).read_bytes() != (b / name).read_bytes()]
        self.assertEqual(differ, ["media/manifest.tsv"])
        old, new = ((t / "media" / "manifest.tsv").read_text(encoding="utf-8").splitlines() for t in (a, b))
        self.assertEqual([n for n, (x, y) in enumerate(zip(old, new), 1) if x != y], [3])
        self.assertEqual(len(old), len(new))

    def test_place_media_copies_the_case_media(self):
        case = next(c for c in evals.load(KIT, "look-reviewer") if c.id == "12-lamp-quay")
        run_dir = self.dir / "run"
        cmd_eval.build_run_dir(KIT, case, 1, run_dir)
        cmd_eval.place_media(case, run_dir)
        image = SUITE / "12-lamp-quay" / "media" / "ivy-turnaround.png"
        self.assertEqual((run_dir / "media" / "ivy-turnaround.png").read_bytes(), image.read_bytes())
        self.assertEqual((run_dir / "media" / "manifest.tsv").read_bytes(),
                         (SUITE / "12-lamp-quay" / "tree" / "media" / "manifest.tsv").read_bytes())
        other = evals.load(KIT, "story-editor")[0]
        self.assertFalse((other.path / "media").exists())
        twin = self.dir / "twin"
        cmd_eval.build_run_dir(KIT, other, 1, twin)
        before = tree_files(twin)
        cmd_eval.place_media(other, twin)
        self.assertEqual(tree_files(twin), before)

    def test_fake_run_places_the_images(self):
        bin_dir, root, out = self.dir / "bin", self.dir / "root", self.dir / "out"
        bin_dir.mkdir()
        root.mkdir()
        shutil.copy(FAKE, bin_dir / "fake-harness")
        os.chmod(bin_dir / "fake-harness", 0o755)
        seeded = json.loads((FIX / "look-reviewer" / "12-lamp-quay.json").read_text(encoding="utf-8"))
        steps = [{"match": r"eval 02-lantern-quay(?![\w-])", "result": APPROVED},
                 {"match": r"eval 12-lamp-quay(?![\w-])", "result": seeded}]
        (bin_dir / "fake-harness.json").write_text(json.dumps(steps), encoding="utf-8")
        hashes, place = {}, cmd_eval.place_media

        def spy(case, run_dir):
            place(case, run_dir)
            data = (Path(run_dir) / "media" / "ivy-turnaround.png").read_bytes()
            hashes[case.id] = hashlib.sha256(data).hexdigest()

        env = {"FACTORY_FAKE_HARNESS": str(bin_dir / "fake-harness")}
        with mock.patch.dict(os.environ, env), mock.patch.object(cmd_eval, "place_media", spy), \
                contextlib.redirect_stdout(io.StringIO()) as printed:
            code = cmd_eval.main(["look-reviewer", "--cases", "*-la*-quay", "--harness", "fake",
                                  "--root", str(root), "--out", str(out)])
        self.assertEqual(code, 0)
        self.assertIn("run 1: score 1.0\n", printed.getvalue())
        self.assertEqual(hashes, {name: hashlib.sha256((SUITE / name / "media" / "ivy-turnaround.png")
                                                       .read_bytes()).hexdigest()
                                  for name in ("02-lantern-quay", "12-lamp-quay")})

    def test_a_pair_case_holds_no_media(self):
        self.assertTrue(evals.load(KIT, "pitch-judge"))
        kit = self.dir / "kit"
        shutil.copytree(KIT / "roles", kit / "roles")
        shutil.copytree(KIT / "evals" / "pitch-judge", kit / "evals" / "pitch-judge")
        self.assertEqual(len(evals.load(kit, "pitch-judge")), len(evals.load(KIT, "pitch-judge")))
        case = sorted(p.name for p in (kit / "evals" / "pitch-judge").iterdir() if p.is_dir())[0]
        (kit / "evals" / "pitch-judge" / case / "media").mkdir()
        with self.assertRaises(evals.EvalError) as raised:
            evals.load(kit, "pitch-judge")
        self.assertEqual(str(raised.exception), f"pitch-judge/{case}: a pair case holds no media/")


if __name__ == "__main__":
    unittest.main()
