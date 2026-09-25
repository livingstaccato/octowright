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
from pathlib import Path
from typing import Any

import pytest

from octowright.artifacts.script_export import render_macro_cli
from octowright.defaults import REDACTED_ASSERTION_TEXT
from octowright.drawn_text import ELEMENT_LIMIT
from octowright.request_failures import ABORTED_REQUEST_FAILURES
from tests._macro_artifact_fixtures import _reload, restore_reloaded_defaults
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


def _refused(page: _FakePage) -> None:
    _fire(page, "requestfailed", types.SimpleNamespace(failure="net::ERR_CONNECTION_REFUSED"))


def test_since_mark_judges_from_the_mark(monkeypatch: pytest.MonkeyPatch) -> None:
    """In one exported script the run is the whole script, so 'run' and 'mark' differ only by the mark."""
    actions = [{"action": "mark_network_clean"}, {"action": "expect_network_clean", "since": "mark"}]
    assert _run(monkeypatch, actions, on_page=_refused)["executed"] == 3


def test_since_mark_without_a_mark_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(BaseException, match="mark_network_clean"):
        _run(monkeypatch, [{"action": "expect_network_clean", "since": "mark"}])


def test_the_export_waits_for_a_request_in_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    def pending_then_fails(page: _FakePage) -> None:
        request = types.SimpleNamespace(resource_type="fetch", failure=None)
        _fire(page, "request", request)

        async def later() -> None:
            await asyncio.sleep(0.15)
            request.failure = "net::ERR_CONNECTION_REFUSED"
            _fire(page, "requestfailed", request)

        asyncio.get_running_loop().create_task(later())

    with pytest.raises(BaseException, match=r"1 failed request\(s\)"):
        _run(monkeypatch, [{"action": "expect_network_clean", "settle_timeout_ms": 2000}], on_page=pending_then_fails)


def test_the_export_never_prints_the_forbidden_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Hard-redacted by action kind, like replay: a non-credential parameter name must not matter."""
    canary = "ACCT-9911-SECRET-CANARY"
    source = render_macro_cli(
        name="m",
        macro={"parameters": ["canary"], "actions": [{"action": "expect_no_text", "text": "{{canary}}"}]},
        include_evidence=False,
    )
    assert "expect_no_text" in source
    try:
        _run_with_args(monkeypatch, source, {"canary": canary})
    except BaseException:
        pass
    assert canary not in capsys.readouterr().out


def _run_with_args(monkeypatch: pytest.MonkeyPatch, source: str, args: dict[str, str]) -> Any:
    rec = _Recorder()

    class _Browser:
        def __init__(self) -> None:
            self.context = _FakeContext(rec)

        async def new_page(self) -> _FakePage:
            return _FakePage(rec, context=self.context)

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
    namespace: dict[str, Any] = {}
    exec(source, namespace)
    return asyncio.run(namespace["run_m"](**args))


def test_a_new_tab_is_watched_during_its_first_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """open_url must watch the tab before goto, or its first load's failures are missed."""
    original_goto = _FakePage.goto

    async def goto(self: _FakePage, url: str) -> None:
        await original_goto(self, url)
        if self.tag.startswith("tab-"):
            _fire(self, "requestfailed", types.SimpleNamespace(failure="net::ERR_CONNECTION_REFUSED"))

    monkeypatch.setattr(_FakePage, "goto", goto)
    actions = [{"action": "open_url", "url": "https://x.test/"}, {"action": "expect_network_clean"}]
    with pytest.raises(BaseException, match=r"1 failed request\(s\)"):
        _run(monkeypatch, actions)


# --- the export's in-flight tracking, driven directly ------------------------------------


def _helpers(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The generated module's namespace, for calling its helpers without a run."""
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    async_api = types.ModuleType("playwright.async_api")
    async_api.async_playwright = lambda: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)
    source = render_macro_cli(name="m", macro={"actions": [{"action": "expect_network_clean"}]}, include_evidence=False)
    namespace: dict[str, Any] = {}
    exec(source, namespace)
    return namespace


class _Page:
    def __init__(self) -> None:
        self.handlers: dict[str, list[Any]] = {}
        self.main_frame = object()

    def on(self, event: str, handler: Any) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def is_closed(self) -> bool:
        return False


def _req(frame: Any, navigation: bool = False) -> Any:
    return types.SimpleNamespace(
        resource_type="document" if navigation else "fetch",
        failure=None,
        frame=frame,
        is_navigation_request=lambda: navigation,
    )


def _watched(monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, Any], dict[str, Any], _Page]:
    ns = _helpers(monkeypatch)
    state: dict[str, Any] = {"watch_network": True, "inflight": {}, "failed_requests": 0, "page_errors": 0}
    state["http_errors"] = 0
    page = _Page()
    ns["_watch_network"](state, page)
    return ns, state, page


def test_the_export_forgets_a_replaced_documents_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chromium never ends a fetch that navigating away cancelled; the commit must."""
    _ns, state, page = _watched(monkeypatch)
    fetch, child_fetch, navigation = _req(page.main_frame), _req(object()), _req(page.main_frame, navigation=True)
    for request in (fetch, child_fetch, navigation):
        _fire(page, "request", request)
    _fire(page, "framenavigated", page.main_frame)
    assert list(state["inflight"]) == [id(navigation)]


def test_the_export_keeps_requests_across_a_same_document_navigation(monkeypatch: pytest.MonkeyPatch) -> None:
    _ns, state, page = _watched(monkeypatch)
    _fire(page, "request", _req(page.main_frame))
    _fire(page, "framenavigated", page.main_frame)
    assert len(state["inflight"]) == 1


def test_the_export_forgets_a_detached_frames_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    _ns, state, page = _watched(monkeypatch)
    child = object()
    _fire(page, "request", _req(child))
    _fire(page, "request", _req(page.main_frame))
    _fire(page, "framedetached", child)
    assert len(state["inflight"]) == 1


def test_the_exports_settle_wait_catches_a_follow_up_inside_the_quiet_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same ordering as the session's: a request starting in the quiet interval is waited for."""
    from tests.test_network_clean_window import FakeClock

    ns, state, page = _watched(monkeypatch)
    clock = FakeClock()
    ns["time"], ns["asyncio"] = clock, clock
    first, follow_up = _req(page.main_frame), _req(page.main_frame)
    _fire(page, "request", first)
    clock.at(0.03, lambda: _fire(page, "requestfinished", first))
    clock.at(0.08, lambda: _fire(page, "request", follow_up))

    def fail() -> None:
        follow_up.failure = "net::ERR_CONNECTION_REFUSED"
        _fire(page, "requestfailed", follow_up)

    clock.at(0.47, fail)
    assert asyncio.run(ns["_settle_network"](state, 2000)) == 0
    assert state["failed_requests"] == 1
    assert 0.47 < clock.elapsed < 0.7


# --- the export refuses and reports what replay refuses and reports -------------------


def test_the_export_refuses_the_redaction_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Searching for the marker itself would pass while the real value is on screen."""

    def leak(page: _FakePage) -> None:
        page.rendered_text = f"your password is {SECRET}"

    with pytest.raises(BaseException, match="redacted"):
        _run(monkeypatch, [{"action": "expect_no_text", "text": REDACTED_ASSERTION_TEXT}], on_page=leak)


def test_the_export_renders_drawn_text_verbatim() -> None:
    """One copy of the collector, comparison, limit, frame rules and messages: replay's own module."""
    import inspect

    from octowright import drawn_text

    source = render_macro_cli(name="m", macro={"actions": []}, include_evidence=False)
    body = inspect.getsource(drawn_text).partition("from __future__ import annotations\n")[2].strip()
    assert body in source
    assert source.count("from __future__ import annotations") == 1
    assert f'"limit": {ELEMENT_LIMIT}' not in source  # the limit is read from the module, not spliced


def test_drawn_text_imports_only_the_standard_library() -> None:
    """It is rendered into a script that has no octowright to import."""
    import ast
    import inspect
    import sys

    from octowright import drawn_text

    tree = ast.parse(inspect.getsource(drawn_text))
    imported = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    }
    imported |= {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    }
    assert imported - {"__future__"} <= set(sys.stdlib_module_names)


def test_the_export_refuses_the_marker_in_replays_words(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.drawn_text import REDACTED_TEXT_REFUSAL

    with pytest.raises(BaseException) as excinfo:
        _run(monkeypatch, [{"action": "expect_no_text", "text": REDACTED_ASSERTION_TEXT}])
    assert str(excinfo.value) == REDACTED_TEXT_REFUSAL


def test_the_export_names_a_leak_in_replays_words(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.drawn_text import leak_message

    def leak(page: _FakePage) -> None:
        page.rendered_text = f"your password is {SECRET}"

    with pytest.raises(RuntimeError) as excinfo:
        _run(monkeypatch, [{"action": "expect_no_text", "text": SECRET, "selector": "#p"}], on_page=leak)
    assert str(excinfo.value) == leak_message(SECRET, "#p", "script scan")


def _scan_returns(monkeypatch: pytest.MonkeyPatch, result: Any) -> None:
    async def evaluate(self: _FakePage, expression: str, *args: Any) -> Any:
        if expression.startswith("({ selector, ownPrefix"):
            return result
        return expression != "() => false"

    monkeypatch.setattr(_FakePage, "evaluate", evaluate)


def test_a_truncated_scan_is_refused_in_the_export(monkeypatch: pytest.MonkeyPatch) -> None:
    _scan_returns(monkeypatch, {"pieces": ["hello"], "matched": 1, "truncated": True})
    with pytest.raises(BaseException, match=str(ELEMENT_LIMIT)):
        _run(monkeypatch, [{"action": "expect_no_text", "text": SECRET}])


def _limit_seen(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    seen: list[Any] = []

    async def evaluate(self: _FakePage, expression: str, *args: Any) -> Any:
        if expression.startswith("({ selector, ownPrefix"):
            seen.append(args[0]["limit"])
            return {"pieces": ["hello"], "matched": 1, "truncated": False}
        return expression != "() => false"

    monkeypatch.setattr(_FakePage, "evaluate", evaluate)
    return seen


def test_the_export_uses_a_steps_element_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _limit_seen(monkeypatch)
    _run(monkeypatch, [{"action": "expect_no_text", "text": SECRET, "element_limit": 123}])
    assert seen == [123]


def test_the_export_reads_the_environment_limit_at_run_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_NO_TEXT_ELEMENT_LIMIT", "77777")
    seen = _limit_seen(monkeypatch)
    _run(monkeypatch, [{"action": "expect_no_text", "text": SECRET}])
    assert seen == [77777]


def test_the_export_names_the_limit_that_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    _scan_returns(monkeypatch, {"pieces": ["hello"], "matched": 1, "truncated": True})
    with pytest.raises(BaseException, match="more than 123 elements"):
        _run(monkeypatch, [{"action": "expect_no_text", "text": SECRET, "element_limit": 123}])


def test_a_main_frame_without_a_result_fails_in_the_export(monkeypatch: pytest.MonkeyPatch) -> None:
    _scan_returns(monkeypatch, None)
    with pytest.raises(BaseException, match="no result"):
        _run(monkeypatch, [{"action": "expect_no_text", "text": SECRET}])


def test_export_macro_cli_refuses_an_unbound_assertion(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Refused at export, naming the step, before a script that could never pass is written."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    try:
        storage.write_macro(
            name="login",
            macro={
                "name": "login",
                "actions": [
                    {"action": "navigate", "url": "https://example.test/"},
                    {"action": "expect_no_text", "text": REDACTED_ASSERTION_TEXT},
                ],
            },
        )
        with pytest.raises(ValueError, match=r"step 1.*redacted") as excinfo:
            macro_artifacts.export_macro_cli(name="login")
        assert "{{parameter}}" in str(excinfo.value)
        assert not list((tmp_path / "recordings").rglob("*.py"))
    finally:
        restore_reloaded_defaults()


def test_the_export_refusal_finds_a_nested_marker_and_says_what_lint_says() -> None:
    """A step inside a branch counts against the top-level step, in lint's own words."""
    from octowright.drawn_text import REDACTED_TEXT_REFUSAL
    from octowright.macros.artifacts import _refuse_unbound_assertions
    from octowright.macros.lint import lint_macro

    marker = {"action": "expect_no_text", "text": REDACTED_ASSERTION_TEXT}
    actions = [
        {"action": "navigate", "url": "https://example.test/"},
        {"action": "if_selector", "selector": "#x", "then": [marker], "else": []},
        {"action": "try", "actions": [{"action": "click", "selector": "#a"}]},
        {"action": "try_each", "branches": [[{"action": "click", "selector": "#b"}], [dict(marker)]]},
        {"action": "expect_no_text", "text": "{{password}}"},
    ]
    with pytest.raises(ValueError) as excinfo:
        _refuse_unbound_assertions("m", {"actions": actions})
    assert str(excinfo.value) == f"macro 'm' cannot be exported at step 1, step 3: {REDACTED_TEXT_REFUSAL}"
    issues = [i for i in lint_macro({"name": "m", "actions": actions}) if i.code == "redacted_assertion_text"]
    assert {i.message for i in issues} == {REDACTED_TEXT_REFUSAL}
    assert sorted(i.action_index for i in issues) == [1, 3]
    _refuse_unbound_assertions("m", {"actions": actions[4:]})  # a bound step is not refused
    _refuse_unbound_assertions("m", {"actions": "not a list"})
