# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Which requests ``expect_network_clean`` waits for, and when that tracking exists at all.

Lazy: a session pays for ``request``/``requestfinished`` events only once
something will judge them -- a ``mark_network_clean`` step, a macro run whose
actions contain ``expect_network_clean``, or a direct call to it. The failure
counters (``requestfailed``/``response``/``pageerror``) stay unconditional.

Navigation: a request belonging to a document that has been replaced can no
longer finish observably (Chromium fires neither ``requestfinished`` nor
``requestfailed`` for it), so a cross-document commit forgets it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.browser_pool.listeners import _wire_listeners
from octowright.macros import execution
from octowright.request_failures import ABORTED_REQUEST_FAILURES
from octowright.session import core_network_mixin
from octowright.session.core import BrowserSession


class _Page:
    """Weakref-able, records the events it is subscribed to, has a main frame."""

    def __init__(self) -> None:
        self.handlers: dict[str, list[Any]] = {}
        self.main_frame = object()
        self.url = "https://octowright.com/"

    def on(self, event: str, handler: Any) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def is_closed(self) -> bool:
        return False

    def fire(self, event: str, payload: Any) -> None:
        for handler in self.handlers.get(event, []):
            handler(payload)


@pytest.fixture
def session(tmp_path: Path) -> BrowserSession:
    page = AsyncMock()
    page.url = "https://octowright.com/"
    page.is_closed = MagicMock(return_value=False)
    page.on = MagicMock()
    return BrowserSession(
        instance_id="test",
        kind="chromium",
        label="t",
        url="https://octowright.com",
        page=page,
        context=MagicMock(),
        browser=MagicMock(),
        log_path=tmp_path / "t.jsonl",
        recorder=MagicMock(),
    )


def _request(frame: Any = None, navigation: bool = False, resource_type: str = "fetch") -> MagicMock:
    request = MagicMock(url="https://api.test/x", method="GET", resource_type=resource_type, failure=None)
    request.headers = {}
    request.frame = frame
    request.is_navigation_request = MagicMock(return_value=navigation)
    return request


# --- lazy wiring -------------------------------------------------------------------------


def test_a_fresh_session_does_not_subscribe_to_request_lifecycle_events(session: BrowserSession) -> None:
    page = _Page()
    _wire_listeners(session, page)
    assert "request" not in page.handlers and "requestfinished" not in page.handlers
    # The failure counters must be complete whether or not anything waits.
    assert {"requestfailed", "response", "pageerror"} <= set(page.handlers)


def test_enabling_wires_current_pages_and_pages_opened_later(session: BrowserSession) -> None:
    current = _Page()
    session.pages.append(current)  # type: ignore[arg-type]
    _wire_listeners(session, current)
    session.enable_inflight_tracking()
    assert {"request", "requestfinished"} <= set(current.handlers)

    later = _Page()
    _wire_listeners(session, later)
    assert {"request", "requestfinished"} <= set(later.handlers)


def test_enabling_twice_does_not_double_subscribe(session: BrowserSession) -> None:
    page = _Page()
    session.pages.append(page)  # type: ignore[arg-type]
    session.enable_inflight_tracking()
    session.enable_inflight_tracking()
    _wire_listeners(session, page)
    assert len(page.handlers["request"]) == 1


@pytest.mark.anyio
async def test_mark_network_clean_enables_tracking(session: BrowserSession) -> None:
    await session.mark_network_clean()
    assert session._inflight_tracking


@pytest.mark.anyio
async def test_a_direct_expect_network_clean_enables_tracking(session: BrowserSession) -> None:
    await session.expect_network_clean(settle_timeout_ms=0)
    assert session._inflight_tracking


def _load(monkeypatch: pytest.MonkeyPatch, macros: dict[str, list[dict[str, Any]]]) -> None:
    monkeypatch.setattr(execution, "load_macro", lambda name: {"name": name, "actions": macros[name]})


@pytest.mark.anyio
async def test_a_macro_without_the_assertion_does_not_enable_tracking(
    session: BrowserSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _load(monkeypatch, {"plain": [{"action": "click", "selector": "#go"}, {"action": "expect_url", "pattern": "x"}]})
    seen: list[bool] = []
    monkeypatch.setattr(execution, "_dispatch_one", _recording_dispatch(session, seen))
    await execution.run_macro(session, "plain")
    assert seen == [False, False] and not session._inflight_tracking


@pytest.mark.anyio
@pytest.mark.parametrize(
    "actions",
    [
        [{"action": "expect_network_clean", "settle_timeout_ms": 0}],
        [{"action": "try", "actions": [{"action": "expect_network_clean", "settle_timeout_ms": 0}]}],
        [{"action": "macro_call", "name": "{{inner}}"}],
    ],
    ids=["top-level", "nested-in-try", "via-macro-call"],
)
async def test_a_macro_that_asserts_enables_tracking_before_its_first_step(
    session: BrowserSession, monkeypatch: pytest.MonkeyPatch, actions: list[dict[str, Any]]
) -> None:
    """Enabled before dispatch, so the requests the journey starts are waited for."""
    _load(monkeypatch, {"outer": actions, "verify": [{"action": "expect_network_clean", "settle_timeout_ms": 0}]})
    seen: list[bool] = []
    monkeypatch.setattr(execution, "_dispatch_one", _recording_dispatch(session, seen))
    await execution.run_macro(session, "outer", {"inner": "verify"})
    assert seen and seen[0] is True


def _recording_dispatch(session: BrowserSession, seen: list[bool]) -> Any:
    async def dispatch(*_args: Any, **_kwargs: Any) -> tuple[int, int]:
        seen.append(session._inflight_tracking)
        return 1, 0

    return dispatch


def test_an_unloadable_nested_macro_does_not_break_the_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.macros.calls import actions_assert_network_clean

    def load(name: str) -> dict[str, Any]:
        raise FileNotFoundError(name)

    assert not actions_assert_network_clean([{"action": "macro_call", "name": "gone"}], load, lambda a, _b: a)


def test_a_recursive_macro_call_terminates() -> None:
    from octowright.macros.calls import actions_assert_network_clean

    macros = {"a": [{"action": "macro_call", "name": "a"}]}
    assert not actions_assert_network_clean(
        macros["a"], lambda name: {"actions": macros[name]}, lambda actions, _args: actions
    )


# --- a replaced document's requests are forgotten ---------------------------------------


def _tracked(session: BrowserSession) -> _Page:
    page = _Page()
    session.pages.append(page)  # type: ignore[arg-type]
    _wire_listeners(session, page)
    session.enable_inflight_tracking()
    return page


def test_a_cross_document_commit_forgets_the_old_documents_requests(session: BrowserSession) -> None:
    page = _tracked(session)
    child = object()
    page.fire("request", _request(page.main_frame))  # the old document's fetch
    page.fire("request", _request(child))  # an old iframe's fetch
    navigation = _request(page.main_frame, navigation=True)
    page.fire("request", navigation)
    assert session.pending_requests() == 3
    page.fire("framenavigated", page.main_frame)
    # The request that brought the new document may still be streaming its body.
    assert list(session._inflight_requests) == [navigation]


def test_a_same_document_navigation_keeps_the_documents_requests(session: BrowserSession) -> None:
    """history.pushState / a fragment also fires framenavigated; the document is still alive."""
    page = _tracked(session)
    page.fire("request", _request(page.main_frame))
    page.fire("framenavigated", page.main_frame)
    assert session.pending_requests() == 1


def test_a_subframe_commit_forgets_only_that_frames_requests(session: BrowserSession) -> None:
    page = _tracked(session)
    child = object()
    page.fire("request", _request(page.main_frame))
    page.fire("request", _request(child))
    page.fire("request", _request(child, navigation=True))
    page.fire("framenavigated", child)
    remaining = [(value[1], value[2]) for value in session._inflight_requests.values()]
    assert sorted(remaining, key=lambda r: r[1]) == [(page.main_frame, False), (child, True)]


def test_a_detached_frames_requests_are_forgotten(session: BrowserSession) -> None:
    page = _tracked(session)
    child = object()
    page.fire("request", _request(child))
    page.fire("request", _request(page.main_frame))
    page.fire("framedetached", child)
    assert session.pending_requests() == 1


def test_a_request_without_a_frame_is_still_tracked(session: BrowserSession) -> None:
    """Service-worker requests raise on ``.frame``; they are still in flight."""
    page = _tracked(session)
    request = _request()
    type(request).frame = property(lambda _self: (_ for _ in ()).throw(RuntimeError("no frame")))
    page.fire("request", request)
    assert session.pending_requests() == 1


# --- evicted requests are reported, not hidden ------------------------------------------


@pytest.mark.anyio
async def test_requests_evicted_past_the_limit_are_reported(
    session: BrowserSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(core_network_mixin, "INFLIGHT_REQUEST_LIMIT", 3)
    page = _tracked(session)
    for _ in range(3):
        page.fire("request", _request(page.main_frame))
    session.mark_network_clean_window()  # evictions before the window do not count
    for _ in range(5):
        page.fire("request", _request(page.main_frame))
    result = await session.expect_network_clean(settle_timeout_ms=0)
    assert result["in_flight"] == 3
    assert result["in_flight_untracked"] == 5


@pytest.mark.anyio
async def test_no_untracked_key_when_nothing_was_evicted(session: BrowserSession) -> None:
    result = await session.expect_network_clean(settle_timeout_ms=0)
    assert "in_flight_untracked" not in result


@pytest.mark.anyio
async def test_untracked_is_counted_from_the_explicit_mark_for_since_mark(
    session: BrowserSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(core_network_mixin, "INFLIGHT_REQUEST_LIMIT", 1)
    page = _tracked(session)
    page.fire("request", _request(page.main_frame))
    await session.mark_network_clean()
    page.fire("request", _request(page.main_frame))
    page.fire("request", _request(page.main_frame))
    session.mark_network_clean_window()
    result = await session.expect_network_clean(since="mark", settle_timeout_ms=0)
    assert result["in_flight_untracked"] == 2


# --- the abort spellings, as literals ---------------------------------------------------


@pytest.mark.parametrize(
    "spelling",
    [
        "net::ERR_ABORTED",  # Chromium, measured
        "NS_BINDING_ABORTED",  # Firefox, measured
        "Load request cancelled",  # WebKit on Linux, measured
        "cancelled",  # WebKit on macOS (CFNetwork NSURLErrorCancelled), NOT measured
    ],
)
def test_each_abort_spelling_is_an_abort(session: BrowserSession, spelling: str) -> None:
    """Fixed literals rather than the set itself, so removing one is caught."""
    assert spelling in ABORTED_REQUEST_FAILURES
    request = _request()
    request.failure = spelling
    session._handle_request_failed(request)
    assert session.network_failures_since()[0] == 0


def test_a_real_failure_is_not_an_abort(session: BrowserSession) -> None:
    request = _request()
    request.failure = "net::ERR_CONNECTION_REFUSED"
    session._handle_request_failed(request)
    assert session.network_failures_since()[0] == 1
