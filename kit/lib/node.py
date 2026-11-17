"""The render node drivers: a GPU node a program starts for one job and stops after it.

``PROVIDERS`` maps a value of the setting ``RENDER_PROVIDER`` to a driver class; the kit ships ``ec2``
(`kit/lib/ec2.py`), one instance of the cloud's compute API started for each job. A
driver is made with the path of the key file ``render.node`` and ``config``, which is
``{"settings": {...}, "secrets": {...}}``: the settings of the call by name, and the path of each key file of
the call by name, exactly as `factory run` gives them from the checks of the resource ``render.node``. A
setting that is not a check, such as ``RENDER_API_URL``, is read only when it is there.

A driver class has:

- ``NEEDS``: a tuple of names, each a setting or a secret it reads. With a name in neither dict of
  ``config``, the driver is not made;
- ``PROGRAMS``: a tuple of the programs it runs on this machine. With one not found on ``PATH``, the
  driver is not made;
- ``start()``: start a node. It raises ``Unavailable`` only when no node could be had and nothing is billed;
- ``run(job)``: run the job on it, ``job`` being ``{"hash", "inputs", "out"}`` of the call, writing every
  output directly in ``out``;
- ``failure`` (optional): after ``run`` raised, None or ``{"log", "step", "tail"}`` (`kit/lib/ec2.py`). The tool
  `render` writes it to `record/node-failure.json` of the job and names the step in its reason;
- ``stop()``: stop the node and return ``{"usd", "compute_min"}``, what was billed since ``start``.
"""

import shutil

from kit.lib import ec2

PROVIDERS = {"ec2": ec2.Node}


class Unavailable(Exception):
    """No node could be had, and nothing is billed."""


def connect(provider, key_file, config):
    """The driver named ``provider`` made with ``key_file`` and ``config``, or None when no driver has that
    name, a name of its ``NEEDS`` is in neither dict of ``config`` or a program of its ``PROGRAMS`` is not
    found."""
    driver = PROVIDERS.get(provider)
    if driver is None:
        return None
    if any(name not in config["settings"] and name not in config["secrets"] for name in driver.NEEDS):
        return None
    if any(shutil.which(program) is None for program in driver.PROGRAMS):
        return None
    return driver(key_file, config)
