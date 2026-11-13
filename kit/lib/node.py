"""The render node drivers: a GPU node a program starts for one job and stops after it.

``PROVIDERS`` maps a value of the setting ``RENDER_PROVIDER`` to a driver class; the kit ships none. A
driver is made with the path of the key file ``render.node`` and has:

- ``start()``: start a node. It raises ``Unavailable`` only when no node could be had and nothing is billed;
- ``run(job)``: run the job on it, ``job`` being ``{"hash", "inputs", "out"}`` of the call, writing every
  output directly in ``out``;
- ``stop()``: stop the node and return ``{"usd", "compute_min"}``, what was billed since ``start``.
"""

PROVIDERS = {}


class Unavailable(Exception):
    """No node could be had, and nothing is billed."""


def connect(provider, key_file):
    """The driver named ``provider`` made with ``key_file``, or None when no driver has that name."""
    driver = PROVIDERS.get(provider)
    return None if driver is None else driver(key_file)
