"""The model tool: a 3D model of the job's reference image, from the model vendor's queue API.

`POST <MODEL_API>/<MODEL_PATH>` with the image's public link, poll the answer's `status_url` until the
request is `COMPLETED`, get its `response_url` and download `model_mesh.url` to `model.glb`, which goes to
the asset store. The model is read (`kit/lib/glb.py`); one that does not read fails the job and is not
stored. The stage files are the asset's `state.json`, `{asset, kind, sha256, triangles, materials, textures,
height_m, skeleton}`, and `manifest.json`. `asset` is the request's `for`; `kind` is `character` when an
input is `bible/canon/characters/<asset>.md`, else `set` when one is `bible/canon/places/<asset>.md`, else
`prop`. The vendor and every reason are in `kit/lib/vendor.py`.
"""

import hashlib
import os

from kit.lib import vendor

RESOURCE = "gen.model3d"
REASONS = vendor.MODEL_REASONS
OUTPUT = "model.glb"
STATE, MANIFEST = "state.json", "manifest.json"
STAGE_FILES = ("manifest.json", "state.json")
SHEETS = (("character", "bible/canon/characters"), ("set", "bible/canon/places"))


def asset_kind(call, asset):
    """`character` or `set` when the asset's canon sheet is an input, else `prop`."""
    paths = {item["path"] for item in call["inputs"]}
    return next((kind for kind, folder in SHEETS if f"{folder}/{asset}.md" in paths), "prop")


def run(call):
    sha256, size = vendor.reference_row(call)
    asset = vendor.asset(call)
    authorization = "Key " + vendor.read_key(call["secrets"]["gen.model3d"])
    image_url = vendor.link(call["settings"], sha256)
    vendor.check_reference(call, sha256, size)
    answer = vendor.submit(f"{vendor.MODEL_API}/{vendor.MODEL_PATH}", authorization, {"image_url": image_url})
    status_url = vendor.under(answer.get("status_url"), vendor.MODEL_API)
    response_url = vendor.under(answer.get("response_url"), vendor.MODEL_API)
    vendor.poll(status_url, authorization, "COMPLETED", ("IN_QUEUE", "IN_PROGRESS"))
    mesh = vendor.get(response_url, authorization).get("model_mesh")
    url = vendor.download_url(mesh.get("url") if isinstance(mesh, dict) else None)
    path = os.path.join(call["out"], OUTPUT)
    vendor.download(url, path)
    summary = vendor.summary(path)
    vendor.store(call, path)
    with open(path, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    state = {"asset": asset, "kind": asset_kind(call, asset), "sha256": digest, **summary}
    vendor.stage_file(call["out"], STATE, state)
    vendor.stage_file(call["out"], MANIFEST, vendor.manifest(call, vendor.MODEL_PATH, (OUTPUT, STATE)))
    return vendor.spent(call)
