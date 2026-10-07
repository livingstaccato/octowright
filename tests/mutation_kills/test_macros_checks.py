# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A JS check that never answers names itself in the timeout an agent sees."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest

from octowright.macros.checks import _check_js
from octowright.session.timeouts import SessionCallTimeoutError


class _Target:
    async def evaluate(self, expression: str) -> Any:
        await asyncio.sleep(30)


class _Session:
    @asynccontextmanager
    async def operation(self, name: str):
        yield

    def _target(self) -> _Target:
        return _Target()


async def test_a_hung_js_check_times_out_as_macro_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_UNBOUNDED_CALL_TIMEOUT_SECONDS", "0.05")

    with pytest.raises(SessionCallTimeoutError) as excinfo:
        await _check_js(_Session(), "1 + 1")  # type: ignore[arg-type]

    assert str(excinfo.value).startswith("macro_check did not answer within 0.05s")
