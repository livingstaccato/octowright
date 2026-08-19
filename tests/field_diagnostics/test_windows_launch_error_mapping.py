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

from octowright.engines import playwright_failure_sanity

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


def test_the_hint_reaches_the_launch_error_a_caller_sees() -> None:
    from octowright.browser_pool.errors import maybe_wrap_playwright_error

    wrapped = maybe_wrap_playwright_error(RuntimeError(WSA), kind="chromium")

    assert "windows_media_stack_missing" in str(wrapped)
    assert "Media Foundation" in str(wrapped)
