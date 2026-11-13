"""Assets: every asset state has its shape, its id, its canon sheet, its budgets, its height and its
skeleton (brief section 6, row `assets`, and section 16).

Rule ids: shape (1), naming (2), references (3), budgets (4), scale (5), skeleton (6). The check reads
every `production/assets/<asset>/assets/state.json` of the tree, the canon sheets and
`bible/canon/ledger.tsv`. Every finding is on line 1 of the state. A state with a `shape` finding gets
no other finding, and one with a `naming` finding none of rules 3 to 6. A character needs its canon
sheet under `bible/canon/characters/`, a set its sheet under `bible/canon/places/`. A character's height
is the ledger row `height` of its id, written `<number> m`; a set's is checked where it has such a row,
a prop's never. Only a character's skeleton is checked beyond its shape.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..lint import Finding, files, read_json, read_tsv

STATE = re.compile(r"^production/assets/([^/]+)/assets/state\.json$")
CANON_LEDGER = "bible/canon/ledger.tsv"
KEY_COLUMNS = ("subject", "attribute", "value")
KEYS = ("asset", "kind", "sha256", "triangles", "materials", "textures", "height_m", "skeleton")
KINDS = ("character", "set", "prop")
ASSET_ID = re.compile(r"[a-z0-9][a-z0-9-]*")
SHA256 = re.compile(r"[0-9a-f]{64}")
HEIGHT = re.compile(r"([0-9]+(?:\.[0-9]+)?) ?m")
SHEETS = {"character": "bible/canon/characters", "set": "bible/canon/places"}
# Placeholder until measured on the golden levels (#118).
TRIANGLE_BUDGETS = {"character": 100000, "prop": 20000, "set": 1000000}
TEXTURE_MAX = 4096
SCALE_TOLERANCE = 0.1


def _whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _texture(value) -> bool:
    return isinstance(value, dict) and isinstance(value.get("name"), str) \
        and _whole(value.get("width")) and _whole(value.get("height"))


def _bone(value) -> bool:
    return isinstance(value, dict) and isinstance(value.get("name"), str) and "parent" in value \
        and (value["parent"] is None or isinstance(value["parent"], str))


def _skeleton(value) -> bool:
    return value is None or (isinstance(value, dict) and isinstance(value.get("bones"), list)
                             and all(_bone(b) for b in value["bones"])
                             and isinstance(value.get("weights_normalised"), bool))


VALID = {
    "asset": (lambda v: isinstance(v, str), "asset is not a string"),
    "kind": (lambda v: isinstance(v, str) and v in KINDS, "kind is not 'character', 'set' or 'prop'"),
    "sha256": (lambda v: isinstance(v, str) and SHA256.fullmatch(v) is not None,
               "sha256 is not 64 lower-case hex characters"),
    "triangles": (_whole, "triangles is not a whole number"),
    "materials": (_whole, "materials is not a whole number"),
    "textures": (lambda v: isinstance(v, list) and all(_texture(t) for t in v),
                 "textures is not a list of {name, width, height}"),
    "height_m": (lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0,
                 "height_m is not a positive number"),
    "skeleton": (_skeleton, "skeleton is not null or {bones, weights_normalised}"),
}


def _shape(data) -> list[str]:
    if not isinstance(data, dict):
        return ["not {" + ", ".join(KEYS) + "}"]
    out = []
    for key in KEYS:
        if key not in data:
            out.append(f"{key} is missing")
        elif not VALID[key][0](data[key]):
            out.append(VALID[key][1])
    return out


def _heights(root: Path, present: set[str]) -> dict[str, tuple[int, str]]:
    """{subject: (line, value)}: the first `height` row of each subject, trimmed and lower-cased."""
    if CANON_LEDGER not in present:
        return {}
    rows = read_tsv(root, CANON_LEDGER)
    if not rows:
        return {}
    header = [c.strip() for c in rows[0][1]]
    if any(c not in header for c in KEY_COLUMNS):
        return {}
    cols = [header.index(c) for c in KEY_COLUMNS]
    heights = {}
    for n, fields in rows[1:]:
        subject, attribute, value = (fields[i].strip() if i < len(fields) else "" for i in cols)
        if attribute.lower() == "height":
            heights.setdefault(subject.lower(), (n, value))
    return heights


def _state(rel: str, directory: str, data, present: set[str], heights) -> list[str]:
    problems = _shape(data)
    if problems:
        return [("shape", p) for p in problems]
    asset, kind = data["asset"], data["kind"]
    if not ASSET_ID.fullmatch(asset):
        return [("naming", f"asset id {asset} does not match [a-z0-9][a-z0-9-]*")]
    if asset != directory:
        return [("naming", f"asset id {asset} is not the directory name {directory}")]
    found = []
    if kind in SHEETS and f"{SHEETS[kind]}/{asset}.md" not in present:
        found.append(("references", f"{kind} {asset} has no {SHEETS[kind]}/{asset}.md"))
    limit = TRIANGLE_BUDGETS[kind]
    if data["triangles"] > limit:
        found.append(("budgets", f"{data['triangles']} triangles, over the {kind} budget of {limit}"))
    for t in data["textures"]:
        if t["width"] > TEXTURE_MAX or t["height"] > TEXTURE_MAX:
            found.append(("budgets", f"texture {t['name']} is {t['width']}x{t['height']}, "
                                     f"over {TEXTURE_MAX} pixels a side"))
    row = heights.get(asset)
    if kind == "character" and row is None:
        found.append(("scale", f"no height row for {asset} in {CANON_LEDGER}"))
    elif kind != "prop" and row is not None:
        n, value = row
        m = HEIGHT.fullmatch(value)
        if m is None:
            found.append(("scale", f"height '{value}' of {asset} ({CANON_LEDGER}:{n}) is not '<number> m'"))
        elif abs(data["height_m"] - float(m.group(1))) > SCALE_TOLERANCE * float(m.group(1)):
            found.append(("scale", f"height_m {format(data['height_m'], 'g')} is not within 10% of {value} "
                                   f"({CANON_LEDGER}:{n})"))
    if kind == "character":
        skeleton = data["skeleton"]
        if skeleton is None:
            found.append(("skeleton", "a character has no skeleton"))
        else:
            bones = skeleton["bones"]
            roots = sum(1 for b in bones if b["parent"] is None)
            if roots != 1:
                found.append(("skeleton", f"skeleton has {roots} roots, not one"))
            names = [b["name"] for b in bones]
            found += [("skeleton", f"bone name {name} repeats")
                      for name in sorted(set(names)) if names.count(name) > 1]
            if not skeleton["weights_normalised"]:
                found.append(("skeleton", "skin weights are not normalised"))
    return found


def check(root: Path) -> list[Finding]:
    present = {p.as_posix() for p in files(root)}
    states = sorted((rel, m.group(1)) for rel in present if (m := STATE.match(rel)))
    if not states:
        return []
    heights = _heights(root, present)
    found = []
    for rel, directory in states:
        _, data = read_json(root, rel)
        found += [Finding(rel, 1, rule, message)
                  for rule, message in _state(rel, directory, data, present, heights)]
    return found
