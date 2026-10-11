# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The follower's stdin read is abandoned on the way out (``proxy_stdin``).

mcp's own stdin read cannot be abandoned, so a bridge that gave up on its
leader stayed in ``stdio_server`` until the client next wrote or closed stdin.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404
import sys
import textwrap
import threading
import time
from io import TextIOWrapper
from pathlib import Path

import anyio

from octowright.proxy_stdin import AbandonableLines
from tests._serve_subprocess import isolated_env

# A read the fix abandons returns at once; one it does not waits for the
# writer below, which unblocks it only after this long.
_UNBLOCK_AFTER_SECONDS = 5.0


def test_a_read_with_no_writer_is_abandoned_when_cancelled() -> None:
    read_fd, write_fd = os.pipe()
    # Unblocks the worker thread either way, so a regression fails instead of hanging.
    unblock = threading.Timer(_UNBLOCK_AFTER_SECONDS, lambda: os.write(write_fd, b"late\n"))
    unblock.start()
    lines = AbandonableLines(TextIOWrapper(os.fdopen(read_fd, "rb"), encoding="utf-8"))

    async def read_then_cancel() -> list[str]:
        seen: list[str] = []

        async def read_all() -> None:
            async for line in lines:
                seen.append(line)

        async with anyio.create_task_group() as tg:
            tg.start_soon(read_all)
            await anyio.sleep(0.2)
            tg.cancel_scope.cancel()
        return seen

    started = time.monotonic()
    try:
        seen = anyio.run(read_then_cancel, backend="asyncio")
        elapsed = time.monotonic() - started
    finally:
        unblock.join()
        os.close(write_fd)
    assert seen == []
    assert elapsed < _UNBLOCK_AFTER_SECONDS / 2, (
        f"cancelling a stdin read with nothing to read took {elapsed:.1f}s: the read was waited for, not abandoned"
    )


_CHILD = textwrap.dedent(
    """
    import os
    import stat
    import sys

    import anyio

    from octowright.proxy_stdin import stdio_server

    def kind() -> str:
        mode = os.fstat(0).st_mode
        return "pipe" if stat.S_ISFIFO(mode) else "chr" if stat.S_ISCHR(mode) else oct(mode)

    async def main() -> None:
        print("before", kind(), file=sys.stderr, flush=True)
        try:
            async with stdio_server() as (read, _write):
                print("during", kind(), repr(os.read(0, 64)), file=sys.stderr, flush=True)
                message = await read.receive()
                print("wire", message.message.method, file=sys.stderr, flush=True)
                # How the bridge leaves when it gives up on its leader.
                raise RuntimeError("leader health check failed")
        except Exception:  # the task group wraps it in a group
            pass
        print("left stdio_server", file=sys.stderr, flush=True)
        # fstat on a pipe a read still holds hangs on Windows.
        print("after", kind(), file=sys.stderr, flush=True)

    anyio.run(main)
    """
)


def test_the_wire_is_claimed_and_the_context_exits_while_the_client_is_silent(tmp_path: Path) -> None:
    """fd 0 points at the null device while serving and at the wire again
    after; and a bridge that gives up (it raises out of ``stdio_server``)
    leaves without waiting for the client's next line -- the client here
    keeps stdin open and says nothing more."""
    script = tmp_path / "child.py"
    script.write_text(_CHILD, encoding="utf-8")
    proc = subprocess.Popen(  # nosec B603
        [sys.executable, str(script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=isolated_env(tmp_path),
    )
    assert proc.stdin is not None and proc.stderr is not None
    try:
        proc.stdin.write(b'{"jsonrpc": "2.0", "id": 1, "method": "ping"}\n')
        proc.stdin.flush()
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise AssertionError(
                "the child stayed in stdio_server with the client silent: its stdin read was not abandoned. "
                + proc.stderr.read().decode(errors="replace")
            ) from None
        lines = proc.stderr.read().decode(errors="replace").splitlines()
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdin.close()
    report = dict(line.split(" ", 1) for line in lines if " " in line)
    assert proc.returncode == 0, lines
    assert report.get("before") == "pipe", lines
    assert report.get("during") == "chr b''", lines
    assert report.get("wire") == "ping", lines
    assert report.get("after") == "pipe", lines
