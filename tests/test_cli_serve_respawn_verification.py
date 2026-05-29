# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.cli import serve as _serve


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_respawn_waits_for_replacement_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    import octowright.daemonize as _daemonize_mod
    import octowright.singleton as _sn_mod

    monkeypatch.setattr(_sn_mod, "read_lock", lambda: None)
    monkeypatch.setattr(_sn_mod, "is_stale", lambda _info: True)
    monkeypatch.setattr(_sn_mod, "probe_http_alive", AsyncMock(return_value=False))

    spawn_daemon = MagicMock()
    wait_for_daemon = AsyncMock(return_value=None)
    monkeypatch.setattr(_daemonize_mod, "spawn_daemon", spawn_daemon)
    monkeypatch.setattr(_daemonize_mod, "wait_for_daemon", wait_for_daemon)

    captured: list[str] = []
    monkeypatch.setattr(_serve.click, "echo", lambda text, err=False: captured.append(text))

    await _serve._respawn_if_leader_gone(http_host="127.0.0.1", http_port=8765, idle_grace=60.0)

    spawn_daemon.assert_called_once_with(http_host="127.0.0.1", http_port=8765, idle_grace=60.0)
    wait_for_daemon.assert_awaited_once()
    assert any("replacement daemon spawn timed out" in line for line in captured)
