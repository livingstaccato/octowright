# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A credential fill whose checked element goes away before its owner frame is read.

``checked_fill`` resolves the field, asks the handle for the frame that owns
it, checks that frame's origin, and fills. A navigation that lands between the
resolve and ``owner_frame()`` leaves the handle pointing at an object the new
document does not have, and every engine raises there -- each in its own words
(measured on Linux, Playwright 1.62, by navigating after ``element_handle``):

* chromium: ``Execution context was destroyed, most likely because of a navigation``
* firefox: ``Protocol error (Page.describeNode): ... Cannot find object with id = id-11``
* webkit: ``Protocol error (DOM.describeNode): Node not found``

That raise was outside the detach retry, so it left the step as a raw
Playwright error instead of the refusal the step gives a navigation it sees
(the firefox shape was the Windows CI failure). Each shape must now re-resolve
and re-check, so the foreign document is refused by *check*; anything else
still propagates untouched. The live counterpart is
``test_macro_credential_input_live::test_a_navigation_while_the_fill_waits_on_a_disabled_field_is_refused``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest

from octowright import credential_input

TRUSTED = "https://app.test/disabled"
EVIL = "https://evil.test/login"

OWNER_FRAME_DETACHED = {
    "chromium": "ElementHandle.owner_frame: Execution context was destroyed, most likely because of a navigation",
    "firefox": 'ElementHandle.owner_frame: Protocol error (Page.describeNode): error in channel "content::11/12/4": '
    'exception while running method "describeNode" in namespace "page": Cannot find object with id = id-37 '
    "unsafeObject@chrome://juggler/content/content/PageAgent.js:1:1",
    "webkit": "ElementHandle.owner_frame: Protocol error (DOM.describeNode): Node not found",
}


class _Refused(RuntimeError):
    pass


def _check(url: str) -> None:
    if not url.startswith("https://app.test"):
        raise _Refused(f"credential arg {{{{password}}}} refused: {url} is not a trusted origin")


class _Frame:
    def __init__(self, url: str) -> None:
        self.url = url


class _Handle:
    def __init__(self, owner: str | Exception) -> None:
        self.owner = owner
        self.filled: list[str] = []
        self.disposed = False

    async def owner_frame(self) -> _Frame:
        if isinstance(self.owner, Exception):
            raise self.owner
        return _Frame(self.owner)

    async def fill(self, value: str, timeout: float | None = None) -> None:
        self.filled.append(value)

    async def dispose(self) -> None:
        self.disposed = True


class _Locator:
    """Answers each ``element_handle`` with the next handle: the page as it is at that moment."""

    def __init__(self, *handles: _Handle) -> None:
        self.handles = list(handles)
        self.resolved = 0

    async def element_handle(self, timeout: float | None = None) -> _Handle:
        handle = self.handles[min(self.resolved, len(self.handles) - 1)]
        self.resolved += 1
        return handle


class _Session:
    @asynccontextmanager
    async def operation(self, _name: str) -> Any:
        yield


@pytest.mark.parametrize("engine", sorted(OWNER_FRAME_DETACHED))
async def test_a_navigation_that_detaches_the_checked_field_is_refused(engine: str) -> None:
    stale = _Handle(Exception(OWNER_FRAME_DETACHED[engine]))
    foreign = _Handle(EVIL)
    locator = _Locator(stale, foreign)
    with pytest.raises(_Refused, match=r"credential arg \{\{password\}\}") as raised:
        await credential_input.checked_fill(_Session(), locator, "s3cret", _check, 5000)
    assert "s3cret" not in str(raised.value)
    assert stale.filled == foreign.filled == []
    assert locator.resolved == 2
    assert stale.disposed and foreign.disposed


@pytest.mark.parametrize("engine", sorted(OWNER_FRAME_DETACHED))
async def test_a_same_origin_navigation_that_detaches_the_checked_field_is_filled_there(engine: str) -> None:
    """The same shapes when the new document is trusted too: re-resolved, re-checked, filled, as a re-render is."""
    stale = _Handle(Exception(OWNER_FRAME_DETACHED[engine]))
    fresh = _Handle(TRUSTED)
    await credential_input.checked_fill(_Session(), _Locator(stale, fresh), "s3cret", _check, 5000)
    assert stale.filled == []
    assert fresh.filled == ["s3cret"]


@pytest.mark.parametrize(
    "message",
    [
        "ElementHandle.owner_frame: Target page, context or browser has been closed",
        "ElementHandle.owner_frame: Protocol error (DOM.describeNode): Some other failure",
        "Node not found",  # webkit's words, but not from describeNode
    ],
)
async def test_an_unrelated_owner_frame_error_propagates(message: str) -> None:
    handle = _Handle(Exception(message))
    locator = _Locator(handle, _Handle(TRUSTED))
    with pytest.raises(Exception, match="^" + message.replace("(", r"\(").replace(")", r"\)") + "$"):
        await credential_input.checked_fill(_Session(), locator, "s3cret", _check, 5000)
    assert locator.resolved == 1
    assert handle.filled == []
