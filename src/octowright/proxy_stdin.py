# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The follower's stdin, read so that the bridge can stop without the client.

mcp's ``stdio_server`` reads stdin in a worker thread that cannot be
abandoned. A bridge that gives up on its leader -- gone for longer than the
recovery window -- leaves ``stdio_server`` by raising, which cancels that
read, but the cancellation took effect only once the read returned. So the
follower stayed inside ``stdio_server`` until the client next wrote a line or
closed stdin, and neither exited nor respawned a daemon (the documented
recovery) for as long as an idle client said nothing.

``stdio_server`` here hands mcp's a stdin whose read is abandoned on
cancellation. The blocked read is left behind in a daemon thread: the
follower is about to exit, and the descriptor it reads is never closed under
it. Passing a stdin of our own skips mcp's claim of fd 0, so the
claim is made here the same way: the wire moves to a private close-on-exec
descriptor and fd 0 points at the null device while the bridge serves, so a
child cannot read the client's requests, and fd 0 is restored on exit.

Windows needs one more step. There a read left waiting on a pipe holds that
pipe's file object, and every other operation on it -- the ``dup2`` that
restores fd 0, an ``fstat`` -- waits for the read, so an abandoned read would
hang the exit it was abandoned for. The waiting read is cancelled
(``CancelSynchronousIo``) before fd 0 is restored, and fd 0 is left on the
null device if it cannot be.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from io import TextIOWrapper
from typing import Any, BinaryIO, cast

import anyio
from mcp.server.stdio import stdio_server as _mcp_stdio_server
from provide.telemetry import get_logger

log = get_logger(__name__)

_STDIN_FD = 0

# How long the exit waits for a cancelled Windows read to give up its pipe.
_CANCEL_READ_SECONDS = 2.0


class _UnownedTextWrapper(TextIOWrapper):
    """Text layer whose close never closes the buffer under it (which may be
    ``sys.stdin``'s own, or a descriptor a reader thread still blocks on)."""

    def close(self) -> None:
        with suppress(ValueError):
            self.detach()


def _settle(future: asyncio.Future[str], line: str | None, error: BaseException | None) -> None:
    if future.done():  # cancelled: the read was abandoned
        return
    if error is not None:
        future.set_exception(error)
    else:
        future.set_result(cast("str", line))


class AbandonableLines(anyio.AsyncFile[str]):
    """An ``AsyncFile`` whose ``readline`` returns at once when cancelled.

    Each line is read in a daemon thread of its own, not an anyio worker:
    those are not daemon threads, so one left blocked in a read would hold up
    the interpreter's exit just as it held up ``stdio_server``'s. A cancelled
    read carries on in its thread and its line, if one ever arrives, is
    dropped -- the bridge cancels its stdin only on its way out. asyncio
    only, as the follower is.
    """

    _reader: threading.Thread | None = None

    async def readline(self) -> str:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()

        def read() -> None:
            line: str | None = None
            error: BaseException | None = None
            try:
                line = self.wrapped.readline()
            except BaseException as exc:  # handed to the awaiting task
                error = exc
            with suppress(RuntimeError):  # the loop closed while we read
                loop.call_soon_threadsafe(_settle, future, line, error)

        self._reader = threading.Thread(target=read, name="octowright-stdin", daemon=True)
        self._reader.start()
        return await future

    def release_wire(self) -> bool:
        """True once no read holds the wire, so fd 0 can be restored from it.

        Only Windows needs this (see the module docstring); elsewhere a waiting
        read blocks nothing else and is left behind.
        """
        reader = self._reader
        if sys.platform != "win32" or reader is None:
            return True
        deadline = time.monotonic() + _CANCEL_READ_SECONDS  # pragma: no cover
        while reader.is_alive() and time.monotonic() < deadline:  # pragma: no cover
            _cancel_synchronous_io(reader)
            reader.join(0.02)
        return not reader.is_alive()  # pragma: no cover


def _cancel_synchronous_io(thread: threading.Thread) -> None:
    """Fail the read ``thread`` is blocked in, if it is in one yet."""
    if sys.platform == "win32" and thread.native_id is not None:  # pragma: no cover
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenThread.restype = wintypes.HANDLE
        kernel32.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.CancelSynchronousIo.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.OpenThread(_THREAD_TERMINATE, False, thread.native_id)
        if handle:
            kernel32.CancelSynchronousIo(handle)
            kernel32.CloseHandle(handle)


# The access right CancelSynchronousIo requires of the thread handle.
_THREAD_TERMINATE = 0x0001


def _rebind_windows_handle() -> None:
    if sys.platform == "win32":  # pragma: no cover
        from mcp.os.win32.utilities import rebind_std_handle_to_fd

        with suppress(OSError):
            rebind_std_handle_to_fd(_STDIN_FD)


def _private_duplicate() -> int:
    """fd 0 duplicated above the standard range, not inherited by children."""
    if sys.platform == "win32":  # pragma: no cover
        duplicate = os.dup(_STDIN_FD)  # non-inheritable by default
        if duplicate <= 2:
            os.close(duplicate)
            raise OSError("duplicate of stdin landed in the standard range")
        return duplicate
    import fcntl

    return fcntl.fcntl(_STDIN_FD, fcntl.F_DUPFD_CLOEXEC, 3)


def _divert_stdin() -> tuple[BinaryIO, int | None]:
    """The wire to read and the private descriptor fd 0 is restored from.

    Best-effort, like ``stdio_server``'s own claim: when fd 0 cannot be moved
    the wire is read in place through ``sys.stdin`` and nothing is restored.
    """
    try:
        in_place = sys.stdin.buffer
        backed = in_place.fileno() == _STDIN_FD
    except (AttributeError, OSError, ValueError):
        backed = False
    if not backed:
        return sys.stdin.buffer, None
    try:
        private_fd = _private_duplicate()
    except OSError:
        return in_place, None
    try:
        diversion_fd = os.open(os.devnull, os.O_RDONLY)
        try:
            os.dup2(diversion_fd, _STDIN_FD)
        finally:
            os.close(diversion_fd)
    except OSError:
        # A Windows dup2 can close its target before failing: put the wire back.
        with suppress(OSError):
            os.dup2(private_fd, _STDIN_FD)
        _rebind_windows_handle()
        os.close(private_fd)
        return in_place, None
    _rebind_windows_handle()
    # closefd=False: an abandoned reader may still block on it.
    return os.fdopen(private_fd, "rb", closefd=False), private_fd


@asynccontextmanager
async def stdio_server() -> AsyncIterator[tuple[Any, Any]]:
    """mcp's ``stdio_server`` over the claimed, abandonable stdin.

    The bridge's only stdio entry point (tests replace it by this name).
    """
    wire, private_fd = _divert_stdin()
    stdin = AbandonableLines(_UnownedTextWrapper(wire, encoding="utf-8", errors="replace"))
    try:
        async with _mcp_stdio_server(stdin=stdin) as streams:
            yield streams
    finally:
        if private_fd is not None and not stdin.release_wire():
            # Restoring it would wait for the read the cancel could not end.
            log.warning("octowright.bridge.stdin_restore_skipped", reason="read still pending")
        elif private_fd is not None:
            try:
                os.dup2(private_fd, _STDIN_FD)
            except OSError as exc:
                log.warning("octowright.bridge.stdin_restore_failed", error=repr(exc))
            else:
                _rebind_windows_handle()
