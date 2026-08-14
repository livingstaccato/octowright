# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Semantic-locator interaction helpers for ``BrowserSession``.

Wraps Playwright's role / label / text / test-id locator API so callers can
operate on elements by accessibility-tree intent rather than CSS selectors.
``click_by`` / ``fill_by`` / ``get_text_by`` are the public surface; tests
and macro authors prefer these because they survive cosmetic DOM churn.

Split out of ``core_ops_mixin`` to keep its file size down and to give the
locator-based actions a single home.
"""

from __future__ import annotations

import os
from typing import Any

from provide.telemetry import get_logger

from octowright.defaults import DEFAULT_ACTION_TIMEOUT_MS, REDACTED_INPUT_PLACEHOLDER
from octowright.session._protocols import SessionLike
from octowright.session.operation_gate import gated_operation

log = get_logger(__name__)


class SessionLocatorMixin(SessionLike):
    @gated_operation("session_locator_redaction")
    async def _is_password_locator(self, locator: Any) -> bool:
        """Best-effort credential check for semantic-locator actions."""
        try:
            info = await locator.first.evaluate(
                "el => el ? {"
                "  type: el.type ? String(el.type).toLowerCase() : '',"
                "  ac: el.autocomplete ? String(el.autocomplete).toLowerCase() : ''"
                "    || (el.getAttribute && el.getAttribute('autocomplete')"
                "         ? String(el.getAttribute('autocomplete')).toLowerCase() : '')"
                "} : {type: '', ac: ''}"
            )
        except Exception as exc:
            log.debug("core_locator_mixin.password_lookup_failed", error=str(exc))
            return True
        if isinstance(info, str):
            return info == "password"
        if not isinstance(info, dict):
            return True
        if info.get("type") == "password":
            return True
        return info.get("ac") in ("current-password", "new-password", "one-time-code")

    @gated_operation("session_locator_redaction")
    async def _redacted_or_original_for_locator(self, locator: Any, value: str) -> str:
        mode = os.environ.get("OCTOWRIGHT_REDACT_INPUTS", "passwords").strip().lower()
        if mode not in {"off", "all", "passwords"}:
            mode = "passwords"
        if mode == "off":
            return value
        if mode == "all":
            return REDACTED_INPUT_PLACEHOLDER
        if await self._is_password_locator(locator):
            return REDACTED_INPUT_PLACEHOLDER
        return value

    @gated_operation("session_locator_resolve")
    async def _locator(self, **finders: Any) -> Any:
        """Return a Playwright Locator for the given finder kwargs.

        Exactly one of role / label / text / test_id must be supplied. Routes
        through _target() so this also works inside iframes when one is active.
        """
        from octowright.session import locators as _locators

        return await _locators.build_locator(self, **finders)

    @gated_operation("browser_click")
    async def click_by(self, *, timeout_ms: int | None = None, **finders: Any) -> dict[str, Any]:
        """Click an element matched by role, label, text, or data-testid."""
        locator = await self._locator(**finders)
        await locator.click(timeout=timeout_ms or DEFAULT_ACTION_TIMEOUT_MS)
        self.recorder.record("click_by", **finders)
        return {"ok": True}

    @gated_operation("browser_fill")
    async def fill_by(self, value: str, *, timeout_ms: int | None = None, **finders: Any) -> dict[str, Any]:
        """Fill an input matched by role, label, or data-testid."""
        locator = await self._locator(**finders)
        recorded_value = await self._redacted_or_original_for_locator(locator, value)
        await locator.fill(value, timeout=timeout_ms or DEFAULT_ACTION_TIMEOUT_MS)
        self.recorder.record("fill_by", value=recorded_value, **finders)
        return {"ok": True}

    @gated_operation("browser_get_text_by")
    async def get_text_by(self, *, timeout_ms: int | None = None, **finders: Any) -> dict[str, Any]:
        """Return the inner text of the matched element.

        Useful for assertions that need a value rather than just a boolean match.
        """
        locator = await self._locator(**finders)
        await locator.wait_for(timeout=timeout_ms or DEFAULT_ACTION_TIMEOUT_MS)
        result = await locator.inner_text()
        self.recorder.record("get_text_by", result=result, **finders)
        return {"ok": True, "text": result}
