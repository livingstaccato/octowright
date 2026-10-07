# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Warn mode of the credential-fill origin check: what it records, and where.

``OCTOWRIGHT_MACRO_CREDENTIAL_FILL_ORIGINS=warn`` lets a credential be typed on
a foreign origin, so the operator's log line and the run result's
``credential_fill_offsite`` are the only trace that it happened. The log line is
the audit record for the daemon log: its event name and fields are pinned
exactly, and it carries the origin -- never the page's path or the value.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from octowright.credential_sinks import CREDENTIAL_FILL_MARKER, CredentialRefusal
from octowright.macros import credential_fill as cf

ACTION = {"action": "fill", "selector": "#pw", "value": "s3cret", CREDENTIAL_FILL_MARKER: ["password"]}


def _session() -> Any:
    return SimpleNamespace(instance_id="b-1", launch_url="https://app.test/", base_url=None)


@pytest.fixture
def warned(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    monkeypatch.delenv("OCTOWRIGHT_MACRO_CREDENTIAL_SINKS", raising=False)
    monkeypatch.setenv("OCTOWRIGHT_MACRO_CREDENTIAL_FILL_ORIGINS", "warn")
    log = MagicMock()
    monkeypatch.setattr(cf, "log", log)
    return log


def test_warn_mode_logs_and_records_each_foreign_origin_once(warned: MagicMock) -> None:
    audit, token = cf.begin_fill_audit()
    try:
        audit.step = 3
        check = cf._OriginCheck(_session(), ACTION)
        check("https://evil.test/login?next=/x")
        check("https://evil.test/other")
        check("https://app.test/ok")
    finally:
        cf.end_fill_audit(token)
    warned.warning.assert_called_once_with(
        "octowright.macro.credential_fill_offsite",
        instance_id="b-1",
        action="fill",
        origin="https://evil.test",
        step=3,
    )
    assert cf.offsite_fields(audit) == {
        "credential_fill_offsite": [{"step": 3, "action": "fill", "origin": "https://evil.test"}]
    }


def test_warn_mode_outside_a_run_logs_without_a_step(warned: MagicMock) -> None:
    cf._OriginCheck(_session(), ACTION)("https://evil.test/")
    warned.warning.assert_called_once_with(
        "octowright.macro.credential_fill_offsite",
        instance_id="b-1",
        action="fill",
        origin="https://evil.test",
        step=None,
    )


def test_block_mode_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_MACRO_CREDENTIAL_SINKS", raising=False)
    monkeypatch.delenv("OCTOWRIGHT_MACRO_CREDENTIAL_FILL_ORIGINS", raising=False)
    with pytest.raises(CredentialRefusal, match=r"into a page at https://evil\.test,"):
        cf._OriginCheck(_session(), ACTION)("https://evil.test/")


def test_a_clean_audit_adds_nothing_to_the_result() -> None:
    audit, token = cf.begin_fill_audit()
    cf.end_fill_audit(token)
    assert cf.offsite_fields(audit) == {}
