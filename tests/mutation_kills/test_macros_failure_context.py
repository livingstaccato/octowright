# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What a macro failure payload asks its producers for, and what it keeps of their answers."""

from __future__ import annotations

from typing import Any

import pytest

from octowright.macros.failure_context import (
    MACRO_FAILURE_CONSOLE_TAIL,
    _diagnostic_bundle,
    failed_requests_tail,
    payload_url,
)


class _Session:
    def __init__(self, raw: Any = None, rows: list[dict[str, Any]] | None = None) -> None:
        self.raw = {"url": "https://app.example/x"} if raw is None else raw
        self.rows = rows or []
        self.calls: list[dict[str, Any]] = []

    async def diagnostic_bundle(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.raw

    def get_network_requests(self, *, limit: Any) -> dict[str, Any]:
        return {"requests": self.rows}


@pytest.mark.parametrize("url", [None, 5, ["https://a.example/?t=1"]])
def test_a_non_string_url_passes_through(url: Any) -> None:
    assert payload_url(url) == url


def test_status_400_is_a_failed_request_and_399_is_not() -> None:
    session = _Session(
        rows=[
            {"url": "https://a.example/ok", "status": 399},
            {"url": "https://a.example/bad?sig=s3cr3t", "status": 400},
            {"url": "https://a.example/none", "status": None},
            {"url": "https://a.example/dropped", "failure": "net::ERR_FAILED"},
        ]
    )

    assert failed_requests_tail(session) == [  # type: ignore[arg-type]
        {"url": "https://a.example/bad", "status": 400},
        {"url": "https://a.example/dropped", "failure": "net::ERR_FAILED"},
    ]


async def test_the_bundle_asks_for_the_console_tail_without_values() -> None:
    session = _Session()

    assert await _diagnostic_bundle(session, ()) == {"url": "https://app.example/x"}  # type: ignore[arg-type]
    assert session.calls == [{"console_tail": MACRO_FAILURE_CONSOLE_TAIL}]


async def test_the_bundle_asks_for_the_console_tail_with_values_and_no_screenshot() -> None:
    session = _Session(raw={"console_tail": [{"text": "pw zebrin4-secret"}]})

    bundle = await _diagnostic_bundle(session, ("zebrin4-secret",))  # type: ignore[arg-type]

    assert bundle == {"console_tail": [{"text": "pw <redacted>"}]}
    assert [sorted(call) for call in session.calls] == [["console_tail", "screenshot", "scrub"]]
    assert session.calls[0]["console_tail"] == MACRO_FAILURE_CONSOLE_TAIL
    assert session.calls[0]["screenshot"] is False


async def test_a_non_dict_bundle_becomes_empty() -> None:
    assert await _diagnostic_bundle(_Session(raw=["not", "a", "dict"]), ()) == {}  # type: ignore[arg-type]
