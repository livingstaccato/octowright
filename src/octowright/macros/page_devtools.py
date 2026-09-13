# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The DevTools half of a redacted screenshot: the page controller and the change count.

Both work over the one DevTools session the screenshot is captured through.

:class:`PageController` creates the in-page controller
(:mod:`octowright.macros.redaction_page_js`) in the page's main world and calls it. The
objects it holds belong to that session, so page script cannot reach them.

:class:`PageChanges` counts what Chrome itself reports about the page once counting
begins: every DOM mutation in the document, in open and closed shadow roots and in
same-process frames (inserted, removed and edited nodes, attributes, pseudo-elements,
shadow roots and the top layer), every stylesheet that is added, removed or edited,
through any route, including CSSOM calls a page script could make without passing any
prototype, and every new animation (a script's ``Element.animate`` included; the
screenshot has paused the animation timeline, so a new one would hold its first
frame). Inline style changes are left to the page controller, which can tell a
harmless one from one that could reveal a value.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterable, Mapping
from typing import Any

from octowright.macros import redaction_page_js as page_js
from octowright.macros.rendered_surface import ValueMatcher

#: DevTools events that mean the page changed.
COUNTED_EVENTS = (
    "Animation.animationCreated",
    "CSS.mediaQueryResultChanged",
    "CSS.styleSheetAdded",
    "CSS.styleSheetChanged",
    "CSS.styleSheetRemoved",
    "DOM.attributeModified",
    "DOM.attributeRemoved",
    "DOM.characterDataModified",
    "DOM.childNodeCountUpdated",
    "DOM.childNodeInserted",
    "DOM.childNodeRemoved",
    "DOM.documentUpdated",
    "DOM.pseudoElementAdded",
    "DOM.pseudoElementRemoved",
    "DOM.shadowRootPopped",
    "DOM.shadowRootPushed",
    "DOM.topLayerElementsUpdated",
)

_STYLE_ATTRIBUTE_EVENTS = frozenset({"DOM.attributeModified", "DOM.attributeRemoved"})
_OBJECT_GROUP = "octowright-redacted-screenshot"
#: How often ending view transitions re-reads the document until their pseudo-elements are gone.
_VIEW_TRANSITION_SETTLE_POLL_SECONDS = 0.02


def closed_shadow_roots(node: Mapping[str, Any]) -> list[int]:
    """Backend node ids of the closed shadow roots in a pierced ``DOM.getDocument`` tree.

    A frame's document is another JavaScript context, so its roots are not collected; the
    frame element is hidden or refused instead.
    """
    found: list[int] = []
    pending = [node]
    while pending:
        current = pending.pop()
        for shadow in current.get("shadowRoots", []):
            if shadow.get("shadowRootType") == "closed":
                found.append(int(shadow["backendNodeId"]))
            pending.append(shadow)
        pending.extend(current.get("children", []))
    return found


def view_transition_pseudo_elements(node: Mapping[str, Any]) -> list[str]:
    """The view-transition pseudo-elements Chrome still draws, in a pierced ``DOM.getDocument`` tree.

    Every shadow root is read, open or closed, including one attached after the redaction collected its roots. A
    frame's document is not: frames are hidden, so a transition inside one is never drawn.
    """
    found: list[str] = []
    pending = [node]
    while pending:
        current = pending.pop()
        for pseudo in current.get("pseudoElements", []):
            kind = str(pseudo.get("pseudoType", ""))
            if kind.startswith("view-transition"):
                found.append(kind)
            pending.append(pseudo)
        pending.extend(current.get("children", []))
        pending.extend(current.get("shadowRoots", []))
    return found


class PageChanges:
    """Counts the page changes Chrome reports over one DevTools session."""

    def __init__(self, cdp: Any) -> None:
        self._cdp = cdp
        self._handlers: list[tuple[str, Any]] = []
        self._sheets: set[str] = set()
        self._counting = False
        self.count = 0

    async def start(self) -> list[int]:
        """Subscribe, enable the DOM and CSS domains, and return the closed shadow roots."""
        for method in COUNTED_EVENTS:
            handler = self._handler(method)
            self._cdp.on(method, handler)
            self._handlers.append((method, handler))
        await self._cdp.send("DOM.enable")
        document = await self._cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
        await self._cdp.send("CSS.enable")
        return closed_shadow_roots(document.get("root", {}))

    async def apply_styles(self) -> None:
        """Have Chrome apply the page's pending style changes now, before counting begins.

        Chrome replaces a stylesheet whose text changed, the redaction's own edit of a ``<style>`` included, only at its
        next style update, and reports the replacement then, as a removed and an added sheet. On a busy machine that
        update can come after counting begins, and the screenshot would be refused for a change the redaction made.
        Computing the document element's style through DevTools, which page script cannot intercept, runs the update,
        and its events arrive before the reply. The document element is read afresh: the page may have replaced it since
        the roots were collected, and a node id read then would name nothing. The read is whole and pierced, like the
        one in :meth:`start`, because Chrome reports DOM changes only for the nodes it last sent; a shallow read would
        stop it reporting any change below the document element.
        """
        document = await self._cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
        children = document.get("root", {}).get("children", [])
        element = next((int(child["nodeId"]) for child in children if child.get("nodeType") == 1), None)
        if element is not None:
            await self._cdp.send("CSS.getComputedStyleForNode", {"nodeId": element})

    def _handler(self, method: str) -> Any:
        def handle(params: Mapping[str, Any]) -> None:
            if method == "CSS.styleSheetAdded":
                self._sheets.add(str(params.get("header", {}).get("styleSheetId", "")))
            elif method == "CSS.styleSheetRemoved":
                self._sheets.discard(str(params.get("styleSheetId", "")))
            if not self._counting:
                return
            if method in _STYLE_ATTRIBUTE_EVENTS and params.get("name") == "style":
                return
            self.count += 1

        return handle

    async def sheets_hold(self, values: Iterable[str]) -> bool:
        """Whether any stylesheet's text holds a value; an unreadable sheet counts as holding one."""
        matcher = ValueMatcher(values)
        for sheet in sorted(self._sheets):
            try:
                reply = await self._cdp.send("CSS.getStyleSheetText", {"styleSheetId": sheet})
            except Exception:
                return True
            if matcher.holds(str(reply.get("text", ""))):
                return True
        return False

    def begin(self) -> None:
        """Count from now on."""
        self._counting = True

    def end(self) -> None:
        """Stop counting, keeping the count."""
        self._counting = False

    async def close(self) -> None:
        """Stop counting, unsubscribe and disable the domains; each step is attempted."""
        self.end()
        for method, handler in self._handlers:
            with contextlib.suppress(Exception):
                self._cdp.remove_listener(method, handler)
        self._handlers.clear()
        for method in ("CSS.disable", "DOM.disable"):
            with contextlib.suppress(Exception):
                await self._cdp.send(method)


async def _resolved(cdp: Any, backend_node_ids: list[int]) -> list[dict[str, Any]]:
    """Call arguments naming the nodes DevTools identifies by backend node id."""
    arguments = []
    for backend_node_id in backend_node_ids:
        resolved = await cdp.send("DOM.resolveNode", {"backendNodeId": backend_node_id, "objectGroup": _OBJECT_GROUP})
        arguments.append({"objectId": resolved["object"]["objectId"]})
    return arguments


async def end_view_transitions(cdp: Any) -> bool:
    """End every running view transition and wait for each to finish; whether any was running.

    A view transition draws a raster of its scope's old state, which no redaction reaches. It runs on the
    document or on any element, including one in a closed shadow root, which only DevTools can hand the page.
    """
    tree = await cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
    roots = await _resolved(cdp, closed_shadow_roots(tree.get("root", {})))
    document = await cdp.send("Runtime.evaluate", {"expression": "document", "objectGroup": _OBJECT_GROUP})
    reply = await cdp.send(
        "Runtime.callFunctionOn",
        {
            "objectId": document["result"]["objectId"],
            "functionDeclaration": page_js.END_VIEW_TRANSITIONS_JS,
            "arguments": roots,
            "awaitPromise": True,
            "returnByValue": True,
            "objectGroup": _OBJECT_GROUP,
        },
    )
    ended = reply.get("result", {}).get("value") is True
    # Their pseudo-elements go at a later rendering update, which DevTools reports as a page change; wait until the
    # document no longer holds them, so the removal lands before anything is counted. The page's own frame callbacks
    # are not used, because page script can replace them.
    while ended:
        document_tree = await cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
        if not view_transition_pseudo_elements(document_tree.get("root", {})):
            break
        await asyncio.sleep(_VIEW_TRANSITION_SETTLE_POLL_SECONDS)
    return ended


class PageController:
    """The in-page controller, held and called through a DevTools session."""

    def __init__(self, cdp: Any, object_id: str) -> None:
        self._cdp = cdp
        self._object_id = object_id

    @classmethod
    async def create(cls, cdp: Any, values: list[str]) -> PageController:
        """Build the controller in the page's main world."""
        document = await cdp.send("Runtime.evaluate", {"expression": "document", "objectGroup": _OBJECT_GROUP})
        created = await cdp.send(
            "Runtime.callFunctionOn",
            {
                "objectId": document["result"]["objectId"],
                "functionDeclaration": page_js.CONTROLLER_JS,
                "arguments": [{"value": page_js.controller_argument(values)}],
                "objectGroup": _OBJECT_GROUP,
            },
        )
        if "exceptionDetails" in created:
            raise RuntimeError("the page controller could not be created")
        return cls(cdp, created["result"]["objectId"])

    async def _call(self, method: str, arguments: list[dict[str, Any]] | None = None) -> Any:
        reply = await self._cdp.send(
            "Runtime.callFunctionOn",
            {
                "objectId": self._object_id,
                "functionDeclaration": f"function(...args) {{ return this.{method}(...args); }}",
                "arguments": arguments or [],
                "returnByValue": True,
                "objectGroup": _OBJECT_GROUP,
            },
        )
        if "exceptionDetails" in reply:
            raise RuntimeError(f"the page controller's {method} failed")
        return reply.get("result", {}).get("value")

    async def redact(self, closed_roots: list[int]) -> None:
        """Redact the document, its open shadow roots and the given closed ones."""
        await self._call("redact", await _resolved(self._cdp, closed_roots))

    async def watch(self, latent: bool) -> None:
        await self._call("watch", [{"value": latent}])

    async def verify(self) -> dict[str, Any]:
        report = await self._call("verify")
        return report if isinstance(report, dict) else {}

    async def restore(self) -> None:
        await self._call("restore")

    async def dispose(self) -> None:
        with contextlib.suppress(Exception):
            await self._cdp.send("Runtime.releaseObjectGroup", {"objectGroup": _OBJECT_GROUP})
