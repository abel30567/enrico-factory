"""The tickets ``factory tick`` files next: a locked ticket's next stage in the pipeline (system brief section 12).

A pure reading of a ``tickets.State``: nothing here writes to GitHub or git. ``due`` gives one entry per
ticket to file, ``{"from", "pull", "room", "stage", "unit", "title", "body", "labels"}``, sorted by the
locked ticket's number, the pipeline order of the stage, then the unit id.

For every ticket with a merged pull, every stage of its room whose ``after`` is the ticket's stage is
filed; a stage the format of the unit's episode skips is replaced by the stages after it. A stage with
the same unit type is filed for the same unit. A ``sequence`` stage after an ``episode`` stage is filed
once per ``episodes/<unit>/sequences/<Q>/sequence.json`` in the tree of the base, as unit ``<unit>/<Q>``.
An ``episode`` stage after a ``sequence`` stage is filed once for the episode, only when every ``<Q>`` of
the episode on the base has a locked ticket of the sequence stage with unit ``<episode>/<Q>``, and only
from the lowest numbered of those tickets (the lowest numbered locked ticket of each ``<Q>``); its body
has one ``Upstream:`` line per sequence. The body of a stage whose makers name ``media-wrangler`` also names the
tool and the expected cost, the settings ``MEDIA_TOOL``, ``MEDIA_USD`` and ``MEDIA_COMPUTE_MIN`` (defaults
``grok-imagine``, ``0.1`` and ``1``). The body of a stage of ``TOOLS`` whose makers name ``render-wrangler`` (and not
``media-wrangler``) names its tool and expected cost, the settings ``<TOOL>_USD`` and ``<TOOL>_COMPUTE_MIN`` (``<TOOL>``
the tool upper-cased, defaults in ``TOOLS``), the kind of an asset, the shot file on the base and the job's inputs
in order. The body of the ``conform`` stage, when its makers name ``render-wrangler``, names a tool, its expected cost
and its inputs for each request name of ``CONFORM_TOOLS`` under ``production/<ep>/conform/requests/``, with the same
settings. A ``rigging`` ticket is filed only for a unit with ``bible/canon/characters/<unit>.md`` on the base. A
``sequence`` stage after a ``shot`` stage is filed once for the sequence ``<ep>/<Q>`` of the shot, only when every
shot of the sequence in ``production/<ep>/shots.json`` on the base has a locked ticket of the shot stage, and only
from the lowest numbered of those tickets; its body has one ``Upstream:`` line per shot. An ``episode`` stage after
a ``shot`` stage is filed the same way once for the episode of the shot, from every shot of the episode in
``production/<ep>/shots.json`` on the base, one ``Upstream:`` line per shot in the order of that file. Any other
pair files nothing. A room, stage and unit that some ticket already has, open or closed, is never filed again.
While a tracked issue does not parse, nothing is filed.

``due`` also gives the two packets of brief section 3, as issues that are not tickets: the story intent packet
when a ``story/reel`` ticket locks, labelled ``room:post`` and ``room:distribution``, and the platform notes
when a ``distribution/window`` ticket locks, labelled ``room:story``. Each is filed once: an issue with its
title, open or closed, in ``State.others`` is never filed again.

A locked ``story/sequences`` ticket also files one ``story/style`` ticket for its episode, as unit ``<ep>``, when some
``<Q>`` of the episode on the base has no ``bible/style/keys/<ep>/<Q>.json`` on the base: its body has one
``Write the sequence key`` line per missing key, in ``<Q>`` order. It is filed once, like any ticket.

A locked ``story/reel`` ticket is the locked story package of its episode (brief section 6, ``intake``): when the
pipeline has ``production/intake``, the production stages after ``intake`` are filed from it. A ``shot`` stage is
filed once per shot of ``episodes/<ep>/reel/edit-list.json`` on the base, as unit ``<ep>/<shot>``; an ``asset``
stage once per character and set those shots' files ``episodes/<ep>/sequences/<Q>/shots/<shot>.json`` name on the
base, as unit ``<id>``. No ``intake`` ticket is filed and no file is written.
"""

from __future__ import annotations

import json
import re

from kit.lib import gitstate, production, tickets

_KEY = re.compile(r"[^A-Za-z0-9]+")

PACKAGE = ("story", "reel")
INTAKE = ("production", "intake")
RIGGING = ("production", "rigging")
EDIT_LIST = "episodes/{}/reel/edit-list.json"
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")

MEDIA_MAKER = "media-wrangler"
OPERATOR = "render-wrangler"
TOOLS = {"assets": ("model3d", "0.3", "1"), "rigging": ("rig", "0.55", "1"), "layout": ("render", "0.2", "20"),
         "animation": ("render", "0.2", "20"), "simulation": ("render", "0.2", "20"),
         "lighting": ("render", "0.2", "20"), "render": ("render", "0.2", "20")}
CONFORM = "conform"
CONFORM_TOOLS = (("voice-L<n>.json", "voice", "0.02", "1"), ("music-<id>.json", "music", "0.15", "1"),
                 ("sfx-<id>.json", "sfx", "0.1", "1"), ("mix.json", "conform", "0", "10"))
SEQUENCES = ("story", "sequences")
STYLE = ("story", "style")
SEQUENCE_KEY = "bible/style/keys/{}/{}.json"
SHOT_STAGES = ("layout", "animation", "simulation", "lighting", "render")
SCENES = ("layout", "animation", "simulation", "lighting")
CHARACTER = "bible/canon/characters/{}.md"
PLACE = "bible/canon/places/{}.md"
SHOT_FILE = re.compile(r"episodes/([^/]+)/sequences/([^/]+)/shots/([^/]+)\.json")

PACKETS = {("story", "reel"): "intent-packet", ("distribution", "window"): "platform-notes"}


def key(stage: str, unit: str) -> str:
    """``<stage>-<unit>``, every run of characters outside letters and digits one ``-``, upper-cased."""
    return _KEY.sub("-", f"{stage}-{unit}").upper()


def _next(p, room: str, stage: str, skips: list) -> list:
    """The entries of ``room`` after ``stage`` in pipeline order, a skipped one replaced by those after it."""
    found = []
    for entry in production.stages(p, room):
        if entry["after"] != stage:
            continue
        if entry["stage"] in skips:
            found += _next(p, room, entry["stage"], skips)
        else:
            found.append(entry)
    return found


def _lowest(state: tickets.State, room: str, stage: str, units: list, ticket: dict) -> list:
    """``(ticket, pull)`` of the lowest numbered locked ticket of ``room``/``stage`` for each of ``units``, in
    order; empty when one is missing or ``ticket`` is not the lowest numbered of them."""
    found = []
    for unit in units:
        number = next((n for n in sorted(state.tickets) if state.tickets[n]["room"] == room
                       and state.tickets[n]["stage"] == stage and state.tickets[n]["unit"] == unit
                       and tickets.locked_pull(state, n) is not None), None)
        if number is None:
            return []
        found.append((state.tickets[number], tickets.locked_pull(state, number)))
    if not found or min(t["number"] for t, _ in found) != ticket["number"]:
        return []
    return found


def _boards(p, root, state: tickets.State, ticket: dict) -> list:
    """``(ticket, pull)`` of the lowest numbered locked ticket of the ticket's stage for each sequence of its
    episode on the base, by unit id; empty when one is missing or ``ticket`` is not the lowest numbered."""
    episode = tickets.episode(ticket["unit"])
    units = [f"{episode}/{q}" for q in tickets.sequences(p, root, episode)]
    return _lowest(state, ticket["room"], ticket["stage"], units, ticket)


def _units(p, root, state: tickets.State, ticket: dict, pull: dict, upstream_unit: str, entry: dict) -> list:
    """``(unit, upstream (ticket, pull) pairs)`` of each ticket of ``entry`` to file from ``ticket``."""
    if entry["unit"] == upstream_unit:
        return [(ticket["unit"], [(ticket, pull)])]
    if upstream_unit == "episode" and entry["unit"] == "sequence":
        return [(f"{ticket['unit']}/{q}", [(ticket, pull)]) for q in tickets.sequences(p, root, ticket["unit"])]
    if upstream_unit == "sequence" and entry["unit"] == "episode":
        upstreams = _boards(p, root, state, ticket)
        return [(tickets.episode(ticket["unit"]), upstreams)] if upstreams else []
    sequence = tickets.shot_sequence(ticket["unit"])
    if upstream_unit == "shot" and entry["unit"] == "sequence" and sequence is not None:
        upstreams = _lowest(state, ticket["room"], ticket["stage"], tickets.shots(p, root, sequence), ticket)
        return [(sequence, upstreams)] if upstreams else []
    if upstream_unit == "shot" and entry["unit"] == "episode":
        episode = tickets.episode(ticket["unit"])
        upstreams = _lowest(state, ticket["room"], ticket["stage"], tickets.shots(p, root, episode), ticket)
        return [(episode, upstreams)] if upstreams else []
    return []


def _cost(tool: str, usd: str, compute_min: str) -> str:
    return f"Tool: {tool}. Expected cost: usd {usd}, compute_min {compute_min}."


def _on_base(p, root, path: str) -> bool:
    return gitstate.show(root, production.base(p), path) is not None


def _shot_file(p, root, unit: str, shot_unit: bool):
    """``(path, data)`` of the first shot file on the base, by path, that is the shot ``unit`` (a shot unit) or
    names the asset ``unit`` in its ``characters`` or as its ``set``; None when there is none."""
    sha = gitstate.head(root, production.base(p))
    out = gitstate.git(root, "--literal-pathspecs", "ls-tree", "-r", "-z", "--name-only", sha, "--", "episodes/")
    episode, _, shot = unit.partition("/")
    for path in sorted(out.split("\0")):
        m = SHOT_FILE.fullmatch(path)
        if m is None or (shot_unit and (m.group(1), m.group(3)) != (episode, shot)):
            continue
        data = _read(p, root, path)
        if not isinstance(data, dict):
            continue
        names = data.get("characters") if isinstance(data.get("characters"), list) else []
        if shot_unit or unit in names or data.get("set") == unit:
            return path, data
    return None


def _operator(p, root, stage: str, unit: str) -> list:
    """The lines of the block of a stage ``render-wrangler`` operates after its tool line, without the empty line."""
    shot_unit = stage in SHOT_STAGES
    found = _shot_file(p, root, unit, shot_unit)
    shot_line = f"Shot file: {found[0]}." if found else "Shot file: none on the base."
    if not shot_unit:
        sheet = next((path.format(unit) for path in (CHARACTER, PLACE) if _on_base(p, root, path.format(unit))), None)
        kind = {CHARACTER.format(unit): "character", PLACE.format(unit): "set"}.get(sheet, "prop")
        if stage == "rigging":
            inputs = [f'the request (refs ["asset:{unit}"])', f"production/assets/{unit}/assets/state.json"]
        else:
            inputs = ["the request", "media/manifest.tsv", *([sheet] if sheet else [])]
        return [f"Kind: {kind}. {shot_line}", f"Inputs, in this order: {', '.join(inputs)}."]
    index = SHOT_STAGES.index(stage)
    inputs = ["the request", *(f"production/{unit}/{s}/scene.py" for s in SCENES[:index + 1])]
    if found:
        inputs.append(found[0])
    inputs.append(f"production/{tickets.episode(unit)}/shots.json")
    if index:
        inputs.append(f"production/{unit}/{SHOT_STAGES[index - 1]}/state.json")
    ids = []
    if found:
        names = found[1].get("characters") if isinstance(found[1].get("characters"), list) else []
        for name in [*names, found[1].get("set")]:
            if isinstance(name, str) and ID.fullmatch(name) and name not in ids:
                ids.append(name)
    inputs += [f"production/assets/{name}/assets/state.json" for name in ids]
    return [shot_line, f"Inputs, in this order: {', '.join(inputs)}."]


def _conform(p, root, unit: str) -> list:
    """The lines of the block of the ``conform`` stage ``render-wrangler`` operates, without the empty line."""
    inputs = {
        "voice": ["the request",
                  f"bible/style/voices/<speaker>.json (the speaker of L<n> in episodes/{unit}/lines.json)"],
        "music": ["the request"],
        "sfx": ["the request"],
        "conform": ["the request", f"production/{unit}/conform/mix-plan.json", "media/manifest.tsv",
                    EDIT_LIST.format(unit), f"production/{unit}/shots.json",
                    *(f"production/{shot}/render/manifest.json" for shot in tickets.shots(p, root, unit))],
    }
    lines = [f"Requests: production/{unit}/conform/requests/, one job per request, by its name."]
    for name, tool, usd, compute_min in CONFORM_TOOLS:
        cost = _cost(tool, production.setting(p, f"{tool.upper()}_USD", usd),
                     production.setting(p, f"{tool.upper()}_COMPUTE_MIN", compute_min))
        lines.append(f"{name}: {cost} Inputs, in this order: {', '.join(inputs[tool])}.")
    return lines


def _tool(p, root, room: str, stage: str, unit: str) -> str:
    """The tool and expected cost line of a stage ``media-wrangler`` makes, or the block of the ``conform`` stage or
    of a stage of ``TOOLS`` ``render-wrangler`` operates, with its empty line; else empty."""
    makers = production.stage_roles(production.stage(p, room, stage) or {}, "maker")
    if MEDIA_MAKER in makers:
        return _cost(production.setting(p, "MEDIA_TOOL", "grok-imagine"), production.setting(p, "MEDIA_USD", "0.1"),
                     production.setting(p, "MEDIA_COMPUTE_MIN", "1")) + "\n\n"
    if OPERATOR in makers and stage == CONFORM:
        return "\n".join(_conform(p, root, unit)) + "\n\n"
    if OPERATOR not in makers or stage not in TOOLS:
        return ""
    tool, usd, compute_min = TOOLS[stage]
    first = _cost(tool, production.setting(p, f"{tool.upper()}_USD", usd),
                  production.setting(p, f"{tool.upper()}_COMPUTE_MIN", compute_min))
    return "\n".join([first, *_operator(p, root, stage, unit)]) + "\n\n"


def _filing(p, root, ticket: dict, pull: dict, upstreams: list, room: str, stage: str, unit: str) -> dict:
    lines = "".join(f"Upstream: #{t['number']} {t['stage']} {t['unit']}, locked by PR #{u['number']}.\n"
                    for t, u in upstreams)
    tool = _tool(p, root, room, stage, unit)
    body = f"Do the stage {stage} for unit {unit}.\n\n{tool}{lines}Filed by factory tick.\n"
    return {"from": ticket["number"], "pull": pull["number"], "room": room, "stage": stage, "unit": unit,
            "title": f"[{key(stage, unit)}] {stage} for {unit}", "body": body,
            "labels": [tickets.TRACKED, f"room:{room}", f"stage:{stage}", f"unit:{unit}"]}


def _read(p, root, path):
    """The JSON value of ``path`` on the base, or None when it is missing or not JSON."""
    text = gitstate.show(root, production.base(p), path)
    try:
        return json.loads(text) if text is not None else None
    except ValueError:
        return None


def _shots(p, root, episode: str) -> list:
    """The shot ids of the events of the episode's edit list on the base, in event order, each once."""
    data = _read(p, root, EDIT_LIST.format(episode))
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        return []
    found = []
    for event in data["events"]:
        shot = event.get("shot") if isinstance(event, dict) else None
        if isinstance(shot, str) and ID.fullmatch(shot) and shot not in found:
            found.append(shot)
    return found


def _assets(p, root, episode: str, shots: list) -> list:
    """The ids of the characters and sets the files of ``shots`` name on the base, sorted, each once."""
    prefix = f"episodes/{episode}/sequences/"
    sha = gitstate.head(root, production.base(p))
    out = gitstate.git(root, "--literal-pathspecs", "ls-tree", "-r", "-z", "--name-only", sha, "--", prefix)
    paths = {}
    for path in sorted(out.split("\0")):
        parts = path[len(prefix):].split("/") if path.startswith(prefix) else []
        if len(parts) == 3 and parts[0] and parts[1] == "shots" and parts[2].endswith(".json"):
            paths.setdefault(parts[2][:-len(".json")], path)
    found = set()
    for shot in shots:
        data = _read(p, root, paths[shot]) if shot in paths else None
        if not isinstance(data, dict):
            continue
        names = data.get("characters") if isinstance(data.get("characters"), list) else []
        found.update(name for name in [*names, data.get("set")] if isinstance(name, str) and ID.fullmatch(name))
    return sorted(found)


def _intake(p, root, ticket: dict, pull: dict, taken: set, order: dict) -> list:
    """The production tickets after ``intake`` to file from the locked story package ``ticket``, sorted."""
    if production.stage(p, *INTAKE) is None:
        return []
    room, episode = INTAKE[0], tickets.episode(ticket["unit"])
    shots = _shots(p, root, episode)
    units = {"shot": [f"{episode}/{shot}" for shot in shots], "asset": _assets(p, root, episode, shots)}
    candidates = []
    for entry in _next(p, *INTAKE, []):
        for unit in units.get(entry["unit"], []):
            if (room, entry["stage"], unit) not in taken:
                taken.add((room, entry["stage"], unit))
                candidates.append(_filing(p, root, ticket, pull, [(ticket, pull)], room, entry["stage"], unit))
    return sorted(candidates, key=lambda f: (order[(f["room"], f["stage"])], f["unit"]))


def _keys(p, root, ticket: dict, pull: dict, taken: set) -> list:
    """The ``style`` ticket for the keys of the sequences of the locked ``sequences`` ticket's episode
    that have no key on the base: one filing, or none."""
    episode = tickets.episode(ticket["unit"])
    if production.stage(p, *STYLE) is None or (*STYLE, episode) in taken:
        return []
    missing = [q for q in tickets.sequences(p, root, episode)
               if gitstate.show(root, production.base(p), SEQUENCE_KEY.format(episode, q)) is None]
    if not missing:
        return []
    taken.add((*STYLE, episode))
    found = _filing(p, root, ticket, pull, [(ticket, pull)], *STYLE, episode)
    head = f"Do the stage style for unit {episode}.\n\n"
    lines = "".join(f"Write the sequence key {SEQUENCE_KEY.format(episode, q)}.\n" for q in missing)
    found["body"] = (head + lines + "Add only these keys and change no other file under bible/style/.\n\n"
                     + found["body"][len(head):])
    return [found]


def _intent_title(ticket: dict) -> str:
    ep = tickets.episode(ticket["unit"])
    return f"[{key('intent', ep)}] story intent packet for {ep}"


def _notes_title(ticket: dict) -> str:
    return f"[{key('notes', ticket['unit'])}] platform notes for {ticket['unit']}"


def _intent_packet(p, root, ticket: dict, pull: dict) -> dict:
    ep = tickets.episode(ticket["unit"])
    path = f"episodes/{ep}/intent-packet.json"
    text = gitstate.show(root, production.base(p), path)
    packet = f"{path} is not on the base." if text is None else "```json\n" + text.rstrip("\n") + "\n```"
    body = (f"Story intent packet for episode {ep}, locked by PR #{pull['number']} "
            f"(ticket #{ticket['number']}).\n\n{packet}\n\n"
            "The story room owns this packet; post and distribution read it and do not edit it.\n"
            "Filed by factory tick.\n")
    return {"unit": ep, "title": _intent_title(ticket), "body": body, "labels": ["room:post", "room:distribution"]}


def _platform_notes(p, root, ticket: dict, pull: dict) -> dict:
    unit = ticket["unit"]
    body = (f"Platform notes for {unit}, after the window locked by PR #{pull['number']} "
            f"(ticket #{ticket['number']}).\n\n"
            "Format limits:\nCTA results:\nAudience theories:\n\n"
            "The distribution room writes these notes; the story room answers here and does not edit them.\n"
            "Filed by factory tick.\n")
    return {"unit": unit, "title": _notes_title(ticket), "body": body, "labels": ["room:story"]}


_PACKET_MAKERS = {"intent-packet": (_intent_title, _intent_packet), "platform-notes": (_notes_title, _platform_notes)}


def _packets(p, state: tickets.State, root) -> list:
    """The packet issues to file, by the locked ticket's number; a title already in ``state.others`` is not,
    and is checked before the packet's file is read."""
    titles = {issue["title"] for issue in state.others}
    found = []
    for number in sorted(state.tickets):
        ticket = state.tickets[number]
        kind = PACKETS.get((ticket["room"], ticket["stage"]))
        pull = tickets.locked_pull(state, number) if kind else None
        if pull is None:
            continue
        title, make = _PACKET_MAKERS[kind]
        if title(ticket) in titles:
            continue
        made = make(p, root, ticket, pull)
        titles.add(made["title"])
        found.append({"from": number, "pull": pull["number"], "room": ticket["room"], "stage": kind, **made})
    return found


def due(p, state: tickets.State, root) -> list:
    """The tickets to file, in the order they are filed."""
    if state.errors:
        return []
    taken = {(t["room"], t["stage"], t["unit"]) for t in state.tickets.values()}
    order = {(entry["room"], entry["stage"]): i for i, entry in enumerate(p.pipeline)}
    found = []
    for number in sorted(state.tickets):
        ticket = state.tickets[number]
        pull = tickets.locked_pull(state, number)
        if pull is None:
            continue
        room, stage = ticket["room"], ticket["stage"]
        if (room, stage) == PACKAGE:
            found += _intake(p, root, ticket, pull, taken, order)
        if (room, stage) == SEQUENCES:
            found += _keys(p, root, ticket, pull, taken)
        if not any(entry["after"] == stage for entry in production.stages(p, room)):
            continue
        upstream_unit = production.stage(p, room, stage)["unit"]
        candidates = []
        for entry in _next(p, room, stage, tickets.skips(p, root, ticket["unit"])):
            for unit, upstreams in _units(p, root, state, ticket, pull, upstream_unit, entry):
                if (room, entry["stage"]) == RIGGING and not _on_base(p, root, CHARACTER.format(unit)):
                    continue
                if (room, entry["stage"], unit) not in taken:
                    taken.add((room, entry["stage"], unit))
                    candidates.append(_filing(p, root, ticket, pull, upstreams, room, entry["stage"], unit))
        found += sorted(candidates, key=lambda f: (order[(f["room"], f["stage"])], f["unit"]))
    return found + _packets(p, state, root)
