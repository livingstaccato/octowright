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

#: Syntax for an operator-facing plugin name: lowercase, starts with a letter,
#: no separators that could escape a path segment or a URL segment. A hyphen is
#: allowed here because an entry-point name never enters a recording filename --
#: it is what an operator writes in ``OCTOWRIGHT_PLUGINS`` and what appears in
#: the asset route, so ``my-plugin`` stays legal.
#: Uses \Z not $, because $ matches at the end of the string OR just before a
#: single trailing newline, so "terminal\n" would incorrectly pass.
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}\Z")

#: Syntax for a session KIND. Identical to :data:`NAME_RE` minus the hyphen,
#: and the difference is load-bearing rather than stylistic.
#:
#: ``recorder.new_log_path`` composes ``{stamp}-{kind}-{instance_id}[-{label}]``
#: and ``http/artifacts.instance_id_from_recording_name`` reads the id back as
#: ``stem.split("-")[2]``. The stamp never contains a hyphen and the label sits
#: at index 3 or later, so those two are harmless -- but a hyphen in the kind or
#: the id shifts every later field and the id parses to the wrong token. A kind
#: of ``my-plugin`` would make every artifact/video/trace lookup for its
#: sessions resolve to ``plugin``, and would let an unrelated session id collide
#: with that token.
#:
#: Constraining the two composed fields is what keeps the parser exact, and it
#: costs a plugin nothing: underscore is available and already matches the
#: enforced ``{kind}_toolname`` prefix convention, which a hyphenated kind would
#: have rendered as ``my-plugin_launch``.
KIND_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}\Z")

#: Syntax for an ``instance_id`` a plugin supplies to ``ctx.begin_session``.
#:
#: Hyphen-free for the filename reason above. A leading DIGIT is allowed
#: because core's own ids are ``uuid.uuid4().hex[:12]`` and routinely start with
#: one, so a letters-first rule would reject the convention core itself follows.
#: Lowercase-only because the id also becomes a directory name under
#: ``session-artifacts/``, and on a case-insensitive filesystem (macOS) ids
#: differing only in case would silently share one artifact directory.
INSTANCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}\Z")

#: Kinds core owns. A plugin claiming one would shadow a browser engine in the
#: registry, or collide with the ``unknown`` classification closed-session
#: discovery emits for a recording it cannot identify.
RESERVED_KINDS: frozenset[str] = frozenset({"chromium", "firefox", "webkit", "browser", "unknown", "session"})


def validate_name(value: str, *, label: str) -> None:
    """Raise ``PluginLoadError`` unless ``value`` matches :data:`NAME_RE`."""
    if not NAME_RE.fullmatch(value):
        raise PluginLoadError(f"{label} {value!r} must match {NAME_RE.pattern}")


def validate_kind(kind: str) -> None:
    """Validate a session kind's syntax and reject reserved names."""
    if not KIND_RE.fullmatch(kind):
        raise PluginLoadError(f"plugin kind {kind!r} must match {KIND_RE.pattern}")
    if kind in RESERVED_KINDS:
        raise PluginLoadError(f"plugin kind {kind!r} is reserved by core")


def validate_instance_id(instance_id: str) -> None:
    """Raise ``ValueError`` unless ``instance_id`` is a safe filename component.

    ``ValueError`` rather than ``PluginLoadError``: this runs per launch, long
    after the plugin loaded, and matches what the path-containment guard beside
    it raises for the same class of bad input.
    """
    if not INSTANCE_ID_RE.fullmatch(instance_id):
        raise ValueError(f"instance id {instance_id!r} must match {INSTANCE_ID_RE.pattern}")


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
