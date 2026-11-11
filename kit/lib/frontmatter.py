"""Front matter of role and format files: plain `key: value` lines between `---` lines.

No YAML. Values are strings; list values are comma-separated (`as_list`). A `#` at the start of a
line or preceded by whitespace starts a comment. Blank lines are ignored.
"""

import re

_COMMENT = re.compile(r"(^|\s)#.*$")


class FrontMatterError(ValueError):
    def __init__(self, line, message):
        super().__init__(f"line {line}: {message}")
        self.line = line


def parse(text):
    """Return (dict, body). Raises FrontMatterError with the 1-based line number."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise FrontMatterError(1, "missing opening ---")
    meta = {}
    for index in range(1, len(lines)):
        number = index + 1
        raw = lines[index].rstrip("\r\n")
        if raw == "---":
            return meta, "".join(lines[index + 1:])
        line = _COMMENT.sub("", raw).strip()
        if not line:
            continue
        if ":" not in line:
            raise FrontMatterError(number, f"no colon in {raw.strip()!r}")
        key, value = line.split(":", 1)
        key = key.strip()
        if not key:
            raise FrontMatterError(number, "empty key")
        if key in meta:
            raise FrontMatterError(number, f"duplicate key {key!r}")
        meta[key] = value.strip()
    raise FrontMatterError(1, "missing closing --- for the opening --- on this line")


def as_list(value):
    """Split a comma-separated value into trimmed, non-empty items."""
    return [item.strip() for item in (value or "").split(",") if item.strip()]
