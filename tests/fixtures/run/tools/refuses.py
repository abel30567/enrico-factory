"""A fake tool for the run tests that refuses before any call: its first input is a JSON script whose
``refuse`` is the reason, raised with ``vendor._before``. Each call appends the job's hash to the file setting
``FAKE_LOG`` names. It reaches nothing.
"""

import json

from kit.lib import vendor

RESOURCE = "gen.fake"
REASONS = (vendor.NO_REFERENCE, vendor.NOT_IN_MANIFEST, "reference is framed like a card scan",
           "reference image could not be decoded")


def run(call):
    with open(call["settings"]["FAKE_LOG"], "a") as handle:
        handle.write(call["hash"] + "\n")
    raise vendor._before(json.loads(call["inputs"][0]["text"])["refuse"])
