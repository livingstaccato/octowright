# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="PTY is POSIX-only")


async def test_terminal_tool_lifecycle() -> None:
    from octowright.server.terminal import lifecycle

    launched = await lifecycle.terminal_launch(kind="pty", command="/bin/cat", label="t")
    iid = launched["instance_id"]
    try:
        assert launched["kind"] == "terminal"
        listed = await lifecycle.terminal_list()
        assert any(s["instance_id"] == iid for s in listed)

        await lifecycle.terminal_send_input(instance_id=iid, text="hi-tools\n")
        waited = await lifecycle.terminal_wait_for(instance_id=iid, text="hi-tools", timeout=5.0)
        assert waited["matched"] is True
        snap = await lifecycle.terminal_snapshot(instance_id=iid)
        assert "hi-tools" in snap["screen"]
    finally:
        closed = await lifecycle.terminal_close(instance_id=iid)
        assert closed["closed"] is True


async def test_terminal_close_refuses_protected_without_force() -> None:
    from octowright.server.terminal import lifecycle

    launched = await lifecycle.terminal_launch(kind="pty", command="/bin/cat", protected=True)
    iid = launched["instance_id"]
    try:
        result = await lifecycle.terminal_close(instance_id=iid)
        assert result["closed"] is False
        assert "protected" in result["reason"]
    finally:
        await lifecycle.terminal_close(instance_id=iid, force=True)


def test_terminals_profile_registered() -> None:
    from octowright.server.profiles import PROFILES

    assert "terminal_launch" in PROFILES["terminals"]
    assert "terminal_close" in PROFILES["terminals"]


def test_pool_raises_clean_error_when_terminal_pool_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Defensive guard: if the helper is ever reached with no terminal_pool wired
    # (a registration/state bug), it must fail loudly with a typed error — not a
    # bare AssertionError that `python -O` would strip into a NoneType crash.
    from octowright_terminal.errors import TerminalPoolUnavailableError

    from octowright.server.terminal import lifecycle

    monkeypatch.setattr(lifecycle, "terminal_pool", None)
    with pytest.raises(TerminalPoolUnavailableError):
        lifecycle._pool()
