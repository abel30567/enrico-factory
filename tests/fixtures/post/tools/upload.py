"""A fake upload tool for the post tests. It makes no call and leaves ``out/`` empty.

The setting ``FAKE_ANSWER`` names a recorded answer. A file is recorded as ``record/post.json`` with ``job``
set to the job's hash. A directory is a recorded record: its files are copied into ``record/`` as they are.
"""

import json
import os
import shutil

RESOURCE = "platform.fake"
REASONS = ()


def run(call):
    source = call["settings"]["FAKE_ANSWER"]
    if os.path.isdir(source):
        for name in sorted(os.listdir(source)):
            shutil.copyfile(os.path.join(source, name), os.path.join(call["record"], name))
    else:
        with open(source, encoding="utf-8") as handle:
            answer = json.load(handle)
        answer["job"] = call["hash"]
        with open(os.path.join(call["record"], "post.json"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps(answer, indent=1, sort_keys=True) + "\n")
    return {"usd": 0, "compute_min": 0}
