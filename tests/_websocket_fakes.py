# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Shared doubles for the websocket capture path.

The mixin's websocket state is a block of declared fields on
``session/core.BrowserSession``, and a test double has to mirror it. Two test
modules had grown their own copy, so adding ``_websocket_seq`` to the real
session broke both -- which is the cost of a hand-mirrored field list, and the
reason this lives in one place beside ``tests/_aria_stubs.py`` and
``tests/_operation_gate_fakes.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock


class FakeSocket:
    """Playwright's WebSocket surface, emitting payloads the way it really does.

    No ``.id``, like the real playwright-python binding -- set one explicitly
    in a test that wants to pin the binding-supplied path.
    """

    def __init__(self, url: str = "ws://app.test/stream") -> None:
        self.url = url
        self._handlers: dict[str, Any] = {}

    def on(self, event: str, handler: Any) -> None:
        self._handlers[event] = handler

    def emit(self, event: str, *args: Any) -> None:
        self._handlers[event](*args) if args else self._handlers[event]()


def io_mixin_session(tmp_path: Path) -> Any:
    """A ``SessionIOMixin`` with just the websocket state it reads."""
    from octowright.session.core_io_mixin import SessionIOMixin

    class _Subject(SessionIOMixin):
        def __init__(self) -> None:
            self.recorder = MagicMock()
            self.log_path = tmp_path / "rec.jsonl"
            self.websocket_path = None
            self._websockets: dict[str, dict[str, Any]] = {}
            self._websockets_dropped = 0
            self._websocket_seq = 0
            self._websocket_truncated = False

        def _websocket_cache_path(self) -> Path:
            # Lives on BrowserSession, not the mixin under test.
            return self.log_path.with_suffix(".websocket.jsonl")

    return _Subject()


def sidecar_rows(session: Any) -> list[dict[str, Any]]:
    """Every row the session has written to its websocket sidecar."""
    session._flush_websocket_cache()
    assert session.websocket_path is not None
    return [json.loads(line) for line in session.websocket_path.read_text().splitlines()]
