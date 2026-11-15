#!/usr/bin/env python3
"""A fake of ffmpeg, tesseract or a perceptual metric, by the name it is installed under.

tests/test_frames.py copies it into a `bin/` directory with its first line replaced. It reads `control.json`
beside it: `log` (a file, one JSON line `{tool, step, argv, cwd}` per call, written before it acts), `fail`
(a step that exits 1), `black` (what the `black` step prints on stderr), `exr` (EXR name to the list of
`"nan"` or `"inf"` its decoding holds; any other EXR decodes to finite values), `text` (frame name to what
tesseract prints) and `metric` (`{stdout, exit}`). A step is `black` (blackdetect), `exr` (the decoding to
`gbrpf32le`), `rgb24` (the decoding of a PNG, which this fake reads itself: 8-bit RGB, filter type 0),
`tesseract` or `metric`. The EXR files the test writes are placeholders; a missing one fails to decode.
"""

import json
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.basename(__file__)
SPECIAL = {"nan": float("nan"), "inf": float("inf")}


def after(argv, flag):
    return argv[argv.index(flag) + 1]


def step_of(argv):
    if TOOL == "ffmpeg":
        if any("blackdetect" in a for a in argv):
            return "black"
        if "gbrpf32le" in argv:
            return "exr"
        if "rgb24" in argv:
            return "rgb24"
        return "unknown"
    if TOOL == "tesseract":
        return "tesseract"
    return "metric"


def rgb24(path):
    """The pixels of an 8-bit RGB PNG whose rows all use filter type 0."""
    with open(path, "rb") as handle:
        data = handle.read()
    pos, idat, width, height = 8, b"", 0, 0
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            width, height = struct.unpack(">II", body[:8])
        elif kind == b"IDAT":
            idat += body
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width * 3 + 1
    rows = [raw[y * stride:(y + 1) * stride] for y in range(height)]
    if any(row[0] != 0 for row in rows):
        raise SystemExit("fake ffmpeg: only filter type 0")
    return b"".join(row[1:] for row in rows)


def main(argv):
    with open(os.path.join(HERE, "control.json"), encoding="utf-8") as handle:
        control = json.load(handle)
    step = step_of(argv)
    with open(control["log"], "a", encoding="utf-8") as log:
        log.write(json.dumps({"tool": TOOL, "step": step, "argv": argv, "cwd": os.getcwd()}) + "\n")
    if control.get("fail") == step or step == "unknown":
        return 1
    if step == "black":
        sys.stderr.write(control.get("black", ""))
    elif step == "exr":
        name = after(argv, "-i")
        if not os.path.isfile(name):
            sys.stderr.write(f"{name}: No such file or directory\n")
            return 1
        values = [SPECIAL[v] for v in control.get("exr", {}).get(name, [])]
        values += [0.5] * (48 - len(values))
        sys.stdout.buffer.write(struct.pack(f"<{len(values)}f", *values))
    elif step == "rgb24":
        sys.stdout.buffer.write(rgb24(after(argv, "-i")))
    elif step == "tesseract":
        sys.stdout.write(control.get("text", {}).get(argv[0], ""))
    elif step == "metric":
        metric = control.get("metric", {})
        sys.stdout.write(metric.get("stdout", ""))
        return metric.get("exit", 0)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
