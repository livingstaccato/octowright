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

import os
import tempfile
from collections.abc import Awaitable, Callable
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


def _make_temp_sibling(path: Path) -> Path:
    """Create a hidden empty sibling temp file in ``path.parent`` and return it.

    Used by the atomic-write helpers below; lives here so callers don't have
    to re-derive the ``.{name}.<rand>.tmp`` naming convention.
    """
    with tempfile.NamedTemporaryFile(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        return Path(tmp.name)


def _inherit_target_mode(target: Path, tmp_path: Path) -> None:
    # NamedTemporaryFile creates the temp at 0o600; if ``target`` already
    # exists with more permissive bits, os.replace would silently demote
    # them. Copy the existing mode onto the temp before the swap so an
    # atomic write is a true content replacement, not a permission change.
    try:
        st = os.stat(target)
    except FileNotFoundError:
        return
    try:
        os.chmod(tmp_path, st.st_mode & 0o7777)
    except OSError:
        pass


async def atomic_write_via_writer(path: Path, writer: Callable[[Path], Awaitable[None]]) -> None:
    """Run ``writer(tmp_path)`` then ``os.replace(tmp_path, path)`` atomically.

    Defeats the symlink-swap TOCTOU window between the caller's path
    containment check and the actual write: a same-user attacker who
    replaces ``path`` (or any segment of its parent) with a symlink between
    resolve() and the writer's open() could redirect the bytes. Staging
    into a sibling temp file inside the same already-resolved parent and
    atomically renaming closes that window.

    The ``writer`` is responsible for writing to (or having the underlying
    tool write to) the temp path. On any exception the temp file is
    best-effort unlinked; on success ``os.replace`` consumes it.
    """
    tmp_path = _make_temp_sibling(path)
    cleanup: Path | None = tmp_path
    try:
        await writer(tmp_path)
        _inherit_target_mode(path, tmp_path)
        os.replace(tmp_path, path)
        cleanup = None
    finally:
        if cleanup is not None:
            try:
                cleanup.unlink()
            except OSError:
                pass


def atomic_write_text(path: Path, body: str, *, encoding: str = "utf-8") -> None:
    """Synchronous sibling of :func:`atomic_write_via_writer` for plain text."""
    tmp_path = _make_temp_sibling(path)
    cleanup: Path | None = tmp_path
    try:
        tmp_path.write_text(body, encoding=encoding)
        _inherit_target_mode(path, tmp_path)
        os.replace(tmp_path, path)
        cleanup = None
    finally:
        if cleanup is not None:
            try:
                cleanup.unlink()
            except OSError:
                pass
