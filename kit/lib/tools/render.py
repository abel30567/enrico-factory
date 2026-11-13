"""The tool `render`: start a render node (kit/lib/node.py), run the job on it, stop it.

The node is the driver ``RENDER_PROVIDER`` names; with none of that name the resource is down. The node is
stopped once whenever ``start`` was called, and the amounts are what ``stop`` returns. The outputs named in
``STAGE_FILES`` are the stage's files; `factory run` commits them and sends every other output to the
asset store.
"""

from kit.lib import node
from kit.lib.tools import Down, Failed

RESOURCE = "render.node"
NOT_STARTED, JOB_FAILED, NOT_STOPPED = "node did not start", "job failed on the node", "node did not stop"
REASONS = (NOT_STARTED, JOB_FAILED, NOT_STOPPED)
STAGE_FILES = ("manifest.json", "mix.json", "render.json", "rerun.json", "state.json")


def run(call):
    driver = node.connect(call["settings"]["RENDER_PROVIDER"], call["secrets"]["render.node"])
    if driver is None:
        raise Down()
    unavailable, reason, held = False, None, None
    try:
        driver.start()
    except node.Unavailable:
        unavailable = True
    except (KeyboardInterrupt, SystemExit) as error:
        held = error
    except Exception:
        reason = NOT_STARTED
    else:
        try:
            driver.run({"hash": call["hash"], "inputs": call["inputs"], "out": call["out"]})
        except (KeyboardInterrupt, SystemExit) as error:
            held = error
        except Exception:
            reason = JOB_FAILED
    try:
        billed = driver.stop()
    except KeyboardInterrupt:
        raise
    except BaseException:
        if isinstance(held, KeyboardInterrupt):
            raise held from None
        raise Failed(NOT_STOPPED) from None
    if held is not None:
        raise held
    if unavailable:
        raise Down()
    if reason is not None:
        amounts = billed if isinstance(billed, dict) else {}
        raise Failed(reason, usd=amounts.get("usd"), compute_min=amounts.get("compute_min"))
    return billed
