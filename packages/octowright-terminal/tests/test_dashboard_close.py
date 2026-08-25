# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The dashboard close button, driven through core's real route handler.

This file exists because the extraction deleted the only test that pinned the
*status code* — ``tests/terminal/test_dashboard_sessions.py::
test_session_close_refuses_protected_terminal_without_force`` — and the
behaviour regressed the moment it was gone: ``TerminalPool.close`` raised a
``ProtectedTerminalCloseError`` outside core's ``ProtectedSessionCloseError``
hierarchy, ``_maybe_close_plugin``'s ``except`` did not match, and the route
turned a refusal into a generic ``500`` with no "pass force=true" guidance.

The parity gate could not catch it: it derives its assertions from
``SessionPool``'s method *names and signatures*, and a raised exception type
is in neither. So this is deliberately an end-to-end route test with a real
PTY, not a unit test of the pool — the pool's own contract assertion lives in
``test_contract_parity.py``, and both are needed because the seam that broke
runs between them.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import pytest
from starlette.requests import Request

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="PTY is POSIX-only")


def _delete_request(instance_id: str, *, force: bool = False) -> Request:
    return Request(
        {
            "type": "http",
            "method": "DELETE",
            "path": f"/api/sessions/{instance_id}",
            "headers": [],
            "query_string": b"force=true" if force else b"",
            "path_params": {"id": instance_id},
        }
    )


@pytest.fixture
def pool() -> Any:
    # The `_activated_terminal_plugin` autouse fixture (conftest.py) registers
    # a real TerminalPool in the process-global plugin registry, which is the
    # same registry `_maybe_close_plugin` resolves through.
    from octowright.server import plugin_state

    return plugin_state.registry().pools()["terminal"]


async def test_session_close_closes_a_live_terminal(pool: Any) -> None:
    """DELETE on a plugin session must not 404 because the id is not a browser."""
    from octowright.http.routes.sessions import session_close

    launched = await pool.launch(kind="pty", connector_config={"command": "/bin/cat"}, label="t")
    instance_id = launched["instance_id"]
    resp = await session_close(_delete_request(instance_id))
    assert resp.status_code == 200
    assert json.loads(bytes(resp.body))["closed"] is True
    assert pool.maybe_get(instance_id) is None


async def test_session_close_refuses_a_protected_terminal_with_409_not_500(pool: Any) -> None:
    """The regression guard. 409 + actionable message, and the session survives.

    Asserting the message matters as much as the code: the whole reason core
    catches its own type here is to rewrite ``force=True`` into the
    ``force=true`` query parameter an operator can actually pass.
    """
    from octowright.http.routes.sessions import session_close

    launched = await pool.launch(kind="pty", connector_config={"command": "/bin/cat"}, label="t", protected=True)
    instance_id = launched["instance_id"]
    try:
        resp = await session_close(_delete_request(instance_id))
        assert resp.status_code == 409
        assert "force=true" in json.loads(bytes(resp.body))["error"]
        assert pool.maybe_get(instance_id) is not None  # refused, not closed

        forced = await session_close(_delete_request(instance_id, force=True))
        assert forced.status_code == 200
        assert pool.maybe_get(instance_id) is None
    finally:
        if pool.maybe_get(instance_id) is not None:
            await pool.close(instance_id, force=True)
