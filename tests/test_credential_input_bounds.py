# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What one credential step may cost: time, driver round trips and handles.

``octowright.credential_input`` types a credential one key at a time, and each
key asks the page what has focus. Four things were wrong with that, each
pinned here against a scripted stand-in for Playwright (the live behaviour is
in ``test_macro_credential_input_live``):

* nothing bounded the per-key calls, so a wedged renderer hung the step, and
  the session gate it holds, forever; and the step's budget did not bound the
  typing, only the retries;
* every key asked a plain ``<input>`` for its ``content_frame()``;
* a handle was leaked when the focused-element lookup returned no element, or
  when ``content_frame()`` raised;
* a failed ``dispose()`` was swallowed silently in a user-action path.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest

from octowright import credential_input
from octowright.credential_input import CredentialInputStopped


class _Handle:
    """A JSHandle / ElementHandle: *element* says which ``as_element`` answers."""

    def __init__(self, calls: list[str], *, element: bool, props: dict[str, _Handle] | None = None) -> None:
        self.calls = calls
        self.element = element
        self.props = props or {}
        self.disposed = False
        self.typed: list[tuple[str, float | None]] = []
        self.content_frame_result: Any = None
        self.dispose_error: Exception | None = None

    def as_element(self) -> _Handle | None:
        return self if self.element else None

    async def get_properties(self) -> dict[str, _Handle]:
        self.calls.append("get_properties")
        return self.props

    async def json_value(self) -> Any:
        self.calls.append("json_value")
        return "nothing"

    async def content_frame(self) -> Any:
        self.calls.append("content_frame")
        if isinstance(self.content_frame_result, Exception):
            raise self.content_frame_result
        return self.content_frame_result

    async def owner_frame(self) -> Any:
        self.calls.append("owner_frame")
        return _Frame(self.calls, lambda: self)

    async def type(self, char: str, timeout: float | None = None) -> None:
        self.calls.append("type")
        self.typed.append((char, timeout))

    async def fill(self, value: str, timeout: float | None = None) -> None:
        self.calls.append("fill")

    async def dispose(self) -> None:
        self.calls.append("dispose")
        self.disposed = True
        if self.dispose_error is not None:
            raise self.dispose_error


class _Frame:
    url = "https://app.test/login"

    def __init__(self, calls: list[str], answer: Callable[[], Any]) -> None:
        self.calls = calls
        self.answer = answer

    async def evaluate_handle(self, _js: str, _arg: Any = None) -> Any:
        self.calls.append("evaluate_handle")
        result = self.answer()
        if asyncio.iscoroutine(result):
            return await result
        return result


class _Locator:
    def __init__(self, calls: list[str], element: _Handle | None = None) -> None:
        self.calls = calls
        self.element = element

    @property
    def first(self) -> _Locator:
        return self

    async def focus(self, timeout: float | None = None) -> None:
        self.calls.append("focus")

    async def element_handle(self, timeout: float | None = None) -> _Handle:
        self.calls.append("element_handle")
        assert self.element is not None
        return self.element


class _Page:
    def __init__(self, frame: _Frame) -> None:
        self.main_frame = frame


class _Session:
    def __init__(self, frame: _Frame) -> None:
        self.page = _Page(frame)

    @asynccontextmanager
    async def operation(self, _name: str) -> Any:
        yield


def _accept(_url: str) -> None:
    return None


async def _never() -> Any:
    await asyncio.Event().wait()


async def test_a_wedged_focus_lookup_fails_the_step_within_its_budget() -> None:
    calls: list[str] = []
    session = _Session(_Frame(calls, _never))
    started = time.monotonic()
    with pytest.raises(CredentialInputStopped, match="did not finish within 200ms"):
        await credential_input.checked_type(session, _Locator(calls), "abc", _accept, delay_ms=None, timeout_ms=200)
    assert time.monotonic() - started < 2


async def test_a_wedged_owner_frame_fails_the_fill_within_its_budget() -> None:
    calls: list[str] = []
    element = _Handle(calls, element=True)

    async def wedged() -> Any:
        await asyncio.Event().wait()

    element.owner_frame = wedged  # type: ignore[method-assign]
    session = _Session(_Frame(calls, lambda: element))
    with pytest.raises(CredentialInputStopped, match="did not finish within 200ms"):
        await credential_input.checked_fill(session, _Locator(calls, element), "v", _accept, 200, strict=False)
    assert element.disposed


async def test_the_budget_bounds_the_typing_not_only_the_retries() -> None:
    """Ten keys a hundred milliseconds apart do not fit in 300ms, as ``page.type(delay=, timeout=)`` would not."""
    calls: list[str] = []
    element = _Handle(calls, element=True)
    session = _Session(_Frame(calls, lambda: element))
    with pytest.raises(CredentialInputStopped, match="did not finish within 300ms"):
        await credential_input.checked_type(
            session, _Locator(calls), "abcdefghij", _accept, delay_ms=100, timeout_ms=300
        )
    assert 0 < len(element.typed) < 10


async def test_each_key_is_given_what_is_left_of_the_budget() -> None:
    calls: list[str] = []
    element = _Handle(calls, element=True)
    session = _Session(_Frame(calls, lambda: element))
    await credential_input.checked_type(session, _Locator(calls), "abc", _accept, delay_ms=20, timeout_ms=5000)
    timeouts = [timeout for _char, timeout in element.typed]
    assert [char for char, _timeout in element.typed] == list("abc")
    assert all(timeout is not None and 0 < timeout <= 5000 for timeout in timeouts), timeouts
    assert timeouts == sorted(timeouts, reverse=True)


async def test_a_plain_input_costs_one_lookup_and_one_key_per_character() -> None:
    """No ``content_frame()`` for an element that is not a frame, and no per-key dispose round trip."""
    calls: list[str] = []
    element = _Handle(calls, element=True)
    session = _Session(_Frame(calls, lambda: element))
    await credential_input.checked_type(session, _Locator(calls), "abcd", _accept, delay_ms=None, timeout_ms=5000)
    per_key = [call for call in calls if call not in ("focus", "dispose")]
    assert per_key == ["evaluate_handle", "type"] * 4
    assert "content_frame" not in calls
    assert element.disposed


async def test_a_lookup_that_names_no_element_is_released() -> None:
    """The lookup reports why no element may take the key; that handle is disposed too."""
    calls: list[str] = []
    stop = _Handle(calls, element=False)
    answer = _Handle(calls, element=False, props={"stop": stop})
    session = _Session(_Frame(calls, lambda: answer))
    with pytest.raises(CredentialInputStopped):
        await credential_input.checked_type(session, _Locator(calls), "a", _accept, delay_ms=None, timeout_ms=5000)
    assert answer.disposed and stop.disposed


async def test_a_frame_whose_content_frame_raises_is_released() -> None:
    calls: list[str] = []
    iframe = _Handle(calls, element=True)
    iframe.content_frame_result = RuntimeError("Target closed")
    answer = _Handle(calls, element=False, props={"frame": iframe})
    session = _Session(_Frame(calls, lambda: answer))
    with pytest.raises(RuntimeError, match="Target closed"):
        await credential_input.checked_type(session, _Locator(calls), "a", _accept, delay_ms=None, timeout_ms=5000)
    assert answer.disposed and iframe.disposed


async def test_a_failed_dispose_is_logged_not_swallowed(caplog: pytest.LogCaptureFixture) -> None:
    calls: list[str] = []
    element = _Handle(calls, element=True)
    element.dispose_error = RuntimeError("Execution context was destroyed")
    session = _Session(_Frame(calls, lambda: element))
    with caplog.at_level(logging.DEBUG, logger=credential_input.__name__):
        await credential_input.checked_type(session, _Locator(calls), "a", _accept, delay_ms=None, timeout_ms=5000)
    assert any("dispose" in record.getMessage() for record in caplog.records), caplog.records
