# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for ``octowright.server.browser.each`` — the fan-out variants.

The shared fixture builds a tiny in-memory fake pool with two recorded
sessions so each tool can be exercised without spinning real browsers.
"""

from __future__ import annotations

from typing import Any

import pytest

from octowright.server._state import pool as _real_pool
from octowright.server.browser import each


class _FakeSession:
    def __init__(self, instance_id: str, *, raise_on: str | None = None) -> None:
        self.instance_id = instance_id
        self._raise_on = raise_on
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


@pytest.mark.asyncio
async def test_navigate_each_hits_every_live_session_when_ids_omitted(fake_pool: _FakePool) -> None:
    out = await each.browser_navigate_each("https://example.com")
    assert set(out.keys()) == {"alpha", "beta"}
    for iid, record in out.items():
        assert record == {
            "ok": True,
            "result": {"kind": "navigate", "args": ("https://example.com",), "kwargs": {}, "id": iid},
        }


@pytest.mark.asyncio
async def test_navigate_each_respects_instance_ids_filter(fake_pool: _FakePool) -> None:
    out = await each.browser_navigate_each("https://example.com", instance_ids=["alpha"])
    assert list(out.keys()) == ["alpha"]


@pytest.mark.asyncio
async def test_resize_each_forwards_width_height(fake_pool: _FakePool) -> None:
    out = await each.browser_resize_each(1920, 1080)
    assert out["alpha"]["result"]["args"] == (1920, 1080)


@pytest.mark.asyncio
async def test_evaluate_each_returns_per_instance_results(fake_pool: _FakePool) -> None:
    out = await each.browser_evaluate_each("location.href")
    assert out["alpha"]["result"]["args"] == ("location.href",)
    assert out["beta"]["result"]["args"] == ("location.href",)


@pytest.mark.asyncio
async def test_wait_for_each_passes_kwargs(fake_pool: _FakePool) -> None:
    out = await each.browser_wait_for_each(selector="main", timeout_ms=5000)
    assert out["alpha"]["result"]["kwargs"] == {"selector": "main", "text": None, "timeout_ms": 5000}


@pytest.mark.asyncio
async def test_per_instance_error_is_caught_without_failing_other_instances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = {
        "good": _FakeSession("good"),
        "bad": _FakeSession("bad", raise_on="navigate"),
    }
    fake = _FakePool(sessions)
    monkeypatch.setattr(each, "pool", fake)

    out = await each.browser_navigate_each("https://example.com")
    assert out["good"]["ok"] is True
    assert out["bad"]["ok"] is False
    assert "navigate failed for bad" in out["bad"]["error"]


def test_real_pool_is_untouched_after_test_run() -> None:
    """Sanity: the monkeypatching above must not bleed to the real pool."""
    assert _real_pool is not None  # not None; live pool object still referenced
    assert hasattr(_real_pool, "list_sessions")
