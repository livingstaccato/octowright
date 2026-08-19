# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Test doubles for the credential-classification call on an aria snapshot.

``octowright.session.aria_redaction.aria_snapshot`` reads the values of a
document's credential fields before it snapshots, so a locator double has to
model that call as well as ``aria_snapshot()``. Failing to model it makes the
scrubber fail closed -- which is the correct production behaviour, and the
reason this helper exists rather than a looser production check.

Some locator doubles already stub ``first.evaluate`` for the record-time
``_is_password_input`` probe, which uses the same Playwright entry point.
The dispatcher below keys on the production JS constant by identity, so the
two probes stay distinguishable without matching on source text.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from octowright.session.aria_redaction import _CREDENTIAL_VALUES_JS


def credential_aware_evaluate(credentials: list[str] | None = None, other: Any = None) -> AsyncMock:
    """An ``evaluate`` double that answers the credential scan and one other probe."""
    values = list(credentials or [])

    async def _dispatch(expression: Any, *_args: Any, **_kwargs: Any) -> Any:
        if expression is _CREDENTIAL_VALUES_JS:
            return values
        return other

    return AsyncMock(side_effect=_dispatch)


def stub_credential_scan(locator: Any, values: list[str] | None = None) -> Any:
    """Teach *locator* to answer the credential-value scan with *values*."""
    locator.first = SimpleNamespace(evaluate=credential_aware_evaluate(values))
    return locator


class FakeAriaLocator:
    """Locator double that returns a fixed tree and a fixed credential list."""

    def __init__(self, aria: str, credentials: list[str] | None = None) -> None:
        self._aria = aria
        self.first = SimpleNamespace(evaluate=credential_aware_evaluate(credentials))

    async def aria_snapshot(self) -> str:
        return self._aria
