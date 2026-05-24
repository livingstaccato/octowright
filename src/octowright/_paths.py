# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Path-containment helpers.

These exist so a single, audited check sits between an external (LLM- or
operator-supplied) name and any filesystem operation. The check resolves
both the candidate and the root to absolute symlink-free paths and verifies
the candidate is anchored under the root.

Used by macros/scenarios/recording-writers wherever a path is built from
untrusted input. Centralised so a future hardening change lands in one place.
"""

from __future__ import annotations

from pathlib import Path


def safe_under(candidate: Path, root: Path) -> bool:
    """Return True iff ``candidate`` resolves to a path inside ``root``.

    Both sides are ``resolve()``-d so symlinks, ``..`` segments, and any
    other path tricks are flattened before the containment check.
    """
    try:
        resolved_candidate = candidate.resolve()
        resolved_root = root.resolve()
    except OSError:
        return False
    return resolved_candidate == resolved_root or resolved_candidate.is_relative_to(resolved_root)


def reject_unsafe_path(candidate: Path, root: Path, *, label: str) -> Path:
    """Resolve ``candidate`` and raise ``ValueError`` unless it lives under
    ``root``. Returns the resolved candidate on success so callers can keep
    using the canonicalised path."""
    if not safe_under(candidate, root):
        raise ValueError(f"{label} {str(candidate)!r} resolves outside {str(root)!r}")
    return candidate.resolve()
