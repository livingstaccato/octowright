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
import secrets
import stat
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from octowright.request_errors import InvalidRequestError


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
    """Resolve ``candidate`` and raise ``InvalidRequestError`` unless it lives
    under ``root``. Returns the resolved candidate on success so callers can
    keep using the canonicalised path.

    ``InvalidRequestError`` is a ``ValueError`` subclass, so every existing
    ``except ValueError`` still catches it; it exists so a sink describing a
    *component's* health can tell "the caller asked for something disallowed"
    apart from "the component broke". See ``octowright.request_errors``.

    ``label`` names the ARGUMENT (``"har_path"``) and nothing else, because
    the message already prints the path. Four of twenty call sites had drifted
    into interpolating it as well, so the live rejection read ``screenshot path
    '/tmp/x.png' '/tmp/x.png' resolves outside '.../sessions'``. Enforced at
    the call sites by ``tests/test_path_guard_message.py`` rather than deduped
    here: a dedupe has to decide whether an occurrence in the label IS the
    rendered path, and the cheap spelling gets that wrong in the direction
    that loses information -- for candidate ``x`` and label ``macro name
    'xylophone'`` a substring test matches and drops the path entirely. A
    label naming a DISTINCT input (``macro name 'x'``, where the name is not
    the resolved path) is useful, common in the forwarded-label call sites,
    and left alone.
    """
    if not safe_under(candidate, root):
        raise InvalidRequestError(f"{label} {str(candidate)!r} resolves outside {str(root)!r}")
    return candidate.resolve()


#: Whether the parent directory can be held open and written through, so the
#: temp file and the final rename cannot be redirected by swapping a path
#: component for a symlink. POSIX; on Windows the helpers fall back to names.
_DIR_FD_SUPPORTED = (
    os.open in os.supports_dir_fd
    and os.rename in os.supports_dir_fd  # os.replace shares its implementation
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
)


def _relative_beneath(directory: Path, root: Path) -> tuple[Path, Path]:
    """``(root_to_open, directory relative to it)``, without resolving ``directory``.

    Resolving ``directory`` would follow the very symlinks the walk refuses.
    ``root`` may be spelled through a symlink of the operator's own (a
    symlinked home or state dir), so its resolved form is tried too.
    """
    absolute = Path(os.path.abspath(directory))
    for anchor in (Path(os.path.abspath(root)), root.resolve()):
        try:
            return anchor, absolute.relative_to(anchor)
        except ValueError:
            continue
    raise InvalidRequestError(f"directory {str(directory)!r} is not under {str(root)!r}")


def _open_parent(path: Path, root: Path | None) -> int:
    """An open descriptor for ``path.parent``; walked from ``root`` without following symlinks.

    Without ``root`` the descriptor still pins ONE directory for the temp file
    and the rename. With it, every component below ``root`` is opened with
    ``O_NOFOLLOW``, so a component replaced by a symlink after the caller's
    containment check is refused instead of followed.
    """
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    if root is None:
        return os.open(path.parent, flags)
    anchor, relative = _relative_beneath(path.parent, root)
    fd = os.open(anchor, flags)
    for part in relative.parts:
        try:
            if part == "..":
                raise NotADirectoryError(part)
            next_fd = os.open(part, flags | os.O_NOFOLLOW, dir_fd=fd)
        except (NotADirectoryError, OSError) as exc:
            os.close(fd)
            if isinstance(exc, FileNotFoundError):
                raise
            raise InvalidRequestError(
                f"{part!r} under {str(anchor)!r} is not a plain directory (a symlink?); refusing to write through it"
            ) from None
        os.close(fd)
        fd = next_fd
    return fd


def _create_temp_in(dir_fd: int, name: str) -> str:
    """Create an empty ``0600`` temp sibling of ``name`` inside ``dir_fd``; return its name."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    for _ in range(100):
        tmp_name = f".{name}.{secrets.token_hex(6)}.tmp"
        try:
            os.close(os.open(tmp_name, flags, 0o600, dir_fd=dir_fd))
        except FileExistsError:
            continue
        return tmp_name
    raise FileExistsError(f"could not create a temp sibling of {name!r}")


def _inherit_target_mode_at(dir_fd: int, name: str, tmp_name: str) -> None:
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISREG(st.st_mode):
        try:
            os.chmod(tmp_name, st.st_mode & 0o7777, dir_fd=dir_fd)
        except OSError:
            pass


def _unlink_at(dir_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=dir_fd)
    except OSError:
        pass


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


async def atomic_write_via_writer(
    path: Path, writer: Callable[[Path], Awaitable[None]], *, root: Path | None = None
) -> None:
    """Run ``writer(tmp_path)`` then ``os.replace(tmp_path, path)`` atomically.

    A symlink at ``path`` itself is replaced, never followed. On POSIX the
    parent directory is also held open for the whole write (walked from
    ``root`` without following symlinks when it is given), and the temp file
    is created and renamed through that descriptor, so a parent swapped for a
    symlink after the caller's containment check cannot redirect the rename
    onto a file elsewhere. Earlier versions claimed this and staged by name,
    which a swap between check and rename defeated.

    Residual limit: ``writer`` can only be handed a path, so a parent swapped
    WHILE it runs sends the writer's bytes through the new link to a fresh
    temp name there. That is detected (the name no longer reaches the file
    created here) and the write is refused rather than published; it cannot
    replace an existing file outside the directory. Windows has no directory
    descriptors and keeps the name-based staging.

    The ``writer`` is responsible for writing to (or having the underlying
    tool write to) the temp path. On any exception the temp file is
    best-effort unlinked; on success ``os.replace`` consumes it.
    """
    if _DIR_FD_SUPPORTED:
        await _atomic_write_via_writer_at(path, writer, root)
        return
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


async def _atomic_write_via_writer_at(path: Path, writer: Callable[[Path], Awaitable[None]], root: Path | None) -> None:
    dir_fd = _open_parent(path, root)
    tmp_name: str | None = None
    try:
        tmp_name = _create_temp_in(dir_fd, path.name)
        await writer(path.parent / tmp_name)
        # The writer could only be given a NAME, so it reached the temp file
        # through ``path.parent`` as it stood then. If that is no longer the
        # directory held open here, its bytes went somewhere else, and
        # renaming would publish the empty file we created -- refuse instead.
        pinned = os.stat(tmp_name, dir_fd=dir_fd, follow_symlinks=False)
        try:
            named = os.stat(path.parent / tmp_name, follow_symlinks=False)
        except OSError:
            named = None
        if named is None or (named.st_dev, named.st_ino) != (pinned.st_dev, pinned.st_ino):
            raise InvalidRequestError(
                f"directory {str(path.parent)!r} changed during the write; refusing to publish it"
            )
        _inherit_target_mode_at(dir_fd, path.name, tmp_name)
        os.replace(tmp_name, path.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        tmp_name = None
    finally:
        if tmp_name is not None:
            _unlink_at(dir_fd, tmp_name)
        os.close(dir_fd)


def atomic_write_text(path: Path, body: str, *, encoding: str = "utf-8", root: Path | None = None) -> None:
    """Synchronous sibling of :func:`atomic_write_via_writer` for plain text.

    Pass the containment ``root`` the caller validated ``path`` against, and
    the directory is re-walked from it without following symlinks (POSIX).
    """
    if _DIR_FD_SUPPORTED:
        _atomic_write_text_at(path, body, encoding, root)
        return
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


def _atomic_write_text_at(path: Path, body: str, encoding: str, root: Path | None) -> None:
    dir_fd = _open_parent(path, root)
    tmp_name: str | None = None
    try:
        tmp_name = _create_temp_in(dir_fd, path.name)
        fd = os.open(tmp_name, os.O_WRONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), dir_fd=dir_fd)
        with open(fd, "w", encoding=encoding) as fh:
            fh.write(body)
        _inherit_target_mode_at(dir_fd, path.name, tmp_name)
        os.replace(tmp_name, path.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        tmp_name = None
    finally:
        if tmp_name is not None:
            _unlink_at(dir_fd, tmp_name)
        os.close(dir_fd)
