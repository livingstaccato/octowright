# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A snapshot is scrubbed of the session's privacy ledger, not only of live credential inputs.

The aria scrub reads the values of the credential inputs on the page at the
moment of the snapshot. A value a macro typed and the page then echoed as
ordinary text -- ``API token: ...`` after the input is gone -- is held only
by the session ledger, so ``browser_snapshot`` returned it and ``golden_save``
wrote it to disk, while ``capture_create`` scrubbed the same content.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.macros.privacy import install_sensitive_recorder
from octowright.session import aria_redaction
from octowright.session.core import BrowserSession

SECRET = "Fixture-Not-A-Real-Secret-Token-5k"  # pragma: allowlist secret


class _Locator:
    def __init__(self, aria: str) -> None:
        self.first = self
        self._aria = aria

    async def aria_snapshot(self, **_kwargs: Any) -> str:
        return self._aria

    async def evaluate(self, *_args: Any, **_kwargs: Any) -> list[str]:
        return []  # no credential input left on the page


def _session(tmp_path: Path, *, url: str = "https://app.example.test/") -> BrowserSession:
    page = AsyncMock()
    page.url = url
    page.title = AsyncMock(return_value="title")
    return BrowserSession(
        instance_id="test",
        kind="chromium",
        label="t",
        url=url,
        page=page,
        context=MagicMock(),
        browser=MagicMock(),
        log_path=tmp_path / "t.jsonl",
        recorder=MagicMock(),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["passwords", "all", "off"])
@pytest.mark.parametrize("echo", [SECRET, SECRET.upper(), SECRET.lower()])
async def test_the_aria_text_is_scrubbed_of_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, echo: str
) -> None:
    monkeypatch.setenv("OCTOWRIGHT_REDACT_INPUTS", mode)
    session = _session(tmp_path)
    install_sensitive_recorder(session, [SECRET])

    aria = await aria_redaction.aria_snapshot(session, _Locator(f'- paragraph: "API token: {echo}"'))

    assert echo not in aria
    assert "API token" in aria


@pytest.mark.anyio
async def test_an_empty_ledger_leaves_the_tree_alone(tmp_path: Path) -> None:
    session = _session(tmp_path)
    install_sensitive_recorder(session, [])
    tree = f'- paragraph: "API token: {SECRET}"'
    assert await aria_redaction.aria_snapshot(session, _Locator(tree)) == tree


@pytest.mark.anyio
async def test_the_snapshot_scrubs_its_url_and_title_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(tmp_path, url=f"https://app.example.test/done?token={SECRET}")
    session.page.locator = MagicMock(return_value=_Locator(f"- heading: {SECRET}"))
    session.page.title = AsyncMock(return_value=f"Welcome {SECRET.upper()}")
    install_sensitive_recorder(session, [SECRET])

    result = await session.snapshot()

    assert all(SECRET.lower() not in str(value).lower() for value in result.values()), result
    assert result["url"].startswith("https://app.example.test/done?token=")
