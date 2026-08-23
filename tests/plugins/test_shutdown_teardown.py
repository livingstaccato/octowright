# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import Any

from octowright.cli.serve import _close_plugin_pools_on_shutdown


class _Recorder:
    def __init__(self) -> None:
        self.closed: list[bool] = []


class _Pool:
    def __init__(self, rec: _Recorder, *, boom: bool = False) -> None:
        self._rec = rec
        self._boom = boom

    async def close_all(self, *, force: bool = False) -> None:
        if self._boom:
            raise RuntimeError("teardown exploded")
        self._rec.closed.append(force)


class _Registry:
    def __init__(self, pools: dict[str, Any]) -> None:
        self._pools = pools

    def pools(self) -> dict[str, Any]:
        return self._pools


class _Log:
    def __init__(self) -> None:
        self.debug_calls: list[tuple[str, dict[str, Any]]] = []

    def debug(self, event: str, **fields: Any) -> None:
        self.debug_calls.append((event, fields))


async def test_every_registered_pool_is_force_closed():
    rec_a, rec_b = _Recorder(), _Recorder()
    registry = _Registry({"a": _Pool(rec_a), "b": _Pool(rec_b)})
    await _close_plugin_pools_on_shutdown(registry, log=_Log())
    assert rec_a.closed == [True]
    assert rec_b.closed == [True]


async def test_one_failing_pool_does_not_stop_the_others():
    rec = _Recorder()
    log = _Log()
    registry = _Registry({"bad": _Pool(_Recorder(), boom=True), "good": _Pool(rec)})
    await _close_plugin_pools_on_shutdown(registry, log=log)
    assert rec.closed == [True], "a failing pool must not abort teardown of the rest"
    assert any("plugin_pool_close_failed" in event for event, _ in log.debug_calls)


async def test_no_registry_is_a_no_op():
    await _close_plugin_pools_on_shutdown(None, log=_Log())
