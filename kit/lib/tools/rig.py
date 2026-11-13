"""The rig tool: the rig vendor's automatic rigging of a model held in the asset store.

The model is the job's reference (`refs[0]` of the request, found in the manifest), got from the asset
store into a temporary directory and sent as a data URI: `POST <RIG_API>/openapi/v1/rigging`, poll the task
until it `SUCCEEDED` and download `result.rigged_character_glb_url` to `rig.glb`, which goes to the asset
store. No height, skeleton template, FBX or animation. The vendor and every reason are in
`kit/lib/vendor.py`.
"""

import os
import shutil
import tempfile

from kit.lib import vendor

RESOURCE = "gen.rig"
REASONS = vendor.RIG_REASONS
OUTPUT = "rig.glb"


def run(call):
    sha256 = vendor.reference(call)
    authorization = "Bearer " + vendor.read_key(call["secrets"]["gen.rig"])
    tmp = tempfile.mkdtemp(prefix="factory-rig-")
    try:
        model = os.path.join(tmp, "model.glb")
        model_url = vendor.data_uri(model, vendor.fetch_model(call, sha256, model))
        api = f"{vendor.RIG_API}/openapi/v1/rigging"
        task = vendor.task_id(vendor.submit(api, authorization, {"model_url": model_url}).get("result"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    answer = vendor.poll(f"{api}/{task}", authorization, "SUCCEEDED", ("PENDING", "IN_PROGRESS"))
    result = answer.get("result")
    url = vendor.download_url(result.get("rigged_character_glb_url") if isinstance(result, dict) else None)
    path = os.path.join(call["out"], OUTPUT)
    vendor.download(url, path)
    vendor.store(call, path)
    return vendor.spent(call)
