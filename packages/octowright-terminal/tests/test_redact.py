# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import pytest
from octowright_terminal import redact

from octowright import defaults


def test_is_password_prompt_detects_trailing_prompt() -> None:
    assert redact.is_password_prompt("user@host's password: ")
    assert redact.is_password_prompt("Enter passphrase for key:")
    assert not redact.is_password_prompt("$ ls -la")


def test_should_mask_off_mode_never_masks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(defaults, "INPUT_REDACTION_MODE", "off")
    assert not redact.should_mask(at_password_prompt=True, password_source=True)


def test_should_mask_all_mode_always_masks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(defaults, "INPUT_REDACTION_MODE", "all")
    assert redact.should_mask(at_password_prompt=False, password_source=False)


def test_should_mask_passwords_mode_masks_credentials_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(defaults, "INPUT_REDACTION_MODE", "passwords")
    assert redact.should_mask(at_password_prompt=True, password_source=False)
    assert redact.should_mask(at_password_prompt=False, password_source=True)
    assert not redact.should_mask(at_password_prompt=False, password_source=False)


def test_input_fields_masked_hides_value_keeps_byte_count() -> None:
    assert redact.input_fields("hunter2", masked=True) == {"keys": "***", "byte_count": 7}


def test_input_fields_unmasked_keeps_literal() -> None:
    assert redact.input_fields("ls\n", masked=False) == {"keys": "ls\n"}
