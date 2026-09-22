# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Every exported dispatch branch is EXECUTED, not just compiled.

``script_export_actions`` holds the branch bodies as string literals, so
coverage reports the module at 100% while none of those strings have run — and
``test_cli_export_coverage`` only proves each branch exists and that the whole
script compiles. That is enough to catch a missing branch and nothing else: a
branch calling a method Playwright does not have, or splatting a field under
the wrong name, still ships green.

So this runs the generated script end to end against a recording fake, once
with a macro containing every kind in ``_ACTION_MAP``, and asserts on the calls
it made. It is the test that would have caught ``mock_route``'s ``pattern`` vs
``url_pattern`` split, or ``drag``'s ``source`` vs ``source_selector``.
"""

from __future__ import annotations

import asyncio
import re
import sys
import types
from typing import Any

import pytest

from octowright.artifacts.script_export import render_macro_cli
from octowright.defaults import DEFAULT_ACTION_TIMEOUT_MS
from octowright.macros.runtime import _ACTION_MAP


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    def names(self) -> list[str]:
        return [name for name, _a, _kw in self.calls]

    def kwargs_for(self, name: str) -> dict[str, Any]:
        return next(kw for n, _a, kw in self.calls if n == name)

    def args_for(self, name: str) -> tuple[Any, ...]:
        return next(a for n, a, _kw in self.calls if n == name)


class _FakeLocator:
    def __init__(self, rec: _Recorder, label: str, owner: str | None = None) -> None:
        self._rec, self._label, self._owner = rec, label, owner

    async def click(self, **kw: Any) -> None:
        self._rec.record(f"locator.click:{self._label}", **kw)
        if self._owner is not None:
            self._rec.record(f"locator.click:{self._label}:{self._owner}", **kw)

    async def fill(self, value: str, **kw: Any) -> None:
        self._rec.record(f"locator.fill:{self._label}", value, **kw)

    async def inner_text(self) -> str:
        self._rec.record(f"locator.inner_text:{self._label}")
        return "read"

    async def focus(self) -> None:
        self._rec.record(f"locator.focus:{self._label}")

    async def evaluate(self, expression: str, *args: Any) -> Any:
        """a11y_dragdrop's grab predicate. ``"() => false"`` is a deliberate
        sentinel other expressions never carry, letting a test force a False
        result without a real JS engine."""
        self._rec.record(f"locator.evaluate:{self._label}", expression)
        return expression != "() => false"

    async def count(self) -> int:
        self._rec.record(f"locator.count:{self._label}")
        return 1


class _FakeFileChooser:
    def __init__(self, rec: _Recorder) -> None:
        self._rec = rec

    async def set_files(self, paths: list[str], **kw: Any) -> None:
        self._rec.record("file_chooser.set_files", paths, **kw)


class _FakeFileChooserInfo:
    def __init__(self, rec: _Recorder) -> None:
        self._rec = rec

    @property
    def value(self) -> Any:
        async def resolve() -> _FakeFileChooser:
            self._rec.record("file_chooser.await")
            return _FakeFileChooser(self._rec)

        return resolve()


class _FakeFileChooserContext:
    def __init__(self, rec: _Recorder, timeout: int | None, owner: str) -> None:
        self._rec, self._timeout, self._owner = rec, timeout, owner

    async def __aenter__(self) -> _FakeFileChooserInfo:
        self._rec.record("file_chooser.arm", timeout=self._timeout)
        self._rec.record(f"file_chooser.arm:{self._owner}", timeout=self._timeout)
        return _FakeFileChooserInfo(self._rec)

    async def __aexit__(self, *_a: Any) -> None:
        return None


class _FakeHandle:
    def __init__(self, rec: _Recorder, frame: Any) -> None:
        self._rec, self._frame = rec, frame

    async def inner_text(self) -> str:
        return "hello world"

    async def content_frame(self) -> Any:
        self._rec.record("content_frame")
        return self._frame


class _FakeKeyboard:
    def __init__(self, rec: _Recorder) -> None:
        self._rec = rec

    async def press(self, key: str) -> None:
        self._rec.record("keyboard.press", key)
        # a11y_dragdrop exception-mid-sequence sentinel: raises AFTER being
        # recorded, so a test can see the attempt happened and then assert on
        # the release-and-reraise path it triggers.
        if key == "PoisonKey":
            raise RuntimeError("boom")


class _FakeContext:
    def __init__(self, rec: _Recorder) -> None:
        self._rec = rec
        self.pages: list[_FakePage] = []

    async def new_page(self) -> _FakePage:
        self._rec.record("context.new_page")
        page = _FakePage(self._rec, tag=f"tab-{len(self.pages)}", context=self)
        self.pages.append(page)
        return page

    async def route(self, pattern: str, handler: Any) -> None:
        """`inject_headers` routes on the CONTEXT, matching the live session --
        a page route would drop the header on popups the live run covered."""
        self._rec.record("context.route", pattern)

    async def unroute(self, pattern: str, handler: Any = None) -> None:
        self._rec.record("context.unroute", pattern)


class _FakePage:
    """Records every call the generated dispatch bodies can make on a page."""

    def __init__(self, rec: _Recorder, tag: str = "main", context: _FakeContext | None = None) -> None:
        self._rec, self.tag = rec, tag
        self.url = "https://example.test/current"
        self.keyboard = _FakeKeyboard(rec)
        self.context = context or _FakeContext(rec)

    def _log(self, name: str, *args: Any, **kw: Any) -> None:
        self._rec.record(name, *args, **kw)

    async def goto(self, url: str) -> None:
        self._log("goto", url)
        self.url = url

    async def click(self, selector: str) -> None:
        self._log("click", selector)

    async def fill(self, selector: str, value: str) -> None:
        self._log("fill", selector, value)

    async def type(self, selector: str, text: str, delay: int = 0) -> None:
        self._log("type", selector, text, delay=delay)

    async def hover(self, selector: str) -> None:
        self._log("hover", selector)

    async def select_option(self, selector: str, **kw: Any) -> None:
        self._log("select_option", selector, **kw)

    async def drag_and_drop(self, source: str, target: str) -> None:
        self._log("drag_and_drop", source, target)

    async def set_input_files(self, selector: str, paths: list[str]) -> None:
        self._log("set_input_files", selector, paths)

    def expect_file_chooser(self, *, timeout: int | None = None) -> _FakeFileChooserContext:
        return _FakeFileChooserContext(self._rec, timeout, self.tag)

    async def set_extra_http_headers(self, headers: dict[str, str]) -> None:
        self._log("set_extra_http_headers", headers)

    async def set_viewport_size(self, size: dict[str, int]) -> None:
        self._log("set_viewport_size", size)

    async def go_back(self) -> None:
        self._log("go_back")

    async def screenshot(self, **kw: Any) -> None:
        self._log("screenshot", **kw)

    async def evaluate(self, expression: str, *args: Any) -> Any:
        """a11y_dragdrop's verify_js/verify_text_contains land here too --
        same ``"() => false"`` sentinel as ``_FakeLocator.evaluate``."""
        self._log("evaluate", expression)
        return expression != "() => false"

    async def wait_for_selector(self, selector: str, **kw: Any) -> _FakeHandle:
        self._log("wait_for_selector", selector)
        return _FakeHandle(self._rec, _FakePage(self._rec, tag="frame"))

    async def query_selector(self, selector: str) -> None:
        self._log("query_selector", selector)
        return None

    async def wait_for_function(self, *args: Any, **kw: Any) -> None:
        self._log("wait_for_function")

    async def wait_for_load_state(self, state: str, **kw: Any) -> None:
        self._log("wait_for_load_state", state)

    async def route(self, pattern: str, handler: Any) -> None:
        self._log("route", pattern)

    async def unroute(self, pattern: str, handler: Any = None) -> None:
        self._log("unroute", pattern)

    def on(self, event: str, handler: Any) -> None:
        self._log("on", event)

    def frame(self, **kw: Any) -> Any:
        self._log("frame", **kw)
        return _FakePage(self._rec, tag="named-frame")

    async def close(self) -> None:
        self._log("close_page", self.tag)
        if self in self.context.pages:
            self.context.pages.remove(self)

    def get_by_role(self, role: str, **kw: Any) -> _FakeLocator:
        return _FakeLocator(self._rec, "role")

    def get_by_label(self, label: str, **kw: Any) -> _FakeLocator:
        return _FakeLocator(self._rec, "label")

    def get_by_text(self, text: str, **kw: Any) -> _FakeLocator:
        return _FakeLocator(self._rec, "text")

    def get_by_test_id(self, test_id: str) -> _FakeLocator:
        return _FakeLocator(self._rec, "test_id")

    def locator(self, selector: str) -> _FakeLocator:
        """a11y_dragdrop's source/verify_selector_* locators (CSS, not ARIA)."""
        self._log(f"locator.resolve:{self.tag}", selector)
        return _FakeLocator(self._rec, "css", self.tag)


def _install(monkeypatch: pytest.MonkeyPatch, rec: _Recorder) -> None:
    class _Browser:
        def __init__(self) -> None:
            self.context = _FakeContext(rec)

        async def new_page(self) -> _FakePage:
            page = _FakePage(rec, context=self.context)
            self.context.pages.append(page)
            return page

        async def close(self) -> None:
            rec.record("browser.close")

    class _Chromium:
        async def launch(self, *, headless: bool) -> _Browser:
            return _Browser()

    class _Playwright:
        chromium = _Chromium()

    class _Ctx:
        async def __aenter__(self) -> _Playwright:
            return _Playwright()

        async def __aexit__(self, *_a: Any) -> None:
            return None

    async_api = types.ModuleType("playwright.async_api")
    async_api.async_playwright = lambda: _Ctx()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)


#: One action per `_ACTION_MAP` kind, ordered so the stateful ones are reachable
#: (open_url before switch_page/close_page, mock_route before unmock_route).
_EVERY_ACTION: list[dict[str, Any]] = [
    {"action": "navigate", "url": "https://example.test/start"},
    {"action": "click", "selector": "#go"},
    {"action": "fill", "selector": "#name", "value": "Ada"},
    {"action": "type", "selector": "#bio", "text": "hi", "delay_ms": 5},
    {"action": "press_key", "key": "Enter"},
    {"action": "wait_for", "selector": "#ready"},
    {"action": "expect_url", "pattern": "example", "mode": "contains"},
    {"action": "expect_selector", "selector": "#ok"},
    {"action": "expect_text", "selector": "#msg", "text": "hello", "mode": "contains"},
    {"action": "expect_js", "expression": "1 === 1"},
    {"action": "click_by", "role": "button", "role_name": "Save"},
    {"action": "fill_by", "label": "Email", "value": "a@b.c"},
    {"action": "get_text_by", "test_id": "total"},
    {"action": "evaluate", "expression": "document.title"},
    {"action": "screenshot", "path": "shot.png"},
    {"action": "hover", "selector": "#menu"},
    {"action": "select_option", "selector": "#country", "value": "NL"},
    {"action": "drag", "source": "#a", "target": "#b"},
    {
        "action": "a11y_dragdrop",
        "source_selector": "#drag-me",
        "nav_key": "arrow",
        "nav_direction": "down",
        "max_nav_steps": 2,
        "verify_js": "() => true",
    },
    {"action": "set_input_files", "selector": "#file", "paths": ["a.txt"]},
    {"action": "upload_files", "selector": "#pick-file", "paths": ["b.txt"], "timeout_ms": 321},
    {"action": "resize", "width": 1280, "height": 800},
    {"action": "navigate_back"},
    {"action": "mock_route", "pattern": "**/api/*", "status": 201, "body": "{}"},
    {"action": "unmock_route", "pattern": "**/api/*"},
    {"action": "set_extra_http_headers", "headers": {"X-Env": "staging"}},
    {"action": "inject_headers", "pattern": "**/api/*", "headers": {"X-Trace": "abc"}},
    {"action": "uninject_headers", "pattern": "**/api/*"},
    {"action": "set_dialog_policy", "policy": "accept"},
    {"action": "open_url", "url": "https://example.test/tab"},
    {"action": "switch_page", "index": 1},
    {"action": "switch_frame", "selector": "iframe#pay"},
    {"action": "reset_frame"},
    {"action": "close_page", "index": 1},
]


def _run(monkeypatch: pytest.MonkeyPatch, actions: list[dict[str, Any]]) -> tuple[dict[str, int], _Recorder]:
    rec = _Recorder()
    _install(monkeypatch, rec)
    source = render_macro_cli(name="everything", macro={"actions": actions}, include_evidence=False)
    namespace: dict[str, Any] = {}
    # Executing the generated artefact is the point of this module.
    exec(source, namespace)
    return asyncio.run(namespace["run_everything"]()), rec


def test_the_fixture_covers_every_dispatchable_kind() -> None:
    """Guards the guard: if a kind is added, this list must grow with it."""
    assert {a["action"] for a in _EVERY_ACTION} == set(_ACTION_MAP)


def test_every_kind_executes_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    result, _rec = _run(monkeypatch, _EVERY_ACTION)
    assert result == {"executed": len(_EVERY_ACTION), "skipped": 0}


def test_each_branch_reaches_the_playwright_call_it_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    _result, rec = _run(monkeypatch, _EVERY_ACTION)
    names = rec.names()
    for expected in (
        "goto",
        "click",
        "fill",
        "type",
        "keyboard.press",
        "evaluate",
        "screenshot",
        "hover",
        "select_option",
        "drag_and_drop",
        "set_input_files",
        "file_chooser.arm",
        "file_chooser.await",
        "file_chooser.set_files",
        "set_viewport_size",
        "go_back",
        "route",
        "unroute",
        "on",
        "context.new_page",
        "content_frame",
        "close_page",
        "locator.click:role",
        "locator.fill:label",
        "locator.inner_text:test_id",
    ):
        assert expected in names, f"{expected} was never called — its branch is inert"


def test_recorded_field_spellings_reach_the_right_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    """The recorder and the session disagree on several names; both must work."""
    _result, rec = _run(monkeypatch, _EVERY_ACTION)
    assert rec.args_for("drag_and_drop") == ("#a", "#b")
    assert rec.args_for("route") == ("**/api/*",)
    assert rec.args_for("set_input_files") == ("#file", ["a.txt"])
    assert rec.args_for("file_chooser.set_files") == (["b.txt"],)
    assert rec.kwargs_for("file_chooser.set_files") == {"timeout": 321}
    assert rec.kwargs_for("select_option") == {"value": "NL"}
    assert rec.args_for("set_viewport_size") == ({"width": 1280, "height": 800},)
    assert rec.kwargs_for("screenshot") == {"path": "shot.png"}


@pytest.mark.parametrize(
    ("trigger", "expected_click"),
    [
        ({"selector": "#pick-file"}, "locator.click:css"),
        ({"role": "button", "role_name": "Upload", "role_exact": True}, "locator.click:role"),
    ],
)
def test_upload_files_arms_clicks_awaits_and_sets_in_order(
    monkeypatch: pytest.MonkeyPatch, trigger: dict[str, Any], expected_click: str
) -> None:
    action = {
        "action": "upload_files",
        "paths": ["first.txt", "second.txt"],
        "timeout_ms": 246,
        **trigger,
    }
    result, rec = _run(monkeypatch, [action])

    assert result == {"executed": 1, "skipped": 0}
    relevant = [
        name
        for name in rec.names()
        if name in {"file_chooser.arm", expected_click, "file_chooser.await", "file_chooser.set_files"}
    ]
    assert relevant == ["file_chooser.arm", expected_click, "file_chooser.await", "file_chooser.set_files"]
    assert rec.kwargs_for("file_chooser.arm") == {"timeout": 246}
    assert rec.kwargs_for(expected_click) == {"timeout": 246}
    assert rec.args_for("file_chooser.set_files") == (["first.txt", "second.txt"],)
    assert rec.kwargs_for("file_chooser.set_files") == {"timeout": 246}


@pytest.mark.parametrize(
    ("recorded", "expected"), [(None, DEFAULT_ACTION_TIMEOUT_MS), (0, DEFAULT_ACTION_TIMEOUT_MS), (246, 246)]
)
def test_upload_files_normalizes_timeout(monkeypatch: pytest.MonkeyPatch, recorded: int | None, expected: int) -> None:
    action: dict[str, Any] = {"action": "upload_files", "selector": "#pick", "paths": ["file.txt"]}
    if recorded is not None:
        action["timeout_ms"] = recorded

    result, rec = _run(monkeypatch, [action])

    assert result == {"executed": 1, "skipped": 0}
    assert rec.kwargs_for("file_chooser.arm") == {"timeout": expected}
    assert rec.kwargs_for("locator.click:css") == {"timeout": expected}
    assert rec.kwargs_for("file_chooser.set_files") == {"timeout": expected}


def test_upload_files_in_frame_arms_page_listener_and_clicks_frame_trigger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions = [
        {"action": "switch_frame", "selector": "iframe#upload"},
        {
            "action": "upload_files",
            "selector": "#pick-file",
            "paths": ["inside-frame.txt"],
            "timeout_ms": 357,
        },
    ]
    result, rec = _run(monkeypatch, actions)
    names = rec.names()

    assert result == {"executed": 2, "skipped": 0}
    assert names.count("file_chooser.arm:main") == 1
    assert "file_chooser.arm:frame" not in names
    assert rec.args_for("locator.resolve:frame") == ("#pick-file",)
    assert "locator.resolve:main" not in names
    assert names.count("locator.click:css:frame") == 1
    assert "locator.click:css:main" not in names
    assert rec.args_for("file_chooser.set_files") == (["inside-frame.txt"],)
    assert rec.kwargs_for("file_chooser.set_files") == {"timeout": 357}


def test_open_url_preserves_active_page_and_frame_until_switch_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions = [
        {"action": "switch_frame", "selector": "iframe#upload"},
        {"action": "open_url", "url": "https://example.test/new"},
        {"action": "upload_files", "selector": "#frame-upload", "paths": ["frame.txt"]},
        {"action": "switch_page", "index": 1},
        {"action": "upload_files", "selector": "#page-upload", "paths": ["page.txt"]},
    ]

    result, rec = _run(monkeypatch, actions)

    assert result == {"executed": len(actions), "skipped": 0}
    names = rec.names()
    assert names.count("file_chooser.arm:main") == 1
    assert names.count("locator.click:css:frame") == 1
    assert names.count("file_chooser.arm:tab-1") == 1
    assert names.count("locator.click:css:tab-1") == 1


def test_close_page_preserves_inactive_identity_and_frame_then_resets_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions = [
        {"action": "open_url", "url": "https://example.test/one"},
        {"action": "open_url", "url": "https://example.test/two"},
        {"action": "switch_page", "index": 2},
        {"action": "switch_frame", "selector": "iframe#upload"},
        {"action": "close_page", "index": 0},
        {"action": "upload_files", "selector": "#still-frame", "paths": ["frame.txt"]},
        {"action": "close_page", "index": 1},
        {"action": "upload_files", "selector": "#remaining", "paths": ["page.txt"]},
    ]

    result, rec = _run(monkeypatch, actions)

    assert result == {"executed": len(actions), "skipped": 0}
    assert [args for name, args, _kw in rec.calls if name == "close_page"] == [("main",), ("tab-2",)]
    assert rec.names().count("file_chooser.arm:tab-2") == 1
    assert rec.names().count("locator.click:css:frame") == 1
    assert rec.names().count("file_chooser.arm:tab-1") == 1
    assert rec.names().count("locator.click:css:tab-1") == 1


def test_close_page_without_index_closes_current_and_refuses_last_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, rec = _run(
        monkeypatch,
        [
            {"action": "open_url", "url": "https://example.test/new"},
            {"action": "switch_page", "index": 1},
            {"action": "close_page"},
            {"action": "upload_files", "selector": "#remaining", "paths": ["page.txt"]},
        ],
    )

    assert result == {"executed": 4, "skipped": 0}
    assert rec.args_for("close_page") == ("tab-1",)
    assert rec.names().count("file_chooser.arm:main") == 1
    with pytest.raises(RuntimeError, match="last remaining page"):
        _run(monkeypatch, [{"action": "close_page"}])


@pytest.mark.parametrize(
    ("recorded", "renamed"),
    [("source", "source_selector"), ("target", "target_selector")],
)
def test_drag_accepts_the_session_parameter_spelling(
    monkeypatch: pytest.MonkeyPatch, recorded: str, renamed: str
) -> None:
    action = {"action": "drag", "source": "#a", "target": "#b"}
    del action[recorded]
    action[renamed] = "#renamed"
    result, rec = _run(monkeypatch, [action])
    assert result["executed"] == 1
    assert "#renamed" in rec.args_for("drag_and_drop")


def test_mock_route_accepts_the_session_parameter_spelling(monkeypatch: pytest.MonkeyPatch) -> None:
    actions = [
        {"action": "mock_route", "url_pattern": "**/x", "status": 200},
        {"action": "unmock_route", "url_pattern": "**/x"},
    ]
    result, rec = _run(monkeypatch, actions)
    assert result["executed"] == 2
    assert rec.args_for("unroute") == ("**/x",)


def test_screenshot_without_a_path_is_skipped_not_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mirrors macros.runtime._dispatch_standard, which returns (0, 1) there."""
    result, rec = _run(monkeypatch, [{"action": "screenshot"}])
    assert result == {"executed": 0, "skipped": 1}
    assert "screenshot" not in rec.names()


def test_switch_frame_retargets_element_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    """A recorded switch_frame has to actually move where clicks land."""
    actions = [
        {"action": "switch_frame", "selector": "iframe#pay"},
        {"action": "click", "selector": "#inside"},
        {"action": "reset_frame"},
        {"action": "click", "selector": "#outside"},
    ]
    result, rec = _run(monkeypatch, actions)
    assert result["executed"] == 4
    assert rec.names().count("click") == 2


def _presses(rec: _Recorder) -> list[str]:
    return [args[0] for name, args, _kw in rec.calls if name == "keyboard.press"]


def test_a11y_dragdrop_verified_immediately_needs_no_release(monkeypatch: pytest.MonkeyPatch) -> None:
    """The success path: grab, navigate, drop, verify passes on the first poll -- no release."""
    action = {
        "action": "a11y_dragdrop",
        "source_selector": "#drag-me",
        "nav_key": "tab",
        "max_nav_steps": 3,
        "verify_js": "() => true",
    }
    result, rec = _run(monkeypatch, [action])
    assert result == {"executed": 1, "skipped": 0}
    assert _presses(rec) == ["Space", "Tab", "Tab", "Tab", "Space"]


@pytest.mark.parametrize(
    ("nav_key", "nav_direction", "expected"),
    [
        ("tab", None, ["Tab", "Tab"]),
        ("tab", "backward", ["Shift+Tab", "Shift+Tab"]),
        ("arrow", "up", ["ArrowUp", "ArrowUp"]),
        ("arrow", None, ["ArrowDown", "ArrowDown"]),
    ],
)
def test_a11y_dragdrop_tab_and_arrow_modes_resolve_the_right_keys(
    monkeypatch: pytest.MonkeyPatch, nav_key: str, nav_direction: str | None, expected: list[str]
) -> None:
    action: dict[str, Any] = {
        "action": "a11y_dragdrop",
        "source_selector": "#drag-me",
        "nav_key": nav_key,
        "max_nav_steps": 2,
        "verify_js": "() => true",
    }
    if nav_direction is not None:
        action["nav_direction"] = nav_direction
    _result, rec = _run(monkeypatch, [action])
    nav_presses = _presses(rec)[1:-1]  # strip the leading grab press and the trailing drop press
    assert nav_presses == expected


def test_a11y_dragdrop_keys_mode_sends_the_explicit_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    """The third nav mode: an explicit key sequence, sent verbatim once."""
    action = {
        "action": "a11y_dragdrop",
        "source_selector": "#drag-me",
        "nav_key": "keys",
        "nav_key_sequence": ["ArrowRight", "ArrowRight", "Enter"],
        "verify_js": "() => true",
    }
    _result, rec = _run(monkeypatch, [action])
    nav_presses = _presses(rec)[1:-1]
    assert nav_presses == ["ArrowRight", "ArrowRight", "Enter"]


def test_a11y_dragdrop_defaults_match_the_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """No optional fields set -- must resolve exactly the engine's defaults:
    nav_key=tab forward, max_nav_steps=12, grab_key=drop_key=Space, release_key=Escape."""
    action = {
        "action": "a11y_dragdrop",
        "source_selector": "#drag-me",
        "verify_js": "() => false",  # sentinel: never verifies, so release always fires
        "verify_timeout_ms": 5,
        "verify_poll_ms": 1,
    }
    result, rec = _run(monkeypatch, [action])
    assert result == {"executed": 1, "skipped": 0}
    presses = _presses(rec)
    assert presses[0] == "Space"  # grab_key default
    assert presses[1:13] == ["Tab"] * 12  # max_nav_steps default (12), tab forward direction default
    assert presses[13] == "Space"  # drop_key default
    assert presses[14] == "Escape"  # release_key default


def test_a11y_dragdrop_releases_on_failed_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    """The trap: a verify that never passes must still press release_key.

    Task 1's fix round removed exactly this bug from the engine -- a grabbed
    widget left stuck is indistinguishable from a grab that never registered.
    """
    action = {
        "action": "a11y_dragdrop",
        "source_selector": "#drag-me",
        "max_nav_steps": 1,
        "verify_js": "() => false",
        "verify_timeout_ms": 20,
        "verify_poll_ms": 5,
        "release_key": "Escape",
    }
    result, rec = _run(monkeypatch, [action])
    assert result == {"executed": 1, "skipped": 0}
    assert _presses(rec) == ["Space", "Tab", "Space", "Escape"]


def test_a11y_dragdrop_ungrabbed_widget_skips_release(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other half of the trap: a False grab predicate must NOT release --
    the key was pressed but the widget demonstrably never entered grab mode,
    so there is nothing to release. Nav/drop/verify must not run either."""
    action = {
        "action": "a11y_dragdrop",
        "source_selector": "#drag-me",
        "grabbed_predicate_js": "() => false",
        "verify_js": "() => true",
    }
    result, rec = _run(monkeypatch, [action])
    assert result == {"executed": 1, "skipped": 0}
    assert _presses(rec) == ["Space"]


def test_a11y_dragdrop_releases_on_exception_mid_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exception during navigate/drop/verify must still release AND
    propagate the ORIGINAL exception -- a failing release must not replace it."""
    action = {
        "action": "a11y_dragdrop",
        "source_selector": "#drag-me",
        "nav_key": "keys",
        "nav_key_sequence": ["PoisonKey"],
        "verify_js": "() => true",
    }
    rec = _Recorder()
    _install(monkeypatch, rec)
    source = render_macro_cli(name="boom", macro={"actions": [action]}, include_evidence=False)
    namespace: dict[str, Any] = {}
    exec(source, namespace)
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(namespace["run_boom"]())
    assert _presses(rec) == ["Space", "PoisonKey", "Escape"]


def test_a11y_dragdrop_rejects_an_unknown_nav_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replay raises ValueError on an unknown nav_key; the exported script used
    to fall through to Tab navigation and silently do something else."""
    action = {
        "action": "a11y_dragdrop",
        "source_selector": "#drag-me",
        "nav_key": "swipe",
        "verify_js": "() => true",
    }
    with pytest.raises(RuntimeError, match="nav_key must be one of"):
        _run(monkeypatch, [action])


def test_a11y_dragdrop_rejects_keys_mode_without_a_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Otherwise the script sends zero navigation keys and drops in place."""
    action = {
        "action": "a11y_dragdrop",
        "source_selector": "#drag-me",
        "nav_key": "keys",
        "verify_js": "() => true",
    }
    with pytest.raises(RuntimeError, match="non-empty nav_key_sequence"):
        _run(monkeypatch, [action])


def test_a11y_dragdrop_rejects_a_sequence_outside_keys_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    action = {
        "action": "a11y_dragdrop",
        "source_selector": "#drag-me",
        "nav_key": "arrow",
        "nav_key_sequence": ["End"],
        "verify_js": "() => true",
    }
    with pytest.raises(RuntimeError, match="only valid with nav_key='keys'"):
        _run(monkeypatch, [action])


@pytest.mark.parametrize(
    "verify",
    [{}, {"verify_js": "() => true", "verify_text_contains": "Done"}],
    ids=["none", "two"],
)
def test_a11y_dragdrop_rejects_wrong_verify_arity(monkeypatch: pytest.MonkeyPatch, verify: dict[str, Any]) -> None:
    """A macro exported without passing lint used to raise a bare KeyError from
    inside the verify dispatch; it now names the actual problem."""
    action = {"action": "a11y_dragdrop", "source_selector": "#drag-me", **verify}
    with pytest.raises(RuntimeError, match="exactly one verify_"):
        _run(monkeypatch, [action])


def test_a11y_dragdrop_empty_verify_js_uses_the_text_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Truthiness dispatch, matching the engine: an empty verify_js is not a
    verify_js, so the author's text check is the one that runs."""
    action = {
        "action": "a11y_dragdrop",
        "source_selector": "#drag-me",
        "max_nav_steps": 1,
        "verify_js": "",
        "verify_text_contains": "Done",
    }
    _result, rec = _run(monkeypatch, [action])
    evaluated = [args[0] for name, args, _kw in rec.calls if name == "evaluate"]
    assert evaluated and all("innerText.includes" in js for js in evaluated), evaluated


def test_unmock_route_without_a_matching_mock_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="no active mock"):
        _run(monkeypatch, [{"action": "unmock_route", "pattern": "**/never"}])


def test_switch_page_out_of_range_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="no page at index"):
        _run(monkeypatch, [{"action": "switch_page", "index": 7}])


def test_expect_url_mismatch_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="URL mismatch"):
        _run(monkeypatch, [{"action": "expect_url", "pattern": "nope", "mode": "equals"}])


def test_lifecycle_actions_are_still_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    result, _rec = _run(monkeypatch, [{"action": "launch"}, {"action": "close"}, {"action": "snapshot"}])
    assert result == {"executed": 0, "skipped": 3}


def test_an_unknown_kind_still_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="unsupported macro action"):
        _run(monkeypatch, [{"action": "not_a_real_action"}])


def test_placeholder_substitution_reaches_a_new_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_resolve` runs before dispatch, so a parameter must land in a new branch too."""
    rec = _Recorder()
    _install(monkeypatch, rec)
    macro = {"parameters": ["term"], "actions": [{"action": "hover", "selector": "#{{term}}"}]}
    source = render_macro_cli(name="p", macro=macro, args={"term": "menu"}, include_evidence=False)
    namespace: dict[str, Any] = {}
    exec(source, namespace)
    asyncio.run(namespace["run_p"]("menu"))
    assert rec.args_for("hover") == ("#menu",)


def test_regex_modes_use_the_re_module_in_the_generated_script(monkeypatch: pytest.MonkeyPatch) -> None:
    """`re` is imported by the generated file, not inherited from this one."""
    actions = [{"action": "expect_url", "pattern": r"example\.test", "mode": "regex"}]
    result, _rec = _run(monkeypatch, actions)
    assert result["executed"] == 1
    assert re.search(r"^import re$", render_macro_cli(name="x", macro={"actions": []}), re.M)
