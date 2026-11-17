"""A fake editor run for tests: `<python> editor.py <work root> <script>`.

Sets `FACTORY_WORK` to the work root, loads the fake `unreal` of this directory as `unreal`, prints the engine
version line, runs the script as `__main__`, then prints the log of the run from `unreal.CALLS` in order:
`log <text>` as `LogPython: <text>`, `log_error <text>` as `LogPython: Error: <text>`, `engine <text>` as
`<text>`, nothing for any other call. Standard library only.
"""

import importlib.util
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PREFIX = {"log": "LogPython: ", "log_error": "LogPython: Error: ", "engine": ""}


def main(argv):
    root, script = argv
    os.environ["FACTORY_WORK"] = root
    spec = importlib.util.spec_from_file_location("unreal", os.path.join(HERE, "unreal.py"))
    unreal = importlib.util.module_from_spec(spec)
    sys.modules["unreal"] = unreal
    spec.loader.exec_module(unreal)
    print("LogInit: Engine Version: 5.0.0-fake+UE5")
    try:
        runpy.run_path(script, run_name="__main__")
    finally:
        for line in unreal.CALLS:
            name, _, text = line.partition(" ")
            if name in PREFIX:
                print(PREFIX[name] + text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
