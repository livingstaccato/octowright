# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Redacted screenshots for classified macro runs.

A run that holds classified values may take a screenshot only after octowright has
redacted the page and proved, before and after the capture, that the page did not change
and renders no classified value. The default stays refusal; the embedding app or the
operator opts in.
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
from octowright.macros import redaction_page_js as page_js
from octowright.macros.privacy import PrivacyLedger

PASSWORD = "B7-REDACT-PASSWORD-CANARY-4c2e"  # pragma: allowlist secret
EMAIL = "b7-redact-canary@example.test"
POLICY_ENV = "OCTOWRIGHT_MACRO_CLASSIFIED_SCREENSHOTS"

CLEAN: dict[str, Any] = {"changed": 0, "remaining": 0}
CLEAN_SNAPSHOT: dict[str, Any] = {"strings": [], "documents": []}
LEAKING_SNAPSHOT: dict[str, Any] = {"strings": [PASSWORD], "documents": [{"nodes": {}, "layout": {"text": [0]}}]}
TAKEN = ["redact", "verify", "snapshot", "screenshot", "verify", "snapshot", "detach", "restore"]


class FakeController:
    def __init__(self, page: FakePage) -> None:
        self.page = page

    async def evaluate(self, expression: str) -> Any:
        page = self.page
        if expression == page_js.REDACT_CALL:
            page.calls.append("redact")
            if page.redact_error is not None:
                raise page.redact_error
            return None
        if expression == page_js.VERIFY_CALL:
            page.calls.append("verify")
            return page.reports.pop(0) if page.reports else dict(CLEAN)
        if expression == page_js.RESTORE_CALL:
            page.calls.append("restore")
            if page.restore_error is not None:
                raise page.restore_error
            return None
        raise AssertionError(f"unexpected controller call: {expression}")

    async def dispose(self) -> None:
        return None


class FakeCDP:
    def __init__(self, page: FakePage) -> None:
        self.page = page

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        assert method == "DOMSnapshot.captureSnapshot"
        assert params == {"computedStyles": ["visibility"]}
        self.page.calls.append("snapshot")
        return self.page.snapshots.pop(0) if self.page.snapshots else CLEAN_SNAPSHOT

    async def detach(self) -> None:
        self.page.calls.append("detach")


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self.page = page

    async def new_cdp_session(self, _page: object) -> FakeCDP:
        if not self.page.chromium:
            raise RuntimeError("CDP session is only available in Chromium")
        return FakeCDP(self.page)


class FakePage:
    """Records the redaction protocol in order: redact, verify, snapshot, screenshot, ..., restore."""

    def __init__(
        self,
        *,
        reports: tuple[dict[str, Any], ...] = (),
        snapshots: tuple[dict[str, Any], ...] = (),
        redact_error: BaseException | None = None,
        restore_error: Exception | None = None,
        chromium: bool = True,
    ) -> None:
        self.calls: list[str] = []
        self.reports = list(reports)
        self.snapshots = list(snapshots)
        self.redact_error = redact_error
        self.restore_error = restore_error
        self.chromium = chromium
        self.redacted_values: list[str] | None = None
        self.context = FakeContext(self)

    async def evaluate_handle(self, expression: str, arg: Any) -> FakeController:
        assert expression == page_js.CONTROLLER_JS
        self.redacted_values = list(arg)
        return FakeController(self)


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


async def _shoot(page: FakePage, path: Path) -> tuple[int, int]:
    return await safe_screenshot.redacted_screenshot(
        _session(page), {"action": "screenshot", "path": str(path)}, (PASSWORD,)
    )


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
    assert page.calls == TAKEN
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
    assert page.calls == TAKEN


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
async def test_a_page_that_changed_before_the_capture_is_refused(tmp_path: Path) -> None:
    page = FakePage(reports=({"changed": 1, "remaining": 0},))

    with pytest.raises(RuntimeError, match="page changed before"):
        await _shoot(page, tmp_path / "shot.png")

    assert page.calls == ["redact", "verify", "restore", "detach"]
    assert not (tmp_path / "shot.png").exists()


@pytest.mark.asyncio
async def test_a_page_that_changed_during_the_capture_loses_its_screenshot(tmp_path: Path) -> None:
    page = FakePage(reports=(CLEAN, {"changed": 2, "remaining": 0}))

    with pytest.raises(RuntimeError, match="page changed after"):
        await _shoot(page, tmp_path / "shot.png")

    assert page.calls == ["redact", "verify", "snapshot", "screenshot", "verify", "restore", "detach"]
    assert not (tmp_path / "shot.png").exists()


@pytest.mark.asyncio
async def test_values_still_in_the_page_refuse_the_screenshot(tmp_path: Path) -> None:
    page = FakePage(reports=({"changed": 0, "remaining": 1},))

    with pytest.raises(RuntimeError, match="still in the page before"):
        await _shoot(page, tmp_path / "shot.png")

    assert page.calls == ["redact", "verify", "restore", "detach"]
    assert not (tmp_path / "shot.png").exists()


@pytest.mark.asyncio
async def test_a_value_the_page_still_renders_refuses_without_naming_it(tmp_path: Path) -> None:
    page = FakePage(snapshots=(LEAKING_SNAPSHOT,))

    with pytest.raises(RuntimeError, match=r"still rendered before the screenshot \(rendered text\)") as raised:
        await _shoot(page, tmp_path / "shot.png")

    assert PASSWORD not in str(raised.value)
    assert page.calls == ["redact", "verify", "snapshot", "restore", "detach"]
    assert not (tmp_path / "shot.png").exists()


@pytest.mark.asyncio
async def test_a_value_rendered_during_the_capture_loses_its_screenshot(tmp_path: Path) -> None:
    page = FakePage(snapshots=(CLEAN_SNAPSHOT, LEAKING_SNAPSHOT))

    with pytest.raises(RuntimeError, match="still rendered after the screenshot"):
        await _shoot(page, tmp_path / "shot.png")

    assert page.calls == [*TAKEN[:-2], "restore", "detach"]
    assert not (tmp_path / "shot.png").exists()


@pytest.mark.asyncio
async def test_a_page_without_a_rendered_surface_snapshot_is_refused_untouched(tmp_path: Path) -> None:
    page = FakePage(chromium=False)

    with pytest.raises(RuntimeError, match="Chromium"):
        await _shoot(page, tmp_path / "shot.png")

    assert page.calls == []
    assert page.redacted_values is None
    assert not (tmp_path / "shot.png").exists()


@pytest.mark.asyncio
async def test_a_redaction_that_fails_midway_is_still_restored(tmp_path: Path) -> None:
    page = FakePage(redact_error=RuntimeError("page script threw"))

    with pytest.raises(RuntimeError, match="page script threw"):
        await _shoot(page, tmp_path / "shot.png")

    assert page.calls == ["redact", "restore", "detach"]
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

    assert page.calls == ["redact", "verify", "snapshot", "screenshot", "restore", "detach"]
    assert not (tmp_path / "shot.png").exists()


@pytest.mark.asyncio
async def test_a_failed_restore_deletes_the_screenshot(tmp_path: Path) -> None:
    page = FakePage(restore_error=RuntimeError("page navigated"))

    with pytest.raises(RuntimeError, match="page navigated"):
        await _shoot(page, tmp_path / "shot.png")

    assert page.calls == TAKEN
    assert not (tmp_path / "shot.png").exists()


@pytest.mark.asyncio
async def test_a_failed_restore_after_a_refusal_reports_the_refusal(tmp_path: Path) -> None:
    page = FakePage(reports=({"changed": 1, "remaining": 0},), restore_error=RuntimeError("page navigated"))

    with pytest.raises(RuntimeError, match="page changed before"):
        await _shoot(page, tmp_path / "shot.png")

    assert page.calls == ["redact", "verify", "restore", "detach"]


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

    assert page.calls == TAKEN
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


@pytest.mark.asyncio
async def test_a_mistyped_policy_suppresses_an_automatic_screenshot_instead_of_failing_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(POLICY_ENV, "sometimes")
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
