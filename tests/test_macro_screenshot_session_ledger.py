# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A classified macro screenshot is redacted of the session ledger, not only the run's values.

A macro screenshot with classified values, and an artifact run's before/after
screenshots, redacted and proved absent only the run's own arguments. A
password a ``browser_fill`` had admitted to the session ledger earlier was
still on the page, and in the PNG.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright import defaults
from octowright.artifacts.evidence import EvidenceBuilder
from octowright.macros import artifacts, execution, safe_screenshot
from octowright.macros.privacy import admit_redacted_input

TYPED = "hunter2-typed-earlier"  # pragma: allowlist secret (synthetic fixture)
TOKEN = "fixture-run-token"  # pragma: allowlist secret (synthetic fixture)
POLICY_ENV = "OCTOWRIGHT_MACRO_CLASSIFIED_SCREENSHOTS"


def _session() -> MagicMock:
    session = MagicMock()
    session.screenshot = AsyncMock(side_effect=AssertionError("the generic screenshot must not be used"))
    session.durable_text_scrubber = None

    @contextlib.asynccontextmanager
    async def operation(_name: str) -> AsyncIterator[None]:
        yield

    session.operation = operation
    session._octowright_sensitive_screenshot_authority = None
    session._octowright_sensitive_screenshot_handler = None
    admit_redacted_input(session, TYPED)  # what a browser_fill on a password field does
    return session


@pytest.fixture(autouse=True)
def _redacting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    monkeypatch.setattr(defaults, "RECORDINGS_DIR", tmp_path)
    monkeypatch.setenv(POLICY_ENV, "redact")
    redacted: list[tuple[str, ...]] = []

    async def _redacted_screenshot(_session: Any, _action: Any, values: tuple[str, ...], **_kw: Any) -> tuple[int, int]:
        redacted.append(tuple(values))
        return 1, 0

    monkeypatch.setattr(safe_screenshot, "redacted_screenshot", _redacted_screenshot)
    return redacted


@pytest.mark.asyncio
async def test_a_macro_screenshot_redacts_the_session_ledger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _redacting: list[tuple[str, ...]]
) -> None:
    monkeypatch.setattr(
        execution, "load_macro", lambda _n: {"actions": [{"action": "screenshot", "path": str(tmp_path / "s.png")}]}
    )
    monkeypatch.setattr(execution, "_push_status", AsyncMock())
    await execution._run_macro_impl(_session(), "m", {"token": TOKEN}, slowmo_ms=0)
    [values] = _redacting
    assert TOKEN in values and TYPED in values


@pytest.mark.asyncio
async def test_an_installed_handler_is_given_the_session_ledger_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    session = _session()
    handled: list[tuple[str, ...]] = []

    async def handler(*, action: dict[str, Any], sensitive_values: tuple[str, ...]) -> tuple[int, int]:
        handled.append(sensitive_values)
        return 1, 0

    safe_screenshot.enable_redacted_screenshots(session, handler=handler)
    monkeypatch.setattr(
        execution, "load_macro", lambda _n: {"actions": [{"action": "screenshot", "path": str(tmp_path / "s.png")}]}
    )
    monkeypatch.setattr(execution, "_push_status", AsyncMock())
    await execution._run_macro_impl(session, "m", {"token": TOKEN}, slowmo_ms=0)
    [values] = handled
    assert TOKEN in values and TYPED in values


@pytest.mark.asyncio
@pytest.mark.parametrize("label", ["before", "after"])
async def test_an_artifact_screenshot_redacts_the_session_ledger(
    tmp_path: Path, _redacting: list[tuple[str, ...]], label: str
) -> None:
    await artifacts._capture_screenshot(
        session=_session(),
        run_dir=tmp_path,
        evidence=EvidenceBuilder(),
        label=label,
        enabled=True,
        sensitive_values=(TOKEN,),
    )
    [values] = _redacting
    assert TOKEN in values and TYPED in values
