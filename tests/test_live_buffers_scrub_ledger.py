# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A password the page echoes is scrubbed from the LIVE buffers, not only the JSONL.

The session ledger was applied by ``SensitiveRecorder`` at the recorder
boundary, so a JSONL row was scrubbed -- but the console handler had already
appended the raw entry to ``session.console``, and a failed response's row and
body went into ``_network_requests`` raw. ``browser_console_messages``,
``browser_network_requests``, their summaries and the dashboard's live
``/console`` all read those buffers, and handed the cleartext credential back
under the default ``passwords`` redaction mode. Scrubbed at ingestion now, so
every reader -- including the macro failure payload, which reads the same
buffers -- sees one scrubbed copy.
"""

from __future__ import annotations

from collections import deque
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.macros.privacy import REDACTED, admit_redacted_input
from octowright.session.core_io_mixin import SessionIOMixin
from octowright.session.core_network_mixin import SessionNetworkMixin

SECRET = "hunter2-live"  # pragma: allowlist secret -- a fixture, never a real credential


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Recorder:
    def __init__(self) -> None:
        self.rows: list[tuple[str, dict[str, Any]]] = []

    def record(self, action: str, **fields: Any) -> None:
        self.rows.append((action, fields))

    def record_control(self, action: str, **fields: Any) -> None:
        self.rows.append((action, fields))


class _Page:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}

    def on(self, event: str, handler: Any) -> None:
        self.handlers[event] = handler


class _Session(SessionIOMixin, SessionNetworkMixin):
    def __init__(self) -> None:
        self.recorder: Any = _Recorder()
        self.durable_text_scrubber = None
        self.page: Any = _Page()
        self.console: deque[dict[str, Any]] = deque(maxlen=1000)
        self.console_count = 0
        self.page_errors: deque[dict[str, Any]] = deque(maxlen=200)
        self.url = "https://app.test/login"
        self._bg_tasks: set[Any] = set()
        self._network_requests: deque[dict[str, Any]] = deque(maxlen=5000)
        self._network_requests_dropped = 0
        self._network = MagicMock()


def _console_message(text: str) -> Any:
    msg = MagicMock()
    msg.type = "log"
    msg.text = text
    return msg


def _response(status: int, url: str, body: bytes) -> Any:
    response = MagicMock()
    response.status = status
    response.status_text = "Server Error"
    response.headers = {"content-length": str(len(body))}
    response.body = AsyncMock(return_value=body)
    request = MagicMock()
    request.url = url
    request.method = "POST"
    request.resource_type = "fetch"
    request.headers = {"x-echo": SECRET}
    response.request = request
    return response


def test_a_console_echo_is_scrubbed_in_the_live_buffer() -> None:
    session = _Session()
    admit_redacted_input(session, SECRET)
    session.attach_console()

    session.page.handlers["console"](_console_message(f"typed {SECRET} into the form"))

    assert list(session.console) == [{"level": "log", "text": f"typed {REDACTED} into the form"}]
    assert session.recorder._recorder.rows[-1][1]["text"] == f"typed {REDACTED} into the form"


def test_a_console_message_before_any_admission_is_untouched() -> None:
    """No ledger, no cost and no change: the common case."""
    session = _Session()
    session.attach_console()

    session.page.handlers["console"](_console_message(f"plain {SECRET}"))

    assert session.console[-1]["text"] == f"plain {SECRET}"


def test_a_page_error_echo_is_scrubbed() -> None:
    session = _Session()
    admit_redacted_input(session, SECRET)

    session._handle_page_error(RuntimeError(f"bad password {SECRET}"))

    assert session.page_errors[-1] == {"message": f"bad password {REDACTED}"}


@pytest.mark.anyio
async def test_a_failed_response_url_headers_and_body_are_scrubbed() -> None:
    session = _Session()
    admit_redacted_input(session, SECRET)

    session._handle_response(
        _response(500, f"https://app.test/api/login?pw={SECRET}", f'{{"error":"wrong {SECRET}"}}'.encode())
    )
    for task in list(session._bg_tasks):
        await task

    rows = session.get_network_requests(include_headers=True)["requests"]
    assert SECRET not in repr(rows)
    assert rows[0]["url"] == f"https://app.test/api/login?pw={REDACTED}"
    assert rows[0]["body"] == f'{{"error":"wrong {REDACTED}"}}'


def test_a_failed_request_is_scrubbed() -> None:
    session = _Session()
    admit_redacted_input(session, SECRET)
    request = MagicMock()
    request.url = f"https://app.test/api?pw={SECRET}"
    request.method = "GET"
    request.resource_type = "fetch"
    request.failure = f"net::ERR_FAILED {SECRET}"
    request.headers = {}

    session._handle_request_failed(request)

    assert SECRET not in repr(list(session._network_requests))


def test_the_websocket_registry_url_is_scrubbed(tmp_path: Any) -> None:
    from tests._websocket_fakes import FakeSocket, io_mixin_session

    session = io_mixin_session(tmp_path)
    admit_redacted_input(session, SECRET)
    session._handle_websocket(FakeSocket(url=f"wss://app.test/socket?token={SECRET}"))

    assert SECRET not in repr(session.get_websocket_summary())
