"""Structure: every episode has its format's beats, in order, inside the runtime, and every later file
accounts for every earlier one, and every bible sheet has its shape (brief sections 10 and 16).

Rule ids: premise (1), beats (2), durations (3), scene-beats (4), script-scenes (5), lines (6),
sequences (7), shots (8), edit-list (9), depends (10), intent-packet (11), sheets (12). Findings in JSON
files are on line 1. Times in `reel/edit-list.json` are seconds, like every other duration in an
episode. The edit list's `bible` is the id `A-<n>` of the amendment the reel was cut from; from the reel
on, an episode has its `intent-packet.json`. A shot needs the canon sheet and the model sheet of each of
its characters and of its set. A sheet is checked wherever it exists: a canon sheet opens with a
description and holds one line `origin: source ...` or `origin: house ...`, a canon character sheet
holds `## Behaviour` and `## Motivation` with text, and a model sheet cites a reference image as a link
to `media:<id>`.
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import frontmatter, roles
from ..frontmatter import as_list
from ..lint import Finding, LintError, link_targets, markdown_lines, read_json, read_text, walk
from .references import MEDIA

KIT = Path(__file__).resolve().parents[2]  # the kit that contains the running lib/
EPISODE = re.compile(r"^episodes/([^/]+)/(.+)$")
SEQUENCE = re.compile(r"^sequences/([^/]+)/sequence\.json$")
SHOT = re.compile(r"^sequences/([^/]+)/shots/[^/]+\.json$")
SHEET = re.compile(r"^bible/(canon|style)/(characters|places)/[^/]+\.md$")
HEADING = re.compile(r"^#{1,6}(\s|$)")
BEAT_HEADING = re.compile(r"^## B(\d+) (\S.*?)\s*$")
SCENE_HEADING = re.compile(r"^## S(\d+) (\S.*?)\s*$")
DURATION = re.compile(r"^duration:\s*(\d+(?:\.\d+)?)$")
BEATS_LINE = re.compile(r"^beats:(.*)$")
SPOKEN = re.compile(r"^L(\d+) ([^:]+):(.*)$")
RANGE = re.compile(r"(\d+)-(\d+)")
AMENDMENT_ID = re.compile(r"A-[0-9]+")
SHOT_KEYS = ("id", "scene", "beat", "duration", "characters", "set", "camera", "action")
LINE_KEYS = ("id", "scene", "speaker", "text")
PACKET_KEYS = {"hook", "spoilers", "cta", "tone"}
CHARACTER_HEADINGS = ("## Behaviour", "## Motivation")


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _positive(value) -> bool:
    return _number(value) and value > 0


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


class _Formats:
    """The kit's formats, loaded on first use."""

    def __init__(self):
        self._docs = None

    def get(self, format_id: str):
        if self._docs is None:
            try:
                self._docs = roles.load_formats(KIT / "formats")
            except ValueError as e:
                raise LintError(f"cannot load the kit's formats: {e}") from None
        doc = self._docs.get(format_id)
        if doc is None:
            return None
        meta = doc["meta"]
        lo, hi = map(int, RANGE.fullmatch(meta["runtime"]).groups())
        return {"id": format_id, "beats": as_list(meta["beats"]), "runtime": (lo, hi),
                "skips": as_list(meta["skips"])}


def _sections(rel: str, text: str, heading: re.Pattern, letter: str, rule: str, numbered: bool = True):
    """Level-2 headings `## <letter><n> <name>` as [(line, id, name, body)], body the non-empty lines
    under the heading as [(line, stripped text)], and the findings for malformed headings and, when
    `numbered`, for ids not numbered from 1."""
    sections, found = [], []
    for n, line in enumerate(text.splitlines(), 1):
        if line.startswith("## "):
            m = heading.match(line)
            if not m:
                found.append(Finding(rel, n, rule, f"heading is not '## {letter}<n> <name>'"))
                sections.append(None)  # body lines until the next heading belong to nobody
                continue
            expected = sum(1 for s in sections if s) + 1
            if numbered and int(m.group(1)) != expected:
                found.append(Finding(rel, n, rule, f"{letter}{m.group(1)} should be {letter}{expected}"))
            sections.append((n, f"{letter}{m.group(1)}", m.group(2), []))
        elif sections and sections[-1] and line.strip():
            sections[-1][3].append((n, line.strip()))
    return [s for s in sections if s], found


def _premise(rel: str, text: str, formats: _Formats):
    try:
        meta, _ = frontmatter.parse(text)
    except frontmatter.FrontMatterError as e:
        return None, [Finding(rel, e.line, "premise", f"front matter: {e}")]
    found = [Finding(rel, 1, "premise", f"front matter has no {key}")
             for key in ("format", "logline") if not meta.get(key)]
    if not meta.get("format"):
        return None, found
    fmt = formats.get(meta["format"])
    if fmt is None:
        line = next((n for n, s in enumerate(text.splitlines(), 1) if s.startswith("format:")), 1)
        found.append(Finding(rel, line, "premise", f"format {meta['format']!r} is not in the kit's formats/"))
    return fmt, found


def _beats(rel: str, text: str, fmt):
    sections, found = _sections(rel, text, BEAT_HEADING, "B", "beats")
    if fmt:
        names = fmt["beats"]
        for i, (line, bid, name, _) in enumerate(sections):
            if i >= len(names):
                found.append(Finding(rel, line, "beats", f"{bid} {name}: {fmt['id']} has {len(names)} beats"))
            elif name != names[i]:
                found.append(Finding(rel, line, "beats",
                                     f"{bid} is {name!r}; beat {i + 1} of {fmt['id']} is {names[i]!r}"))
        found += [Finding(rel, 1, "beats", f"beat {i + 1} of {fmt['id']}, {names[i]!r}, is missing")
                  for i in range(len(sections), len(names))]
    total, complete = 0.0, True
    for line, bid, _, body in sections:
        first = body[0] if body else (line, "")
        m = DURATION.match(first[1])
        if not m or float(m.group(1)) <= 0:
            found.append(Finding(rel, first[0], "durations",
                                 f"{bid}: first line is not 'duration: <seconds above zero>'"))
            complete = False
        else:
            total += float(m.group(1))
    if fmt and sections and complete:
        lo, hi = fmt["runtime"]
        if not lo <= total <= hi:
            found.append(Finding(rel, 1, "durations",
                                 f"durations sum to {total:g} s, outside the runtime {lo}-{hi} s"))
    return [bid for _, bid, _, _ in sections], found


def _scene_beats(rel: str, sections, beat_ids):
    """Rule 4 on `beats:` lines. Returns ({scene id: [beat ids]}, findings)."""
    scene_beats, found, covered = {}, [], set()
    for line, sid, _, body in sections:
        first = body[0] if body else (line, "")
        m = BEATS_LINE.match(first[1])
        named = as_list(m.group(1)) if m else []
        if not named:
            found.append(Finding(rel, first[0], "scene-beats", f"{sid}: first line is not 'beats: B<n>, ...'"))
        for bid in named:
            if beat_ids is not None and bid not in beat_ids:
                found.append(Finding(rel, first[0], "scene-beats", f"{sid} names {bid}, not a beat in beats.md"))
        scene_beats.setdefault(sid, named)
        covered.update(named)
    if beat_ids is not None:
        found += [Finding(rel, 1, "scene-beats", f"{bid} is named by no scene")
                  for bid in beat_ids if bid not in covered]
    return scene_beats, found


def _spoken(rel: str, text: str):
    """Spoken lines of the script as {id: (line, scene, speaker, text)} and findings for repeated ids."""
    spoken, found, scene = {}, [], None
    for n, line in enumerate(text.splitlines(), 1):
        if line.startswith("## "):
            m = SCENE_HEADING.match(line)
            scene = f"S{m.group(1)}" if m else None
            continue
        m = SPOKEN.match(line)
        if not m:
            continue
        lid = f"L{m.group(1)}"
        if lid in spoken:
            found.append(Finding(rel, n, "lines", f"{lid} is repeated (first on line {spoken[lid][0]})"))
        else:
            spoken[lid] = (n, scene, m.group(2).strip(), m.group(3).strip())
    return spoken, found


def _lines(rel: str, data, spoken, script_rel: str):
    if not isinstance(data, list) or not all(
            isinstance(e, dict) and all(isinstance(e.get(k), str) for k in LINE_KEYS) for e in data):
        return [Finding(rel, 1, "lines", "not a list of {id, scene, speaker, text} strings")]
    found, entries = [], {}
    for e in data:
        if e["id"] in entries:
            found.append(Finding(rel, 1, "lines", f"{e['id']} is repeated"))
        else:
            entries[e["id"]] = e
    if spoken is None:
        return found
    for lid in sorted(spoken):
        n, scene, speaker, text = spoken[lid]
        e = entries.get(lid)
        if e is None:
            found.append(Finding(rel, 1, "lines", f"{lid} ({script_rel}:{n}) is missing"))
            continue
        for key, want in (("scene", scene), ("speaker", speaker), ("text", text)):
            if e[key] != want:
                found.append(Finding(rel, 1, "lines", f"{lid}: {key} is {e[key]!r}, {script_rel}:{n} has {want!r}"))
    found += [Finding(rel, 1, "lines", f"{lid} is not a line of {script_rel}")
              for lid in sorted(entries) if lid not in spoken]
    return found


def _shot(rel: str, data, scenes, scene_beats, files):
    if not isinstance(data, dict):
        return None, [Finding(rel, 1, "shots", "not a JSON object")]
    found = [Finding(rel, 1, "shots", f"missing key {key!r}") for key in SHOT_KEYS if key not in data]
    scene, beat = data.get("scene"), data.get("beat")
    if scenes is not None and "scene" in data and scene not in scenes:
        found.append(Finding(rel, 1, "shots", f"scene {scene!r} is not a scene of its sequence"))
    elif scene_beats is not None and scene in scene_beats and "beat" in data \
            and beat not in scene_beats[scene]:
        found.append(Finding(rel, 1, "shots", f"beat {beat!r} is not a beat of {scene}"))
    if "duration" in data and not _positive(data["duration"]):
        found.append(Finding(rel, 1, "shots", "duration is not a number above zero"))
    characters = data.get("characters", [])
    if not isinstance(characters, list) or not all(isinstance(c, str) for c in characters):
        found.append(Finding(rel, 1, "shots", "characters is not a list of ids"))
    else:
        for c in characters:
            for kind in ("canon", "style"):
                if f"bible/{kind}/characters/{c}.md" not in files:
                    found.append(Finding(rel, 1, "shots", f"character {c} has no bible/{kind}/characters/{c}.md"))
                    break
    if "set" in data:
        for kind in ("canon", "style"):
            if not isinstance(data["set"], str) or f"bible/{kind}/places/{data['set']}.md" not in files:
                found.append(Finding(rel, 1, "shots", f"set {data['set']!r} has no file under bible/{kind}/places/"))
                break
    return data.get("id") if isinstance(data.get("id"), str) else None, found


def _edit_list(rel: str, data, shot_ids, beat_ids, fmt, files):
    if not isinstance(data, dict) or not _positive(data.get("fps")) or not isinstance(data.get("events"), list):
        return [Finding(rel, 1, "edit-list", "not {fps, events: [...]} with fps above zero")]
    found, end, total, seen, complete = [], 0.0, 0.0, set(), True
    bible = data.get("bible")
    if not isinstance(bible, str) or not AMENDMENT_ID.fullmatch(bible):
        found.append(Finding(rel, 1, "edit-list", "bible is not the id A-<n> of an amendment"))
    elif f"bible/amendments/{bible}.json" not in files:
        found.append(Finding(rel, 1, "edit-list", f"bible {bible}: bible/amendments/{bible}.json is missing"))
    for i, e in enumerate(data["events"], 1):
        if not isinstance(e, dict) or not isinstance(e.get("shot"), str) or not isinstance(e.get("beat"), str) \
                or not _number(e.get("start")) or not _positive(e.get("duration")):
            found.append(Finding(rel, 1, "edit-list", f"event {i} is not {{shot, beat, start, duration}} "
                                                      "with a duration above zero"))
            complete = False
            continue
        if abs(e["start"] - end) > 1e-6:
            kind = "gap" if e["start"] > end else "overlap"
            found.append(Finding(rel, 1, "edit-list",
                                 f"event {i} starts at {e['start']:g} s, after the previous end {end:g} s: {kind}"))
        end = e["start"] + e["duration"]
        total += e["duration"]
        seen.add(e["beat"])
        if e["shot"] not in shot_ids:
            found.append(Finding(rel, 1, "edit-list", f"event {i}: shot {e['shot']} does not exist"))
    if beat_ids is not None:
        found += [Finding(rel, 1, "edit-list", f"{bid} appears in no event") for bid in beat_ids if bid not in seen]
    if fmt and complete:
        lo, hi = fmt["runtime"]
        if not lo <= total <= hi:
            found.append(Finding(rel, 1, "edit-list", f"total {total:g} s is outside the runtime {lo}-{hi} s"))
    return found


def _packet(rel: str, data):
    if not isinstance(data, dict) or set(data) != PACKET_KEYS or not (_text(data["hook"]) and _text(data["tone"])) \
            or not all(isinstance(data[k], list) and all(_text(s) for s in data[k]) for k in ("spoilers", "cta")):
        return [Finding(rel, 1, "intent-packet", "not {hook, spoilers: [...], cta: [...], tone}")]
    return []


def _canon_sheet(rel: str, text: str, character: bool) -> list[Finding]:
    found, described, origin, section, sections = [], False, None, None, {}
    for n, line in enumerate(text.splitlines(), 1):
        if line.startswith("## "):
            section = line.rstrip()
            sections.setdefault(section, [n, False])
            continue
        is_origin = line.startswith("origin:")
        if is_origin and origin is not None:
            found.append(Finding(rel, n, "sheets", f"origin is repeated (first on line {origin})"))
        elif is_origin:
            origin = n
            words = line[len("origin:"):].split()
            if not words or words[0] not in ("source", "house"):
                found.append(Finding(rel, n, "sheets", "origin is not 'source' or 'house'"))
        if not line.strip() or HEADING.match(line):
            continue
        if section is None:
            described = described or not is_origin
        else:
            sections[section][1] = True
    if not described:
        found.append(Finding(rel, 1, "sheets", "no description before the first '## ' heading"))
    if origin is None:
        found.append(Finding(rel, 1, "sheets", "no line 'origin: source' or 'origin: house'"))
    for heading in CHARACTER_HEADINGS if character else ():
        if heading not in sections:
            found.append(Finding(rel, 1, "sheets", f"no '{heading}' heading"))
        elif not sections[heading][1]:
            found.append(Finding(rel, sections[heading][0], "sheets", f"'{heading}' has no text under it"))
    return found


def _model_sheet(rel: str, text: str) -> list[Finding]:
    if any(MEDIA.match(target) for _, line in markdown_lines(text) for target in link_targets(line)):
        return []
    return [Finding(rel, 1, "sheets", "no reference image cited as a link to media:<id>")]


def _episode(root: Path, ep: str, have: set[str], files: set[str], formats: _Formats) -> list[Finding]:
    def p(name):
        return f"{ep}/{name}"

    found = []

    def needs(name, dep):
        if dep not in have:
            found.append(Finding(p(name), 1, "depends", f"{p(dep)} is missing"))

    fmt = beat_ids = outline_beats = script_ids = script_beats = spoken = None
    if "premise.md" in have:
        fmt, f = _premise(p("premise.md"), read_text(root, p("premise.md")), formats)
        found += f
    if "beats.md" in have:
        needs("beats.md", "premise.md")
        beat_ids, f = _beats(p("beats.md"), read_text(root, p("beats.md")), fmt)
        found += f
    outline_ids = None
    if "outline.md" in have:
        needs("outline.md", "beats.md")
        sections, f = _sections(p("outline.md"), read_text(root, p("outline.md")), SCENE_HEADING, "S",
                                "scene-beats")
        outline_ids = [sid for _, sid, _, _ in sections]
        outline_beats, g = _scene_beats(p("outline.md"), sections, beat_ids)
        found += f + g
    script_lines = {}
    if "script.md" in have:
        rel = p("script.md")
        needs("script.md", "beats.md")
        outline_required = bool(fmt) and "outline" not in fmt["skips"]
        if outline_required:
            needs("script.md", "outline.md")
        needs("script.md", "lines.json")
        text = read_text(root, rel)
        # with an outline, the outline's ids in order are the numbering rule
        sections, f = _sections(rel, text, SCENE_HEADING, "S", "script-scenes", outline_ids is None)
        found += f
        script_ids = [sid for _, sid, _, _ in sections]
        script_lines = {sid: line for line, sid, _, _ in sections}
        if outline_ids is not None:
            for i, (line, sid, _, _) in enumerate(sections):
                want = outline_ids[i] if i < len(outline_ids) else None
                if sid != want:
                    found.append(Finding(rel, line, "script-scenes",
                                         f"{sid}: scene {i + 1} of outline.md is {want or 'absent'}"))
            found += [Finding(rel, 1, "script-scenes", f"{outline_ids[i]} of outline.md is missing")
                      for i in range(len(sections), len(outline_ids))]
        elif not outline_required:
            script_beats, f = _scene_beats(rel, sections, beat_ids)
            found += f
        spoken, f = _spoken(rel, text)
        found += f
    if "lines.json" in have:
        needs("lines.json", "script.md")
        _, data = read_json(root, p("lines.json"))
        found += _lines(p("lines.json"), data, spoken, p("script.md"))

    seq_scenes = {}
    for name in sorted(n for n in have if SEQUENCE.match(n)):
        rel = p(name)
        needs(name, "script.md")
        _, data = read_json(root, rel)
        scenes = data.get("scenes") if isinstance(data, dict) else None
        if not isinstance(scenes, list) or not all(isinstance(s, str) for s in scenes):
            found.append(Finding(rel, 1, "sequences", "not {id, scenes: [S<n>, ...]}"))
            scenes = []
        seq_scenes[SEQUENCE.match(name).group(1)] = scenes
        if script_ids is None:
            continue
        for sid in scenes:
            if sid not in script_ids:
                found.append(Finding(rel, 1, "sequences", f"{sid} is not a scene of script.md"))
    if seq_scenes and script_ids is not None:
        owner = {}
        for q in sorted(seq_scenes):
            for sid in seq_scenes[q]:
                if sid in owner:
                    found.append(Finding(p(f"sequences/{q}/sequence.json"), 1, "sequences",
                                         f"{sid} is also in {owner[sid]}"))
                elif sid in script_ids:
                    owner[sid] = p(f"sequences/{q}/sequence.json")
        found += [Finding(p("script.md"), script_lines[sid], "sequences", f"{sid} is in no sequence")
                  for sid in script_ids if sid not in owner]

    scene_beats = outline_beats if outline_beats is not None else script_beats
    shot_ids = set()
    shots = sorted(n for n in have if SHOT.match(n))
    for name in shots:
        q = SHOT.match(name).group(1)
        needs(name, f"sequences/{q}/sequence.json")
        _, data = read_json(root, p(name))
        sid, f = _shot(p(name), data, seq_scenes.get(q), scene_beats, files)
        found += f
        if sid:
            shot_ids.add(sid)

    if "intent-packet.json" in have:
        _, data = read_json(root, p("intent-packet.json"))
        found += _packet(p("intent-packet.json"), data)
    if "reel/edit-list.json" in have:
        needs("reel/edit-list.json", "intent-packet.json")
        if not shots:
            found.append(Finding(p("reel/edit-list.json"), 1, "depends", f"{p('sequences')}/*/shots/ has no shot"))
        _, data = read_json(root, p("reel/edit-list.json"))
        found += _edit_list(p("reel/edit-list.json"), data, shot_ids, beat_ids, fmt, files)
    return found


def check(root: Path) -> list[Finding]:
    files = walk(root)
    episodes = {}
    for rel in files:
        m = EPISODE.match(rel)
        if m:
            episodes.setdefault(m.group(1), set()).add(m.group(2))
    formats, found, file_set = _Formats(), [], set(files)
    for ep in sorted(episodes):
        found += _episode(root, f"episodes/{ep}", episodes[ep], file_set, formats)
    for rel in sorted(files):
        m = SHEET.match(rel)
        if m and m.group(1) == "canon":
            found += _canon_sheet(rel, read_text(root, rel), m.group(2) == "characters")
        elif m:
            found += _model_sheet(rel, read_text(root, rel))
    return found
