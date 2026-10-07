# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Explicit thresholds override the configured ones; absent ones read the environment."""

from __future__ import annotations

import pytest

from octowright.macros.scrub_admission import (
    SCRUB_COMMON_VALUES_ENV,
    SCRUB_MIN_LENGTH_ENV,
    scrub_exemption_reason,
)


@pytest.fixture(autouse=True)
def _default_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SCRUB_MIN_LENGTH_ENV, raising=False)
    monkeypatch.delenv(SCRUB_COMMON_VALUES_ENV, raising=False)


def test_the_configured_floor_applies_when_none_is_given() -> None:
    assert scrub_exemption_reason("abc") == "short"
    assert scrub_exemption_reason("abcd") is None


def test_an_explicit_floor_overrides_the_configured_one() -> None:
    assert scrub_exemption_reason("abc", min_length=0) is None
    assert scrub_exemption_reason("abcdef", min_length=10) == "short"


def test_the_configured_floor_follows_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SCRUB_MIN_LENGTH_ENV, "8")

    assert scrub_exemption_reason("abcdefg") == "short"
    assert scrub_exemption_reason("abcdefg", min_length=2) is None


def test_the_configured_common_list_applies_when_none_is_given() -> None:
    assert scrub_exemption_reason(" Admin ", min_length=0) == "common"


def test_an_explicit_common_list_overrides_the_configured_one() -> None:
    assert scrub_exemption_reason("admin", min_length=0, common=frozenset()) is None
    assert scrub_exemption_reason("acme-corp", min_length=0, common=frozenset({"acme-corp"})) == "common"
