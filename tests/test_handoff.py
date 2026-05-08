# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from octowright.browser_pool import BrowserPool


@pytest.mark.anyio
async def test_handoff_reuses_profile_and_closes_original(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = BrowserPool()
    source = SimpleNamespace(
        instance_id="old01",
        kind="webkit",
        profile="dante",
        label="lab",
        url="https://example.com/app",
        user_data_dir="/tmp/profile-dir",
        har_path=None,
        stabilize=False,
        page=SimpleNamespace(url="https://example.com/live"),
    )
    pool._sessions["old01"] = source

    close_calls: list[str] = []

    async def _fake_close(instance_id: str) -> dict[str, Any]:
        close_calls.append(instance_id)
        pool._sessions.pop(instance_id, None)
        return {"closed": True}

    async def _fake_launch(**kwargs: Any) -> dict[str, Any]:
        return {
            "instance_id": "new01",
            "kind": kwargs["kind"],
            "label": kwargs.get("label"),
            "profile": kwargs.get("profile"),
            "url": kwargs.get("url"),
            "log_path": "/tmp/new01.jsonl",
            "record_video": kwargs.get("record_video", False),
            "trace": kwargs.get("trace", False),
        }

    monkeypatch.setattr(pool, "close", _fake_close)
    monkeypatch.setattr(pool, "launch", _fake_launch)

    result = await pool.handoff("old01", headed=False)

    assert close_calls == ["old01"]
    assert result["old_instance_id"] == "old01"
    assert result["new_instance_id"] == "new01"
    assert result["old_closed"] is True
    assert result["profile"] == "dante"


@pytest.mark.anyio
async def test_handoff_rejects_stateless_without_opt_in() -> None:
    pool = BrowserPool()
    pool._sessions["old02"] = SimpleNamespace(
        instance_id="old02",
        kind="chromium",
        profile=None,
        label=None,
        url="https://example.com",
        user_data_dir=None,
        har_path=None,
        stabilize=False,
        page=SimpleNamespace(url="https://example.com"),
    )

    with pytest.raises(ValueError, match="accept_stateless=True"):
        await pool.handoff("old02", headed=True)


@pytest.mark.anyio
async def test_handoff_rejects_keep_original_for_persistent() -> None:
    pool = BrowserPool()
    pool._sessions["old03"] = SimpleNamespace(
        instance_id="old03",
        kind="firefox",
        profile="ops",
        label="ops",
        url="https://example.com",
        user_data_dir="/tmp/ops",
        har_path=None,
        stabilize=False,
        page=SimpleNamespace(url="https://example.com"),
    )

    with pytest.raises(ValueError, match="close_original=True"):
        await pool.handoff("old03", headed=False, close_original=False)


@pytest.mark.asyncio
async def test_handoff_preserves_session_scoped_tmpdir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pool = BrowserPool()
    source = SimpleNamespace(
        instance_id="old-session",
        kind="chromium",
        label="scratch",
        profile=None,
        user_data_dir=tmp_path / "session-dir",
        page=SimpleNamespace(url="https://example.com"),
        url="https://example.com",
        stabilize=False,
        trace=False,
        har_path=None,
    )
    pool._sessions["old-session"] = source  # type: ignore[assignment]
    launched: dict[str, object] = {}

    async def fake_close(instance_id: str) -> dict[str, object]:
        pool._sessions.pop(instance_id, None)
        return {"closed": True}

    async def fake_launch(**kwargs: object) -> dict[str, object]:
        launched.update(kwargs)
        return {
            "instance_id": "new-session",
            "kind": "chromium",
            "label": kwargs.get("label"),
            "profile": kwargs.get("profile"),
            "url": kwargs.get("url"),
            "log_path": "/tmp/new.jsonl",
            "record_video": False,
            "trace": False,
        }

    monkeypatch.setattr(pool, "close", fake_close)
    monkeypatch.setattr(pool, "launch", fake_launch)

    result = await BrowserPool.handoff(pool, "old-session", headed=False)

    assert result["new_instance_id"] == "new-session"
    assert launched["session"] is True
    assert launched["profile"] is None
