# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from playwright.async_api import Playwright, async_playwright
from provide.telemetry import get_logger

from octowright.browser_pool import driver_health, driver_relaunch
from octowright.browser_pool._metrics import launch_span
from octowright.browser_pool.cleanup import cleanup_on_launch_failure
from octowright.browser_pool.events import SessionCloseReason
from octowright.browser_pool.launch_execution import launch_profile_locked
from octowright.browser_pool.lifecycle import (
    ClosingSession,
    accept_external_close_nowait,
    close_browser,
    shutdown_pool,
)
from octowright.browser_pool.options import LaunchOptions
from octowright.browser_pool.relaunch import handoff_browser, relaunch_fluid_browser
from octowright.browser_pool.roster import close_all as _close_all
from octowright.browser_pool.roster import spawn_roster as _spawn_roster
from octowright.browser_pool.session_dirs import SESSION_TMPDIR_PREFIX
from octowright.browser_pool.visuals import _tile_args_for_chromium
from octowright.defaults import RECORDINGS_DIR, get_default_url
from octowright.profile_lifecycle import profile_lifecycle_lock, profile_names_match
from octowright.session import BrowserSession
from octowright.session.operation_gate import (
    SessionClosedError,
    SessionClosingError,
    resolve_operation_queue_timeout_seconds,
)

log = get_logger(__name__)

_safe_cleanup_on_launch_failure = cleanup_on_launch_failure


class BrowserPool:
    """Owns a single Playwright driver and a dict of active BrowserSession objects.

    One playwright instance is shared across all sessions; each session gets its own
    Browser, BrowserContext, and Page.
    """

    # Cap on how many externally-evicted instance ids we remember for the
    # "relaunch" hint in get(); bounded so a long-lived pool can't leak.
    _RECENTLY_EVICTED_CAP = 64

    def __init__(
        self,
        *,
        recordings_dir: Path | None = None,
        operation_queue_timeout_seconds: float | None = None,
    ) -> None:
        # Per-pool artefact write root (custom root = write-side only); see CLAUDE.md.
        self._recordings_dir = (recordings_dir if recordings_dir is not None else RECORDINGS_DIR).expanduser()
        # Resolved once here (explicit arg > OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS
        # > default) and handed to every session this pool launches, so all
        # sessions in one pool share one effective queue timeout instead of each
        # re-reading the env var at construction time.
        self._operation_queue_timeout_seconds = resolve_operation_queue_timeout_seconds(operation_queue_timeout_seconds)
        self._pw: Playwright | None = None
        self._pw_lock = asyncio.Lock()
        # Count of shared-driver rebuilds after a death (surfaced in status).
        self._driver_restarts: int = 0
        self._sessions: dict[str, BrowserSession] = {}
        self._sessions_lock = asyncio.Lock()
        # Sessions mid-teardown: inserted by ``reserve_close_browser``/
        # ``accept_external_close_nowait`` as soon as a close is accepted,
        # retained through teardown + outcome publication (see lifecycle.py).
        # During the drain interval an entry's session is intentionally
        # visible in BOTH this dict and ``_sessions``; once its ticket owns
        # the gate only the ``_sessions`` entry is removed.
        self._closing_sessions: dict[str, ClosingSession] = {}
        # Instance ids dropped via the external close/crash path
        # (_accept_external_close_nowait), insertion-ordered + capped. Maps id -> crashed?
        # (True when a page.on("crash") was seen) so get() can say "crashed" vs a
        # generic "ended unexpectedly", instead of a bare "no such id".
        self._recently_evicted: dict[str, bool] = {}
        # Monotonic counter for window-tile slot assignment. Reading
        # len(_sessions) at launch time would race when N launches run in
        # parallel — they'd all see the same count and grab the same slot.
        # _tile_lock guards the read+increment so concurrent spawn_roster
        # coroutines don't both observe the same slot before either bumps it.
        self._tile_counter: int = 0
        self._tile_lock = asyncio.Lock()
        # session=True profile dirs: tmpdirs that live for the daemon's
        # lifetime. Keyed by (session_key, kind) so the same label across
        # engines gets independent jars (matching real persistent semantics).
        self._session_profile_dirs: dict[tuple[str, str], Path] = {}

    @property
    def recordings_dir(self) -> Path:
        """Root this pool writes per-launch artefacts under (see __init__)."""
        return self._recordings_dir

    @property
    def operation_queue_timeout_seconds(self) -> float:
        """Effective per-session operation-gate queue timeout this pool passes to every launch (see __init__)."""
        return self._operation_queue_timeout_seconds

    async def _ensure_pw(self) -> Playwright:
        async with self._pw_lock:
            if self._pw is None:
                self._pw = await async_playwright().start()
        return self._pw

    async def _reset_driver(self, *, reason: str | None = None) -> None:
        """Discard the shared Playwright driver so the next launch rebuilds it.

        Called when a driver-death error is seen (see ``driver_health``). Best-
        effort ``stop()`` of the dead handle, then clear it under the lock so a
        concurrent ``_ensure_pw`` starts a fresh driver. Hands off to
        ``driver_relaunch.on_driver_reset`` which records the restart incident,
        captures/evicts the sessions lost with the dead driver (surfaced in
        status), and — when OCTOWRIGHT_DRIVER_RELAUNCH is set — reopens them."""
        async with self._pw_lock:
            old = self._pw
            self._pw = None
        self._driver_restarts += 1
        driver_relaunch.on_driver_reset(self, reason=reason)
        if old is not None:
            try:
                await old.stop()
            except Exception as exc:
                log.debug("octowright.pool.driver_stop_failed", error=repr(exc))

    def driver_restart_count(self) -> int:
        """How many times the shared driver has been rebuilt after a death."""
        return self._driver_restarts

    async def launch(self, **options: Any) -> dict[str, Any]:
        async with launch_span(options.get("kind") or "chromium") as sp:
            try:
                return await self._launch_impl(options, sp)
            except Exception as exc:
                # A dead shared driver (its pipe closed) fails every launch until
                # rebuilt. Reset it and retry ONCE — a second failure propagates,
                # so there is no retry loop. Ordinary launch errors are re-raised
                # untouched.
                if not driver_health.is_driver_dead_error(exc):
                    raise
                log.warning("octowright.pool.driver_died_relaunching", error=repr(exc))
                await self._reset_driver(reason=repr(exc))
                return await self._launch_impl(options, sp)

    async def _launch_impl(self, options: dict[str, Any], _sp: Any) -> dict[str, Any]:
        launch_options = LaunchOptions.from_mapping(options)
        kind = launch_options.kind
        # Promote: a named launch (label given, no explicit profile, not ephemeral
        # and not session-scoped) gets a persistent profile by default. The whole
        # reason for naming a browser is so you can come back to it; ephemeral
        # and session are the explicit exceptions.
        profile = launch_options.promoted_profile()
        target_url = launch_options.url or get_default_url()
        # Target validation is a pure preflight. In particular it must happen
        # before session tempdirs, Playwright, or recording files are allocated.
        from octowright.session.core_page_mixin import _reject_unsafe_url

        _reject_unsafe_url(target_url)

        # Deletion takes this same key before its in-use check. Holding it from
        # persona/default-url resolution through registration closes both race
        # windows: delete cannot remove a directory while Playwright opens it,
        # and cannot slip between context creation and pool registration.
        async with profile_lifecycle_lock(kind, profile):
            return await launch_profile_locked(self, launch_options, _sp, profile, target_url)

    def get(self, instance_id: str) -> BrowserSession:
        """Return the live session for ``instance_id``.

        Checks ``_closing_sessions`` FIRST: a session mid-teardown must never
        be handed back as though it were usable, and the caller deserves the
        gate's own terminal-state errors (``SessionClosingError`` while the
        FIFO ticket is still draining, ``SessionClosedError`` once it has
        finished) rather than a generic "no such instance" ``KeyError``.

        Otherwise reads ``_sessions`` without ``_sessions_lock`` — concurrent
        eviction (``accept_external_close_nowait`` in ``lifecycle.py``)
        writes without the lock too, relying on CPython's GIL-atomic
        ``dict.pop``. The returned session may therefore be evicted by a
        Playwright-side close signal between this call and the caller's
        first ``await`` on it; tool handlers in that window will surface as
        Playwright-disconnected errors rather than a clean KeyError. The
        caller is expected to treat both as terminal for the session and
        either close or retry.
        """
        closing = self._closing_sessions.get(instance_id)
        if closing is not None:
            message = (
                f"browser instance_id={instance_id!r} is closing — its teardown is already in "
                "flight; wait for it to finish, or relaunch a new one with browser_launch"
            )
            if closing.session.operation_snapshot()["state"] == "closing":
                raise SessionClosingError(message)
            raise SessionClosedError(message)
        if instance_id not in self._sessions:
            raise KeyError(self._missing_session_message(instance_id))
        return self._sessions[instance_id]

    def _missing_session_message(self, instance_id: str) -> str:
        if instance_id in self._recently_evicted:
            if self._recently_evicted[instance_id]:
                return (
                    f"browser instance_id={instance_id!r} crashed (its process died) — relaunch it with browser_launch"
                )
            return (
                f"browser instance_id={instance_id!r} ended unexpectedly (closed or crashed "
                f"externally) — relaunch it with browser_launch"
            )
        known = list(self._sessions)
        hint = (
            "no browsers are live — call browser_launch first"
            if not known
            else f"call browser_list to see live ids; known: {known}"
        )
        return f"no browser with instance_id={instance_id!r}; {hint}"

    def maybe_get(self, instance_id: str) -> BrowserSession | None:
        return self._sessions.get(instance_id)

    def has_session(self, instance_id: str) -> bool:
        return instance_id in self._sessions

    def iter_sessions(self) -> Iterable[BrowserSession]:
        return tuple(self._sessions.values())

    def active_count(self) -> int:
        return len(self._sessions)

    def protected_count(self) -> int:
        return sum(1 for s in self._sessions.values() if s.protected)

    def list_sessions(self) -> list[dict[str, Any]]:
        # Snapshot values() into a tuple before iterating: Playwright sync
        # close callbacks fire _accept_external_close_nowait between awaits
        # and could otherwise mutate the dict mid-iteration.
        return [
            {
                "instance_id": s.instance_id,
                "kind": s.kind,
                "label": s.label,
                "profile": s.profile,
                "url": s.url,
                "log_path": str(s.log_path),
                "har_path": str(s.har_path) if s.har_path else None,
                "protected": s.protected,
                "operation_gate": s.operation_snapshot(),
            }
            for s in tuple(self._sessions.values())
        ]

    async def _expose_viewport_binding(self, context: Any, session: BrowserSession) -> None:
        expose_binding = getattr(context, "expose_binding", None)
        if expose_binding is None:
            return

        async def _viewport_action(_source: Any, payload: dict[str, Any]) -> dict[str, Any]:
            action = payload.get("action")
            if action == "sync":
                return await session.viewport_sync()
            if action == "relaunch-fluid":
                return await self.relaunch_fluid(session.instance_id)
            if action == "state":
                # The pill's init script is injected once and re-run on every
                # document with the values baked in at LAUNCH, so a mode or
                # chrome change made since -- by resize() or viewport_sync() --
                # is undone by the next navigation. The binding, unlike the
                # script text, is live, so the pill asks instead of trusting
                # what it was born with.
                return {
                    "mode": session.viewport_mode,
                    "width": session.viewport_width,
                    "height": session.viewport_height,
                    "inset_w": session.viewport_frame_inset_w,
                    "inset_h": session.viewport_frame_inset_h,
                }
            raise ValueError(f"unknown viewport action: {action!r}")

        await expose_binding("__octowright_viewport_action", _viewport_action)

    async def relaunch_fluid(self, instance_id: str) -> dict[str, Any]:
        # Body lives in browser_pool.relaunch (Task 8): the URL/profile
        # snapshot used to build the replacement launch must be taken INSIDE
        # the close ticket, after it owns the gate -- see
        # ``relaunch_fluid_browser`` / ``RelaunchSnapshot``.
        return await relaunch_fluid_browser(self, instance_id)

    def profile_in_use(self, kind: str, profile: str) -> bool:
        return any(s.kind == kind and profile_names_match(s.profile, profile) for s in tuple(self._sessions.values()))

    def _accept_external_close_nowait(
        self,
        instance_id: str,
        *,
        expected_session: BrowserSession | None = None,
        reason: SessionCloseReason = "user_close",
    ) -> ClosingSession | None:
        """Synchronous external-close acceptance seam — see
        ``lifecycle.accept_external_close_nowait`` for the full contract.
        Called from sync Playwright event callbacks (page.close,
        context.close, browser.disconnected) and a dead shared driver."""
        return accept_external_close_nowait(self, instance_id, expected_session=expected_session, reason=reason)

    async def close(
        self,
        instance_id: str,
        *,
        force: bool = False,
        _reason: SessionCloseReason = "agent_close",
        _expected_session: BrowserSession | None = None,
    ) -> dict[str, Any]:
        return await close_browser(
            self,
            instance_id,
            force=force,
            _reason=_reason,
            _expected_session=_expected_session,
        )

    async def close_all(
        self,
        *,
        force: bool = False,
        _reason: SessionCloseReason = "agent_close",
    ) -> dict[str, Any]:
        return await _close_all(self, force=force, _reason=_reason)

    async def handoff(
        self,
        old_instance_id: str,
        *,
        headed: bool | None = None,
        close_original: bool = True,
        accept_stateless: bool = False,
    ) -> dict[str, Any]:
        return await handoff_browser(
            self,
            old_instance_id,
            headed=headed,
            close_original=close_original,
            accept_stateless=accept_stateless,
        )

    async def spawn_roster(self, specs: list[dict[str, Any]]) -> dict[str, Any]:
        """Launch N browsers concurrently from a list of launch spec dicts.

        Each spec may contain any subset of: kind, url, headed, label, profile,
        viewport_w, viewport_h, stabilize, record_video.  Runs with
        asyncio.gather so they boot in parallel.  An error on one browser does
        NOT abort the others.

        Returns {"launched": [launch_result, ...], "errors": [{"spec": ..., "error": "..."}, ...]}.
        """

        return await _spawn_roster(self, specs)

    async def shutdown(self) -> None:
        await shutdown_pool(self)

    async def _build_launch_kwargs(self, *, tile: bool, kind: str, headless: bool) -> dict[str, Any]:
        """Chromium-only window tiling + new-tab-page override extension.

        Headed Chromium loads a tiny unpacked extension that overrides the
        new-tab page to redirect to the daemon's /new-tab. Chromium's
        privileged NTP detaches Playwright handles, so a post-open page.goto
        redirect is unreliable — the extension is the robust path. Old headless
        can't load extensions, so it's skipped there (no user pressing Cmd+T in
        headless anyway). Firefox/WebKit have no equivalent CLI hook; their new
        tabs are handled by the page-event redirector in launch_pipeline.py.
        """
        out: dict[str, Any] = {}
        if kind != "chromium":
            return out
        args: list[str] = []
        # Linux/CI: the default /dev/shm (often 64MB in containers) is too small
        # for Chromium's shared-memory transport; exhaustion surfaces as random
        # renderer crashes. Route shared memory to a regular tmpfile instead.
        # Needed on Linux only (no-op risk on macOS/Windows), and it applies to
        # headless too — the early "headless returns nothing" path used to skip it.
        if sys.platform.startswith("linux"):
            args.append("--disable-dev-shm-usage")
        if not headless:
            args.extend(self._headed_chromium_args())
        if tile and not headless:
            async with self._tile_lock:
                tile_index = self._tile_counter
                self._tile_counter += 1
            args.extend(_tile_args_for_chromium(tile_index))
        if args:
            out["args"] = args
        return out

    def _headed_chromium_args(self) -> list[str]:
        """New-tab-override extension args for headed Chromium (tile args are
        added by the caller under ``_tile_lock``)."""
        from octowright.browser_pool.newtab_extension import ensure_newtab_extension
        from octowright.defaults import get_default_url

        ext_dir = ensure_newtab_extension(get_default_url())
        return [f"--disable-extensions-except={ext_dir}", f"--load-extension={ext_dir}"]

    async def _resolve_session_dir(
        self,
        session: bool,
        launch_options: LaunchOptions,
        instance_id: str,
        kind: str,
    ) -> str | None:
        """Session=True: a tmpdir profile that lives for the daemon's
        lifetime, reused across launches sharing the same (session_key, kind).
        Named sessions reuse by label; anonymous sessions key by instance_id
        so unrelated callers never share state.

        Concurrency: ``spawn_roster`` gathers _launch_one coroutines that all
        funnel here; without serialisation two same-(label, kind) coros could
        each call ``mkdtemp`` and leak the loser. ``_sessions_lock`` collapses
        the read-or-create critical section so exactly one tmpdir per key is
        ever minted."""
        if not session:
            return None
        import tempfile

        session_name = launch_options.session_name(instance_id)
        session_key = (session_name, kind)
        async with self._sessions_lock:
            existing = self._session_profile_dirs.get(session_key)
            if existing is None or not existing.exists():
                tmp = Path(tempfile.mkdtemp(prefix=f"{SESSION_TMPDIR_PREFIX}{session_name}-{kind}-"))
                self._session_profile_dirs[session_key] = tmp
                existing = tmp
        return str(existing)
