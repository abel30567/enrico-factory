"""The tool `voice`: a character's line spoken in its stored voice, by the vendor of kit/lib/audioapi.py.

The line is the prompt of the request. The stored voice is the one input after the request whose path is
``bible/style/voices/<character>.json``, holding exactly ``{"voice_id": "<id>"}``.
"""

import json
import re

from kit.lib import audioapi
from kit.lib.tools import Failed

RESOURCE = "gen.voice"
NO_VOICE = "the inputs hold no one stored voice"
BAD_VOICE = 'the stored voice file is not {"voice_id": <id>}'
NO_SUCH_VOICE = "the vendor has no such voice"
REASONS = audioapi.REASONS + (NO_VOICE, BAD_VOICE, NO_SUCH_VOICE)

VOICE_FILE = re.compile(r"bible/style/voices/[a-z0-9][a-z0-9-]*\.json")
VOICE_ID = re.compile(r"[A-Za-z0-9]{1,64}")


def _voice_id(inputs):
    """The voice id of the one stored voice in ``inputs``; ``Failed`` before any call otherwise."""
    voices = [item for item in inputs if VOICE_FILE.fullmatch(item["path"])]
    if len(voices) != 1:
        raise Failed(NO_VOICE, usd=0, compute_min=0)
    try:
        stored = json.loads(voices[0]["text"])
    except (TypeError, ValueError):
        stored = None
    if (not isinstance(stored, dict) or list(stored) != ["voice_id"] or not isinstance(stored["voice_id"], str)
            or not VOICE_ID.fullmatch(stored["voice_id"])):
        raise Failed(BAD_VOICE, usd=0, compute_min=0)
    return stored["voice_id"]


def run(call):
    secret = audioapi.key(call, RESOURCE)
    text = audioapi.prompt(call)
    voice_id = _voice_id(call["inputs"][1:])
    body = {"model_id": "eleven_multilingual_v2", "text": text}
    return audioapi.post(call, secret, f"/v1/text-to-speech/{voice_id}", body, "voice",
                         {"voice_not_found": NO_SUCH_VOICE})
