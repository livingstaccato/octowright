# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from octowright.browser_pool import BrowserPool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool() -> BrowserPool:
    return BrowserPool()


def _launch_result(label: str | None = None, kind: str = "webkit") -> dict[str, Any]:
    return {
        "instance_id": f"fake-{label or kind}",
        "kind": kind,
        "label": label,
        "profile": None,
        "url": "https://octowright.com",
        "log_path": "/tmp/fake.jsonl",
        "record_video": False,
    }


# ---------------------------------------------------------------------------
# spawn_roster — all succeed
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_spawn_roster_launches_all_specs(monkeypatch: pytest.MonkeyPatch) -> None:
    """All three specs succeed — launched has 3 entries, errors is empty."""
    pool = _make_pool()
    call_kwargs: list[dict[str, Any]] = []

    async def _fake_launch(**kwargs: Any) -> dict[str, Any]:
        call_kwargs.append(kwargs)
        return _launch_result(label=kwargs.get("label"), kind=kwargs.get("kind", "chromium"))

    monkeypatch.setattr(pool, "launch", _fake_launch)

    specs = [
        {"kind": "webkit", "url": "https://octowright.com", "headed": False, "label": "a"},
        {"kind": "chromium", "url": "https://octowright.com", "headed": False, "label": "b"},
        {"kind": "firefox", "url": "https://octowright.com", "headed": False, "label": "c"},
    ]

    result = await pool.spawn_roster(specs)

    assert len(result["launched"]) == 3
    assert result["errors"] == []

    # All three label values appear in the calls.
    labels = {kw["label"] for kw in call_kwargs}
    assert labels == {"a", "b", "c"}

    # Kinds are forwarded correctly.
    kinds = {kw["kind"] for kw in call_kwargs}
    assert kinds == {"webkit", "chromium", "firefox"}


# ---------------------------------------------------------------------------
# spawn_roster — one spec fails
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_spawn_roster_partial_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """One of three specs raises; the other two succeed; errors entry includes the spec."""
    pool = _make_pool()

    async def _fake_launch(**kwargs: Any) -> dict[str, Any]:
        if kwargs.get("label") == "bad":
            raise RuntimeError("simulated browser launch failure")
        return _launch_result(label=kwargs.get("label"))

    monkeypatch.setattr(pool, "launch", _fake_launch)

    specs = [
        {"kind": "webkit", "url": "https://octowright.com", "headed": False, "label": "ok1"},
        {"kind": "webkit", "url": "https://octowright.com", "headed": False, "label": "bad"},
        {"kind": "webkit", "url": "https://octowright.com", "headed": False, "label": "ok2"},
    ]

    result = await pool.spawn_roster(specs)

    assert len(result["launched"]) == 2
    assert len(result["errors"]) == 1

    error_entry = result["errors"][0]
    assert error_entry["spec"]["label"] == "bad"
    assert "simulated browser launch failure" in error_entry["error"]

    # The two successful entries are present.
    launched_labels = {r["label"] for r in result["launched"]}
    assert launched_labels == {"ok1", "ok2"}


# ---------------------------------------------------------------------------
# spawn_roster — empty spec list
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_spawn_roster_cancelled_child_closes_launched_siblings(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cancelled child launch must not leak the siblings that did launch.

    The successfully-launched browsers must be fully closed *before* the
    CancelledError re-propagates — a detached create_task close would leave the
    recorded closes empty at the point the exception surfaces.
    """
    import asyncio

    pool = _make_pool()
    closed: list[str] = []

    async def _fake_launch(**kwargs: Any) -> dict[str, Any]:
        if kwargs.get("label") == "cancelme":
            raise asyncio.CancelledError
        return _launch_result(label=kwargs.get("label"))

    async def _fake_close(instance_id: str, **_kwargs: Any) -> None:
        closed.append(instance_id)

    monkeypatch.setattr(pool, "launch", _fake_launch)
    monkeypatch.setattr(pool, "close", _fake_close)

    specs = [
        {"kind": "webkit", "label": "alpha"},
        {"kind": "webkit", "label": "cancelme"},
        {"kind": "webkit", "label": "gamma"},
    ]

    with pytest.raises(asyncio.CancelledError):
        await pool.spawn_roster(specs)

    assert sorted(closed) == ["fake-alpha", "fake-gamma"]


@pytest.mark.anyio
async def test_spawn_roster_empty_specs(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _make_pool()
    monkeypatch.setattr(pool, "launch", AsyncMock(return_value=_launch_result()))

    result = await pool.spawn_roster([])

    assert result["launched"] == []
    assert result["errors"] == []


# ---------------------------------------------------------------------------
# spawn_roster — default kwarg forwarding
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_spawn_roster_defaults_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    """A spec with no optional keys should still call launch with safe defaults."""
    pool = _make_pool()
    call_kwargs: list[dict[str, Any]] = []

    async def _fake_launch(**kwargs: Any) -> dict[str, Any]:
        call_kwargs.append(kwargs)
        return _launch_result()

    monkeypatch.setattr(pool, "launch", _fake_launch)

    await pool.spawn_roster([{}])

    kw = call_kwargs[0]
    assert kw["kind"] == "chromium"
    assert kw["headed"] is None
    assert kw["stabilize"] is False
    assert kw["record_video"] is False
    assert kw["label"] is None
    assert kw["url"] is None
