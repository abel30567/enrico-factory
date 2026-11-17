"""The conform job: cut the stored frames by the shots and mix the stored audio on `local.ffmpeg` (brief
sections 10 and 16).

`make(call)` is the tool `conform` (`kit/lib/tools/conform.py`). Its first input is the mix request
`production/<ep>/conform/requests/mix[-<id>].json`, whose text is not read. It reads, by exact path,
`production/<ep>/conform/mix-plan.json`, `media/manifest.tsv`, `episodes/<ep>/reel/edit-list.json` (its `fps`),
`production/<ep>/shots.json` and `production/<ep>/<shot>/render/manifest.json` for each of its rows; it ignores
any other input. Every check is made before any program or store call.

The plan is ``{lines, music, effects}``: each `lines` entry ``{line, media, start, end, gain}``, each `music`
or `effects` entry ``{media, start, end, gain}``, frames from the episode's frame 0 and gains in dB; `media` is
an id of `media/manifest.tsv`. The frames and sounds come from the asset store into a temporary directory;
ffmpeg makes the stems `dialogue.wav`, `music.wav` and `effects.wav` (48 kHz stereo, one sample count from frame
0 to the last frame), the master `master.mp4` from the frames and the three stems, and measures the master
with `ebur128`. Nothing normalises loudness: the plan's gains decide it and the lint judges it.

The outputs, written to ``call["out"]``, are the stems, the master, `mix.json` (``{loudness_lufs,
true_peak_dbtp, lines, stems, master}``) and `manifest.json`; `factory run` stores the media and commits the
two stage files. Nothing here writes to the store. `RUN` is `subprocess.run` or a stand-in with its signature.
A reason never holds a value.
"""

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile

from kit.lib import assets, media
from kit.lib.tools import Down, Failed

RUN = subprocess.run
STEP_S = 900
RATE = 48000

NOT_A_REQUEST = "the first input is not a mix request"
MISSING = "an input the mix needs is missing"
NO_FPS = "the edit list has no fps that is a whole number above zero"
NOT_A_RUN = "shots.json is not one run of shots from frame 0"
NOT_A_PLAN = "mix-plan.json is not {lines, music, effects} with frames and gains"
LINE_OUTSIDE = "a line of mix-plan.json lies outside the shots"
CUE_AFTER = "a music cue or effect of mix-plan.json ends after the last frame"
NOT_IN_MANIFEST = "a media id of mix-plan.json is not in the manifest"
FRAME_MISSING = "a frame of the picture is not in its shot's render manifest"
NOT_IN_STORE = "a frame or a sound is not in the asset store"
FFMPEG_FAILED = "ffmpeg failed"
NO_MEASURE = "ebur128 gave no integrated loudness or true peak"
REASONS = (NOT_A_REQUEST, MISSING, NO_FPS, NOT_A_RUN, NOT_A_PLAN, LINE_OUTSIDE, CUE_AFTER, NOT_IN_MANIFEST,
           FRAME_MISSING, NOT_IN_STORE, FFMPEG_FAILED, NO_MEASURE)

REQUEST = re.compile(r"production/([A-Za-z0-9][A-Za-z0-9._-]*)/conform/requests/mix(-[a-z0-9][a-z0-9-]*)?\.json")
MANIFEST = "media/manifest.tsv"
_SHA256 = re.compile(r"[0-9a-f]{64}")
FFMPEG = ["ffmpeg", "-nostdin", "-hide_banner"]
STEMS = (("lines", "dialogue"), ("music", "music"), ("effects", "effects"))
LINE_KEYS = {"line", "media", "start", "end", "gain"}
CUE_KEYS = {"media", "start", "end", "gain"}
LOUDNESS = re.compile(r"^\s*I:\s+(-?[0-9]+(?:\.[0-9]+)?) LUFS\s*$", re.MULTILINE)
PEAK = re.compile(r"^\s*Peak:\s+(-?[0-9]+(?:\.[0-9]+)?) dBFS\s*$", re.MULTILINE)


def _before(reason):
    """A failure before anything was spent."""
    return Failed(reason, usd=0, compute_min=0)


def _frame(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _json(text):
    try:
        return json.loads(text)
    except ValueError:
        return None


def _name(frame):
    return f"frame-{frame:06d}.png"


def _fps(text):
    data = _json(text)
    fps = data.get("fps") if isinstance(data, dict) else None
    if not _frame(fps) or fps <= 0:
        raise _before(NO_FPS)
    return fps


def _shots(text):
    """The rows ``(shot, first, last)``, one run of frames from 0."""
    data = _json(text)
    if not isinstance(data, list) or not data:
        raise _before(NOT_A_RUN)
    rows, following = [], 0
    for row in data:
        if not (isinstance(row, dict) and isinstance(row.get("shot"), str) and row["shot"]
                and _frame(row.get("first")) and _frame(row.get("last"))
                and row["first"] == following and row["first"] <= row["last"]):
            raise _before(NOT_A_RUN)
        rows.append((row["shot"], row["first"], row["last"]))
        following = row["last"] + 1
    return rows


def _entry(entry, keys):
    return (isinstance(entry, dict) and set(entry) == keys
            and all(isinstance(entry[k], str) and entry[k] for k in keys & {"line", "media"})
            and _frame(entry["start"]) and _frame(entry["end"]) and 0 <= entry["start"] <= entry["end"]
            and isinstance(entry["gain"], (int, float)) and not isinstance(entry["gain"], bool)
            and math.isfinite(entry["gain"]))


def _plan(text):
    data = _json(text)
    if not (isinstance(data, dict) and set(data) == {"lines", "music", "effects"}
            and all(isinstance(data[k], list) for k in data)
            and all(_entry(e, LINE_KEYS) for e in data["lines"])
            and all(_entry(e, CUE_KEYS) for e in data["music"] + data["effects"])):
        raise _before(NOT_A_PLAN)
    lines = [e["line"] for e in data["lines"]]
    if len(set(lines)) != len(lines):
        raise _before(NOT_A_PLAN)
    return data


def _media(text):
    """``{id: sha256}`` of the manifest's rows after the header; the first row of an id wins."""
    found = {}
    for row in text.split("\n")[1:]:
        fields = row.rstrip("\r").split("\t")
        if len(fields) >= 2 and _SHA256.fullmatch(fields[1]):
            found.setdefault(fields[0], fields[1])
    return found


def _frames(text, first, last):
    """``[sha256]`` of the frames ``first`` to ``last`` in a render manifest."""
    data = _json(text)
    outputs = data.get("outputs") if isinstance(data, dict) else None
    held = {}
    for output in outputs if isinstance(outputs, list) else ():
        if (isinstance(output, dict) and isinstance(output.get("name"), str)
                and isinstance(output.get("sha256"), str) and _SHA256.fullmatch(output["sha256"])):
            held.setdefault(output["name"], output["sha256"])
    if any(_name(f) not in held for f in range(first, last + 1)):
        raise _before(FRAME_MISSING)
    return [held[_name(f)] for f in range(first, last + 1)]


def _check(call):
    """``(fps, last frame, plan, frame sha256s, {media: sha256})``; ``Failed`` on the first defect."""
    inputs = call["inputs"]
    match = REQUEST.fullmatch(inputs[0]["path"]) if inputs else None
    if match is None:
        raise _before(NOT_A_REQUEST)
    ep = match.group(1)
    texts = {}
    for item in inputs:
        texts.setdefault(item["path"], item["text"])
    plan_path, edit_list = f"production/{ep}/conform/mix-plan.json", f"episodes/{ep}/reel/edit-list.json"
    shots_path = f"production/{ep}/shots.json"
    if any(texts.get(path) is None for path in (plan_path, MANIFEST, edit_list, shots_path)):
        raise _before(MISSING)
    fps = _fps(texts[edit_list])
    rows = _shots(texts[shots_path])
    plan = _plan(texts[plan_path])
    last = rows[-1][2]
    if any(e["end"] > last for e in plan["lines"]):
        raise _before(LINE_OUTSIDE)
    if any(e["end"] > last for e in plan["music"] + plan["effects"]):
        raise _before(CUE_AFTER)
    stored = _media(texts[MANIFEST])
    if any(e["media"] not in stored for kind, _ in STEMS for e in plan[kind]):
        raise _before(NOT_IN_MANIFEST)
    frames = []
    for shot, first, end in rows:
        text = texts.get(f"production/{ep}/{shot}/render/manifest.json")
        if text is None:
            raise _before(MISSING)
        frames += _frames(text, first, end)
    return fps, last, plan, frames, stored


def _program(argv, work):
    """The finished stem, master or measure call; ``Failed`` when it did not finish with exit 0."""
    try:
        done = RUN(argv, cwd=work, stdin=subprocess.DEVNULL, capture_output=True, timeout=STEP_S)
    except (OSError, subprocess.SubprocessError):
        raise _before(FFMPEG_FAILED) from None
    if done.returncode != 0:
        raise _before(FFMPEG_FAILED)
    return done


def _engine(work):
    """The first line of ``ffmpeg -version``; ``Down`` when ffmpeg cannot be run."""
    try:
        done = RUN(["ffmpeg", "-version"], cwd=work, stdin=subprocess.DEVNULL, capture_output=True,
                   timeout=STEP_S)
    except (OSError, subprocess.SubprocessError):
        raise Down() from None
    if done.returncode != 0:
        raise Down()
    lines = _text(done.stdout).splitlines()
    engine = lines[0].strip() if lines else ""
    if not engine:
        raise Down()
    return engine


def _text(data):
    if isinstance(data, bytes):
        return data.decode("utf-8", "replace")
    return data or ""


def _fetch(call, sha256, path):
    try:
        assets.get(call["settings"], call["secrets"]["store.assets"], sha256, path)
    except media.Refused:
        raise Down() from None
    except media.Failed as failure:
        if str(failure) == assets.NO_SUCH_OBJECT:
            raise _before(NOT_IN_STORE) from None
        raise Down() from None


def _stem(entries, stored, fps, last, path):
    def s(frame):
        return frame * RATE // fps

    argv = [*FFMPEG, "-f", "lavfi", "-i", f"anullsrc=r={RATE}:cl=stereo"]
    graph = []
    for n, e in enumerate(entries, 1):
        argv += ["-i", f"audio/{stored[e['media']]}"]
        graph.append(f"[{n}:a]aresample={RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,"
                     f"volume={e['gain']:g}dB,atrim=end_sample={s(e['end'] + 1) - s(e['start'])},"
                     f"adelay=delays={s(e['start'])}S:all=1[a{n}]")
    labels = "".join(f"[a{n}]" for n in range(1, len(entries) + 1))
    graph.append(f"[0:a]{labels}amix=inputs={len(entries) + 1}:duration=first:normalize=0,"
                 f"atrim=end_sample={s(last + 1)}[out]")
    return argv + ["-filter_complex", ";".join(graph), "-map", "[out]", "-c:a", "pcm_s24le", path]


def _measure(done):
    text = _text(done.stderr) + _text(done.stdout)
    at = text.rfind("Summary:")
    if at < 0:
        raise _before(NO_MEASURE)
    summary = text[at:]
    loudness, peak = LOUDNESS.findall(summary), PEAK.findall(summary)
    if not loudness or not peak:
        raise _before(NO_MEASURE)
    return float(loudness[-1]), float(peak[-1])


def _digest(path):
    hasher, size = hashlib.sha256(), 0
    with open(path, "rb") as handle:
        while piece := handle.read(1 << 20):
            hasher.update(piece)
            size += len(piece)
    return hasher.hexdigest(), size


def _write(path, data):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, indent=2, sort_keys=True) + "\n")


def make(call):
    """Make the stems, the master, `mix.json` and `manifest.json` in ``call["out"]``; nothing is spent."""
    fps, last, plan, frames, stored = _check(call)
    out = call["out"]
    work = tempfile.mkdtemp(prefix="factory-conform-")
    try:
        engine = _engine(work)
        os.mkdir(os.path.join(work, "frames"))
        os.mkdir(os.path.join(work, "audio"))
        for frame, sha256 in enumerate(frames):
            _fetch(call, sha256, os.path.join(work, "frames", _name(frame)))
        for sha256 in sorted({stored[e["media"]] for kind, _ in STEMS for e in plan[kind]}):
            _fetch(call, sha256, os.path.join(work, "audio", sha256))
        for kind, stem in STEMS:
            _program(_stem(plan[kind], stored, fps, last, f"{out}/{stem}.wav"), work)
        _program([*FFMPEG, "-framerate", str(fps), "-start_number", "0", "-i", "frames/frame-%06d.png",
                  "-i", f"{out}/dialogue.wav", "-i", f"{out}/music.wav", "-i", f"{out}/effects.wav",
                  "-filter_complex", "[1:a][2:a][3:a]amix=inputs=3:duration=first:normalize=0[a]",
                  "-map", "0:v", "-map", "[a]", "-frames:v", str(last + 1), "-c:v", "libx264",
                  "-pix_fmt", "yuv420p", "-crf", "18", "-c:a", "aac", "-b:a", "320k", f"{out}/master.mp4"], work)
        loudness, peak = _measure(_program([*FFMPEG, "-i", f"{out}/master.mp4", "-map", "0:a",
                                            "-af", "ebur128=peak=true", "-f", "null", "-"], work))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    _write(os.path.join(out, "mix.json"), {
        "loudness_lufs": loudness,
        "true_peak_dbtp": peak,
        "lines": [{"line": e["line"], "start": e["start"], "end": e["end"]} for e in plan["lines"]],
        "stems": [{"name": stem, "sha256": _digest(os.path.join(out, f"{stem}.wav"))[0]} for _, stem in STEMS],
        "master": _digest(os.path.join(out, "master.mp4"))[0],
    })
    outputs = []
    for name in sorted(os.listdir(out)):
        sha256, size = _digest(os.path.join(out, name))
        outputs.append({"name": name, "sha256": sha256, "bytes": size})
    _write(os.path.join(out, "manifest.json"), {
        "job": call["hash"],
        "engine": engine,
        "flags": [],
        "inputs": [{"path": item["path"], "sha256": item["sha256"]} for item in call["inputs"]],
        "seeds": {},
        "outputs": outputs,
    })
    return {"usd": 0, "compute_min": 0}
