"""Tests for the engine side of a render job (#462): `kit/engine/start.sh`, `job.sh`, `build.py` and
`kit/lib/scenestate.py`. No test needs docker, curl, timeout, ffmpeg or the engine: `job.sh` runs with the
fake programs of `tests/fixtures/engine/bin`, `build.py` with the fake `unreal` of `tests/fixtures/engine`.
Seeded copies live in a temporary directory.
"""

import ast
import copy
import gc
import hashlib
import importlib.util
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit.lib import golden, reproduce, scenestate  # noqa: E402

ENGINE = ROOT / "kit/engine"
FIX = ROOT / "tests/fixtures/engine"
GOLDEN = ROOT / "kit/evals/golden"
IMAGE = "registry.invalid/team/engine:5@sha256:" + "a" * 64
STORE = "https://store.invalid/jobs/n0nce"
LINKS = f"GET in {STORE}/in\nPUT out {STORE}/out\nPUT log {STORE}/log\nPUT status {STORE}/status\n"
PLACEHOLDERS = {"__MAX_MIN__", "__IMAGE__", "__TOKEN__", "__LINKS__"}
START_NEEDS = (
    'REGISTRY="${IMAGE%%/*}"',
    "printf '%s\\n' '__TOKEN__' | docker login \"$REGISTRY\" -u x --password-stdin",
    'docker pull -q "$IMAGE"',
    'docker logout "$REGISTRY"',
    "curl -sf -o \"$W/links.txt\" '__LINKS__'",
    "sleep 4",
    'mkdir -p "$W/work/job" "$W/work/out"',
    'tar -xf "$W/in.tar" -C "$W/work/job"',
    'WORK="$W/work"',
    'LINKS="$W/links.txt"',
    "NODE_LOG=/var/log/factory-node.log",
    "export WORK IMAGE LINKS NODE_LOG",
    "mkfs.ext4",
    "/mnt/factory",
    "/var/lib/factory",
    "lsblk -rno MOUNTPOINT \"$D\" | grep -m1 '^/'",
    "/var/lib/$d",
)
RUN_JOB = 'bash "$W/work/job/job.sh"'
BASH4 = ("declare -A", "mapfile", "readarray", ",,}", "^^}", "&>>", "|&")
VENDOR = re.compile(r"ghcr\.io|dlami|SMOKE|smoke")
TAIL = ["-unattended", "-nosplash", "-nosound", "-stdout", "-FullStdOutLogOutput"]
EDITOR = "/home/ue4/UnrealEngine/Engine/Binaries/Linux/UnrealEditor-Cmd"
# The binary of the job asset wren (the asset of tests/test_asset_jobs.py), its sha256 and its locked state.
GLB = b"glTF wren"
BIN = hashlib.sha256(GLB).hexdigest()
WREN = {"asset": "wren", "kind": "character", "sha256": BIN}
MEASURED = {"floor": [0, 0.4557, 1, 1], "ball": [0.3924, 0.3547, 0.6076, 0.7563],
            "block": [0.6016, 0.4815, 0.6983, 0.655]}


def start_problems(text: str) -> list[str]:
    """What keeps a start script from meeting ruling 1 of #462."""
    found = []
    commands = [line.strip() for line in text.splitlines()[1:] if line.strip() and not line.strip().startswith("#")]
    if not text.startswith("#!/bin/bash\n"):
        found.append("no #!/bin/bash line")
    if not commands or commands[0] != "shutdown -h +__MAX_MIN__":
        found.append("first command is not shutdown -h +__MAX_MIN__")
    if not commands or commands[-1] != "shutdown -h now":
        found.append("last command is not shutdown -h now")
    names = set(re.findall(r"__[A-Z0-9_]+__", text))
    if names != PLACEHOLDERS:
        found.append(f"placeholders are {sorted(names)}")
    if text.count("__TOKEN__") != 1:
        found.append("__TOKEN__ is not there once")
    for needed in START_NEEDS + (RUN_JOB,):
        if needed not in text:
            found.append(f"lacks {needed}")
    for banned in ("sleep 30", "-run=pythonscript"):
        if banned in text:
            found.append(f"holds {banned}")
    if len(text.encode("utf-8")) > 8192:
        found.append("more than 8192 bytes")
    run = text.find(RUN_JOB)
    for step in ("pull", "fetch"):
        at = text.find(f'"step": "{step}"')
        stop = text.find("shutdown -h now", at)
        if at < 0 or stop < 0 or not stop < run:
            found.append(f'"step": "{step}" is not followed by shutdown -h now before job.sh runs')
    return found


def job_problems(text: str) -> list[str]:
    """What keeps job.sh from running with `set -u`, not `set -e`, on bash 3.2."""
    lines = [line.strip() for line in text.splitlines()]
    found = [] if "set -u" in lines else ["no line set -u"]
    if any(re.match(r"set\s+(-[a-z]*e|-o\s+errexit)", line) for line in lines):
        found.append("a line set -e")
    found += [f"holds {b}" for b in BASH4 if b in text]
    return found


def registry_problems(texts: dict) -> list[str]:
    """Vendor values in the engine files, and a login or logout that does not name "$REGISTRY"."""
    found = [f"{name} holds a vendor value" for name, text in sorted(texts.items()) if VENDOR.search(text)]
    for name, text in sorted(texts.items()):
        for line in text.splitlines():
            for verb in ("docker login", "docker logout"):
                if verb in line and f'{verb} "$REGISTRY"' not in line:
                    found.append(f"{name}: {verb} does not name \"$REGISTRY\"")
    return found


def import_problems(text: str) -> list[str]:
    """Imports that are neither `unreal` nor the standard library."""
    found = []
    for node in ast.walk(ast.parse(text)):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""] if node.level == 0 else ["."]
        for name in names:
            top = name.split(".")[0]
            if top != "unreal" and top not in sys.stdlib_module_names:
                found.append(f"imports {name}")
    return found


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def copy(self, name: str, text: str) -> str:
        """Writes a seeded copy and reads it back."""
        path = self.tmp / "copies" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path.read_text(encoding="utf-8")


class ScriptTest(EngineCase):
    def test_1_scripts_parse_and_meet_the_rulings(self):
        for name in ("start.sh", "job.sh"):
            run = subprocess.run(["bash", "-n", str(ENGINE / name)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
        start = (ENGINE / "start.sh").read_text(encoding="utf-8")
        job = (ENGINE / "job.sh").read_text(encoding="utf-8")
        self.assertEqual(start_problems(start), [])
        self.assertEqual(job_problems(job), [])
        lines = start.splitlines()
        stop = lines.index("shutdown -h +__MAX_MIN__")
        moved = lines[:stop] + lines[stop + 1:]
        say = next(i for i, line in enumerate(moved) if line.startswith("say(){"))
        moved.insert(say + 1, "shutdown -h +__MAX_MIN__")
        defects = {
            "shutdown after say": "\n".join(moved) + "\n",
            "__DISK__": start.replace('D=$(', 'D=__DISK__ D2=$(', 1),
            "sleep 30": start.replace("sync\nshutdown -h now\n", "sync; sleep 30\nshutdown -h now\n"),
            "no fetch": "".join(l for l in start.splitlines(True) if '"step": "fetch"' not in l),
        }
        for name, text in defects.items():
            with self.subTest(defect=name):
                self.assertNotEqual(text, start)
                self.assertNotEqual(start_problems(self.copy("start.sh", text)), [])
        for name, text in {"mapfile": job.replace("set -u\n", "set -u\nmapfile -t L < \"$LINKS\"\n"),
                           "set -e": job.replace("set -u\n", "set -u\nset -e\n")}.items():
            with self.subTest(defect=name):
                self.assertNotEqual(text, job)
                self.assertNotEqual(job_problems(self.copy("job.sh", text)), [])

    def test_2_no_vendor_value_and_the_registry_from_the_image(self):
        texts = {p.name: p.read_text(encoding="utf-8") for p in sorted(ENGINE.iterdir()) if p.is_file()}
        self.assertEqual(sorted(texts), ["build.py", "job.sh", "start.sh"])
        self.assertEqual(registry_problems(texts), [])
        line = next(l for l in texts["start.sh"].splitlines() if l.startswith("REGISTRY="))
        run = subprocess.run(["bash", "-c", line + '\necho "$REGISTRY"'], capture_output=True, text=True,
                             env={"PATH": os.environ["PATH"], "IMAGE": IMAGE})
        self.assertEqual(run.stdout, "registry.invalid\n")
        seeded = dict(texts, **{"start.sh": self.copy("start.sh", texts["start.sh"].replace(
            'docker login "$REGISTRY"', "docker login ghcr.io"))})
        self.assertNotEqual(seeded["start.sh"], texts["start.sh"])
        self.assertNotEqual(registry_problems(seeded), [])

    def test_3_build_imports_only_unreal_and_the_standard_library(self):
        text = (ENGINE / "build.py").read_text(encoding="utf-8")
        self.assertEqual(import_problems(text), [])
        self.assertEqual(import_problems(self.copy("build.py", "import requests\n" + text)), ["imports requests"])


class JobTest(EngineCase):
    def run_job(self, links: str = LINKS, **fake) -> dict:
        """Runs a copy of job.sh with the fake programs first on PATH; returns what it left."""
        bin_ = self.tmp / "bin"
        shutil.copytree(FIX / "bin", bin_)
        for path in bin_.iterdir():
            path.chmod(0o755)
        work = self.tmp / "work"
        (work / "job").mkdir(parents=True)
        shutil.copy(ENGINE / "job.sh", work / "job/job.sh")
        shutil.copy(ENGINE / "build.py", work / "job/build.py")
        (self.tmp / "links.txt").write_text(links, encoding="utf-8")
        (self.tmp / "fake").mkdir(exist_ok=True)
        env = {"PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}", "WORK": str(work), "IMAGE": IMAGE,
               "LINKS": str(self.tmp / "links.txt"), "NODE_LOG": str(self.tmp / "node.log"),
               "FAKE": str(self.tmp / "fake"), **fake}
        run = subprocess.run(["bash", str(work / "job/job.sh")], env=env, capture_output=True, text=True,
                             timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        fake_dir = self.tmp / "fake"
        with tarfile.open(work / "out.tar") as tar:
            names = tar.getnames()
        return {"work": work, "names": names,
                "status": (work / "status.json").read_text(encoding="utf-8"),
                "node": json.loads((work / "out/node.json").read_text(encoding="utf-8")),
                "log": (work / "log.txt").read_text(encoding="utf-8"),
                "curl": (fake_dir / "curl.log").read_text(encoding="utf-8").splitlines(),
                "timeout": (fake_dir / "timeout.log").read_text(encoding="utf-8").split(),
                "put": fake_dir / "put"}

    def test_4_job_runs_build_render_rerender_and_puts_status_last(self):
        found = self.run_job()
        frames = [f"frame-{n:06d}.{e}" for n in (8, 9, 10) for e in ("exr", "png")]
        self.assertEqual(found["names"], ["engine-build.log", "engine-render.log"] + frames
                         + ["node.json", "rerender-000008.png", "rerender-000010.png", "scene.json"])
        self.assertEqual(found["curl"], [f"PUT {STORE}/out", f"PUT {STORE}/log", f"PUT {STORE}/status"])
        self.assertEqual(found["status"], '{"result": "ok"}\n')
        self.assertEqual((found["put"] / "status").read_text(encoding="utf-8"), '{"result": "ok"}\n')
        node = found["node"]
        self.assertEqual(sorted(node), ["editor", "engine", "image", "result", "steps"])
        self.assertEqual((node["editor"], node["engine"], node["image"], node["result"]),
                         (EDITOR, "5.0.0-fake+UE5", IMAGE, "ok"))
        self.assertEqual([s["step"] for s in node["steps"]], ["build", "render", "rerender"])
        self.assertEqual(node["steps"][0]["markers"], ["FACTORY build ok"])
        self.assertEqual(node["steps"][0]["command"], [EDITOR, "/work/Job/Job.uproject",
                                                       "-ExecutePythonScript=/work/job/build.py",
                                                       "-RenderOffscreen"] + TAIL)
        render = node["steps"][1]["command"]
        self.assertIn("-ResX=640", render)
        self.assertEqual(render[-6:], ["-MoviePipelineConfig=MovieRenderPipeline/QueueManifest.utxt"] + TAIL)
        self.assertIn("-MoviePipelineConfig=MovieRenderPipeline/Rerender.utxt", node["steps"][2]["command"])
        self.assertTrue(all(sorted(s) == ["command", "markers", "rc", "seconds", "step"] for s in node["steps"]))
        self.assertEqual(found["timeout"], ["900", "1800", "900"])
        self.assertRegex(found["log"], r"(?m)^NODE \d\d:\d\d:\d\d step build rc=0 seconds=\d+$")
        self.assertRegex(found["log"], r"(?m)^NODE \d\d:\d\d:\d\d put out http=200$")
        self.assertRegex(found["log"], r"(?m)^NODE \d\d:\d\d:\d\d result ok$")
        self.assertIn("== engine-build.log (last 100 lines)\n", found["log"])
        self.assertIn("== rerender\n", (found["work"] / "out/engine-render.log").read_text(encoding="utf-8"))
        self.assertTrue((found["work"] / "Job/Job.uproject").is_file())

    def test_5_job_defects(self):
        with self.subTest(defect="no ok line"):
            found = self.run_job(FAKE_NO_OK="1")
            self.assertEqual(found["status"], '{"result": "failed", "step": "build"}\n')
            self.assertEqual([s["step"] for s in found["node"]["steps"]], ["build"])
            self.assertEqual(found["node"]["result"], "failed")
            self.assertEqual(found["curl"], [f"PUT {STORE}/out", f"PUT {STORE}/log", f"PUT {STORE}/status"])
            self.assertRegex(found["log"], r"(?m)^NODE \d\d:\d\d:\d\d result failed step=build$")
        self.setUp()
        with self.subTest(defect="rc 139 after the ok line"):
            found = self.run_job(FAKE_BUILD_RC="139")
            self.assertEqual(found["status"], '{"result": "ok"}\n')
            self.assertEqual(found["node"]["steps"][0]["rc"], 139)
        self.setUp()
        with self.subTest(defect="0009.exr missing"):
            found = self.run_job(FAKE_MISSING="0009.exr")
            self.assertEqual(found["status"], '{"result": "failed", "step": "render"}\n')
            self.assertEqual([s["step"] for s in found["node"]["steps"]], ["build", "render"])
        self.setUp()
        with self.subTest(defect="a job.env line that runs a command"):
            env = self.tmp / "job.env"
            env.write_text('WIDTH=640; touch "$WORK/hit"\nHEIGHT=360\nFIRST=8\nLAST=10\nSTAGE=render\n'
                           'SAMPLED="8 10"\n', encoding="utf-8")
            found = self.run_job(FAKE_ENV=str(env))
            self.assertEqual(found["status"], '{"result": "failed", "step": "build"}\n')
            self.assertFalse((found["work"] / "hit").exists())
        self.setUp()
        with self.subTest(defect="a marker with quotes and a backslash"):
            found = self.run_job(FAKE_MARKER='FACTORY build ok "q" \\x')
            self.assertEqual(found["node"]["steps"][0]["markers"], ['FACTORY build ok "q" \\x'])
            self.assertEqual(found["status"], '{"result": "ok"}\n')
        self.setUp()
        with self.subTest(defect="PUT out answered 403"):
            found = self.run_job(FAKE_PUT_OUT="403")
            self.assertEqual(found["status"], '{"result": "failed", "step": "upload"}\n')
            self.assertEqual(found["curl"], [f"PUT {STORE}/out", f"PUT {STORE}/log", f"PUT {STORE}/status"])
            self.assertRegex(found["log"], r"(?m)^NODE \d\d:\d\d:\d\d put out http=403$")

    def test_12_job_gets_each_asset_binary_of_links_before_the_build(self):
        (self.tmp / "fake/store").mkdir(parents=True)
        (self.tmp / "fake/store" / BIN).write_bytes(GLB)
        lacking = "c" * 64
        found = self.run_job(LINKS + "".join(f"GET {n} https://store.invalid/{n}\n" for n in (BIN, lacking)))
        self.assertEqual(found["curl"], [f"GET https://store.invalid/{BIN}", f"GET https://store.invalid/{lacking}",
                                         f"PUT {STORE}/out", f"PUT {STORE}/log", f"PUT {STORE}/status"])
        assets = found["work"] / "job/assets"
        self.assertEqual(sorted(p.name for p in assets.iterdir()), [f"{BIN}.glb"])
        self.assertEqual((assets / f"{BIN}.glb").read_bytes(), GLB)
        got = re.search(rf"(?m)^NODE \d\d:\d\d:\d\d get {BIN} http=200$", found["log"])
        lost = re.search(rf"(?m)^NODE \d\d:\d\d:\d\d get {lacking} http=404$", found["log"])
        build = re.search(r"(?m)^NODE \d\d:\d\d:\d\d step build ", found["log"])
        self.assertTrue(got and lost and build)
        self.assertLess(got.start(), build.start())
        self.assertLess(lost.start(), build.start())
        self.assertEqual(found["status"], '{"result": "ok"}\n')


def _job(unit: str, scripts: int = 1) -> dict:
    level = golden.UNITS[unit]
    sampled = [level["first"], (level["first"] + level["last"]) // 2, level["last"]] if level["stage"] == "render" else []
    return {"hash": "0" * 64, "shot": "Q1-2" if unit == "two-shot/Q1-2" else "Q1-1", "stage": level["stage"],
            "fps": level["fps"], "first": level["first"], "last": level["last"], "width": level["width"],
            "height": level["height"], "scripts": [f"scripts/{i:02d}.py" for i in range(scripts)], "assets": {},
            "sampled": sampled}


class BuildTest(EngineCase):
    def build(self, scripts: list, job: dict, script: Path = ENGINE / "build.py", binaries: dict | None = None):
        """Runs `script` under the fake `unreal`; returns the fake, the work root and the kinds alive at gc.collect."""
        work = self.tmp / f"work{len(list(self.tmp.iterdir()))}"
        (work / "job/scripts").mkdir(parents=True)
        (work / "job/assets").mkdir()
        for name, data in (binaries or {}).items():
            (work / "job/assets" / name).write_bytes(data)
        for i, path in enumerate(scripts):
            shutil.copy(path, work / f"job/scripts/{i:02d}.py")
        (work / "job/job.json").write_text(json.dumps(dict(job, scripts=[f"scripts/{i:02d}.py" for i in range(len(scripts))])),
                                           encoding="utf-8")
        spec = importlib.util.spec_from_file_location("unreal", FIX / "unreal.py")
        fake = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fake)
        alive = []
        real = gc.collect

        def collect(*args, **kwargs):
            count = real(*args, **kwargs)
            fake.CALLS.append("gc.collect")
            alive.extend(kind for kind, ref in fake.HANDED if ref() is not None)
            return count

        saved = sys.modules.get("unreal"), os.environ.get("FACTORY_WORK")
        sys.modules["unreal"] = fake
        os.environ["FACTORY_WORK"] = str(work)
        gc.collect = collect
        try:
            runpy.run_path(str(script), run_name="__main__")
        finally:
            gc.collect = real
            if saved[0] is None:
                sys.modules.pop("unreal", None)
            else:
                sys.modules["unreal"] = saved[0]
            if saved[1] is None:
                os.environ.pop("FACTORY_WORK", None)
            else:
                os.environ["FACTORY_WORK"] = saved[1]
        return fake, work, alive

    def test_6_build_under_the_fake_engine(self):
        fake, work, alive = self.build([GOLDEN / "lit-render/scene.py"], _job("lit-render"))
        self.assertEqual((work / "out/scene.json").read_bytes(), (FIX / "lit-render/scene.json").read_bytes())
        env = (work / "Job/job.env").read_text(encoding="utf-8")
        self.assertIn("WIDTH=640\n", env)
        self.assertEqual(env, 'WIDTH=640\nHEIGHT=360\nFIRST=0\nLAST=23\nSTAGE=render\nSAMPLED="0 11 23"\n')
        self.assertEqual(fake.CALLS[-2:], ["gc.collect", "log FACTORY build ok"])
        self.assertEqual(alive, [])
        self.assertEqual(sorted({kind for kind, _ in fake.HANDED}), ["job", "queue", "sequence", "subsystem"])
        manifests = work / "Job/Saved/MovieRenderPipeline"
        rerender = json.loads((manifests / "Rerender.utxt").read_text(encoding="utf-8"))["jobs"]
        main = json.loads((manifests / "QueueManifest.utxt").read_text(encoding="utf-8"))["jobs"]
        self.assertEqual([j["settings"]["MoviePipelineOutputSetting"]["custom_start_frame"] for j in rerender],
                         [0, 11, 23])
        self.assertNotIn("MoviePipelineImageSequenceOutput_EXR", rerender[0]["settings"])
        self.assertEqual(len(main), 1)
        self.assertIn("MoviePipelineImageSequenceOutput_EXR", main[0]["settings"])
        self.assertEqual(main[0]["settings"]["MoviePipelineOutputSetting"]["file_name_format"], "{frame_number}")
        self.assertEqual(main[0]["settings"]["MoviePipelineConsoleVariableSetting"],
                         {"r.AntiAliasingMethod": 4.0, "r.DynamicGlobalIlluminationMethod": 1.0,
                          "r.ReflectionMethod": 0.0})
        self.assertIn("LevelSequence.set_playback_end 24", fake.CALLS)
        self.assertIn("MovieSceneCameraCutSection.set_camera_binding_id binding:camera", fake.CALLS)

        with self.subTest(defect="no del line"):
            text = (ENGINE / "build.py").read_text(encoding="utf-8")
            kept = "".join(l for l in text.splitlines(True) if not l.startswith("del "))
            self.assertNotEqual(kept, text)
            copy_path = self.tmp / "build.py"
            copy_path.write_text(kept, encoding="utf-8")
            fake, work, alive = self.build([GOLDEN / "lit-render/scene.py"], _job("lit-render"), copy_path)
            self.assertNotEqual(alive, [])
        with self.subTest(defect="a raising second script"):
            fake, work, alive = self.build([FIX / "scripts/first.py", FIX / "scripts/raises.py"], _job("lit-render"))
            self.assertEqual(fake.CALLS[-1], "log FACTORY build failed scripts/01.py")
            self.assertNotIn("log FACTORY build ok", fake.CALLS)
            self.assertIn("log_error RuntimeError: scene script failed", fake.CALLS)
            self.assertFalse((work / "out/scene.json").exists())
            self.assertFalse((work / "Job/job.env").exists())
            self.assertEqual(alive, [])
        with self.subTest(defect="no camera"):
            fake, work, alive = self.build([FIX / "scripts/nocamera.py"], _job("lit-render"))
            self.assertEqual(fake.CALLS[-1], "log FACTORY build failed no actor labelled camera")
            self.assertFalse((work / "out/scene.json").exists())
        with self.subTest(twin="two scripts in order"):
            fake, work, alive = self.build([FIX / "scripts/first.py", FIX / "scripts/second.py"], _job("lit-render"))
            labels = [c for c in fake.CALLS if c.startswith("Actor.set_actor_label")]
            self.assertEqual(labels, ["Actor.set_actor_label first", "Actor.set_actor_label second",
                                      "Actor.set_actor_label camera"])
            self.assertEqual(fake.CALLS[-1], "log FACTORY build ok")
        for unit in ("prop-drop", "two-shot/Q1-1", "two-shot/Q1-2"):
            with self.subTest(twin=unit):
                fake, work, alive = self.build([GOLDEN / unit / "scene.py"], _job(unit))
                self.assertEqual(fake.CALLS[-2:], ["gc.collect", "log FACTORY build ok"])
                self.assertEqual(alive, [])
                self.assertEqual(scenestate.state(json.loads((work / "out/scene.json").read_text(encoding="utf-8")),
                                                  _job(unit), golden.UNITS[unit])["shot"], _job(unit)["shot"])

    def test_9_build_places_the_binary_of_a_job_asset(self):
        job = dict(_job("lit-render"), assets={"wren": BIN})
        fake, work, alive = self.build([GOLDEN / "lit-render/scene.py"], job, binaries={f"{BIN}.glb": GLB})
        self.assertEqual(fake.CALLS[-2:], ["gc.collect", "log FACTORY build ok"])
        self.assertEqual(alive, [])
        self.assertIn(f"AssetTools.import_asset_tasks {work}/job/assets/{BIN}.glb /Game/Job/Assets wren True True True",
                      fake.CALLS)
        labels = [c for c in fake.CALLS if c.startswith("Actor.set_actor_label")]
        self.assertLess(labels.index("Actor.set_actor_label wren"), labels.index("Actor.set_actor_label camera"))
        scene = json.loads((work / "out/scene.json").read_text(encoding="utf-8"))
        row = next(a for a in scene["actors"] if a["label"] == "wren")
        self.assertEqual((row["class"], row["location"], row["rotation"]),
                         ("StaticMeshActor", [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]))
        state = scenestate.state(scene, job, golden.UNITS["lit-render"], {"wren": WREN})
        wren = next(a for a in state["actors"] if a["id"] == "wren")
        self.assertEqual((wren["kind"], wren["asset"], wren["sha256"]), ("character", "asset:wren", BIN))
        fixture = json.loads((FIX / "lit-render/scene.json").read_text(encoding="utf-8"))
        before = scenestate.state(fixture, _job("lit-render"), golden.UNITS["lit-render"])
        self.assertEqual([a for a in state["actors"] if a["id"] != "wren"], before["actors"])

    def test_10_build_refuses_a_binary_it_cannot_place(self):
        other = b"not a glb"
        defects = {
            "the asset store gave no binary": (BIN, {}),
            "the binary's sha256 is not its state's": (BIN, {f"{BIN}.glb": b"glTF bo"}),
            "the engine placed no actor for its binary": (hashlib.sha256(other).hexdigest(),
                                                          {f"{hashlib.sha256(other).hexdigest()}.glb": other}),
        }
        for message, (digest, binaries) in defects.items():
            with self.subTest(defect=message):
                job = dict(_job("lit-render"), assets={"wren": digest})
                fake, work, alive = self.build([GOLDEN / "lit-render/scene.py"], job, binaries=binaries)
                self.assertEqual(fake.CALLS[-1], f"log FACTORY build failed asset:wren: {message}")
                self.assertNotIn("log FACTORY build ok", fake.CALLS)
                self.assertFalse((work / "out/scene.json").exists())
                self.assertFalse([c for c in fake.CALLS if c.startswith("Actor.set_actor_label")])
                self.assertEqual(alive, [])

    def test_11_a_scene_of_primitives_imports_nothing(self):
        fake, work, alive = self.build([GOLDEN / "lit-render/scene.py"], _job("lit-render"),
                                       binaries={f"{BIN}.glb": GLB})
        self.assertEqual((work / "out/scene.json").read_bytes(), (FIX / "lit-render/scene.json").read_bytes())
        self.assertFalse([c for c in fake.CALLS if "import" in c or "spawn_actor_from_object" in c])


class StateTest(unittest.TestCase):
    def scene(self) -> dict:
        return json.loads((FIX / "lit-render/scene.json").read_text(encoding="utf-8"))

    def test_7_state_of_the_lit_render_scene(self):
        level, job = golden.UNITS["lit-render"], _job("lit-render")
        state = scenestate.state(self.scene(), job, level)
        self.assertEqual(sorted(state), sorted(golden.STATE_KEYS))
        self.assertEqual(len(state), 14)
        self.assertEqual(golden._state_problems("lit-render", "state.json", state), [])
        problems = []
        reproduce._check_state("state.json", state, problems)
        self.assertEqual(problems, [])
        self.assertEqual({row["actor"]: row["box"] for row in state["framing"] if row["frame"] == 0}, MEASURED)
        self.assertEqual(len(state["framing"]), 3 * 24)
        ball = next(a for a in state["actors"] if a["id"] == "ball")
        self.assertEqual(ball, {"id": "ball", "kind": "prop", "asset": None, "sha256": None, "scale": [1, 1, 1],
                                "height_m": 1, "start": {"location": [0, 0, 0.5], "rotation": [0, 0, 0]},
                                "end": {"location": [0, 0, 0.5], "rotation": [0, 0, 0]}})
        self.assertEqual(state["lights"], [
            {"id": "fill", "kind": "point", "intensity": 50, "unit": "cd", "color": [1, 1, 1], "temperature_k": 4000},
            {"id": "key", "kind": "directional", "intensity": 10, "unit": "lux", "color": [1, 1, 1],
             "temperature_k": 6500}])
        self.assertEqual(state["camera"], {"focal_mm": {"min": 35, "max": 35}, "sensor_mm": [36, 20.25],
                                           "start": {"location": [-5, 0, 1.5], "rotation": [0, -10, 0]},
                                           "end": {"location": [-5, 0, 1.5], "rotation": [0, -10, 0]}})
        self.assertEqual((state["sun"], state["motion"], state["lipsync"], state["caches"], state["line_flips"]),
                         ({"elevation_deg": 45, "azimuth_deg": 210}, [], [], [], []))

        def raises(scene, job_, level_, message):
            with self.assertRaises(scenestate.SceneError) as caught:
                scenestate.state(scene, job_, level_)
            self.assertEqual(str(caught.exception), message)

        scene = self.scene()
        del scene["camera"]
        raises(scene, job, level, "no actor labelled camera")
        scene = self.scene()
        ball = next(a for a in scene["actors"] if a["label"] == "ball")
        ball["location"][0] = ball["bounds"]["origin"][0] = -1000.0
        state = scenestate.state(scene, job, level)
        self.assertEqual(sorted({row["actor"] for row in state["framing"]}), ["block", "floor"])
        raises(self.scene(), dict(job, assets={"fox": "b" * 64}), level,
               "scene names asset:fox; it has no locked state")
        raises(self.scene(), job, None, "actor ball has no kind: no level names it")
        scene = self.scene()
        next(l for l in scene["lights"] if l["label"] == "fill")["units"] = "UNITLESS"
        raises(scene, job, level, "light fill intensity is in UNITLESS, not lux or cd")

    def test_8_rounding_makes_equal_states(self):
        level, job = golden.UNITS["lit-render"], _job("lit-render")

        def dumps(x=None, yaw=None, focal=None):
            scene = self.scene()
            ball = next(a for a in scene["actors"] if a["label"] == "ball")
            if x is not None:
                ball["location"][0] = ball["bounds"]["origin"][0] = x
            if yaw is not None:
                ball["rotation"][2] = yaw
            if focal is not None:
                scene["camera"]["focal_mm"] = focal
            return scenestate.dumps(scenestate.state(scene, job, level))

        at = json.loads(dumps(12345.6789))
        self.assertEqual(next(a for a in at["actors"] if a["id"] == "ball")["start"]["location"][0], 123.4568)
        self.assertEqual(dumps(12345.67894), dumps(12345.6789))
        self.assertNotEqual(dumps(12345.6989), dumps(12345.6789))
        self.assertEqual(dumps(yaw=180.0), dumps(yaw=-180.0))
        self.assertIn('"rotation": [\n          0,\n          0,\n          180\n', dumps(yaw=-180.0))
        text = dumps(focal=35.0)
        self.assertIn('"max": 35,', text)
        self.assertEqual(type(json.loads(text)["camera"]["focal_mm"]["max"]), int)
        self.assertEqual(scenestate.dumps({"b": 1, "a": [1]}), '{\n  "a": [\n    1\n  ],\n  "b": 1\n}\n')


if __name__ == "__main__":
    unittest.main()
