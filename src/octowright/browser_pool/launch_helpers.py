# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Helpers extracted from pool.py launch() to keep both LOC and cyclomatic
complexity below the project gates. Each helper handles one cohesive slice
of launch wiring: kwargs assembly, context open, recorder event, manifest
write."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from provide.telemetry import get_logger

from octowright._paths import reject_unsafe_path
from octowright.browser_pool.cleanup import cleanup_on_launch_failure
from octowright.browser_pool.download_history import prune_download_history
from octowright.browser_pool.restore_prompt import clear_crash_restore_prompt
from octowright.browser_pool.singleton_locks import prune_stale_singleton_locks
from octowright.browser_pool.viewport import ViewportInfo, ViewportMode
from octowright.defaults import DEFAULT_VIEWPORT_H, DEFAULT_VIEWPORT_W, PROFILES_DIR, RECORDINGS_DIR
from octowright.personas import engine_profile_dir, load_persona
from octowright.private_paths import secure_profile_tree
from octowright.recorder import Recorder
from octowright.session.timeouts import bounded
from octowright.session_manifest import record_launch as _manifest_record_launch
from octowright.session_manifest import run_manifest_transaction_async
from octowright.ssrf_guard import install_navigation_guard

if TYPE_CHECKING:
    # Annotation only — avoids the launch_helpers → options runtime cycle
    # (options.py imports launch_helpers locally for the same reason).
    from octowright.browser_pool.options import LaunchOptions

log = get_logger(__name__)


def _build_viewport_kwargs(
    headless: bool, viewport_w: int | None, viewport_h: int | None
) -> tuple[dict[str, Any], dict[str, Any], bool, ViewportInfo]:
    """Headed launches with no explicit size let Playwright adopt the OS
    window via no_viewport=True. Headless and explicit-size launches pin a
    fixed viewport. Returns (kwargs, recorder_payload, explicit_size_flag)."""
    explicit_size = viewport_w is not None or viewport_h is not None
    if headless or explicit_size:
        vw = viewport_w or DEFAULT_VIEWPORT_W
        vh = viewport_h or DEFAULT_VIEWPORT_H
        info = ViewportInfo(mode=ViewportMode.FIXED, width=vw, height=vh)
        return {"viewport": {"width": vw, "height": vh}}, info.to_recording(), explicit_size, info
    info = ViewportInfo(mode=ViewportMode.FLUID)
    return {"no_viewport": True}, info.to_recording(), explicit_size, info


def _build_video_kwargs(
    record_video: bool,
    headless: bool,
    explicit_size: bool,
    viewport_w: int | None,
    viewport_h: int | None,
    *,
    log_path: Path,
    recordings_dir: Path | None = None,
) -> tuple[dict[str, Any], Path | None]:
    """Allocate a per-launch videos/ dir and assemble the record_video_*
    context kwargs. Pins video size to the viewport so Playwright doesn't
    auto-scale to its 800x800 default. ``recordings_dir`` defaults to the
    process-global root (resolved at call time so monkeypatching works); the
    pool passes its own so per-pool routing works.

    The directory reuses ``log_path.stem`` (``{timestamp}-{kind}-{instance_id}
    {-label}``, from ``recorder.new_log_path``) rather than a random id, so a
    video is findable by label/instance_id on disk the same way the JSONL,
    trace, and HAR for the same launch already are — a random UUID here was
    the one artifact type that didn't carry the label, so it could only be
    found via the launch/close result's returned path, not by convention.
    """
    if not record_video:
        return {}, None
    root = recordings_dir if recordings_dir is not None else RECORDINGS_DIR
    video_dir = root / "videos" / log_path.stem
    video_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any] = {"record_video_dir": str(video_dir)}
    if headless or explicit_size:
        out["record_video_size"] = {
            "width": viewport_w or DEFAULT_VIEWPORT_W,
            "height": viewport_h or DEFAULT_VIEWPORT_H,
        }
    return out, video_dir


_MAX_HAR_ROTATIONS = 10_000


def next_har_path(p: Path) -> Path:
    """Pick a HAR path that won't clobber an existing recording. Returns ``p``
    itself if it doesn't exist; otherwise suffixes the stem with ``.{n}`` and
    bumps ``n`` until a free sibling is found (e.g. ``foo.har`` -> ``foo.1.har``).
    Raises ``RuntimeError`` after ``_MAX_HAR_ROTATIONS`` siblings to avoid an
    unbounded ``stat()`` loop on a pathologically full directory."""
    if not p.exists():
        return p
    parent = p.parent
    stem = p.stem
    suffix = p.suffix
    for n in range(1, _MAX_HAR_ROTATIONS + 1):
        candidate = parent / f"{stem}.{n}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"exhausted {_MAX_HAR_ROTATIONS} HAR rotations for {p}")


def rotate_har_path(current: Path | None) -> Path | None:
    """``next_har_path`` with a ``None`` short-circuit.

    The relaunch / handoff / JSONL-replay paths all want "rotate the prior
    HAR if there was one, else leave it alone" — collapsing the ``None``
    check into a one-liner avoids three near-identical pieces of code at
    those call sites."""
    if current is None:
        return None
    return next_har_path(current)


def _build_har_kwargs(
    *,
    har: bool,
    har_path_opt: str | None,
    har_mode: str,
    har_url_filter: str | None,
    har_content: str | None,
    log_path: Path,
    recordings_dir: Path | None = None,
) -> tuple[Path | None, dict[str, Any]]:
    """Resolve the HAR output path (relative paths land under ``recordings_dir``)
    and assemble the record_har_* context kwargs. ``recordings_dir`` defaults to
    the process-global root (resolved at call time so monkeypatching works); the
    pool passes its own for per-pool routing."""
    if not (har or har_path_opt):
        return None, {}
    root = recordings_dir if recordings_dir is not None else RECORDINGS_DIR
    har_path = Path(har_path_opt) if har_path_opt else log_path.with_suffix(".har")
    if not har_path.is_absolute():
        har_path = (root / har_path).resolve()
    # Both branches (relative-sandboxed and absolute-supplied) must end up
    # under the root. Sandboxing only the relative path would let an
    # absolute LLM-supplied path pass straight through.
    har_path = reject_unsafe_path(har_path, root, label="har_path")
    har_path.parent.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any] = {
        "record_har_path": str(har_path),
        "record_har_mode": har_mode,
    }
    if har_url_filter:
        out["record_har_url_filter"] = har_url_filter
    if har_content:
        out["record_har_content"] = har_content
    return har_path, out


def build_recording_kwargs(
    launch_options: LaunchOptions,
    *,
    headless: bool,
    explicit_size: bool,
    log_path: Path,
    recordings_dir: Path,
) -> tuple[dict[str, Any], Path | None, Path | None, dict[str, Any]]:
    """Assemble the record_video_* and record_har_* context kwargs for one
    launch, routing both under ``recordings_dir`` (the owning pool's write
    root). Returns ``(video_kwargs, video_dir, har_path, har_kwargs)``."""
    video_kwargs, video_dir = _build_video_kwargs(
        launch_options.record_video,
        headless,
        explicit_size,
        launch_options.viewport_w,
        launch_options.viewport_h,
        log_path=log_path,
        recordings_dir=recordings_dir,
    )
    har_path, har_kwargs = _build_har_kwargs(
        har=launch_options.har,
        har_path_opt=launch_options.har_path,
        har_mode=launch_options.har_mode,
        har_url_filter=launch_options.har_url_filter,
        har_content=launch_options.har_content,
        log_path=log_path,
        recordings_dir=recordings_dir,
    )
    return video_kwargs, video_dir, har_path, har_kwargs


def base_url_kwargs(profile: str | None, explicit: str | None = None) -> dict[str, str]:
    """Playwright ``base_url`` for a launch: explicit wins, else the persona's.

    Explicit exists for callers that drive octowright as a library with no saved
    persona -- a test replaying a macro against a dev stack, a batch run pointed
    at one tier. The persona remains the answer for anything launched by name.

    Validated through the same guard every navigation uses. It has to be: a
    relative navigate is waved through precisely BECAUSE it inherits this
    origin, so an unchecked base_url would be a way to reach a host the SSRF
    policy refuses by writing '/' in a macro.
    """
    from octowright.session.core_page_mixin import _reject_unsafe_url

    chosen = explicit or persona_base_url_kwargs(profile).get("base_url")
    if not chosen:
        return {}
    _reject_unsafe_url(chosen)
    return {"base_url": chosen}


def persona_base_url_kwargs(profile: str | None) -> dict[str, str]:
    """Playwright ``base_url`` for a persona, so macros can be host-relative.

    A macro is the BEHAVIOUR; the persona is the WHERE. That split already
    exists here -- ``resolve`` scores a persona against a URL on its
    ``default_url`` host and ``app.hosts``, and ``scenarios`` falls back to
    ``default_url`` for a participant with no URL of its own. Contexts were the
    one place it was not honoured, so a macro that wanted to be portable had to
    bake an origin into every ``navigate``, and replaying the same behaviour
    against another deployment meant editing the macro rather than choosing a
    different persona.

    ``base_url`` is Playwright's own mechanism for exactly this: with it set,
    ``page.goto("/orders")`` resolves against the persona's origin, and
    ``expect_url`` accepts the same relative form.

    Silent when there is no persona behind the profile, or it declares no
    ``default_url``: a profile name is not required to be a saved persona, and
    an absolute URL in a macro keeps working either way.
    """
    if not profile:
        return {}
    try:
        persona = load_persona(profile)
    except (FileNotFoundError, ValueError, yaml.YAMLError):
        return {}
    return {"base_url": persona.default_url} if persona.default_url else {}


async def install_scoped_header_routes(
    context: Any, headers: dict[str, str] | None, url_patterns: list[str] | None
) -> None:
    """Apply launch headers to matching URLs only, via CONTEXT routes.

    Context scope rather than page scope so the routes follow popups and pages
    opened later -- the property page-level routing lacks and the reason the
    page-scoped version of this had to be re-registered after every page switch.

    ``None`` and ``[]`` are deliberately different: ``None`` means "no scoping
    was asked for" (the headers stay context-level), while an empty list means
    "scope to nothing" and registers nothing. A truthiness check conflated the
    two and sent the headers on EVERY request -- failing open in the
    credential-spraying direction. ``LaunchOptions`` refuses ``[]`` outright;
    this helper is callable on its own and must not fail open either.
    """
    if not headers or url_patterns is None:
        return

    def _make(extra: dict[str, str]) -> Any:
        async def _handler(route: Any) -> None:
            try:
                await route.fallback(headers={**route.request.headers, **extra})
            except Exception as exc:  # pragma: no cover - route already gone
                # A route whose page navigated away raises on fallback and abort
                # alike (the same reason ssrf_guard._handle_route wraps its own
                # body). Let loose, the exception escapes into Playwright's route
                # dispatcher, the intercepted request is never answered, and the
                # load hangs until it times out.
                log.debug("octowright.session.header_route_failed", error=repr(exc))

        return _handler

    for pattern in url_patterns:
        await bounded(
            context.route(pattern, _make(dict(headers))),
            operation="browser_launch_scoped_header_route",
        )


async def install_context_routes(context: Any, headers: dict[str, str] | None, url_patterns: list[str] | None) -> None:
    """Install every launch-time context route, in the ONE order that is correct.

    Playwright runs context route handlers **last-registered-first**, and
    ``install_navigation_guard`` is itself a context route. Registering the
    scoped header routes AFTER it therefore makes them run FIRST, so the
    guard's own ``route.fetch(max_redirects=0)`` -- which is now the ONLY
    fetch of a navigation -- carries the same headers the browser's request
    would have.

    Reversed, they are two: an unauthenticated validation fetch can be answered
    with an allowed redirect (a login page) while the authenticated request the
    browser actually makes redirects somewhere the policy would have refused,
    and the guard never sees it. The order is the whole point of this helper
    existing rather than two calls at the call site.

    Scoped headers also install the guard with the SSRF policy off
    (``scope_headers``): a route's header override rides every redirect the
    engine follows, so only the guard's one-fetch-per-hop navigation lets each
    hop be re-matched against the patterns. Unscoped or no headers register
    nothing.
    """
    await install_navigation_guard(context, scope_headers=bool(headers) and url_patterns is not None)
    await install_scoped_header_routes(context, headers, url_patterns)


def extra_http_headers_kwargs(
    headers: dict[str, str] | None, url_patterns: list[str] | None = None
) -> dict[str, dict[str, str]]:
    """Playwright context kwargs for launch-time extra headers.

    Returns ``{}`` when there is nothing to say, so a launch that sets no
    headers passes no ``extra_http_headers`` at all rather than an empty dict
    -- the same "silent when there is nothing to say" shape ``base_url_kwargs``
    uses, and the reason every pre-existing launch is untouched.

    Copied, not aliased: the context outlives the caller's dict, and a caller
    mutating it afterwards must not retroactively change what the browser
    sends.
    """
    # With URL patterns the headers go on scoped context ROUTES instead, so the
    # context must not also carry them unscoped -- that is the whole point.
    # ``is not None`` rather than truthiness: an empty list means "scope to
    # nothing", and reading it as "no scoping" put the headers on every request.
    if url_patterns is not None:
        return {}
    return {"extra_http_headers": dict(headers)} if headers else {}


# What a page reports before it has committed a navigation of its own. Both
# spellings occur; neither is content anyone would miss.
_BLANK_URLS = frozenset({"", "about:blank"})


def _is_blank(page: Any) -> bool:
    """Whether *page* holds nothing worth preserving.

    A page that cannot be read is treated as NOT blank: it is closing, or
    otherwise unhealthy, and the safe direction is to leave it alone rather
    than elect it as the one we are about to navigate.
    """
    try:
        return page.url in _BLANK_URLS
    except Exception:
        return False


async def select_launch_page(context: Any) -> Any:
    """Pick the page a persistent launch should navigate to the target URL.

    ``context.pages[0]`` assumed the first page was octowright's own. Chromium
    session restore breaks that assumption AND the order is a race -- two runs
    of the same probe put ``about:blank`` first, then third -- so the caller
    could navigate a restored tab and destroy its content.

    One page is the ordinary case and is returned untouched whatever its URL:
    Chromium's initial page is not always ``about:blank`` (the new-tab override
    extension replaces it), so testing for blankness there would open a
    spurious second page on every launch. Only a context handing back several
    pages takes the other path, and even then nothing is closed or navigated
    over -- with no blank page to spare, a new one is opened instead.
    """
    pages = list(context.pages)
    if not pages:
        return await context.new_page()
    if len(pages) == 1:
        return pages[0]
    for page in pages:
        if _is_blank(page):
            return page
    return await context.new_page()


async def _prune_chromium_download_history(kind: str, user_data_dir: Path) -> None:
    """Chromium 153 kills its browser process on the first download of a headed
    run while the user-data-dir holds any download-history row. See
    download_history. Blocking SQLite, so off the loop -- but awaited, so it is
    done before the browser opens the database."""
    if kind == "chromium":
        await asyncio.to_thread(prune_download_history, user_data_dir)


async def _prepare_session_user_data_dir(kind: str, session_dir: Path) -> None:
    """A ``session=True`` tmpdir is reused by every launch sharing its label, so
    it carries download rows into the next launch exactly like a profile --
    including the relaunch after the crash those rows cause. The stale-lock
    prune comes first for the same reason as on a profile: a crashed browser
    leaves its ``SingletonLock`` behind, and the download prune refuses a dir
    that still holds one."""
    if kind != "chromium":
        return
    prune_stale_singleton_locks(session_dir)
    await _prune_chromium_download_history(kind, session_dir)


async def _prepare_persistent_user_data_dir(
    *, kind: str, profile: str | None, session_user_data_dir: str | None, launch_kwargs: dict[str, Any]
) -> tuple[str | None, dict[str, Any]]:
    """Ready the directory a persistent launch opens; return it and the launch
    kwargs (a persona's trust settings join them)."""
    if not profile:
        if session_user_data_dir is not None:
            await _prepare_session_user_data_dir(kind, Path(session_user_data_dir))
        return session_user_data_dir, launch_kwargs
    pdir = engine_profile_dir(persona=profile, kind=kind)
    pdir.mkdir(parents=True, exist_ok=True)
    # Live session cookies live here; Firefox/WebKit write them 0644
    # into an 0755 tree. See octowright.private_paths.
    secure_profile_tree(pdir, PROFILES_DIR)
    # A profile whose browser died without cleaning up — or whose lock
    # socket went with a temp-dir sweep — keeps a lock naming a pid that
    # no longer exists, and Chromium then refuses the profile ("already
    # in use") on every future launch. Only a confirmed-dead local owner
    # is pruned; see singleton_locks.
    prune_stale_singleton_locks(pdir)
    # A browser that died without an orderly shutdown also leaves the
    # profile marked crashed, so every later launch opens behind a
    # "Restore pages?" bubble covering the page we just navigated to.
    # See restore_prompt.
    clear_crash_restore_prompt(pdir)
    await _prune_chromium_download_history(kind, pdir)
    # Scoped trust: a persona's roots reach its own Chromium only.
    # Applied here because the daemon and `octowright test` both open
    # persistent contexts through this function. See persona_trust.
    # Off the loop: an rmtree plus certutil runs with 30s timeouts.
    from octowright import persona_trust

    trust_kwargs = await asyncio.to_thread(persona_trust.persona_trust_launch_kwargs, profile, kind)
    return str(pdir), {**launch_kwargs, **trust_kwargs}


#: Closes of launches whose caller was cancelled before the handle arrived,
#: held so a pending close is not garbage-collected partway.
_ORPHAN_CLOSES: set[asyncio.Future[None]] = set()


async def _launch_unorphaned(launch: Any, *, persistent: bool) -> Any:
    """Await an engine *launch* so a cancellation cannot orphan what it launched.

    Playwright answers a cancelled launch by sending the driver ``__abort__``,
    which kills a browser still starting. But a cancel that lands after the
    driver finished and before Python took the reply discards the handle with
    the browser running -- measured on Chromium, persistent and headless: 2 of
    24 cancels swept across the ~0.1s launch left its process up, unowned, and
    on a persistent profile holding the ``SingletonLock``. The handle cannot be
    recovered once Playwright drops it, so the launch is shielded instead: the
    caller's cancellation returns at once, and the launch's handle is closed
    when it lands. The cost is that a cancelled launch now runs to completion,
    or to Playwright's own launch timeout, before it is closed.
    """
    task = asyncio.ensure_future(launch)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        task.add_done_callback(lambda done: _close_orphaned_launch(done, persistent=persistent))
        raise


def _close_orphaned_launch(task: asyncio.Future[Any], *, persistent: bool) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.debug("octowright.launch.orphaned_launch_failed", error=repr(exc))
        return
    handle = task.result()
    log.info("octowright.launch.orphaned_launch_closing", persistent=persistent)
    closing = asyncio.ensure_future(
        cleanup_on_launch_failure(
            context=handle if persistent else None, browser=None if persistent else handle, video_dir=None
        )
    )
    _ORPHAN_CLOSES.add(closing)
    closing.add_done_callback(_ORPHAN_CLOSES.discard)


async def _open_browser_context(
    *,
    browser_type: Any,
    kind: str,
    profile: str | None,
    session_user_data_dir: str | None,
    headless: bool,
    viewport_kwargs: dict[str, Any],
    ctx_video_kwargs: dict[str, Any],
    ctx_har_kwargs: dict[str, Any],
    launch_kwargs: dict[str, Any],
    base_url: str | None = None,
    extra_http_headers: dict[str, str] | None = None,
    extra_http_headers_urls: list[str] | None = None,
) -> tuple[Any, Any, Any, str | None]:
    """Open a Playwright BrowserContext + Page. Persistent profile and
    session-tmpdir paths both go through launch_persistent_context (no
    standalone Browser); the ephemeral path goes through Browser.new_context.

    Returns (browser, context, page, user_data_dir). browser is None for the
    persistent path.

    Owns its own cleanup from the engine launch on: a failure -- or a
    cancellation, which is how the launch deadline and a client disconnect
    arrive -- in any later step (opening the context or page, installing the
    routes) closes what was launched before re-raising. Nothing is returned
    on that path, so the caller's cleanup only ever sees ``context=None``; a
    browser left to it stayed running and, on a persistent profile, kept the
    ``SingletonLock`` that fails every later launch of that profile."""
    ctx_base_url_kwargs = base_url_kwargs(profile, base_url)
    ctx_headers_kwargs = extra_http_headers_kwargs(extra_http_headers, extra_http_headers_urls)
    ctx_kwargs = {**ctx_base_url_kwargs, **ctx_headers_kwargs, **viewport_kwargs, **ctx_video_kwargs, **ctx_har_kwargs}
    persistent = bool(profile or session_user_data_dir)
    user_data_dir: str | None = None
    if persistent:
        user_data_dir, launch_kwargs = await _prepare_persistent_user_data_dir(
            kind=kind, profile=profile, session_user_data_dir=session_user_data_dir, launch_kwargs=launch_kwargs
        )
    browser: Any = None
    context: Any = None
    try:
        if persistent:
            context = await _launch_unorphaned(
                browser_type.launch_persistent_context(
                    user_data_dir, headless=headless, accept_downloads=True, **ctx_kwargs, **launch_kwargs
                ),
                persistent=True,
            )
            page = await select_launch_page(context)
        else:
            browser = await _launch_unorphaned(
                browser_type.launch(headless=headless, **launch_kwargs), persistent=False
            )
            context = await browser.new_context(accept_downloads=True, **ctx_kwargs)
            page = await context.new_page()
        # Pre-flight SSRF checks only see the URL that was asked for; a redirect
        # is a different host, and a subresource was never asked for at all. No-op
        # unless a policy is enabled. Registration order is load-bearing -- see
        # install_context_routes.
        await install_context_routes(context, extra_http_headers, extra_http_headers_urls)
    except BaseException:
        # Shielded inside, so a repeated cancellation cannot abort it halfway.
        await cleanup_on_launch_failure(context=context, browser=browser, video_dir=None)
        raise
    return browser, context, page, user_data_dir


def _record_launch_event(
    recorder: Recorder,
    *,
    instance_id: str,
    kind: str,
    label: str | None,
    profile: str | None,
    user_data_dir: str | None,
    target_url: str,
    headless: bool,
    log_viewport: dict[str, Any] | None,
    stabilize: bool,
    record_video: bool,
    video_dir: Path | None,
    trace: bool,
    har_path: Path | None,
    har_mode: str,
    har_url_filter: str | None,
    har_content: str | None,
    badge: bool,
    badge_position: str,
    tile: bool,
    ephemeral: bool,
    session: bool,
    disable_automation_controlled: bool,
    wayland_native: bool | None = None,
) -> None:
    """Emit the JSONL `launch` event with all the conditional fields. Pulled
    out of launch() to keep its complexity rank below the gate."""
    recorder.record(
        "launch",
        instance_id=instance_id,
        kind=kind,
        label=label,
        profile=profile,
        user_data_dir=user_data_dir,
        url=target_url,
        headed=not headless,
        viewport=log_viewport,
        stabilize=stabilize,
        record_video=record_video,
        video_dir=str(video_dir) if video_dir else None,
        trace=trace,
        har=bool(har_path),
        har_path=str(har_path) if har_path else None,
        har_mode=har_mode if har_path else None,
        har_url_filter=har_url_filter if har_path else None,
        har_content=har_content if har_path else None,
        badge=badge,
        badge_position=badge_position,
        tile=tile,
        ephemeral=ephemeral,
        session=session,
        disable_automation_controlled=disable_automation_controlled,
        wayland_native=wayland_native,
    )


async def _safe_manifest_record(
    *,
    instance_id: str,
    kind: str,
    label: str | None,
    profile: str | None,
    user_data_dir: str | None,
    log_path: Path,
) -> None:
    """Best-effort manifest write. The manifest is purely an out-of-band
    convenience for the dashboard; a write failure must not block the launch.

    The cross-process lock polls synchronously, so keep it off the leader's
    asyncio thread while a split leader or frozen peer owns the manifest.
    """
    try:
        await run_manifest_transaction_async(
            _manifest_record_launch,
            session_id=instance_id,
            kind=kind,
            label=label,
            profile=profile,
            user_data_dir=user_data_dir,
            log_path=log_path,
        )
    except Exception as exc:
        log.warning("octowright.session_manifest.write_failed", instance_id=instance_id, error=repr(exc))
