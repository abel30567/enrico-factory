"""A fake `unreal` module for tests: the names the golden scenes and `kit/engine/build.py` use, nothing else.

Every call appends one line to `CALLS`. Objects of the kinds in `TRACKED` (subsystems, the queue, its jobs,
the level sequence) are recorded in `HANDED` as weak references, so a test can tell which are still alive.
The fake holds no strong reference to them. Bounds: origin the actor's location, extent 50 times its scale
(`z` 0 for the plane mesh), 0 for lights and cameras. Console variables start at 0; a local light's units
start `UNITLESS`. The root of the saved files is `FACTORY_WORK`, as in `build.py`.
"""

import json
import os
import weakref

CALLS = []
HANDED = []
WORLD = []
CVARS = {}
LEVELS = []
SAVED = []


def _call(name, *args):
    CALLS.append(" ".join([name] + [str(a) for a in args]))


def _hand(kind, obj):
    HANDED.append((kind, weakref.ref(obj)))
    return obj


def log(text):
    _call("log", text)


def log_warning(text):
    _call("log_warning", text)


def log_error(text):
    _call("log_error", text.splitlines()[-1] if text.strip() else text)


class _Enum:
    def __init__(self, owner, name):
        self.owner, self.name = owner, name

    def __repr__(self):
        return f"{self.owner}.{self.name}"


class ComponentMobility:
    STATIC = _Enum("ComponentMobility", "STATIC")
    STATIONARY = _Enum("ComponentMobility", "STATIONARY")
    MOVABLE = _Enum("ComponentMobility", "MOVABLE")


class LightUnits:
    UNITLESS = _Enum("LightUnits", "UNITLESS")
    CANDELAS = _Enum("LightUnits", "CANDELAS")
    LUMENS = _Enum("LightUnits", "LUMENS")


class Vector:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)

    def __repr__(self):
        return f"Vector({self.x}, {self.y}, {self.z})"


class Rotator:
    def __init__(self, roll=0.0, pitch=0.0, yaw=0.0):
        self.roll, self.pitch, self.yaw = float(roll), float(pitch), float(yaw)

    def __repr__(self):
        return f"Rotator({self.roll}, {self.pitch}, {self.yaw})"


class LinearColor:
    def __init__(self, r=0.0, g=0.0, b=0.0, a=1.0):
        self.r, self.g, self.b, self.a = float(r), float(g), float(b), float(a)

    def __repr__(self):
        return f"LinearColor({self.r}, {self.g}, {self.b}, {self.a})"


class CameraFilmbackSettings:
    def __init__(self, sensor_width=36.0, sensor_height=24.0):
        self.sensor_width, self.sensor_height = float(sensor_width), float(sensor_height)

    def __repr__(self):
        return f"CameraFilmbackSettings({self.sensor_width}, {self.sensor_height})"


class IntPoint:
    def __init__(self, x=0, y=0):
        self.x, self.y = int(x), int(y)

    def __repr__(self):
        return f"IntPoint({self.x}, {self.y})"


class FrameRate:
    def __init__(self, numerator=24, denominator=1):
        self.numerator, self.denominator = int(numerator), int(denominator)

    def __repr__(self):
        return f"FrameRate({self.numerator}, {self.denominator})"


class SoftObjectPath:
    def __init__(self, path=""):
        self.path = str(path)

    def __repr__(self):
        return f"SoftObjectPath({self.path})"


class DirectoryPath:
    def __init__(self, path=""):
        self.path = str(path)

    def __repr__(self):
        return f"DirectoryPath({self.path})"


class StaticMesh:
    def __init__(self, path):
        self.path = path

    def __repr__(self):
        return f"StaticMesh({self.path})"


def load_asset(path):
    _call("load_asset", path)
    return StaticMesh(path)


class _Properties:
    """A component with editor properties: `set_editor_property` refuses a name it does not hold."""

    PROPERTIES = {}

    def __init__(self):
        self._props = dict(self.PROPERTIES)

    def set_editor_property(self, name, value):
        _call(f"{type(self).__name__}.set_editor_property", name, value)
        if name not in self._props:
            raise AttributeError(f"{type(self).__name__} has no property {name}")
        self._props[name] = value

    def get_editor_property(self, name):
        _call(f"{type(self).__name__}.get_editor_property", name)
        if name not in self._props:
            raise AttributeError(f"{type(self).__name__} has no property {name}")
        return self._props[name]


class StaticMeshComponent(_Properties):
    PROPERTIES = {"static_mesh": None, "mobility": ComponentMobility.STATIC, "simulate_physics": False}

    def set_static_mesh(self, mesh):
        _call("StaticMeshComponent.set_static_mesh", mesh)
        self._props["static_mesh"] = mesh
        return True

    def set_mobility(self, mobility):
        _call("StaticMeshComponent.set_mobility", mobility)
        self._props["mobility"] = mobility

    def set_simulate_physics(self, simulate):
        _call("StaticMeshComponent.set_simulate_physics", simulate)
        self._props["simulate_physics"] = bool(simulate)


class LightComponent(_Properties):
    PROPERTIES = {"intensity": 10.0, "light_color": LinearColor(1, 1, 1, 1), "use_temperature": False,
                  "temperature": 6500.0}

    def set_intensity(self, value):
        _call(f"{type(self).__name__}.set_intensity", value)
        self._props["intensity"] = float(value)

    def set_light_color(self, color):
        _call(f"{type(self).__name__}.set_light_color", color)
        self._props["light_color"] = color

    def get_light_color(self):
        _call(f"{type(self).__name__}.get_light_color")
        return self._props["light_color"]

    def set_use_temperature(self, value):
        _call(f"{type(self).__name__}.set_use_temperature", value)
        self._props["use_temperature"] = bool(value)

    def set_temperature(self, value):
        _call(f"{type(self).__name__}.set_temperature", value)
        self._props["temperature"] = float(value)


class DirectionalLightComponent(LightComponent):
    pass


class PointLightComponent(LightComponent):
    PROPERTIES = dict(LightComponent.PROPERTIES, intensity_units=LightUnits.UNITLESS, intensity=8.0)


class CineCameraComponent(_Properties):
    PROPERTIES = {"filmback": CameraFilmbackSettings(), "current_focal_length": 35.0}


class _Class:
    def __init__(self, name):
        self.name = name

    def get_name(self):
        _call("Class.get_name", self.name)
        return self.name


class Actor:
    def __init__(self, location, rotation):
        self._label = type(self).__name__
        self._location, self._rotation, self._scale = location, rotation, Vector(1, 1, 1)

    def set_actor_label(self, label):
        _call("Actor.set_actor_label", label)
        self._label = label

    def get_actor_label(self):
        _call("Actor.get_actor_label", self._label)
        return self._label

    def set_actor_scale3d(self, scale):
        _call("Actor.set_actor_scale3d", scale)
        self._scale = Vector(scale.x, scale.y, scale.z)

    def get_actor_scale3d(self):
        _call("Actor.get_actor_scale3d", self._label)
        return Vector(self._scale.x, self._scale.y, self._scale.z)

    def get_actor_location(self):
        _call("Actor.get_actor_location", self._label)
        return Vector(self._location.x, self._location.y, self._location.z)

    def get_actor_rotation(self):
        _call("Actor.get_actor_rotation", self._label)
        return Rotator(self._rotation.roll, self._rotation.pitch, self._rotation.yaw)

    def get_class(self):
        _call("Actor.get_class", self._label)
        return _Class(type(self).__name__)

    def _extent(self):
        return Vector(0, 0, 0)

    def get_actor_bounds(self, only_colliding_components, include_from_child_actors=False):
        _call("Actor.get_actor_bounds", self._label, only_colliding_components)
        return self.get_actor_location(), self._extent()


class StaticMeshActor(Actor):
    def __init__(self, location, rotation):
        super().__init__(location, rotation)
        self.static_mesh_component = StaticMeshComponent()

    def _extent(self):
        mesh = self.static_mesh_component._props["static_mesh"]
        if mesh is None:
            return Vector(0, 0, 0)
        flat = mesh.path.endswith("/Plane.Plane")
        return Vector(50 * self._scale.x, 50 * self._scale.y, 0 if flat else 50 * self._scale.z)


class Light(Actor):
    COMPONENT = LightComponent

    def __init__(self, location, rotation):
        super().__init__(location, rotation)
        self.light_component = self.COMPONENT()


class DirectionalLight(Light):
    COMPONENT = DirectionalLightComponent


class PointLight(Light):
    COMPONENT = PointLightComponent


class CineCameraActor(Actor):
    def __init__(self, location, rotation):
        super().__init__(location, rotation)
        self._camera = CineCameraComponent()

    def get_cine_camera_component(self):
        _call("CineCameraActor.get_cine_camera_component", self._label)
        return self._camera


class EditorActorSubsystem:
    def spawn_actor_from_class(self, actor_class, location, rotation=None, transient=False):
        _call("EditorActorSubsystem.spawn_actor_from_class", actor_class.__name__, location, rotation)
        actor = actor_class(Vector(location.x, location.y, location.z), rotation or Rotator())
        WORLD.append(actor)
        return actor

    def get_all_level_actors(self):
        _call("EditorActorSubsystem.get_all_level_actors")
        return list(WORLD)


class LevelEditorSubsystem:
    def new_level(self, asset_path):
        _call("LevelEditorSubsystem.new_level", asset_path)
        LEVELS.append(asset_path)
        WORLD.clear()
        return True

    def save_current_level(self):
        _call("LevelEditorSubsystem.save_current_level")
        return True


class MoviePipelineOutputSetting:
    pass


class MoviePipelineDeferredPassBase:
    pass


class MoviePipelineImageSequenceOutput_PNG:
    pass


class MoviePipelineImageSequenceOutput_EXR:
    pass


class MoviePipelineAntiAliasingSetting:
    pass


class MoviePipelineConsoleVariableSetting:
    pass


_SETTINGS = {
    MoviePipelineOutputSetting: ("output_directory", "output_resolution", "file_name_format",
                                 "use_custom_playback_range", "custom_start_frame", "custom_end_frame"),
    MoviePipelineDeferredPassBase: (),
    MoviePipelineImageSequenceOutput_PNG: (),
    MoviePipelineImageSequenceOutput_EXR: (),
    MoviePipelineAntiAliasingSetting: ("spatial_sample_count", "temporal_sample_count", "engine_warm_up_count"),
    MoviePipelineConsoleVariableSetting: (),
}


class _Setting:
    """One setting of a job's configuration: only the properties `_SETTINGS` names can be set."""

    def __init__(self, cls):
        object.__setattr__(self, "_class", cls)
        object.__setattr__(self, "_values", {})

    def __setattr__(self, name, value):
        _call(f"{self._class.__name__}.{name}", value)
        if name not in _SETTINGS[self._class]:
            raise AttributeError(f"{self._class.__name__} has no property {name}")
        self._values[name] = value

    def add_or_update_console_variable(self, name, value):
        _call(f"{self._class.__name__}.add_or_update_console_variable", name, value)
        if self._class is not MoviePipelineConsoleVariableSetting:
            raise AttributeError(f"{self._class.__name__} has no console variables")
        self._values[name] = float(value)
        return True

    def dump(self):
        return {k: repr(v) if not isinstance(v, (int, float, str, bool)) else v for k, v in self._values.items()}


class MoviePipelinePrimaryConfig:
    def __init__(self):
        self._settings = {}

    def find_or_add_setting_by_class(self, cls):
        _call("MoviePipelinePrimaryConfig.find_or_add_setting_by_class", cls.__name__)
        if cls not in _SETTINGS:
            raise TypeError(f"{cls} is not a setting")
        return self._settings.setdefault(cls, _Setting(cls))


class MoviePipelineExecutorJob:
    def __init__(self):
        self.job_name, self.map, self.sequence = "", None, None
        self._config = MoviePipelinePrimaryConfig()

    def __setattr__(self, name, value):
        if not name.startswith("_"):
            _call(f"MoviePipelineExecutorJob.{name}", value)
        object.__setattr__(self, name, value)

    def get_configuration(self):
        _call("MoviePipelineExecutorJob.get_configuration")
        return self._config


class MoviePipelineQueue:
    def __init__(self):
        self._jobs = []

    def get_jobs(self):
        _call("MoviePipelineQueue.get_jobs")
        return list(self._jobs)

    def delete_job(self, job):
        _call("MoviePipelineQueue.delete_job", job.job_name)
        self._jobs.remove(job)

    def allocate_new_job(self, job_class):
        _call("MoviePipelineQueue.allocate_new_job", job_class.__name__)
        job = _hand("job", job_class())
        self._jobs.append(job)
        return job


class MoviePipelineQueueSubsystem:
    def __init__(self):
        self._queue = None

    def get_queue(self):
        _call("MoviePipelineQueueSubsystem.get_queue")
        if self._queue is None:
            self._queue = _hand("queue", MoviePipelineQueue())
        return self._queue


class MoviePipelineEditorLibrary:
    @staticmethod
    def save_queue_to_manifest_file(queue):
        """Writes the queue as JSON to `<FACTORY_WORK>/Job/Saved/MovieRenderPipeline/QueueManifest.utxt`."""
        _call("MoviePipelineEditorLibrary.save_queue_to_manifest_file", len(queue._jobs))
        jobs = [{"job_name": j.job_name, "map": repr(j.map), "sequence": repr(j.sequence),
                 "settings": {cls.__name__: s.dump() for cls, s in sorted(j._config._settings.items(),
                                                                          key=lambda kv: kv[0].__name__)}}
                for j in queue._jobs]
        directory = os.path.join(os.environ.get("FACTORY_WORK", "/work"), "Job", "Saved", "MovieRenderPipeline")
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "QueueManifest.utxt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"jobs": jobs}, sort_keys=True, indent=2) + "\n")
        return path


def get_editor_subsystem(cls):
    _call("get_editor_subsystem", cls.__name__)
    if cls not in (EditorActorSubsystem, LevelEditorSubsystem, MoviePipelineQueueSubsystem):
        raise TypeError(f"{cls} is not an editor subsystem")
    return _hand("subsystem", cls())


class SystemLibrary:
    @staticmethod
    def execute_console_command(world, command):
        _call("SystemLibrary.execute_console_command", command)
        name, _, value = command.partition(" ")
        CVARS[name] = int(float(value))

    @staticmethod
    def get_console_variable_int_value(name):
        _call("SystemLibrary.get_console_variable_int_value", name)
        return CVARS.get(name, 0)


class MovieSceneCameraCutSection:
    def set_range(self, start, end):
        _call("MovieSceneCameraCutSection.set_range", start, end)

    def set_camera_binding_id(self, binding_id):
        _call("MovieSceneCameraCutSection.set_camera_binding_id", binding_id)


class MovieSceneCameraCutTrack:
    def add_section(self):
        _call("MovieSceneCameraCutTrack.add_section")
        return MovieSceneCameraCutSection()


class MovieSceneBindingProxy:
    def __init__(self, actor):
        self.actor_label = actor._label


class LevelSequence:
    def __init__(self, name, path):
        self.name, self.path = name, path

    def set_display_rate(self, rate):
        _call("LevelSequence.set_display_rate", rate)

    def set_playback_start(self, frame):
        _call("LevelSequence.set_playback_start", frame)

    def set_playback_end(self, frame):
        _call("LevelSequence.set_playback_end", frame)

    def add_possessable(self, actor):
        _call("LevelSequence.add_possessable", actor._label)
        return MovieSceneBindingProxy(actor)

    def add_track(self, track_class):
        _call("LevelSequence.add_track", track_class.__name__)
        return track_class()

    def get_binding_id(self, binding):
        _call("LevelSequence.get_binding_id", binding.actor_label)
        return f"binding:{binding.actor_label}"


class LevelSequenceFactoryNew:
    pass


class AssetTools:
    def create_asset(self, asset_name, package_path, asset_class, factory):
        _call("AssetTools.create_asset", asset_name, package_path, asset_class.__name__, type(factory).__name__)
        return _hand("sequence", asset_class(asset_name, package_path))


class AssetToolsHelpers:
    @staticmethod
    def get_asset_tools():
        _call("AssetToolsHelpers.get_asset_tools")
        return AssetTools()


class EditorAssetLibrary:
    @staticmethod
    def save_loaded_asset(asset, only_if_is_dirty=True):
        _call("EditorAssetLibrary.save_loaded_asset", f"{asset.path}/{asset.name}")
        SAVED.append(f"{asset.path}/{asset.name}")
        return True
