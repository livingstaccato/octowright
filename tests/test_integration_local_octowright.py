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


@pytest.mark.asyncio
async def test_shared_canvas_claims_propagate_between_two_browsers(
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
        player_1 = await pool.launch(
            kind="chromium",
            headed=False,
            url=f"{integration_local_base_url}/canvas.html?role=player-1&colour=%23009ad6",
            label="integration-canvas-p1",
            viewport_w=960,
            viewport_h=700,
        )
        player_2 = await pool.launch(
            kind="chromium",
            headed=False,
            url=f"{integration_local_base_url}/canvas.html?role=player-2&colour=%2364c85a",
            label="integration-canvas-p2",
            viewport_w=960,
            viewport_h=700,
        )
        page_1 = pool.get(player_1["instance_id"]).page
        page_2 = pool.get(player_2["instance_id"]).page

        await page_1.click('[data-testid="tile-1-1"]')
        await page_2.click('[data-testid="tile-1-2"]')

        async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as client:
            for _ in range(30):
                state = (await client.get(f"{integration_local_base_url}/api/state")).json()
                if state["canvas"][1][1] and state["canvas"][1][2]:
                    break
                await asyncio.sleep(0.1)

        assert state["canvas"][1][1] == "#009ad6"
        assert state["canvas"][1][2] == "#64c85a"

        # The /api/state poll above establishes that the SERVER holds both
        # claims. It says nothing about either BROWSER having received the
        # broadcast and re-rendered, so reading the DOM straight after it waits
        # on one observable and asserts a different one -- which failed roughly
        # one run in three, counter_2 still showing "1" (its own claim only).
        for _ in range(30):
            counter_1 = await page_1.locator("#claim-counter").inner_text()
            counter_2 = await page_2.locator("#claim-counter").inner_text()
            if counter_1 == "2" and counter_2 == "2":
                break
            await asyncio.sleep(0.1)

        assert counter_1 == "2"
        assert counter_2 == "2"
    finally:
        await pool.shutdown()


@pytest.mark.asyncio
async def test_network_lab_buttons_hit_deterministic_server_endpoints(
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
            url=f"{integration_local_base_url}/network-lab.html",
            label="integration-network",
            viewport_w=960,
            viewport_h=700,
        )
        page = pool.get(launched["instance_id"]).page

        await page.click('[data-testid="network-ping"]')
        ping_text = ""
        for _ in range(20):
            ping_text = await page.locator("#network-result").inner_text()
            if "status: 200" in ping_text:
                break
            await asyncio.sleep(0.1)
        assert "status: 200" in ping_text

        await page.click('[data-testid="network-error"]')
        text = ""
        for _ in range(20):
            text = await page.locator("#network-result").inner_text()
            if "status: 418" in text:
                break
            await asyncio.sleep(0.1)
        assert "status: 418" in text

        async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as client:
            state = (await client.get(f"{integration_local_base_url}/api/state")).json()
        messages = [entry["message"] for entry in state["events"] if entry["source"] == "network-lab"]
        assert any("GET /api/ping 200" in msg for msg in messages)
        assert any("GET /api/error 418" in msg for msg in messages)
    finally:
        await pool.shutdown()
