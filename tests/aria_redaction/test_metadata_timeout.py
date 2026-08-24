# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The aria read that annotates an action must honour that action's timeout.

``click``/``fill``/``type_text`` resolve role and name for the recording
before they act. That read was unbounded, so on a selector that never resolves
it spent the full default -- the credential scan alone uses
``DEFAULT_ACTION_TIMEOUT_MS`` -- BEFORE the bounded action even started.
Measured downstream: a click carrying ``timeout_ms=4000`` against a missing
selector took 19.1s, roughly 15 of them in the scan.

Bounding it is what makes ``timeout_ms`` mean what it says.
"""

from __future__ import annotations

from typing import Any

import pytest

from octowright.session import aria_redaction


class _Locator:
    """Records the timeouts it is handed by both Playwright calls."""

    def __init__(self) -> None:
        self.snapshot_kwargs: list[dict[str, Any]] = []
        self.evaluate_kwargs: list[dict[str, Any]] = []
        self.first = self

    async def aria_snapshot(self, **kwargs: Any) -> str:
        self.snapshot_kwargs.append(kwargs)
        return "- button 'Submit'"

    async def evaluate(self, _expression: Any, _mode: Any = None, **kwargs: Any) -> list[str]:
        self.evaluate_kwargs.append(kwargs)
        return []


class _Session:
    def operation(self, _name: str) -> Any:
        class _Lease:
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, *_exc: Any) -> bool:
                return False

        return _Lease()


@pytest.mark.anyio
async def test_a_budget_bounds_both_playwright_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(aria_redaction, "resolve_redaction_mode", lambda: "strict")
    locator = _Locator()

    await aria_redaction.aria_snapshot(_Session(), locator, timeout_ms=4000)

    assert locator.evaluate_kwargs[0]["timeout"] == 4000, "credential scan unbounded"
    assert locator.snapshot_kwargs[0]["timeout"] == 4000, "snapshot unbounded"


@pytest.mark.anyio
async def test_no_budget_leaves_the_snapshot_call_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Playwright reads an explicit ``timeout=None`` as "wait forever", so an
    unset budget must omit the argument rather than forward it."""
    monkeypatch.setattr(aria_redaction, "resolve_redaction_mode", lambda: "strict")
    locator = _Locator()

    await aria_redaction.aria_snapshot(_Session(), locator)

    assert locator.snapshot_kwargs[0] == {}


@pytest.mark.anyio
async def test_redaction_off_still_honours_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 'off' path skips the credential scan but not the snapshot."""
    monkeypatch.setattr(aria_redaction, "resolve_redaction_mode", lambda: "off")
    locator = _Locator()

    await aria_redaction.aria_snapshot(_Session(), locator, timeout_ms=250)

    assert locator.evaluate_kwargs == []
    assert locator.snapshot_kwargs[0]["timeout"] == 250


@pytest.mark.anyio
async def test_click_hands_its_own_budget_to_the_metadata_read(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The wiring, not just the helper: deleting the argument at the call site
    must fail a test."""
    from octowright.session import core_page_mixin
    from tests.test_locators import FakePage, _make_session

    seen: dict[str, Any] = {}

    async def _spy(_session: Any, _loc: Any, **kwargs: Any) -> str:
        seen.update(kwargs)
        return "- button 'Submit'"

    monkeypatch.setattr(core_page_mixin, "redacted_aria_snapshot", _spy)

    class _Page(FakePage):  # type: ignore[misc]
        def locator(self, _selector: str) -> Any:
            return _Locator()

        async def click(self, _selector: str, **_kwargs: Any) -> None:
            return None

    session = _make_session(_Page(), tmp_path)
    await session.click("#nope", timeout_ms=4000)

    assert seen == {"timeout_ms": 4000}
