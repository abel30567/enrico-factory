"""The rig tool: the rig vendor's automatic rigging of a model held in the asset store.

The model is the one the asset's state names: the request names `refs` `["asset:<for>"]` and has
`production/assets/<for>/assets/state.json` as an input, whose `sha256` is the model's. It is got from the
asset store into a temporary directory and sent as a data URI: `POST <RIG_API>/<RIG_PATH>` with `model_url`
and nothing else, poll the answer's `status_url` until the request is `COMPLETED`, get its `response_url` and
download `rigged_character_glb.url` to `rig.glb`, which is read (`kit/lib/glb.py`) and goes to the asset
store; one that does not read fails the job and is not stored. The stage file is `manifest.json`, with the
rig's `skeleton` beside the manifest's keys. No height, skeleton template, FBX or animation is asked for or
downloaded. The vendor and every reason are in `kit/lib/vendor.py`.
"""

import os
import shutil
import tempfile

from kit.lib import vendor

RESOURCE = "gen.rig"
REASONS = vendor.RIG_REASONS
OUTPUT = "rig.glb"
MANIFEST = "manifest.json"
STAGE_FILES = ("manifest.json",)


def run(call):
    sha256 = vendor.model(call)
    authorization = "Key " + vendor.read_key(call["secrets"]["gen.rig"])
    tmp = tempfile.mkdtemp(prefix="factory-rig-")
    try:
        model = os.path.join(tmp, "model.glb")
        model_url = vendor.data_uri(model, vendor.fetch_model(call, sha256, model))
        answer = vendor.submit(f"{vendor.RIG_API}/{vendor.RIG_PATH}", authorization, {"model_url": model_url})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    status_url = vendor.under(answer.get("status_url"), vendor.RIG_API)
    response_url = vendor.under(answer.get("response_url"), vendor.RIG_API)
    vendor.poll(status_url, authorization, "COMPLETED", ("IN_QUEUE", "IN_PROGRESS"))
    rig = vendor.get(response_url, authorization).get("rigged_character_glb")
    url = vendor.download_url(rig.get("url") if isinstance(rig, dict) else None)
    path = os.path.join(call["out"], OUTPUT)
    vendor.download(url, path)
    skeleton = vendor.summary(path)["skeleton"]
    vendor.store(call, path)
    vendor.stage_file(call["out"], MANIFEST, vendor.manifest(call, vendor.RIG_PATH, (OUTPUT,), skeleton=skeleton))
    return vendor.spent(call)
