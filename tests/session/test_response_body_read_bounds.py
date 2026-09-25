# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A failed body is read only when its decoded size is bounded before the read.

Playwright has no ranged or streaming body read: ``response.body()``
materialises the whole decoded body, and the 2 KiB retention cap slices it
afterwards. A same-origin 500 with no ``Content-Length`` (chunked), or a small
compressed payload that decodes enormously, was therefore read whole into the
leader -- per response, concurrently. Now: no trustworthy length, no read; a
compressed body only where even a worst-case gzip/deflate decode stays under
the read ceiling; and a bounded number of reads in flight per session.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.session.core_network_mixin import (
    COMPRESSED_BODY_READ_MAX_BYTES,
    RESPONSE_BODY_READS_IN_FLIGHT_MAX,
    SessionNetworkMixin,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Net(SessionNetworkMixin):
    def __init__(self) -> None:
        self.url = "https://app.test/orders"
        self._bg_tasks: set[Any] = set()


def _response(headers: dict[str, str], body: bytes = b'{"detail": "x"}') -> MagicMock:
    response = MagicMock()
    response.status = 500
    response.headers = headers
    response.body = AsyncMock(return_value=body)
    request = MagicMock()
    request.url = "https://app.test/api"
    response.request = request
    return response


async def _read(response: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"url": "https://app.test/api"}
    await _Net()._read_response_body(response, row, 2048, row["url"])
    return row


@pytest.mark.anyio
@pytest.mark.parametrize("headers", [{}, {"content-length": "garbage"}, {"content-length": "-5"}])
async def test_no_trustworthy_length_means_no_read(headers: dict[str, str]) -> None:
    response = _response(headers)
    row = await _read(response)
    response.body.assert_not_awaited()
    assert row["body_skipped"] == "unknown_length"
    assert "body" not in row


@pytest.mark.anyio
async def test_a_declared_length_is_still_read() -> None:
    """The common case keeps its diagnostic."""
    row = await _read(_response({"content-length": "15"}))
    assert row["body"] == '{"detail": "x"}'
    assert "body_skipped" not in row


@pytest.mark.anyio
@pytest.mark.parametrize("encoding", ["gzip", "deflate", "x-gzip", "GZIP"])
async def test_a_small_gzip_body_is_read(encoding: str) -> None:
    row = await _read(_response({"content-length": "40", "content-encoding": encoding}))
    assert row["body"] == '{"detail": "x"}'


@pytest.mark.anyio
async def test_a_gzip_body_that_could_decode_past_the_ceiling_is_not_read() -> None:
    response = _response({"content-length": str(COMPRESSED_BODY_READ_MAX_BYTES + 1), "content-encoding": "gzip"})
    row = await _read(response)
    response.body.assert_not_awaited()
    assert row["body_skipped"] == "encoded"


@pytest.mark.anyio
@pytest.mark.parametrize("encoding", ["br", "zstd", "compress", "gzip, br"])
async def test_an_unboundable_encoding_is_not_read(encoding: str) -> None:
    """Brotli and zstd have no useful worst-case ratio: a few hundred bytes can
    decode to gigabytes, so no declared length makes them safe to read."""
    response = _response({"content-length": "40", "content-encoding": encoding})
    row = await _read(response)
    response.body.assert_not_awaited()
    assert row["body_skipped"] == "encoded"


@pytest.mark.anyio
async def test_identity_encoding_is_uncompressed() -> None:
    row = await _read(_response({"content-length": "15", "content-encoding": "identity"}))
    assert row["body"] == '{"detail": "x"}'


@pytest.mark.anyio
async def test_reads_in_flight_are_bounded_per_session() -> None:
    net = _Net()
    gate = asyncio.Event()

    async def slow_body() -> bytes:
        await gate.wait()
        return b"{}"

    rows: list[dict[str, Any]] = []
    for _ in range(RESPONSE_BODY_READS_IN_FLIGHT_MAX + 3):
        response = _response({"content-length": "2"})
        response.body = AsyncMock(side_effect=slow_body)
        row: dict[str, Any] = {"url": "https://app.test/api"}
        rows.append(row)
        net._maybe_capture_body(response, row)

    assert len(net._bg_tasks) == RESPONSE_BODY_READS_IN_FLIGHT_MAX
    assert [row.get("body_skipped") for row in rows[-3:]] == ["busy"] * 3
    gate.set()
    await asyncio.gather(*list(net._bg_tasks))
    assert sum("body" in row for row in rows) == RESPONSE_BODY_READS_IN_FLIGHT_MAX
