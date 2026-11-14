#!/usr/bin/env python3
"""A fake of yt-dlp, ffprobe, ffmpeg, tesseract or whisper-cli, by the name it is installed under.

tests/test_analysis.py copies it into the world's `bin/` with its first line replaced. It reads
`control.json` beside it, never an environment variable (the fetch gets none of the test's):
`log` (a file, one JSON line `{tool, step, argv, env, cwd, pid}` per call, written before it acts),
`answers` (a directory), `video` (a file the fetch copies to `copy.<ext>`, or null to write nothing),
`drop` (filter names `ffmpeg -filters` leaves out), `fail` (a step that exits 1) and `hang` (a step that
never ends). A step is `version`, `filters`, `yt-dlp`, `ffprobe`, `audio`, `cuts`, `frames`,
`whisper-cli` or `tesseract`.
"""

import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.basename(__file__)
FILTERS = ("ebur128", "loudnorm", "ssim", "libvmaf", "blackdetect", "freezedetect", "silencedetect", "subtitles")
FRAMES = 5


def after(argv, flag):
    return argv[argv.index(flag) + 1]


def step_of(argv):
    if argv[:1] in (["-version"], ["--version"]):
        return "version"
    if TOOL == "ffmpeg":
        if "-filters" in argv:
            return "filters"
        if "0:a:0" in argv:
            return "audio"
        if any("showinfo" in a for a in argv):
            return "cuts"
        return "frames"
    return TOOL


def main(argv):
    with open(os.path.join(HERE, "control.json"), encoding="utf-8") as handle:
        control = json.load(handle)
    step = step_of(argv)
    with open(control["log"], "a", encoding="utf-8") as log:
        log.write(json.dumps({"tool": TOOL, "step": step, "argv": argv, "env": dict(os.environ),
                              "cwd": os.getcwd(), "pid": os.getpid()}) + "\n")
    if control.get("hang") == step:
        while True:
            time.sleep(1)
    if control.get("fail") == step:
        return 1
    answers = control.get("answers", "")
    if step == "version":
        print(f"{TOOL} version 0.0-fake")
    elif step == "filters":
        for name in FILTERS:
            if name not in control.get("drop", []):
                print(f" ... {name}            V->V       a fake filter")
    elif step == "yt-dlp":
        video = control.get("video")
        if video:
            ext = os.path.splitext(video)[1] or ".mp4"
            shutil.copyfile(video, os.path.join(after(argv, "--paths"), "copy" + ext))
    elif step == "ffprobe":
        with open(os.path.join(answers, "ffprobe.json"), encoding="utf-8") as handle:
            sys.stdout.write(handle.read())
    elif step == "audio":
        with open(argv[-1], "wb") as handle:
            handle.write(b"RIFF fake audio")
    elif step == "cuts":
        with open(os.path.join(answers, "showinfo.txt"), encoding="utf-8") as handle:
            sys.stderr.write(handle.read())
    elif step == "frames":
        pattern = argv[-1]
        for index in range(1, FRAMES + 1):
            with open(pattern % index, "wb") as handle:
                handle.write(b"fake frame")
    elif step == "whisper-cli":
        shutil.copyfile(os.path.join(answers, "whisper.json"), after(argv, "-of") + ".json")
    elif step == "tesseract":
        with open(os.path.join(answers, "ocr.json"), encoding="utf-8") as handle:
            texts = json.load(handle)
        sys.stdout.write(texts.get(os.path.basename(argv[0]), ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
