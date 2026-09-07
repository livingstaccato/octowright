# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Choosing which page a persistent launch should drive.

``launch_persistent_context`` can hand back a context that already holds
pages, and octowright then navigates the one it picked to the caller's target
URL. Taking ``context.pages[0]`` assumed that page was octowright's own. With
Chromium session restore it is not, and the order is a **race**: two runs of
the same probe returned

    [about:blank, tab0, tab2, tab1]
    [tab2, about:blank, tab0, tab1]

so on the second one octowright would have navigated a restored tab, silently
destroying its content. Suppressing the restore prompt (see
``test_restore_prompt.py``) makes this unreachable by default; this is what
keeps the documented opt-out from being a footgun.

The rule deliberately leaves the ordinary single-page handback byte-identical:
only a context that hands back MORE than one page takes a different path, and
even then nothing is closed or navigated over -- a context with no blank page
to spare gets a new one.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.browser_pool.launch_helpers import select_launch_page


def _page(url: str) -> MagicMock:
    page = MagicMock()
    page.url = url
    return page


def _context(*pages: Any) -> MagicMock:
    context = MagicMock()
    context.pages = list(pages)
    context.new_page = AsyncMock(return_value=_page("about:blank"))
    return context


@pytest.mark.anyio
async def test_an_empty_context_gets_a_new_page() -> None:
    context = _context()

    page = await select_launch_page(context)

    context.new_page.assert_awaited_once()
    assert page is context.new_page.return_value


@pytest.mark.anyio
async def test_a_single_page_is_used_whatever_its_url() -> None:
    """The ordinary launch. Chromium's initial page is not always about:blank
    (the new-tab override extension replaces it), so a blankness test applied
    here would spawn a spurious second page on every single launch."""
    only = _page("chrome://new-tab-page")
    context = _context(only)

    page = await select_launch_page(context)

    assert page is only
    context.new_page.assert_not_awaited()


@pytest.mark.anyio
async def test_a_blank_page_is_preferred_over_a_restored_tab() -> None:
    restored = _page("https://example.test/tab2")
    blank = _page("about:blank")
    context = _context(restored, blank, _page("https://example.test/tab0"))

    page = await select_launch_page(context)

    assert page is blank
    context.new_page.assert_not_awaited()


@pytest.mark.anyio
async def test_the_blank_page_is_found_wherever_it_sits() -> None:
    """The bug is positional: the fix must not be."""
    blank = _page("about:blank")
    context = _context(_page("https://example.test/a"), _page("https://example.test/b"), blank)

    assert await select_launch_page(context) is blank


@pytest.mark.anyio
async def test_an_empty_url_counts_as_blank() -> None:
    """A page that has not committed a navigation reports "" rather than about:blank."""
    blank = _page("")
    context = _context(_page("https://example.test/a"), blank)

    assert await select_launch_page(context) is blank


@pytest.mark.anyio
async def test_with_nothing_blank_a_new_page_is_opened_rather_than_clobbering_one() -> None:
    """Every page holds content someone wants; navigating any of them loses it."""
    context = _context(_page("https://example.test/a"), _page("https://example.test/b"))

    page = await select_launch_page(context)

    context.new_page.assert_awaited_once()
    assert page is context.new_page.return_value


@pytest.mark.anyio
async def test_a_page_whose_url_cannot_be_read_is_not_treated_as_blank() -> None:
    """A page closing under us must not become the one we navigate."""
    broken = MagicMock()
    type(broken).url = property(lambda self: (_ for _ in ()).throw(RuntimeError("closed")))
    blank = _page("about:blank")
    context = _context(broken, blank)

    assert await select_launch_page(context) is blank
