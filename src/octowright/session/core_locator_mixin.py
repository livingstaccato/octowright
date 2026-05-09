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

from typing import Any

from octowright.defaults import DEFAULT_ACTION_TIMEOUT_MS
from octowright.session._protocols import SessionLike


class SessionLocatorMixin(SessionLike):
    def _locator(self, **finders: Any) -> Any:
        """Return a Playwright Locator for the given finder kwargs.

        Exactly one of role / label / text / test_id must be supplied. Routes
        through _target() so this also works inside iframes when one is active.
        """
        from octowright.session import locators as _locators

        return _locators.build_locator(self._target(), **finders)

    async def click_by(self, *, timeout_ms: int | None = None, **finders: Any) -> dict[str, Any]:
        """Click an element matched by role, label, text, or data-testid."""
        locator = self._locator(**finders)
        await locator.click(timeout=timeout_ms or DEFAULT_ACTION_TIMEOUT_MS)
        self.recorder.record("click_by", **finders)
        return {"ok": True}

    async def fill_by(self, value: str, *, timeout_ms: int | None = None, **finders: Any) -> dict[str, Any]:
        """Fill an input matched by role, label, or data-testid."""
        locator = self._locator(**finders)
        await locator.fill(value, timeout=timeout_ms or DEFAULT_ACTION_TIMEOUT_MS)
        self.recorder.record("fill_by", value=value, **finders)
        return {"ok": True}

    async def get_text_by(self, *, timeout_ms: int | None = None, **finders: Any) -> dict[str, Any]:
        """Return the inner text of the matched element.

        Useful for assertions that need a value rather than just a boolean match.
        """
        locator = self._locator(**finders)
        await locator.wait_for(timeout=timeout_ms or DEFAULT_ACTION_TIMEOUT_MS)
        result = await locator.inner_text()
        self.recorder.record("get_text_by", result=result, **finders)
        return {"ok": True, "text": result}
