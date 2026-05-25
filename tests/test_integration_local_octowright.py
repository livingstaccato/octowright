# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from octowright.browser_pool import BrowserPool

pytestmark = [pytest.mark.integration_local, pytest.mark.live_browser]


def _configure_runtime_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rec = tmp_path / "recordings"
    prof = tmp_path / "profiles"
    rec.mkdir()
    prof.mkdir()
    monkeypatch.setenv("OCTOWRIGHT_HEADLESS", "1")
    monkeypatch.setenv("OCTOWRIGHT_RECORDINGS", str(rec))
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(prof))

    import octowright.browser_pool.launch_helpers as _launch_helpers
    import octowright.browser_pool.pool as _pool
    from octowright import defaults as _defaults
    from octowright import engine_profiles as _profiles
    from octowright import personas as _personas

    monkeypatch.setattr(_defaults, "RECORDINGS_DIR", rec)
    monkeypatch.setattr(_pool, "RECORDINGS_DIR", rec)
    monkeypatch.setattr(_launch_helpers, "RECORDINGS_DIR", rec)
    monkeypatch.setattr(_defaults, "PROFILES_DIR", prof)
    monkeypatch.setattr(_personas, "PROFILES_DIR", prof)
    monkeypatch.setattr(_profiles, "PROFILES_DIR", prof)


@pytest.mark.asyncio
async def test_form_flow_posts_three_steps_via_octowright_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    integration_local_base_url: str,
    playground_server: object,
) -> None:
    pytest.importorskip("playwright")
    _ = playground_server
    _configure_runtime_paths(monkeypatch, tmp_path)

    pool = BrowserPool()
    try:
        launched = await pool.launch(
            kind="chromium",
            headed=False,
            url=f"{integration_local_base_url}/form-flow.html",
            label="integration-form",
            viewport_w=960,
            viewport_h=700,
        )
        session = pool.get(launched["instance_id"])
        page = session.page

        await page.fill("#name", "Octavia Wright")
        await page.click("#next-1")
        await page.fill("#email", "octavia@octowright.test")
        await page.click("#next-2")
        await page.fill("#notes", "integration local run")
        await page.click("#submit")

        async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as client:
            for _ in range(30):
                state = (await client.get(f"{integration_local_base_url}/api/state")).json()
                if len(state["form_steps"]) == 3:
                    break
                await asyncio.sleep(0.1)
        assert len(state["form_steps"]) == 3
        assert state["form_steps"][0]["label"] == "name"
        assert state["form_steps"][1]["label"] == "email"
        assert state["form_steps"][2]["label"] == "notes"
    finally:
        await pool.shutdown()
