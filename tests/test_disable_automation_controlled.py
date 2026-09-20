# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import pytest

from octowright.browser_pool.options import LaunchOptions
from octowright.request_errors import InvalidRequestError


def test_option_is_off_by_default() -> None:
    assert LaunchOptions().disable_automation_controlled is False


def test_fixed_option_needs_no_arbitrary_argv_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_ALLOW_EXECUTABLE_PATH", raising=False)

    LaunchOptions(disable_automation_controlled=True).validate()
    assert LaunchOptions(disable_automation_controlled=True).to_pool_kwargs()["disable_automation_controlled"] is True


@pytest.mark.parametrize("kind", ["firefox", "webkit"])
def test_enabled_option_rejects_non_chromium_in_mapping_path(kind: str) -> None:
    with pytest.raises(InvalidRequestError, match="only supported for kind='chromium'"):
        LaunchOptions.from_mapping({"kind": kind, "disable_automation_controlled": True})


@pytest.mark.parametrize("kind", ["firefox", "webkit"])
def test_enabled_option_rejects_non_chromium_in_direct_mcp_path(kind: str) -> None:
    with pytest.raises(InvalidRequestError, match="only supported for kind='chromium'"):
        LaunchOptions(kind=kind, disable_automation_controlled=True).to_pool_kwargs()


@pytest.mark.parametrize("kind", ["chromium", "firefox", "webkit"])
def test_disabled_option_is_valid_for_every_engine(kind: str) -> None:
    LaunchOptions(kind=kind, disable_automation_controlled=False).validate()
