# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Internal-only launch options are closed at every external entry point, in one place.

``trusted_launch_url`` picks the URL the macro credential guards trust, and
``session_key`` names another browser's ``session=True`` directory -- its
cookies. Only handoff/relaunch may set them. ``LaunchOptions`` accepts both,
so each entry point that builds options from a caller's MAPPING had to refuse
them itself: the roster did, and ``POST /api/sessions`` did not, which let a
paired dashboard client (or anything holding the bridge token) choose either.

The refusal now lives in ``LaunchOptions.from_external_mapping``, and the scan
below fails on any module outside the pool that builds options from a mapping
some other way, so a new entry point cannot quietly reopen this.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from octowright.browser_pool.options import INTERNAL_ONLY_LAUNCH_FIELDS, LaunchOptions
from octowright.request_errors import InvalidRequestError

_SRC = Path(__file__).resolve().parents[1] / "src" / "octowright"

#: The modules allowed to call the unguarded ``from_mapping``: the pool itself
#: (``pool.launch`` kwargs, which handoff/relaunch reach with these options
#: set on purpose) and ``options.py`` (``from_launch_record`` builds its own
#: mapping without them, and ``from_external_mapping`` is the guard).
_INTERNAL_CALLERS = {"browser_pool/pool.py", "browser_pool/options.py"}

#: Every external entry point that builds launch options from a mapping,
#: named so a new one has to be added here -- and therefore looked at.
_EXTERNAL_CALLERS = {"browser_pool/roster.py", "http/routes/sessions.py"}


def _mapping_builders(method: str) -> set[str]:
    found: set[str] = set()
    for path in _SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == method:
                found.add(path.relative_to(_SRC).as_posix())
    return found


def test_the_internal_only_set_is_exactly_the_two_options() -> None:
    assert frozenset({"trusted_launch_url", "session_key"}) == INTERNAL_ONLY_LAUNCH_FIELDS


def test_only_the_pool_builds_launch_options_from_an_unguarded_mapping() -> None:
    assert _mapping_builders("from_mapping") <= _INTERNAL_CALLERS


def test_every_external_entry_point_goes_through_the_guard() -> None:
    assert _mapping_builders("from_external_mapping") == _EXTERNAL_CALLERS


@pytest.mark.parametrize("key", sorted(INTERNAL_ONLY_LAUNCH_FIELDS))
def test_the_guard_refuses_each_internal_option(key: str) -> None:
    with pytest.raises(InvalidRequestError, match=key):
        LaunchOptions.from_external_mapping({"kind": "chromium", "session": True, key: "x"}, source="a test")


@pytest.mark.parametrize("key", sorted(INTERNAL_ONLY_LAUNCH_FIELDS))
def test_a_roster_spec_may_not_set_an_internal_option(key: str) -> None:
    from octowright.browser_pool.roster import roster_launch_kwargs

    with pytest.raises(InvalidRequestError, match=key):
        roster_launch_kwargs({"session": True, key: "x"})


@pytest.mark.parametrize("key", sorted(INTERNAL_ONLY_LAUNCH_FIELDS))
def test_the_http_launch_route_refuses_an_internal_option(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    from starlette.testclient import TestClient

    from octowright import http as _http
    from octowright.server import _state

    launched: list[dict[str, Any]] = []

    class _Pool:
        async def launch(self, **kwargs: Any) -> dict[str, Any]:
            launched.append(kwargs)
            raise AssertionError("must be refused before launching")

    monkeypatch.setattr(_state, "pool", _Pool())
    client = TestClient(_http.build_app())

    r = client.post("/api/sessions", json={"kind": "chromium", "session": True, key: "https://evil.test/"})

    assert r.status_code == 400, r.text
    assert key in r.json()["error"]
    assert launched == []
