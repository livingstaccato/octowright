# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``browser_screenshot`` honours the session privacy ledger.

It called ``BrowserSession.screenshot``, which wrote raw pixels and never
consulted the ledger -- so a password filled through a browser tool and
rendered back by the page reached a PNG, while a macro run refused the same
screenshot. The session method is the one boundary every caller
(``browser_screenshot``, ``browser_each``, capture-and-close, a macro's plain
screenshot) goes through, so it routes through ``safe_screenshot`` there.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.macros import safe_screenshot
from octowright.macros.privacy import admit_redacted_input
from octowright.session.core_page_mixin import SessionPageMixin

SECRET = "rendered-pw-77"  # pragma: allowlist secret -- a fixture


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Session(SessionPageMixin):
    def __init__(self) -> None:
        self.recorder: Any = MagicMock()
        self.durable_text_scrubber = None
        self.page: Any = MagicMock()
        self.page.screenshot = AsyncMock()

    def operation(self, name: str) -> Any:
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _lease() -> Any:
            yield

        return _lease()


@pytest.mark.anyio
async def test_without_a_ledger_the_raw_screenshot_is_taken(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    redacted = AsyncMock()
    monkeypatch.setattr(safe_screenshot, "redacted_screenshot", redacted)
    session = _Session()

    await session.screenshot.__wrapped__(session, tmp_path / "a.png")

    session.page.screenshot.assert_awaited_once()
    redacted.assert_not_awaited()


@pytest.mark.anyio
async def test_with_a_ledger_value_the_redacted_path_is_taken(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    redacted = AsyncMock(return_value=(1, 0))
    monkeypatch.setattr(safe_screenshot, "redacted_screenshot", redacted)
    session = _Session()
    admit_redacted_input(session, SECRET)
    target = tmp_path / "a.png"

    await session.screenshot.__wrapped__(session, target)

    session.page.screenshot.assert_not_awaited()
    redacted.assert_awaited_once()
    args, kwargs = redacted.await_args
    assert args[1] == {"action": "screenshot", "path": str(target)}
    assert args[2] == (SECRET,)
    assert kwargs["root"] == tmp_path


@pytest.mark.anyio
async def test_an_installed_handler_wins_and_its_refusal_raises(tmp_path: Path) -> None:
    session = _Session()
    admit_redacted_input(session, SECRET)
    handler = AsyncMock(return_value=None)
    safe_screenshot.enable_redacted_screenshots(session, handler=handler)

    with pytest.raises(RuntimeError, match="refused"):
        await session.screenshot.__wrapped__(session, tmp_path / "a.png")
    session.page.screenshot.assert_not_awaited()
    handler.assert_awaited_once()


# --- OCTOWRIGHT_LEDGER_SCREENSHOTS: refuse (default) | allow ---------------------------


@pytest.mark.anyio
async def test_allow_takes_the_raw_screenshot_despite_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The operator's escape hatch that keeps recording redaction on (``OCTOWRIGHT_REDACT_INPUTS=off`` does not)."""
    monkeypatch.setenv("OCTOWRIGHT_LEDGER_SCREENSHOTS", "allow")
    redacted = AsyncMock()
    monkeypatch.setattr(safe_screenshot, "redacted_screenshot", redacted)
    session = _Session()
    admit_redacted_input(session, SECRET)

    await session.screenshot.__wrapped__(session, tmp_path / "a.png")

    session.page.screenshot.assert_awaited_once()
    redacted.assert_not_awaited()


@pytest.mark.parametrize("raw", [None, "refuse", "REFUSE", " refuse ", "permit", "yes", ""])
def test_anything_but_allow_refuses(raw: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo must not decide that a password may reach a PNG."""
    if raw is None:
        monkeypatch.delenv("OCTOWRIGHT_LEDGER_SCREENSHOTS", raising=False)
    else:
        monkeypatch.setenv("OCTOWRIGHT_LEDGER_SCREENSHOTS", raw)
    assert safe_screenshot.ledger_screenshot_policy() == "refuse"


@pytest.mark.parametrize("raw", ["allow", "ALLOW", " allow "])
def test_allow_is_read_case_and_space_insensitively(raw: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_LEDGER_SCREENSHOTS", raw)
    assert safe_screenshot.ledger_screenshot_policy() == "allow"


@pytest.mark.anyio
async def test_allow_does_not_override_an_installed_handler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An embedding application's own privacy handler is its decision, not the operator's env."""
    monkeypatch.setenv("OCTOWRIGHT_LEDGER_SCREENSHOTS", "allow")
    session = _Session()
    admit_redacted_input(session, SECRET)
    handler = AsyncMock(return_value=None)
    safe_screenshot.enable_redacted_screenshots(session, handler=handler)

    with pytest.raises(RuntimeError, match="refused"):
        await session.screenshot.__wrapped__(session, tmp_path / "a.png")
    session.page.screenshot.assert_not_awaited()
