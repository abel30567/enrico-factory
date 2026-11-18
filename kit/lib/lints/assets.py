"""Assets: every asset state has its shape, its id, its canon sheet, its budgets, its height and its
skeleton (brief section 6, row `assets`, and section 16).

Rule ids: shape (1), naming (2), references (3), budgets (4), scale (5), skeleton (6), depends (7),
requests (8). The check reads every `production/assets/<asset>/assets/state.json` of the tree, the canon
sheets and `bible/canon/ledger.tsv`. Every finding is on line 1 of the state, except one on a scene
script and those of rule 8. A state with a `shape` finding gets no other finding, and one with a `naming`
finding none of rules 3 to 6. A character needs its canon sheet under `bible/canon/characters/`, a set
its sheet under `bible/canon/places/`. A character's height is the ledger row `height` of its id, written
`<number> m`; a set's is checked where it has such a row, a prop's never. A character's skeleton is
checked when it is not null; rigging requires one. Rule 7 holds each `assets/scene.py` to `JOB_FILES`
(`lint.job_files`), on line 1 of the script. A state is superseded, and gets no finding of rules 1 to 6,
when its `manifest.json` names a `job` hash other than that of the job file now at
`jobs/production/assets/<asset>/assets/requests/model3d.json`: the gated run of that job replaces it.

Rule 8 reads every `production/assets/<asset>/assets/requests/model3d.json`, whether or not a state
exists, with the model sheets under `bible/style/` and `media/manifest.tsv`, on line 1 of the request: it
is `{for, prompt, refs}` for its asset, names at least one reference image as `media:<id>` with a row in
the manifest, and every one is of its subject: a character's or a set's model sheet cites each of them,
and no model sheet cites a prop's (a prop has none).
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

from ..lint import Finding, LintError, files, job_files, link_targets, markdown_lines, read_json, read_text, read_tsv
from .references import MEDIA
from .structure import SHEET

STATE = re.compile(r"^production/assets/([^/]+)/assets/state\.json$")
SCENE = re.compile(r"^production/assets/[^/]+/assets/scene\.py$")
REQUEST = re.compile(r"^production/assets/([^/]+)/assets/requests/model3d\.json$")
REQUEST_KEYS = ["for", "prompt", "refs"]
MEDIA_MANIFEST = "media/manifest.tsv"
MODEL_SHEETS = {"character": "bible/style/characters", "set": "bible/style/places"}
JOB_FILES = ("manifest.json", "state.json")
STAGED_MANIFEST = "production/assets/{}/assets/manifest.json"
JOB_FILE = "jobs/production/assets/{}/assets/requests/model3d.json"
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
SCALE_TOLERANCE = Decimal("0.1")


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


def skeleton_problems(skeleton) -> list[str]:
    """What is wrong with a skeleton of the right shape: not one root, a repeated bone name, weights that
    are not normalised."""
    found = []
    bones = skeleton["bones"]
    roots = sum(1 for b in bones if b["parent"] is None)
    if roots != 1:
        found.append(f"skeleton has {roots} roots, not one")
    names = [b["name"] for b in bones]
    found += [f"bone name {name} repeats" for name in sorted(set(names)) if names.count(name) > 1]
    if not skeleton["weights_normalised"]:
        found.append("skin weights are not normalised")
    return found


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
        # Decimals from the written numbers, so that exactly 10% passes (binary floats round it over).
        elif abs(Decimal(repr(data["height_m"])) - Decimal(m.group(1))) > SCALE_TOLERANCE * Decimal(m.group(1)):
            found.append(("scale", f"height_m {format(data['height_m'], 'g')} is not within 10% of {value} "
                                   f"({CANON_LEDGER}:{n})"))
    if kind == "character" and data["skeleton"] is not None:
        found += [("skeleton", message) for message in skeleton_problems(data["skeleton"])]
    return found


def _superseded(root: Path, asset: str, present: set[str]) -> bool:
    """Whether the staged state of ``asset`` was made by a job other than its job file now; False when the
    manifest is missing, unreadable or has no string `job`, or the job file is missing, unreadable or not a
    valid job."""
    # Imported here: cmd_gate imports registry, which imports this module.
    from .. import cmd_gate

    manifest, job_file = STAGED_MANIFEST.format(asset), JOB_FILE.format(asset)
    if manifest not in present or job_file not in present:
        return False
    try:
        staged = json.loads(read_text(root, manifest))
        _, digest = cmd_gate._parsed(read_text(root, job_file), job_file)
    except (LintError, ValueError, cmd_gate.Refused):
        return False
    return isinstance(staged, dict) and isinstance(staged.get("job"), str) and staged["job"] != digest


def _cited(root: Path, rel: str) -> set[str]:
    """The media ids a sheet cites as a link to `media:<id>`."""
    return {m.group(1) for _, line in markdown_lines(read_text(root, rel)) for target in link_targets(line)
            if (m := MEDIA.match(target))}


def _request(root: Path, rel: str, asset: str, present: set[str], ids: set[str], sheets) -> list[str]:
    """Rule 8 on one request of ``asset``; ``sheets`` is ``[(path, ids it cites)]`` of the model sheets."""
    try:
        data = json.loads(read_text(root, rel))
    except ValueError:
        data = None
    if not isinstance(data, dict) or sorted(data) != REQUEST_KEYS or not isinstance(data["refs"], list):
        return ["request is not {for, prompt, refs}"]
    if not data["refs"]:
        return ["request names no reference image"]
    found = []
    if data["for"] != asset:
        named = data["for"] if isinstance(data["for"], str) else json.dumps(data["for"])
        found.append(f"request for {named} is not for asset {asset}")
    kind = next((k for k, folder in SHEETS.items() if f"{folder}/{asset}.md" in present), "prop")
    sheet = f"{MODEL_SHEETS[kind]}/{asset}.md" if kind in MODEL_SHEETS else None
    own = dict(sheets).get(sheet)
    if sheet is not None and own is None:
        found.append(f"{kind} {asset} has no model sheet {sheet}")
    for k, ref in enumerate(data["refs"]):
        mid = ref[len("media:"):] if isinstance(ref, str) and ref.startswith("media:") else ""
        if not mid:
            found.append(f"refs[{k}] is not media:<id>")
            continue
        if mid not in ids:
            found.append(f"refs[{k}] media:{mid} has no row in {MEDIA_MANIFEST}")
            continue
        other = next((path for path, cited in sheets if path != sheet and mid in cited), None)
        if sheet is None:
            if other is not None:
                found.append(f"refs[{k}] media:{mid} is cited by {other}, the model sheet of another subject")
        elif own is not None and mid not in own:
            found.append(f"refs[{k}] media:{mid} is cited by {other}, not by {sheet}" if other
                         else f"refs[{k}] media:{mid} is not cited by {sheet}")
    return found


def _requests(root: Path, present: set[str]) -> list[Finding]:
    requests = sorted((rel, m.group(1)) for rel in present if (m := REQUEST.match(rel)))
    if not requests:
        return []
    ids = set()
    if MEDIA_MANIFEST in present:
        ids = {fields[0] for _, fields in read_tsv(root, MEDIA_MANIFEST)[1:]}
    sheets = [(rel, _cited(root, rel)) for rel in sorted(present)
              if rel.startswith("bible/style/") and SHEET.match(rel)]
    return [Finding(rel, 1, "requests", message)
            for rel, asset in requests for message in _request(root, rel, asset, present, ids, sheets)]


def check(root: Path) -> list[Finding]:
    present = {p.as_posix() for p in files(root)}
    states = sorted((rel, m.group(1)) for rel in present if (m := STATE.match(rel)))
    found = [f for rel in sorted(present) if SCENE.match(rel) for f in job_files(present, rel, JOB_FILES)]
    if states:
        heights = _heights(root, present)
        for rel, directory in states:
            if _superseded(root, directory, present):
                continue
            _, data = read_json(root, rel)
            found += [Finding(rel, 1, rule, message)
                      for rule, message in _state(rel, directory, data, present, heights)]
    return found + _requests(root, present)
