"""Holds the actor subsystem, then raises."""
import unreal

ACTORS = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
raise RuntimeError("scene script failed")
