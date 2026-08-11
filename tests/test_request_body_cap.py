# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Route-level request-body ceiling (OCTOWRIGHT_MAX_REQUEST_BODY_BYTES).

OFF by default (back-compat). When set, oversized JSON bodies are rejected with
413 before being fully materialized, and a lying/absent Content-Length can't
bypass the stream counter.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from starlette.requests import Request

from octowright.http.routes._common import _read_json_body


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _req(body: bytes, headers: dict[str, str] | None = None) -> Request:
    hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope: dict[str, Any] = {"type": "http", "method": "POST", "headers": hdrs, "query_string": b""}
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


@pytest.mark.anyio
async def test_body_cap_rejects_oversized_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_MAX_REQUEST_BODY_BYTES", "100")
    big = json.dumps({"x": "y" * 500}).encode()
    payload, err = await _read_json_body(
        _req(big, {"content-type": "application/json", "content-length": str(len(big))})
    )
    assert payload is None
    assert err is not None
    assert err.status_code == 413


@pytest.mark.anyio
async def test_body_cap_lying_content_length_still_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_MAX_REQUEST_BODY_BYTES", "100")
    big = json.dumps({"x": "y" * 500}).encode()
    # No Content-Length header — the stream counter must still enforce the cap.
    payload, err = await _read_json_body(_req(big, {"content-type": "application/json"}))
    assert payload is None
    assert err is not None
    assert err.status_code == 413


@pytest.mark.anyio
async def test_body_within_limit_is_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_MAX_REQUEST_BODY_BYTES", "1000")
    small = json.dumps({"x": "y"}).encode()
    payload, err = await _read_json_body(_req(small, {"content-type": "application/json"}))
    assert err is None
    assert payload == {"x": "y"}


@pytest.mark.anyio
async def test_body_cap_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_MAX_REQUEST_BODY_BYTES", raising=False)
    big = json.dumps({"x": "y" * 5000}).encode()
    payload, err = await _read_json_body(_req(big, {"content-type": "application/json"}))
    assert err is None
    assert payload["x"] == "y" * 5000
