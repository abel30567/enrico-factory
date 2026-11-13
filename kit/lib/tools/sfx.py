"""The tool `sfx`: a sound effect from the prompt of a request, by the vendor of kit/lib/audioapi.py."""

from kit.lib import audioapi

RESOURCE = "gen.sfx"
REASONS = audioapi.REASONS


def run(call):
    secret = audioapi.key(call, RESOURCE)
    body = {"model_id": "eleven_text_to_sound_v2", "text": audioapi.prompt(call)}
    return audioapi.post(call, secret, "/v1/sound-generation", body, "sfx")
