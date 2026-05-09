# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.singleton (leader-election lockfile).

Currently 0% covered. Pins:
- LeaderInfo round-trip + missing-field handling
- read_lock: file-missing/corrupt-JSON/wrong-shape → None vs valid → LeaderInfo
- write_lock: tmp + replace atomicity, parent dir created
- remove_lock: idempotent on missing file
- pid_is_alive: 0/negative pids, ProcessLookupError, PermissionError ("alive but not ours"), success
- is_stale: pid dead → True; alive → False
- probe_http_alive: 200/non-200/HTTPError/OSError handling
- make_leader_info: every field including the trailing-slash mcp_url
- LOCK_PATH default location + env var override
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.singleton import (
    LeaderInfo,
    is_stale,
    make_leader_info,
    pid_is_alive,
    probe_http_alive,
    read_lock,
    remove_lock,
    write_lock,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _info(**overrides: Any) -> LeaderInfo:
    base = {
        "pid": os.getpid(),
        "http_host": "127.0.0.1",
        "http_port": 8765,
        "mcp_url": "http://127.0.0.1:8765/mcp/",
        "started_at": time.time(),
    }
    base.update(overrides)
    return LeaderInfo(**base)  # type: ignore[arg-type]


# ─── LeaderInfo (de)serialisation ────────────────────────────────────────────


class TestLeaderInfo:
    def test_to_json_round_trip(self) -> None:
        """to_json → from_json preserves all five fields."""
        info = _info(pid=4242, http_host="0.0.0.0", http_port=9999, started_at=1000.0)
        roundtripped = LeaderInfo.from_json(info.to_json())
        assert roundtripped == info

    def test_to_json_is_indented_for_human_inspection(self) -> None:
        """indent=2 is part of the format — easier to spot in `cat ~/.config/...`."""
        info = _info()
        text = info.to_json()
        assert "\n  " in text  # has indentation

    def test_to_json_is_sorted_for_diff_stability(self) -> None:
        """sort_keys=True so the file diff is stable."""
        info = _info()
        text = info.to_json()
        # `http_host` lexically precedes `pid` — assert that ordering.
        assert text.index('"http_host"') < text.index('"pid"')

    def test_from_json_rejects_missing_field(self) -> None:
        """Missing key → TypeError when constructing dataclass."""
        bad = json.dumps({"pid": 1, "http_host": "x", "http_port": 1, "mcp_url": "y"})
        with pytest.raises(TypeError):
            LeaderInfo.from_json(bad)

    def test_from_json_rejects_extra_field(self) -> None:
        """Unknown key → TypeError."""
        bad = json.dumps({**_info().__dict__, "extra": True})
        with pytest.raises(TypeError):
            LeaderInfo.from_json(bad)


# ─── read_lock ───────────────────────────────────────────────────────────────


class TestReadLock:
    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        """No lockfile → None (the most common case before first launch)."""
        assert read_lock(tmp_path / "nope.lock") is None

    def test_well_formed_returns_leader_info(self, tmp_path: Path) -> None:
        """Valid JSON → LeaderInfo."""
        info = _info()
        p = tmp_path / "lock"
        p.write_text(info.to_json())
        out = read_lock(p)
        assert out == info

    def test_malformed_json_returns_none(self, tmp_path: Path) -> None:
        """JSONDecodeError swallowed, returns None — caller will overwrite."""
        p = tmp_path / "lock"
        p.write_text("{ not json")
        assert read_lock(p) is None

    def test_missing_field_returns_none(self, tmp_path: Path) -> None:
        """TypeError from missing dataclass field swallowed → None."""
        p = tmp_path / "lock"
        p.write_text(json.dumps({"pid": 1}))
        assert read_lock(p) is None

    def test_default_arg_uses_module_level_lock_path(self) -> None:
        """The function signature's default IS the LOCK_PATH module constant.

        We can't easily monkeypatch this because the default is bound at
        definition time, but we can assert the binding is the same object.
        """
        from octowright import singleton as _sg

        sig_default = _sg.read_lock.__defaults__[0]  # type: ignore[index]
        assert sig_default == _sg.LOCK_PATH


# ─── write_lock ──────────────────────────────────────────────────────────────


class TestWriteLock:
    def test_writes_atomically_via_tmp(self, tmp_path: Path) -> None:
        """Tmp file is renamed; no leftover *.tmp on disk."""
        p = tmp_path / "lock"
        write_lock(_info(), p)
        assert p.exists()
        # No stray .tmp files in parent dir.
        assert not list(tmp_path.glob("*.tmp"))

    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        """Parents created — supports first-run scenario where config dir is missing."""
        p = tmp_path / "newdir" / "subdir" / "lock"
        write_lock(_info(), p)
        assert p.exists()

    def test_overwrites_existing_lock(self, tmp_path: Path) -> None:
        """Pre-existing lock is replaced by the new info atomically."""
        p = tmp_path / "lock"
        write_lock(_info(pid=111), p)
        write_lock(_info(pid=222), p)
        assert read_lock(p).pid == 222  # type: ignore[union-attr]

    def test_round_trip_with_read_lock(self, tmp_path: Path) -> None:
        """write_lock → read_lock returns the same LeaderInfo."""
        p = tmp_path / "lock"
        info = _info(pid=4242, http_port=9000)
        write_lock(info, p)
        assert read_lock(p) == info


# ─── remove_lock ─────────────────────────────────────────────────────────────


class TestRemoveLock:
    def test_removes_existing(self, tmp_path: Path) -> None:
        """Calling on an existing file unlinks it."""
        p = tmp_path / "lock"
        p.write_text(_info().to_json())
        remove_lock(p)
        assert not p.exists()

    def test_missing_is_silent(self, tmp_path: Path) -> None:
        """FileNotFoundError swallowed — no crash if the lock was already cleared."""
        # Must not raise.
        remove_lock(tmp_path / "nope.lock")

    def test_idempotent(self, tmp_path: Path) -> None:
        """Two consecutive calls don't error."""
        p = tmp_path / "lock"
        p.write_text(_info().to_json())
        remove_lock(p)
        remove_lock(p)


# ─── pid_is_alive ────────────────────────────────────────────────────────────


class TestPidIsAlive:
    def test_zero_or_negative_returns_false(self) -> None:
        """pid<=0 short-circuits to False before any os.kill probe."""
        assert pid_is_alive(0) is False
        assert pid_is_alive(-1) is False

    def test_current_pid_is_alive(self) -> None:
        """Current process is trivially alive."""
        assert pid_is_alive(os.getpid()) is True

    def test_dead_pid_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ProcessLookupError → False."""
        monkeypatch.setattr(os, "kill", MagicMock(side_effect=ProcessLookupError))
        if os.name == "nt":
            pytest.skip("Windows uses _pid_is_alive_windows; os.kill mock doesn't apply")
        assert pid_is_alive(99999) is False

    def test_permission_denied_returns_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """PermissionError means the process exists but isn't ours — counts as alive."""
        if os.name == "nt":
            pytest.skip("Windows path uses ctypes")
        monkeypatch.setattr(os, "kill", MagicMock(side_effect=PermissionError))
        assert pid_is_alive(99999) is True


# ─── is_stale ────────────────────────────────────────────────────────────────


class TestIsStale:
    def test_alive_pid_not_stale(self) -> None:
        """Current PID is alive → not stale."""
        assert is_stale(_info(pid=os.getpid())) is False

    def test_dead_pid_is_stale(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """pid_is_alive returns False → is_stale returns True."""
        from octowright import singleton as _sg

        monkeypatch.setattr(_sg, "pid_is_alive", lambda _pid: False)
        assert _sg.is_stale(_info(pid=99999)) is True


# ─── probe_http_alive ────────────────────────────────────────────────────────


class TestProbeHttpAlive:
    @pytest.mark.anyio
    async def test_200_returns_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP 200 from /api/health → True."""
        import httpx

        async def fake_get(self: Any, url: str) -> Any:
            response = MagicMock()
            response.status_code = 200
            return response

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = fake_get.__get__(client)
        monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: client)
        assert await probe_http_alive(_info()) is True

    @pytest.mark.anyio
    async def test_non_200_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """500 from /api/health → False (the leader is sick)."""
        import httpx

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        response = MagicMock()
        response.status_code = 500
        client.get = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: client)
        assert await probe_http_alive(_info()) is False

    @pytest.mark.anyio
    async def test_http_error_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """httpx.HTTPError swallowed → False."""
        import httpx

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(side_effect=httpx.ConnectError("nope"))
        monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: client)
        assert await probe_http_alive(_info()) is False

    @pytest.mark.anyio
    async def test_os_error_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OSError (e.g. socket-not-connected) swallowed → False."""
        import httpx

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(side_effect=OSError("no socket"))
        monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: client)
        assert await probe_http_alive(_info()) is False

    @pytest.mark.anyio
    async def test_url_uses_info_host_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The probed URL uses info.http_host + http_port + /api/health."""
        import httpx

        captured: list[str] = []

        async def fake_get(self: Any, url: str) -> Any:
            captured.append(url)
            response = MagicMock()
            response.status_code = 200
            return response

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = fake_get.__get__(client)
        monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: client)
        await probe_http_alive(_info(http_host="10.0.0.1", http_port=12345))
        assert captured == ["http://10.0.0.1:12345/api/health"]


# ─── make_leader_info ───────────────────────────────────────────────────────


class TestMakeLeaderInfo:
    def test_pid_is_current(self) -> None:
        """pid field is os.getpid() at construction time."""
        info = make_leader_info("127.0.0.1", 8765)
        assert info.pid == os.getpid()

    def test_host_and_port_round_trip(self) -> None:
        """http_host / http_port preserved verbatim."""
        info = make_leader_info("0.0.0.0", 9000)
        assert info.http_host == "0.0.0.0"
        assert info.http_port == 9000

    def test_mcp_url_has_trailing_slash(self) -> None:
        """Trailing slash on /mcp/ is load-bearing for Starlette Mount routing."""
        info = make_leader_info("127.0.0.1", 8765)
        assert info.mcp_url == "http://127.0.0.1:8765/mcp/"
        assert info.mcp_url.endswith("/mcp/")

    def test_started_at_is_now(self) -> None:
        """started_at within ±2s of time.time()."""
        before = time.time()
        info = make_leader_info("127.0.0.1", 8765)
        after = time.time()
        assert before - 0.5 <= info.started_at <= after + 0.5


# ─── LOCK_PATH default ─────────────────────────────────────────────────────


class TestLockPathDefault:
    def test_default_under_user_config(self) -> None:
        """LOCK_PATH lives under the user config dir by default."""
        from octowright import singleton as _sg
        from octowright.config_paths import user_config_dir

        # The runtime constant was initialised from env or default at import.
        # We don't override OCTOWRIGHT_LOCK_PATH in this test, so it should be
        # under user_config_dir() OR the env value if a prior test set one.
        env_override = os.environ.get("OCTOWRIGHT_LOCK_PATH")
        if env_override:
            assert str(_sg.LOCK_PATH) == env_override
        else:
            assert _sg.LOCK_PATH.parent == user_config_dir()
            assert _sg.LOCK_PATH.name == "octowright.lock"
