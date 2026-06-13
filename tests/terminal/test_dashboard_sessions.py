# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
import sys

import pytest
from starlette.requests import Request

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="PTY is POSIX-only")


def _get_request(path: str = "/api/sessions") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "path_params": {},
        }
    )


async def test_terminal_pool_exposed_on_http_state() -> None:
    import octowright.http.state as state
    from octowright.server import _state as server_state

    # The HTTP layer reads pools through state.<name>; terminal_pool must
    # forward to the server singleton (non-None when the extra is installed).
    assert state.terminal_pool is server_state.terminal_pool


async def test_list_sessions_includes_terminals() -> None:
    import octowright.http.state as state
    from octowright.http.routes.sessions import list_sessions

    launched = await state.terminal_pool.launch(kind="pty", connector_config={"command": "/bin/cat"}, label="t")
    iid = launched["instance_id"]
    try:
        resp = await list_sessions(_get_request())
        body = json.loads(resp.body)
        assert any(s["id"] == iid and s["kind"] == "terminal" for s in body["live"])
    finally:
        await state.terminal_pool.close(iid, force=True)


async def test_session_detail_terminal_does_not_crash() -> None:
    import octowright.http.state as state
    from octowright.http.routes.sessions import session_detail

    launched = await state.terminal_pool.launch(kind="pty", connector_config={"command": "/bin/cat"}, label="t")
    iid = launched["instance_id"]
    try:
        req = Request(
            {
                "type": "http",
                "method": "GET",
                "path": f"/api/sessions/{iid}",
                "headers": [],
                "query_string": b"",
                "path_params": {"id": iid},
            }
        )
        resp = await session_detail(req)
        body = json.loads(resp.body)
        # Terminal detail returns the summary shape with kind=terminal and no
        # browser-only artefacts — it must not run the browser detail builder.
        assert body["id"] == iid
        assert body["kind"] == "terminal"
        assert body["connector_type"] == "pty"
        assert body["video_path"] is None
    finally:
        await state.terminal_pool.close(iid, force=True)
