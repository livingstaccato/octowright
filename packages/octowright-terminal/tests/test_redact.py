# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import pytest
from octowright_terminal import redact


def test_is_password_prompt_detects_trailing_prompt() -> None:
    assert redact.is_password_prompt("user@host's password: ")
    assert redact.is_password_prompt("Enter passphrase for key:")
    assert not redact.is_password_prompt("$ ls -la")


def test_should_mask_off_mode_never_masks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_REDACT_INPUTS", "off")
    assert not redact.should_mask(at_password_prompt=True, password_source=True)


def test_should_mask_all_mode_always_masks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_REDACT_INPUTS", "all")
    assert redact.should_mask(at_password_prompt=False, password_source=False)


def test_should_mask_passwords_mode_masks_credentials_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_REDACT_INPUTS", "passwords")
    assert redact.should_mask(at_password_prompt=True, password_source=False)
    assert redact.should_mask(at_password_prompt=False, password_source=True)
    assert not redact.should_mask(at_password_prompt=False, password_source=False)


def test_input_fields_masked_hides_value_keeps_byte_count() -> None:
    assert redact.input_fields("hunter2", masked=True) == {"keys": "***", "byte_count": 7}


def test_input_fields_unmasked_keeps_literal() -> None:
    assert redact.input_fields("ls\n", masked=False) == {"keys": "ls\n"}


def test_should_mask_follows_a_policy_change_without_a_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    """Core reads OCTOWRIGHT_REDACT_INPUTS per call; the plugin read core's
    import-time snapshot, so a runtime change reached browser recordings but
    not terminal ones."""
    from octowright import defaults

    monkeypatch.setattr(defaults, "INPUT_REDACTION_MODE", "off")
    monkeypatch.setenv("OCTOWRIGHT_REDACT_INPUTS", " ALL ")
    assert redact.should_mask(at_password_prompt=False, password_source=False)
    monkeypatch.setenv("OCTOWRIGHT_REDACT_INPUTS", "Off")
    assert not redact.should_mask(at_password_prompt=True, password_source=True)


@pytest.mark.parametrize("value", ["", "of", "everything"])
def test_an_unknown_or_empty_mode_masks_credentials(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("OCTOWRIGHT_REDACT_INPUTS", value)
    assert redact.should_mask(at_password_prompt=False, password_source=True)
    assert not redact.should_mask(at_password_prompt=False, password_source=False)


def test_an_unset_mode_masks_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_REDACT_INPUTS", raising=False)
    assert redact.should_mask(at_password_prompt=True, password_source=False)
