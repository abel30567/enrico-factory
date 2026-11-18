"""The model tool: a 3D model of the job's reference image, from the model vendor's queue API.

`POST <MODEL_API>/<MODEL_PATH>` with the image's public link and `face_limit`, nine tenths of the triangle
budget of the asset's kind (`TRIANGLE_BUDGETS` of `kit/lib/lints/assets.py`), because the vendor's model can
come back slightly over the number it is sent, poll the answer's `status_url` until the request is
`COMPLETED`, get its `response_url` and download `model_mesh.url` to `model.glb`, which goes to the asset
store. The model is read (`kit/lib/glb.py`); one that does not read fails the job and is not
stored. The stage files are the asset's `state.json`, `{asset, kind, sha256, triangles, materials, textures,
height_m, skeleton}`, and `manifest.json`. `asset` is the request's `for`; `kind` is `character` when an
input is `bible/canon/characters/<asset>.md`, else `set` when one is `bible/canon/places/<asset>.md`, else
`prop`.

A character or a set is scaled to its canon height: the first row of `bible/canon/ledger.tsv`, when it is an
input, whose `subject` is the asset and `attribute` is `height`, its value `<number> m` (as the `assets` lint
reads it). The downloaded model is scaled uniformly by that height over its own (`kit/lib/glb.py` `scale`)
before it is summarised and stored, so its `height_m` is the canon height; one that cannot be scaled fails the
job and is not stored. A character without such a row fails the job before any vendor request; a set without
one is not scaled, nor is a prop ever. The vendor and every reason are in `kit/lib/vendor.py`.
"""

import hashlib
import os

from kit.lib import glb, vendor
from kit.lib.lints.assets import CANON_LEDGER, HEIGHT, KEY_COLUMNS, TRIANGLE_BUDGETS
from kit.lib.tools import Failed

RESOURCE = "gen.model3d"
REASONS = vendor.MODEL_REASONS
OUTPUT = "model.glb"
STATE, MANIFEST = "state.json", "manifest.json"
STAGE_FILES = ("manifest.json", "state.json")
SHEETS = (("character", "bible/canon/characters"), ("set", "bible/canon/places"))
FACE_LIMIT = {kind: budget * 9 // 10 for kind, budget in TRIANGLE_BUDGETS.items()}


def asset_kind(call, asset):
    """`character` or `set` when the asset's canon sheet is an input, else `prop`."""
    paths = {item["path"] for item in call["inputs"]}
    return next((kind for kind, folder in SHEETS if f"{folder}/{asset}.md" in paths), "prop")


def canon_height(call, asset):
    """The asset's height in metres from `bible/canon/ledger.tsv` among the inputs; None without one."""
    text = next((item["text"] for item in call["inputs"] if item["path"] == CANON_LEDGER), None)
    rows = [line.split("\t") for line in (text or "").splitlines() if line.strip()]
    header = [c.strip() for c in rows[0]] if rows else []
    if any(c not in header for c in KEY_COLUMNS):
        return None
    cols = [header.index(c) for c in KEY_COLUMNS]
    for fields in rows[1:]:
        subject, attribute, value = (fields[i].strip() if i < len(fields) else "" for i in cols)
        if subject.lower() == asset.lower() and attribute.lower() == "height":
            found = HEIGHT.fullmatch(value)
            height = float(found.group(1)) if found else 0
            return height if height > 0 else None
    return None


def scale_to(path, height):
    """Scale the model at ``path`` to ``height`` metres; one that cannot be is deleted and fails the job."""
    try:
        with open(path, "rb") as handle:
            glb.scale(path, height / glb.height(handle.read()))
    except (ValueError, glb.Broken):
        os.unlink(path)
        raise Failed(vendor.NOT_SCALABLE) from None


def run(call):
    sha256, size = vendor.reference_row(call)
    asset = vendor.asset(call)
    kind = asset_kind(call, asset)
    height = canon_height(call, asset) if kind != "prop" else None
    if kind == "character" and height is None:
        raise Failed(vendor.NO_HEIGHT, usd=0, compute_min=0)
    authorization = "Key " + vendor.read_key(call["secrets"]["gen.model3d"])
    image_url = vendor.link(call["settings"], sha256)
    vendor.check_reference(call, sha256, size)
    body = {"image_url": image_url, "face_limit": FACE_LIMIT[kind]}
    answer = vendor.submit(f"{vendor.MODEL_API}/{vendor.MODEL_PATH}", authorization, body)
    status_url = vendor.under(answer.get("status_url"), vendor.MODEL_API)
    response_url = vendor.under(answer.get("response_url"), vendor.MODEL_API)
    vendor.poll(status_url, authorization, "COMPLETED", ("IN_QUEUE", "IN_PROGRESS"))
    mesh = vendor.get(response_url, authorization).get("model_mesh")
    url = vendor.download_url(mesh.get("url") if isinstance(mesh, dict) else None)
    path = os.path.join(call["out"], OUTPUT)
    vendor.download(url, path)
    summary = vendor.summary(path)
    if height is not None:
        scale_to(path, height)
        summary = vendor.summary(path)
    vendor.store(call, path)
    with open(path, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    state = {"asset": asset, "kind": kind, "sha256": digest, **summary}
    vendor.stage_file(call["out"], STATE, state)
    vendor.stage_file(call["out"], MANIFEST, vendor.manifest(call, vendor.MODEL_PATH, (OUTPUT, STATE)))
    return vendor.spent(call)
