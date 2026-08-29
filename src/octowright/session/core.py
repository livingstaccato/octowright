# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import secrets
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, LiteralString
from weakref import WeakSet

from playwright.async_api import Browser, BrowserContext, Page, Video

from octowright.defaults import NETWORK_EVENT_LIMIT
from octowright.recorder import Recorder
from octowright.session._constants import DEFAULT_PREVIEW_CHARS
from octowright.session.core_expect_mixin import SessionExpectMixin
from octowright.session.core_interaction_mixin import SessionInteractionMixin
from octowright.session.core_io_mixin import SessionIOMixin
from octowright.session.core_locator_mixin import SessionLocatorMixin
from octowright.session.core_network_mixin import SessionNetworkMixin
from octowright.session.core_ops_mixin import SessionOpsMixin
from octowright.session.core_page_mixin import SessionPageMixin
from octowright.session.operation_gate import (
    USE_DEFAULT,
    OperationGateSnapshot,
    SessionOperationGate,
    UseDefault,
    resolve_operation_queue_timeout_seconds,
)

# ``DEFAULT_PREVIEW_CHARS`` is the public preview cap, re-exported via
# ``session.__init__`` and used by server/browser tools. Defined in
# ``session._constants`` so mixin modules can import the same singleton
# without forming an import cycle with this module.
__all__ = ["DEFAULT_PREVIEW_CHARS", "BrowserSession"]

# Default for ``BrowserSession.viewport_mode`` — kept as a string literal
# (rather than ``ViewportMode.UNKNOWN.value``) to avoid importing
# ``octowright.browser_pool.viewport`` here. That import would trigger
# ``browser_pool/__init__`` → ``pool`` → ``launch_pipeline`` → ``listeners``,
# all of which import ``BrowserSession`` back from this module, closing a
# circular-import cycle when ``octowright.session`` is loaded first (the
# failure mode when a single ``test_session_*`` file is run in isolation).
# The string must stay in sync with ``ViewportMode.UNKNOWN`` in
# ``octowright.browser_pool.viewport``; a ``TYPE_CHECKING`` guarded import
# below pins the relationship for type checkers and human readers.
_VIEWPORT_MODE_UNKNOWN = "unknown"

if TYPE_CHECKING:  # pragma: no cover - import-time-only assertion
    from octowright.browser_pool.viewport import ViewportMode as _ViewportMode

    # Compile-time assertion that the literal default still matches the enum.
    _: str = _ViewportMode.UNKNOWN.value


@dataclass
class BrowserSession(
    SessionIOMixin,
    SessionExpectMixin,
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
    protected: bool = False
    protected_reason: str = "explicit"
    trace: bool = False
    har_path: Path | None = None
    viewport_mode: str = _VIEWPORT_MODE_UNKNOWN
    viewport_width: int | None = None
    viewport_height: int | None = None
    # Browser chrome around the content area, in CSS pixels, measured once at
    # launch: ``outerWidth - innerWidth`` and ``outerHeight - innerHeight``.
    #
    # It is a launch-time measurement because that is the only moment the
    # numbers are trustworthy. Playwright welds the OS window to a fixed
    # viewport — it resizes the window so the content area matches, and
    # re-welds on every set_viewport_size — so at launch the difference
    # between the window and the viewport IS the chrome and nothing else.
    # Later it may also contain drift (a tiling WM, or a maximise the
    # emulated viewport did not follow), which is exactly what
    # ``viewport_status`` exists to report; without a baseline captured now,
    # there is nothing to subtract and the drift stays invisible.
    #
    # None means "not measured" — a page that could not be evaluated at
    # launch. Callers must treat None as unknown and decline to warn rather
    # than guess: a hardcoded chrome allowance is what made ``mismatch`` fire
    # on every headed session (chrome is ~85px tall on Linux/Wayland, over the
    # old 80px bar), and a permanent warning cannot warn.
    viewport_frame_inset_w: int | None = None
    viewport_frame_inset_h: int | None = None
    # Per-launch capability token the viewport pill's init script must present
    # on every ``__octowright_viewport_action`` binding call. The binding is
    # installed on ``window`` for every frame of every page in the context
    # (Playwright's ``expose_binding`` has no notion of caller identity), so
    # without this a hostile/compromised page could call it directly and, via
    # ``relaunch-fluid``, force a ``protected`` browser closed. The token is
    # generated once per launch, spliced into the init script text (never
    # assigned to ``window`` -- see ``viewport_pill.js``'s ``VIEWPORT_TOKEN``
    # closure const), and checked with a constant-time compare in
    # ``BrowserPool._expose_viewport_binding``. ``repr=False`` keeps it out of
    # any ``repr(session)``/logging that might stringify the dataclass.
    viewport_action_token: str = field(default_factory=lambda: secrets.token_urlsafe(24), repr=False)
    console: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=1000))
    video_path: Path | None = None
    trace_path: Path | None = None
    _video: Video | None = field(default=None, repr=False)
    _browser_for_close: Browser | None = field(default=None, repr=False)
    pages: list[Page] = field(default_factory=list)
    _dialog_policy: str = "dismiss"
    _dialog_prompt_text: str | None = None
    _active_routes: dict[str, Any] = field(default_factory=dict)
    #: Header-injection route handlers, keyed by url_pattern. Kept separate
    #: from _active_routes so a mock and an injector can share a pattern
    #: without one silently evicting the other's handler from the registry.
    _header_routes: dict[str, Any] = field(default_factory=dict)
    #: The headers each _header_routes entry merges in, same keys. Stored
    #: because the registry above holds the route CLOSURE, from which the
    #: headers cannot be recovered -- so nothing could report what an injector
    #: was actually adding.
    _injected_headers: dict[str, dict[str, str]] = field(default_factory=dict)
    #: Launch-time context-level headers, as handed to new_context(). Playwright
    #: exposes no getter, so without this copy a browser could not say what it
    #: was sending -- which left an adopting client guessing whether a running
    #: browser carried the current run's tag or a stale one.
    extra_http_headers: dict[str, str] | None = None
    #: URL globs scoping the above, when it was installed as scoped routes
    #: instead of unscoped context headers. Reported alongside, because scoped
    #: headers do not ride every request and the headers alone overstate reach.
    extra_http_headers_urls: list[str] | None = None
    #: Headers set on the ACTIVE page by set_extra_http_headers. Per page by
    #: nature, so this tracks the page it was last applied to rather than
    #: claiming browser-wide scope.
    _page_extra_headers: dict[str, str] | None = None
    active_frame: Any | None = None  # playwright.async_api.Frame when set
    downloads: list[dict[str, Any]] = field(default_factory=list)
    _pending_download_events: list[Any] = field(default_factory=list)
    _bg_tasks: set[Any] = field(default_factory=set, repr=False)
    # Pages already wired by _wire_listeners, keyed by identity, so crash
    # recovery re-wiring a page the context "page" event already wired is a
    # no-op instead of a duplicate-handler bug. WeakSet: a closed page drops out.
    _wired_pages: WeakSet[Page] = field(default_factory=WeakSet, repr=False)
    markdown_path: Path | None = None
    _last_markdown_capture_url: str | None = None
    _last_markdown_capture_key: str | None = None
    _pending_markdown_capture: Any | None = None
    #: Set by capture_markdown() on every attempt: None on success, the
    #: caught exception on failure. capture_markdown() swallows its own
    #: exceptions and returns None on failure, so a caller that gets None
    #: back (browser_read_markdown, capture_create(source="markdown")) reads
    #: this to tell a hung target (SessionCallTimeoutError) apart from an
    #: ordinary rendering failure -- reporting the former as "ensure
    #: markitdown is installed" would be actively misleading.
    _last_markdown_capture_error: Exception | None = None
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
    # WS sidecar byte accounting for the OCTOWRIGHT_WEBSOCKET_MAX_BYTES ceiling
    # (off by default). Once _websocket_truncated flips, no more frames append.
    _websocket_bytes: int = field(default=0, repr=False)
    _websocket_truncated: bool = field(default=False, repr=False)
    _network_requests: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=NETWORK_EVENT_LIMIT))
    _network_requests_dropped: int = 0
    _last_mcp_navigation: str | None = None
    _on_page_close: Callable[..., None] | None = field(default=None, repr=False)
    _on_page_crash: Callable[..., None] | None = field(default=None, repr=False)
    _make_framenavigated_handler: Callable[[Any], Any] | None = field(default=None, repr=False)
    # Installed by ``launch_pipeline._build_session_object`` before registry
    # publication so ``BrowserSession.close()`` routes through the pool's
    # durable close coordinator instead of tearing down directly. ``None`` for
    # a session constructed outside a pool (tests only -- see
    # ``SessionOpsMixin.close``'s standalone fallback), which then owns its
    # own reservation via ``_standalone_close_task``.
    _pool_close_requester: Callable[[], Awaitable[Any]] | None = field(default=None, repr=False)
    _standalone_close_task: asyncio.Task[None] | None = field(default=None, repr=False)
    # Set True by the page.on("crash") listener; lets eviction report a definite
    # crash (reason="crashed") instead of an ambiguous external close. Cleared
    # again when crash_recovery successfully reloads the page.
    _crashed: bool = field(default=False, repr=False)
    # Auto-recovery bookkeeping (browser_pool.crash_recovery): count of reload
    # attempts and the monotonic time of the last crash, used to bound recovery
    # and detect crash loops vs occasional crashes.
    _crash_recoveries: int = field(default=0, repr=False)
    _last_crash_monotonic: float = field(default=0.0, repr=False)
    console_count: int = 0
    download_count: int = 0
    page_count: int = 1
    started_at: str = ""
    operation_queue_timeout_seconds: float | None = field(default=None, repr=False)
    _operation_gate: SessionOperationGate = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._operation_gate = SessionOperationGate(
            self.instance_id,
            self.kind,
            queue_timeout_seconds=resolve_operation_queue_timeout_seconds(self.operation_queue_timeout_seconds),
        )
        if self._browser_for_close is None and self.browser is not None:
            self._browser_for_close = self.browser
        if self.page not in self.pages:
            self.pages.insert(0, self.page)
        self.page_count = len(self.pages)
        if not self.started_at:
            from datetime import UTC, datetime

            self.started_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _target(self) -> Any:
        return self.active_frame if self.active_frame is not None else self.page

    def _markdown_cache_path(self) -> Path:
        return self.log_path.with_suffix(".markdown.md")

    def _websocket_cache_path(self) -> Path:
        return self.log_path.with_suffix(".websocket.jsonl")

    def operation(
        self,
        operation_name: LiteralString,
        *,
        wait_timeout_seconds: float | UseDefault | None = USE_DEFAULT,
    ) -> AbstractAsyncContextManager[None]:
        return self._operation_gate.operation(operation_name, wait_timeout_seconds=wait_timeout_seconds)

    def operation_snapshot(self) -> OperationGateSnapshot:
        return self._operation_gate.snapshot()

    async def set_protected_state(
        self,
        protected: bool,
        *,
        reason: str = "explicit",
    ) -> dict[str, object]:
        def _commit() -> dict[str, object]:
            self.protected = protected
            self.protected_reason = reason
            return {"instance_id": self.instance_id, "protected": protected}

        return await self._operation_gate.control_update("browser_set_protected", _commit)
