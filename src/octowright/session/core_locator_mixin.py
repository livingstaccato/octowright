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

import time
from typing import Any

from provide.telemetry import get_logger

from octowright.defaults import DEFAULT_ACTION_TIMEOUT_MS
from octowright.session._protocols import SessionLike
from octowright.session.fill_origin import FillOriginCheck, pending_fill_origin_check
from octowright.session.input_redaction import CREDENTIAL_FIELD_JS, classify_credential_field, recorded_input_value
from octowright.session.operation.gate import gated_operation

log = get_logger(__name__)


class SessionLocatorMixin(SessionLike):
    @gated_operation("session_locator_redaction")
    async def _is_password_locator(self, locator: Any) -> bool | None:
        """Best-effort credential check for semantic-locator actions.

        Same contract as ``core_page_mixin._is_password_input``: ``None`` when
        the field cannot be classified.
        """
        try:
            info = await locator.first.evaluate(CREDENTIAL_FIELD_JS)
        except Exception as exc:
            log.debug("core_locator_mixin.password_lookup_failed", error=str(exc))
            return None
        return classify_credential_field(info)

    @gated_operation("session_locator_redaction")
    async def _redacted_or_original_for_locator(self, locator: Any, value: str) -> str:
        return await recorded_input_value(self, value, lambda: self._is_password_locator(locator))

    @gated_operation("macro_credential_fill_origin")
    async def _checked_element(self, locator: Any, check: FillOriginCheck, timeout_ms: int) -> tuple[Any, int]:
        """*locator*'s element once *check* has passed its owning frame's URL, and the budget left.

        See ``session.fill_origin``. The caller types into the returned handle,
        never the selector again, and disposes of it. The handle is resolved
        with the same strictness a selector fill has, and waiting for it
        spends the step's budget rather than adding to it.
        """
        started = time.monotonic()
        handle = await locator.element_handle(timeout=timeout_ms)
        try:
            frame = await handle.owner_frame()
            check(str(getattr(frame, "url", "") or ""))
        except BaseException:
            await self._dispose_handle(handle)
            raise
        return handle, max(1, timeout_ms - int((time.monotonic() - started) * 1000))

    @gated_operation("macro_credential_fill_origin")
    async def _dispose_handle(self, handle: Any) -> None:
        try:
            await handle.dispose()
        except Exception as exc:  # the document it lived in may be gone, which is the point
            log.debug("core_locator_mixin.handle_dispose_failed", error=str(exc))

    @gated_operation("session_locator_resolve")
    async def _locator(self, **finders: Any) -> Any:
        """Return a Playwright Locator for the given finder kwargs.

        Exactly one of role / label / text / test_id must be supplied. Routes
        through _target() so this also works inside iframes when one is active.
        """
        from octowright.session import locators as _locators

        return await _locators.build_locator(self, **finders)

    @gated_operation("browser_click")
    async def click_by(
        self,
        *,
        timeout_ms: int | None = None,
        no_wait_after: bool = False,
        **finders: Any,
    ) -> dict[str, Any]:
        """Click an element matched by role, label, text, or data-testid."""
        locator = await self._locator(**finders)
        click_kwargs: dict[str, Any] = {"timeout": timeout_ms or DEFAULT_ACTION_TIMEOUT_MS}
        if no_wait_after:
            click_kwargs["no_wait_after"] = True
        await locator.click(**click_kwargs)
        recorded_kwargs = dict(finders)
        if no_wait_after:
            recorded_kwargs["no_wait_after"] = True
        self.recorder.record("click_by", **recorded_kwargs)
        return {"ok": True}

    @gated_operation("browser_fill")
    async def fill_by(self, value: str, *, timeout_ms: int | None = None, **finders: Any) -> dict[str, Any]:
        """Fill an input matched by role, label, or data-testid."""
        locator = await self._locator(**finders)
        recorded_value = await self._redacted_or_original_for_locator(locator, value)
        budget = timeout_ms or DEFAULT_ACTION_TIMEOUT_MS
        check = pending_fill_origin_check()
        if check is None:
            await locator.fill(value, timeout=budget)
        else:
            handle, left = await self._checked_element(locator, check, budget)
            try:
                await handle.fill(value, timeout=left)
            finally:
                await self._dispose_handle(handle)
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
