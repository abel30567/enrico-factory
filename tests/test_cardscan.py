"""Tests for kit/lib/cardscan.py: the pictures are made here, as `rgb24` bytes of 160 by 160 pixels or
PNGs written with `zlib` and `struct`; no picture is a file of the repository."""

import random
import shutil
import struct
import subprocess
import sys
import unittest
import zlib
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import cardscan  # noqa: E402

N = cardscan.SIZE
FRAME, ART, SKY, PANEL, INK = (176, 140, 52), (70, 110, 160), (150, 190, 235), (232, 222, 196), (30, 26, 22)


class Picture:
    """``N`` by ``N`` pixels of one colour, painted with rectangles."""

    def __init__(self, colour):
        self.px = [[colour] * N for _ in range(N)]

    def fill(self, x0, y0, x1, y1, colour):
        for y in range(y0, y1):
            self.px[y][x0:x1] = [colour] * (x1 - x0)
        return self

    def rgb(self):
        return bytes(c for row in self.px for p in row for c in p)

    def png(self):
        raw = b"".join(b"\x00" + bytes(c for p in row for c in p) for row in self.px)

        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", N, N, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def marks(picture, x0, x1, lines, colour=INK):
    """Lines of dashes, as text: 5 pixels wide, 4 apart, 4 high, from ``x0`` to ``x1``."""
    for y in lines:
        for x in range(x0, x1 - 5, 9):
            picture.fill(x, y, x + 5, y + 4, colour)
    return picture


def card(top_text=False):
    """A frame band, an art window with a figure in it, a text panel of dark marks."""
    p = Picture(FRAME).fill(14, 14, 146, 90, ART).fill(66, 30, 94, 90, (200, 90, 60))
    p.fill(14, 100, 146, 148, PANEL)
    marks(p, 20, 140, (106, 116, 126, 136))
    if top_text:
        marks(p, 20, 140, (5,))
    return p


def figure():
    return Picture((255, 255, 255)).fill(60, 50, 100, 140, (40, 60, 120)).fill(70, 22, 90, 48, (220, 180, 150))


def sea():
    p = Picture(SKY).fill(0, 70, N, N, (30, 80, 140))
    return p.fill(56, 58, 104, 84, (90, 50, 20)).fill(78, 20, 82, 58, (240, 240, 230))


def window():
    p = Picture((200, 190, 170)).fill(30, 30, 130, 100, SKY)
    return p.fill(78, 30, 82, 100, (120, 90, 60)).fill(30, 63, 130, 67, (120, 90, 60))


def noise():
    return bytes(random.Random(1).randrange(256) for _ in range(N * N * 3))


class ArtRegion(unittest.TestCase):
    def test_card_is_framed(self):
        for name, picture in (("card", card()), ("card with text in its top band", card(top_text=True))):
            with self.subTest(name):
                self.assertIsNotNone(cardscan.art_region(picture.rgb()))

    def test_pictures_alone_are_not(self):
        for name, rgb in (("figure on white", figure().rgb()), ("sea with a hull", sea().rgb()),
                          ("window on a plain wall", window().rgb()), ("noise", noise()),
                          ("all black", bytes(N * N * 3))):
            with self.subTest(name):
                self.assertIsNone(cardscan.art_region(rgb))

    def test_deterministic(self):
        rgb = card(top_text=True).rgb()
        self.assertEqual(cardscan.art_region(rgb), cardscan.art_region(rgb))
        self.assertIsNone(cardscan.art_region(noise()))


class Decode(unittest.TestCase):
    def test_stand_in(self):
        pixels = card().rgb()
        self.assertEqual(len(pixels), 76800)
        run = mock.Mock(return_value=subprocess.CompletedProcess(cardscan.DECODE, 0, pixels, b""))
        self.assertEqual(cardscan.decode(b"picture", run), pixels)
        run.assert_called_once_with(cardscan.DECODE, input=b"picture", capture_output=True, timeout=60)

    def test_not_decoded(self):
        cases = {
            "no ffmpeg": mock.Mock(side_effect=FileNotFoundError("ffmpeg")),
            "too slow": mock.Mock(side_effect=subprocess.TimeoutExpired(cardscan.DECODE, 60)),
            "exit 1": mock.Mock(return_value=subprocess.CompletedProcess(cardscan.DECODE, 1, bytes(76800), b"")),
            "76,799 bytes": mock.Mock(return_value=subprocess.CompletedProcess(cardscan.DECODE, 0, bytes(76799), b"")),
        }
        for name, run in cases.items():
            with self.subTest(name):
                self.assertIsNone(cardscan.decode(b"picture", run))


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class RealFfmpeg(unittest.TestCase):
    def test_png(self):
        found = {}
        for name, picture in (("card", card()), ("figure", figure())):
            pixels = cardscan.decode(picture.png(), subprocess.run)
            self.assertEqual(len(pixels or b""), 76800, name)
            found[name] = cardscan.art_region(pixels)
        self.assertIsNotNone(found["card"])
        self.assertIsNone(found["figure"])


if __name__ == "__main__":
    unittest.main()
