"""factory run --analyse <platform> <id> <link> [--root DIR] [--apply]: notes on one video from a temporary copy.

Decision D5, option (b): the copy is fetched anonymously, on a machine that holds no posting session, and
never with the YouTube project's data. In this order: the platform, the id and the link; the register; the
machine (`SECRETS_DIR` empty, missing or an empty directory); `STATE_DIR`; the temporary directory, outside
the production and `STATE_DIR`; the stored notes of the platform 30 days old or more are deleted; then the
resources `local.ffmpeg`, `local.ocr` and `local.transcribe`, the programs `yt-dlp` and `TRANSCRIBE_CMD`,
and notes of the same second. With `--apply` the guard (`kit/lib/copyguard.py`) makes the temporary
directory; `yt-dlp` fetches the copy into it with no cookies, config, cache or login and an environment of
`HOME`, `LANG` and `PATH` only; `ffprobe`, `ffmpeg`, `TRANSCRIBE_CMD` (a whisper.cpp `whisper-cli`) and
`tesseract` derive the notes; the copy, its audio and its frames are deleted; then the notes are written to
`<STATE_DIR>/analyses/<platform>/<id>/<stamp>.json`, `<stamp>` the `read_at` without `-` and `:`. They hold
the video's words, so they live in the state directory only and never enter git. No model is run.

Without `--apply` nothing is deleted, fetched or written: each line says what would be done. Exit 0
analysed or would analyse, 1 a step failed or the copy could not be deleted, 2 refused. Nothing a tool
prints is repeated anywhere.
"""

import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from kit.lib import production, reads, resources, trends

GUARD = Path(__file__).resolve().parent / "copyguard.py"
HOSTS = {"youtube": ("m.youtube.com", "www.youtube.com", "youtu.be", "youtube.com"),
         "tiktok": ("vm.tiktok.com", "www.tiktok.com"),
         "instagram": ("www.instagram.com",)}
ID = re.compile(r"[A-Za-z0-9_-]+")
RESOURCES = ("local.ffmpeg", "local.ocr", "local.transcribe")
STEP_S = 900
GUARD_S = 10
HOOK_S = 3
F = ("ffmpeg", "-nostdin", "-hide_banner")
NOT_VIDEO = "the copy is not a video"
FFMPEG = "ffmpeg failed"
TRANSCRIBER = "the transcriber failed"
OCR = "the OCR failed"
FETCH = "the fetch failed"
NOT_DELETED = "the copy could not be deleted"
SHOWINFO_TIME = re.compile(r"pts_time:(\S+)")


class Refused(Exception):
    pass


class Step(Exception):
    """A step of an applied analysis failed; its message is the reason."""


def _link(platform, link):
    try:
        parts = urlsplit(link)
        host = parts.hostname
    except ValueError:
        host = None
    if (host is None or any(c.isspace() for c in link) or parts.scheme != "https" or parts.netloc != host
            or host not in HOSTS[platform]):
        raise Refused(f"link is not an https link to {platform}")


def _keys_here(values, name):
    """True when setting ``name`` names a directory with an entry. Nothing in it is opened."""
    value = values.get(name, "")
    if not value:
        return False
    path = resources.expand(value)
    if not os.path.isdir(path):
        return False
    try:
        with os.scandir(path) as entries:
            return next(entries, None) is not None
    except OSError:
        return True


def _inside(path, top):
    return path == top or top in path.parents


def _expire(state, platform, now, apply):
    """Delete (with ``apply``) every note on ``platform`` 30 days old or more, for every id."""
    directory = state / "analyses" / platform
    ids = sorted(e.name for e in os.scandir(directory) if e.is_dir(follow_symlinks=False)) if directory.is_dir() else []
    verb = "deleted" if apply else "would delete"
    for vid in ids:
        folder = directory / vid
        for name in sorted(e.name for e in os.scandir(folder) if e.is_file(follow_symlinks=False)):
            if not trends._READ_NAME.fullmatch(name):
                continue
            try:
                age = (now - reads._time(name[:-len(".json")])).total_seconds()
            except ValueError:
                continue
            if age >= reads.EXPIRY_S:
                if apply:
                    os.remove(folder / name)
                print(f"{verb} analyses/{platform}/{vid}/{name}: older than 30 days")


def _check(p, platform, vid, link, read_at, apply):
    """Every check of the order, refusing at the first that fails. Returns what an applied run needs."""
    if platform not in trends.PLATFORMS:
        raise Refused(f"platform must be one of {', '.join(trends.PLATFORMS)}")
    if not ID.fullmatch(vid):
        raise Refused("id must match [A-Za-z0-9_-]+")
    _link(platform, link)
    register, problems = resources.load(resources.REGISTER)
    if problems:
        raise Refused("; ".join(problems))
    values = p.settings
    if _keys_here(values, register["secrets_dir_setting"]):
        raise Refused(f"this machine holds the production's keys ({register['secrets_dir_setting']} is not empty);"
                      " analyse on a machine that holds no posting session")
    state = production.state_dir(p)
    parent = Path(tempfile.gettempdir()).resolve()
    if _inside(parent, p.root) or _inside(parent, state.resolve()):
        raise Refused("the temporary directory must be outside the production and STATE_DIR")
    _expire(state, platform, reads._time(_stamp(read_at)), apply)
    rows = {row["id"]: row for row in register["resources"]}
    for rid in RESOURCES:
        passed, reasons = resources.check(rows[rid], values, register, p.root)
        if not passed:
            raise Refused(f"resource {rid} is not usable: {'; '.join(reasons)}")
    fetcher = shutil.which("yt-dlp")
    if fetcher is None:
        raise Refused("the program yt-dlp is not on PATH")
    if shutil.which(values["TRANSCRIBE_CMD"]) is None:
        raise Refused("setting TRANSCRIBE_CMD is not a program on PATH")
    rel = f"analyses/{platform}/{vid}/{_stamp(read_at)}.json"
    if (state / rel).exists():
        raise Refused(f"{rel} is already stored")
    return state, rel, parent, fetcher, values


def _stamp(read_at):
    return read_at.replace("-", "").replace(":", "")


class _Runner:
    """Starts each tool in its own session and tells the guard before waiting and after reaping it."""

    def __init__(self, guard, directory):
        self.guard, self.directory = guard, directory

    def _tell(self, word, pid):
        self.guard.stdin.write(f"{word} {pid}\n")
        self.guard.stdin.flush()

    def __call__(self, argv, reason, env=None):
        """``(stdout, stderr)`` as bytes of a run that exited 0; else ``Step(reason)``."""
        try:
            tool = subprocess.Popen(argv, cwd=self.directory, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        except (OSError, ValueError, subprocess.SubprocessError):
            raise Step(reason) from None
        self._tell("run", tool.pid)
        try:
            out, err = tool.communicate(timeout=STEP_S)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(tool.pid, signal.SIGKILL)
            except OSError:
                pass
            tool.communicate()
            self._tell("done", tool.pid)
            raise Step(reason) from None
        self._tell("done", tool.pid)
        if tool.returncode != 0:
            raise Step(reason)
        return out, err


def _text(value):
    return " ".join(value.split())


def _probe(out):
    try:
        doc = json.loads(out)
        duration = round(float(doc["format"]["duration"]), 3)
        streams = doc["streams"]
        video = next(s for s in streams if s.get("codec_type") == "video")
        width, height = video["width"], video["height"]
        a, b = (int(n) for n in video["r_frame_rate"].split("/"))
        fps = round(a / b, 3)
        audio = any(s.get("codec_type") == "audio" for s in streams)
    except (ValueError, KeyError, TypeError, AttributeError, StopIteration, ZeroDivisionError):
        raise Step(NOT_VIDEO) from None
    if not (math.isfinite(duration) and duration > 0
            and all(isinstance(n, int) and not isinstance(n, bool) for n in (width, height))):
        raise Step(NOT_VIDEO)
    return {"duration_s": duration, "width": width, "height": height, "fps": fps, "audio": audio}


def _transcript(path):
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))["transcription"]
        lines = [{"from_s": e["offsets"]["from"] / 1000, "to_s": e["offsets"]["to"] / 1000, "text": _text(e["text"])}
                 for e in entries]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise Step(TRANSCRIBER) from None
    return [line for line in lines if line["text"]]


def _cuts(err):
    found = set()
    for line in err.decode("utf-8", "replace").splitlines():
        if "Parsed_showinfo" not in line:
            continue
        for value in SHOWINFO_TIME.findall(line):
            try:
                found.add(round(float(value), 3))
            except ValueError:
                pass
    return sorted(c for c in found if math.isfinite(c))


def _derive(run, directory, fetcher, link, values):
    """The derived notes of the copy fetched into ``directory``."""
    home = directory / "home"
    home.mkdir()
    env = {"HOME": str(home), "LANG": "C.UTF-8", "PATH": os.environ.get("PATH") or os.defpath}
    run([fetcher, "--ignore-config", "--no-cookies", "--no-cookies-from-browser", "--no-cache-dir", "--no-playlist",
         "--no-progress", "--quiet", "--no-warnings", "--max-filesize", "500M", "--format", "b[height<=720]/b",
         "--paths", str(directory), "--output", "copy.%(ext)s", "--", link], FETCH, env=env)
    copies = [e.path for e in os.scandir(directory)
              if e.name.startswith("copy.") and not e.name.endswith(".part") and e.is_file(follow_symlinks=False)]
    if len(copies) != 1:
        raise Step(FETCH)
    copy = copies[0]
    out, _ = run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,r_frame_rate",
                  "-of", "json", copy], NOT_VIDEO)
    notes = _probe(out)
    transcript = []
    if notes["audio"]:
        audio = str(directory / "audio.wav")
        run([*F, "-v", "error", "-i", copy, "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", audio],
            FFMPEG)
        run([values["TRANSCRIBE_CMD"], "-m", values["TRANSCRIBE_MODEL"], "-f", audio, "-oj", "-of",
             str(directory / "transcript"), "-np"], TRANSCRIBER)
        transcript = _transcript(directory / "transcript.json")
    _, err = run([*F, "-v", "info", "-i", copy, "-map", "0:v:0", "-an", "-vf", "select='gt(scene,0.3)',showinfo",
                  "-f", "null", "-"], FFMPEG)
    cuts = _cuts(err)
    frames = directory / "frames"
    frames.mkdir()
    run([*F, "-v", "error", "-i", copy, "-map", "0:v:0", "-vf", "fps=1", str(frames / "%05d.png")], FFMPEG)
    screen = []
    for index, name in enumerate(sorted(os.listdir(frames))):
        out, _ = run(["tesseract", str(frames / name), "stdout"], OCR)
        text = _text(out.decode("utf-8", "replace"))
        if text:
            screen.append({"at_s": index, "text": text})
    duration = notes["duration_s"]
    notes.update({
        "transcript": transcript, "cuts_s": cuts, "screen_text": screen,
        "cuts_per_minute": round(len(cuts) * 60 / duration, 2), "mean_shot_s": round(duration / (len(cuts) + 1), 3),
        "hook": {"until_s": HOOK_S, "words": " ".join(t["text"] for t in transcript if t["from_s"] < HOOK_S),
                 "cuts_s": [c for c in cuts if c < HOOK_S], "screen_text": [s for s in screen if s["at_s"] < HOOK_S]}})
    return notes


def _apply(platform, vid, link, read_at, state, rel, parent, fetcher, values):
    guard = subprocess.Popen([sys.executable, "-I", str(GUARD), str(parent)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True, text=True)
    reason, notes, gone = None, None, False
    try:
        made = guard.stdout.readline().rstrip("\n")
        if not made:
            raise Step("the temporary directory could not be made")
        directory = Path(made)
        try:
            notes = _derive(_Runner(guard, directory), directory, fetcher, link, values)
        except Step as step:
            reason = str(step)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
            gone = not os.path.lexists(directory)
    except Step as step:
        reason = str(step)
    finally:
        guard.stdin.close()
        try:
            guard.wait(GUARD_S)
        except subprocess.TimeoutExpired:
            pass
        guard.stdout.close()
    if reason is None and not gone:
        reason = NOT_DELETED
    if reason is not None:
        print(f"run: analyse {platform} {vid} failed: {reason}", file=sys.stderr)
        return 1
    deleted_at = production.now()
    doc = {**notes, "platform": platform, "id": vid, "link": link, "read_at": read_at, "deleted_at": deleted_at}
    reads._write(state / rel, json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    print(f"analysed {platform} {vid} at {read_at}: duration {notes['duration_s']} s, cuts {len(notes['cuts_s'])},"
          f" transcript lines {len(notes['transcript'])}, screen texts {len(notes['screen_text'])}")
    print(f"deleted the copy, its audio and its frames at {deleted_at}")
    print(f"wrote {rel}")
    return 0


def main(p, platform, vid, link, apply):
    read_at = production.now()
    state, rel, parent, fetcher, values = _check(p, platform, vid, link, read_at, apply)
    if not apply:
        print(f"analyse {platform} {vid}: would fetch a temporary copy of {link}")
        return 0
    return _apply(platform, vid, link, read_at, state, rel, parent, fetcher, values)
