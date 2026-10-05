# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What a replacement carries of the original's post-launch routes and headers.

Three tools change what a browser sends after it launched, at two levels:

* ``browser_inject_headers`` -- CONTEXT routes (``_header_routes``, with the
  headers each merges in ``_injected_headers``);
* ``browser_mock_route`` -- PAGE routes (``_active_routes``, with the
  response each fulfils in ``_mock_specs``);
* ``browser_set_extra_http_headers`` -- the PAGE's own header map
  (``_page_extra_headers``).

``LaunchOptions`` carries none of them, so handoff, fluid relaunch and the
driver-death / process-crash reopen -- each a new context -- lost all three,
and crash recovery -- a new page in the same context -- lost the two page-level
ones. Nothing said so. Two ways they are carried now:

* **A new context** (:class:`RouteCarry`, :func:`replay_onto_session`): read
  off the original, then replayed through the replacement session's OWN gated
  methods before its first navigation, so the replacement's recording and its
  redaction are exactly what calling those tools would have produced.
* **A new page in the same context** (:func:`page_routes_of`,
  :func:`install_page_routes`): the context routes are still there; the page
  routes are re-registered on the new page with the original handlers.

Order is kept. Within one level Playwright runs the LAST-registered handler
first, so each registry is an insertion-ordered dict whose order is
registration order (a re-registration moves its pattern to the end) and is
replayed in that order. Across levels order does not enter into it -- page
routes run ahead of context ones.

What cannot be carried is named, never dropped: a mock whose response was not
kept (its handler is an opaque closure), and a mock or page headers set on a
page other than the one the replacement reopens (a replacement opens ONE page,
at the active page's URL). Warnings name patterns and kinds, never header
values or error text that might echo them, because they reach
``octowright_status``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from provide.telemetry import get_logger

from octowright.session.timeouts import bounded

log = get_logger(__name__)

#: Headers are never in a warning; nor is an exception's text, which a header
#: validation error could fill with one. The exception's TYPE is enough to act on.
_FAILED: Final = "{tool} {pattern} could not be re-installed on the replacement ({error})"


@dataclass(frozen=True)
class MockSpec:
    """The response a ``mock_route`` fulfils, kept so another context can rebuild it."""

    status: int
    body: str | None
    content_type: str
    headers: dict[str, str]
    #: The page it was installed on: a mock is a PAGE route, so it covers that
    #: page only (a popup or a page switched to afterwards never had it).
    page: Any = field(default=None, compare=False, repr=False)

    def kwargs(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "body": self.body,
            "content_type": self.content_type,
            "headers": dict(self.headers),
        }


def _mapping(session: Any, name: str) -> dict[str, Any]:
    value = getattr(session, name, None)
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class RouteCarry:
    """The original's post-launch routes and headers, for a replacement in a new context."""

    injected: tuple[tuple[str, dict[str, str]], ...] = ()
    mocks: tuple[tuple[str, MockSpec], ...] = ()
    page_headers: dict[str, str] | None = None
    #: What could not be carried, as warnings, in the order found.
    not_carried: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.injected or self.mocks or self.page_headers or self.not_carried)

    @classmethod
    def of(cls, session: Any) -> RouteCarry:
        """Read off *session* now: the copy outlives the original's teardown."""
        active = getattr(session, "page", None)
        specs = _mapping(session, "_mock_specs")
        mocks: list[tuple[str, MockSpec]] = []
        not_carried: list[str] = []
        for pattern in _mapping(session, "_active_routes"):
            spec = specs.get(pattern)
            if not isinstance(spec, MockSpec):
                not_carried.append(f"mock_route {pattern!r} was not carried: its response was not kept")
            elif spec.page is not active:
                not_carried.append(
                    f"mock_route {pattern!r} was not carried: it was installed on a page other than the "
                    "active one, and a replacement reopens only the active page"
                )
            else:
                mocks.append((pattern, MockSpec(**spec.kwargs())))
        page_headers = _mapping(session, "_page_extra_headers")
        if page_headers and getattr(session, "_page_extra_headers_page", None) is not active:
            not_carried.append(
                "page-level extra HTTP headers were not carried: they were set on a page other than "
                "the active one, and a replacement reopens only the active page"
            )
            page_headers = {}
        return cls(
            injected=tuple(
                (pattern, dict(headers)) for pattern, headers in _mapping(session, "_injected_headers").items()
            ),
            mocks=tuple(mocks),
            page_headers=dict(page_headers) or None,
            not_carried=tuple(not_carried),
        )


async def replay_onto_session(session: Any, carry: RouteCarry) -> list[str]:
    """Re-install *carry* on *session* through its own route methods; returns the warnings.

    Called before the replacement's first navigation, by the task holding its
    launch lease (the gate lets the owning task re-enter). A step that fails is
    logged and becomes a warning; the rest still replay.
    """
    warnings = list(carry.not_carried)

    async def _step(tool: str, pattern: str, call: Any) -> None:
        try:
            await call
        except Exception as exc:
            log.warning("octowright.session.route_carry_failed", tool=tool, pattern=pattern, error=type(exc).__name__)
            warnings.append(_FAILED.format(tool=tool, pattern=repr(pattern), error=type(exc).__name__))

    for pattern, headers in carry.injected:
        await _step("inject_headers", pattern, session.inject_headers(pattern, dict(headers)))
    for pattern, spec in carry.mocks:
        await _step("mock_route", pattern, session.mock_route(pattern, **spec.kwargs()))
    if carry.page_headers:
        await _step("set_extra_http_headers", "", session.set_extra_http_headers(dict(carry.page_headers)))
    for warning in carry.not_carried:
        log.warning("octowright.session.route_not_carried", warning=warning)
    return warnings


@dataclass(frozen=True)
class PageRoutes:
    """The page-level routes and headers one page had, for its replacement in the same context."""

    mocks: tuple[str, ...] = ()
    page_headers: dict[str, str] | None = None
    not_carried: tuple[str, ...] = ()


def page_routes_of(session: Any, page: Any) -> PageRoutes:
    """The mocks and page headers installed on *page*, read before it is replaced."""
    specs = _mapping(session, "_mock_specs")
    mocks: list[str] = []
    not_carried: list[str] = []
    for pattern in _mapping(session, "_active_routes"):
        spec = specs.get(pattern)
        if not isinstance(spec, MockSpec):
            not_carried.append(f"mock_route {pattern!r} was not restored: the page it was installed on is unknown")
        elif spec.page is page:
            mocks.append(pattern)
    page_headers = _mapping(session, "_page_extra_headers")
    on_page = bool(page_headers) and getattr(session, "_page_extra_headers_page", None) is page
    return PageRoutes(
        mocks=tuple(mocks), page_headers=dict(page_headers) if on_page else None, not_carried=tuple(not_carried)
    )


async def install_page_routes(session: Any, routes: PageRoutes, new_page: Any) -> list[str]:
    """Register *routes* on *new_page* with the original handlers; returns the warnings.

    Not through the session's methods: those act on ``session.page``, which is
    still the dead page until the swap, and this restores state rather than
    recording a new action. The handlers are the originals -- a mock's handler
    is independent of the page it serves -- so a later ``unmock_route``
    removes the same handler it would have.
    """
    warnings = list(routes.not_carried)
    handlers = _mapping(session, "_active_routes")
    # Re-enters the crash recovery's own lease, as its other helpers do.
    async with session.operation("crash_recovery", wait_timeout_seconds=None):
        for pattern in routes.mocks:
            handler = handlers.get(pattern)
            if handler is None:
                continue
            try:
                await bounded(new_page.route(pattern, handler), operation="crash_recovery")
            except Exception as exc:
                log.warning("octowright.crash.route_restore_failed", pattern=pattern, error=type(exc).__name__)
                warnings.append(_FAILED.format(tool="mock_route", pattern=repr(pattern), error=type(exc).__name__))
        if routes.page_headers:
            try:
                headers = dict(routes.page_headers)
                await bounded(new_page.set_extra_http_headers(headers), operation="crash_recovery")
            except Exception as exc:
                log.warning("octowright.crash.route_restore_failed", pattern="", error=type(exc).__name__)
                warnings.append(_FAILED.format(tool="set_extra_http_headers", pattern="''", error=type(exc).__name__))
    return warnings


def rebind_page_routes(session: Any, routes: PageRoutes, new_page: Any) -> None:
    """Record that *routes* now live on *new_page*, once it holds the dead page's slot."""
    specs = _mapping(session, "_mock_specs")
    for pattern in routes.mocks:
        spec = specs.get(pattern)
        if isinstance(spec, MockSpec):
            specs[pattern] = MockSpec(**spec.kwargs(), page=new_page)
    if routes.page_headers is not None:
        session._page_extra_headers_page = new_page
