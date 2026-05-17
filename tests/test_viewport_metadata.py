# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from octowright.browser_pool.launch_helpers import _build_viewport_kwargs
from octowright.browser_pool.viewport import ViewportMode


def test_headed_without_explicit_viewport_is_fluid() -> None:
    kwargs, log_viewport, explicit_size, viewport = _build_viewport_kwargs(
        headless=False,
        viewport_w=None,
        viewport_h=None,
    )

    assert kwargs == {"no_viewport": True}
    assert log_viewport == {"mode": "fluid"}
    assert explicit_size is False
    assert viewport.mode == ViewportMode.FLUID
    assert viewport.width is None
    assert viewport.height is None


def test_headless_without_explicit_viewport_is_fixed_default() -> None:
    kwargs, log_viewport, explicit_size, viewport = _build_viewport_kwargs(
        headless=True,
        viewport_w=None,
        viewport_h=None,
    )

    assert kwargs == {"viewport": {"width": 1280, "height": 800}}
    assert log_viewport == {"mode": "fixed", "w": 1280, "h": 800}
    assert explicit_size is False
    assert viewport.mode == ViewportMode.FIXED
    assert viewport.width == 1280
    assert viewport.height == 800


def test_explicit_viewport_is_fixed_even_when_headed() -> None:
    kwargs, log_viewport, explicit_size, viewport = _build_viewport_kwargs(
        headless=False,
        viewport_w=1440,
        viewport_h=900,
    )

    assert kwargs == {"viewport": {"width": 1440, "height": 900}}
    assert log_viewport == {"mode": "fixed", "w": 1440, "h": 900}
    assert explicit_size is True
    assert viewport.mode == ViewportMode.FIXED
    assert viewport.width == 1440
    assert viewport.height == 900
