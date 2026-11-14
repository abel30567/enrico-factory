"""Tests for `factory run --analyse <platform> <id> <link>`: kit/lib/analysis.py and kit/lib/copyguard.py.

Each run is `python3 kit/factory run --analyse ... --root <world>/production` in a subprocess. The world copies
tests/fixtures/analysis/: `production/` is the production, `answers/` what the fake tools answer. Beside it: a
fresh `TMPDIR` (`tmp/`), `state/` (`STATE_DIR`), `secrets/` (absent unless a test makes it), `models/` and a
seeded `home/` (`.netrc`, `.config/yt-dlp/config` with `--cookies`). `PATH` is `<world>/bin` alone: `yt-dlp`,
`ffprobe`, `ffmpeg`, `tesseract` and `whisper-cli` are copies of `fake_tool.py`, which reads `bin/control.json`
and logs each call to `log.jsonl`. The environment also holds `GH_TOKEN` and `http_proxy`, both seeds that
the fetch must never see. `FACTORY_NOW` is 2026-11-14T09:00:00Z. Each seeded defect is one change to the world.

The answers: 4.5 s, 1080 by 1920, `30000/1001`, an audio stream; whisper 0 to 1800 and 1800 to 4200 ms (and an
empty entry); cuts at 1.201 and 3.504 s; 5 frames, text on the first and the fourth. No video file is
committed: `y4m()` writes the one the real ffmpeg reads.
"""

import ast
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "analysis"
FACTORY = ROOT / "kit" / "factory"
NOW = "2026-11-14T09:00:00Z"
SEED = "seeded-Wickabel-7f3a90"
LINK = "https://www.youtube.com/watch?v=yt-a"
NOTES = "analyses/youtube/yt-a/20261114T090000Z.json"
TOOLS = ("yt-dlp", "ffprobe", "ffmpeg", "tesseract", "whisper-cli")
CHECK_STEPS = {"version", "filters"}
CLEAN_OUT = ("analysed youtube yt-a at 2026-11-14T09:00:00Z: duration 4.5 s, cuts 2, transcript lines 2,"
             " screen texts 2\n"
             "deleted the copy, its audio and its frames at 2026-11-14T09:00:00Z\n"
             f"wrote {NOTES}\n")
FETCH_ARGV = ["--ignore-config", "--no-cookies", "--no-cookies-from-browser", "--no-cache-dir", "--no-playlist",
              "--no-progress", "--quiet", "--no-warnings", "--max-filesize", "500M", "--format",
              "b[height<=720]/b", "--paths"]
FFMPEG_FILTERS = ("ebur128", "loudnorm", "ssim", "libvmaf", "blackdetect", "freezedetect", "silencedetect",
                  "subtitles")
# Ruling 2: what analysis.py may take from the kit, and what neither module may import.
KIT_ALLOWED = {"production", "reads", "resources", "trends"}
FORBIDDEN = {"cmd_run", "media", "assets", "gitstate", "github", "socket", "http"}


def y4m(path):
    """A 16 by 16 Y4M at 2 fps, 4 s long, black then white from 2.0 s: 3,160 bytes."""
    header = b"YUV4MPEG2 W16 H16 F2:1 Ip A1:1 C420jpeg\n"
    frames = b""
    for index in range(8):
        luma = 16 if index < 4 else 235
        frames += b"FRAME\n" + bytes([luma]) * 256 + bytes([128]) * 128
    path.write_bytes(header + frames)
    return path


def tree(path):
    """Every file under ``path`` (relative posix path to bytes); empty when ``path`` does not exist."""
    if not path.exists():
        return {}
    return {f.relative_to(path).as_posix(): f.read_bytes() for f in sorted(path.rglob("*")) if f.is_file()}


def imported(source):
    """The modules ``source`` imports, by `ast`: `import a.b` gives `a.b`, `from a import b` gives `a.b`."""
    names = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names += [f"{node.module}.{alias.name}" if node.module else alias.name for alias in node.names]
            if node.module:
                names.append(node.module)
    return names


def forbidden(source):
    """The imports of ``source`` that ruling 2 forbids, sorted: a name with a forbidden part, or under
    `urllib.request`."""
    return sorted({name for name in imported(source)
                   if set(name.split(".")) & FORBIDDEN or name == "urllib.request" or name.startswith("urllib.request.")})


class World:
    def __init__(self, test):
        tmp = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, tmp, True)
        self.test, self.tmp = test, tmp
        self.root, self.answers = tmp / "production", tmp / "answers"
        shutil.copytree(FIX / "production", self.root)
        shutil.copytree(FIX / "answers", self.answers)
        self.state, self.secrets, self.tmpdir = tmp / "state", tmp / "secrets", tmp / "tmp"
        self.state.mkdir()
        self.tmpdir.mkdir()
        self.model = tmp / "models" / "ggml-fake.bin"
        self.model.parent.mkdir()
        self.model.write_bytes(b"fake model")
        self.home = tmp / "home"
        (self.home / ".config" / "yt-dlp").mkdir(parents=True)
        (self.home / ".netrc").write_text(f"machine youtube login owner password {SEED}\n")
        (self.home / ".config" / "yt-dlp" / "config").write_text(f"--cookies {self.home}/cookies-{SEED}.txt\n")
        self.bin, self.log = tmp / "bin", tmp / "log.jsonl"
        self.bin.mkdir()
        text = (FIX / "fake_tool.py").read_text(encoding="utf-8").split("\n", 1)[1]
        for name in TOOLS:
            self.install(name, text)
        self.video = tmp / "video.mp4"
        self.video.write_bytes(b"fake video bytes")
        self.control = {"log": str(self.log), "answers": str(self.answers), "video": str(self.video)}
        self.settings = {"STATE_DIR": str(self.state), "SECRETS_DIR": "", "TRANSCRIBE_CMD": "whisper-cli",
                         "TRANSCRIBE_MODEL": str(self.model)}
        self.env = {"PATH": str(self.bin), "TMPDIR": str(self.tmpdir), "FACTORY_NOW": NOW, "HOME": str(self.home),
                    "GH_TOKEN": SEED, "http_proxy": "http://127.0.0.1:1", "LANG": "C.UTF-8"}

    def install(self, name, text):
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n{text}", encoding="utf-8")
        path.chmod(0o755)

    def answer(self, name, doc):
        (self.answers / name).write_text(json.dumps(doc), encoding="utf-8")

    def write(self):
        (self.root / "production.local.env").write_text(
            "".join(f"{k}={v}\n" for k, v in self.settings.items()), encoding="utf-8")
        (self.bin / "control.json").write_text(json.dumps(self.control), encoding="utf-8")

    def argv(self, *args):
        return [sys.executable, str(FACTORY), "run", *args, "--root", str(self.root)]

    def run(self, *args):
        self.write()
        done = subprocess.run(self.argv(*args), env=self.env, capture_output=True, text=True, timeout=120)
        return done.returncode, done.stdout, done.stderr

    def analyse(self, *extra, platform="youtube", vid="yt-a", link=LINK):
        return self.run("--analyse", platform, vid, link, *extra)

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def steps(self):
        return [c["step"] for c in self.calls()]

    def tool_steps(self):
        return [s for s in self.steps() if s not in CHECK_STEPS]

    def notes(self):
        path = self.state / NOTES
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


class Base(unittest.TestCase):
    def setUp(self):
        self.w = World(self)

    def assertRefused(self, result, line):
        code, out, err = result
        self.assertEqual((code, out, err), (2, "", f"run: {line}\n"))
        self.assertEqual(self.w.tool_steps(), [])

    def assertCleanTmp(self):
        self.assertEqual(os.listdir(self.w.tmpdir), [])


class Clean(Base):
    """1. A clean run: ruling 9's lines, the expected notes, nothing left of the copy."""

    def test_clean(self):
        self.w.write()
        before = tree(self.w.root)
        code, out, err = self.w.analyse("--apply")
        self.assertEqual((code, out, err), (0, CLEAN_OUT, ""))
        self.assertEqual((self.w.state / NOTES).read_bytes(), (FIX / "expected" / "20261114T090000Z.json").read_bytes())
        self.assertCleanTmp()
        self.assertEqual(sorted(tree(self.w.state)), [NOTES])
        self.assertEqual(tree(self.w.root), before)
        self.assertEqual(self.w.tool_steps(), ["yt-dlp", "ffprobe", "audio", "whisper-cli", "cuts", "frames",
                                               "tesseract", "tesseract", "tesseract", "tesseract", "tesseract"])

    def test_notes_keys(self):
        self.w.analyse("--apply")
        self.assertEqual(sorted(self.w.notes()), [
            "audio", "cuts_per_minute", "cuts_s", "deleted_at", "duration_s", "fps", "height", "hook", "id", "link",
            "mean_shot_s", "platform", "read_at", "screen_text", "transcript", "width"])

    def test_other_tools_get_the_jobs_environment_in_the_copy_directory(self):
        self.w.analyse("--apply")
        calls = [c for c in self.w.calls() if c["step"] not in CHECK_STEPS]
        fetch_cwd = calls[0]["cwd"]
        self.assertTrue(Path(fetch_cwd).parent == self.w.tmpdir.resolve())
        for call in calls[1:]:
            self.assertEqual(call["cwd"], fetch_cwd)
            self.assertEqual(call["env"]["GH_TOKEN"], SEED)


class AnonymousFetch(Base):
    """2. The fetch: ruling 6's argv, only HOME, LANG and PATH, no seed; no fetch on a machine with keys."""

    def fetch(self):
        return next(c for c in self.w.calls() if c["step"] == "yt-dlp")

    def test_argv_and_environment(self):
        code, _, _ = self.w.analyse("--apply")
        self.assertEqual(code, 0)
        call = self.fetch()
        directory = call["cwd"]
        self.assertEqual(Path(directory).parent, self.w.tmpdir.resolve())
        self.assertTrue(Path(directory).name.startswith("factory-copy-"))
        self.assertEqual(call["argv"], [*FETCH_ARGV, directory, "--output", "copy.%(ext)s", "--", LINK])
        self.assertEqual(sorted(call["env"]), ["HOME", "LANG", "PATH"])
        self.assertEqual(call["env"]["HOME"], f"{directory}/home")
        self.assertEqual(call["env"]["LANG"], "C.UTF-8")
        self.assertEqual(call["env"]["PATH"], str(self.w.bin))
        self.assertNotIn(SEED, json.dumps(call))
        self.assertNotIn(str(self.w.home), json.dumps(call))

    def test_keys_on_the_machine(self):
        self.w.secrets.mkdir()
        (self.w.secrets / "youtube.key").write_text(SEED)
        self.w.settings["SECRETS_DIR"] = str(self.w.secrets)
        self.assertRefused(self.w.analyse("--apply"),
                           "this machine holds the production's keys (SECRETS_DIR is not empty);"
                           " analyse on a machine that holds no posting session")
        self.assertNotIn("yt-dlp", self.w.steps())

    def test_empty_secrets_directory(self):
        self.w.secrets.mkdir()
        self.w.settings["SECRETS_DIR"] = str(self.w.secrets)
        self.assertEqual(self.w.analyse("--apply"), (0, CLEAN_OUT, ""))

    def test_missing_secrets_directory(self):
        self.w.settings["SECRETS_DIR"] = str(self.w.secrets)
        self.assertEqual(self.w.analyse("--apply"), (0, CLEAN_OUT, ""))


class GoneOnFailure(Base):
    """3. A failing step: its reason, exit 1, no notes, nothing left of the copy."""

    def assertFailed(self, reason):
        self.assertEqual(self.w.analyse("--apply"), (1, "", f"run: analyse youtube yt-a failed: {reason}\n"))
        self.assertCleanTmp()
        self.assertEqual(tree(self.w.state), {})

    def test_each_step(self):
        for step, reason in (("yt-dlp", "the fetch failed"), ("ffprobe", "the copy is not a video"),
                             ("audio", "ffmpeg failed"), ("whisper-cli", "the transcriber failed"),
                             ("cuts", "ffmpeg failed"), ("frames", "ffmpeg failed"), ("tesseract", "the OCR failed")):
            with self.subTest(step=step):
                self.w.log.unlink(missing_ok=True)
                self.w.control["fail"] = step
                self.assertFailed(reason)
                self.assertEqual(self.w.tool_steps()[-1], step)

    def test_fetch_writes_nothing(self):
        self.w.control["video"] = None
        self.assertFailed("the fetch failed")

    def test_no_video_stream(self):
        self.w.answer("ffprobe.json", {"streams": [{"codec_type": "audio"}], "format": {"duration": "4.5"}})
        self.assertFailed("the copy is not a video")

    def test_unreadable_transcript(self):
        (self.w.answers / "whisper.json").write_text("not json")
        self.assertFailed("the transcriber failed")


class GoneWhenKilled(Base):
    """4. A job killed while a tool hangs: the guard kills the tool and deletes the copy."""

    def kill(self, step, how, sig):
        self.w.log.unlink(missing_ok=True)
        for leftover in self.w.tmpdir.iterdir():
            shutil.rmtree(leftover)
        self.w.control["hang"] = step
        self.w.write()
        job = subprocess.Popen(self.w.argv("--analyse", "youtube", "yt-a", LINK, "--apply"), env=self.w.env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            deadline = time.monotonic() + 60
            while step not in self.w.steps():
                self.assertLess(time.monotonic(), deadline, f"{step} never started")
                time.sleep(0.05)
            pid = next(c["pid"] for c in self.w.calls() if c["step"] == step)
            time.sleep(0.2)
            (os.kill if how == "job" else os.killpg)(job.pid, sig)
            job.wait(10)
        finally:
            if job.poll() is None:
                job.kill()
                job.wait()
        deadline = time.monotonic() + 5
        while True:
            alive = True
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                alive = False
            if not alive and not os.listdir(self.w.tmpdir):
                break
            if time.monotonic() > deadline:
                if alive:
                    os.kill(pid, signal.SIGKILL)
                self.fail(f"after 5 s: tool alive {alive}, TMPDIR {os.listdir(self.w.tmpdir)}")
            time.sleep(0.05)
        self.assertEqual(tree(self.w.state), {})

    def test_sigkill_to_the_job(self):
        self.kill("frames", "job", signal.SIGKILL)

    def test_signals_to_the_group(self):
        for step in ("yt-dlp", "whisper-cli", "tesseract"):
            for sig in (signal.SIGKILL, signal.SIGTERM, signal.SIGINT):
                with self.subTest(step=step, sig=sig.name):
                    self.kill(step, "group", sig)


class OutsideTheProduction(Base):
    """5. The temporary directory must be outside the production and STATE_DIR."""

    def test_inside_the_production(self):
        self.w.env["TMPDIR"] = str(self.w.root / "tmp")
        (self.w.root / "tmp").mkdir()
        self.assertRefused(self.w.analyse("--apply"),
                           "the temporary directory must be outside the production and STATE_DIR")

    def test_inside_state_dir(self):
        self.w.env["TMPDIR"] = str(self.w.state / "tmp")
        (self.w.state / "tmp").mkdir()
        self.assertRefused(self.w.analyse("--apply"),
                           "the temporary directory must be outside the production and STATE_DIR")

    def test_sibling_of_the_production(self):
        self.assertEqual(self.w.tmpdir.parent, self.w.root.parent)
        self.assertEqual(self.w.analyse("--apply"), (0, CLEAN_OUT, ""))
        self.assertCleanTmp()


class NoAudio(Base):
    """6. No audio stream: no extract, no transcriber, an empty transcript."""

    def test_no_audio(self):
        self.w.answer("ffprobe.json", {"streams": [{"codec_type": "video", "width": 1080, "height": 1920,
                                                    "r_frame_rate": "30000/1001"}],
                                       "format": {"duration": "4.500000"}})
        code, out, _ = self.w.analyse("--apply")
        self.assertEqual(code, 0)
        self.assertIn("transcript lines 0", out)
        notes = self.w.notes()
        self.assertIs(notes["audio"], False)
        self.assertEqual(notes["transcript"], [])
        self.assertEqual(notes["hook"]["words"], "")
        self.assertNotIn("audio", self.w.steps())
        self.assertNotIn("whisper-cli", self.w.steps())
        self.assertCleanTmp()


class ToolsMissing(Base):
    """7. A tool or a setting missing: refused before any fetch."""

    def test_yt_dlp_removed(self):
        (self.w.bin / "yt-dlp").unlink()
        self.assertRefused(self.w.analyse("--apply"), "the program yt-dlp is not on PATH")

    def test_transcribe_cmd_not_a_program(self):
        self.w.settings["TRANSCRIBE_CMD"] = "no-such-program"
        self.assertRefused(self.w.analyse("--apply"), "setting TRANSCRIBE_CMD is not a program on PATH")

    def test_tesseract_removed(self):
        (self.w.bin / "tesseract").unlink()
        self.assertRefused(self.w.analyse("--apply"),
                           "resource local.ocr is not usable: command not found: tesseract --version")

    def test_model_missing(self):
        self.w.model.unlink()
        self.assertRefused(self.w.analyse("--apply"),
                           "resource local.transcribe is not usable: setting TRANSCRIBE_MODEL names nothing that exists")

    def test_libvmaf_dropped(self):
        self.w.control["drop"] = ["libvmaf"]
        self.assertRefused(self.w.analyse("--apply"),
                           "resource local.ffmpeg is not usable: pattern not found: libvmaf")


class Arguments(Base):
    """8. Rulings 1 and 3: each argument refused before any tool runs."""

    def test_job_file_with_analyse(self):
        self.assertRefused(self.w.run("job.json", "--analyse", "youtube", "yt-a", LINK),
                           "--analyse takes no job file, no --pull and no --read")

    def test_pull_with_analyse(self):
        self.assertRefused(self.w.run("--pull", "3", "--analyse", "youtube", "yt-a", LINK),
                           "--analyse takes no job file, no --pull and no --read")

    def test_read_with_analyse(self):
        self.assertRefused(self.w.run("--read", "harbour-tales", "youtube", "--analyse", "youtube", "yt-a", LINK),
                           "--analyse takes no job file, no --pull and no --read")

    def test_platform(self):
        self.assertRefused(self.w.analyse("--apply", platform="vimeo"),
                           "platform must be one of youtube, tiktok, instagram")

    def test_id(self):
        self.assertRefused(self.w.analyse("--apply", vid="yt a"), "id must match [A-Za-z0-9_-]+")

    def test_links(self):
        for link in ("http://www.youtube.com/watch?v=a", "https://www.youtube.com:8443/watch?v=a",
                     "https://user@www.youtube.com/watch?v=a", "https://www.youtube.com.evil.example/x",
                     "https://WWW.youtube.com/watch?v=a", "https://www.youtube.com/watch?v=a b",
                     "https://www.tiktok.com/@a/video/1", "https://[::1/x"):
            with self.subTest(link=link):
                self.assertRefused(self.w.analyse("--apply", link=link), "link is not an https link to youtube")

    def test_state_dir_relative(self):
        self.w.settings["STATE_DIR"] = "relative"
        self.assertRefused(self.w.analyse("--apply"),
                           "setting STATE_DIR must be an absolute path outside the repository")

    def test_tiktok_without_apply(self):
        link = "https://www.tiktok.com/@a/video/7301"
        self.assertEqual(self.w.analyse(platform="tiktok", vid="7301", link=link),
                         (0, f"analyse tiktok 7301: would fetch a temporary copy of {link}\n", ""))

    def test_every_host(self):
        for platform, host in (("youtube", "m.youtube.com"), ("youtube", "youtu.be"), ("youtube", "youtube.com"),
                               ("tiktok", "vm.tiktok.com"), ("instagram", "www.instagram.com")):
            with self.subTest(host=host):
                code, _, _ = self.w.analyse(platform=platform, link=f"https://{host}/x")
                self.assertEqual(code, 0)


class WithoutApply(Base):
    """9. Without --apply: the would-fetch line, only the register's checks run, nothing written."""

    def test_without_apply(self):
        self.w.write()
        state, tmp = tree(self.w.state), os.listdir(self.w.tmpdir)
        self.assertEqual(self.w.analyse(), (0, f"analyse youtube yt-a: would fetch a temporary copy of {LINK}\n", ""))
        self.assertEqual(self.w.steps(), ["version", "filters", "version"])
        self.assertEqual(tree(self.w.state), state)
        self.assertEqual(os.listdir(self.w.tmpdir), tmp)


class ExpiryAndSameSecond(Base):
    """10. Notes 30 days old are deleted; nothing else is; notes of the same second refuse the run."""

    def seed(self):
        files = {
            "analyses/youtube/yt-old1/20261015T090000Z.json": "{}\n",
            "analyses/youtube/yt-old2/20261015T090000Z.json": "{}\n",
            "analyses/youtube/yt-keep/20261015T090001Z.json": "{\"keep\": 1}\n",
            "analyses/youtube/yt-old1/notes.txt": "kept\n",
            "analyses/tiktok/7301/20261001T090000Z.json": "{\"keep\": 2}\n",
            "reads/harbour-tales/youtube/20261014T090000Z.json": "{\"keep\": 3}\n",
        }
        for rel, text in files.items():
            (self.w.state / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.w.state / rel).write_text(text)
        return {k: v.encode() for k, v in files.items()}

    LINES = ("{} analyses/youtube/yt-old1/20261015T090000Z.json: older than 30 days\n"
             "{} analyses/youtube/yt-old2/20261015T090000Z.json: older than 30 days\n")

    def test_expiry(self):
        files = self.seed()
        code, out, err = self.w.analyse("--apply")
        self.assertEqual((code, out, err), (0, self.LINES.format("deleted", "deleted") + CLEAN_OUT, ""))
        kept = {k: v for k, v in files.items() if "old" not in k or k.endswith("notes.txt")}
        self.assertEqual({k: v for k, v in tree(self.w.state).items() if k != NOTES}, kept)

    def test_expiry_without_apply(self):
        files = self.seed()
        code, out, err = self.w.analyse()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, self.LINES.format("would delete", "would delete")
                         + f"analyse youtube yt-a: would fetch a temporary copy of {LINK}\n")
        self.assertEqual(tree(self.w.state), files)

    def test_same_second(self):
        (self.w.state / NOTES).parent.mkdir(parents=True)
        (self.w.state / NOTES).write_text("{}\n")
        self.assertRefused(self.w.analyse("--apply"), f"{NOTES} is already stored")
        self.assertEqual((self.w.state / NOTES).read_text(), "{}\n")


def real_ffmpeg():
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        return False
    try:
        done = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    listed = (done.stdout + done.stderr).decode("utf-8", "replace")
    return done.returncode == 0 and all(name in listed for name in FFMPEG_FILTERS)


@unittest.skipUnless(real_ffmpeg(), "needs ffmpeg and ffprobe with the eight filters of local.ffmpeg")
class RealFfmpeg(Base):
    """11. The real ffmpeg and ffprobe on the Y4M of `y4m()`."""

    def test_real_ffmpeg(self):
        for name in ("ffmpeg", "ffprobe"):
            (self.w.bin / name).unlink()
            (self.w.bin / name).symlink_to(shutil.which(name))
        self.w.control["video"] = str(y4m(self.w.tmp / "video.y4m"))
        self.assertEqual((self.w.tmp / "video.y4m").stat().st_size, 3160)
        code, _, err = self.w.analyse("--apply")
        self.assertEqual((code, err), (0, ""))
        notes = self.w.notes()
        self.assertEqual((notes["duration_s"], notes["width"], notes["height"], notes["fps"], notes["audio"]),
                         (4.0, 16, 16, 2.0, False))
        self.assertEqual((notes["cuts_s"], notes["cuts_per_minute"], notes["mean_shot_s"]), ([2.0], 15.0, 2.0))
        self.assertCleanTmp()


class Interface(unittest.TestCase):
    """12. By `ast`: the imports of both modules are those of ruling 2."""

    def source(self, name):
        return (ROOT / "kit" / "lib" / name).read_text(encoding="utf-8")

    def test_analysis_imports(self):
        names = imported(self.source("analysis.py"))
        self.assertEqual(forbidden(self.source("analysis.py")), [])
        kit = {n.split(".")[2] for n in names if n.startswith("kit.lib.")}
        self.assertLessEqual(kit, KIT_ALLOWED)

    def test_copyguard_imports(self):
        names = imported(self.source("copyguard.py"))
        self.assertEqual(forbidden(self.source("copyguard.py")), [])
        self.assertEqual([n for n in names if n == "kit" or n.startswith("kit.")], [])
        self.assertTrue(all(n.split(".")[0] in sys.stdlib_module_names for n in names))

    def test_seeded_imports(self):
        for source, flagged in (("import media", ["media"]), ("from kit.lib import gitstate", ["kit.lib.gitstate"]),
                                ("import urllib.request", ["urllib.request"]),
                                ("from kit.lib import cmd_run", ["kit.lib.cmd_run"]), ("import socket", ["socket"]),
                                ("import kit.lib.media", ["kit.lib.media"])):
            with self.subTest(source=source):
                self.assertEqual(forbidden(source), flagged)

    def test_urllib_parse_allowed(self):
        self.assertEqual(forbidden("import urllib.parse"), [])
        self.assertEqual(forbidden("from urllib.parse import urlsplit"), [])


if __name__ == "__main__":
    unittest.main()
