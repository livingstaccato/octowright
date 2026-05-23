# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any, Protocol

from playwright.async_api import Browser, BrowserContext, Page, Video

from octowright.recorder import Recorder


class SessionLike(Protocol):
    instance_id: str
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
