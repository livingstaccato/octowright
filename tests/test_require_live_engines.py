# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``OCTOWRIGHT_REQUIRE_LIVE_ENGINES`` turns an engine-unavailable skip into a failure.

Every live fixture catches a launch exception and skips, so on a runner where an
engine regressed the engine-measured tests pass green as skips. The switch is
for a runner that installed the engines and therefore expects them to launch.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tests._live_engines import engine_skip_failure, required_engines


def _skipped_while_handling(error: Exception, reason: str = "chromium unavailable") -> BaseException:
    try:
        try:
            raise error
        except Exception:
            pytest.skip(reason)
    except BaseException as skipped:  # pytest's Skipped is an OutcomeException, not an Exception
        return skipped
    raise AssertionError("pytest.skip did not raise")


def _plain_skip(reason: str) -> BaseException:
    try:
        pytest.skip(reason)
    except BaseException as skipped:
        return skipped
    raise AssertionError("pytest.skip did not raise")


def _item(nodeid: str = "tests/test_x_live.py::test_y[chromium]", *, live: bool = True, params: Any = None) -> Any:
    markers = {"live_browser": object()} if live else {}
    item = SimpleNamespace(nodeid=nodeid, get_closest_marker=markers.get)
    if params is not None:
        item.callspec = SimpleNamespace(params=params)
    return item


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, frozenset()),
        ("", frozenset()),
        ("0", frozenset()),
        ("off", frozenset()),
        ("1", frozenset({"chromium", "firefox", "webkit"})),
        ("all", frozenset({"chromium", "firefox", "webkit"})),
        ("chromium, webkit", frozenset({"chromium", "webkit"})),
    ],
)
def test_the_switch_names_the_engines_it_requires(raw: str | None, expected: frozenset[str]) -> None:
    assert required_engines(raw) == expected


def test_an_unknown_engine_name_is_refused_rather_than_ignored() -> None:
    """A typo would otherwise silently require nothing."""
    with pytest.raises(pytest.UsageError, match="chromiumm"):
        required_engines("chromiumm")


def test_a_launch_failure_turned_into_a_skip_fails_when_required() -> None:
    skipped = _skipped_while_handling(RuntimeError("Executable doesn't exist at /ms-playwright/chromium"))
    message = engine_skip_failure(_item(), skipped, frozenset({"chromium"}))
    assert message is not None
    assert "OCTOWRIGHT_REQUIRE_LIVE_ENGINES" in message
    assert "Executable doesn't exist" in message


def test_it_still_skips_when_nothing_is_required() -> None:
    skipped = _skipped_while_handling(RuntimeError("boom"))
    assert engine_skip_failure(_item(), skipped, frozenset()) is None


def test_only_the_required_engines_fail() -> None:
    skipped = _skipped_while_handling(RuntimeError("boom"))
    webkit = _item("tests/test_x_live.py::test_y[webkit]", params={"session": "webkit"})
    assert engine_skip_failure(webkit, skipped, frozenset({"chromium"})) is None
    assert engine_skip_failure(webkit, skipped, frozenset({"webkit"})) is not None


def test_an_unparametrized_live_test_launches_chromium() -> None:
    skipped = _skipped_while_handling(RuntimeError("boom"))
    item = _item("tests/test_x_live.py::test_y")
    assert engine_skip_failure(item, skipped, frozenset({"chromium"})) is not None
    assert engine_skip_failure(item, skipped, frozenset({"firefox"})) is None


def test_a_skip_that_is_not_about_a_launch_stays_a_skip() -> None:
    """A test that skips on purpose (a Chromium-only surface) is not an engine failure."""
    skipped = _plain_skip("closed shadow roots are only reachable through Chromium's DOM snapshot")
    assert engine_skip_failure(_item(), skipped, frozenset({"chromium"})) is None


def test_a_daemon_reporting_no_usable_engine_fails_when_required() -> None:
    """The daemon-driven tests read the launch failure from a result, not an exception."""
    skipped = _plain_skip("no usable browser engine: {'error': 'Executable doesn't exist'}")
    assert engine_skip_failure(_item(), skipped, frozenset({"chromium"})) is not None


def test_a_non_live_test_is_left_alone() -> None:
    skipped = _skipped_while_handling(RuntimeError("boom"))
    assert engine_skip_failure(_item(live=False), skipped, frozenset({"chromium"})) is None
