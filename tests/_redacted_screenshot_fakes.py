# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The fake page, DevTools session and session the redacted-screenshot tests drive.

Shared by ``test_macro_redacted_screenshot.py`` and the modules that pin the rest of the
protocol (refusal messages, settle and retry, drawn transitions, artifact runs).
"""

from __future__ import annotations

import base64
import contextlib
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from octowright.macros import animation_freeze, safe_screenshot
from octowright.macros import redaction_page_js as page_js
from octowright.macros.redaction_text import JS_IGNORABLE_CLASS
from octowright.macros.rendered_surface import SNAPSHOT_PARAMS

PASSWORD = "B7-REDACT-PASSWORD-CANARY-4c2e"  # pragma: allowlist secret
EMAIL = "b7-redact-canary@example.test"
POLICY_ENV = "OCTOWRIGHT_MACRO_CLASSIFIED_SCREENSHOTS"

CLEAN: dict[str, Any] = {"changed": 0, "remaining": 0, "transitioning": 0}
CLEAN_SNAPSHOT: dict[str, Any] = {"strings": [], "documents": []}
LEAKING_SNAPSHOT: dict[str, Any] = {"strings": [PASSWORD], "documents": [{"nodes": {}, "layout": {"text": [0]}}]}
PNG = b"\x89PNG-redacted-canary-bytes"
TAKEN = [
    "animations:0",
    "view_transitions",
    "redact",
    "watch",
    "verify",
    "snapshot",
    "screenshot",
    "verify",
    "snapshot",
    "restore",
    "animations:1",
    "detach",
]
_QUIET_METHODS = {"Animation.enable", "Animation.disable", "DOM.enable", "DOM.disable", "CSS.enable", "CSS.disable"}


def _refused(*steps: str) -> list[str]:
    """The protocol of a refusal: animations paused, view transitions ended, the steps taken, then restore and release."""
    return ["animations:0", "view_transitions", *steps, "restore", "animations:1", "detach"]


class FakeCDP:
    """A DevTools session over a fake page: the controller's calls, DevTools events and captures."""

    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.handlers: dict[str, list[Callable[[dict[str, Any]], None]]] = {}

    def on(self, method: str, handler: Callable[[dict[str, Any]], None]) -> None:
        self.handlers.setdefault(method, []).append(handler)

    def remove_listener(self, method: str, handler: Callable[[dict[str, Any]], None]) -> None:
        self.handlers[method].remove(handler)

    def emit(self, method: str, params: dict[str, Any]) -> None:
        for handler in list(self.handlers.get(method, [])):
            handler(params)

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        page = self.page
        if method in _QUIET_METHODS:
            assert params is None
            return {}
        if method == "Animation.setPlaybackRate":
            assert params is not None
            rate = params["playbackRate"]
            if rate == 0 and page.pause_error is not None:
                raise page.pause_error
            page.calls.append(f"animations:{rate}")
            if rate == 1 and page.resume_error is not None:
                raise page.resume_error
            return {}
        assert params is not None, method
        handler = getattr(self, "_" + method.replace(".", "_"))
        result: dict[str, Any] = await handler(params)
        return result

    async def _DOM_getDocument(self, params: dict[str, Any]) -> dict[str, Any]:
        assert params == {"depth": -1, "pierce": True}
        # CSS.enable announces existing sheets once; later pierced reads (view-transition checks) announce nothing.
        if self.handlers.get("CSS.styleSheetAdded") and not getattr(self, "sheets_announced", False):
            self.sheets_announced = True
            for sheet_id in self.page.sheets:
                self.emit("CSS.styleSheetAdded", {"header": {"styleSheetId": sheet_id}})
        return {"root": self.page.document}

    async def _DOM_resolveNode(self, params: dict[str, Any]) -> dict[str, Any]:
        self.page.resolved.append(params["backendNodeId"])
        return {"object": {"objectId": f"root-{params['backendNodeId']}"}}

    async def _CSS_getStyleSheetText(self, params: dict[str, Any]) -> dict[str, Any]:
        # Which sheet was read, and how many style updates came before the read.
        self.page.sheet_reads.append((params["styleSheetId"], len(self.page.style_updates)))
        text = self.page.sheets[params["styleSheetId"]]
        if text is None:
            raise RuntimeError("No style sheet with given id found")
        return {"text": text}

    async def _CSS_getComputedStyleForNode(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.page.style_error is not None:
            raise self.page.style_error
        self.page.style_updates.append((params["nodeId"], list(self.page.calls)))
        return {"computedStyle": []}

    async def _Runtime_evaluate(self, params: dict[str, Any]) -> dict[str, Any]:
        page = self.page
        if params["expression"] == safe_screenshot._settle_expression():
            # Rendering settles: what the page had pending lands now, before anything is counted.
            assert params["awaitPromise"] is True and params["returnByValue"] is True
            page.settles.append(list(page.calls))
            for method, event in page.events.pop("settle", ()):
                self.emit(method, event)
            if page.settle_error is not None:
                raise page.settle_error
            return page.settle_reply
        assert params["expression"] == "document"
        return {"result": {"objectId": "document"}}

    async def _Runtime_releaseObjectGroup(self, params: dict[str, Any]) -> dict[str, Any]:
        # The animation freeze releases its own group; only the controller's counts here.
        if params["objectGroup"] != animation_freeze.OBJECT_GROUP:
            self.page.released = True
        return {}

    async def _Runtime_callFunctionOn(self, params: dict[str, Any]) -> dict[str, Any]:
        page = self.page
        if params["functionDeclaration"] == animation_freeze.PENDING_JS:
            # No animation is pending on the fake page, so nothing is noted or repaired.
            assert params["objectId"] == "document" and params["returnByValue"] is False
            return {"result": {"type": "object", "subtype": "null", "value": None}}
        if params["functionDeclaration"] == page_js.END_VIEW_TRANSITIONS_JS:
            assert params["objectId"] == "document" and params["awaitPromise"] is True
            if getattr(page, "end_error", None) is not None:
                raise page.end_error
            page.calls.append("view_transitions")
            page.ended_roots = [argument["objectId"] for argument in params["arguments"]]
            return {"result": {"value": False}}
        if params["functionDeclaration"] == page_js.CONTROLLER_JS:
            argument = params["arguments"][0]["value"]
            assert argument["ignorable"] == JS_IGNORABLE_CLASS
            page.redacted_values = list(argument["values"])
            return {"result": {"objectId": "controller"}}
        assert params["objectId"] == "controller"
        call = params["functionDeclaration"].split("this.", 1)[1].split("(", 1)[0]
        page.calls.append(call)
        for method, event in page.events.pop(call, ()):
            self.emit(method, event)
        if call == "redact":
            page.redact_arguments = params["arguments"]
            if page.redact_error is not None:
                raise page.redact_error
        elif call == "watch":
            page.latent = params["arguments"][0]["value"]
        elif call == "verify":
            return {"result": {"value": page.reports.pop(0) if page.reports else dict(CLEAN)}}
        elif call == "restore" and page.restore_error is not None:
            raise page.restore_error
        return {"result": {}}

    async def _DOMSnapshot_captureSnapshot(self, params: dict[str, Any]) -> dict[str, Any]:
        assert params == SNAPSHOT_PARAMS
        self.page.calls.append("snapshot")
        return self.page.snapshots.pop(0) if self.page.snapshots else CLEAN_SNAPSHOT

    async def _Page_captureScreenshot(self, params: dict[str, Any]) -> dict[str, Any]:
        page = self.page
        assert params == {"format": page.image_format}
        page.calls.append("screenshot")
        events = page.events.get("screenshot", ()) if page.repeat_capture_events else page.events.pop("screenshot", ())
        for method, event in events:
            self.emit(method, event)
        if page.capture_error is not None:
            raise page.capture_error
        image = page.capture_images.pop(0) if page.capture_images else PNG
        return {"data": base64.b64encode(image).decode()}

    async def detach(self) -> None:
        self.page.calls.append("detach")
        assert not any(self.handlers.values()), "every DevTools listener is removed before detaching"


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self.page = page

    async def new_cdp_session(self, _page: object) -> FakeCDP:
        if not self.page.chromium:
            raise RuntimeError("CDP session is only available in Chromium")
        return FakeCDP(self.page)


class FakePage:
    """Records the redaction protocol in order: redact, watch, verify, snapshot, screenshot, ..., restore."""

    def __init__(
        self,
        *,
        reports: tuple[dict[str, Any], ...] = (),
        snapshots: tuple[dict[str, Any], ...] = (),
        events: dict[str, tuple[tuple[str, dict[str, Any]], ...]] | None = None,
        sheets: dict[str, str | None] | None = None,
        document: dict[str, Any] | None = None,
        redact_error: BaseException | None = None,
        restore_error: BaseException | None = None,
        style_error: Exception | None = None,
        capture_error: Exception | None = None,
        pause_error: Exception | None = None,
        resume_error: Exception | None = None,
        chromium: bool = True,
        image_format: str = "png",
        settle_reply: dict[str, Any] | None = None,
        settle_error: Exception | None = None,
        capture_images: tuple[bytes, ...] = (),
        repeat_capture_events: bool = False,
    ) -> None:
        self.calls: list[str] = []
        self.reports = list(reports)
        self.snapshots = list(snapshots)
        self.events = dict(events or {})
        self.sheets = dict(sheets or {})
        self.document = document or {}
        self.redact_error = redact_error
        self.restore_error = restore_error
        self.style_error = style_error
        self.capture_error = capture_error
        self.pause_error = pause_error
        self.resume_error = resume_error
        self.chromium = chromium
        self.image_format = image_format
        self.settle_reply = settle_reply or {"result": {"value": True}}
        self.settle_error = settle_error
        self.settles: list[list[str]] = []
        self.capture_images = list(capture_images)
        self.repeat_capture_events = repeat_capture_events
        self.redacted_values: list[str] | None = None
        self.redact_arguments: list[dict[str, Any]] | None = None
        self.resolved: list[int] = []
        self.style_updates: list[tuple[int, list[str]]] = []
        self.sheet_reads: list[tuple[str, int]] = []
        self.ended_roots: list[str] | None = None
        self.latent: bool | None = None
        self.released = False
        self.context = FakeContext(self)


def _session(page: FakePage) -> MagicMock:
    session = MagicMock()
    session.page = page
    # Playwright's screenshot helper writes styles onto the page; the redacted path must not use it.
    session.screenshot = AsyncMock(side_effect=AssertionError("session.screenshot must not be used"))

    @contextlib.asynccontextmanager
    async def operation(_name: str) -> AsyncIterator[None]:
        yield

    session.operation = operation
    # A MagicMock answers every attribute; the guard must see only what was installed.
    session._octowright_sensitive_screenshot_authority = None
    session._octowright_sensitive_screenshot_handler = None
    return session


async def _shoot(page: FakePage, path: Path) -> tuple[int, int]:
    return await safe_screenshot.redacted_screenshot(
        _session(page), {"action": "screenshot", "path": str(path)}, (PASSWORD,)
    )
