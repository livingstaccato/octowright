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

from octowright._tracing import span
from octowright.browser_pool import driver_health, driver_relaunch
from octowright.browser_pool._metrics import launch_span
from octowright.browser_pool.cleanup import cleanup_on_launch_failure
from octowright.browser_pool.events import SessionCloseReason
from octowright.browser_pool.launch_execution import launch_profile_locked
from octowright.browser_pool.launch_helpers import rotate_har_path
from octowright.browser_pool.lifecycle import close_browser, handoff_browser, shutdown_pool
from octowright.browser_pool.options import LaunchOptions
from octowright.browser_pool.roster import close_all as _close_all
from octowright.browser_pool.roster import spawn_roster as _spawn_roster
from octowright.browser_pool.session_dirs import SESSION_TMPDIR_PREFIX
from octowright.browser_pool.visuals import _tile_args_for_chromium
from octowright.defaults import RECORDINGS_DIR, get_default_url
from octowright.profile_lifecycle import profile_lifecycle_lock
from octowright.session import BrowserSession

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

    def __init__(self, *, recordings_dir: Path | None = None) -> None:
        # Per-pool artefact write root (custom root = write-side only); see CLAUDE.md.
        self._recordings_dir = (recordings_dir if recordings_dir is not None else RECORDINGS_DIR).expanduser()
        self._pw: Playwright | None = None
        self._pw_lock = asyncio.Lock()
        # Count of shared-driver rebuilds after a death (surfaced in status).
        self._driver_restarts: int = 0
        self._sessions: dict[str, BrowserSession] = {}
        self._sessions_lock = asyncio.Lock()
        # Instance ids dropped via the external close/crash path
        # (_evict_session_nowait), insertion-ordered + capped. Maps id -> crashed?
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
        """Return the live session for ``instance_id`` or raise KeyError.

        Reads ``_sessions`` without ``_sessions_lock`` — concurrent eviction
        (``_evict_session_nowait`` in ``listeners.py``) writes without the
        lock too, relying on CPython's GIL-atomic ``dict.pop``. The returned
        session may therefore be evicted by a Playwright-side close signal
        between this call and the caller's first ``await`` on it; tool
        handlers in that window will surface as Playwright-disconnected
        errors rather than a clean KeyError. (The eviction itself has
        already incremented ``octowright_browser_evicted_total`` via the
        listener path.) The caller is expected to treat both as terminal
        for the session and either close or retry.
        """
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
        # close callbacks fire _evict_session_nowait between awaits and could
        # otherwise mutate the dict mid-iteration.
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
            raise ValueError(f"unknown viewport action: {action!r}")

        await expose_binding("__octowright_viewport_action", _viewport_action)

    async def relaunch_fluid(self, instance_id: str) -> dict[str, Any]:
        source = self.get(instance_id)
        # Snapshot every field we need BEFORE awaiting close. A Playwright
        # external-close eviction can fire between pool.get() and pool.close(),
        # popping the session and turning close() into a KeyError. We treat
        # that race as "already closed" and still launch the replacement so
        # the user isn't left with no browser.
        source_kind = source.kind
        source_label = source.label
        source_profile = source.profile
        source_user_data_dir = source.user_data_dir
        source_stabilize = source.stabilize
        source_trace = source.trace
        source_har_path = source.har_path
        source_protected = getattr(source, "protected", False)
        source_protected_reason = getattr(source, "protected_reason", "explicit")
        target_url = getattr(source.page, "url", None) or source.url
        # Wrap close+launch under a parent span so the child browser.close /
        # browser.launch spans nest underneath as one fluid-mode round-trip.
        with span("octowright.browser.relaunch_fluid", instance_id=instance_id, kind=source_kind):
            session_scoped = source_profile is None and source_user_data_dir is not None
            stateless = source_profile is None and source_user_data_dir is None
            # Don't overwrite the prior HAR — relaunch gets a sibling path.
            next_har = rotate_har_path(source_har_path)
            try:
                # force=True: relaunch_fluid closes the source only to reopen
                # the same logical browser immediately after (state/profile
                # preserved) — it is not a destructive agent close, so a
                # protected (e.g. headed-by-default) source must not refuse
                # here the way an explicit browser_close would.
                close_result: dict[str, Any] | None = await self.close(instance_id, force=True)
            except KeyError:
                log.warning(
                    "octowright.browser.relaunch_fluid.close_raced_eviction",
                    instance_id=instance_id,
                    kind=source_kind,
                )
                close_result = None
            result = await self.launch(
                kind=source_kind,
                url=target_url,
                headed=True,
                label=source_label,
                profile=source_profile,
                stabilize=source_stabilize,
                trace=source_trace,
                har=bool(source_har_path),
                har_path=str(next_har) if next_har else None,
                badge=True,
                ephemeral=stateless,
                session=session_scoped,
                protected=source_protected,
            )
            # resolve_protected() always stamps reason="explicit" whenever an
            # explicit (non-None) protected value is passed in — which we just
            # did with source_protected above, to carry the boolean across the
            # relaunch. That correctly preserves the protected bit but loses
            # the ORIGINAL reason (e.g. "headed_default"), which the tailored
            # close-refusal message keys off. Restore it post-hoc now that the
            # new session is registered in the pool. Use maybe_get (not get):
            # some unit tests stub out ``launch`` entirely, so there may be no
            # real session behind the returned instance_id — nothing to patch
            # in that case.
            new_session = self.maybe_get(result["instance_id"])
            if new_session is not None:
                new_session.protected_reason = source_protected_reason
            return {
                "ok": True,
                "old_instance_id": instance_id,
                "new_instance_id": result["instance_id"],
                "old_closed": bool(close_result and close_result.get("closed")),
                "mode": "fluid",
                "launch": result,
            }

    def profile_in_use(self, kind: str, profile: str) -> bool:
        return any(s.kind == kind and s.profile == profile for s in tuple(self._sessions.values()))

    def _evict_session_nowait(self, instance_id: str) -> BrowserSession | None:
        # Called from synchronous Playwright event callbacks (page.close,
        # context.close, browser.disconnected). Can't `await` a lock from a
        # sync callback, but CPython dict.pop is GIL-atomic and asyncio is
        # single-threaded — so this and the locked pop in close_browser
        # cannot interleave in flight. Idempotent: returns None on miss.
        session = self._sessions.pop(instance_id, None)
        if session is not None:
            # Remember it died externally (crash / OS close), and whether a crash
            # was observed on it, so get() can say "crashed" vs the generic
            # "ended unexpectedly". An explicit agent close pops under the lock
            # first, so this returns None there and we skip — agent-closed ids
            # keep the plain "no such id" message.
            self._recently_evicted[instance_id] = bool(getattr(session, "_crashed", False))
            if len(self._recently_evicted) > self._RECENTLY_EVICTED_CAP:
                del self._recently_evicted[next(iter(self._recently_evicted))]
        return session

    async def close(
        self,
        instance_id: str,
        *,
        force: bool = False,
        _reason: SessionCloseReason = "agent_close",
    ) -> dict[str, Any]:
        return await close_browser(self, instance_id, force=force, _reason=_reason)

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
