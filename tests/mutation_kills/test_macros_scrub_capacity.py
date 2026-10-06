# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The scrub-set cap: its setting, and the refusal an agent sees when the set is full."""

from __future__ import annotations

import logging
import uuid

import pytest

from octowright.macros.scrub_capacity import (
    DEFAULT_SCRUB_MAX_VALUES,
    SCRUB_MAX_VALUES_ENV,
    scrub_max_values,
    scrub_set_full,
)
from octowright.request_errors import InvalidRequestError


def test_the_full_scrub_set_refusal_is_exact() -> None:
    error = scrub_set_full(256, 256)

    assert isinstance(error, InvalidRequestError)
    assert str(error) == (
        "this session's scrub set is full (256 of 256 persistent values), so a macro run that "
        "would add a new credential to it is refused before it runs. Relaunch the browser for a fresh "
        "session, or raise OCTOWRIGHT_MACRO_SCRUB_MAX_VALUES (0 disables the cap)."
    )


def test_an_unset_cap_is_the_default_without_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv(SCRUB_MAX_VALUES_ENV, raising=False)
    caplog.set_level(logging.WARNING)

    assert scrub_max_values() == DEFAULT_SCRUB_MAX_VALUES
    assert scrub_max_values({SCRUB_MAX_VALUES_ENV: "  "}) == DEFAULT_SCRUB_MAX_VALUES
    assert caplog.records == []


def test_an_unparsable_cap_keeps_the_default_and_says_which_setting(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    raw = f"lots-{uuid.uuid4().hex}"

    assert scrub_max_values({SCRUB_MAX_VALUES_ENV: raw}) == DEFAULT_SCRUB_MAX_VALUES

    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 1, messages
    assert " octowright.macro.scrub_max_values_invalid [" in messages[0]
    assert f"env={SCRUB_MAX_VALUES_ENV}" in messages[0]
    assert f"default={DEFAULT_SCRUB_MAX_VALUES}" in messages[0]
