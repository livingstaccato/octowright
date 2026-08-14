# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import pytest

from octowright.macros import checks as macro_checks
from tests._operation_gate_fakes import OperationAwareFake


class _Elem:
    def __init__(self, text: str) -> None:
        self._text = text

    async def inner_text(self) -> str:
        return self._text


class _Page:
    def __init__(self, url: str = "https://example.test/path") -> None:
        self.url = url
        self.selector_map: dict[str, _Elem | None] = {}
        self.eval_result: object = True

    async def wait_for_selector(self, selector: str, timeout: int = 0):
        return self.selector_map.get(selector)

    async def query_selector(self, selector: str):
        return self.selector_map.get(selector)

    async def evaluate(self, expression: str):
        return self.eval_result


class _Session(OperationAwareFake):
    """Wraps a bare ``_Page`` double behind the ``SessionLike`` surface
    ``macros.checks`` now requires: ``.page`` for ``_check_url`` and
    ``._target()`` for the element/JS checks."""

    def __init__(self, page: _Page) -> None:
        super().__init__()
        self.page = page

    def _target(self) -> _Page:
        return self.page


@pytest.mark.anyio
async def test_check_url_modes_and_errors() -> None:
    page = _Page(url="https://example.test/path?q=1")
    session = _Session(page)
    assert await macro_checks._check_url(session, "https://example.test/path?q=1", mode="equals") == page.url
    assert await macro_checks._check_url(session, "example.test/path", mode="contains") == page.url
    assert await macro_checks._check_url(session, r"example\.test/path", mode="regex") == page.url

    with pytest.raises(RuntimeError):
        await macro_checks._check_url(session, "https://other", mode="equals")
    with pytest.raises(RuntimeError):
        await macro_checks._check_url(session, "other", mode="contains")
    with pytest.raises(RuntimeError):
        await macro_checks._check_url(session, r"^nomatch$", mode="regex")
    with pytest.raises(ValueError):
        await macro_checks._check_url(session, "x", mode="unknown")


@pytest.mark.anyio
async def test_check_text_modes_and_errors() -> None:
    page = _Page()
    page.selector_map["#ok"] = _Elem("hello world")
    session = _Session(page)
    assert await macro_checks._check_text(session, "#ok", "hello", mode="contains") == "hello world"
    assert await macro_checks._check_text(session, "#ok", "hello world", mode="equals") == "hello world"
    assert await macro_checks._check_text(session, "#ok", r"hello\s+world", mode="regex") == "hello world"

    with pytest.raises(RuntimeError):
        await macro_checks._check_text(session, "#missing", "x")
    with pytest.raises(RuntimeError):
        await macro_checks._check_text(session, "#ok", "zzz", mode="contains")
    with pytest.raises(RuntimeError):
        await macro_checks._check_text(session, "#ok", "zzz", mode="equals")
    with pytest.raises(RuntimeError):
        await macro_checks._check_text(session, "#ok", r"^zzz$", mode="regex")
    with pytest.raises(ValueError):
        await macro_checks._check_text(session, "#ok", "x", mode="bad")


@pytest.mark.anyio
async def test_check_selector_present_and_absent() -> None:
    page = _Page()
    page.selector_map["#present"] = _Elem("x")
    session = _Session(page)

    await macro_checks._check_selector(session, "#present", present=True)
    await macro_checks._check_selector(session, "#absent", present=False)

    with pytest.raises(RuntimeError):
        await macro_checks._check_selector(session, "#present", present=False)


@pytest.mark.anyio
async def test_check_js_truthy_and_equals() -> None:
    page = _Page()
    session = _Session(page)
    page.eval_result = {"ok": 1}
    assert await macro_checks._check_js(session, "1") == {"ok": 1}

    page.eval_result = 12
    assert await macro_checks._check_js(session, "x", equals=12) == 12

    with pytest.raises(RuntimeError):
        await macro_checks._check_js(session, "x", equals=13)

    page.eval_result = ""
    with pytest.raises(RuntimeError):
        await macro_checks._check_js(session, "x")
