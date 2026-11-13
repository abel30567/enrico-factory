"""The tools `factory run` calls, and the two exceptions a tool raises.

A tool is the file ``<name>.py`` in this directory, where ``<name>`` is a job's ``tool`` and matches
``[a-z][a-z0-9-]*``. `factory run` loads it by file location and makes one call. Each tool the kit ships
is one file here, added by its own ticket; adding one edits no other file.

A read tool is not a job tool: it is the file ``reads/<platform>.py``, ``<platform>`` one of the platforms of
`kit/lib/trends.py`, and `factory run --read <topic> <platform>` loads it by file location (`kit/lib/reads.py`).
It has ``PLATFORM`` (its platform), ``RESOURCE`` and ``REASONS`` as below, ``API`` (the default base URL of the
platform's API, setting ``<PLATFORM>_API_URL`` replacing it), ``BUDGET`` (``(<setting>, <limit>)``: the setting
of the searches allowed a day and its highest value) and ``read(call) -> {"videos": [...]}``, the videos of a
stored read (`kit/lib/trends.py`). Its ``call`` has exactly ``api`` (the base URL), ``read_at`` (the read's time),
``secrets`` (as below) and ``topic`` (the platform's part of the topic file), and ``settings`` (as below) only when its
resource has a ``setting`` check. It raises ``Failed(reason)`` or ``Down()`` as below; amounts are not read. One read is
one search of ``BUDGET``, whatever number of requests ``read`` makes. It may also have ``WATCH``, ``{key: check}``: the
keys of the platform's part of the topic file, each required and no other, a value valid when ``check(value)`` returns
True (without it, ``{words, region, days}`` as `kit/lib/reads.py` states); and ``STORED``, a tuple of the other keys
``read`` returns, each returned every time and stored beside ``videos``, each matching ``[a-z][a-z_]*`` and none of
``platform``, ``read_at``, ``topic``, ``videos`` (without it, none).

A tool module has:

- ``RESOURCE``: a non-empty string, the id of one resource in the resources register. `factory run`
  refuses the job unless every check of that resource passes.
- ``REASONS``: a tuple of strings, every reason the tool can give for a failure. These are the only
  words of a tool that `factory run` repeats.
- ``run(call) -> dict``: makes the one call and returns ``{"usd": <number>, "compute_min": <number>}``,
  the real cost, each a finite number of at least 0 with at most 4 decimal places.

``call`` is a dict with exactly these keys:

- ``hash``: the job's hash; ``ticket``: the ticket number; ``caller``: the operator role;
- ``expected``: the job's ``{"compute_min", "usd"}``;
- ``inputs``: the job's inputs, each ``{"path", "sha256", "text"}``, ``text`` the file's text on the base;
- ``out``: the path of an empty directory. Every regular file directly inside it when ``run`` returns is
  an output: non-empty, its name matching ``[a-z0-9][a-z0-9._-]*``;
- ``settings``: the name and value of each ``setting`` check of the resource;
- ``secrets``: the name and absolute path of each ``secret`` check of the resource. `factory run` never
  opens a key file; the tool reads it.

A tool ends in one of three other ways:

- ``raise Failed(reason, usd=None, compute_min=None)``: the call failed. ``reason`` should be one of
  ``REASONS`` (any other is recorded as ``tool failed``); an amount that is None is not known and is
  counted as the job's expected amount.
- ``raise Down()``: the resource is not usable and nothing was spent. Nothing is recorded, and the job
  may be run again under the same PASS.
- anything else it raises or returns is recorded as a failure at the expected cost.

What a tool prints is dropped, and so is the text of any exception it raises.

A tool gets the two classes with this one line, wherever its file is:

    from kit.lib.tools import Down, Failed
"""


class Failed(Exception):
    """The call failed: ``reason`` out of the tool's ``REASONS``, and the amounts spent when known."""

    def __init__(self, reason, usd=None, compute_min=None):
        super().__init__(reason)
        self.reason = reason
        self.usd = usd
        self.compute_min = compute_min


class Down(Exception):
    """The tool's resource is not usable and nothing was spent."""
