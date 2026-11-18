"""The frame measurement: `<stage>/render.json` from a directory of frames (brief section 16).

Design: `docs/design/render-job.md`, "The frame measurement" and "Two renders are never byte-equal". It runs
on the machine that runs `factory run`, after `out.tar` is unpacked, with `local.ffmpeg` and `local.ocr`.

`measure(directory, fps, first, last, sampled)` returns `{fps, frames, count, width, height, frames_sha256,
sequence_sha256, black, nan, text, samples}`. The frames are `frame-<nnnnnn>.png` for every frame `first` to
`last` and nothing else of that pattern; `nan` reads `frame-<nnnnnn>.exr` for each of them. `width` and
`height` come from the PNG header. `black` is ffmpeg's `blackdetect` on the sequence, `nan` the frames whose
EXR decodes (32-bit floats) to a NaN or an infinity, `text` what `tesseract` reads holding a word of three
letters or more, `samples` per frame of `sampled` the mean linear colour, its Rec. 709 luminance and the
share of pixels with a channel at 255, each rounded to 6 decimals.

`rerender(pairs, command)` returns `{frames, metric, scores}`: `command` is the split `PERCEPTUAL_METRIC`,
run on each `(frame, first_path, second_path)`; the score is the number on the last line it prints.

Two renders of one job are never byte-equal, so nothing here hashes a frame against another run's frame.
A frame that is missing, extra, of another size or unreadable, or a program that fails, raises `FramesError`.
`run` is `subprocess.run` or a stand-in with its signature.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import struct
import subprocess

FRAME = re.compile(r"^frame-(\d{6})\.png$")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
BLACK = re.compile(r"black_start:\s*(-?\d+(?:\.\d+)?)\s+black_end:\s*(-?\d+(?:\.\d+)?)")
WORD = re.compile(r"[^\W\d_]{3,}")
# The rule of reproduce._score: the last printed line is the score.
NUMBER = re.compile(r"-?\d+(\.\d+)?")
FFMPEG = ["ffmpeg", "-nostdin", "-hide_banner"]
STEP_S = 900


class FramesError(Exception):
    """The frames cannot be measured; the message names the frame or the program."""


def _linear(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


SRGB_TO_LINEAR = tuple(_linear(v) for v in range(256))
# bytes.translate table: 255 becomes 1, everything else 0.
AT_255 = bytes(255) + b"\x01"


def _name(frame: int, ext: str = "png") -> str:
    return f"frame-{frame:06d}.{ext}"


def _call(run, argv: list[str], directory: str, what: str) -> subprocess.CompletedProcess:
    try:
        done = run(argv, cwd=directory, stdin=subprocess.DEVNULL, capture_output=True, timeout=STEP_S)
    except subprocess.TimeoutExpired:
        raise FramesError(f"{argv[0]} did not finish in {STEP_S} s on {what}") from None
    except OSError as error:
        raise FramesError(f"{argv[0]} could not be started on {what}: {error.strerror or error}") from None
    if done.returncode != 0:
        raise FramesError(f"{argv[0]} exited {done.returncode} on {what}")
    return done


def _size(path: str, name: str) -> tuple[int, int]:
    try:
        with open(path, "rb") as handle:
            head = handle.read(24)
    except OSError:
        raise FramesError(f"{name} cannot be read") from None
    if len(head) < 24 or head[:8] != PNG_SIGNATURE or head[12:16] != b"IHDR":
        raise FramesError(f"{name} is not a PNG")
    return struct.unpack(">II", head[16:24])


def _frames(directory: str, first: int, last: int) -> tuple[int, int]:
    """The (width, height) every frame shares; `FramesError` on a missing, extra or differently sized frame."""
    try:
        names = os.listdir(directory)
    except OSError:
        raise FramesError(f"{directory} cannot be listed") from None
    found = {int(m.group(1)): name for name in names if (m := FRAME.match(name))}
    expected = set(range(first, last + 1))
    missing = sorted(expected - set(found))
    if missing:
        raise FramesError("missing " + ", ".join(_name(f) for f in missing))
    extra = sorted(set(found) - expected)
    if extra:
        raise FramesError(f"outside frames {first} to {last}: " + ", ".join(found[f] for f in extra))
    size = None
    for frame in range(first, last + 1):
        name = _name(frame)
        this = _size(os.path.join(directory, name), name)
        if size is None:
            size = this
        elif this != size:
            raise FramesError(f"{name} is {this[0]}x{this[1]}, {_name(first)} is {size[0]}x{size[1]}")
    return size


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _black(directory: str, fps: int, first: int, last: int, run) -> list[int]:
    argv = FFMPEG + ["-framerate", str(fps), "-start_number", str(first), "-i", "frame-%06d.png",
                     "-vf", "blackdetect=d=0:pix_th=0.10", "-an", "-f", "null", "-"]
    done = _call(run, argv, directory, "the sequence")
    printed = (done.stderr or b"").decode("utf-8", "replace") + (done.stdout or b"").decode("utf-8", "replace")
    black = set()
    for start, end in BLACK.findall(printed):
        black.update(range(first + round(float(start) * fps), first + round(float(end) * fps)))
    return sorted(f for f in black if first <= f <= last)


def _nan(directory: str, first: int, last: int, run) -> list[int]:
    out = []
    for frame in range(first, last + 1):
        name = _name(frame, "exr")
        try:
            done = _call(run, FFMPEG + ["-i", name, "-f", "rawvideo", "-pix_fmt", "gbrpf32le", "-"], directory, name)
        except FramesError:
            if not os.path.isfile(os.path.join(directory, name)):
                raise FramesError(f"missing {name}") from None
            raise
        data = done.stdout or b""
        if not data or len(data) % 4:
            raise FramesError(f"{name} decoded to {len(data)} bytes, not 32-bit floats")
        if not all(map(math.isfinite, struct.unpack(f"<{len(data) // 4}f", data))):
            out.append(frame)
    return out


def _text(directory: str, first: int, last: int, run) -> list[dict]:
    out = []
    for frame in range(first, last + 1):
        name = _name(frame)
        done = _call(run, ["tesseract", name, "stdout"], directory, name)
        text = (done.stdout or b"").decode("utf-8", "replace").strip()
        if WORD.search(text):
            out.append({"frame": frame, "text": text})
    return out


def _sample(directory: str, frame: int, width: int, height: int, run) -> dict:
    name = _name(frame)
    done = _call(run, FFMPEG + ["-i", name, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], directory, name)
    data = done.stdout or b""
    pixels = width * height
    if len(data) != pixels * 3 or not pixels:
        raise FramesError(f"{name} decoded to {len(data)} bytes, not {width}x{height} rgb24")
    channels = [data[c::3] for c in range(3)]
    color = [sum(SRGB_TO_LINEAR[v] * channel.count(v) for v in range(256)) / pixels for channel in channels]
    luma = 0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]
    # Each channel as one byte per pixel, 1 where it is 255; a pixel is clipped when any of its three is 1.
    marks = [int.from_bytes(channel.translate(AT_255), "big") for channel in channels]
    clipped = bin(marks[0] | marks[1] | marks[2]).count("1") / pixels
    return {"frame": frame, "luma": round(luma, 6), "clipped": round(clipped, 6),
            "color": [round(c, 6) for c in color]}


def _int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def measure(directory: str, fps: int, first: int, last: int, sampled, run=subprocess.run) -> dict:
    """`render.json` without `rerender` for the frames `first` to `last` in `directory`."""
    if not (_int(fps) and fps > 0):
        raise FramesError(f"fps {fps!r} is not a positive integer")
    if not (_int(first) and _int(last) and first >= 0 and last >= first):
        raise FramesError(f"frames {first!r} to {last!r} are not a range of frame numbers")
    sampled = list(sampled)
    outside = [f for f in sampled if not (_int(f) and first <= f <= last)]
    if outside:
        raise FramesError(f"sampled frames outside {first} to {last}: " + ", ".join(repr(f) for f in outside))
    directory = os.fspath(directory)
    width, height = _frames(directory, first, last)
    hashes = [_sha256(os.path.join(directory, _name(f))) for f in range(first, last + 1)]
    return {
        "fps": fps,
        "frames": {"first": first, "last": last},
        "count": last - first + 1,
        "width": width,
        "height": height,
        "frames_sha256": hashes,
        "sequence_sha256": hashlib.sha256("\n".join(hashes).encode()).hexdigest(),
        "black": _black(directory, fps, first, last, run),
        "nan": _nan(directory, first, last, run),
        "text": _text(directory, first, last, run),
        "samples": [_sample(directory, f, width, height, run) for f in sampled],
    }


def rerender(pairs, command: list[str], run=subprocess.run) -> dict:
    """`{frames, metric, scores}`: `command` scores each `(frame, first_path, second_path)`."""
    command = list(command)
    if not command:
        raise FramesError("the metric command is empty")
    pairs = list(pairs)
    if not pairs:
        raise FramesError("no frame was rendered again")
    frames, scores = [], []
    for frame, first_path, second_path in pairs:
        what = f"frame {frame}"
        try:
            done = run([*command, os.path.abspath(first_path), os.path.abspath(second_path)],
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=STEP_S)
        except subprocess.TimeoutExpired:
            raise FramesError(f"the metric did not finish in {STEP_S} s on {what}") from None
        except OSError:
            raise FramesError(f"the metric exited 127 on {what}") from None
        if done.returncode != 0:
            raise FramesError(f"the metric exited {done.returncode} on {what}")
        printed = [line.strip() for line in (done.stdout or b"").decode("utf-8", "replace").splitlines()
                   if line.strip()]
        if not printed or not NUMBER.fullmatch(printed[-1]):
            raise FramesError(f"the metric printed no number on its last line on {what}")
        frames.append(frame)
        scores.append(float(printed[-1]))
    return {"frames": frames, "metric": os.path.basename(command[0]), "scores": scores}
