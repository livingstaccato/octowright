# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from collections import deque
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any, LiteralString, Protocol

from playwright.async_api import Browser, BrowserContext, Page, Video

from octowright.recorder import Recorder
from octowright.session.operation_gate import USE_DEFAULT, OperationGateSnapshot, UseDefault


class SessionLike(Protocol):
    instance_id: str
    kind: str
    url: str
    page: Page
    pages: list[Page]
    recorder: Recorder
    log_path: Path
    console: deque[dict[str, Any]]
    console_count: int
    active_frame: Any | None
    downloads: list[dict[str, Any]]
    download_count: int
    page_count: int
    _bg_tasks: set[Any]
    _dialog_policy: str
    _dialog_prompt_text: str | None
    _active_routes: dict[str, Any]
    _network_requests: deque[dict[str, Any]]
    _network_requests_dropped: int
    trace: bool
    trace_path: Path | None
    har_path: Path | None
    viewport_mode: str
    viewport_width: int | None
    viewport_height: int | None
    video_path: Path | None
    markdown_path: Path | None
    websocket_path: Path | None
    _websocket_fh: Any
    _websocket_frames_since_flush: int
    _websocket_last_flush_ts: float
    context: BrowserContext
    browser: Browser | None
    _video: Video | None
    _last_mcp_navigation: str | None
    _last_markdown_capture_url: str | None
    _last_markdown_capture_key: str | None
    _pending_markdown_capture: Any | None

    def _target(self) -> Any: ...

    def _schedule_markdown_capture(self, page: Page | None = None, force: bool = False) -> None: ...

    def _markdown_cache_path(self) -> Path: ...

    def _websocket_cache_path(self) -> Path: ...

    # Implemented on SessionExpectMixin; declared here so SessionPageMixin's
    # wait_for (which calls self._poll_until for its text/expression
    # branches) type-checks in isolation despite the two mixins only being
    # combined together on BrowserSession.
    async def _poll_until(self, timeout_ms: int, predicate: Any, label: str) -> None: ...

    # Action methods accessed directly (not via getattr) by the macro
    # dispatcher. The remaining action methods (navigate, type_text,
    # press_key, etc.) are looked up dynamically through ``_ACTION_MAP``
    # so they don't need a declared signature here.
    async def click(self, selector: str) -> None: ...

    async def fill(self, selector: str, value: str) -> None: ...

    async def list_pages(self) -> list[dict[str, Any]]: ...

    async def list_frames(self) -> list[dict[str, Any]]: ...

    async def set_dialog_policy(self, policy: str, prompt_text: str | None = None) -> dict[str, Any]: ...

    async def diagnostic_bundle(
        self,
        *,
        screenshot_dir: Path | None = None,
        console_tail: int = 0,
        html_preview_chars: int = 0,
        html_full: bool = False,
    ) -> dict[str, Any]: ...

    async def snapshot(self) -> dict[str, Any]: ...

    def operation(
        self,
        operation_name: LiteralString,
        *,
        wait_timeout_seconds: float | None | UseDefault = USE_DEFAULT,
    ) -> AbstractAsyncContextManager[None]: ...

    def operation_snapshot(self) -> OperationGateSnapshot: ...

    async def set_protected_state(
        self,
        protected: bool,
        *,
        reason: str = "explicit",
    ) -> dict[str, object]: ...
