# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Name the Windows image failure instead of reading it as a network fault.

Field report: a Server Core runner failed at ``browser_launch`` with
``WSALookupServiceBegin ... 10091``. Raw, that reads as a transient network
problem and sends the reader off checking DNS and proxies; the actual cause
is that the image lacks OS components Chromium initializes at startup.
"""

from __future__ import annotations

import pytest

from octowright import engines
from octowright.engines import playwright_failure_sanity


@pytest.fixture(autouse=True)
def _on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """These are Windows-image failures; the detector is gated on the host."""
    monkeypatch.setattr(engines, "_running_on_windows", lambda: True)


WSA = "browserType.launch: Browser closed.\n[ERROR:network_change_notifier_win.cc] WSALookupServiceBegin failed with: 10091"
MF_DLL = "browserType.launch: Failed to load mf.dll"
MFPLAT = "LoadLibrary failed for mfplat.dll"


@pytest.mark.parametrize("text", [WSA, MF_DLL, MFPLAT])
def test_windows_image_failures_are_named(text: str) -> None:
    hint = playwright_failure_sanity(text, kind="chromium")

    assert hint is not None
    assert hint["category"] == "windows_media_stack_missing"
    assert "Windows image" in hint["probable_cause"]
    assert any("Media Foundation" in action for action in hint["recommended_actions"])


# The text a Server Core runner actually produces: Playwright reports the launch
# as a closed target and puts the WSALookupServiceBegin line in the browser log.
# Anything matching _detect_target_closed / _detect_sandbox_blocked first would
# claim it and tell the reader to relaunch a browser that cannot start here.
FIELD_TEXT = (
    "browserType.launch: Target page, context or browser has been closed\n"
    "Browser logs:\n"
    "[ERROR:network_change_notifier_win.cc] WSALookupServiceBegin failed with: 10091"
)
FIELD_TEXT_WITH_SANDBOX = f"{FIELD_TEXT}\nsandbox: chrome-sandbox is not configured correctly"


@pytest.mark.parametrize("text", [FIELD_TEXT, FIELD_TEXT_WITH_SANDBOX])
def test_it_wins_over_the_detectors_that_match_the_same_field_text(text: str) -> None:
    """Pins the ordering the comment in engines.py asserts. Without this, moving
    the detector back next to its generic sibling passes the whole suite while
    restoring the exact misdiagnosis this change exists to fix."""
    hint = playwright_failure_sanity(text, kind="chromium")

    assert hint is not None
    assert hint["category"] == "windows_media_stack_missing"


def test_the_masking_detectors_still_claim_their_own_text() -> None:
    """Ordering first must not steal failures that are genuinely those cases."""
    closed = playwright_failure_sanity("Target page, context or browser has been closed", kind="chromium")
    sandboxed = playwright_failure_sanity(
        "browserType.launch: Target page, context or browser has been closed\nsandbox error", kind="chromium"
    )

    assert closed is not None and closed["category"] == "playwright_target_closed"
    assert sandboxed is not None and sandboxed["category"] == "playwright_sandbox_blocked"


def test_it_wins_over_the_generic_network_detector() -> None:
    """The regression risk: the Windows text can carry net:: noise, and
    "verify URL, DNS, proxy" is actively misleading for a missing OS feature."""
    mixed = f"{WSA}\nnet::ERR_NAME_NOT_RESOLVED"

    hint = playwright_failure_sanity(mixed, kind="chromium")

    assert hint is not None
    assert hint["category"] == "windows_media_stack_missing"


def test_ordinary_network_failures_are_untouched() -> None:
    hint = playwright_failure_sanity("net::ERR_CONNECTION_REFUSED", kind="chromium")

    assert hint is not None
    assert hint["category"] == "playwright_network_unreachable"


def test_matching_is_case_insensitive_but_still_specific() -> None:
    assert playwright_failure_sanity("wsalookupservicebegin failed", kind="chromium") is not None
    # A bare error code is not enough -- it would fire on unrelated numbers.
    assert playwright_failure_sanity("exit code 10091", kind="chromium") is None


def test_off_windows_the_generic_detector_keeps_the_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ordering it ahead of the generic detectors is only safe because it does
    not fire off-Windows: the remedy is Windows-only, and claiming a Linux
    failure would suppress the correct sandbox/target-closed diagnosis."""
    monkeypatch.setattr(engines, "_running_on_windows", lambda: False)

    hint = playwright_failure_sanity(FIELD_TEXT, kind="chromium")

    assert hint is not None
    assert hint["category"] == "playwright_target_closed"


def test_the_hint_reaches_the_launch_error_a_caller_sees() -> None:
    from octowright.browser_pool.errors import maybe_wrap_playwright_error

    wrapped = maybe_wrap_playwright_error(RuntimeError(WSA), kind="chromium")

    assert "windows_media_stack_missing" in str(wrapped)
    assert "Media Foundation" in str(wrapped)
