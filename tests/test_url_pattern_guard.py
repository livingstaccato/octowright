# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A route glob is compiled into a backtracking regex, so it is bounded.

Playwright turns a URL glob into a regex before matching every intercepted
request: ``**a**a**b`` becomes ``^(.*)a(.*)a(.*)b$``. That shape has no nested
quantifier, so it is polynomial rather than exponential -- but the exponent is
one per wildcard and the caller picks it. Measured against a 129-character URL:

    k=2   0.0014s
    k=3   0.0394s
    k=4   0.9478s
    k=5  18.0107s          <- an EIGHTEEN character pattern

Three things make that worse than a slow call. The match runs **in the Node
driver**, which is shared by every session in the pool, so a hostile pattern
installed on one browser stalls navigations in all of them. The cost is paid
**per intercepted request**, so an ordinary page multiplies it by its
subresource count. And Playwright's own ``timeout=`` is enforced in that same
wedged driver, so it cannot fire.

``session/timeouts.bounded`` cannot help either: the stall is not in an awaited
Python call, and the leader's event loop keeps running normally. Refusing the
pattern in Python is sufficient precisely because the wedge is driver-side -- a
pattern octowright never forwards is never compiled by anyone.
"""

from __future__ import annotations

import re
import time

import pytest

from octowright.url_patterns import (
    MAX_URL_PATTERN_CHARS,
    MAX_URL_PATTERN_WILDCARDS,
    validate_url_pattern,
)


@pytest.mark.parametrize(
    "pattern",
    [
        "**/api/**/users",
        "https://example.test/*",
        "**/*.png",
        "*",
        "https://example.test/orders/12345",
    ],
)
def test_ordinary_patterns_are_accepted(pattern: str) -> None:
    """The cap has to be generous enough that real globs never meet it.

    A guard that rejects ``**/api/**/users`` would be worse than the problem.
    Accept-side assertions are the half that pins the boundary: a test that
    only proves a hostile pattern is refused passes just as happily against a
    cap of zero.
    """
    validate_url_pattern(pattern, field="url_pattern")


def test_the_measured_attack_pattern_is_refused() -> None:
    """The exact shape that took 18 seconds, as a regression."""
    with pytest.raises(ValueError, match="wildcard"):
        validate_url_pattern("**a" * 6 + "**b", field="url_pattern")


def test_wildcard_runs_are_counted_not_double_stars() -> None:
    """``*`` and ``**`` each contribute one group, so both must count.

    Playwright's converter emits ``([^/]*)`` for ``*`` and ``(.*)`` for ``**``.
    Counting only ``**`` would leave ``*a*a*a*a*a*a*b`` -- same backtracking
    shape, same blow-up -- entirely unguarded.
    """
    with pytest.raises(ValueError, match="wildcard"):
        validate_url_pattern("*a" * 6 + "*b", field="url_pattern")


def test_an_overlong_pattern_is_refused() -> None:
    """A length cap as well, since a long pattern costs per request too."""
    with pytest.raises(ValueError, match="chars"):
        validate_url_pattern("x" * (MAX_URL_PATTERN_CHARS + 1), field="url_pattern")


def test_the_error_names_the_field_it_came_from() -> None:
    """One validator serves mock_route, header injection and scenario YAML.

    Without the field name the message cannot say which of them to fix, and a
    scenario author reading "url_pattern" would go looking in the wrong file.
    """
    with pytest.raises(ValueError, match="extra_http_headers_urls"):
        validate_url_pattern("**a" * 9, field="extra_http_headers_urls")


def test_the_cap_is_not_raised_past_the_measured_safe_value() -> None:
    """Checked as a constant, deliberately, so a bad edit fails INSTANTLY.

    The obvious test -- time the worst pattern the cap still allows -- cannot
    police the upper side, because the thing it would catch is the thing that
    makes it hang: at 6 wildcards the same match takes roughly 500 seconds, so
    a test that measured it would blow the suite's 300s per-test timeout and
    kill the whole run rather than report a failure. (Observed while verifying
    this guard: raising the cap to 7 wedged the check until it was killed.)

    An assertion on the number costs nothing and fails in microseconds.
    """
    assert MAX_URL_PATTERN_WILDCARDS <= 5


def test_the_cap_actually_bounds_the_match_cost() -> None:
    """And the accepted worst case really is fast, against the real converter.

    The bound is generous rather than tight: 4 wildcards against this URL
    measured ~0.95s locally, so asserting anything near that would flake on a
    loaded CI box. It still separates "under a second-ish" from the 18s the
    next wildcard costs, which is the distinction that matters.
    """
    pytest.importorskip("playwright")
    from playwright._impl._glob import glob_to_regex_pattern

    worst_allowed = "**a" * (MAX_URL_PATTERN_WILDCARDS - 1) + "**b"
    validate_url_pattern(worst_allowed, field="url_pattern")

    compiled = re.compile(glob_to_regex_pattern(worst_allowed))
    url = "http://a.test/" + "a" * 115
    start = time.perf_counter()
    compiled.search(url)
    assert time.perf_counter() - start < 5.0


# ---------------------------------------------------------------------------
# Call sites, not just the helper.
#
# The first mutation-survivor batch established that this suite tests helpers
# thoroughly and their call sites not at all. A validator nobody calls is
# exactly that failure with a security label on it, so each install path is
# asserted through its own entry point.
# ---------------------------------------------------------------------------


def test_launch_scoped_header_globs_are_bounded_too() -> None:
    """``extra_http_headers_urls`` capped LENGTH but not wildcards.

    The measured attack is eighteen characters, so a 2048-char cap let it
    straight through -- and these globs become context routes exactly like
    ``inject_headers``. Same compile, same driver, same stall.
    """
    from octowright.http_headers import validate_extra_http_header_urls

    validate_extra_http_header_urls(["**/api/**"])
    with pytest.raises(ValueError, match="wildcard"):
        validate_extra_http_header_urls(["**a" * 7 + "**b"])


@pytest.mark.anyio
async def test_mock_route_refuses_a_hostile_pattern_before_reaching_playwright() -> None:
    """The refusal must happen in Python, ahead of ``page.route``.

    That ordering is the whole mitigation: the stall is inside the Node
    driver, which the pool shares across every session, and Playwright's own
    ``timeout=`` is enforced in that same wedged driver so it cannot end it.
    A pattern octowright never forwards is never compiled by anyone -- so this
    asserts the page was not touched at all, not merely that an error was
    raised.
    """
    from unittest.mock import AsyncMock, MagicMock

    from octowright.session.core_interaction_mixin import SessionInteractionMixin

    session = MagicMock(spec=SessionInteractionMixin)
    session.page = MagicMock()
    session.page.route = AsyncMock()
    session.page.unroute = AsyncMock()
    # Both registries stubbed so that WITHOUT the guard this call proceeds all
    # the way to `page.route` and fails on the assertion below. Leaving one out
    # made it die on a missing attribute instead -- still red, but red for a
    # reason that says nothing about whether the pattern was forwarded.
    session._active_routes = {}
    session._header_routes = {}
    session.recorder = MagicMock()
    session.instance_id = "b1"

    with pytest.raises(ValueError, match="wildcard"):
        await SessionInteractionMixin.mock_route(session, "**a" * 8 + "**b", status=200)

    session.page.route.assert_not_awaited()
