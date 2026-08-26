# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What the dashboard shows for a live terminal.

A terminal has no page/console/download/video/trace artefacts. Core used to
short-circuit before its browser detail builder to achieve this
(``_terminal_session_detail`` in ``http/routes/sessions.py``); the plugin
path gets there differently: ``TerminalPlugin.session_detail`` supplies only
terminal's own additions (``connector_type``, the explicit-``None``
browser-only paths, and ``action_count``), and core's
``plugin_session_detail`` merges those under the same uniform
``_live_summary`` base every plugin gets (started_at/live/protected/
event_count/console_count/download_count/page_count/log_path/...). Both
halves are covered here: the plugin's own contribution in isolation, and the
merged payload a real dashboard request would see.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from octowright_terminal.plugin import plugin

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="PTY is POSIX-only")


class _FakeSession:
    instance_id = "abc123"
    kind = "terminal"
    label = "ops-box"
    connector_type = "ssh"
    log_path = "/tmp/rec/abc123.jsonl"
    protected = False


def test_plugin_contributes_connector_type():
    detail = plugin.session_detail(_FakeSession())
    assert detail["connector_type"] == "ssh"


def test_plugin_contributes_action_count_as_an_int():
    """No ``recorder`` on the fake session: falls back to 0, not a crash."""
    detail = plugin.session_detail(_FakeSession())
    assert detail["action_count"] == 0


def test_plugin_reports_browser_only_paths_as_none_not_omitted():
    """A terminal has none of video/trace/markdown/websocket artefacts, but the
    keys are present as ``None`` (not absent) so dashboard summaries stay
    uniform across session kinds rather than the dashboard branching on which
    keys exist.
    """
    detail = plugin.session_detail(_FakeSession())
    for browser_only_path in ("video_path", "trace_path", "markdown_path", "websocket_path"):
        assert browser_only_path in detail
        assert detail[browser_only_path] is None


def test_plugin_action_count_reads_a_real_recorder():
    session = SimpleNamespace(recorder=SimpleNamespace(action_count=7))
    detail = plugin.session_detail(session)
    assert detail["action_count"] == 7


async def test_merged_detail_carries_both_the_summary_base_and_terminal_fields():
    """End-to-end: what ``GET /api/sessions/{id}`` actually returns for a live
    terminal once it resolves through the plugin registry rather than core's
    hardcoded branch. The `_activated_terminal_plugin` autouse fixture (see
    conftest.py) registers this package's real ``TerminalPool``.
    """
    from octowright.http.routes._session_kinds import plugin_session_detail
    from octowright.server import plugin_state

    pool = plugin_state.registry().pools()["terminal"]
    launched = await pool.launch(kind="pty", connector_config={"command": "/bin/cat"}, label="ops-box")
    iid = launched["instance_id"]
    try:
        session = pool.get(iid)
        detail = plugin_session_detail("terminal", session)

        # The uniform base every plugin gets (from _live_summary), not
        # hand-written by the terminal plugin itself.
        assert detail["id"] == iid
        assert detail["kind"] == "terminal"
        assert detail["label"] == "ops-box"
        assert detail["live"] is True
        assert detail["protected"] is False
        assert "started_at" in detail
        assert "log_path" in detail
        assert detail["event_count"] >= 0
        assert detail["console_count"] == 0
        assert detail["download_count"] == 0
        # TerminalSession has no `pages` attribute; _live_summary's generic
        # fallback (`getattr(..., "pages", ()) or (1,)`) reports 1, same as
        # it always has for terminals via _live_summary's generic contract.
        assert detail["page_count"] == 1

        # Terminal's own additions.
        assert detail["connector_type"] == "pty"
        assert detail["video_path"] is None
        assert detail["trace_path"] is None
        assert detail["markdown_path"] is None
        assert detail["websocket_path"] is None
        assert isinstance(detail["action_count"], int)
    finally:
        await pool.close(iid, force=True)
