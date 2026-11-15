"""Places a cube labelled `first`."""
import unreal

ACTORS = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
ACTORS.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(0, 0, 50), unreal.Rotator(0, 0, 0)).set_actor_label("first")
