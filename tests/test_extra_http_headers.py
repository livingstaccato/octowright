# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Launch-time extra HTTP headers (Playwright context ``extra_http_headers``).

Chosen over a route interceptor because it is the only layer that also covers
popups, new tabs and subresources, and because it was measured to apply to the
SSRF guard's own validation fetch as well -- so the hop the guard checks and
the hop the browser makes carry the same headers.
"""

from __future__ import annotations

import inspect
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from octowright.artifacts.script_export_actions import EXPORT_DISPATCH
from octowright.browser_pool.launch_helpers import (
    extra_http_headers_kwargs,
    install_context_routes,
    install_scoped_header_routes,
)
from octowright.browser_pool.options import LaunchOptions
from octowright.http_headers import (
    MAX_EXTRA_HTTP_HEADER_URLS,
    MAX_EXTRA_HTTP_HEADERS,
    REDACTED_HEADER_PLACEHOLDER,
    is_credential_header,
    redact_header_values,
)
from octowright.session.core_interaction_mixin import SessionInteractionMixin, _reject_redacted_headers
from octowright.session.core_network_mixin import _recorded_headers


def test_headers_reach_the_pool_kwargs() -> None:
    opts = LaunchOptions(extra_http_headers={"X-Env": "staging"})

    assert opts.to_pool_kwargs()["extra_http_headers"] == {"X-Env": "staging"}


def test_a_launch_without_headers_passes_none_at_all() -> None:
    """Silent when there is nothing to say: an empty dict is not the same as
    the argument being absent, and every pre-existing launch must be untouched."""
    assert extra_http_headers_kwargs(None) == {}
    assert extra_http_headers_kwargs({}) == {}
    assert extra_http_headers_kwargs({"X-A": "1"}) == {"extra_http_headers": {"X-A": "1"}}


def test_the_context_kwargs_copy_the_mapping() -> None:
    """The context outlives the caller's dict; a later mutation must not
    retroactively change what the browser sends."""
    source = {"X-A": "1"}

    built = extra_http_headers_kwargs(source)
    source["X-B"] = "2"

    assert built["extra_http_headers"] == {"X-A": "1"}


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Bad": "value\r\nX-Injected: evil"},  # CR/LF ends the header, starts another
        {"X-Bad": "value\nX-Injected: evil"},
        {"X-Bad": "null\x00byte"},
        {"Bad Name": "v"},  # space is not an RFC 7230 token character
        {"Bad:Name": "v"},
        {"": "v"},
        {"X-Bad": 1},
        {"X-Bad": None},
    ],
)
def test_a_header_that_could_forge_a_request_is_refused(headers: dict) -> None:
    with pytest.raises(ValueError):
        LaunchOptions(extra_http_headers=headers).validate()


def test_the_map_is_bounded() -> None:
    too_many = {f"X-H{index}": "v" for index in range(MAX_EXTRA_HTTP_HEADERS + 1)}

    with pytest.raises(ValueError, match="at most"):
        LaunchOptions(extra_http_headers=too_many).validate()


def test_an_over_long_value_is_refused() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        LaunchOptions(extra_http_headers={"X-Big": "x" * 100_000}).validate()


def test_ordinary_headers_are_accepted() -> None:
    LaunchOptions(
        extra_http_headers={"Authorization": "Bearer abc.def", "X-Env": "staging", "Accept-Language": "en-GB"}
    ).validate()


def test_a_poisoned_recording_cannot_inject_headers_on_relaunch() -> None:
    """The security property, and the reason this is not simply restored like
    ``kind`` or ``har_path``: a JSONL recording is untrusted input (another
    local user, a poisoned CI step), and a header it could set would ride
    EVERY request the relaunched browser makes -- an attacker-chosen
    ``Authorization``/``Cookie`` attached to every site the user then visits.
    Same exclusion ``channel``/``executable_path``/``launch_args`` already get.
    """
    record = {
        "kind": "chromium",
        "url": "https://example.test/",
        "extra_http_headers": {"Authorization": "Bearer attacker-token"},
    }

    restored = LaunchOptions.from_launch_record(record)

    assert restored.extra_http_headers is None


# ─── page-level override (the macro action) ──────────────────────────────────


class TestPageLevelHeaders:
    """Measured page-over-context precedence on chromium, firefox and webkit
    (Playwright 1.62). Per PAGE, so a popup opened afterwards does not inherit
    them -- that is why the launch-time option exists alongside this."""

    def test_the_action_maps_to_a_method_that_exists(self) -> None:
        """The replay invariant: an entry in _ACTION_MAP whose method is missing
        makes every recorded occurrence a silent skip."""
        from octowright.macros.runtime import _ACTION_MAP
        from octowright.session.core_interaction_mixin import SessionInteractionMixin

        method = _ACTION_MAP["set_extra_http_headers"]

        assert hasattr(SessionInteractionMixin, method)

    def test_a_credential_header_is_scrubbed_under_the_default_policy(self) -> None:
        """Unlike press_key/evaluate -- selector-less sinks that cannot classify
        their own value and so are scrubbed only under `all` -- a header carries
        its NAME, and the name says whether the value is a secret."""
        scrubbed = redact_header_values({"Authorization": "Bearer s3cret", "X-Env": "staging"}, "passwords")

        assert scrubbed == {"Authorization": REDACTED_HEADER_PLACEHOLDER, "X-Env": "staging"}

    def test_all_scrubs_every_value_and_off_scrubs_none(self) -> None:
        headers = {"Authorization": "Bearer s3cret", "X-Env": "staging"}

        assert redact_header_values(headers, "all") == {
            "Authorization": REDACTED_HEADER_PLACEHOLDER,
            "X-Env": REDACTED_HEADER_PLACEHOLDER,
        }
        assert redact_header_values(headers, "off") == headers

    def test_names_are_never_scrubbed(self) -> None:
        """Which headers a run set is the diagnostic value; the name is not the secret."""
        assert sorted(redact_header_values({"Authorization": "x"}, "all")) == ["Authorization"]

    @pytest.mark.parametrize(
        "name", ["Authorization", "authorization", "Cookie", "X-Api-Key", "X-Session-Token", "proxy-authorization"]
    )
    def test_credential_header_names_are_recognised(self, name: str) -> None:
        assert is_credential_header(name)

    @pytest.mark.parametrize("name", ["X-Env", "Accept-Language", "X-Request-Id", "User-Agent"])
    def test_benign_header_names_are_not(self, name: str) -> None:
        assert not is_credential_header(name)

    def test_replaying_a_scrubbed_value_fails_with_the_fix_in_the_message(self) -> None:
        """A macro saved from a recording carries the placeholder, not the token.
        Sending it would authenticate as nobody and surface as a confusing 401
        several actions later."""
        with pytest.raises(ValueError, match="redaction placeholder"):
            _reject_redacted_headers({"Authorization": REDACTED_HEADER_PLACEHOLDER})

    def test_a_real_value_replays_normally(self) -> None:
        _reject_redacted_headers({"Authorization": "Bearer real", "X-Env": "staging"})


# ─── per-endpoint injection (the route layer) ────────────────────────────────


class TestPerEndpointInjection:
    """The most expensive layer, and the only one that can vary by URL.

    Verified end to end on chromium, firefox and webkit: a matching request got
    the header, a non-matching one was untouched, and uninject removed it.
    """

    def test_both_actions_map_to_methods_that_exist(self) -> None:
        from octowright.macros.runtime import _ACTION_MAP
        from octowright.session.core_interaction_mixin import SessionInteractionMixin

        for kind in ("inject_headers", "uninject_headers"):
            assert hasattr(SessionInteractionMixin, _ACTION_MAP[kind]), kind

    def test_the_recorded_field_is_renamed_for_replay(self) -> None:
        """The recorder writes ``pattern``; the method's parameter is
        ``url_pattern``. Without the rename every replay raises TypeError --
        the bug mock_route/unmock_route already shipped once."""
        from octowright.macros.runtime import _REPLAY_RENAME_KEYS

        assert _REPLAY_RENAME_KEYS["inject_headers"] == {"pattern": "url_pattern"}
        assert _REPLAY_RENAME_KEYS["uninject_headers"] == {"pattern": "url_pattern"}

    def test_injection_registry_is_separate_from_mocks(self) -> None:
        """A mock and an injector may share a pattern; one registry would make
        the second install evict the first's handler reference and leak it."""
        from octowright.session.core import BrowserSession

        fields = BrowserSession.__dataclass_fields__

        assert "_header_routes" in fields
        assert fields["_header_routes"].name != fields["_active_routes"].name


# ─── context scope + observability ───────────────────────────────────────────


class TestContextScopedInjection:
    """`inject_headers` was a `page.route` and died at the page boundary.

    A caller had to re-register after every page switch and hope they caught
    them all -- and the interesting traffic is often exactly in the popup (a
    field report hit this with a test player that runs in one). Measured on all
    three engines: a context route sees a popup's requests; a page route does
    not, and the end-to-end run confirms the popup now receives the header.
    """

    def test_it_registers_on_the_context_not_the_page(self) -> None:
        source = Path(inspect.getsourcefile(SessionInteractionMixin) or "").read_text(encoding="utf-8")
        body = source.split("async def inject_headers")[1].split("async def uninject_headers")[0]

        assert "self.context.route(" in body
        assert "self.page.route(" not in body

    def test_removal_unroutes_the_context_too(self) -> None:
        source = Path(inspect.getsourcefile(SessionInteractionMixin) or "").read_text(encoding="utf-8")
        body = source.split("async def uninject_headers")[1].split("async def set_extra_http_headers")[0]

        assert "self.context.unroute(" in body


class TestScopedLaunchHeaders:
    """Launch headers with no URL filter are context-level and ride EVERY
    request, including cross-origin subresources -- which on Chromium makes
    them CORS-preflighted, so a third party that does not echo
    Access-Control-Allow-Headers rejects them outright (measured; seen in the
    field as blocked font/CDN requests). Firefox and WebKit applied the header
    below the CORS check and were unaffected, so it is Chromium-specific."""

    def test_patterns_move_the_headers_off_the_context(self) -> None:
        """Otherwise they would apply twice: unscoped AND scoped, which defeats
        the entire point of scoping them."""
        assert extra_http_headers_kwargs({"X-A": "1"}, ["**/api/**"]) == {}

    def test_without_patterns_they_stay_context_level(self) -> None:
        assert extra_http_headers_kwargs({"X-A": "1"}, None) == {"extra_http_headers": {"X-A": "1"}}

    def test_patterns_alone_do_nothing(self) -> None:
        """A filter with nothing to filter is not an error, just a no-op."""
        assert extra_http_headers_kwargs(None, ["**/api/**"]) == {}

    def test_they_survive_the_round_trip_to_pool_kwargs(self) -> None:
        opts = LaunchOptions(extra_http_headers={"X-A": "1"}, extra_http_headers_urls=["**/api/**"])

        assert opts.to_pool_kwargs()["extra_http_headers_urls"] == ["**/api/**"]


class TestNetworkHeaderObservability:
    """These records carried no headers at all, so every header feature was
    unverifiable from the tool surface: a field report set a launch header,
    checked here to confirm it applied, saw nothing, and nearly concluded the
    feature was broken."""

    def test_headers_are_recorded(self) -> None:
        request = SimpleNamespace(headers={"Trk-ID": "abc", "user-agent": "x"})

        assert _recorded_headers(request)["Trk-ID"] == "abc"

    def test_credentials_are_scrubbed_by_name(self) -> None:
        """This output goes to an LLM, and a browser sends Cookie and
        Authorization on ordinary requests."""
        request = SimpleNamespace(headers={"Authorization": "Bearer s3cret", "Cookie": "sid=1", "X-Env": "staging"})

        recorded = _recorded_headers(request)

        assert recorded["Authorization"] == REDACTED_HEADER_PLACEHOLDER
        assert recorded["Cookie"] == REDACTED_HEADER_PLACEHOLDER
        assert recorded["X-Env"] == "staging"

    def test_an_unreadable_request_never_breaks_recording(self) -> None:
        """This runs in a network event handler; it must not be the thing that
        raises."""

        class _Broken:
            @property
            def headers(self) -> dict[str, str]:
                raise RuntimeError("gone")

        assert _recorded_headers(_Broken()) == {}


class TestGuardOrdering:
    """The scoped header routes must be registered LAST.

    Playwright runs context route handlers last-registered-first, and
    ``install_navigation_guard`` is itself a context route. Registered BEFORE
    the guard, the injector runs after it -- so the guard's own
    ``route.fetch(max_redirects=0)`` validation hop carries none of the headers
    while the browser's real request (reached via the guard's ``fallback()``)
    carries all of them. The chain the guard checked would then not be the
    chain the browser follows: an unauthenticated validation fetch can be
    answered with an allowed redirect while the authenticated request the
    browser actually makes redirects somewhere the policy would have refused.
    """

    async def test_the_ssrf_guard_registers_before_the_header_routes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", "block-private")
        registered: list[str] = []

        class _Context:
            async def route(self, pattern: str, handler: object) -> None:
                registered.append(pattern)

        await install_context_routes(_Context(), {"X-A": "1"}, ["**/api/**"])

        assert registered == ["**/*", "**/api/**"]

    async def test_the_order_holds_for_several_patterns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", "block-private")
        registered: list[str] = []

        class _Context:
            async def route(self, pattern: str, handler: object) -> None:
                registered.append(pattern)

        await install_context_routes(_Context(), {"X-A": "1"}, ["**/api/**", "**/gql"])

        assert registered[0] == "**/*"

    async def test_a_dead_route_does_not_escape_into_playwrights_dispatcher(self) -> None:
        """A page that navigated away raises on ``fallback`` and ``abort`` alike
        -- the same reason ``ssrf_guard._handle_route`` wraps its own body. Let
        loose, the exception leaves the intercepted request unanswered and the
        load hangs until it times out."""
        captured: list[object] = []

        class _Context:
            async def route(self, pattern: str, handler: object) -> None:
                captured.append(handler)

        await install_scoped_header_routes(_Context(), {"X-A": "1"}, ["**/api/**"])

        class _DeadRoute:
            request = SimpleNamespace(headers={})

            async def fallback(self, **kwargs: object) -> None:
                raise RuntimeError("Target page, context or browser has been closed")

        await captured[0](_DeadRoute())


class TestScopedPatternValidation:
    """``extra_http_headers`` is validated; the patterns that SCOPE it were not.

    ``POST /api/sessions`` feeds a raw JSON body to ``from_mapping``, and the
    MCP ``browser_launch`` builds a ``LaunchOptions`` directly -- so an
    unvalidated value reaches ``context.route`` verbatim.
    """

    def test_a_string_is_refused_rather_than_iterated_per_character(self) -> None:
        """A bare string iterates CHARACTERS: one context route per char --
        ``*``, ``/``, ``a`` -- which attaches the credential to unrelated
        origins, the exact opposite of what scoping is for."""
        with pytest.raises(ValueError, match="list"):
            LaunchOptions(
                extra_http_headers={"Authorization": "Bearer x"},
                extra_http_headers_urls="**/api/**",  # type: ignore[arg-type]
            ).to_pool_kwargs()

    def test_a_non_string_element_is_refused_at_the_edge(self) -> None:
        with pytest.raises(ValueError, match="string"):
            LaunchOptions(
                extra_http_headers={"X-A": "1"},
                extra_http_headers_urls=[None],  # type: ignore[list-item]
            ).to_pool_kwargs()

    def test_an_empty_pattern_is_refused(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            LaunchOptions(extra_http_headers={"X-A": "1"}, extra_http_headers_urls=[""]).to_pool_kwargs()

    def test_the_pattern_list_is_bounded(self) -> None:
        with pytest.raises(ValueError, match="at most"):
            LaunchOptions(
                extra_http_headers={"X-A": "1"},
                extra_http_headers_urls=[f"**/p{i}/**" for i in range(MAX_EXTRA_HTTP_HEADER_URLS + 1)],
            ).to_pool_kwargs()

    def test_an_empty_list_is_refused_rather_than_failing_open(self) -> None:
        """``[]`` most naturally reads as "scope to nothing"; the old truthiness
        check read it as "no scoping" and sent the headers on EVERY request.
        For a security-adjacent knob, failing open in the credential-spraying
        direction is the wrong way to be wrong -- so it is an error."""
        with pytest.raises(ValueError, match="non-empty"):
            LaunchOptions(extra_http_headers={"X-A": "1"}, extra_http_headers_urls=[]).to_pool_kwargs()

    def test_an_empty_list_never_falls_back_to_unscoped_headers(self) -> None:
        """The helper is callable on its own; it must not fail open either."""
        assert extra_http_headers_kwargs({"X-A": "1"}, []) == {}

    async def test_an_empty_list_registers_no_routes(self) -> None:
        registered: list[str] = []

        class _Context:
            async def route(self, pattern: str, handler: object) -> None:
                registered.append(pattern)

        await install_scoped_header_routes(_Context(), {"X-A": "1"}, [])

        assert registered == []

    def test_ordinary_patterns_are_accepted(self) -> None:
        opts = LaunchOptions(extra_http_headers={"X-A": "1"}, extra_http_headers_urls=["**/api/**"])

        assert opts.to_pool_kwargs()["extra_http_headers_urls"] == ["**/api/**"]

    def test_the_launch_funnel_validates_the_headers_too(self) -> None:
        """``to_pool_kwargs`` is what every launch path funnels through;
        ``validate()`` is only reached from ``from_mapping``, so the MCP
        ``browser_launch`` path was unchecked."""
        with pytest.raises(ValueError, match="control character"):
            LaunchOptions(extra_http_headers={"X-A": "one\r\nX-B: two"}).to_pool_kwargs()


class TestExportedScriptParity:
    """A macro recorded against the popup case ``inject_headers`` now covers
    must not pass live and fail in the exported standalone script."""

    def test_the_exported_injector_routes_on_the_context(self) -> None:
        body = EXPORT_DISPATCH["inject_headers"]

        assert "_page(state).context.route(" in body
        assert "_page(state).route(" not in body

    def test_the_exported_remover_unroutes_the_context(self) -> None:
        body = EXPORT_DISPATCH["uninject_headers"]

        assert "_page(state).context.unroute(" in body
        assert "_page(state).unroute(" not in body

    def test_mock_route_stays_page_level(self) -> None:
        """``mock_route`` is a PAGE route live, and page routes take precedence
        over context ones -- moving it would change which handler wins."""
        assert "_page(state).route(" in EXPORT_DISPATCH["mock_route"]


class _StubRoutes:
    """Minimal session surface the two route-installing methods touch.

    ``gated_operation`` reads ``self.operation`` dynamically, so an object with
    an async context-manager ``operation()`` is enough -- no real gate, page or
    browser needed.
    """

    def __init__(self) -> None:
        self.instance_id = "i"
        self._header_routes: dict[str, object] = {}
        self._injected_headers: dict[str, dict[str, str]] = {}
        self._active_routes: dict[str, object] = {}
        self.page = SimpleNamespace(route=self._noop, unroute=self._noop)
        self.context = SimpleNamespace(route=self._noop, unroute=self._noop)
        self.recorder = SimpleNamespace(record=lambda *a, **k: None)

    async def _noop(self, *args: object, **kwargs: object) -> None:
        return None

    def operation(self, *args: object, **kwargs: object) -> Any:
        @asynccontextmanager
        async def _cm() -> Any:
            yield None

        return _cm()

    inject_headers = SessionInteractionMixin.inject_headers
    uninject_headers = SessionInteractionMixin.uninject_headers
    mock_route = SessionInteractionMixin.mock_route


class TestMockShadowWarning:
    """A page-level mock wins over a context-level injector in EITHER order.

    While both were page routes, last-registered-first meant only the
    mock-then-inject order lost, so a single warning on ``inject_headers``
    covered it. With the injector on the context, page-before-context decides
    it instead and the other order loses silently -- hence the mirror.
    """

    async def test_installing_an_injector_over_a_mock_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        subject = _StubRoutes()
        await subject.mock_route("**/api/**", body="{}")

        with caplog.at_level(logging.WARNING):
            await subject.inject_headers("**/api/**", {"X-A": "1"})

        assert "header_injection_shadowed_by_mock" in caplog.text

    async def test_installing_a_mock_over_an_injector_warns_too(self, caplog: pytest.LogCaptureFixture) -> None:
        subject = _StubRoutes()
        await subject.inject_headers("**/api/**", {"X-A": "1"})

        with caplog.at_level(logging.WARNING):
            await subject.mock_route("**/api/**", body="{}")

        assert "header_injection_shadowed_by_mock" in caplog.text

    async def test_no_warning_without_a_collision(self, caplog: pytest.LogCaptureFixture) -> None:
        subject = _StubRoutes()
        await subject.inject_headers("**/api/**", {"X-A": "1"})

        with caplog.at_level(logging.WARNING):
            await subject.mock_route("**/other/**", body="{}")

        assert "header_injection_shadowed_by_mock" not in caplog.text
