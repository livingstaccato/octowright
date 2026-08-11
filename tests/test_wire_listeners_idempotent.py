# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``_wire_listeners`` must be idempotent per page.

Crash recovery calls ``context.new_page()`` — which fires the context "page"
event and runs ``_register_popup`` (itself calling ``_wire_listeners``) — and
then calls ``_wire_listeners`` again explicitly. Without an identity guard the
replacement page ends up with duplicate dialog/download/response/websocket/…
handlers, so every such event fires twice.
"""

from __future__ import annotations

from types import SimpleNamespace


class _FakePage:
    """A weakref-able stand-in for a Playwright Page (SimpleNamespace is not
    weakly referenceable, but real Page objects are — matching the WeakSet guard)."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def on(self, event: str, handler: object) -> None:
        self.events.append(event)


def _fake_session() -> SimpleNamespace:
    return SimpleNamespace(
        _handle_dialog=lambda *a: None,
        _handle_download=lambda *a: None,
        _handle_response=lambda *a: None,
        _handle_request_failed=lambda *a: None,
        _handle_websocket=lambda *a: None,
        _schedule_markdown_capture=lambda **k: None,
    )


def test_wire_listeners_is_idempotent_per_page() -> None:
    from octowright.browser_pool.listeners import _wire_listeners

    session = _fake_session()
    page = _FakePage()

    _wire_listeners(session, page)
    first = list(page.events)
    _wire_listeners(session, page)  # same page again — must add nothing

    assert page.events == first
    assert {"dialog", "download", "response", "websocket"} <= set(first)


def test_wire_listeners_still_wires_a_different_page() -> None:
    from octowright.browser_pool.listeners import _wire_listeners

    session = _fake_session()
    page_a = _FakePage()
    page_b = _FakePage()

    _wire_listeners(session, page_a)
    _wire_listeners(session, page_b)  # distinct page — the guard must not suppress it

    assert page_b.events == page_a.events
    assert page_b.events  # not empty
