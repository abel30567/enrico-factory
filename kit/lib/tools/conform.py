"""The tool `conform`: cut the stored frames by the shots and mix the stored audio with ffmpeg
(kit/lib/conformjob.py). The outputs named in ``STAGE_FILES`` are the stage's files; `factory run` commits them
and sends the stems and the master to the asset store.
"""

from kit.lib import conformjob

RESOURCE = "store.assets"
REASONS = conformjob.REASONS
STAGE_FILES = ("manifest.json", "mix.json")


def run(call):
    return conformjob.make(call)
