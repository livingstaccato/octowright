# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Namespace rules for plugin identifiers.

Several identifiers flow into URLs, recording metadata, status output, and
lookup dictionaries, so they are validated before any plugin logic runs. The
entry-point *name* is the configured identity — what an operator writes in
``OCTOWRIGHT_PLUGINS`` and what appears in the asset route — while ``kind`` is
runtime metadata stamped into recordings.
"""

from __future__ import annotations

import re

from octowright.plugins.errors import PluginLoadError

#: One safe syntax for every plugin identifier: lowercase, starts with a
#: letter, no separators that could escape a path segment or a URL segment.
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

#: Kinds core owns. A plugin claiming one would shadow a browser engine in the
#: registry, or collide with the ``unknown`` classification closed-session
#: discovery emits for a recording it cannot identify.
RESERVED_KINDS: frozenset[str] = frozenset({"chromium", "firefox", "webkit", "browser", "unknown", "session"})


def validate_name(value: str, *, label: str) -> None:
    """Raise ``PluginLoadError`` unless ``value`` matches :data:`NAME_RE`."""
    if not NAME_RE.match(value):
        raise PluginLoadError(f"{label} {value!r} must match {NAME_RE.pattern}")


def validate_kind(kind: str) -> None:
    """Validate a session kind's syntax and reject reserved names."""
    validate_name(kind, label="plugin kind")
    if kind in RESERVED_KINDS:
        raise PluginLoadError(f"plugin kind {kind!r} is reserved by core")


def validate_tool_names(kind: str, tool_names: frozenset[str]) -> None:
    """Require every declared tool name to carry the ``{kind}_`` prefix.

    Enforced rather than advisory: it makes the cross-plugin collision check a
    fast path and keeps a third-party tool from squatting a name core may
    want later.
    """
    prefix = f"{kind}_"
    for name in sorted(tool_names):
        if not name.startswith(prefix):
            raise PluginLoadError(f"plugin tool {name!r} must start with {prefix!r}")
