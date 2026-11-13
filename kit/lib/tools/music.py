"""The tool `music`: instrumental music from the prompt of a request, by the vendor of kit/lib/audioapi.py.

The tool keeps no list of artists or songs: the vendor refuses a prompt that names one (``bad_prompt``).
"""

from kit.lib import audioapi

RESOURCE = "gen.music"
NAMES_A_WORK = "the vendor found an artist or a song in the prompt"
REASONS = audioapi.REASONS + (NAMES_A_WORK,)


def run(call):
    secret = audioapi.key(call, RESOURCE)
    body = {"force_instrumental": True, "model_id": "music_v1", "prompt": audioapi.prompt(call)}
    return audioapi.post(call, secret, "/v1/music", body, "music", {"bad_prompt": NAMES_A_WORK})
