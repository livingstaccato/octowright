# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A macro failure carries the requests that explain it.

A timeout is almost never the bug -- it is the symptom of something the page
reported and the macro could not see. The console tail and final URL already
reached the failure payload; the failed requests did not, so a payload could
report "timed out waiting for #foo" while the 409 explaining it sat unread in
the session's own deque.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from octowright.macros.execution import MACRO_FAILURE_NETWORK_TAIL, _failed_requests_tail


def _session(rows: list[dict[str, Any]]) -> MagicMock:
    session = MagicMock()
    session.get_network_requests = MagicMock(return_value={"requests": rows})
    return session


class TestFailedRequestsTail:
    def test_non_2xx_rows_are_selected(self) -> None:
        session = _session(
            [
                {"url": "/ok", "status": 200},
                {"url": "/conflict", "status": 409, "body": '{"detail": "component_allocation_required"}'},
            ]
        )
        selected = _failed_requests_tail(session)
        assert [row["url"] for row in selected] == ["/conflict"]
        assert selected[0]["body"] == '{"detail": "component_allocation_required"}'

    def test_transport_failures_are_selected_too(self) -> None:
        """A request that never got a status is exactly the ERR_NETWORK_CHANGED
        class of cause this exists to surface."""
        session = _session([{"url": "/x", "status": None, "failure": "net::ERR_NETWORK_CHANGED"}])
        assert len(_failed_requests_tail(session)) == 1

    def test_successful_rows_are_excluded(self) -> None:
        session = _session([{"url": f"/ok{i}", "status": 200} for i in range(5)])
        assert _failed_requests_tail(session) == []

    def test_bounded_to_the_newest_failures(self) -> None:
        """A long-running step must not produce an unreadable payload."""
        rows = [{"url": f"/fail{i}", "status": 500} for i in range(MACRO_FAILURE_NETWORK_TAIL + 15)]
        selected = _failed_requests_tail(_session(rows))
        assert len(selected) == MACRO_FAILURE_NETWORK_TAIL
        # Newest kept, not oldest -- the recent ones are the relevant ones.
        assert selected[-1]["url"] == rows[-1]["url"]

    def test_a_session_that_cannot_answer_yields_no_block(self) -> None:
        """Best-effort: this must never turn a macro failure into a different,
        more confusing failure."""
        session = MagicMock()
        session.get_network_requests = MagicMock(side_effect=RuntimeError("gate broken"))
        assert _failed_requests_tail(session) == []

    def test_reads_the_whole_deque_not_a_capped_page(self) -> None:
        """A capped read would silently drop the failure that explains the
        timeout whenever it sat outside the default page."""
        session = _session([])
        _failed_requests_tail(session)
        assert session.get_network_requests.call_args.kwargs == {"limit": None}


class TestPayloadPlacement:
    """`failed_requests` is a sibling of `bundle`, not a key inside it.

    `bundle` is what `diagnostic_bundle()` returned. Folding another
    producer's data into it makes that claim false for every reader, and it
    broke a whole-record assertion that had every right to hold.
    """

    def test_bundle_is_left_exactly_as_diagnostic_bundle_returned_it(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock

        import pytest

        from octowright.macros import execution

        session = MagicMock()
        session.diagnostic_bundle = AsyncMock(return_value={"hint": "yo"})
        session.get_network_requests = MagicMock(return_value={"requests": [{"url": "/conflict", "status": 409}]})

        async def _boom(*_args: Any, **_kwargs: Any) -> tuple[int, int]:
            raise ValueError("boom")

        with (
            patch.object(execution, "_dispatch_one", _boom),
            patch.object(execution, "load_macro", return_value={"actions": [{"action": "click"}]}),
            patch.object(execution, "_push_status", AsyncMock()),
            patch.object(execution, "_suggest_fix", AsyncMock(return_value=None)),
            pytest.raises(RuntimeError) as exc_info,
        ):
            asyncio.run(execution.run_macro(session, "m"))

        payload = exc_info.value.args[0]
        assert payload["bundle"] == {"hint": "yo"}
        assert payload["failed_requests"] == [{"url": "/conflict", "status": 409}]
