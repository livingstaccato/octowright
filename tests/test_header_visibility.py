# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A browser must be able to say which extra HTTP headers it is sending.

`extra_http_headers` was a launch argument only. It went into
`new_context(extra_http_headers=...)` and was then known to Playwright alone --
`BrowserSession` kept no copy, and neither did the page-level
`set_extra_http_headers` or the per-pattern `inject_headers` (which stored the
route *closure*, not the headers it merges). So nothing could answer "what is
this browser actually sending?".

Reported downstream: a consumer that tags outbound traffic with a per-run
header and later ADOPTS an already-running browser could not tell whether that
browser carried the current run's tag or a stale one, and worked around it with
a process-local set of instance_ids it had launched -- wrong across process
restarts and blind to any other client's browsers.

THE THREE SCOPES ARE REPORTED SEPARATELY, NOT MERGED. They have genuinely
different reach, and a merged view would assert a precedence that does not
hold uniformly: launch headers are context-level and ride every request in the
browser; page headers cover ONE page and override the context on it; injected
headers are context routes matching a URL glob. Flattening them would make a
page-scoped token look browser-wide.

REDACTION IS NOT OPTIONAL HERE -- see TestRedaction for why this surface is
deliberately stricter than the recorder's policy.
"""

from __future__ import annotations

import pytest

from octowright.http_headers import REDACTED_HEADER_PLACEHOLDER, redact_headers_for_report


class TestRedaction:
    """Reuses the recorder's header-NAME classification, but not its `off` mode.

    `OCTOWRIGHT_REDACT_INPUTS=off` is documented as a legacy opt-in for
    RECORDINGS -- a 0600 file on the operator's own disk. This surface is not
    that: `browser_list` output crosses the MCP transport to any connected
    client and into an LLM's context. Honouring `off` here would turn a
    recording-privacy setting into "ship my bearer token to every caller",
    which no one setting that variable is asking for.

    So the allow/deny logic is shared (`is_credential_header`, via
    `redact_header_values`) and only the mode floor differs: `off` is treated
    as `passwords`, and `all` is still honoured because it is stricter.
    """

    @pytest.mark.parametrize("name", ["Authorization", "authorization", "Cookie", "X-Api-Key", "X-Auth-Token"])
    def test_a_credential_header_is_never_emitted_raw(self, name: str) -> None:
        assert redact_headers_for_report({name: "sekrit"}) == {name: REDACTED_HEADER_PLACEHOLDER}

    @pytest.mark.parametrize("mode", ["off", "passwords", "all", "", "bogus"])
    def test_no_mode_can_expose_a_credential(self, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
        """The hard requirement: there is no configuration under which this
        surface emits a raw Authorization."""
        monkeypatch.setenv("OCTOWRIGHT_REDACT_INPUTS", mode)

        assert redact_headers_for_report({"Authorization": "Bearer sekrit"}) == {
            "Authorization": REDACTED_HEADER_PLACEHOLDER
        }

    def test_a_benign_header_stays_readable_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The whole point of the feature: the run tag must be legible, or a
        consumer still cannot tell a current tag from a stale one."""
        monkeypatch.setenv("OCTOWRIGHT_REDACT_INPUTS", "passwords")

        assert redact_headers_for_report({"X-Run-Id": "run-42"}) == {"X-Run-Id": "run-42"}

    def test_all_mode_is_honoured_because_it_is_stricter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OCTOWRIGHT_REDACT_INPUTS", "all")

        assert redact_headers_for_report({"X-Run-Id": "run-42"}) == {"X-Run-Id": REDACTED_HEADER_PLACEHOLDER}

    def test_off_is_floored_to_passwords_not_honoured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OCTOWRIGHT_REDACT_INPUTS", "off")
        out = redact_headers_for_report({"Authorization": "Bearer x", "X-Env": "staging"})

        assert out == {"Authorization": REDACTED_HEADER_PLACEHOLDER, "X-Env": "staging"}

    def test_names_are_never_scrubbed(self) -> None:
        """Which headers a browser sets is the diagnostic value, and the name
        is not the secret -- the same call the recorder makes."""
        assert list(redact_headers_for_report({"Authorization": "x"})) == ["Authorization"]


class TestSessionHeaderState:
    def test_a_session_with_no_headers_reports_an_empty_map(self, header_session: object) -> None:
        """Empty means "nothing set anywhere", unambiguously -- a consumer can
        rely on the key existing rather than distinguishing absent from unset."""
        assert header_session.header_state() == {}  # type: ignore[attr-defined]

    def test_launch_headers_are_reported_under_launch(self, header_session: object) -> None:
        header_session.extra_http_headers = {"X-Run-Id": "run-42"}  # type: ignore[attr-defined]

        assert header_session.header_state() == {"launch": {"X-Run-Id": "run-42"}}  # type: ignore[attr-defined]

    def test_launch_url_patterns_ride_along(self, header_session: object) -> None:
        """Scoped launch headers do NOT ride every request, so reporting the
        headers without the globs would overstate their reach."""
        header_session.extra_http_headers = {"X-Run-Id": "run-42"}  # type: ignore[attr-defined]
        header_session.extra_http_headers_urls = ["**/api/**"]  # type: ignore[attr-defined]

        state = header_session.header_state()  # type: ignore[attr-defined]

        assert state["launch_url_patterns"] == ["**/api/**"]

    def test_launch_credentials_are_redacted(self, header_session: object) -> None:
        header_session.extra_http_headers = {"Authorization": "Bearer sekrit"}  # type: ignore[attr-defined]

        assert header_session.header_state()["launch"] == {  # type: ignore[attr-defined]
            "Authorization": REDACTED_HEADER_PLACEHOLDER
        }

    async def test_page_headers_are_reported_separately(self, header_session: object) -> None:
        await header_session.set_extra_http_headers({"X-Page": "1"})  # type: ignore[attr-defined]

        state = header_session.header_state()  # type: ignore[attr-defined]

        assert state["page"] == {"X-Page": "1"}
        assert "launch" not in state

    async def test_injected_headers_are_keyed_by_pattern(self, header_session: object) -> None:
        await header_session.inject_headers("**/api/**", {"X-Tag": "t"})  # type: ignore[attr-defined]

        assert header_session.header_state()["injected"] == {"**/api/**": {"X-Tag": "t"}}  # type: ignore[attr-defined]

    async def test_uninjecting_removes_it_from_the_report(self, header_session: object) -> None:
        await header_session.inject_headers("**/api/**", {"X-Tag": "t"})  # type: ignore[attr-defined]
        await header_session.uninject_headers("**/api/**")  # type: ignore[attr-defined]

        assert header_session.header_state() == {}  # type: ignore[attr-defined]

    async def test_the_three_scopes_do_not_merge(self, header_session: object) -> None:
        header_session.extra_http_headers = {"X-Launch": "l"}  # type: ignore[attr-defined]
        await header_session.set_extra_http_headers({"X-Page": "p"})  # type: ignore[attr-defined]
        await header_session.inject_headers("**/api/**", {"X-Inj": "i"})  # type: ignore[attr-defined]

        state = header_session.header_state()  # type: ignore[attr-defined]

        assert state["launch"] == {"X-Launch": "l"}
        assert state["page"] == {"X-Page": "p"}
        assert state["injected"] == {"**/api/**": {"X-Inj": "i"}}


class TestBrowserListEntry:
    def test_the_entry_carries_the_header_state(self) -> None:
        """The reported gap: a browser_list entry had no way to say what a
        browser was sending, so an adopting consumer had to track it locally."""
        import inspect

        from octowright.browser_pool.pool import BrowserPool

        source = inspect.getsource(BrowserPool.list_sessions)

        assert '"extra_http_headers"' in source
        assert "header_state()" in source


@pytest.fixture
def header_session():  # type: ignore[no-untyped-def]
    """A session exposing the real header methods over fake Playwright objects.

    Composed from the production mixin rather than reimplemented, so a change
    to how the state is stored fails here instead of passing against a stub.
    """
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from octowright.session.core_interaction_mixin import SessionInteractionMixin

    class _Page:
        async def set_extra_http_headers(self, headers: dict[str, str]) -> None:
            self.headers = dict(headers)

    class _Context:
        async def route(self, pattern: str, handler: object) -> None:
            return None

        async def unroute(self, pattern: str, handler: object = None) -> None:
            return None

    class _Session:
        def __init__(self) -> None:
            self.instance_id = "i"
            self.page = _Page()
            self.context = _Context()
            self.recorder = SimpleNamespace(record=lambda *a, **k: None)
            self.extra_http_headers: dict[str, str] | None = None
            self.extra_http_headers_urls: list[str] | None = None
            self._header_routes: dict[str, object] = {}
            self._injected_headers: dict[str, dict[str, str]] = {}
            self._active_routes: dict[str, object] = {}
            self._page_extra_headers: dict[str, str] | None = None

        def operation(self, *args: object, **kwargs: object) -> object:
            @asynccontextmanager
            async def _cm():  # type: ignore[no-untyped-def]
                yield None

            return _cm()

        inject_headers = SessionInteractionMixin.inject_headers
        uninject_headers = SessionInteractionMixin.uninject_headers
        set_extra_http_headers = SessionInteractionMixin.set_extra_http_headers
        header_state = SessionInteractionMixin.header_state

    return _Session()


class TestLaunchWiring:
    """The launch argument has to reach the session or `launch` is always empty
    in production while the unit tests pass against a hand-set attribute."""

    def test_launch_publish_copies_both_fields_onto_the_session(self) -> None:
        import inspect

        from octowright.browser_pool import launch_publish

        source = inspect.getsource(launch_publish)

        assert "extra_http_headers=dict(launch_options.extra_http_headers)" in source
        assert "extra_http_headers_urls=(" in source

    def test_the_session_dataclass_declares_them(self) -> None:
        from octowright.session.core import BrowserSession

        fields = BrowserSession.__dataclass_fields__

        assert "extra_http_headers" in fields
        assert "extra_http_headers_urls" in fields
        assert "_injected_headers" in fields
        assert "_page_extra_headers" in fields

    def test_the_copies_are_defensive(self) -> None:
        """The session outlives the caller's dict; a later mutation must not
        retroactively change what the browser reports."""
        import inspect

        from octowright.browser_pool import launch_publish

        source = inspect.getsource(launch_publish)

        assert "dict(launch_options.extra_http_headers)" in source
        assert "list(launch_options.extra_http_headers_urls)" in source


class TestClearingPageHeaders:
    async def test_setting_an_empty_map_clears_the_reported_page_headers(self, header_session: object) -> None:
        """Playwright treats `set_extra_http_headers({})` as clearing the page's
        headers, so leaving the previous map in the report would claim the
        browser is still sending something it is not."""
        await header_session.set_extra_http_headers({"X-Page": "1"})  # type: ignore[attr-defined]
        await header_session.set_extra_http_headers({})  # type: ignore[attr-defined]

        assert header_session.header_state() == {}  # type: ignore[attr-defined]

    async def test_a_later_call_replaces_rather_than_merges(self, header_session: object) -> None:
        """Matching Playwright: each call sets the page's full header map."""
        await header_session.set_extra_http_headers({"X-A": "1"})  # type: ignore[attr-defined]
        await header_session.set_extra_http_headers({"X-B": "2"})  # type: ignore[attr-defined]

        assert header_session.header_state()["page"] == {"X-B": "2"}  # type: ignore[attr-defined]
