"""Minimal {{placeholder}} template rendering.

Content templates only ever need value substitution, so the standard
library covers it; Jinja2 would add a dependency and a template-injection
surface for no gain.
"""

import re

PLACEHOLDER = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}", re.IGNORECASE)


class TemplateError(Exception):
    """A placeholder had no value."""


def render(template, values):
    """Replace every {{key}} with values[key]; unknown keys are an error."""
    missing = []

    def substitute(match):
        key = match.group(1)

        if key not in values:
            missing.append(key)
            return match.group(0)

        return str(values[key])

    result = PLACEHOLDER.sub(substitute, template)

    if missing:
        raise TemplateError(
            f"Missing template values: {', '.join(sorted(set(missing)))}"
        )

    return result


def placeholders(template):
    """Placeholder names used by a template."""
    return sorted({match.group(1) for match in PLACEHOLDER.finditer(template)})
