# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Bridge capability token: leader-side /mcp auth + follower-side presentation.

The loopback /mcp transport drives browsers (RCE-equivalent) with zero auth, so
any local process can POST to it. A per-leader token, held only in the 0600
lockfile, gates /mcp: a process that can't read the lockfile (a *different user*
on a shared host, or a sandboxed process) can no longer drive the leader. This
does NOT defend against a same-user process that reads the 0600 lockfile — that
gets the token; the lockfile is the same-user trust boundary.

Pins the units: the lockfile token field (+ back-compat default), make_leader_info,
resolve_leader_token's loopback gate, and the BridgeTokenGuard ASGI check.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from octowright import singleton


class TestLeaderInfoToken:
    def test_token_round_trips_through_json(self) -> None:
        info = singleton.LeaderInfo(
            pid=1,
            http_host="127.0.0.1",
            http_port=6286,
            mcp_url="http://127.0.0.1:6286/mcp/",
            started_at=0.0,
            token="sekret",
        )
        assert singleton.LeaderInfo.from_json(info.to_json()).token == "sekret"

    def test_old_lockfile_without_token_defaults_empty(self) -> None:
        # A pre-upgrade daemon's lockfile has no "token" key; it must still parse.
        s = json.dumps(
            {
                "pid": 1,
                "http_host": "127.0.0.1",
                "http_port": 6286,
                "mcp_url": "http://127.0.0.1:6286/mcp/",
                "started_at": 0.0,
            }
        )
        assert singleton.LeaderInfo.from_json(s).token == ""


class TestMakeLeaderInfo:
    def test_carries_caller_token(self) -> None:
        info = singleton.make_leader_info("127.0.0.1", 6286, token="cap-123")
        assert info.token == "cap-123"

    def test_default_token_empty(self) -> None:
        info = singleton.make_leader_info("127.0.0.1", 6286)
        assert info.token == ""


class TestResolveLeaderToken:
    def _info(self, token: str, *, host: str = "127.0.0.1") -> singleton.LeaderInfo:
        return singleton.LeaderInfo(
            pid=os.getpid(),
            http_host=host,
            http_port=6286,
            mcp_url=f"http://{host}:6286/mcp/",
            started_at=0.0,
            token=token,
        )

    def test_returns_token_for_loopback_lock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from octowright import proxy_runtime

        monkeypatch.setattr(singleton, "read_lock", lambda *a, **k: self._info("tk-xyz"))
        monkeypatch.setattr(singleton, "is_stale", lambda info: False)
        assert proxy_runtime.resolve_leader_token() == "tk-xyz"

    def test_no_token_for_nonloopback_lock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from octowright import proxy_runtime

        # A poisoned/remote lock URL must not leak the token to it.
        monkeypatch.setattr(singleton, "read_lock", lambda *a, **k: self._info("tk-xyz", host="evil.example"))
        monkeypatch.setattr(singleton, "is_stale", lambda info: False)
        monkeypatch.delenv("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD", raising=False)
        assert proxy_runtime.resolve_leader_token() == ""

    def test_no_lock_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from octowright import proxy_runtime

        monkeypatch.setattr(singleton, "read_lock", lambda *a, **k: None)
        assert proxy_runtime.resolve_leader_token() == ""


# ─── BridgeTokenGuard (leader-side /mcp auth) ──────────────────────────────


class _OkApp:
    """Inner ASGI app: always 200, records that it was reached."""

    def __init__(self) -> None:
        self.reached = False

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.reached = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


async def _drive(app: Any, *, token: str | None) -> int:
    headers: list[tuple[bytes, bytes]] = []
    if token is not None:
        headers.append((b"x-octowright-token", token.encode()))
    scope = {"type": "http", "method": "POST", "path": "/", "headers": headers}
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg: dict[str, Any]) -> None:
        sent.append(msg)

    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    return int(start["status"])


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class TestBridgeTokenGuard:
    @pytest.mark.anyio
    async def test_correct_token_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from octowright.http import bridge_auth

        monkeypatch.delenv("OCTOWRIGHT_BRIDGE_REQUIRE_TOKEN", raising=False)
        inner = _OkApp()
        guard = bridge_auth.BridgeTokenGuard(inner, "good-token")
        assert await _drive(guard, token="good-token") == 200
        assert inner.reached

    @pytest.mark.anyio
    async def test_wrong_token_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from octowright.http import bridge_auth

        monkeypatch.delenv("OCTOWRIGHT_BRIDGE_REQUIRE_TOKEN", raising=False)
        inner = _OkApp()
        guard = bridge_auth.BridgeTokenGuard(inner, "good-token")
        assert await _drive(guard, token="bad") == 403
        assert not inner.reached

    @pytest.mark.anyio
    async def test_missing_token_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from octowright.http import bridge_auth

        monkeypatch.delenv("OCTOWRIGHT_BRIDGE_REQUIRE_TOKEN", raising=False)
        inner = _OkApp()
        guard = bridge_auth.BridgeTokenGuard(inner, "good-token")
        assert await _drive(guard, token=None) == 403
        assert not inner.reached

    @pytest.mark.anyio
    async def test_disabled_bypasses_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from octowright.http import bridge_auth

        monkeypatch.setenv("OCTOWRIGHT_BRIDGE_REQUIRE_TOKEN", "off")
        inner = _OkApp()
        guard = bridge_auth.BridgeTokenGuard(inner, "good-token")
        # Even a wrong token is allowed through when the operator disabled the gate.
        assert await _drive(guard, token="bad") == 200
        assert inner.reached

    @pytest.mark.anyio
    async def test_empty_expected_token_bypasses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from octowright.http import bridge_auth

        monkeypatch.delenv("OCTOWRIGHT_BRIDGE_REQUIRE_TOKEN", raising=False)
        # No token configured (e.g. follower built the app without one) → no gate.
        inner = _OkApp()
        guard = bridge_auth.BridgeTokenGuard(inner, "")
        assert await _drive(guard, token=None) == 200
        assert inner.reached
