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
