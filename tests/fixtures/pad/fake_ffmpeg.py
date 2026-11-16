#!/usr/bin/env python3
"""A fake of ffmpeg for tests/test_tool_pad.py, which copies it into a `bin/` with its first line replaced.

It reads `control.json` beside it, never an environment variable: `log` (a file, one JSON line
`{argv, env, cwd, input_sha256}` per call, written before it acts, `input_sha256` the sha256 of the file after
`-i`), `fail` (exit 1 when true, after writing) and `write` (a file copied to the last argument, or null
to write nothing).
"""

import hashlib
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv):
    with open(os.path.join(HERE, "control.json"), encoding="utf-8") as handle:
        control = json.load(handle)
    try:
        with open(argv[argv.index("-i") + 1], "rb") as handle:
            given = hashlib.sha256(handle.read()).hexdigest()
    except (ValueError, IndexError, OSError):
        given = None
    with open(control["log"], "a", encoding="utf-8") as log:
        log.write(json.dumps({"argv": argv, "env": dict(os.environ), "cwd": os.getcwd(),
                              "input_sha256": given}) + "\n")
    if control.get("write"):
        shutil.copyfile(control["write"], argv[-1])
    return 1 if control.get("fail") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
