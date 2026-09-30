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
  typing, only the retries. The budget now counts what the page takes, not
  the pauses the step asked for, and backs Playwright's own timeouts up
  rather than racing them, so a selector that never matches is reported by
  Playwright;
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
    with pytest.raises(CredentialInputStopped, match="did not start within 200ms"):
        await credential_input.checked_type(session, _Locator(calls), "abc", _accept, delay_ms=None, timeout_ms=200)
    assert time.monotonic() - started < 2


async def test_a_wedged_owner_frame_fails_the_fill_within_its_budget() -> None:
    calls: list[str] = []
    element = _Handle(calls, element=True)

    async def wedged() -> Any:
        await asyncio.Event().wait()

    element.owner_frame = wedged  # type: ignore[method-assign]
    session = _Session(_Frame(calls, lambda: element))
    with pytest.raises(CredentialInputStopped, match="did not start within 200ms"):
        await credential_input.checked_fill(session, _Locator(calls, element), "v", _accept, 200)
    assert element.disposed


async def test_each_key_is_given_what_is_left_of_the_budget() -> None:
    calls: list[str] = []
    element = _Handle(calls, element=True)
    session = _Session(_Frame(calls, lambda: element))
    await credential_input.checked_type(session, _Locator(calls), "abc", _accept, delay_ms=20, timeout_ms=5000)
    timeouts = [timeout for _char, timeout in element.typed]
    assert [char for char, _timeout in element.typed] == list("abc")
    # The budget is the action timeout plus the step's own pauses, 3 * 20ms.
    assert all(timeout is not None and 0 < timeout <= 5060 for timeout in timeouts), timeouts
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


class _PlaywrightTimeout(Exception):
    """Playwright's own ``TimeoutError``, which names what it waited for."""


class _NeverMatches(_Locator):
    """A selector nothing matches: Playwright waits out the timeout it is given, then says what it waited for."""

    async def _wait_out(self, timeout: float | None) -> None:
        assert timeout is not None
        await asyncio.sleep(timeout / 1000)
        raise _PlaywrightTimeout(f"Timeout {timeout:g}ms exceeded.\n  - waiting for locator('#no-such-field')")

    async def focus(self, timeout: float | None = None) -> None:
        await self._wait_out(timeout)

    async def element_handle(self, timeout: float | None = None) -> _Handle:
        await self._wait_out(timeout)
        raise AssertionError("unreachable")


async def test_a_fill_whose_selector_never_matches_reports_playwrights_wait() -> None:
    """Playwright's timeout fires first, naming the locator; the step's own bound is only the backstop."""
    calls: list[str] = []
    session = _Session(_Frame(calls, _never))
    for _ in range(5):  # the two used to expire together, and the backstop usually won
        with pytest.raises(_PlaywrightTimeout, match="waiting for locator"):
            await credential_input.checked_fill(session, _NeverMatches(calls), "v", _accept, 100)


async def test_a_type_whose_selector_never_matches_reports_playwrights_wait() -> None:
    calls: list[str] = []
    session = _Session(_Frame(calls, _never))
    for _ in range(5):
        with pytest.raises(_PlaywrightTimeout, match="waiting for locator"):
            await credential_input.checked_type(
                session, _NeverMatches(calls), "abc", _accept, delay_ms=None, timeout_ms=100
            )


async def test_a_step_stopped_before_its_first_key_says_nothing_was_typed() -> None:
    calls: list[str] = []
    session = _Session(_Frame(calls, _never))
    with pytest.raises(CredentialInputStopped, match="did not start") as raised:
        await credential_input.checked_type(session, _Locator(calls), "abc", _accept, delay_ms=None, timeout_ms=100)
    assert raised.value.started is False


async def test_a_step_stopped_partway_says_so() -> None:
    calls: list[str] = []
    element = _Handle(calls, element=True)
    answers = iter([element, element])

    async def lookup() -> Any:
        return next(answers, None) or await _never()

    session = _Session(_Frame(calls, lookup))
    with pytest.raises(CredentialInputStopped, match="did not finish") as raised:
        await credential_input.checked_type(session, _Locator(calls), "abcd", _accept, delay_ms=None, timeout_ms=100)
    assert raised.value.started is True
    assert [char for char, _timeout in element.typed] == ["a", "b"]


async def test_the_pauses_between_keys_are_not_counted_against_the_budget() -> None:
    """Ten keys a hundred milliseconds apart under a 300ms action timeout: the budget is 300 + 10 * 100."""
    calls: list[str] = []
    element = _Handle(calls, element=True)
    session = _Session(_Frame(calls, lambda: element))
    await credential_input.checked_type(session, _Locator(calls), "abcdefghij", _accept, delay_ms=100, timeout_ms=300)
    assert [char for char, _timeout in element.typed] == list("abcdefghij")


async def test_slow_keys_still_spend_the_budget() -> None:
    """What the page takes to answer is counted: keys that take 100ms each stop a 300ms step partway."""
    calls: list[str] = []
    element = _Handle(calls, element=True)
    session = _Session(_Frame(calls, lambda: element))

    async def slow(handle: Any, char: str, timeout_ms: float) -> None:
        await asyncio.sleep(0.1)
        await handle.type(char, timeout=timeout_ms)

    with pytest.raises(CredentialInputStopped, match="did not finish within 300ms"):
        await credential_input.checked_type(
            session, _Locator(calls), "abcdefghij", _accept, delay_ms=None, timeout_ms=300, send=slow
        )
    assert 0 < len(element.typed) < 10


async def test_a_fill_uses_the_locator_it_is_given() -> None:
    """The caller chooses ``locator.first`` (a selector fill) or the strict locator (``fill_by``)."""
    calls: list[str] = []
    element = _Handle(calls, element=True)
    session = _Session(_Frame(calls, lambda: element))

    class _Strict(_Locator):
        @property
        def first(self) -> _Locator:
            raise AssertionError("checked_fill must not narrow the locator it was given")

    await credential_input.checked_fill(session, _Strict(calls, element), "v", _accept, 5000)
    assert "fill" in calls


async def test_a_focus_stop_at_the_first_key_says_nothing_was_typed() -> None:
    """Stopped before any key went to the page, the step must not claim it stopped partway."""
    calls: list[str] = []
    stop = _Handle(calls, element=False)
    answer = _Handle(calls, element=False, props={"stop": stop})
    session = _Session(_Frame(calls, lambda: answer))
    with pytest.raises(CredentialInputStopped) as raised:
        await credential_input.checked_type(session, _Locator(calls), "abc", _accept, delay_ms=None, timeout_ms=5000)
    assert raised.value.started is False


class TimeoutError(Exception):  # noqa: A001 - named as Playwright's own, which is not the builtin
    """Playwright's ``TimeoutError``: its own class, not a subclass of the builtin."""


async def test_a_deadline_spent_during_the_focus_lookup_stops_the_step() -> None:
    """The lookup outlasts the budget: the key is not sent with a 1ms timeout, the step stops in its own words."""
    calls: list[str] = []
    element = _Handle(calls, element=True)
    sent: list[float] = []

    async def slow_lookup() -> Any:
        await asyncio.sleep(0.15)
        return element

    async def send(_handle: Any, _char: str, timeout_ms: float) -> None:
        sent.append(timeout_ms)
        raise TimeoutError(f"Timeout {timeout_ms:g}ms exceeded.")

    session = _Session(_Frame(calls, slow_lookup))
    with pytest.raises(CredentialInputStopped, match="did not start within 100ms") as raised:
        await credential_input.checked_type(
            session, _Locator(calls), "abc", _accept, delay_ms=None, timeout_ms=100, send=send
        )
    assert raised.value.started is False
    assert sent == []


async def test_a_playwright_timeout_on_a_key_becomes_the_steps_own_stop() -> None:
    """Each key is given what is left of the budget, so its timeout is the step's: say so, not Playwright's 1ms."""
    calls: list[str] = []
    element = _Handle(calls, element=True)
    keys = iter([None])

    async def send(handle: Any, char: str, timeout_ms: float) -> None:
        if next(keys, "timeout") is None:
            await handle.type(char, timeout=timeout_ms)
            return
        raise TimeoutError(f"Timeout {timeout_ms:g}ms exceeded.")

    session = _Session(_Frame(calls, lambda: element))
    with pytest.raises(CredentialInputStopped, match="did not finish within 5000ms") as raised:
        await credential_input.checked_type(
            session, _Locator(calls), "abc", _accept, delay_ms=None, timeout_ms=5000, send=send
        )
    assert raised.value.started is True
