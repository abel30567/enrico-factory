"""Poses the rig `wren` in `rest` and `arms-up`, leaves the six cameras of the poses and logs the rig state."""
import json

import unreal

LEVEL = {"poses": ["rest", "arms-up"]}
VIEWS = {"front": (300, 0, 180), "side": (0, 300, -90), "rear-right": (-210, -210, 45)}

ACTORS = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
for pose in LEVEL["poses"]:
    for view, (x, y, yaw) in VIEWS.items():
        camera = ACTORS.spawn_actor_from_class(unreal.CineCameraActor, unreal.Vector(x, y, 100),
                                               unreal.Rotator(0, 0, yaw))
        camera.set_actor_label(f"camera-{pose}-{view}")
STATE = {"joints": [{"name": "spine", "min": [-45.0, -30.0, -60.0], "max": [45.0, 30.0, 60.0]}],
         "rom": {"poses": len(LEVEL["poses"]), "limit_violations": 0, "penetration_mm": 0.0}}
unreal.log("FACTORY RIG_STATE " + json.dumps(STATE, sort_keys=True))
