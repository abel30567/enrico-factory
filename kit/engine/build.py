"""The build step of a render job, run by the editor with `-ExecutePythonScript` (docs/design/render-job.md).

Reads `job/job.json`, makes a new level, runs each scene script in its own namespace, in order, then writes
the raw scene to `out/scene.json` (centimetres and degrees) and the six values `job.sh` reads to
`Job/job.env`, makes the level sequence with a camera cut to the actor `camera`, the Movie Render Queue
queues (the re-render queue first, saved as `Rerender.utxt`, at `render`; the main queue last, as
`QueueManifest.utxt`) and saves them. It ends clean, as measured on #115: every scene namespace and engine
object is dropped and collected, and only then is the ok marker logged; the job trusts that line and
the saved files, not the exit code. Imports only `unreal` and the standard library.
"""

import gc
import json
import os
import runpy
import shutil
import traceback

import unreal

ROOT = os.environ.get("FACTORY_WORK", "/work")
MANIFESTS = os.path.join(ROOT, "Job", "Saved", "MovieRenderPipeline")
LEVEL = "/Game/Job/L_job"
SEQUENCE = "/Game/Job/Seq_job"
CVARS = ("r.DynamicGlobalIlluminationMethod", "r.ReflectionMethod", "r.AntiAliasingMethod")
RENDER = {"spatial_samples": 1, "temporal_samples": 1, "warmup_frames": 0}


def _vector(v):
    return [float(v.x), float(v.y), float(v.z)]


def _rotation(r):
    return [float(r.roll), float(r.pitch), float(r.yaw)]


def _scene(actors, camera):
    """The raw scene: every actor but the camera and the lights, the camera, the lights, the console variables."""
    scene = {"actors": [], "lights": [], "cvars": {}, "render": dict(RENDER)}
    for actor in actors:
        if actor is camera:
            continue
        row = {"label": actor.get_actor_label(), "class": actor.get_class().get_name(),
               "location": _vector(actor.get_actor_location()), "rotation": _rotation(actor.get_actor_rotation())}
        if isinstance(actor, unreal.Light):
            light = actor.light_component
            color = light.get_light_color()
            units = None
            if not isinstance(actor, unreal.DirectionalLight):
                units = light.get_editor_property("intensity_units").name
            temperature = None
            if light.get_editor_property("use_temperature"):
                temperature = float(light.get_editor_property("temperature"))
            row.update(intensity=float(light.get_editor_property("intensity")), units=units,
                       color=[float(color.r), float(color.g), float(color.b)], temperature_k=temperature)
            scene["lights"].append(row)
        else:
            origin, extent = actor.get_actor_bounds(False)
            row.update(scale=_vector(actor.get_actor_scale3d()),
                       bounds={"origin": _vector(origin), "extent": _vector(extent)})
            scene["actors"].append(row)
    lens = camera.get_cine_camera_component()
    filmback = lens.get_editor_property("filmback")
    scene["camera"] = {"label": camera.get_actor_label(), "location": _vector(camera.get_actor_location()),
                       "rotation": _rotation(camera.get_actor_rotation()),
                       "sensor_mm": [float(filmback.sensor_width), float(filmback.sensor_height)],
                       "focal_mm": float(lens.get_editor_property("current_focal_length"))}
    for name in CVARS:
        scene["cvars"][name] = int(unreal.SystemLibrary.get_console_variable_int_value(name))
    return scene


def _sequence(job, camera, held):
    """The level sequence `SEQUENCE`: frames `first` to `last + 1` at `fps`, a camera cut to `camera`."""
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    name = SEQUENCE.rsplit("/", 1)
    seq = held["sequence"] = tools.create_asset(name[1], name[0], unreal.LevelSequence,
                                                unreal.LevelSequenceFactoryNew())
    seq.set_display_rate(unreal.FrameRate(job["fps"], 1))
    seq.set_playback_start(job["first"])
    seq.set_playback_end(job["last"] + 1)
    binding = seq.add_possessable(camera)
    cut = seq.add_track(unreal.MovieSceneCameraCutTrack).add_section()
    cut.set_range(job["first"], job["last"] + 1)
    cut.set_camera_binding_id(seq.get_binding_id(binding))
    unreal.EditorAssetLibrary.save_loaded_asset(seq)


def _configure(job, entry, scene, output, exr):
    """One queue job's settings: output directory, size, frame names, PNG (and EXR), samples, console variables."""
    entry.map = unreal.SoftObjectPath(f"{LEVEL}.{LEVEL.rsplit('/', 1)[1]}")
    entry.sequence = unreal.SoftObjectPath(f"{SEQUENCE}.{SEQUENCE.rsplit('/', 1)[1]}")
    config = entry.get_configuration()
    out = config.find_or_add_setting_by_class(unreal.MoviePipelineOutputSetting)
    out.output_directory = unreal.DirectoryPath(output)
    out.output_resolution = unreal.IntPoint(job["width"], job["height"])
    out.file_name_format = "{frame_number}"
    config.find_or_add_setting_by_class(unreal.MoviePipelineDeferredPassBase)
    config.find_or_add_setting_by_class(unreal.MoviePipelineImageSequenceOutput_PNG)
    if exr:
        config.find_or_add_setting_by_class(unreal.MoviePipelineImageSequenceOutput_EXR)
    samples = config.find_or_add_setting_by_class(unreal.MoviePipelineAntiAliasingSetting)
    samples.spatial_sample_count = scene["render"]["spatial_samples"]
    samples.temporal_sample_count = scene["render"]["temporal_samples"]
    samples.engine_warm_up_count = scene["render"]["warmup_frames"]
    cvars = config.find_or_add_setting_by_class(unreal.MoviePipelineConsoleVariableSetting)
    for name in CVARS:
        cvars.add_or_update_console_variable(name, float(scene["cvars"][name]))
    return out


def _queues(job, scene, held):
    """The re-render queue at `render` (one job per sampled frame, `Rerender.utxt`), then the main queue."""
    queue = held["queue"] = held["queues"].get_queue()
    if job["stage"] == "render" and job["sampled"]:
        for old in queue.get_jobs():
            queue.delete_job(old)
        for frame in job["sampled"]:
            entry = queue.allocate_new_job(unreal.MoviePipelineExecutorJob)
            entry.job_name = f"rerender-{frame}"
            out = _configure(job, entry, scene, ROOT + "/out/rerender", False)
            out.use_custom_playback_range = True
            out.custom_start_frame = frame
            out.custom_end_frame = frame + 1
        unreal.MoviePipelineEditorLibrary.save_queue_to_manifest_file(queue)
        shutil.copyfile(os.path.join(MANIFESTS, "QueueManifest.utxt"), os.path.join(MANIFESTS, "Rerender.utxt"))
    for old in queue.get_jobs():
        queue.delete_job(old)
    entry = queue.allocate_new_job(unreal.MoviePipelineExecutorJob)
    entry.job_name = "job"
    _configure(job, entry, scene, ROOT + "/out/frames", True)
    unreal.MoviePipelineEditorLibrary.save_queue_to_manifest_file(queue)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _build(namespaces, held):
    """Runs the job; returns None when everything is saved, else what failed."""
    with open(os.path.join(ROOT, "job", "job.json"), encoding="utf-8") as handle:
        job = json.load(handle)
    held["levels"] = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    held["levels"].new_level(LEVEL)
    for script in job["scripts"]:
        try:
            namespaces.append(runpy.run_path(os.path.join(ROOT, "job", script)))
        except BaseException:
            unreal.log_error(traceback.format_exc())
            return script
    held["actors"] = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actors = held["actors"].get_all_level_actors()
    cameras = [a for a in actors if a.get_actor_label() == "camera"]
    if not cameras:
        return "no actor labelled camera"
    scene = _scene(actors, cameras[0])
    sampled = " ".join(str(int(f)) for f in job["sampled"])
    _write(os.path.join(ROOT, "out", "scene.json"), json.dumps(scene, sort_keys=True, indent=2) + "\n")
    _write(os.path.join(ROOT, "Job", "job.env"),
           f"WIDTH={int(job['width'])}\nHEIGHT={int(job['height'])}\nFIRST={int(job['first'])}\n"
           f"LAST={int(job['last'])}\nSTAGE={job['stage']}\nSAMPLED=\"{sampled}\"\n")
    _sequence(job, cameras[0], held)
    held["queues"] = unreal.get_editor_subsystem(unreal.MoviePipelineQueueSubsystem)
    _queues(job, scene, held)
    held["levels"].save_current_level()
    return None


NAMESPACES = []
HELD = {}
try:
    FAILED = _build(NAMESPACES, HELD)
except Exception:
    unreal.log_error(traceback.format_exc())
    FAILED = "engine"
del NAMESPACES, HELD
gc.collect()
if FAILED:
    unreal.log(f"FACTORY build failed {FAILED}")
else:
    unreal.log("FACTORY build ok")
