# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The exported CLI's ``expect_network_clean`` / ``expect_no_text`` mean what replay means.

Same window (the run), same abort list, same refusal to repeat the forbidden
text. Driven through the generated script against the recording fake page.
"""

from __future__ import annotations

import asyncio
import sys
import types
from typing import Any

import pytest

from octowright.artifacts.script_export import render_macro_cli
from octowright.request_failures import ABORTED_REQUEST_FAILURES
from tests.macro_lint.test_cli_export_execution import _FakeContext, _FakePage, _Recorder

SECRET = "hunter2-Correct-Horse!"  # pragma: allowlist secret -- a fixture, never a real credential


def _run(monkeypatch: pytest.MonkeyPatch, actions: list[dict[str, Any]], on_page: Any = None) -> Any:
    """Run the generated CLI; *on_page* is called with the page once listeners are wired."""
    rec = _Recorder()
    page_holder: list[_FakePage] = []

    class _Browser:
        def __init__(self) -> None:
            self.context = _FakeContext(rec)

        async def new_page(self) -> _FakePage:
            page = _FakePage(rec, context=self.context)
            page_holder.append(page)
            return page

        async def close(self) -> None:
            return None

    class _Chromium:
        async def launch(self, *, headless: bool) -> _Browser:
            return _Browser()

    class _Ctx:
        async def __aenter__(self) -> Any:
            return types.SimpleNamespace(chromium=_Chromium())

        async def __aexit__(self, *_a: Any) -> None:
            return None

    async_api = types.ModuleType("playwright.async_api")
    async_api.async_playwright = lambda: _Ctx()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)

    hook = [{"action": "evaluate", "expression": "__hook__"}]
    source = render_macro_cli(name="m", macro={"actions": hook + actions}, include_evidence=False)
    namespace: dict[str, Any] = {}
    exec(source, namespace)

    original = _FakePage.evaluate

    async def evaluate(self: _FakePage, expression: str, *args: Any) -> Any:
        if expression == "__hook__" and on_page is not None:
            on_page(self)
        return await original(self, expression, *args)

    monkeypatch.setattr(_FakePage, "evaluate", evaluate)
    return asyncio.run(namespace["run_m"]())


def _fire(page: _FakePage, event: str, payload: Any) -> None:
    for handler in page.handlers.get(event, []):
        handler(payload)


def test_clean_run_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run(monkeypatch, [{"action": "expect_network_clean"}])["executed"] == 2


def test_failed_request_and_page_error_fail_with_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    def dirty(page: _FakePage) -> None:
        _fire(page, "requestfailed", types.SimpleNamespace(failure="net::ERR_CONNECTION_REFUSED"))
        _fire(page, "pageerror", Exception(SECRET))

    with pytest.raises(BaseException) as excinfo:
        _run(monkeypatch, [{"action": "expect_network_clean"}], on_page=dirty)
    assert "1 failed request(s), 1 page error(s)" in str(excinfo.value)
    assert SECRET not in str(excinfo.value)


@pytest.mark.parametrize("aborted", sorted(ABORTED_REQUEST_FAILURES))
def test_aborts_are_not_failures(monkeypatch: pytest.MonkeyPatch, aborted: str) -> None:
    def abort(page: _FakePage) -> None:
        _fire(page, "requestfailed", types.SimpleNamespace(failure=aborted))

    assert _run(monkeypatch, [{"action": "expect_network_clean"}], on_page=abort)["executed"] == 2


def test_no_text_fails_without_repeating_it(monkeypatch: pytest.MonkeyPatch) -> None:
    def leak(page: _FakePage) -> None:
        page.rendered_text = f"your password is {SECRET}"

    with pytest.raises(BaseException) as excinfo:
        _run(monkeypatch, [{"action": "expect_no_text", "text": SECRET}], on_page=leak)
    assert "forbidden text" in str(excinfo.value)
    assert SECRET not in str(excinfo.value)


def test_no_text_passes_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run(monkeypatch, [{"action": "expect_no_text", "text": SECRET}])["executed"] == 2


def test_a_macro_without_the_assertion_never_touches_page_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same convention as the dialog policy: the common case stays a straight-line driver."""
    seen: list[str] = []
    monkeypatch.setattr(_FakePage, "on", lambda self, event, handler: seen.append(event))
    _run(monkeypatch, [{"action": "expect_no_text", "text": SECRET}])
    assert seen == []


def test_a_nested_assertion_still_wires_the_listeners(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(_FakePage, "on", lambda self, event, handler: seen.append(event))
    nested = {"action": "try", "actions": [{"action": "expect_network_clean"}]}
    try:
        _run(monkeypatch, [nested])
    except BaseException:
        pass  # whether `try` exports is not this test's question
    assert {"requestfailed", "pageerror"} <= set(seen)


def _server_error(page: _FakePage) -> None:
    request = types.SimpleNamespace(resource_type="fetch")
    _fire(page, "response", types.SimpleNamespace(status=500, request=request))
    _fire(page, "response", types.SimpleNamespace(status=404, request=types.SimpleNamespace(resource_type="image")))


def test_http_errors_are_off_by_default_in_the_export(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run(monkeypatch, [{"action": "expect_network_clean"}], on_page=_server_error)["executed"] == 2


def test_http_errors_opt_in_counts_only_api_and_page_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(BaseException) as excinfo:
        _run(monkeypatch, [{"action": "expect_network_clean", "http_errors": True}], on_page=_server_error)
    assert "1 HTTP error(s)" in str(excinfo.value)
