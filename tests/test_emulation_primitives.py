# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Viewport/input/network emulation primitives used by interactive checks.

These pin the primitives Octowright had to offer manually through a page's
fetch: a physical mouse wheel with optional selector targeting, context-wide
offline mode, refused-request routes, and phone safe-area insets. Offline and
aborts must remain state changes in the session, not patches into page script
an agent cannot replay later, and relevant recordings must carry the same
action name for macro replay.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from octowright.recorder import Recorder
from octowright.session import BrowserSession
from octowright.session.route_carry import RouteCarry, replay_onto_session

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _FakeMouse:
    def __init__(self) -> None:
        self.moves: list[tuple[int, int]] = []

    async def move(self, x: float, y: float) -> None:
        self.moves.append((x, y))

    async def wheel(self, delta_x: int, delta_y: int) -> None:
        self.moves.append((delta_x, delta_y))


class _FakeRoute:
    def __init__(self) -> None:
        self.aborted: str | None = None

    async def abort(self, error_code: str) -> None:
        self.aborted = error_code


class _FakeLocator:
    def __init__(self) -> None:
        self.scroll_calls = 0

    async def scroll_into_view_if_needed(self) -> None:
        self.scroll_calls += 1


class _FakePage:
    def __init__(self) -> None:
        self.mouse = _FakeMouse()
        self._routes: dict[str, Any] = {}
        self.locators: dict[str, _FakeLocator] = {}
        self.metrics = {"x": 0, "y": 640, "height": 1688, "innerHeight": 844}

    async def route(self, pattern: str, handler: Any) -> None:
        self._routes[pattern] = handler

    async def unroute(self, pattern: str, handler: Any) -> None:
        assert self._routes.pop(pattern, None) is not None

    def locator(self, selector: str) -> _FakeLocator:
        return self.locators.setdefault(selector, _FakeLocator())

    async def evaluate(self, expression: Any) -> Any:
        if isinstance(expression, str) and "scrollY" in expression:
            return dict(self.metrics)
        raise AssertionError(f"unexpected page.evaluate({expression!r})")


class _FakeCDPSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def send(self, method: str, params: dict[str, Any]) -> None:
        self.calls.append((method, params))


class _FakeContext:
    def __init__(self) -> None:
        self.offline: bool | None = None
        self.cdp = _FakeCDPSession()

    async def set_offline(self, offline: bool) -> None:
        self.offline = offline

    async def new_cdp_session(self, page: Any) -> _FakeCDPSession:
        return self.cdp


def _session(tmp_path, *, kind: str = "chromium") -> BrowserSession:
    return BrowserSession(
        instance_id="demo",
        kind=kind,
        label=None,
        url="about:blank",
        browser=None,  # type: ignore[arg-type]
        context=_FakeContext(),  # type: ignore[arg-type]
        page=_FakePage(),  # type: ignore[arg-type]
        recorder=Recorder(tmp_path / "log.jsonl"),
        log_path=tmp_path / "log.jsonl",
    )


async def test_scroll_uses_a_real_wheel_and_reports_post_position(tmp_path) -> None:
    session = _session(tmp_path)
    result = await session.scroll(delta_x=18, delta_y=-220)
    assert session.page.mouse.moves == [(18, -220)]  # type: ignore[attr-defined]
    assert result["delta_y"] == -220
    assert result["y"] == 640
    assert result["height"] == 1688


async def test_scroll_can_move_an_element_into_view_first(tmp_path) -> None:
    session = _session(tmp_path)
    await session.scroll(delta_x=0, delta_y=400, selector="#order-total")
    locator = session.page.locator("#order-total")  # type: ignore[attr-defined]
    assert locator.scroll_calls == 1


async def test_set_offline_changes_context(tmp_path) -> None:
    session = _session(tmp_path)
    await session.set_offline(True)
    assert session.context.offline is True  # type: ignore[attr-defined]
    result = await session.set_offline(False)
    assert session.context.offline is False  # type: ignore[attr-defined]
    assert result == {"ok": True, "offline": False}


async def test_set_safe_area_sends_chromium_insets(tmp_path) -> None:
    session = _session(tmp_path)
    result = await session.set_safe_area(bottom=34, top=47)
    expected = {"top": 47, "right": 0, "bottom": 34, "left": 0}
    assert session.context.cdp.calls == [("Emulation.setSafeAreaInsets", {"insets": expected})]  # type: ignore[attr-defined]
    assert result["insets"] == expected
    actions = [json.loads(line)["action"] for line in (tmp_path / "log.jsonl").read_text().splitlines()]
    assert "set_safe_area" in actions
    assert "scroll" not in actions


async def test_set_safe_area_is_refused_outside_chromium(tmp_path) -> None:
    session = _session(tmp_path, kind="webkit")
    with pytest.raises(ValueError, match="Chromium"):
        await session.set_safe_area(bottom=34)


async def test_abort_route_aborts_with_the_refusal_reason(tmp_path) -> None:
    session = _session(tmp_path)
    await session.abort_route("**/api/users", error_code="connectionrefused")
    route = _FakeRoute()
    await session._active_routes["**/api/users"](route)
    assert route.aborted == "connectionrefused"
    assert session._mock_specs["**/api/users"].error_code == "connectionrefused"


async def test_abort_route_rejects_an_unknown_error_code(tmp_path) -> None:
    session = _session(tmp_path)
    with pytest.raises(ValueError, match="error_code"):
        await session.abort_route("**/api/nope", error_code="probably-dns")
    assert "**/api/nope" not in session._active_routes


async def test_abort_route_reinstalls_as_an_abort_on_a_new_session(tmp_path) -> None:
    original = _session(tmp_path)
    await original.abort_route("**/api/users", error_code="connectionrefused")
    carry = RouteCarry.of(original)
    (pattern, spec), *_ = carry.mocks
    assert pattern == "**/api/users"
    assert spec.kwargs()["error_code"] == "connectionrefused"

    replacement = _session(tmp_path)
    await replay_onto_session(replacement, carry)
    route = _FakeRoute()
    await replacement._active_routes["**/api/users"](route)
    assert route.aborted == "connectionrefused"
    assert not hasattr(replacement.page, "fulfilled"), "an abort must not become a fulfilled mock on replay"
