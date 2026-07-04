# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Headed browsers launch protected by default; headless do not.

Uses the fixture-free ``BrowserPool()`` pattern shared by the other
``*_live.py`` pool tests (see ``test_pool_crash_live.py``): no shared
``live_pool`` fixture exists in this repo, so each test builds its own pool,
monkeypatches ``RECORDINGS_DIR`` to a tmp dir, and closes everything it
launched in a ``finally`` block.
"""

from __future__ import annotations

import pytest

from octowright import defaults

pytestmark = pytest.mark.live_browser

_NO_ENGINE = (
    "executable doesn't exist",
    "missing x server",
    "xserver running",
    "no protocol specified",
    "playwright install",
)


def _make_pool(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> object:
    from octowright.browser_pool import pool as _pool
    from octowright.browser_pool.pool import BrowserPool

    rec = tmp_path / "rec"  # type: ignore[operator]
    rec.mkdir()
    monkeypatch.setattr(defaults, "RECORDINGS_DIR", rec)
    monkeypatch.setattr(_pool, "RECORDINGS_DIR", rec)
    return BrowserPool()


async def _launch_or_skip(pool: object, **kwargs: object) -> dict:
    try:
        return await pool.launch(**kwargs)  # type: ignore[attr-defined]
    except Exception as exc:
        if any(snippet in str(exc).lower() for snippet in _NO_ENGINE):
            pytest.skip(f"live browser engine unavailable: {exc}")
        raise


async def test_headed_launch_is_protected_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    pytest.importorskip("playwright")
    pool = _make_pool(monkeypatch, tmp_path)
    try:
        res = await _launch_or_skip(pool, kind="chromium", url="about:blank", headed=True)
        session = pool.get(res["instance_id"])  # type: ignore[attr-defined]
        assert session.protected is True
        assert session.protected_reason == "headed_default"
    finally:
        await pool.close_all(force=True)  # type: ignore[attr-defined]


async def test_headless_launch_is_not_protected(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    pytest.importorskip("playwright")
    pool = _make_pool(monkeypatch, tmp_path)
    try:
        res = await _launch_or_skip(pool, kind="chromium", url="about:blank", headed=False)
        session = pool.get(res["instance_id"])  # type: ignore[attr-defined]
        assert session.protected is False
    finally:
        await pool.close_all(force=True)  # type: ignore[attr-defined]


async def test_explicit_false_overrides_headed_default(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    pytest.importorskip("playwright")
    pool = _make_pool(monkeypatch, tmp_path)
    try:
        res = await _launch_or_skip(pool, kind="chromium", url="about:blank", headed=True, protected=False)
        session = pool.get(res["instance_id"])  # type: ignore[attr-defined]
        assert session.protected is False
    finally:
        await pool.close_all(force=True)  # type: ignore[attr-defined]


async def test_protect_headed_env_off(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    pytest.importorskip("playwright")
    monkeypatch.setattr(defaults, "PROTECT_HEADED_DEFAULT", False)
    pool = _make_pool(monkeypatch, tmp_path)
    try:
        res = await _launch_or_skip(pool, kind="chromium", url="about:blank", headed=True)
        session = pool.get(res["instance_id"])  # type: ignore[attr-defined]
        assert session.protected is False
    finally:
        await pool.close_all(force=True)  # type: ignore[attr-defined]


async def test_ephemeral_headed_stays_closeable(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    pytest.importorskip("playwright")
    pool = _make_pool(monkeypatch, tmp_path)
    try:
        res = await _launch_or_skip(pool, kind="chromium", url="about:blank", headed=True, ephemeral=True)
        session = pool.get(res["instance_id"])  # type: ignore[attr-defined]
        assert session.protected is False
    finally:
        await pool.close_all(force=True)  # type: ignore[attr-defined]


async def test_roster_headed_participant_is_protected_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """A roster/scenario-style launch (pool.spawn_roster) auto-protects a
    headed, non-ephemeral participant exactly like a direct pool.launch —
    both route through the same ``_launch_impl`` -> ``resolve_protected``
    chokepoint, but nothing pinned that down with a test before.
    """
    pytest.importorskip("playwright")
    pool = _make_pool(monkeypatch, tmp_path)
    try:
        specs = [{"kind": "chromium", "url": "about:blank", "headed": True, "label": "roster-headed"}]
        try:
            result = await pool.spawn_roster(specs)  # type: ignore[attr-defined]
        except Exception as exc:
            if any(snippet in str(exc).lower() for snippet in _NO_ENGINE):
                pytest.skip(f"live browser engine unavailable: {exc}")
            raise
        # Unlike pool.launch(), spawn_roster() doesn't raise on a per-participant
        # launch failure — it collects it in result["errors"] instead. Route those
        # through the same engine-unavailable skip as the exception path above.
        for err in result["errors"]:
            if any(snippet in str(err).lower() for snippet in _NO_ENGINE):
                pytest.skip(f"live browser engine unavailable: {err}")
        assert result["errors"] == []
        assert len(result["launched"]) == 1
        instance_id = result["launched"][0]["instance_id"]
        session = pool.get(instance_id)  # type: ignore[attr-defined]
        assert session.protected is True
        assert session.protected_reason == "headed_default"
    finally:
        await pool.close_all(force=True)  # type: ignore[attr-defined]


async def test_relaunch_fluid_preserves_headed_default_protection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """relaunch_fluid preserves BOTH the protected bool and the original
    protected_reason ("headed_default") across the close+relaunch round-trip.

    Without the reason fix, resolve_protected() always re-stamps an explicit
    (non-None) protected value as reason="explicit", so the tailored
    close-refusal message would revert to the generic one after a relaunch.
    """
    pytest.importorskip("playwright")
    pool = _make_pool(monkeypatch, tmp_path)
    try:
        res = await _launch_or_skip(pool, kind="chromium", url="about:blank", headed=True)
        session = pool.get(res["instance_id"])  # type: ignore[attr-defined]
        assert session.protected is True
        assert session.protected_reason == "headed_default"

        relaunch_result = await pool.relaunch_fluid(res["instance_id"])  # type: ignore[attr-defined]
        new_session = pool.get(relaunch_result["new_instance_id"])  # type: ignore[attr-defined]
        assert new_session.protected is True
        assert new_session.protected_reason == "headed_default"
    finally:
        await pool.close_all(force=True)  # type: ignore[attr-defined]
