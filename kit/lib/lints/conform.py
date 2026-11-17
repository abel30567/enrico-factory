"""Conform: integrated loudness, true peak, captions aligned with the mix, and the conformed edit list
equal to the locked one, event by event (brief sections 6 and 16).

Rule ids: depends, edit-list, captions, mix, loudness, true-peak, shots. Any file under
`production/<ep>/conform/` puts the episode in scope, a scene script, the plan and a request included;
it then needs `conform/captions.json`, `conform/edit-list.json`, `conform/mix.json`,
`episodes/<ep>/lines.json` and the locked `episodes/<ep>/reel/edit-list.json`.
While `conform/mix.json` does not exist, `conform/mix-plan.json` or a request `conform/requests/<id>.json`
holds back every finding of the episode, and neither is opened.
`stems` is a list of `{name, sha256}` with distinct names among dialogue, music and effects, dialogue
among them.
`master` is a sha256 of 64 lowercase hex digits.
When `production/<ep>/shots.json` exists, each line of the mix is heard inside its frames, from the
smallest `first` to the largest `last`.
Findings are on line 1. Frames are whole numbers from the episode's first frame, 0. Edit list values
are compared with `==`, so equal seconds at one `fps` are equal frames and nothing is converted.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..lint import Finding, files, read_json

CONFORM = re.compile(r"^production/([^/]+)/conform/")
HOLD = re.compile(r"^production/[^/]+/conform/(mix-plan\.json|requests/[^/]+\.json)$")
SHA256 = re.compile(r"[0-9a-f]{64}")
STEMS = ("dialogue", "music", "effects")
EVENT_KEYS = ("shot", "beat", "start", "duration")
LOUDNESS_MIN, LOUDNESS_MAX = -15, -13  # LUFS, placeholder (brief section 16)
TRUE_PEAK_MAX = -1  # dBTP, placeholder (brief section 16)
CUE_FRAMES = 2  # placeholder (brief section 16)


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _frame(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _sha256(value) -> bool:
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def _span(item, *strings) -> bool:
    return isinstance(item, dict) and all(isinstance(item.get(k), str) for k in strings) \
        and _frame(item.get("start")) and _frame(item.get("end")) and 0 <= item["start"] <= item["end"]


def _edit_list(data) -> bool:
    return isinstance(data, dict) and _number(data.get("fps")) and data["fps"] > 0 \
        and isinstance(data.get("events"), list)


def _compare(rel: str, reel: str, got, want) -> list[Finding]:
    def finding(message):
        return Finding(rel, 1, "edit-list", message)

    if not _edit_list(got):
        return [finding("not {fps, events: [...]} with fps above zero")]
    if not _edit_list(want):
        return [finding(f"{reel} is not {{fps, events: [...]}}: nothing to compare")]
    found = []
    if got["fps"] != want["fps"]:
        found.append(finding(f"fps is {got['fps']!r}, {reel} has {want['fps']!r}"))
    for i, (e, w) in enumerate(zip(got["events"], want["events"]), 1):
        if not isinstance(e, dict) or any(k not in e for k in EVENT_KEYS):
            found.append(finding(f"event {i} is not {{shot, beat, start, duration}}"))
            continue
        w = w if isinstance(w, dict) else {}
        found += [finding(f"event {i}: {k} is {e[k]!r}, {reel} has {w.get(k)!r}")
                  for k in EVENT_KEYS if e[k] != w.get(k)]
    if len(got["events"]) != len(want["events"]):
        found.append(finding(f"events count is {len(got['events'])}, {reel} has {len(want['events'])}"))
    return found


def _stems(rel: str, stems) -> list[Finding]:
    def finding(message):
        return Finding(rel, 1, "mix", message)

    if not isinstance(stems, list):
        return [finding("stems is not a list of {name, sha256}")]
    found, seen = [], set()
    for i, stem in enumerate(stems, 1):
        if not (isinstance(stem, dict) and isinstance(stem.get("name"), str) and _sha256(stem.get("sha256"))):
            found.append(finding(f"stems entry {i} is not {{name, sha256}} with a sha256 of 64 lowercase hex digits"))
        elif stem["name"] not in STEMS:
            found.append(finding(f"stems entry {i}: {stem['name']!r} is not dialogue, music or effects"))
        elif stem["name"] in seen:
            found.append(finding(f"stems entry {i}: {stem['name']} is listed already"))
        else:
            seen.add(stem["name"])
    if "dialogue" not in seen:
        found.append(finding("stems has no dialogue"))
    return found


def _shots(rel: str, lines, shots_rel: str, shots) -> list[Finding]:
    """Each line of the mix heard outside the frames of `shots_rel`, rule `shots`."""
    if not isinstance(shots, list) or not shots or not all(
            isinstance(row, dict) and _frame(row.get("first")) and _frame(row.get("last"))
            and 0 <= row["first"] <= row["last"] for row in shots):
        return [Finding(rel, 1, "shots", f"{shots_rel} is not a list of {{shot, first, last}} "
                                         "with frames 0 <= first <= last")]
    first, last = min(row["first"] for row in shots), max(row["last"] for row in shots)
    return [Finding(rel, 1, "shots", f"lines entry {i}: {entry['line']} is heard at {entry['start']}-{entry['end']}, "
                                     f"outside frames {first}-{last} of {shots_rel}")
            for i, entry in enumerate(lines, 1)
            if _span(entry, "line") and (entry["start"] < first or entry["end"] > last)]


def _mix(rel: str, data, ids):
    """The mix's findings and the frames each line is heard at, or None when no cue can be compared."""
    if not isinstance(data, dict):
        return [Finding(rel, 1, "mix", "not {loudness_lufs, true_peak_dbtp, lines, stems, master}")], None
    found = []
    loudness, peak = data.get("loudness_lufs"), data.get("true_peak_dbtp")
    if not _number(loudness):
        found.append(Finding(rel, 1, "mix", "loudness_lufs is not a number"))
    elif not LOUDNESS_MIN <= loudness <= LOUDNESS_MAX:
        found.append(Finding(rel, 1, "loudness", f"integrated loudness {loudness:g} LUFS is outside "
                                                 f"{LOUDNESS_MIN:g} to {LOUDNESS_MAX:g} LUFS"))
    if not _number(peak):
        found.append(Finding(rel, 1, "mix", "true_peak_dbtp is not a number"))
    elif peak > TRUE_PEAK_MAX:
        found.append(Finding(rel, 1, "true-peak", f"true peak {peak:g} dBTP is above {TRUE_PEAK_MAX:g} dBTP"))
    found += _stems(rel, data.get("stems"))
    if not _sha256(data.get("master")):
        found.append(Finding(rel, 1, "mix", "master is not a sha256 of 64 lowercase hex digits"))
    if not isinstance(data.get("lines"), list):
        return found + [Finding(rel, 1, "mix", "lines is not a list of {line, start, end}")], None
    heard = {}
    for i, entry in enumerate(data["lines"], 1):
        if not _span(entry, "line"):
            found.append(Finding(rel, 1, "mix", f"lines entry {i} is not {{line, start, end}} "
                                                "with frames 0 <= start <= end"))
        elif entry["line"] in heard:
            found.append(Finding(rel, 1, "mix", f"lines entry {i}: {entry['line']} is heard already"))
        else:
            heard[entry["line"]] = (entry["start"], entry["end"])
    found += [Finding(rel, 1, "mix", f"lines has no entry for {lid}") for lid in ids or () if lid not in heard]
    return found, heard


def _captions(rel: str, data, ids, heard, lines_rel: str, mix_rel: str) -> list[Finding]:
    def finding(message):
        return Finding(rel, 1, "captions", message)

    found = []
    if ids is None:
        found.append(finding(f"{lines_rel} is not a list of {{id, scene, speaker, text}}"))
    if not isinstance(data, list):
        return found + [finding("not a list of {line, text, start, end}")]
    cues = {}  # line -> (cue number, start, end)
    for i, cue in enumerate(data, 1):
        if not _span(cue, "line", "text"):
            found.append(finding(f"cue {i} is not {{line, text, start, end}} with frames 0 <= start <= end"))
        elif cue["line"] in cues:
            found.append(finding(f"cue {i}: {cue['line']} has a cue already (cue {cues[cue['line']][0]})"))
        else:
            cues[cue["line"]] = (i, cue["start"], cue["end"])
            if ids is not None and cue["line"] not in ids:
                found.append(finding(f"cue {i}: {cue['line']} is not a line of {lines_rel}"))
    for lid in ids or ():
        if lid not in cues:
            found.append(finding(f"{lid} has no cue"))
        elif heard is not None and lid in heard:
            (_, start, end), (h_start, h_end) = cues[lid], heard[lid]
            if abs(start - h_start) > CUE_FRAMES or abs(end - h_end) > CUE_FRAMES:
                found.append(finding(f"{lid}: cue {start}-{end} is more than {CUE_FRAMES} frames "
                                     f"from {h_start}-{h_end} in {mix_rel}"))
    return found


def _line_ids(data):
    if not isinstance(data, list) or not all(isinstance(e, dict) and isinstance(e.get("id"), str) for e in data):
        return None
    return [e["id"] for e in data]


def _episode(root: Path, ep: str, have: set[str]) -> list[Finding]:
    conform = {name: f"production/{ep}/conform/{name}.json" for name in ("captions", "edit-list", "mix")}
    mine = sorted(rel for rel in have if rel.startswith(f"production/{ep}/conform/"))
    if conform["mix"] not in have and any(HOLD.match(rel) for rel in mine):
        return []
    lines_rel, reel = f"episodes/{ep}/lines.json", f"episodes/{ep}/reel/edit-list.json"
    needed = [*conform.values(), lines_rel, reel]
    missing = [rel for rel in needed if rel not in have]
    if missing:
        on = next((rel for rel in conform.values() if rel in have), mine[0])
        return [Finding(on, 1, "depends", f"{rel} is missing") for rel in missing]
    data = {rel: read_json(root, rel)[1] for rel in needed}
    ids = _line_ids(data[lines_rel])
    found = _compare(conform["edit-list"], reel, data[conform["edit-list"]], data[reel])
    mix_found, heard = _mix(conform["mix"], data[conform["mix"]], ids)
    found += mix_found
    shots_rel, mix = f"production/{ep}/shots.json", data[conform["mix"]]
    if shots_rel in have and isinstance(mix, dict) and isinstance(mix.get("lines"), list):
        found += _shots(conform["mix"], mix["lines"], shots_rel, read_json(root, shots_rel)[1])
    found += _captions(conform["captions"], data[conform["captions"]], ids, heard, lines_rel, conform["mix"])
    return found


def check(root: Path) -> list[Finding]:
    have = {rel.as_posix() for rel in files(root)}
    episodes = sorted({m.group(1) for rel in have for m in [CONFORM.match(rel)] if m and m.group(1) != "assets"})
    return [f for ep in episodes for f in _episode(Path(root), ep, have)]
