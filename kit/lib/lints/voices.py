"""Voices: every stored voice is one the voice tool reads, and every speaker has one (brief section 16).

Rule ids: voice-file, speakers. A file under `bible/style/voices/` must be named
`<character>.json`, `<character>` of a-z, 0-9 and -, and hold exactly `{"voice_id": "<id>"}`, as
`kit/lib/tools/voice.py` reads it. Each speaker of an `episodes/<ep>/lines.json` needs
`bible/style/voices/<character>.json`, `<character>` the speaker trimmed and lower-cased, as `rights`
maps a speaker to its canon sheet; the missing voice is found on the voice file's path, the art
director's. A `lines.json` that cannot be read, does not parse or is not a list is skipped. Every finding
is on line 1. No stage lists this check yet.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..lint import Finding, files
from ..tools.voice import BAD_VOICE, VOICE_FILE, VOICE_ID
from .rights import LINES

VOICES = "bible/style/voices/"
NOT_A_VOICE_FILE = ("the voice tool reads only bible/style/voices/<character>.json, "
                    "<character> of a-z, 0-9 and -")


def _read(root: Path, rel: str):
    """The text of ``rel`` read as UTF-8, None when it cannot be read or decoded."""
    try:
        return (root / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _parse(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError, RecursionError):
        return None


def _voice_file(root: Path, rel: str) -> list[Finding]:
    if not VOICE_FILE.fullmatch(rel):
        return [Finding(rel, 1, "voice-file", NOT_A_VOICE_FILE)]
    stored = _parse(_read(root, rel))
    if (not isinstance(stored, dict) or list(stored) != ["voice_id"] or not isinstance(stored["voice_id"], str)
            or not VOICE_ID.fullmatch(stored["voice_id"])):
        return [Finding(rel, 1, "voice-file", BAD_VOICE)]
    return []


def _speakers(root: Path, rel: str, have: set[str]) -> list[Finding]:
    data = _parse(_read(root, rel))
    if not isinstance(data, list):
        return []
    found = []
    for speaker in sorted({e["speaker"] for e in data if isinstance(e, dict) and isinstance(e.get("speaker"), str)}):
        character = speaker.strip().lower()
        voice = f"{VOICES}{character}.json"
        if not VOICE_FILE.fullmatch(voice):
            found.append(Finding(rel, 1, "speakers", f"speaker {speaker} has no voice file name: "
                                                     f"'{character}' is not of a-z, 0-9 and -"))
        elif voice not in have:
            found.append(Finding(voice, 1, "speakers", f"speaker {speaker} of {rel} has no stored voice"))
    return found


def check(root: Path) -> list[Finding]:
    root = Path(root)
    have = {p.as_posix() for p in files(root)}
    found = []
    for rel in sorted(have):
        if rel.startswith(VOICES):
            found += _voice_file(root, rel)
        elif LINES.match(rel):
            found += _speakers(root, rel, have)
    return found
