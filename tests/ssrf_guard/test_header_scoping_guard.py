# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The guard's single-fetch path also runs for URL-scoped headers, with the SSRF policy off.

A scoped header's ``fallback(headers=...)`` override rides every redirect the
engine follows, so a header scoped to one origin reached the next. With the
policy off, a context whose session carries scoped headers still gets the
guard's navigation handling -- every hop fetched once and served as its own
navigation, so the header routes re-match it -- and none of the policy's host
refusals. A context without scoped headers gets nothing registered. The live
proof is ``test_scoped_header_hops_live.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from octowright import ssrf, ssrf_guard
from octowright.browser_pool import launch_helpers
from octowright.ssrf_guard import _handle_route, begin_navigation, install_navigation_guard


class _Response:
    def __init__(self, status: int, location: str | None = None) -> None:
        self.status = status
        self.status_text = "Found" if 300 <= status < 400 else "OK"
        self.headers = {"location": location} if location else {}


class _Context:
    def __init__(self) -> None:
        self.registered: list[str] = []
        self.pages: list[Any] = []

    async def route(self, pattern: str, _handler: Any) -> None:
        self.registered.append(pattern)

    async def route_web_socket(self, _pattern: Any, _handler: Any) -> None:
        self.registered.append("route_web_socket")

    def on(self, *_args: Any) -> None:
        return None


class _Page:
    def __init__(self, context: Any) -> None:
        self.context = context


class _Frame:
    def __init__(self, context: Any = None) -> None:
        self.page = _Page(context)


class _Request:
    def __init__(self, url: str, method: str = "GET", navigation: bool = True, frame: Any = None) -> None:
        self.url = url
        self.method = method
        self.frame = frame if frame is not None else _Frame()
        self._navigation = navigation

    def is_navigation_request(self) -> bool:
        return self._navigation


class _Route:
    def __init__(self, chain: dict[str, _Response], request: _Request) -> None:
        self.chain = chain
        self.request = request
        self.fetched: list[str] = []
        self.aborted: str | None = None
        self.fell_back = False
        self.fulfilled: Any = None
        self.fulfilled_body: dict[str, Any] | None = None

    async def fetch(self, *, max_redirects: int) -> _Response:
        assert max_redirects == 0
        self.fetched.append(self.request.url)
        return self.chain[self.request.url]

    async def fulfill(self, response: Any = None, **body: Any) -> None:
        self.fulfilled = response
        self.fulfilled_body = body or None

    async def abort(self, reason: str) -> None:
        self.aborted = reason

    async def fallback(self) -> None:
        self.fell_back = True


@pytest.fixture(autouse=True)
def policy_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_SSRF_POLICY", raising=False)

    def _no_resolution(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the policy is off: nothing may be resolved or refused by host")

    monkeypatch.setattr(ssrf, "_getaddrinfo", _no_resolution)


async def test_without_scoped_headers_nothing_is_registered() -> None:
    context = _Context()
    await install_navigation_guard(context)
    assert context.registered == []


async def test_scoped_headers_register_the_navigation_route_but_no_websocket_route() -> None:
    context = _Context()
    await install_navigation_guard(context, scope_headers=True)
    assert context.registered == ["**/*"]
    # Installed once per context, however many scoped headers arrive.
    await install_navigation_guard(context, scope_headers=True)
    assert context.registered == ["**/*"]


async def test_the_policy_guard_is_not_installed_twice_by_a_later_scoped_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", "block-private")
    context = _Context()
    await install_navigation_guard(context)
    await install_navigation_guard(context, scope_headers=True)
    assert context.registered == ["**/*", "route_web_socket"]


async def test_scoped_launch_headers_install_the_guard_ahead_of_their_routes() -> None:
    context = _Context()
    await launch_helpers.install_context_routes(context, {"X-A": "1"}, ["**/api/**"])
    assert context.registered == ["**/*", "**/api/**"]


async def test_unscoped_launch_headers_install_nothing() -> None:
    context = _Context()
    await launch_helpers.install_context_routes(context, {"X-A": "1"}, None)
    assert context.registered == []


async def test_a_subresource_falls_through_unchecked(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _refuse(_url: str) -> None:
        raise AssertionError("the policy is off: a subresource is not checked")

    monkeypatch.setattr(ssrf, "check_request_url_cached", _refuse)
    request = _Request("http://169.254.169.254/x", navigation=False)
    route = _Route({}, request)
    await _handle_route(route, request)
    assert route.fell_back
    assert route.aborted is None


async def test_a_redirect_to_a_private_host_is_served_as_a_client_redirect() -> None:
    """Only header scoping applies with the policy off: no host is refused."""
    request = _Request("http://127.0.0.1:9/start")
    route = _Route({"http://127.0.0.1:9/start": _Response(302, "http://169.254.169.254/next")}, request)
    await _handle_route(route, request)
    assert route.aborted is None
    assert route.fulfilled_body is not None
    assert "http://169.254.169.254/next" in route.fulfilled_body["body"]
    assert ssrf_guard.client_redirect_of(request) == {
        "status": 302,
        "status_text": "Found",
        "location": "http://169.254.169.254/next",
    }


async def test_a_plain_navigation_is_fulfilled_from_the_one_fetch() -> None:
    request = _Request("http://10.0.0.1/")
    first = _Response(200)
    route = _Route({"http://10.0.0.1/": first}, request)
    await _handle_route(route, request)
    assert route.fetched == ["http://10.0.0.1/"]
    assert route.fulfilled is first


@pytest.mark.parametrize("status", [307, 308])
async def test_a_method_preserving_redirect_of_a_post_is_still_refused(status: int) -> None:
    request = _Request("http://10.0.0.1/form", method="POST")
    route = _Route({"http://10.0.0.1/form": _Response(status, "http://10.0.0.1/next")}, request)
    await _handle_route(route, request)
    assert route.aborted == "blockedbyclient"
    assert route.fulfilled_body is None


async def test_a_redirect_to_a_non_http_scheme_is_still_refused() -> None:
    request = _Request("http://10.0.0.1/")
    route = _Route({"http://10.0.0.1/": _Response(302, "javascript:alert(1)")}, request)
    await _handle_route(route, request)
    assert route.aborted == "blockedbyclient"


async def test_a_tool_navigation_reads_the_verdict_in_a_scoped_context() -> None:
    """A refused or failed later hop must still end ``guarded_navigation``'s wait."""
    context = _Context()
    await install_navigation_guard(context, scope_headers=True)
    assert begin_navigation(_Frame(context)) is not None
    assert ssrf_guard.guards_context(context)
    assert ssrf_guard.guards_frame(_Frame(context))


async def test_an_unscoped_context_keeps_the_plain_await() -> None:
    context = _Context()
    assert begin_navigation(_Frame(context)) is None
    assert not ssrf_guard.guards_context(context)
    assert not ssrf_guard.guards_frame(object())


async def test_inject_headers_installs_the_guard_before_its_own_route() -> None:
    """Context routes run last-registered-first: the guard must be registered first so the header route runs first."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from octowright.session.core_interaction_mixin import SessionInteractionMixin

    class _InjectContext(_Context):
        async def unroute(self, pattern: str, _handler: Any = None) -> None:
            self.registered.append(f"unroute {pattern}")

    class _Session:
        def __init__(self) -> None:
            self.instance_id = "i"
            self.context = _InjectContext()
            self.recorder = SimpleNamespace(record=lambda *a, **k: None)
            self._header_routes: dict[str, object] = {}
            self._injected_headers: dict[str, dict[str, str]] = {}
            self._active_routes: dict[str, object] = {}

        def operation(self, *args: object, **kwargs: object) -> object:
            @asynccontextmanager
            async def _cm():  # type: ignore[no-untyped-def]
                yield None

            return _cm()

        inject_headers = SessionInteractionMixin.inject_headers

    session = _Session()
    await session.inject_headers("**/api/**", {"X-Tag": "t"})
    await session.inject_headers("**/gql", {"X-Tag": "t"})
    assert session.context.registered == ["**/*", "**/api/**", "**/gql"]


class _UnrouteContext(_Context):
    """A context that records removals too, and the page listener the guard adds."""

    def __init__(self) -> None:
        super().__init__()
        self.listeners: list[str] = []

    async def unroute(self, pattern: str, _handler: Any = None) -> None:
        self.registered.append(f"unroute {pattern}")

    def on(self, event: str, *_args: Any) -> None:
        self.listeners.append(event)

    def remove_listener(self, event: str, *_args: Any) -> None:
        self.listeners.remove(event)


async def _release(context: Any) -> bool:
    """What ``uninject_headers`` does under its gated operation."""
    if not ssrf_guard.scoped_header_guard_releasable(context):
        return False
    await context.unroute(*ssrf_guard.navigation_route())
    ssrf_guard.forget_scoped_header_guard(context)
    return True


async def test_releasing_a_scope_only_guard_removes_its_route() -> None:
    context = _UnrouteContext()
    await install_navigation_guard(context, scope_headers=True)
    assert context.listeners == ["page"]

    assert await _release(context) is True

    assert context.registered == ["**/*", "unroute **/*"]
    assert context.listeners == []
    assert not ssrf_guard.guards_context(context)
    # A later scoped header installs it afresh, ahead of its own route.
    await install_navigation_guard(context, scope_headers=True)
    assert context.registered == ["**/*", "unroute **/*", "**/*"]


async def test_releasing_never_removes_the_policy_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", "block-private")
    context = _UnrouteContext()
    await install_navigation_guard(context, scope_headers=True)

    assert await _release(context) is False
    assert "unroute **/*" not in context.registered


async def test_releasing_keeps_a_scope_guard_once_the_policy_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """The route reads the policy per request: with it on now, the route is the policy's guard too."""
    context = _UnrouteContext()
    await install_navigation_guard(context, scope_headers=True)
    monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", "block-private")

    assert await _release(context) is False
    assert "unroute **/*" not in context.registered


async def test_releasing_an_unguarded_context_is_a_no_op() -> None:
    context = _UnrouteContext()
    assert await _release(context) is False
    assert context.registered == []


class _ScopedSession:
    """The session surface ``inject_headers`` / ``uninject_headers`` touch, over a recording context."""

    def __init__(self, *, launch_headers: dict[str, str] | None = None, launch_urls: list[str] | None = None) -> None:
        from types import SimpleNamespace

        self.instance_id = "i"
        self.context = _UnrouteContext()
        self.recorder = SimpleNamespace(record=lambda *a, **k: None)
        self._header_routes: dict[str, object] = {}
        self._injected_headers: dict[str, dict[str, str]] = {}
        self._active_routes: dict[str, object] = {}
        self.extra_http_headers = launch_headers
        self.extra_http_headers_urls = launch_urls

    def operation(self, *args: object, **kwargs: object) -> object:
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _cm():  # type: ignore[no-untyped-def]
            yield None

        return _cm()

    from octowright.session.core_interaction_mixin import SessionInteractionMixin as _Mixin

    inject_headers = _Mixin.inject_headers
    uninject_headers = _Mixin.uninject_headers


async def test_uninjecting_the_last_injection_removes_the_navigation_route() -> None:
    session = _ScopedSession()
    await session.inject_headers("**/api/**", {"X-Tag": "t"})
    await session.inject_headers("**/gql", {"X-Tag": "t"})

    await session.uninject_headers("**/api/**")
    assert "unroute **/*" not in session.context.registered
    assert ssrf_guard.guards_context(session.context)

    await session.uninject_headers("**/gql")
    assert session.context.registered[-1] == "unroute **/*"
    assert not ssrf_guard.guards_context(session.context)


async def test_uninjecting_keeps_the_route_for_scoped_launch_headers() -> None:
    session = _ScopedSession(launch_headers={"X-Launch": "1"}, launch_urls=["**/launch/**"])
    await install_navigation_guard(session.context, scope_headers=True)
    await session.inject_headers("**/api/**", {"X-Tag": "t"})

    await session.uninject_headers("**/api/**")

    assert "unroute **/*" not in session.context.registered
    assert ssrf_guard.guards_context(session.context)


async def test_uninjecting_keeps_the_route_under_the_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", "block-private")
    session = _ScopedSession()
    await install_navigation_guard(session.context)
    await session.inject_headers("**/api/**", {"X-Tag": "t"})

    await session.uninject_headers("**/api/**")

    assert "unroute **/*" not in session.context.registered
