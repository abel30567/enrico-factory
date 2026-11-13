"""Draw the turnaround of each manifest row of each case into <case>/media/<id>.png.

Run as `python3 evals/look-reviewer/draw.py`; nothing imports it. Each image is drawn from the case's
model sheet (part table and height), style lock (palette) and canon ledger (a heron gets a beak).
"""

import re
import struct
import zlib
from pathlib import Path

SUITE = Path(__file__).resolve().parent
WIDTH, HEIGHT, GROUND, CENTRES, WHITE = 480, 400, 390, ((80, 1.0), (240, 0.5), (400, 1.0)), (255, 255, 255)
LEGS, ARMS = ((-0.09, -0.02), (0.02, 0.09)), ((-0.16, -0.11), (0.11, 0.16))
BODY = [(x0, x1, 0.55, 1.0) for x0, x1 in LEGS] + [(x0, x1, 0.17, 0.5) for x0, x1 in ARMS] \
    + [(-0.07, 0.07, 0, 0.14)]
LONG = (-0.11, 0.11, 0.17, 0.55)
PARTS = [("dress", [(-0.11, 0.11, 0.17, 0.75)]), ("oilskin, to the ankle", [(-0.11, 0.11, 0.17, 0.95)])] \
    + [(name, [LONG]) for name in ("coat", "apron", "smock", "tunic", "jacket", "oilskin")] \
    + [("shawl", [(-0.16, 0.16, 0.17, 0.27)]), ("sash", [(-0.11, 0.11, 0.5, 0.55)]),
       ("scarf", [(-0.06, 0.06, 0.14, 0.17)]), ("hat", [(-0.1, 0.1, 0, 0.05)]),
       ("cap", [(-0.07, 0.07, 0, 0.04)]), ("boots", [(x0, x1, 0.9, 1.0) for x0, x1 in LEGS])] \
    + [(name, [(x0, x1, 0.45, 0.5) for x0, x1 in ARMS]) for name in ("gloves", "mittens")] \
    + [(name, [(0.16, 0.24, 0.42, 0.52)]) for name in ("satchel", "bag")] \
    + [("lamp", [(0.16, 0.22, 0.5, 0.58)])]
BEAK = (0.07, 0.2, 0.06, 0.08)
OVERRIDES = {("12-lamp-quay", "ivy-turnaround"): {"coat": "#C0392B"}}


def rgb(value):
    return tuple(bytes.fromhex(value[1:]))


def colour(value, palette):
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        return rgb(value)
    return rgb(palette[value]) if value in palette else WHITE


def shapes(case, ident):
    tree = SUITE / case / "tree"
    name = ident[:-len("-turnaround")]
    sheet = (tree / "bible" / "style" / "model-sheets" / f"{name}.md").read_text(encoding="utf-8")
    lock = (tree / "bible" / "style" / "lock.md").read_text(encoding="utf-8")
    ledger = (tree / "bible" / "canon" / "ledger.tsv").read_text(encoding="utf-8").splitlines()
    palette = dict(re.findall(r"(?m)^\| (\w+) \| (#[0-9A-Fa-f]{6}) \|", lock))
    table = {**dict(re.findall(r"(?m)^\| ([a-z][a-z, ]*) \| (\S+) \| ", sheet)), **OVERRIDES.get((case, ident), {})}
    body = BODY + ([BEAK] if f"{name}\tspecies\theron" in "\n".join(ledger) else [])
    drawn = [(box, WHITE) for box in body]
    drawn += [(box, colour(table[part], palette)) for part, boxes in PARTS if part in table for box in boxes]
    return int(re.search(r"(?m)^Height: (\d+) cm$", sheet)[1]), rgb(palette["N1"]), drawn


def draw(case, ident):
    cm, line, drawn = shapes(case, ident)
    h = 2 * cm
    pixels = [[WHITE] * WIDTH for _ in range(HEIGHT)]
    for centre, squeeze in CENTRES:
        for (x0, x1, y0, y1), fill in drawn:
            left, right = centre + int(x0 * h * squeeze), centre + int(x1 * h * squeeze)
            top, bottom = GROUND - h + int(y0 * h), GROUND - h + int(y1 * h)
            for y in range(top, bottom):
                for x in range(left, right):
                    edge = x < left + 2 or x >= right - 2 or y < top + 2 or y >= bottom - 2
                    pixels[y][x] = line if edge else fill
    raw = b"".join(b"\x00" + bytes(v for p in row for v in p) for row in pixels)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0)) \
        + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


for path in sorted(SUITE.glob("*/tree/media/manifest.tsv")):
    case = path.parts[-4]
    for row in path.read_text(encoding="utf-8").splitlines()[1:]:
        ident = row.split("\t")[0]
        (SUITE / case / "media").mkdir(exist_ok=True)
        (SUITE / case / "media" / f"{ident}.png").write_bytes(draw(case, ident))
