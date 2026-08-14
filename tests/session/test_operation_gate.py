# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import pytest

from octowright.session.operation_gate import (
    OperationGateInvariantError,
    SessionBusyTimeoutError,
    SessionClosedError,
    SessionClosingError,
    resolve_operation_queue_timeout_seconds,
    validate_operation_name,
)


def test_operation_timeout_resolution_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS", "41.5")
    assert resolve_operation_queue_timeout_seconds(None) == 41.5
    assert resolve_operation_queue_timeout_seconds(7.0) == 7.0


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf", "nope"])
def test_operation_timeout_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError, match="OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS"):
        resolve_operation_queue_timeout_seconds(None)


def test_gate_errors_are_distinct_runtime_errors() -> None:
    errors = {
        SessionBusyTimeoutError,
        SessionClosingError,
        SessionClosedError,
        OperationGateInvariantError,
    }
    assert len(errors) == 4
    assert all(issubclass(error, RuntimeError) for error in errors)


def test_operation_names_are_fixed_identifiers() -> None:
    assert validate_operation_name("browser_click") == "browser_click"
    for unsafe in ("#password", "https://secret.test", "user supplied", "", "a" * 65):
        with pytest.raises(ValueError, match="fixed identifier"):
            validate_operation_name(unsafe)
