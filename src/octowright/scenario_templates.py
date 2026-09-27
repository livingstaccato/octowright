# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Substituting template args into an already-parsed scenario template.

The template is parsed BEFORE substitution (see
``scenarios.load_scenario_template``), which is what stops an arg from
rewriting the document -- but it also made every substituted value a string,
so ``record_video: "{{video}}"`` with ``video=false`` produced the truthy
string ``"false"``, which the bool validators then rejected.

A quoted scalar that is EXACTLY one placeholder therefore takes the arg's
scalar type. The text is resolved against the YAML 1.2 core schema's
bool/int/float/null spellings by regex, never by handing it to a YAML
parser, so no mapping, sequence, tag, anchor or alias can come from an arg.
Deliberately narrower than PyYAML's own resolver: YAML 1.1's ``yes``/``no``/
``on``/``off`` booleans (the "Norway problem"), timestamps, hex/octal and
``.inf``/``.nan`` stay strings, and so does the empty string -- 1.2 core
calls it null, but an empty arg meaning "absent" is not what a caller who
passed ``""`` wrote. A placeholder inside a longer string stays a string.
"""

from __future__ import annotations

import re
from typing import Any

_PLACEHOLDER = re.compile(r"\{\{([^{}]+)\}\}")
_NULL = re.compile(r"null|Null|NULL|~")
_TRUE = re.compile(r"true|True|TRUE")
_FALSE = re.compile(r"false|False|FALSE")
_INT = re.compile(r"[-+]?[0-9]+")
_FLOAT = re.compile(r"[-+]?(\.[0-9]+|[0-9]+(\.[0-9]*)?)([eE][-+]?[0-9]+)?")

# A block-mapping value or sequence item that is a bare ``{{name}}`` -- YAML
# reads it as a nested flow mapping, so the template fails to parse.
_BARE_PLACEHOLDER_LINE = re.compile(r"^(?P<lead>\s*(?:-\s+)?(?:[^\s#'\"][^#'\"]*?:\s+)?)(?P<ph>\{\{[^{}]+\}\})\s*$")


def coerce_scalar(text: str) -> Any:
    """``text`` as the YAML 1.2 core bool/int/float/null it spells, else unchanged."""
    if _NULL.fullmatch(text):
        return None
    if _TRUE.fullmatch(text):
        return True
    if _FALSE.fullmatch(text):
        return False
    if _INT.fullmatch(text):
        return int(text)
    if _FLOAT.fullmatch(text):
        return float(text)
    return text


def _whole_value(value: Any) -> Any:
    # A caller over MCP may pass a real JSON bool/number/null; keep it rather
    # than round-trip it through str. Anything else goes through the text.
    if value is None or isinstance(value, bool | int | float):
        return value
    return coerce_scalar(str(value))


def substitute_placeholders(node: Any, args: dict[str, Any]) -> Any:
    """Replace ``{{key}}`` inside every string of a parsed YAML tree.

    Keys are substituted too but always stay strings: a mapping key that
    turned into a bool or ``None`` is never what a template meant.
    """
    replacements = {f"{{{{{k}}}}}": str(v) for k, v in args.items()}
    return _substitute(node, args, replacements, is_value=True)


def _substitute(node: Any, args: dict[str, Any], replacements: dict[str, str], *, is_value: bool) -> Any:
    if isinstance(node, str):
        whole = _PLACEHOLDER.fullmatch(node)
        if is_value and whole is not None and whole.group(1) in args:
            return _whole_value(args[whole.group(1)])
        for placeholder, value in replacements.items():
            node = node.replace(placeholder, value)
        return node
    if isinstance(node, dict):
        return {
            _substitute(k, args, replacements, is_value=False): _substitute(v, args, replacements, is_value=True)
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_substitute(item, args, replacements, is_value=True) for item in node]
    return node


def unquoted_placeholder_hint(text: str) -> str:
    """How to fix the bare placeholders that stopped ``text`` parsing."""
    fixes = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = _BARE_PLACEHOLDER_LINE.match(line)
        if match is not None:
            lead, placeholder = match.group("lead"), match.group("ph")
            fixes.append(f'line {number}: {lead.strip()} {placeholder} -> {lead.strip()} "{placeholder}"'.strip())
    where = "; ".join(fixes) if fixes else 'e.g. persona: "{{persona_1}}"'
    return (
        f"quote every placeholder ({where}). A quoted placeholder that is the whole value still "
        "takes its argument's type: true/false becomes a boolean, 1280 a number, null None"
    )
