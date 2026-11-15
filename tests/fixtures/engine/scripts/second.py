"""Places a cube labelled `second` and the camera."""
import unreal

ACTORS = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
ACTORS.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(0, 100, 50), unreal.Rotator(0, 0, 0)).set_actor_label("second")
ACTORS.spawn_actor_from_class(unreal.CineCameraActor, unreal.Vector(-500, 0, 150), unreal.Rotator(0, 0, 0)).set_actor_label("camera")
