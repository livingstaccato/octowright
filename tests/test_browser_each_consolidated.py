# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""TDD tests for the consolidated browser_each tool.

Written before the implementation exists — each test must fail first.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from octowright.server.browser import each


class _FakeSession:
    def __init__(self, instance_id: str, *, raise_on: str | None = None) -> None:
        self.instance_id = instance_id
        self._raise_on = raise_on
        self.log_path = Path(f"/tmp/{instance_id}.jsonl")
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def _record(self, kind: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((kind, args, kwargs))
        if self._raise_on == kind:
            raise RuntimeError(f"{kind} failed for {self.instance_id}")
        return {"kind": kind, "args": args, "kwargs": kwargs, "id": self.instance_id}

    async def navigate(self, url: str) -> Any:
        return await self._record("navigate", url)

    async def resize(self, width: int, height: int) -> Any:
        return await self._record("resize", width, height)

    async def evaluate(self, expression: str) -> Any:
        return await self._record("evaluate", expression)

    async def wait_for(self, *, selector: str | None, text: str | None, timeout_ms: int | None) -> Any:
        return await self._record("wait_for", selector=selector, text=text, timeout_ms=timeout_ms)

    async def screenshot(self, path: Path) -> Path:
        await self._record("screenshot", path)
        return path


class _FakePool:
    def __init__(self, sessions: dict[str, _FakeSession]) -> None:
        self._sessions = sessions

    def list_sessions(self) -> list[dict[str, Any]]:
        return [{"instance_id": iid} for iid in self._sessions]

    def get(self, instance_id: str) -> _FakeSession:
        return self._sessions[instance_id]


@pytest.fixture
def fake_pool(monkeypatch: pytest.MonkeyPatch) -> _FakePool:
    sessions = {
        "alpha": _FakeSession("alpha"),
        "beta": _FakeSession("beta"),
    }
    fake = _FakePool(sessions)
    monkeypatch.setattr(each, "pool", fake)
    return fake


# ── navigate ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_each_navigate_hits_all_live_sessions(fake_pool: _FakePool) -> None:
    out = await each.browser_each("navigate", url="https://octowright.com")
    assert set(out.keys()) == {"alpha", "beta"}
    for _iid, record in out.items():
        assert record["ok"] is True
        assert record["result"]["args"] == ("https://octowright.com",)


@pytest.mark.asyncio
async def test_browser_each_navigate_respects_instance_ids(fake_pool: _FakePool) -> None:
    out = await each.browser_each("navigate", url="https://octowright.com", instance_ids=["alpha"])
    assert list(out.keys()) == ["alpha"]
    assert "beta" not in out


# ── resize ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_each_resize_forwards_width_and_height(fake_pool: _FakePool) -> None:
    out = await each.browser_each("resize", width=1920, height=1080)
    assert out["alpha"]["result"]["args"] == (1920, 1080)
    assert out["beta"]["result"]["args"] == (1920, 1080)


# ── evaluate ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_each_evaluate_passes_expression(fake_pool: _FakePool) -> None:
    out = await each.browser_each("evaluate", expression="document.title")
    assert out["alpha"]["result"]["args"] == ("document.title",)
    assert out["beta"]["result"]["args"] == ("document.title",)


# ── wait_for ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_each_wait_for_passes_selector_and_timeout(fake_pool: _FakePool) -> None:
    out = await each.browser_each("wait_for", selector="main", timeout_ms=5000)
    assert out["alpha"]["result"]["kwargs"] == {"selector": "main", "text": None, "timeout_ms": 5000}


@pytest.mark.asyncio
async def test_browser_each_wait_for_passes_text(fake_pool: _FakePool) -> None:
    out = await each.browser_each("wait_for", text="Ready")
    assert out["alpha"]["result"]["kwargs"]["text"] == "Ready"
    assert out["alpha"]["result"]["kwargs"]["selector"] is None


# ── screenshot ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_each_screenshot_returns_path_per_instance(fake_pool: _FakePool) -> None:
    out = await each.browser_each("screenshot")
    assert out["alpha"]["ok"] is True
    assert "path" in out["alpha"]["result"]
    assert out["beta"]["ok"] is True


# ── error isolation ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_each_error_in_one_instance_does_not_cancel_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = {
        "good": _FakeSession("good"),
        "bad": _FakeSession("bad", raise_on="navigate"),
    }
    monkeypatch.setattr(each, "pool", _FakePool(sessions))

    out = await each.browser_each("navigate", url="https://octowright.com")
    assert out["good"]["ok"] is True
    assert out["bad"]["ok"] is False
    assert "navigate failed for bad" in out["bad"]["error"]


# ── unknown action ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_each_unknown_action_returns_error_for_each_instance(
    fake_pool: _FakePool,
) -> None:
    out = await each.browser_each("teleport", url="https://octowright.com")
    for record in out.values():
        assert record["ok"] is False
        assert "teleport" in record["error"]


# ── missing required param ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_each_navigate_missing_url_returns_error(fake_pool: _FakePool) -> None:
    out = await each.browser_each("navigate")
    for record in out.values():
        assert record["ok"] is False
        assert "url" in record["error"]


@pytest.mark.asyncio
async def test_browser_each_resize_missing_dimensions_returns_error(fake_pool: _FakePool) -> None:
    out = await each.browser_each("resize", width=1920)
    for record in out.values():
        assert record["ok"] is False
        assert "height" in record["error"]
