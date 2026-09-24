# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Page-controlled output is bounded per event, before it is serialised.

A visited page decides how big a console message, a websocket frame or an
error body is. The global disk ceilings are off by default, and where they are
on they were checked only after the full payload had been copied,
base64-expanded and ``json.dumps``'d -- so one multi-megabyte event cost its
full size in memory (and, for console, on disk) regardless. These pin the
per-event caps that bound it at the source.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.session import core_io_mixin as _io
from octowright.session._constants import CONSOLE_TEXT_MAX_CHARS, WEBSOCKET_FRAME_MAX_BYTES
from octowright.session.core_network_mixin import RESPONSE_BODY_READ_MAX_BYTES, SessionNetworkMixin
from tests.test_session_io_mixin_branches import _make_subject


@pytest.fixture(autouse=True)
def _flush_every_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_io, "WEBSOCKET_CACHE_FLUSH_FRAMES", 1)
    monkeypatch.setattr(_io, "WEBSOCKET_CACHE_FLUSH_SECONDS", 0.0)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _console_handler(subj: Any) -> Any:
    subj.attach_console()
    return subj.page.on.call_args[0][1]


# ─── console ─────────────────────────────────────────────────────────────────


def test_oversized_console_text_is_capped_before_recording(tmp_path: Path) -> None:
    subj = _make_subject(tmp_path)
    handler = _console_handler(subj)
    handler(MagicMock(type="log", text="A" * (CONSOLE_TEXT_MAX_CHARS * 5)))
    fields = subj.recorder.record.call_args.kwargs
    assert len(fields["text"]) < CONSOLE_TEXT_MAX_CHARS + 100
    assert fields["text"].startswith("A" * 100)
    assert fields["text_truncated"] is True
    assert fields["text_length"] == CONSOLE_TEXT_MAX_CHARS * 5
    # The in-memory ring buffer holds the capped entry too.
    assert subj.console[-1]["text"] == fields["text"]


def test_ordinary_console_text_is_untouched(tmp_path: Path) -> None:
    subj = _make_subject(tmp_path)
    handler = _console_handler(subj)
    handler(MagicMock(type="warning", text="hello"))
    assert subj.recorder.record.call_args.kwargs == {"level": "warning", "text": "hello"}


def test_popup_console_text_is_capped_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import octowright.browser_pool.listeners as listeners

    monkeypatch.setattr(listeners, "_wire_listeners", lambda *_a, **_k: None)
    subj = _make_subject(tmp_path)
    popup = MagicMock()
    popup.url = "https://popup.test/"
    subj._register_popup(popup)
    handler = popup.on.call_args_list[0][0][1]
    handler(MagicMock(type="log", text="B" * (CONSOLE_TEXT_MAX_CHARS + 1)))
    fields = subj.recorder.record.call_args.kwargs
    assert fields["text_truncated"] is True
    assert fields["page_index"] == 0


# ─── websocket sidecar ───────────────────────────────────────────────────────


def _rows(subj: Any) -> list[dict[str, Any]]:
    return [json.loads(line) for line in subj.websocket_path.read_text().splitlines()]


def test_frame_over_the_ceiling_is_dropped_before_encoding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With a ceiling set, a frame that cannot fit is never base64'd."""
    monkeypatch.setenv("OCTOWRIGHT_WEBSOCKET_MAX_BYTES", "4096")
    encoded: list[int] = []
    real = base64.b64encode

    def spy(data: bytes, *args: Any) -> bytes:
        encoded.append(len(data))
        return real(data, *args)

    monkeypatch.setattr(_io.base64, "b64encode", spy)
    subj = _make_subject(tmp_path)
    subj._append_websocket_cache(direction="framereceived", id_=1, url="ws://x", payload=b"\x00" * 1_000_000)
    assert encoded == []
    rows = _rows(subj)
    assert rows[-1]["action"] == "websocket_truncated"


def test_frame_that_fits_the_ceiling_is_stored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_WEBSOCKET_MAX_BYTES", "4096")
    subj = _make_subject(tmp_path)
    subj._append_websocket_cache(direction="framereceived", id_=1, url="ws://x", payload="small")
    assert _rows(subj)[-1]["payload_text"] == "small"


def test_without_a_ceiling_one_frame_is_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_WEBSOCKET_MAX_BYTES", raising=False)
    subj = _make_subject(tmp_path)
    size = WEBSOCKET_FRAME_MAX_BYTES * 3
    subj._append_websocket_cache(direction="framereceived", id_=1, url="ws://x", payload=b"\x01" * size)
    row = _rows(subj)[-1]
    assert row["payload_size"] == size
    assert row["payload_truncated"] is True
    assert len(base64.b64decode(row["payload_b64"])) == WEBSOCKET_FRAME_MAX_BYTES


def test_without_a_ceiling_a_text_frame_is_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_WEBSOCKET_MAX_BYTES", raising=False)
    subj = _make_subject(tmp_path)
    size = WEBSOCKET_FRAME_MAX_BYTES + 10
    subj._append_websocket_cache(direction="framesent", id_=1, url="ws://x", payload="t" * size)
    row = _rows(subj)[-1]
    assert row["payload_size"] == size
    assert row["payload_truncated"] is True
    assert len(row["payload_text"]) == WEBSOCKET_FRAME_MAX_BYTES


def test_small_frame_carries_no_truncation_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_WEBSOCKET_MAX_BYTES", raising=False)
    subj = _make_subject(tmp_path)
    subj._append_websocket_cache(direction="framesent", id_=1, url="ws://x", payload=b"ok")
    assert "payload_truncated" not in _rows(subj)[-1]


def test_the_ceiling_is_read_once_per_frame(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``OCTOWRIGHT_WEBSOCKET_MAX_BYTES`` is resolved once per frame, not per check."""
    monkeypatch.setenv("OCTOWRIGHT_WEBSOCKET_MAX_BYTES", "4096")
    calls: list[int] = []
    real = _io._websocket_max_bytes
    monkeypatch.setattr(_io, "_websocket_max_bytes", lambda: calls.append(1) or real())
    subj = _make_subject(tmp_path)
    subj._append_websocket_cache(direction="framesent", id_=1, url="ws://x", payload=b"ok", payload_size=2)
    assert len(calls) == 1


def test_a_bytes_repr_frame_is_sized_by_the_callers_exact_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The caller already decoded the repr; its exact size refuses the frame before any copy.

    ``len(repr) // 4`` fits under the ceiling here, so the estimate let the
    frame be decoded and base64'd only for the finished line to be refused.
    """
    monkeypatch.setenv("OCTOWRIGHT_WEBSOCKET_MAX_BYTES", "4096")
    encoded: list[int] = []
    real = base64.b64encode
    monkeypatch.setattr(_io.base64, "b64encode", lambda data, *a: encoded.append(len(data)) or real(data, *a))
    subj = _make_subject(tmp_path)
    payload = "b'" + "a" * 5000 + "'"
    subj._append_websocket_cache(direction="framereceived", id_=1, url="ws://x", payload=payload, payload_size=5000)
    assert encoded == []
    assert _rows(subj)[-1]["action"] == "websocket_truncated"


# ─── failed-response body ────────────────────────────────────────────────────


class _Net(SessionNetworkMixin):
    def __init__(self) -> None:
        self.url = "https://app.test/orders"
        self._bg_tasks: set[Any] = set()


def _response(content_length: str | None, body: bytes = b"{}") -> MagicMock:
    response = MagicMock()
    response.status = 500
    response.headers = {} if content_length is None else {"content-length": content_length}
    response.body = AsyncMock(return_value=body)
    return response


@pytest.mark.anyio
async def test_body_over_the_read_ceiling_is_never_materialised() -> None:
    response = _response(str(RESPONSE_BODY_READ_MAX_BYTES + 1))
    row: dict[str, Any] = {"url": "https://app.test/api"}
    await _Net()._read_response_body(response, row, 2048, row["url"])
    response.body.assert_not_awaited()
    assert row["body_skipped"] == "too_large"
    assert "body" not in row


@pytest.mark.anyio
@pytest.mark.parametrize("content_length", [None, "900", "garbage"])
async def test_body_without_a_large_declared_length_is_still_read(content_length: str | None) -> None:
    response = _response(content_length, body=b'{"detail": "x"}')
    row: dict[str, Any] = {"url": "https://app.test/api"}
    await _Net()._read_response_body(response, row, 2048, row["url"])
    assert row["body"] == '{"detail": "x"}'
    assert "body_skipped" not in row
