"""The golden level `prop-drop`:
a cube that falls from 3 m onto the floor, 48 frames (`assets`, `simulation`).

Builds `LEVEL` in the level the job has open, nothing else: it saves no level, reads and writes no
file and sets no seed. The job sets the resolution and the frame range. `LEVEL` equals
`golden.UNITS["prop-drop"]` in `lib/golden.py`.
"""

import unreal

LEVEL = {
    "stage": "simulation",
    "fps": 24,
    "first": 0,
    "last": 47,
    "width": 640,
    "height": 360,
    "lumen": False,
    "tsr": False,
    "primitives": [
        {"id": "floor", "shape": "plane", "kind": "set", "location": [0, 0, 0],
         "rotation": [0, 0, 0], "scale": [10, 10, 1], "physics": False},
        {"id": "prop", "shape": "cube", "kind": "prop", "location": [0, 0, 3],
         "rotation": [0, 0, 0], "scale": [0.5, 0.5, 0.5], "physics": True},
    ],
    "lights": [
        {"id": "key", "kind": "directional", "intensity": 10, "unit": "lux", "color": [1, 1, 1],
         "temperature_k": 6500, "location": [0, 0, 5], "rotation": [0, -45, 30]},
    ],
    "camera": {"focal_mm": 35, "sensor_mm": [36, 20.25], "location": [-6, 0, 1.5], "rotation": [0, -10, 0]},
}

MESHES = {"cube": "/Engine/BasicShapes/Cube.Cube", "sphere": "/Engine/BasicShapes/Sphere.Sphere",
          "plane": "/Engine/BasicShapes/Plane.Plane"}
LIGHTS = {"directional": unreal.DirectionalLight, "point": unreal.PointLight}
ACTORS = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def place(cls, label, location, rotation):
    """Spawn `cls` in the open level at a location in metres and a `[roll, pitch, yaw]` in degrees."""
    x, y, z = (100 * v for v in location)
    roll, pitch, yaw = rotation
    actor = ACTORS.spawn_actor_from_class(cls, unreal.Vector(x, y, z), unreal.Rotator(roll, pitch, yaw))
    actor.set_actor_label(label)
    return actor


for item in LEVEL["primitives"]:
    actor = place(unreal.StaticMeshActor, item["id"], item["location"], item["rotation"])
    actor.set_actor_scale3d(unreal.Vector(*item["scale"]))
    mesh = actor.static_mesh_component
    mesh.set_static_mesh(unreal.load_asset(MESHES[item["shape"]]))
    if item["physics"]:
        mesh.set_mobility(unreal.ComponentMobility.MOVABLE)
    mesh.set_simulate_physics(item["physics"])

for item in LEVEL["lights"]:
    light = place(LIGHTS[item["kind"]], item["id"], item["location"], item["rotation"]).light_component
    if item["unit"] == "cd":
        light.set_editor_property("intensity_units", unreal.LightUnits.CANDELAS)
    light.set_intensity(item["intensity"])
    light.set_light_color(unreal.LinearColor(*item["color"], 1))
    light.set_use_temperature(True)
    light.set_temperature(item["temperature_k"])

view = LEVEL["camera"]
lens = place(unreal.CineCameraActor, "camera", view["location"], view["rotation"]).get_cine_camera_component()
width, height = view["sensor_mm"]
lens.set_editor_property("filmback", unreal.CameraFilmbackSettings(sensor_width=width, sensor_height=height))
lens.set_editor_property("current_focal_length", view["focal_mm"])

unreal.SystemLibrary.execute_console_command(None, f"r.DynamicGlobalIlluminationMethod {int(LEVEL['lumen'])}")
unreal.SystemLibrary.execute_console_command(None, f"r.AntiAliasingMethod {4 if LEVEL['tsr'] else 0}")
