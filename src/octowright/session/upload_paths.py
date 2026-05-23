# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Allowlist-based validation for file paths handed to ``set_input_files``.

This lives under ``octowright.session`` (rather than ``octowright.server``)
so the validator runs for both code paths that can drive an upload:

1. The MCP tool wrapper in ``server.browser.input``.
2. Macro replay, which dispatches actions straight at ``BrowserSession``
   methods via :mod:`octowright.macros.runtime`.

Keeping a single validator in the session layer means a recorded macro
cannot be replayed to exfiltrate arbitrary files just because its action
JSON was hand-edited.
"""

from __future__ import annotations

import os
from pathlib import Path

from octowright import defaults


def _allowed_upload_roots() -> list[Path]:
    """Resolve every directory an LLM-driven upload may read from.

    Always includes the daemon's CWD and the configured staging dir. Extra
    roots come from OCTOWRIGHT_UPLOAD_ROOTS (os.pathsep-separated). Each root
    is .resolve()'d so symlink games at the root level don't bypass the
    allowlist comparison below.
    """
    roots: list[Path] = [Path.cwd().resolve(), defaults.UPLOAD_STAGING_DIR.expanduser().resolve()]
    extra_raw = defaults.UPLOAD_EXTRA_ROOTS_RAW
    if extra_raw:
        for chunk in extra_raw.split(os.pathsep):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                roots.append(Path(chunk).expanduser().resolve())
            except OSError:
                # A bogus entry in the env var must not crash every upload.
                continue
    # Deduplicate while preserving order.
    seen: set[Path] = set()
    unique: list[Path] = []
    for r in roots:
        if r in seen:
            continue
        seen.add(r)
        unique.append(r)
    return unique


def validate_upload_path(path: str) -> Path:
    """Resolve and allowlist-check a single LLM-supplied upload path.

    ``.resolve()`` collapses symlinks so a symlink under an allowed root that
    points at e.g. /etc/passwd resolves to /etc/passwd and is rejected. Raises
    ``ValueError`` on rejection (matches the arg-validation style used
    elsewhere in the tool surface, e.g. browser_quick_launch).
    """
    if not isinstance(path, str) or not path:
        raise ValueError("upload path must be a non-empty string")
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError as exc:
        raise ValueError(f"upload path {path!r} could not be resolved: {exc}") from exc
    if not resolved.exists():
        raise ValueError(f"upload path {str(resolved)!r} does not exist")
    roots = _allowed_upload_roots()
    for root in roots:
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        return resolved
    allowed = ", ".join(str(r) for r in roots)
    raise ValueError(
        f"upload path {str(resolved)!r} is outside the allowed roots; "
        f"move the file under one of: {allowed} "
        f"(or extend OCTOWRIGHT_UPLOAD_ROOTS for additional roots)"
    )
