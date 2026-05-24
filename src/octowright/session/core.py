# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Video
from provide.telemetry import bind_context, unbind_context

from octowright.browser_pool.viewport import ViewportMode
from octowright.defaults import NETWORK_EVENT_LIMIT
from octowright.recorder import Recorder
from octowright.session.core_interaction_mixin import SessionInteractionMixin
from octowright.session.core_io_mixin import SessionIOMixin
from octowright.session.core_locator_mixin import SessionLocatorMixin
from octowright.session.core_network_mixin import SessionNetworkMixin
from octowright.session.core_ops_mixin import SessionOpsMixin
from octowright.session.core_page_mixin import SessionPageMixin

# Public constant re-exported via session.__init__ and used by server/browser tools.
DEFAULT_PREVIEW_CHARS = 4000


@dataclass
class BrowserSession(
    SessionIOMixin,
    SessionPageMixin,
    SessionOpsMixin,
    SessionNetworkMixin,
    SessionInteractionMixin,
    SessionLocatorMixin,
):
    instance_id: str
    kind: str
    label: str | None
    url: str
    browser: Browser | None
    context: BrowserContext
    page: Page
    recorder: Recorder
    log_path: Path
    user_data_dir: Path | None = None
    profile: str | None = None
    stabilize: bool = False
    trace: bool = False
    har_path: Path | None = None
    viewport_mode: str = ViewportMode.UNKNOWN.value
    viewport_width: int | None = None
    viewport_height: int | None = None
    console: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=1000))
    video_path: Path | None = None
    trace_path: Path | None = None
    _video: Video | None = field(default=None, repr=False)
    _browser_for_close: Browser | None = field(default=None, repr=False)
    pages: list[Page] = field(default_factory=list)
    _dialog_policy: str = "dismiss"
    _dialog_prompt_text: str | None = None
    _active_routes: dict[str, Any] = field(default_factory=dict)
    active_frame: Any | None = None  # playwright.async_api.Frame when set
    downloads: list[dict[str, Any]] = field(default_factory=list)
    _pending_download_events: list[Any] = field(default_factory=list)
    _bg_tasks: set[Any] = field(default_factory=set, repr=False)
    markdown_path: Path | None = None
    _last_markdown_capture_url: str | None = None
    _last_markdown_capture_key: str | None = None
    _pending_markdown_capture: Any | None = None
    websocket_path: Path | None = None
    # Lazy-opened append handle for high-frequency WS feeds; typed as Any
    # because Path.open("a", encoding="utf-8") returns TextIOWrapper while
    # the protocol surface only needs write/flush/close.
    _websocket_fh: Any = field(default=None, repr=False)
    # Batched-flush bookkeeping for the WS cache: count frames since the
    # last flush and remember when we last flushed so we can flush when
    # either threshold (count OR elapsed) is hit. See defaults.WEBSOCKET_
    # CACHE_FLUSH_FRAMES / SECONDS for the policy.
    _websocket_frames_since_flush: int = field(default=0, repr=False)
    _websocket_last_flush_ts: float = field(default=0.0, repr=False)
    _network_requests: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=NETWORK_EVENT_LIMIT))
    _network_requests_dropped: int = 0
    _last_mcp_navigation: str | None = None
    _on_page_close: Callable[..., None] | None = field(default=None, repr=False)
    _make_framenavigated_handler: Callable[[Any], Any] | None = field(default=None, repr=False)
    console_count: int = 0
    download_count: int = 0
    page_count: int = 1
    started_at: str = ""

    def __post_init__(self) -> None:
        if self._browser_for_close is None and self.browser is not None:
            self._browser_for_close = self.browser
        if self.page not in self.pages:
            self.pages.insert(0, self.page)
        self.page_count = len(self.pages)
        if not self.started_at:
            from datetime import UTC, datetime

            self.started_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        # Bind session context so every log line emitted while this session
        # is alive auto-carries the per-browser identifiers. unbind happens
        # in close(). Context is thread-local — fine for asyncio's single-
        # loop model; callers in worker threads (asyncio.to_thread workers)
        # see the parent loop's binding via structlog's contextvars copy.
        try:
            bind_context(
                octowright_instance_id=self.instance_id,
                octowright_kind=self.kind,
                octowright_profile=self.profile,
                octowright_label=self.label,
            )
        except Exception:
            # Telemetry must never break session creation.
            pass

    def unbind_telemetry_context(self) -> None:
        try:
            unbind_context(
                "octowright_instance_id",
                "octowright_kind",
                "octowright_profile",
                "octowright_label",
            )
        except Exception:
            pass

    def _target(self) -> Any:
        return self.active_frame if self.active_frame is not None else self.page

    def _markdown_cache_path(self) -> Path:
        return self.log_path.with_suffix(".markdown.md")

    def _websocket_cache_path(self) -> Path:
        return self.log_path.with_suffix(".websocket.jsonl")
