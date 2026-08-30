# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Per-engine launch health (``BrowserPool.engine_health`` / ``_record_engine_health``).

Diagnosing a real incident spent about an hour of a 12.6-hour wedge just
establishing "WebKit is broken on this machine, Chromium is fine" -- the pool
already saw every launch and every failure per engine kind, it just never said
so. These tests cover: a successful launch records ``ok`` with a timestamp; a
failed launch records the exception CLASS NAME and never its message; each
kind is tracked independently; a kind never launched is absent (not falsely
reported healthy); and the block is wired into
``octowright_status()["pool"]["engine_health"]``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from octowright.browser_pool.pool import BrowserPool


def test_engine_health_empty_when_nothing_launched() -> None:
    pool = BrowserPool()
    assert pool.engine_health() == {}


async def test_successful_launch_records_ok_with_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = BrowserPool()

    async def _impl(_options: dict[str, Any], _sp: object) -> dict[str, Any]:
        return {"instance_id": "healthy-chromium"}

    monkeypatch.setattr(pool, "_launch_impl", _impl)

    out = await pool.launch(kind="chromium")

    assert out == {"instance_id": "healthy-chromium"}
    health = pool.engine_health()
    assert set(health.keys()) == {"chromium"}
    entry = health["chromium"]
    assert entry["outcome"] == "ok"
    assert "error" not in entry
    # "at" is a real, parseable ISO-8601 UTC timestamp, not a placeholder.
    parsed = datetime.fromisoformat(entry["at"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    assert (datetime.now(UTC) - parsed).total_seconds() < 30


async def test_failed_launch_records_error_class_only(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = BrowserPool()
    # Message deliberately carries the kind of sensitive detail a launch
    # failure can leak -- a filesystem path with a real username/profile name.
    secret_message = "Executable doesn't exist at /Users/tanuki-tim/.config/octowright/profiles/secret-persona"

    async def _impl(_options: dict[str, Any], _sp: object) -> dict[str, Any]:
        raise RuntimeError(secret_message)

    monkeypatch.setattr(pool, "_launch_impl", _impl)

    with pytest.raises(RuntimeError, match="secret-persona"):
        await pool.launch(kind="webkit")

    health = pool.engine_health()
    entry = health["webkit"]
    assert entry["outcome"] == "error"
    assert entry["error"] == "RuntimeError"
    # The message text must never appear anywhere in the recorded entry --
    # this is a hard requirement, not a style preference.
    rendered = repr(entry)
    assert "secret-persona" not in rendered
    assert "tanuki-tim" not in rendered
    assert secret_message not in rendered


async def test_kinds_tracked_independently(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact case this exists to show: chromium healthy, webkit failing."""
    pool = BrowserPool()

    async def _impl(options: dict[str, Any], _sp: object) -> dict[str, Any]:
        if options.get("kind") == "webkit":
            raise RuntimeError("WebKit is broken on this machine")
        return {"instance_id": f"{options.get('kind')}-ok"}

    monkeypatch.setattr(pool, "_launch_impl", _impl)

    await pool.launch(kind="chromium")
    with pytest.raises(RuntimeError):
        await pool.launch(kind="webkit")

    health = pool.engine_health()
    assert health["chromium"]["outcome"] == "ok"
    assert health["webkit"]["outcome"] == "error"
    assert health["webkit"]["error"] == "RuntimeError"
    # Neither entry's fields bled into the other.
    assert "error" not in health["chromium"]


async def test_kind_never_launched_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Absent means 'no data', not 'healthy' -- conflating them is what made
    the original diagnosis slow."""
    pool = BrowserPool()

    async def _impl(_options: dict[str, Any], _sp: object) -> dict[str, Any]:
        return {"instance_id": "chromium-only"}

    monkeypatch.setattr(pool, "_launch_impl", _impl)

    await pool.launch(kind="chromium")

    health = pool.engine_health()
    assert "chromium" in health
    assert "webkit" not in health
    assert "firefox" not in health


async def test_repeated_launches_keep_only_the_last_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = BrowserPool()
    calls = {"n": 0}

    async def _impl(_options: dict[str, Any], _sp: object) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first attempt failed")
        return {"instance_id": "recovered"}

    monkeypatch.setattr(pool, "_launch_impl", _impl)

    with pytest.raises(RuntimeError):
        await pool.launch(kind="chromium")
    assert pool.engine_health()["chromium"]["outcome"] == "error"

    await pool.launch(kind="chromium")
    assert pool.engine_health()["chromium"]["outcome"] == "ok"
    assert "error" not in pool.engine_health()["chromium"]


async def test_driver_death_retry_records_success_once_healed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A launch that survives a driver-death retry is recorded ``ok``.

    Scope, honestly: this pins the OUTCOME, not the mechanism. Recording
    per-attempt inside ``_launch_with_driver_retry`` instead of once in
    ``launch()`` also leaves this green, because the later write overwrites
    the transient ``error`` before any assertion reads it. The split is
    still the right design -- a concurrent ``octowright_status()`` landing
    between the transient failure and the retry's success would briefly
    report ``error`` under the per-attempt shape and never does under this
    one -- but that race is not what this test proves.
    """
    from unittest.mock import AsyncMock

    pool = BrowserPool()
    monkeypatch.setattr(pool, "_reset_driver", AsyncMock())
    calls = {"n": 0}

    async def _impl(_options: dict[str, Any], _sp: object) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("BrowserType.launch: Connection closed")
        return {"instance_id": "healed"}

    monkeypatch.setattr(pool, "_launch_impl", _impl)

    out = await pool.launch(kind="chromium")

    assert out == {"instance_id": "healed"}
    assert calls["n"] == 2
    entry = pool.engine_health()["chromium"]
    assert entry["outcome"] == "ok"
    assert "error" not in entry


def test_engine_health_returns_a_copy() -> None:
    """Mutating a returned snapshot must not corrupt the pool's own state."""
    pool = BrowserPool()
    pool._record_engine_health("chromium", None)

    snapshot = pool.engine_health()
    snapshot["chromium"]["outcome"] = "MUTATED"
    snapshot["firefox"] = {"outcome": "MUTATED", "at": "x"}

    fresh = pool.engine_health()
    assert fresh["chromium"]["outcome"] == "ok"
    assert "firefox" not in fresh


async def test_engine_health_surfaced_in_octowright_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end wiring: a real launch on the process-wide singleton pool is
    visible at octowright_status()["pool"]["engine_health"]."""
    from octowright.server.meta import octowright_status
    from octowright.server.meta import pool as status_pool

    # The singleton pool is shared across the whole test session -- snapshot
    # and restore its engine-health state so this test can't leak into others.
    original = dict(status_pool._engine_health)

    async def _impl(_options: dict[str, Any], _sp: object) -> dict[str, Any]:
        return {"instance_id": "status-wiring-firefox"}

    try:
        monkeypatch.setattr(status_pool, "_launch_impl", _impl)
        await status_pool.launch(kind="firefox")

        snap = octowright_status()

        assert "engine_health" in snap["pool"]
        assert snap["pool"]["engine_health"]["firefox"]["outcome"] == "ok"
        assert "at" in snap["pool"]["engine_health"]["firefox"]
    finally:
        status_pool._engine_health.clear()
        status_pool._engine_health.update(original)


async def test_an_unsupported_kind_is_clamped_rather_than_recorded_verbatim() -> None:
    """A caller-supplied ``kind`` must not become a permanent status key.

    ``kind`` reaches ``launch()`` straight from the caller and is validated
    only deeper, in ``LaunchOptions.validate()``, so a launch that fails
    validation still records health under whatever string was passed -- and
    ``browser_launch``'s signature is ``kind: str``, not a ``Literal``, so
    the MCP schema accepts anything. Unclamped, an LLM could fill a
    never-evicted dict, echoed verbatim into every ``octowright_status()``,
    with arbitrary strings.

    Every other case in this file monkeypatches ``_launch_impl`` and so never
    reaches validation, which is exactly why this went unnoticed until a
    whole-branch review drove it. This one deliberately does NOT patch, so
    the real validation path runs.
    """
    pool = BrowserPool()

    for bogus in ("../../etc/passwd", "chrome", "Chromium", "x" * 80):
        with pytest.raises(Exception):
            await pool.launch(kind=bogus)
        assert bogus not in pool.engine_health()

    # The attempts are still visible, collapsed under a bounded key.
    assert set(pool.engine_health()) <= {"chromium", "firefox", "webkit", "unknown"}
    assert pool.engine_health()["unknown"]["outcome"] == "error"
