"""The model tool: a 3D model of the job's reference image, from the model vendor's queue API.

`POST <MODEL_API>/<MODEL_PATH>` with the image's public link, poll the answer's `status_url` until the
request is `COMPLETED`, get its `response_url` and download `model_mesh.url` to `model.glb`, which goes to
the asset store. The vendor and every reason are in `kit/lib/vendor.py`.
"""

import os

from kit.lib import vendor

RESOURCE = "gen.model3d"
REASONS = vendor.REASONS
OUTPUT = "model.glb"


def run(call):
    sha256 = vendor.reference(call)
    authorization = "Key " + vendor.read_key(call["secrets"]["gen.model3d"])
    image_url = vendor.link(call["settings"], sha256)
    answer = vendor.submit(f"{vendor.MODEL_API}/{vendor.MODEL_PATH}", authorization, {"image_url": image_url})
    status_url = vendor.under(answer.get("status_url"), vendor.MODEL_API)
    response_url = vendor.under(answer.get("response_url"), vendor.MODEL_API)
    vendor.poll(status_url, authorization, "COMPLETED", ("IN_QUEUE", "IN_PROGRESS"))
    mesh = vendor.get(response_url, authorization).get("model_mesh")
    url = vendor.download_url(mesh.get("url") if isinstance(mesh, dict) else None)
    path = os.path.join(call["out"], OUTPUT)
    vendor.download(url, path)
    vendor.store(call, path)
    return vendor.spent(call)
