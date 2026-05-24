# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Regression test for BrowserSession telemetry context behaviour.

Historically ``BrowserSession.__post_init__`` called
``provide.telemetry.bind_context`` with the per-session identifiers
(``instance_id``, ``kind``, ``profile``, ``label``). That binding was
fragile because contextvars only flow within the asyncio task that set
them — subsequent MCP tool calls ran on different tasks and did not
inherit it. The binding was dropped (see finding A7); span attributes
are the canonical way to attach session identity to telemetry that
survives across tool calls, and structured log calls pass
``instance_id=`` explicitly.

This test guards against accidentally re-introducing the contextvar
binding by failing if session construction touches the telemetry
``bind_context`` / ``unbind_context`` helpers.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.session.core import BrowserSession


def _make_session(
    *,
    instance_id: str = "iid-1",
    kind: str = "chromium",
    profile: str | None = "alice",
    label: str | None = "L",
) -> BrowserSession:
    """Construct a minimal BrowserSession suitable for lifecycle assertions."""
    context = MagicMock()
    context.close = AsyncMock(return_value=None)
    return BrowserSession(
        instance_id=instance_id,
        kind=kind,
        label=label,
        url="https://example.test",
        browser=None,
        context=context,
        page=MagicMock(),
        recorder=MagicMock(),
        log_path=Path("/tmp/octowright-telemetry-test.jsonl"),
        profile=profile,
    )


class TestNoContextvarBinding:
    """Session construction must not touch the telemetry contextvar helpers."""

    def test_post_init_does_not_call_bind_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Re-introducing ``bind_context`` would silently restore the
        fragile per-task binding the A7 finding removed. If somebody adds
        an import + call back into ``session.core``, this test catches it."""
        import provide.telemetry as _telemetry

        bind_spy = MagicMock(side_effect=AssertionError("bind_context must not be called"))
        unbind_spy = MagicMock(side_effect=AssertionError("unbind_context must not be called"))
        monkeypatch.setattr(_telemetry, "bind_context", bind_spy)
        monkeypatch.setattr(_telemetry, "unbind_context", unbind_spy)

        session = _make_session(instance_id="iid-42", kind="webkit", profile="bob", label="player")

        # Session identity must still be set — it just lives on the dataclass,
        # not on a contextvar.
        assert session.instance_id == "iid-42"
        assert session.kind == "webkit"
        assert session.profile == "bob"
        assert session.label == "player"
        bind_spy.assert_not_called()
        unbind_spy.assert_not_called()

    def test_session_has_no_unbind_telemetry_context_helper(self) -> None:
        """The unbind helper was the symmetric counterpart of the bind
        call. If it ever reappears, this test points at where to delete
        it again. Failing here means somebody added back the binding."""
        session = _make_session()
        assert not hasattr(session, "unbind_telemetry_context")
