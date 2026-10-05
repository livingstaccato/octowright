# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A boolean under a classified key is never blind-scrubbed; a number still is.

A classified leaf is admitted to the blind-scrub ledger as ``str(value)``.
For ``{"accept_cookies": True}`` that put ``True`` in the session ledger, and
since scrubbing ignores case every ``true``/``false`` on the session -- JSON
bodies, ``aria-expanded`` selectors, ``untrue`` -- became ``<redacted>``. A
boolean carries no secret, so it is left out of the ledger (still redacted by
key in ``args_used``). A number is not: ``{{otp}}`` expands to its digits.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from octowright.artifacts.script_export import render_macro_cli
from octowright.macros.privacy import (
    blind_scrub_arg_values,
    classified_arg_values,
    redact_args,
)


def _exported(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    async_api = types.ModuleType("playwright.async_api")
    async_api.async_playwright = lambda: None  # type: ignore[attr-defined]
    package = types.ModuleType("playwright")
    package.async_api = async_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)
    module: dict[str, Any] = {"__name__": "m"}
    exec(compile(render_macro_cli(name="m", macro={"actions": []}), "<m>", "exec"), module)
    return module


@pytest.mark.parametrize("flag", [True, False])
@pytest.mark.parametrize("key", ["accept_cookies", "Accept_Cookies", "rememberToken", "TOKEN"])
def test_a_boolean_is_not_admitted(key: str, flag: bool) -> None:
    args = {key: flag, "nested": {"password": flag}}
    assert classified_arg_values(args) == ()
    assert blind_scrub_arg_values(args) == ()
    # Still redacted structurally, by its key.
    assert redact_args(args)[key] == "<redacted>"


@pytest.mark.parametrize("flag", [True, False])
def test_the_export_does_not_admit_a_boolean(monkeypatch: pytest.MonkeyPatch, flag: bool) -> None:
    module = _exported(monkeypatch)
    assert list(module["_blind_scrub_arg_values"]({"accept_cookies": flag, "otp": flag})) == []


@pytest.mark.parametrize("otp", [482193, 4821.93])
def test_a_numeric_credential_is_still_admitted(monkeypatch: pytest.MonkeyPatch, otp: float) -> None:
    assert blind_scrub_arg_values({"otp": otp}) == (str(otp),)
    assert str(otp) in list(_exported(monkeypatch)["_blind_scrub_arg_values"]({"otp": otp}))
