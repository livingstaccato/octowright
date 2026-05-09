# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for the larger coordinator functions in octowright.cli.serve.

Helper-level tests live in `tests/test_cli_serve_branches.py`. The
existing decision-flow tests live in `tests/test_serve_promotion.py`
and `tests/test_serve_stdio_eof.py`. This file targets the remaining
coordinator surface:

- `serve` click command — every option flag wires through to env or kwargs;
  PROVIDE_LOG_LEVEL and OCTOWRIGHT_PROFILE export ordering.
- `_serve_async` — daemon-mode short-circuit + no-singleton short-circuit.
- `_run_follower` — health URL derivation and proxy_bridge invocation.
- `_serve_singleton` — happy follow path and inline-fallback short-circuit.
- `_run_leader` — paths the EOF tests don't pin: no_http skips sidecar,
  no_singleton skips lock write/remove, daemon-mode arms watchdog immediately.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from octowright.cli import serve as _serve


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _patch_async_run_capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace `_serve_async` with a recorder, then drive the coroutine
    that `serve()` hands to `asyncio.run`. We don't bypass run — we let
    it execute the recorder coroutine, which captures the kwargs."""
    captured: dict[str, Any] = {}

    async def fake_serve_async(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(_serve, "_serve_async", fake_serve_async)
    return captured


def _patch_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub setup_telemetry / shutdown_telemetry so they don't hit real telemetry."""
    monkeypatch.setattr(_serve, "setup_telemetry", lambda: None)
    monkeypatch.setattr(_serve, "shutdown_telemetry", lambda: None)


# ─── serve click command: option wiring ─────────────────────────────────────


class TestServeCommandOptions:
    def test_no_options_passes_defaults_to_serve_async(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Bare `serve` invocation passes None/False defaults to _serve_async."""
        _patch_telemetry(monkeypatch)
        captured = _patch_async_run_capture(monkeypatch)
        result = CliRunner().invoke(_serve.serve, [])
        assert result.exit_code == 0
        assert captured == {
            "http_host": None,
            "http_port": None,
            "no_http": False,
            "keep_alive": False,
            "idle_grace": None,
            "no_singleton": False,
            "daemon_mode": False,
        }

    def test_http_port_option_wired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--http-port=N → http_port=N int passed to _serve_async."""
        _patch_telemetry(monkeypatch)
        captured = _patch_async_run_capture(monkeypatch)
        CliRunner().invoke(_serve.serve, ["--http-port", "9000"])
        assert captured["http_port"] == 9000

    def test_http_host_option_wired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--http-host=X → http_host=X str passed to _serve_async."""
        _patch_telemetry(monkeypatch)
        captured = _patch_async_run_capture(monkeypatch)
        CliRunner().invoke(_serve.serve, ["--http-host", "0.0.0.0"])
        assert captured["http_host"] == "0.0.0.0"

    def test_no_http_flag_wired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--no-http → no_http=True."""
        _patch_telemetry(monkeypatch)
        captured = _patch_async_run_capture(monkeypatch)
        CliRunner().invoke(_serve.serve, ["--no-http"])
        assert captured["no_http"] is True

    def test_keep_alive_flag_wired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--keep-alive → keep_alive=True."""
        _patch_telemetry(monkeypatch)
        captured = _patch_async_run_capture(monkeypatch)
        CliRunner().invoke(_serve.serve, ["--keep-alive"])
        assert captured["keep_alive"] is True

    def test_idle_grace_option_wired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--idle-grace=N → idle_grace=float(N)."""
        _patch_telemetry(monkeypatch)
        captured = _patch_async_run_capture(monkeypatch)
        CliRunner().invoke(_serve.serve, ["--idle-grace", "120.5"])
        assert captured["idle_grace"] == 120.5

    def test_no_singleton_flag_wired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--no-singleton → no_singleton=True."""
        _patch_telemetry(monkeypatch)
        captured = _patch_async_run_capture(monkeypatch)
        CliRunner().invoke(_serve.serve, ["--no-singleton"])
        assert captured["no_singleton"] is True

    def test_daemon_mode_flag_wired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--daemon-mode → daemon_mode=True (hidden flag, internal use)."""
        _patch_telemetry(monkeypatch)
        captured = _patch_async_run_capture(monkeypatch)
        CliRunner().invoke(_serve.serve, ["--daemon-mode"])
        assert captured["daemon_mode"] is True

    def test_log_level_choice_validation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--log-level rejects non-canonical values via click.Choice."""
        _patch_telemetry(monkeypatch)
        _patch_async_run_capture(monkeypatch)
        result = CliRunner().invoke(_serve.serve, ["--log-level", "BOGUS"])
        assert result.exit_code != 0
        assert "BOGUS" in result.output or "Invalid value" in result.output

    def test_log_level_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--log-level=debug accepted (Choice has case_sensitive=False)."""
        _patch_telemetry(monkeypatch)
        _patch_async_run_capture(monkeypatch)
        # Save and restore env to avoid leaking PROVIDE_LOG_LEVEL.
        prior = os.environ.pop("PROVIDE_LOG_LEVEL", None)
        try:
            result = CliRunner().invoke(_serve.serve, ["--log-level", "debug"])
            assert result.exit_code == 0
            # Also assert the env var was UPPER-cased.
            assert os.environ.get("PROVIDE_LOG_LEVEL") == "DEBUG"
        finally:
            os.environ.pop("PROVIDE_LOG_LEVEL", None)
            if prior is not None:
                os.environ["PROVIDE_LOG_LEVEL"] = prior


# ─── serve click command: env var export ordering ──────────────────────────


class TestServeEnvVarExports:
    def test_log_level_sets_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--log-level=DEBUG sets os.environ['PROVIDE_LOG_LEVEL']='DEBUG'."""
        _patch_telemetry(monkeypatch)
        _patch_async_run_capture(monkeypatch)
        monkeypatch.delenv("PROVIDE_LOG_LEVEL", raising=False)
        CliRunner().invoke(_serve.serve, ["--log-level", "DEBUG"])
        assert os.environ.get("PROVIDE_LOG_LEVEL") == "DEBUG"

    def test_log_level_uppercases(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """log_level is .upper()-ed before export."""
        _patch_telemetry(monkeypatch)
        _patch_async_run_capture(monkeypatch)
        monkeypatch.delenv("PROVIDE_LOG_LEVEL", raising=False)
        CliRunner().invoke(_serve.serve, ["--log-level", "warning"])
        assert os.environ.get("PROVIDE_LOG_LEVEL") == "WARNING"

    def test_no_log_level_leaves_env_alone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No --log-level → PROVIDE_LOG_LEVEL not set by serve."""
        _patch_telemetry(monkeypatch)
        _patch_async_run_capture(monkeypatch)
        monkeypatch.delenv("PROVIDE_LOG_LEVEL", raising=False)
        CliRunner().invoke(_serve.serve, [])
        assert "PROVIDE_LOG_LEVEL" not in os.environ

    def test_profile_sets_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--profile=core sets os.environ['OCTOWRIGHT_PROFILE']='core'."""
        _patch_telemetry(monkeypatch)
        _patch_async_run_capture(monkeypatch)
        monkeypatch.delenv("OCTOWRIGHT_PROFILE", raising=False)
        CliRunner().invoke(_serve.serve, ["--profile", "core"])
        assert os.environ.get("OCTOWRIGHT_PROFILE") == "core"

    def test_profile_preserves_case(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OCTOWRIGHT_PROFILE is NOT uppercased — preserved verbatim."""
        _patch_telemetry(monkeypatch)
        _patch_async_run_capture(monkeypatch)
        monkeypatch.delenv("OCTOWRIGHT_PROFILE", raising=False)
        CliRunner().invoke(_serve.serve, ["--profile", "core,advanced"])
        assert os.environ.get("OCTOWRIGHT_PROFILE") == "core,advanced"

    def test_no_profile_leaves_env_alone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No --profile → OCTOWRIGHT_PROFILE not set by serve."""
        _patch_telemetry(monkeypatch)
        _patch_async_run_capture(monkeypatch)
        monkeypatch.delenv("OCTOWRIGHT_PROFILE", raising=False)
        CliRunner().invoke(_serve.serve, [])
        assert "OCTOWRIGHT_PROFILE" not in os.environ


# ─── serve click command: telemetry lifecycle ───────────────────────────────


class TestServeTelemetryLifecycle:
    def test_setup_called_before_serve_async(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """setup_telemetry runs before _serve_async is awaited."""
        order: list[str] = []
        monkeypatch.setattr(_serve, "setup_telemetry", lambda: order.append("setup"))
        monkeypatch.setattr(_serve, "shutdown_telemetry", lambda: order.append("shutdown"))

        async def fake_serve_async(**_kwargs: Any) -> None:
            order.append("serve_async")

        monkeypatch.setattr(_serve, "_serve_async", fake_serve_async)
        result = CliRunner().invoke(_serve.serve, [])
        assert result.exit_code == 0
        assert order == ["setup", "serve_async", "shutdown"]

    def test_shutdown_called_even_on_serve_async_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The try/finally ensures shutdown_telemetry runs even if _serve_async raises."""
        order: list[str] = []
        monkeypatch.setattr(_serve, "setup_telemetry", lambda: order.append("setup"))
        monkeypatch.setattr(_serve, "shutdown_telemetry", lambda: order.append("shutdown"))

        async def fake_serve_async(**_kwargs: Any) -> None:
            order.append("serve_async")
            raise RuntimeError("simulated")

        monkeypatch.setattr(_serve, "_serve_async", fake_serve_async)
        result = CliRunner().invoke(_serve.serve, [])
        # CliRunner converts the exception to a non-zero exit; that's fine.
        assert result.exit_code != 0
        assert "shutdown" in order


# ─── _serve_async: dispatch decisions ──────────────────────────────────────


class TestServeAsyncDispatch:
    @pytest.mark.anyio
    async def test_daemon_mode_routes_to_run_leader_with_arm_immediate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """daemon_mode=True → _run_leader called with arm_watchdog_immediately=True."""
        captured: list[dict[str, Any]] = []

        async def fake_run_leader(**kwargs: Any) -> None:
            captured.append(kwargs)

        monkeypatch.setattr(_serve, "_run_leader", fake_run_leader)
        await _serve._serve_async(
            http_host=None,
            http_port=None,
            no_http=False,
            keep_alive=False,
            idle_grace=None,
            no_singleton=False,
            daemon_mode=True,
        )
        assert len(captured) == 1
        assert captured[0]["arm_watchdog_immediately"] is True
        assert captured[0]["no_singleton"] is False

    @pytest.mark.anyio
    async def test_no_singleton_routes_to_run_leader(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """no_singleton=True → _run_leader called with no_singleton=True (no arm flag)."""
        captured: list[dict[str, Any]] = []

        async def fake_run_leader(**kwargs: Any) -> None:
            captured.append(kwargs)

        monkeypatch.setattr(_serve, "_run_leader", fake_run_leader)
        await _serve._serve_async(
            http_host=None,
            http_port=None,
            no_http=False,
            keep_alive=False,
            idle_grace=None,
            no_singleton=True,
            daemon_mode=False,
        )
        assert len(captured) == 1
        assert captured[0]["no_singleton"] is True
        assert "arm_watchdog_immediately" not in captured[0]

    @pytest.mark.anyio
    async def test_default_routes_to_serve_singleton(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No daemon_mode + no_singleton → _serve_singleton called instead."""
        captured: list[dict[str, Any]] = []

        async def fake_serve_singleton(leader_kwargs: dict[str, Any], **kwargs: Any) -> None:
            captured.append({"leader_kwargs": leader_kwargs, **kwargs})

        async def fake_run_leader(**_kwargs: Any) -> None:
            raise AssertionError("should not run leader directly")

        monkeypatch.setattr(_serve, "_serve_singleton", fake_serve_singleton)
        monkeypatch.setattr(_serve, "_run_leader", fake_run_leader)
        await _serve._serve_async(
            http_host="127.0.0.1",
            http_port=8765,
            no_http=False,
            keep_alive=False,
            idle_grace=300.0,
            no_singleton=False,
            daemon_mode=False,
        )
        assert len(captured) == 1
        assert captured[0]["http_host"] == "127.0.0.1"
        assert captured[0]["http_port"] == 8765
        assert captured[0]["idle_grace"] == 300.0
        # leader_kwargs include the relevant fields.
        leader_kwargs = captured[0]["leader_kwargs"]
        assert leader_kwargs["http_host"] == "127.0.0.1"
        assert leader_kwargs["no_http"] is False
        assert leader_kwargs["keep_alive"] is False

    @pytest.mark.anyio
    async def test_daemon_mode_takes_precedence_over_no_singleton(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """daemon_mode=True wins even when no_singleton is also True."""
        captured: list[dict[str, Any]] = []

        async def fake_run_leader(**kwargs: Any) -> None:
            captured.append(kwargs)

        monkeypatch.setattr(_serve, "_run_leader", fake_run_leader)
        await _serve._serve_async(
            http_host=None,
            http_port=None,
            no_http=False,
            keep_alive=False,
            idle_grace=None,
            no_singleton=True,
            daemon_mode=True,
        )
        # arm_watchdog_immediately path taken (daemon mode wins).
        assert captured[0]["arm_watchdog_immediately"] is True
        assert captured[0]["no_singleton"] is False


# ─── _run_follower: URL derivation + run_proxy invocation ───────────────────


class TestRunFollower:
    @pytest.mark.anyio
    async def test_strips_mcp_and_appends_health(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`http://host:port/mcp/` → health URL `http://host:port/api/health`."""
        captured: dict[str, Any] = {}

        async def fake_run_proxy(url: str, *, health_url: str) -> None:
            captured["url"] = url
            captured["health_url"] = health_url

        from octowright import proxy_bridge as _pb

        monkeypatch.setattr(_pb, "run_proxy", fake_run_proxy)
        await _serve._run_follower("http://127.0.0.1:8765/mcp/")
        assert captured["url"] == "http://127.0.0.1:8765/mcp/"
        assert captured["health_url"] == "http://127.0.0.1:8765/api/health"

    @pytest.mark.anyio
    async def test_handles_url_without_trailing_slash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`http://host:port/mcp` (no trailing slash) → health correctly built."""
        captured: dict[str, Any] = {}

        async def fake_run_proxy(url: str, *, health_url: str) -> None:
            captured["health_url"] = health_url

        from octowright import proxy_bridge as _pb

        monkeypatch.setattr(_pb, "run_proxy", fake_run_proxy)
        await _serve._run_follower("http://127.0.0.1:8765/mcp")
        # rsplit("/mcp", 1) splits on the last /mcp, then "" + "/api/health".
        assert captured["health_url"] == "http://127.0.0.1:8765/api/health"

    @pytest.mark.anyio
    async def test_logs_connection_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Echoes 'connecting to leader at <url>' on stderr before bridging."""
        captured_messages: list[str] = []

        from octowright import proxy_bridge as _pb

        async def fake_run_proxy(url: str, *, health_url: str) -> None:
            return None

        monkeypatch.setattr(_pb, "run_proxy", fake_run_proxy)
        monkeypatch.setattr(
            _serve.click,
            "echo",
            lambda text, err=False: captured_messages.append(text),
        )
        await _serve._run_follower("http://127.0.0.1:8765/mcp/")
        assert any("connecting to leader at" in msg for msg in captured_messages)
        assert any("http://127.0.0.1:8765/mcp/" in msg for msg in captured_messages)

    @pytest.mark.anyio
    async def test_run_proxy_exception_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """run_proxy exception is NOT swallowed by _run_follower."""

        async def boom(url: str, *, health_url: str) -> None:
            raise RuntimeError("bridge failed")

        from octowright import proxy_bridge as _pb

        monkeypatch.setattr(_pb, "run_proxy", boom)
        monkeypatch.setattr(_serve.click, "echo", lambda *_a, **_kw: None)
        with pytest.raises(RuntimeError, match="bridge failed"):
            await _serve._run_follower("http://x/mcp/")

    @pytest.mark.anyio
    async def test_health_url_for_unusual_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify URL-derivation works when /mcp is in a sub-path."""
        captured: dict[str, Any] = {}

        async def fake_run_proxy(url: str, *, health_url: str) -> None:
            captured["health_url"] = health_url

        from octowright import proxy_bridge as _pb

        monkeypatch.setattr(_pb, "run_proxy", fake_run_proxy)
        # rsplit('/mcp', 1) splits on the LAST occurrence, so any prefix is preserved.
        await _serve._run_follower("http://127.0.0.1:8765/proxy/mcp/")
        assert captured["health_url"] == "http://127.0.0.1:8765/proxy/api/health"


# ─── _serve_singleton: composition test ────────────────────────────────────


class TestServeSingleton:
    @pytest.mark.anyio
    async def test_inline_fallback_short_circuits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When _ensure_leader_or_inline returns None (inline fallback already
        ran), _serve_singleton returns immediately without bridge or respawn."""
        bridge_calls: list[Any] = []
        respawn_calls: list[Any] = []

        async def fake_ensure(*_args: Any, **_kwargs: Any) -> Any:
            return None  # signals "inline fallback already happened"

        async def fake_bridge(_info: Any) -> None:
            bridge_calls.append(_info)

        async def fake_respawn(*_args: Any, **_kwargs: Any) -> None:
            respawn_calls.append(_args)

        monkeypatch.setattr(_serve, "_ensure_leader_or_inline", fake_ensure)
        monkeypatch.setattr(_serve, "_bridge_to_leader", fake_bridge)
        monkeypatch.setattr(_serve, "_respawn_if_leader_gone", fake_respawn)

        await _serve._serve_singleton({}, http_host=None, http_port=None, idle_grace=None)
        assert bridge_calls == []
        assert respawn_calls == []

    @pytest.mark.anyio
    async def test_happy_path_calls_ensure_then_bridge_then_respawn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sequence: ensure → bridge → respawn-check."""
        order: list[str] = []
        leader_info = MagicMock()

        async def fake_ensure(*_args: Any, **_kwargs: Any) -> Any:
            order.append("ensure")
            return leader_info

        async def fake_bridge(info: Any) -> None:
            assert info is leader_info
            order.append("bridge")

        async def fake_respawn(*_args: Any, **_kwargs: Any) -> None:
            order.append("respawn")

        monkeypatch.setattr(_serve, "_ensure_leader_or_inline", fake_ensure)
        monkeypatch.setattr(_serve, "_bridge_to_leader", fake_bridge)
        monkeypatch.setattr(_serve, "_respawn_if_leader_gone", fake_respawn)

        await _serve._serve_singleton({"http_host": None}, http_host=None, http_port=None, idle_grace=None)
        assert order == ["ensure", "bridge", "respawn"]

    @pytest.mark.anyio
    async def test_passes_kwargs_to_ensure_and_respawn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """http_host/port/idle_grace flow through to both ensure and respawn."""
        ensure_kwargs: dict[str, Any] = {}
        respawn_kwargs: dict[str, Any] = {}

        async def fake_ensure(_leader_kwargs: Any, **kwargs: Any) -> Any:
            ensure_kwargs.update(kwargs)
            return MagicMock()

        async def fake_bridge(_info: Any) -> None:
            return None

        async def fake_respawn(**kwargs: Any) -> None:
            respawn_kwargs.update(kwargs)

        monkeypatch.setattr(_serve, "_ensure_leader_or_inline", fake_ensure)
        monkeypatch.setattr(_serve, "_bridge_to_leader", fake_bridge)
        monkeypatch.setattr(_serve, "_respawn_if_leader_gone", fake_respawn)
        await _serve._serve_singleton({}, http_host="0.0.0.0", http_port=9000, idle_grace=300.0)
        assert ensure_kwargs["http_host"] == "0.0.0.0"
        assert ensure_kwargs["http_port"] == 9000
        assert ensure_kwargs["idle_grace"] == 300.0
        assert respawn_kwargs["http_host"] == "0.0.0.0"
        assert respawn_kwargs["http_port"] == 9000
        assert respawn_kwargs["idle_grace"] == 300.0


# ─── _run_leader: skip-sidecar / skip-lock branches ────────────────────────


class _LeaderStubs:
    """Holds stubbed long-running tasks for _run_leader tests."""

    def __init__(self) -> None:
        self.stdio_done = asyncio.Event()
        self.http_done = asyncio.Event()
        self.watchdog_done = asyncio.Event()
        self.http_called = False
        self.watchdog_called = False
        self.lock_writes: list[Any] = []
        self.lock_removes: list[Any] = []


@pytest.fixture
def leader_stubs(monkeypatch: pytest.MonkeyPatch) -> _LeaderStubs:
    s = _LeaderStubs()

    async def fake_stdio() -> None:
        await s.stdio_done.wait()

    async def fake_http(**kwargs: Any) -> None:
        s.http_called = True
        on_bound = kwargs.get("on_bound")
        if on_bound is not None:
            on_bound("127.0.0.1", 18999)
        await s.http_done.wait()

    async def fake_watchdog(*_args: Any, **_kwargs: Any) -> None:
        s.watchdog_called = True
        await s.watchdog_done.wait()

    from octowright.server import _state as _server_state

    monkeypatch.setattr(_server_state.mcp, "run_stdio_async", fake_stdio)

    from octowright import http as _http_pkg

    monkeypatch.setattr(_http_pkg, "serve_app", fake_http)

    from octowright import idle_watchdog as _watchdog_mod

    monkeypatch.setattr(_watchdog_mod, "idle_watchdog", fake_watchdog)

    # Capture lock writes/removes without touching real lockfile.
    from octowright import singleton as _sn

    monkeypatch.setattr(_sn, "write_lock", lambda info, **_kw: s.lock_writes.append(info))
    monkeypatch.setattr(_sn, "remove_lock", lambda **_kw: s.lock_removes.append(True))

    return s


def _leader_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "http_host": None,
        "http_port": None,
        "no_http": False,
        "keep_alive": False,
        "idle_grace": None,
        "no_singleton": False,
    }
    base.update(overrides)
    return base


class TestRunLeaderBranches:
    @pytest.mark.anyio
    async def test_no_http_skips_sidecar_spawn(self, leader_stubs: _LeaderStubs) -> None:
        """no_http=True → _http.serve_app is NEVER called."""
        leader_task = asyncio.create_task(_serve._run_leader(**_leader_kwargs(no_http=True)))
        # Give the leader a tick to set up tasks.
        await asyncio.sleep(0.05)
        # End stdio + watchdog so the leader exits cleanly.
        leader_stubs.stdio_done.set()
        leader_stubs.watchdog_done.set()
        await asyncio.wait_for(leader_task, timeout=2.0)
        assert leader_stubs.http_called is False

    @pytest.mark.anyio
    async def test_keep_alive_skips_watchdog_spawn(self, leader_stubs: _LeaderStubs) -> None:
        """keep_alive=True → idle_watchdog NEVER called; mcp_task is the only wait_for."""
        leader_task = asyncio.create_task(_serve._run_leader(**_leader_kwargs(no_http=True, keep_alive=True)))
        await asyncio.sleep(0.05)
        # End stdio so the leader exits.
        leader_stubs.stdio_done.set()
        await asyncio.wait_for(leader_task, timeout=2.0)
        assert leader_stubs.watchdog_called is False

    @pytest.mark.anyio
    async def test_no_singleton_skips_lock_write_and_remove(self, leader_stubs: _LeaderStubs) -> None:
        """no_singleton=True → on_bound returns early, no write_lock; finally skips remove_lock."""
        leader_task = asyncio.create_task(_serve._run_leader(**_leader_kwargs(no_singleton=True, keep_alive=True)))
        await asyncio.sleep(0.05)
        # End stdio so leader exits.
        leader_stubs.stdio_done.set()
        # Also end http (it's still active even with no_singleton).
        leader_stubs.http_done.set()
        await asyncio.wait_for(leader_task, timeout=2.0)
        assert leader_stubs.lock_writes == []
        assert leader_stubs.lock_removes == []

    @pytest.mark.anyio
    async def test_singleton_writes_lock_on_bind_and_removes_on_exit(self, leader_stubs: _LeaderStubs) -> None:
        """Default path: on_bound writes lock; finally removes it."""
        leader_task = asyncio.create_task(_serve._run_leader(**_leader_kwargs(keep_alive=True)))
        # Give serve_app a chance to call on_bound.
        await asyncio.sleep(0.05)
        # End stdio + http to wind down.
        leader_stubs.stdio_done.set()
        leader_stubs.http_done.set()
        await asyncio.wait_for(leader_task, timeout=2.0)
        assert len(leader_stubs.lock_writes) == 1
        assert len(leader_stubs.lock_removes) == 1
