"""The guard of one temporary copy (kit/lib/analysis.py): `python3 -I copyguard.py <parent>`.

It makes `<parent>/factory-copy-*`, prints its path, then reads lines from stdin: `run <pid>` adds a tool,
`done <pid>` drops it. At the end of its input, however the job that started it ended, it kills the
session of each tool left and deletes the directory. It runs in its own session, so a signal to the job's
process group does not reach it. Standard library only: it never imports the kit.
"""

import os
import shutil
import signal
import sys
import tempfile
import time

TRIES = 50
PAUSE_S = 0.1


def main(argv):
    path = tempfile.mkdtemp(prefix="factory-copy-", dir=argv[0])
    print(path, flush=True)
    tools = set()
    try:
        for line in sys.stdin:
            word, _, pid = line.strip().partition(" ")
            if not pid.isdigit():
                continue
            if word == "run":
                tools.add(int(pid))
            elif word == "done":
                tools.discard(int(pid))
    finally:
        for pid in sorted(tools):
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                pass
        for _ in range(TRIES):
            shutil.rmtree(path, ignore_errors=True)
            if not os.path.lexists(path):
                break
            time.sleep(PAUSE_S)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
