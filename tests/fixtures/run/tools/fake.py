"""A fake tool for the run tests. It makes no call: its first input is a JSON script that says what to do.

The script: ``outputs`` (name to text, written into ``out`` in order), ``usd``, ``compute_min``, and
optional ``fail`` (a reason: raise ``Failed`` with the script's amounts after writing the outputs),
``down`` (raise ``Down`` when the log does not hold this job's hash yet), ``crash`` (raise an error whose
text is the key), ``shout`` (print the key to standard output and error) and ``returns`` (return this
value instead of the amounts). Each call appends the job's hash to the file setting ``FAKE_LOG`` names.
"""

import json
import os
import sys

from kit.lib.tools import Down, Failed

RESOURCE = "gen.fake"
REASONS = ("rejected",)


def run(call):
    log = call["settings"]["FAKE_LOG"]
    seen = open(log).read().split() if os.path.exists(log) else []
    with open(log, "a") as handle:
        handle.write(call["hash"] + "\n")
    try:
        with open(call["secrets"]["gen.fake"]) as handle:
            key = handle.read()
    except OSError:
        key = ""
    if not key:
        raise Failed("rejected")
    script = json.loads(call["inputs"][0]["text"])
    if script.get("down") and call["hash"] not in seen:
        raise Down()
    if script.get("crash"):
        raise RuntimeError(key)
    if script.get("shout"):
        print(key)
        print(key, file=sys.stderr)
    for name, text in script.get("outputs", {}).items():
        with open(os.path.join(call["out"], name), "w") as handle:
            handle.write(text)
    if "fail" in script:
        raise Failed(script["fail"], usd=script.get("usd"), compute_min=script.get("compute_min"))
    if "returns" in script:
        return script["returns"]
    return {"usd": script["usd"], "compute_min": script["compute_min"]}
