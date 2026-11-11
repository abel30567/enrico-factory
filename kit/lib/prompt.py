"""The one prompt renderer: fills a role body's ``{{NAME}}`` placeholders (docs/harness.md, section 3)."""

import re

_PLACEHOLDER = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")


class PromptError(Exception):
    pass


def render(body: str, values: dict[str, str]) -> str:
    """``body`` with every ``{{NAME}}`` replaced by ``values[NAME]`` in one pass.

    A value is inserted as is: a ``{{X}}`` inside it is not expanded again. A value no placeholder
    uses is not an error; a placeholder with no value is, the first by position.
    """
    for match in _PLACEHOLDER.finditer(body):
        if match.group(1) not in values:
            raise PromptError(f"no value for {{{{{match.group(1)}}}}}")
    return _PLACEHOLDER.sub(lambda match: values[match.group(1)], body)
