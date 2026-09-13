# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Redacted screenshots for classified macro runs.

A run that holds classified values may take a screenshot only after octowright has
removed every rendered spelling of those values and proved none remain. The
default stays refusal; the embedding app or the operator opts in.
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
from octowright.macros.privacy import PrivacyLedger

PASSWORD = "B7-REDACT-PASSWORD-CANARY-4c2e"  # pragma: allowlist secret
EMAIL = "b7-redact-canary@example.test"
POLICY_ENV = "OCTOWRIGHT_MACRO_CLASSIFIED_SCREENSHOTS"


class FakePage:
    """Records the redaction protocol: redact, then restore, in that order."""

    def __init__(self, *, remaining: int = 0, restore_error: Exception | None = None) -> None:
        self.calls: list[str] = []
        self.remaining = remaining
        self.restore_error = restore_error
        self.redacted_values: list[str] | None = None

    async def evaluate(self, expression: str, arg: Any = None) -> Any:
        if expression == safe_screenshot.REDACT_RENDERED_JS:
            self.calls.append("redact")
            self.redacted_values = list(arg)
            return self.remaining
        if expression == safe_screenshot.RESTORE_RENDERED_JS:
            self.calls.append("restore")
            if self.restore_error is not None:
                raise self.restore_error
            return None
        raise AssertionError(f"unexpected evaluate: {expression[:40]}")


def _session(page: FakePage) -> MagicMock:
    session = MagicMock()
    session.page = page
    written: list[Path] = []

    async def screenshot(path: Path) -> Path:
        page.calls.append("screenshot")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
        written.append(path)
        return path

    session.screenshot = AsyncMock(side_effect=screenshot)
    session.written = written

    @contextlib.asynccontextmanager
    async def operation(_name: str) -> AsyncIterator[None]:
        yield

    session.operation = operation
    # A MagicMock answers every attribute; the guard must see only what was installed.
    session._octowright_sensitive_screenshot_authority = None
    session._octowright_sensitive_screenshot_handler = None
    return session


@pytest.fixture(autouse=True)
def _recordings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(defaults, "RECORDINGS_DIR", tmp_path)
    monkeypatch.delenv(POLICY_ENV, raising=False)
    return tmp_path


def _classified_run(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    monkeypatch.setattr(execution, "load_macro", lambda _name: {"actions": [{"action": "screenshot", "path": path}]})
    monkeypatch.setattr(execution, "_push_status", AsyncMock())


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, "refuse"), ("refuse", "refuse"), ("redact", "redact"), (" REDACT ", "redact")],
)
def test_policy_defaults_to_refusal(monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: str) -> None:
    if raw is not None:
        monkeypatch.setenv(POLICY_ENV, raw)
    assert safe_screenshot.classified_screenshot_policy() == expected


def test_an_unknown_policy_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(POLICY_ENV, "sometimes")
    with pytest.raises(ValueError, match=POLICY_ENV):
        safe_screenshot.classified_screenshot_policy()


@pytest.mark.asyncio
async def test_without_opt_in_the_classified_screenshot_is_still_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    page = FakePage()
    session = _session(page)
    _classified_run(monkeypatch, str(tmp_path / "shot.png"))

    with pytest.raises(RuntimeError, match="privacy handler"):
        await execution._run_macro_impl(session, "private", {"password": PASSWORD}, slowmo_ms=0)

    assert page.calls == []


@pytest.mark.asyncio
async def test_opt_in_redacts_then_screenshots_then_restores(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    page = FakePage()
    session = _session(page)
    safe_screenshot.enable_redacted_screenshots(session)
    _classified_run(monkeypatch, str(tmp_path / "shot.png"))

    result = await execution._run_macro_impl(session, "private", {"password": PASSWORD, "email": EMAIL}, slowmo_ms=0)

    assert result["executed"] == 1
    assert page.calls == ["redact", "screenshot", "restore"]
    assert page.redacted_values is not None
    assert PASSWORD in page.redacted_values and EMAIL in page.redacted_values
    assert session.written == [(tmp_path / "shot.png").resolve()]


@pytest.mark.asyncio
async def test_the_operator_policy_redacts_without_an_installed_handler(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(POLICY_ENV, "redact")
    page = FakePage()
    session = _session(page)
    _classified_run(monkeypatch, str(tmp_path / "shot.png"))

    result = await execution._run_macro_impl(session, "private", {"password": PASSWORD}, slowmo_ms=0)

    assert result["executed"] == 1
    assert page.calls == ["redact", "screenshot", "restore"]


@pytest.mark.asyncio
async def test_an_installed_handler_takes_precedence_over_the_policy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(POLICY_ENV, "redact")
    page = FakePage()
    session = _session(page)
    handled: list[tuple[str, ...]] = []

    async def handler(*, action: dict[str, Any], sensitive_values: tuple[str, ...]) -> tuple[int, int]:
        del action
        handled.append(sensitive_values)
        return 1, 0

    safe_screenshot.enable_redacted_screenshots(session, handler=handler)
    _classified_run(monkeypatch, str(tmp_path / "shot.png"))

    await execution._run_macro_impl(session, "private", {"password": PASSWORD}, slowmo_ms=0)

    assert handled == [(PASSWORD,)]
    assert page.calls == []


@pytest.mark.asyncio
async def test_values_still_rendered_after_redaction_refuse_the_screenshot(tmp_path: Path) -> None:
    page = FakePage(remaining=1)
    session = _session(page)

    with pytest.raises(RuntimeError, match="still rendered"):
        await safe_screenshot.redacted_screenshot(
            session, {"action": "screenshot", "path": str(tmp_path / "shot.png")}, (PASSWORD,)
        )

    assert page.calls == ["redact", "restore"]
    assert not (tmp_path / "shot.png").exists()


@pytest.mark.asyncio
async def test_a_failed_screenshot_still_restores_and_leaves_no_file(tmp_path: Path) -> None:
    page = FakePage()
    session = _session(page)

    async def broken(path: Path) -> Path:
        page.calls.append("screenshot")
        path.write_bytes(b"partial")
        raise OSError("disk full")

    session.screenshot = AsyncMock(side_effect=broken)

    with pytest.raises(OSError, match="disk full"):
        await safe_screenshot.redacted_screenshot(
            session, {"action": "screenshot", "path": str(tmp_path / "shot.png")}, (PASSWORD,)
        )

    assert page.calls == ["redact", "screenshot", "restore"]
    assert not (tmp_path / "shot.png").exists()


@pytest.mark.asyncio
async def test_a_failed_restore_deletes_the_screenshot(tmp_path: Path) -> None:
    page = FakePage(restore_error=RuntimeError("page navigated"))
    session = _session(page)

    with pytest.raises(RuntimeError, match="page navigated"):
        await safe_screenshot.redacted_screenshot(
            session, {"action": "screenshot", "path": str(tmp_path / "shot.png")}, (PASSWORD,)
        )

    assert page.calls == ["redact", "screenshot", "restore"]
    assert not (tmp_path / "shot.png").exists()


@pytest.mark.asyncio
async def test_a_path_outside_the_recordings_root_is_refused_before_the_page(tmp_path: Path) -> None:
    page = FakePage()
    session = _session(page)
    outside = tmp_path.parent / "elsewhere" / "shot.png"

    with pytest.raises(ValueError):
        await safe_screenshot.redacted_screenshot(session, {"action": "screenshot", "path": str(outside)}, (PASSWORD,))

    assert page.calls == []


@pytest.mark.asyncio
async def test_a_screenshot_without_a_path_is_skipped() -> None:
    page = FakePage()
    session = _session(page)

    assert await safe_screenshot.redacted_screenshot(session, {"action": "screenshot"}, (PASSWORD,)) == (0, 1)
    assert page.calls == []


@pytest.mark.asyncio
async def test_nested_classified_values_reach_the_built_in_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(POLICY_ENV, "redact")
    page = FakePage()
    session = _session(page)

    await execution._dispatch_one(
        session,
        {"action": "screenshot", "path": str(defaults.RECORDINGS_DIR / "nested.png")},
        invocation_stack=["outer"],
        run_ledger=PrivacyLedger((PASSWORD, EMAIL)),
    )

    assert page.redacted_values is not None
    assert PASSWORD in page.redacted_values and EMAIL in page.redacted_values


@pytest.mark.asyncio
async def test_an_automatic_screenshot_is_redacted_under_opt_in(tmp_path: Path) -> None:
    page = FakePage()
    session = _session(page)
    safe_screenshot.enable_redacted_screenshots(session)
    evidence = EvidenceBuilder()

    await artifacts._capture_screenshot(
        session=session,
        run_dir=tmp_path,
        evidence=evidence,
        label="after",
        enabled=True,
        sensitive_values=(PASSWORD,),
    )

    assert page.calls == ["redact", "screenshot", "restore"]
    assert [record["type"] for record in evidence.records] == ["screenshot"]


@pytest.mark.asyncio
async def test_an_automatic_screenshot_is_suppressed_and_says_so_without_opt_in(tmp_path: Path) -> None:
    page = FakePage()
    session = _session(page)
    evidence = EvidenceBuilder()

    await artifacts._capture_screenshot(
        session=session,
        run_dir=tmp_path,
        evidence=evidence,
        label="after",
        enabled=True,
        sensitive_values=(PASSWORD,),
    )

    assert page.calls == []
    assert [record["type"] for record in evidence.records] == ["screenshot_suppressed"]


@pytest.mark.asyncio
async def test_an_automatic_screenshot_never_bypasses_a_custom_handler(tmp_path: Path) -> None:
    page = FakePage()
    session = _session(page)

    async def handler(*, action: dict[str, Any], sensitive_values: tuple[str, ...]) -> tuple[int, int]:
        del action, sensitive_values
        return 1, 0

    safe_screenshot.enable_redacted_screenshots(session, handler=handler)
    evidence = EvidenceBuilder()

    await artifacts._capture_screenshot(
        session=session,
        run_dir=tmp_path,
        evidence=evidence,
        label="after",
        enabled=True,
        sensitive_values=(PASSWORD,),
    )

    assert page.calls == []
    assert [record["type"] for record in evidence.records] == ["screenshot_suppressed"]
