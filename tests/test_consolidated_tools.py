# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for consolidated MCP tools (browser_quick_launch, browser_capture_and_close)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.browser_pool import BrowserPool
from octowright.server.browser import inspect as _inspect
from octowright.server.browser import lifecycle as _lifecycle


@pytest.fixture(autouse=True)
def _patch_state(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Replace the module-level `pool` and `resolve_mod` with mocks."""
    fake_pool = MagicMock()
    # The browser cap (now ON by default) calls pool.active_count() before each
    # user-facing launch; give it a real int so the cap check passes.
    fake_pool.active_count.return_value = 0
    fake_resolve = MagicMock()

    monkeypatch.setattr(_lifecycle, "pool", fake_pool)
    monkeypatch.setattr(_lifecycle, "resolve_mod", fake_resolve)
    monkeypatch.setattr(_inspect, "pool", fake_pool)

    return {"pool": fake_pool, "resolve": fake_resolve}


def _gated_session(*, instance_id: str = "inst-1", kind: str = "chromium") -> MagicMock:
    """Session double carrying a REAL ``SessionOperationGate``: capture-and-
    close/handoff/relaunch (Task 8) drive ``_operation_gate`` and
    ``session.operation(...)`` directly from inside the pool's close
    coordinator, so a bare unspecced MagicMock (auto-mocked, non-awaitable
    methods) cannot stand in for the session anymore."""
    from octowright.session.operation_gate import SessionOperationGate

    session = MagicMock()
    session.instance_id = instance_id
    session.kind = kind
    session.protected = False
    session.log_path = Path("/tmp/test.jsonl")
    session.video_path = None
    session.trace_path = None
    session.page = MagicMock()
    session.page.url = "https://octowright.com"
    session.page.title = AsyncMock(return_value="Example Title")

    # Mock aria_snapshot on page.locator("html")
    html_locator = MagicMock()
    html_locator.aria_snapshot = AsyncMock(return_value="aria tree content")
    session.page.locator = MagicMock(return_value=html_locator)
    # _target() defaults to the page when no frame is switched (capture_and_close
    # reads url + aria through it).
    session._target.return_value = session.page
    session.screenshot = AsyncMock(return_value=Path("/tmp/test.png"))
    session._teardown_after_close_cutoff = AsyncMock()

    gate = SessionOperationGate(instance_id, kind)
    session._operation_gate = gate
    session.operation = gate.operation
    session.operation_snapshot = gate.snapshot
    return session


@pytest.mark.anyio
async def test_browser_quick_launch_direct_profile(_patch_state: dict[str, MagicMock]) -> None:
    pool = _patch_state["pool"]
    pool.launch = AsyncMock(return_value={"instance_id": "inst-1"})

    result = await _lifecycle.browser_quick_launch(url="https://x.com", profile="my-persona")

    pool.launch.assert_awaited_once()
    assert result["instance_id"] == "inst-1"
    assert result["profile_used"] == "my-persona"


@pytest.mark.anyio
async def test_browser_quick_launch_outline_mode_returns_page_outline(
    _patch_state: dict[str, MagicMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = _patch_state["pool"]
    pool.launch = AsyncMock(return_value={"instance_id": "inst-1", "url": "https://x.com"})
    outline = AsyncMock(return_value={"url": "https://x.com", "headings": []})
    monkeypatch.setattr(_lifecycle, "browser_page_outline", outline)

    result = await _lifecycle.browser_quick_launch(
        url="https://x.com",
        profile="my-persona",
        response_mode="outline",
    )

    assert result["instance_id"] == "inst-1"
    assert result["profile_used"] == "my-persona"
    assert result["outline"]["url"] == "https://x.com"
    outline.assert_awaited_once_with("inst-1")


@pytest.mark.anyio
async def test_browser_quick_launch_with_ambiguous_suggest(_patch_state: dict[str, MagicMock]) -> None:
    resolve = _patch_state["resolve"]
    resolve.suggest_for_url.return_value = {"ambiguous": True, "matches": [{"persona": "A"}, {"persona": "B"}]}

    result = await _lifecycle.browser_quick_launch(url="https://x.com")

    assert result["ambiguous"] is True
    assert len(result["matches"]) == 2


@pytest.mark.anyio
async def test_browser_quick_launch_with_recommendation(_patch_state: dict[str, MagicMock]) -> None:
    pool = _patch_state["pool"]
    resolve = _patch_state["resolve"]
    pool.launch = AsyncMock(return_value={"instance_id": "inst-1"})
    resolve.suggest_for_url.return_value = {
        "ambiguous": False,
        "recommendation": {"persona": "RecPersona"},
        "ephemeral_ok": False,
    }

    result = await _lifecycle.browser_quick_launch(url="https://x.com")

    assert result["instance_id"] == "inst-1"
    assert result["profile_used"] == "RecPersona"

    pool.launch.assert_awaited_once()
    _, kwargs = pool.launch.call_args
    assert kwargs["url"] == "https://x.com"
    assert kwargs["profile"] == "RecPersona"


@pytest.mark.anyio
async def test_browser_quick_launch_with_resolver_match(_patch_state: dict[str, MagicMock]) -> None:
    pool = _patch_state["pool"]
    resolve = _patch_state["resolve"]
    pool.launch = AsyncMock(return_value={"instance_id": "inst-1"})
    resolve.suggest_for_url.return_value = {
        "ambiguous": False,
        "recommendation": "exactly one match: 'real/chromium'",
        "ephemeral_ok": False,
        "matches": [{"persona": "real", "kind": "chromium", "score": 3.0}],
    }

    result = await _lifecycle.browser_quick_launch(url="https://x.com")

    assert result["instance_id"] == "inst-1"
    assert result["profile_used"] == "real"
    _, kwargs = pool.launch.call_args
    assert kwargs["profile"] == "real"
    assert kwargs["kind"] == "chromium"


@pytest.mark.anyio
async def test_browser_quick_launch_missing_url(_patch_state: dict[str, MagicMock]) -> None:
    with pytest.raises(ValueError, match="url is required"):
        await _lifecycle.browser_quick_launch(url="")


@pytest.mark.anyio
async def test_browser_launch_returns_before_mcp_timeout(
    _patch_state: dict[str, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _patch_state["pool"]

    async def _hung_launch(**_kwargs: object) -> dict[str, object]:
        await asyncio.sleep(10)
        return {"instance_id": "late"}

    pool.launch = AsyncMock(side_effect=_hung_launch)
    monkeypatch.setattr(_lifecycle, "BROWSER_LAUNCH_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(TimeoutError, match=r"browser launch exceeded 0\.0s"):
        await _lifecycle.browser_launch(url="https://x.com", ephemeral=True)


@pytest.mark.anyio
async def test_browser_launch_outline_mode_returns_page_outline(
    _patch_state: dict[str, MagicMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = _patch_state["pool"]
    pool.launch = AsyncMock(return_value={"instance_id": "inst-1", "url": "https://x.com"})
    outline = AsyncMock(return_value={"url": "https://x.com", "links": []})
    monkeypatch.setattr(_lifecycle, "browser_page_outline", outline)

    result = await _lifecycle.browser_launch(
        url="https://x.com",
        ephemeral=True,
        response_mode="outline",
    )

    assert result["instance_id"] == "inst-1"
    assert result["outline"]["url"] == "https://x.com"
    outline.assert_awaited_once_with("inst-1")


@pytest.mark.anyio
async def test_browser_capture_and_close(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """browser_capture_and_close now routes through the REAL close
    coordinator (Task 8: capture runs as a preparation callback inside the
    close ticket), so this needs a real BrowserPool + a session double with
    a real gate rather than a fully-mocked pool/session pair."""
    from octowright.browser_pool import close_helpers as _close_helpers
    from octowright.server.browser import inspect_capture as _inspect_capture

    monkeypatch.setattr(_close_helpers, "remove_manifest_session", lambda _id: None)
    # Pin RECORDINGS_DIR so the default screenshot path (derived from
    # session.log_path) passes the path-containment guard.
    monkeypatch.setattr(_inspect_capture, "RECORDINGS_DIR", tmp_path)
    pool = BrowserPool()
    monkeypatch.setattr(_inspect_capture, "pool", pool)
    session = _gated_session(instance_id="inst-1")
    session.log_path = tmp_path / "test.jsonl"
    pool._sessions["inst-1"] = session

    result = await _inspect_capture.browser_capture_and_close("inst-1")

    assert result["title"] == "Example Title"
    assert result["closed"] is True
    assert "aria" in result
    session.screenshot.assert_awaited_once()
    session._teardown_after_close_cutoff.assert_awaited_once()


@pytest.mark.anyio
async def test_browser_relaunch_fluid_preserves_state_without_viewport(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.browser_pool import close_helpers as _close_helpers

    monkeypatch.setattr(_close_helpers, "remove_manifest_session", lambda _id: None)
    pool = BrowserPool()
    session = _gated_session(instance_id="old-id")
    session.label = "player"
    session.profile = "profile-a"
    session.stabilize = True
    session.trace = False
    session.har_path = None
    session.user_data_dir = None
    session.url = "https://octowright.com/original"
    session.page.url = "https://octowright.com/current"
    pool._sessions["old-id"] = session
    pool.launch = AsyncMock(return_value={"instance_id": "new-id"})

    result = await pool.relaunch_fluid("old-id")

    session._teardown_after_close_cutoff.assert_awaited_once()
    pool.launch.assert_awaited_once()
    _, kwargs = pool.launch.call_args
    assert kwargs["url"] == "https://octowright.com/current"
    assert kwargs["kind"] == "chromium"
    assert kwargs["label"] == "player"
    assert kwargs["profile"] == "profile-a"
    assert kwargs["headed"] is True
    assert "viewport_w" not in kwargs
    assert "viewport_h" not in kwargs
    assert result["old_instance_id"] == "old-id"
    assert result["new_instance_id"] == "new-id"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
