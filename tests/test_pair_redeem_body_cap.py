# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The unauthenticated pairing redemption route reads a bounded body.

``/api/pair/redeem`` is the one body-reading route a caller reaches without
any credential -- it is the bootstrap. It decoded the body before rejecting a
bad code, and the global ``OCTOWRIGHT_MAX_REQUEST_BODY_BYTES`` ceiling is off
by default, so a different local user or a sandboxed process could make the
leader buffer an arbitrarily large body per request. A pairing code needs a
few dozen bytes; the route's own ceiling is fixed and cannot be switched off.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient

from octowright.http.app import build_app
from octowright.http.routes.pairing import PAIR_REDEEM_MAX_BODY_BYTES

_TOKEN = "test-cap-token"  # pragma: allowlist secret (synthetic fixture)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("OCTOWRIGHT_MAX_REQUEST_BODY_BYTES", raising=False)
    return TestClient(build_app(mcp_token=_TOKEN))


def test_an_oversized_declared_body_is_refused(client: TestClient) -> None:
    body = b'{"code": "' + b"x" * (PAIR_REDEEM_MAX_BODY_BYTES * 4) + b'"}'
    response = client.post("/api/pair/redeem", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 413


def test_an_oversized_chunked_body_is_refused(client: TestClient) -> None:
    """No Content-Length: the cap has to be counted, not read off a header."""

    def chunks() -> Iterator[bytes]:
        yield b'{"code": "'
        for _ in range(64):
            yield b"x" * 1024
        yield b'"}'

    response = client.post("/api/pair/redeem", content=chunks(), headers={"content-type": "application/json"})
    assert response.status_code == 413


def test_the_cap_holds_even_when_the_global_ceiling_is_larger(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OCTOWRIGHT_MAX_REQUEST_BODY_BYTES", str(10 * 1024 * 1024))
    body = b'{"code": "' + b"x" * (PAIR_REDEEM_MAX_BODY_BYTES * 4) + b'"}'
    response = client.post("/api/pair/redeem", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 413


def test_a_real_code_still_redeems(client: TestClient) -> None:
    pairing = client.app.state.dashboard_pairing  # type: ignore[attr-defined]
    response = client.post("/api/pair/redeem", json={"code": pairing.mint_code()})
    assert response.status_code == 200
    assert response.json()["bearer"]


def test_a_bad_code_is_still_a_403(client: TestClient) -> None:
    assert client.post("/api/pair/redeem", json={"code": "nope"}).status_code == 403
