# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Page-level extra HTTP headers are remembered per page.

``browser_set_extra_http_headers`` is per page in Playwright, but the session
kept one slot: headers set on page A, then on page B after a ``page_switch``,
left A still sending its headers with no record of them anywhere --
``header_state`` reported nothing for A once switched back, and a
replacement neither carried nor named them. Each page's map is now kept.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from octowright.session.page_headers import page_headers_on, set_page_headers
from octowright.session.route_carry import RouteCarry, page_routes_of, rebind_page_routes
from tests.test_header_visibility import header_session  # noqa: F401  (fixture)


class _Page:
    def __init__(self, closed: bool = False) -> None:
        self.closed = closed

    def is_closed(self) -> bool:
        return self.closed


async def test_each_page_keeps_its_own_report(header_session: Any) -> None:  # noqa: F811
    first = header_session.page
    await header_session.set_extra_http_headers({"X-A": "1"})
    second = type(first)()
    header_session.page = second  # what page_switch does
    await header_session.set_extra_http_headers({"X-B": "2"})

    assert header_session.header_state()["page"] == {"X-B": "2"}
    header_session.page = first
    assert header_session.header_state()["page"] == {"X-A": "1"}


async def test_clearing_one_page_leaves_the_other(header_session: Any) -> None:  # noqa: F811
    first = header_session.page
    await header_session.set_extra_http_headers({"X-A": "1"})
    header_session.page = type(first)()
    await header_session.set_extra_http_headers({"X-B": "2"})
    await header_session.set_extra_http_headers({})

    assert "page" not in header_session.header_state()
    header_session.page = first
    assert header_session.header_state()["page"] == {"X-A": "1"}


def _session(active: Any) -> SimpleNamespace:
    return SimpleNamespace(page=active, _injected_headers={}, _active_routes={}, _mock_specs={})


def test_a_replacement_carries_the_active_pages_headers_and_names_the_others() -> None:
    first, second = _Page(), _Page()
    session = _session(second)
    set_page_headers(session, first, {"X-A": "1"})
    set_page_headers(session, second, {"X-B": "2"})

    carry = RouteCarry.of(session)

    assert carry.page_headers == {"X-B": "2"}
    assert len(carry.not_carried) == 1 and "page-level" in carry.not_carried[0]
    assert "X-A" not in carry.not_carried[0] and "1" not in carry.not_carried[0]


def test_a_closed_pages_headers_are_not_reported_as_lost() -> None:
    gone, active = _Page(closed=True), _Page()
    session = _session(active)
    set_page_headers(session, gone, {"X-A": "1"})

    assert RouteCarry.of(session).not_carried == ()


def test_crash_recovery_restores_the_dead_pages_own_headers_and_moves_them() -> None:
    first, second, replacement = _Page(), _Page(), _Page()
    session = _session(second)
    set_page_headers(session, first, {"X-A": "1"})
    set_page_headers(session, second, {"X-B": "2"})

    routes = page_routes_of(session, first)
    assert routes.page_headers == {"X-A": "1"}

    rebind_page_routes(session, routes, replacement)
    assert page_headers_on(session, replacement) == {"X-A": "1"}
    assert page_headers_on(session, first) is None
    assert page_headers_on(session, second) == {"X-B": "2"}


@pytest.mark.parametrize("headers", [{}, {"X-A": "1"}])
def test_setting_replaces_rather_than_adds(headers: dict[str, str]) -> None:
    page = _Page()
    session = _session(page)
    set_page_headers(session, page, {"X-Old": "0"})
    set_page_headers(session, page, headers)

    assert page_headers_on(session, page) == (headers or None)
