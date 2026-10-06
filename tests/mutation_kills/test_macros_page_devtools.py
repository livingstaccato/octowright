# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What the redacted screenshot's DevTools half sends, counts and returns.

A scripted DevTools session stands in for Chrome: it records every command exactly as
sent and answers from a per-method script, so each test pins the whole conversation.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from octowright.macros import page_devtools
from octowright.macros import redaction_page_js as page_js
from octowright.macros.page_devtools import (
    COUNTED_EVENTS,
    PageChanges,
    PageController,
    closed_shadow_roots,
    end_view_transitions,
    view_transition_pseudo_elements,
)

GROUP = "octowright-redacted-screenshot"
WHOLE = {"depth": -1, "pierce": True}


class ScriptedCDP:
    """A DevTools session that records every call and answers from a script."""

    def __init__(self, replies: dict[str, Any] | None = None) -> None:
        self.sent: list[tuple[Any, ...]] = []
        self.listeners: list[tuple[str, Any]] = []
        self.removed: list[tuple[str, Any]] = []
        self.replies = dict(replies or {})
        self.fail_remove: set[str] = set()

    async def send(self, *args: Any) -> Any:
        self.sent.append(args)
        reply = self.replies.get(args[0], {})
        if isinstance(reply, BaseException):
            raise reply
        if callable(reply):
            return reply(*args[1:])
        if isinstance(reply, list):
            return reply.pop(0)
        return reply

    def on(self, method: str, handler: Any) -> None:
        self.listeners.append((method, handler))

    def remove_listener(self, method: str, handler: Any) -> None:
        self.removed.append((method, handler))
        if method in self.fail_remove:
            raise RuntimeError("gone")

    def fire(self, method: str, params: dict[str, Any]) -> None:
        for name, handler in self.listeners:
            if name == method:
                handler(params)


# --- tree walkers -------------------------------------------------------------------


def test_closed_shadow_roots_collects_every_closed_root_in_walk_order() -> None:
    tree = {
        "shadowRoots": [
            {
                "shadowRootType": "open",
                "backendNodeId": 1,
                "shadowRoots": [{"shadowRootType": "closed", "backendNodeId": "2"}],
            },
            {"shadowRootType": "closed", "backendNodeId": 3},
        ],
        "children": [
            {"shadowRoots": [{"shadowRootType": "closed", "backendNodeId": 4}]},
            {"children": [{"shadowRoots": [{"backendNodeId": 5}, {"shadowRootType": "closed", "backendNodeId": 6}]}]},
        ],
    }
    found = closed_shadow_roots(tree)
    assert found == [3, 6, 4, 2]
    assert all(type(item) is int for item in found)


def test_closed_shadow_roots_of_an_empty_node_is_empty() -> None:
    assert closed_shadow_roots({}) == []


def test_view_transition_pseudo_elements_reads_pseudo_children_shadow_roots_and_nested_pseudos() -> None:
    tree = {
        "pseudoElements": [
            {"pseudoType": "before"},
            {"pseudoType": "view-transition", "pseudoElements": [{"pseudoType": "view-transition-group"}]},
            {},
        ],
        "children": [{"pseudoElements": [{"pseudoType": "view-transition-old"}]}],
        "shadowRoots": [{"pseudoElements": [{"pseudoType": "view-transition-new"}]}],
    }
    assert view_transition_pseudo_elements(tree) == [
        "view-transition",
        "view-transition-new",
        "view-transition-old",
        "view-transition-group",
    ]


def test_view_transition_pseudo_elements_ignores_a_type_that_only_contains_the_word() -> None:
    assert view_transition_pseudo_elements({"pseudoElements": [{"pseudoType": "x-view-transition"}]}) == []
    assert view_transition_pseudo_elements({}) == []


# --- PageChanges ---------------------------------------------------------------------


async def test_start_subscribes_to_every_counted_event_and_returns_the_closed_roots() -> None:
    document = {"root": {"shadowRoots": [{"shadowRootType": "closed", "backendNodeId": 9}]}}
    cdp = ScriptedCDP({"DOM.getDocument": document})
    changes = PageChanges(cdp)
    assert changes.count == 0
    assert await changes.start() == [9]
    assert [method for method, _ in cdp.listeners] == list(COUNTED_EVENTS)
    assert cdp.sent == [("DOM.enable",), ("DOM.getDocument", WHOLE), ("CSS.enable",)]
    assert changes.count == 0


async def test_start_without_a_root_finds_no_closed_roots() -> None:
    cdp = ScriptedCDP({"DOM.getDocument": {}})
    assert await PageChanges(cdp).start() == []


async def test_apply_styles_computes_the_style_of_the_first_element_child() -> None:
    document = {
        "root": {
            "children": [{"nodeType": 10, "nodeId": 1}, {"nodeType": 1, "nodeId": "7"}, {"nodeType": 1, "nodeId": 8}]
        }
    }
    cdp = ScriptedCDP({"DOM.getDocument": document})
    await PageChanges(cdp).apply_styles()
    assert cdp.sent == [("DOM.getDocument", WHOLE), ("CSS.getComputedStyleForNode", {"nodeId": 7})]
    assert type(cdp.sent[1][1]["nodeId"]) is int


async def test_apply_styles_without_an_element_only_reads_the_document() -> None:
    cdp = ScriptedCDP({"DOM.getDocument": {"root": {"children": [{"nodeType": 3, "nodeId": 4}]}}})
    await PageChanges(cdp).apply_styles()
    assert cdp.sent == [("DOM.getDocument", WHOLE)]
    cdp = ScriptedCDP({"DOM.getDocument": {}})
    await PageChanges(cdp).apply_styles()
    assert cdp.sent == [("DOM.getDocument", WHOLE)]


async def test_apply_styles_reads_element_id_zero_as_an_element() -> None:
    cdp = ScriptedCDP({"DOM.getDocument": {"root": {"children": [{"nodeType": 1, "nodeId": 0}]}}})
    await PageChanges(cdp).apply_styles()
    assert cdp.sent[-1] == ("CSS.getComputedStyleForNode", {"nodeId": 0})


async def _started(cdp: ScriptedCDP) -> PageChanges:
    cdp.replies.setdefault("DOM.getDocument", {"root": {}})
    changes = PageChanges(cdp)
    await changes.start()
    return changes


async def test_changes_count_only_between_begin_and_end_and_skip_inline_style_edits() -> None:
    cdp = ScriptedCDP()
    changes = await _started(cdp)
    cdp.fire("DOM.childNodeInserted", {})
    assert changes.count == 0
    changes.begin()
    cdp.fire("DOM.childNodeInserted", {})
    cdp.fire("DOM.attributeModified", {"name": "style"})
    cdp.fire("DOM.attributeRemoved", {"name": "style"})
    assert changes.count == 1
    cdp.fire("DOM.attributeModified", {"name": "class"})
    cdp.fire("DOM.attributeRemoved", {})
    cdp.fire("DOM.childNodeRemoved", {"name": "style"})
    cdp.fire("CSS.styleSheetAdded", {"header": {"styleSheetId": "s"}})
    cdp.fire("CSS.styleSheetRemoved", {"styleSheetId": "s"})
    assert changes.count == 6
    changes.end()
    cdp.fire("DOM.childNodeInserted", {})
    assert changes.count == 6
    changes.begin()
    cdp.fire("Animation.animationCreated", {})
    assert changes.count == 7


async def test_sheets_hold_reads_every_added_sheet_in_order_until_one_holds_a_value() -> None:
    cdp = ScriptedCDP({"CSS.getStyleSheetText": lambda params: {"text": f"body {{}} /* {params['styleSheetId']} */"}})
    changes = await _started(cdp)
    for sheet in ("c", "a", "b", "gone"):
        cdp.fire("CSS.styleSheetAdded", {"header": {"styleSheetId": sheet}})
    cdp.fire("CSS.styleSheetRemoved", {"styleSheetId": "gone"})
    cdp.sent.clear()
    assert await changes.sheets_hold(["zebrin4"]) is False
    assert cdp.sent == [
        ("CSS.getStyleSheetText", {"styleSheetId": "a"}),
        ("CSS.getStyleSheetText", {"styleSheetId": "b"}),
        ("CSS.getStyleSheetText", {"styleSheetId": "c"}),
    ]
    cdp.sent.clear()
    cdp.replies["CSS.getStyleSheetText"] = lambda params: {"text": "x" if params["styleSheetId"] == "a" else "zebrin4"}
    assert await changes.sheets_hold(["zebrin4"]) is True
    assert cdp.sent == [
        ("CSS.getStyleSheetText", {"styleSheetId": "a"}),
        ("CSS.getStyleSheetText", {"styleSheetId": "b"}),
    ]


async def test_sheets_hold_counts_an_unreadable_sheet_as_holding_and_a_textless_one_as_not() -> None:
    cdp = ScriptedCDP({"CSS.getStyleSheetText": {}})
    changes = await _started(cdp)
    cdp.fire("CSS.styleSheetAdded", {})
    cdp.sent.clear()
    assert await changes.sheets_hold(["zebrin4"]) is False
    assert cdp.sent == [("CSS.getStyleSheetText", {"styleSheetId": ""})]
    cdp.replies["CSS.getStyleSheetText"] = RuntimeError("no such sheet")
    assert await changes.sheets_hold(["zebrin4"]) is True


@pytest.mark.parametrize("value", ["None", "XXXX"])
async def test_a_textless_sheet_holds_no_value_whatever_the_value_spells(value: str) -> None:
    cdp = ScriptedCDP({"CSS.getStyleSheetText": {}})
    changes = await _started(cdp)
    cdp.fire("CSS.styleSheetAdded", {"header": {"styleSheetId": "s"}})
    assert await changes.sheets_hold([value]) is False


async def test_a_removed_sheet_without_an_id_drops_the_idless_sheet() -> None:
    cdp = ScriptedCDP({"CSS.getStyleSheetText": RuntimeError("unreadable")})
    changes = await _started(cdp)
    cdp.fire("CSS.styleSheetAdded", {"header": {}})
    cdp.fire("CSS.styleSheetRemoved", {})
    assert await changes.sheets_hold(["zebrin4"]) is False


async def test_close_unsubscribes_every_handler_and_disables_both_domains_whatever_fails() -> None:
    cdp = ScriptedCDP({"CSS.disable": RuntimeError("detached")})
    cdp.fail_remove = {COUNTED_EVENTS[0]}
    changes = await _started(cdp)
    changes.begin()
    cdp.sent.clear()
    await changes.close()
    assert cdp.removed == cdp.listeners
    assert cdp.sent == [("CSS.disable",), ("DOM.disable",)]
    cdp.fire("DOM.childNodeInserted", {})
    assert changes.count == 0
    cdp.removed.clear()
    cdp.sent.clear()
    await changes.close()
    assert cdp.removed == []
    assert cdp.sent == [("CSS.disable",), ("DOM.disable",)]


# --- end_view_transitions --------------------------------------------------------------


def _record_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(page_devtools.asyncio, "sleep", sleep)
    return sleeps


async def test_end_view_transitions_ends_them_and_waits_for_their_pseudo_elements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps = _record_sleeps(monkeypatch)
    transition = {"root": {"pseudoElements": [{"pseudoType": "view-transition"}]}}
    cdp = ScriptedCDP(
        {
            "DOM.getDocument": [
                {"root": {"shadowRoots": [{"shadowRootType": "closed", "backendNodeId": 11}]}},
                transition,
                transition,
                {"root": {}},
            ],
            "DOM.resolveNode": {"object": {"objectId": "root-11"}},
            "Runtime.evaluate": {"result": {"objectId": "doc-1"}},
            "Runtime.callFunctionOn": {"result": {"value": True}},
        }
    )
    assert await end_view_transitions(cdp) is True
    assert cdp.sent == [
        ("DOM.getDocument", WHOLE),
        ("DOM.resolveNode", {"backendNodeId": 11, "objectGroup": GROUP}),
        ("Runtime.evaluate", {"expression": "document", "objectGroup": GROUP}),
        (
            "Runtime.callFunctionOn",
            {
                "objectId": "doc-1",
                "functionDeclaration": page_js.END_VIEW_TRANSITIONS_JS,
                "arguments": [{"objectId": "root-11"}],
                "awaitPromise": True,
                "returnByValue": True,
                "objectGroup": GROUP,
            },
        ),
        ("DOM.getDocument", WHOLE),
        ("DOM.getDocument", WHOLE),
        ("DOM.getDocument", WHOLE),
    ]
    assert sleeps == [0.02, 0.02]


@pytest.mark.parametrize(
    "reply", [{}, {"result": {}}, {"result": {"value": "true"}}, {"result": {"value": False}}, {"result": {"value": 1}}]
)
async def test_end_view_transitions_reports_none_running_unless_the_page_says_true(
    reply: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps = _record_sleeps(monkeypatch)
    cdp = ScriptedCDP(
        {
            "DOM.getDocument": {"root": {"pseudoElements": [{"pseudoType": "view-transition"}]}},
            "Runtime.evaluate": {"result": {"objectId": "doc-1"}},
            "Runtime.callFunctionOn": reply,
        }
    )
    assert await asyncio.wait_for(end_view_transitions(cdp), 5) is False
    assert [call[0] for call in cdp.sent] == ["DOM.getDocument", "Runtime.evaluate", "Runtime.callFunctionOn"]
    assert cdp.sent[-1][1]["arguments"] == []
    assert sleeps == []


async def test_end_view_transitions_without_a_root_resolves_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _record_sleeps(monkeypatch)
    cdp = ScriptedCDP(
        {
            "DOM.getDocument": [{}, {}],
            "Runtime.evaluate": {"result": {"objectId": "doc-1"}},
            "Runtime.callFunctionOn": {"result": {"value": True}},
        }
    )
    assert await end_view_transitions(cdp) is True
    assert [call[0] for call in cdp.sent] == [
        "DOM.getDocument",
        "Runtime.evaluate",
        "Runtime.callFunctionOn",
        "DOM.getDocument",
    ]


# --- PageController --------------------------------------------------------------------


def _controller_cdp(call_reply: Any = None) -> ScriptedCDP:
    return ScriptedCDP(
        {
            "Runtime.evaluate": {"result": {"objectId": "doc-1"}},
            "Runtime.callFunctionOn": call_reply if call_reply is not None else [{"result": {"objectId": "ctl-1"}}],
            "DOM.resolveNode": lambda params: {"object": {"objectId": f"node-{params['backendNodeId']}"}},
        }
    )


async def test_create_builds_the_controller_in_the_main_world() -> None:
    cdp = _controller_cdp()
    controller = await PageController.create(cdp, ["zebrin4"])
    assert isinstance(controller, PageController)
    assert cdp.sent == [
        ("Runtime.evaluate", {"expression": "document", "objectGroup": GROUP}),
        (
            "Runtime.callFunctionOn",
            {
                "objectId": "doc-1",
                "functionDeclaration": page_js.CONTROLLER_JS,
                "arguments": [{"value": page_js.controller_argument(["zebrin4"])}],
                "objectGroup": GROUP,
            },
        ),
    ]


async def test_create_refuses_when_the_page_throws() -> None:
    cdp = _controller_cdp([{"exceptionDetails": {}, "result": {"objectId": "ctl-1"}}])
    with pytest.raises(RuntimeError) as raised:
        await PageController.create(cdp, [])
    assert str(raised.value) == "the page controller could not be created"


def _call(method: str, arguments: list[dict[str, Any]]) -> tuple[Any, ...]:
    return (
        "Runtime.callFunctionOn",
        {
            "objectId": "ctl-1",
            "functionDeclaration": f"function(...args) {{ return this.{method}(...args); }}",
            "arguments": arguments,
            "returnByValue": True,
            "objectGroup": GROUP,
        },
    )


async def _created(replies: list[Any]) -> tuple[PageController, ScriptedCDP]:
    cdp = _controller_cdp([{"result": {"objectId": "ctl-1"}}, *replies])
    controller = await PageController.create(cdp, [])
    cdp.sent.clear()
    return controller, cdp


async def test_every_controller_method_calls_through_the_held_object() -> None:
    controller, cdp = await _created([{}, {}, {"result": {"value": {"ok": True}}}, {}])
    await controller.redact([5, 6])
    await controller.watch(True)
    assert await controller.verify() == {"ok": True}
    await controller.restore()
    assert cdp.sent == [
        ("DOM.resolveNode", {"backendNodeId": 5, "objectGroup": GROUP}),
        ("DOM.resolveNode", {"backendNodeId": 6, "objectGroup": GROUP}),
        _call("redact", [{"objectId": "node-5"}, {"objectId": "node-6"}]),
        _call("watch", [{"value": True}]),
        _call("verify", []),
        _call("restore", []),
    ]


async def test_redact_with_no_closed_roots_passes_no_arguments() -> None:
    controller, cdp = await _created([{}])
    await controller.redact([])
    assert cdp.sent == [_call("redact", [])]


@pytest.mark.parametrize(
    "reply", [{}, {"result": {}}, {"result": {"value": ["not", "a", "dict"]}}, {"result": {"value": None}}]
)
async def test_verify_reads_anything_but_a_report_as_empty(reply: dict[str, Any]) -> None:
    controller, _ = await _created([reply])
    assert await controller.verify() == {}


async def test_a_controller_call_that_throws_names_the_method() -> None:
    controller, _ = await _created([{"exceptionDetails": {"text": "boom"}, "result": {"value": {}}}])
    with pytest.raises(RuntimeError) as raised:
        await controller.verify()
    assert str(raised.value) == "the page controller's verify failed"


async def test_dispose_releases_the_object_group_and_never_raises() -> None:
    controller, cdp = await _created([])
    await controller.dispose()
    assert cdp.sent == [("Runtime.releaseObjectGroup", {"objectGroup": GROUP})]
    cdp.replies["Runtime.releaseObjectGroup"] = RuntimeError("detached")
    await controller.dispose()
