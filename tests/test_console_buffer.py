# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from collections import deque
from unittest.mock import MagicMock

import pytest

from octowright.session.core import BrowserSession


@pytest.fixture
def mock_recorder():
    return MagicMock()


@pytest.fixture
def session(mock_recorder, tmp_path):
    return BrowserSession(
        instance_id="test-session",
        kind="chromium",
        label="test",
        url="about:blank",
        browser=MagicMock(),
        context=MagicMock(),
        page=MagicMock(),
        recorder=mock_recorder,
        log_path=tmp_path / "session.jsonl",
    )


def test_console_buffer_is_deque(session):
    assert isinstance(session.console, deque)
    assert session.console.maxlen == 1000


def test_console_buffer_limit(session):
    for i in range(1100):
        session.console.append({"level": "log", "text": f"msg {i}"})

    assert len(session.console) == 1000
    assert session.console[0]["text"] == "msg 100"
    assert session.console[-1]["text"] == "msg 1099"


def test_register_popup_records_console(session, mock_recorder):
    mock_page = MagicMock()
    mock_page.url = "http://popup.com"

    # This is a bit tricky due to how it's imported in _register_popup.
    # Keep it simple: ensure event wiring works and updates in-session log.

    session._register_popup(mock_page)

    # Check if console listener was attached to the NEW page
    assert mock_page.on.called
    args, _kwargs = mock_page.on.call_args_list[0]
    assert args[0] == "console"
    handler = args[1]

    mock_msg = MagicMock()
    mock_msg.type = "error"
    mock_msg.text = "popup error"

    handler(mock_msg)

    assert len(session.console) == 1
    assert session.console[0] == {"level": "error", "text": "popup error", "page_index": 1}
    mock_recorder.record.assert_any_call("console", level="error", text="popup error", page_index=1)
