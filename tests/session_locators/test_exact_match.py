# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Exact-match control for the text and label semantic locators.

``role_exact`` already forwards ``exact=`` to ``get_by_role``, but the text and
label branches called ``get_by_text``/``get_by_label`` bare, so they inherited
Playwright's substring default with no way to opt out. That silently matches a
superstring: renaming a colliding "Ada Lovelace" profile to "Ada Lovelace (old)"
leaves the original selector matching BOTH, and the failure looks like a bad
rename rather than a locator-semantics problem.

``text_exact`` / ``label_exact`` mirror ``role_exact``: default False (substring,
back-compat), True forwards ``exact=True``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from octowright.session.locators import build_locator
from tests._operation_gate_fakes import OperationAwareFake


class _LocatorSessionFake(OperationAwareFake):
    def __init__(self, target: Any) -> None:
        super().__init__()
        self._resolved_target = target

    def _target(self) -> Any:
        return self._resolved_target


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def target() -> MagicMock:
    return MagicMock()


@pytest.mark.anyio
async def test_text_exact_true_forwards_exact_to_get_by_text(target: MagicMock) -> None:
    await build_locator(_LocatorSessionFake(target), text="Ada Lovelace", text_exact=True)
    target.get_by_text.assert_called_once_with("Ada Lovelace", exact=True)


@pytest.mark.anyio
async def test_text_defaults_to_substring_matching(target: MagicMock) -> None:
    """Default must stay substring so every existing macro keeps working."""
    await build_locator(_LocatorSessionFake(target), text="Ada Lovelace")
    target.get_by_text.assert_called_once_with("Ada Lovelace", exact=False)


@pytest.mark.anyio
async def test_label_exact_true_forwards_exact_to_get_by_label(target: MagicMock) -> None:
    await build_locator(_LocatorSessionFake(target), label="Email", label_exact=True)
    target.get_by_label.assert_called_once_with("Email", exact=True)


@pytest.mark.anyio
async def test_label_defaults_to_substring_matching(target: MagicMock) -> None:
    await build_locator(_LocatorSessionFake(target), label="Email")
    target.get_by_label.assert_called_once_with("Email", exact=False)


@pytest.mark.anyio
async def test_exact_flags_do_not_count_as_a_provided_finder(target: MagicMock) -> None:
    """The one-finder validation counts role/label/text/test_id only.

    A bare exact flag is a modifier, not a finder, so passing it with no finder
    must still raise the same 'exactly one of' error rather than silently
    resolving to something.
    """
    with pytest.raises(ValueError, match="exactly one of"):
        await build_locator(_LocatorSessionFake(target), text_exact=True)
