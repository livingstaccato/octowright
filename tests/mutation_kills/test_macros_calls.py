# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Nested ``macro_call`` dispatch: own-site trust, depth propagation, skip totals."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from octowright.macros.calls import dispatch_macro_call, report_progress
from octowright.macros.substitution import substitute

_SECRET = "zebrin4-secret"  # pragma: allowlist secret


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _session() -> Any:
    return SimpleNamespace(launch_url="https://app.example.test/", base_url=None)


@pytest.mark.anyio
async def test_a_credential_header_for_the_own_site_is_expanded_inside_a_called_macro() -> None:
    dispatched: list[dict[str, Any]] = []

    async def dispatch_one(_session: Any, action: dict[str, Any], **_kwargs: Any) -> tuple[int, int]:
        dispatched.append(action)
        return 1, 0

    child = {
        "name": "child",
        "actions": [
            {
                "action": "inject_headers",
                "pattern": "https://app.example.test/**",
                "headers": {"Authorization": "Bearer {{password}}"},
                "forward_on_redirect": {"Authorization": True},
            }
        ],
    }

    result = await dispatch_macro_call(
        _session(),
        {"action": "macro_call", "name": "child", "args": {"password": _SECRET}},
        invocation_stack=[],
        max_depth=None,
        load_macro=lambda _name: child,
        substitute=substitute,
        dispatch_one=dispatch_one,
    )

    assert result == (2, 0)
    assert [action["headers"] for action in dispatched] == [{"Authorization": f"Bearer {_SECRET}"}]


@pytest.mark.anyio
async def test_an_explicit_depth_limit_reaches_every_nested_level() -> None:
    chain = {"a": "b", "b": "c", "c": "d", "d": "e"}

    def load(name: str) -> dict[str, Any]:
        return {"name": name, "actions": [{"action": "macro_call", "name": chain[name]}]}

    async def dispatch_one(session: Any, action: dict[str, Any], **kwargs: Any) -> tuple[int, int]:
        return await dispatch_macro_call(
            session,
            action,
            load_macro=load,
            substitute=lambda actions, _args, **_kw: actions,
            dispatch_one=dispatch_one,
            **kwargs,
        )

    with pytest.raises(RuntimeError) as excinfo:
        await dispatch_one(_session(), {"action": "macro_call", "name": "a"}, invocation_stack=[], max_depth=2)

    assert str(excinfo.value) == "macro_call recursion depth exceeded (2) at a -> b -> c"


@pytest.mark.anyio
async def test_skipped_counts_from_every_subaction_are_summed() -> None:
    async def dispatch_one(_session: Any, _action: dict[str, Any], **_kwargs: Any) -> tuple[int, int]:
        return 1, 2

    result = await dispatch_macro_call(
        _session(),
        {"action": "macro_call", "name": "child"},
        invocation_stack=[],
        max_depth=None,
        load_macro=lambda _name: {"name": "child", "actions": [{"action": "click"}, {"action": "click"}]},
        substitute=lambda actions, _args, **_kw: actions,
        dispatch_one=dispatch_one,
    )

    assert result == (3, 4)


@pytest.mark.anyio
async def test_a_failing_progress_report_never_fails_the_macro() -> None:
    calls: list[tuple[float, float, str | None]] = []

    class _Ctx:
        async def report_progress(self, progress: float, *, total: float, message: str | None) -> None:
            calls.append((progress, total, message))
            raise RuntimeError("transport went away")

    await report_progress(_Ctx(), 1, 4, "step 1")

    assert calls == [(1, 4, "step 1")]
