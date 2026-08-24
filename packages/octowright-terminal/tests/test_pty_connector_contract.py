# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import sys

import pytest
from provide.uterm.server.connectors import (
    build_connector,
    register_connector,
    registered_types,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="PTY connector is POSIX-only")


def _ensure_pty_registered() -> None:
    """Canonical registration snippet — reused verbatim by TerminalEngine.

    The PTY connector lives in provide-uterm-platform and registers under the
    type name "pty". Importing the module + registering is idempotent-guarded
    so calling this repeatedly is safe.
    """
    if "pty" not in registered_types():
        from provide.uterm.pty.connector import PTYConnector

        register_connector("pty", PTYConnector)


async def test_pty_connector_emits_cumulative_snapshot() -> None:
    _ensure_pty_registered()
    assert "pty" in registered_types()

    conn = build_connector("char-1", "char", "pty", {"command": "/bin/echo", "args": ["hello-uterm"]})
    await conn.start()
    try:
        screen = ""
        for _ in range(60):
            for msg in await conn.poll_messages():
                # Contract: PTY emits ONLY snapshot frames; screen is cumulative text.
                assert msg["type"] == "snapshot"
                assert isinstance(msg["screen"], str)
                assert "cursor" in msg and "cols" in msg and "rows" in msg
                screen = msg["screen"]
            if "hello-uterm" in screen:
                break
            await asyncio.sleep(0.05)
        assert "hello-uterm" in screen
    finally:
        await conn.stop()


async def test_pty_handle_input_returns_snapshot() -> None:
    _ensure_pty_registered()
    conn = build_connector("char-2", "char", "pty", {"command": "/bin/cat"})
    await conn.start()
    try:
        msgs = await conn.handle_input("ping\n")
        assert msgs and msgs[0]["type"] == "snapshot"
        # /bin/cat echoes input back into the cumulative buffer.
        screen = ""
        for _ in range(60):
            for msg in await conn.poll_messages():
                screen = msg["screen"]
            if "ping" in screen:
                break
            await asyncio.sleep(0.05)
        assert "ping" in screen
    finally:
        await conn.stop()
