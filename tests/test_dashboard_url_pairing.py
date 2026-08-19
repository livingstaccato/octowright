# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""`octowright_dashboard_url` must hand back a URL that actually opens.

The dashboard requires pairing by default, so returning the bare address
would have the agent reporting a broken dashboard: the user clicks and gets
401. This is the only path to the dashboard for a chat client with no
terminal, so it is pinned end to end -- including that the minted code
really is redeemable by the running app, not merely well-formed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import pytest

from octowright.http import app as _http_app
from octowright.http import state as _http_state
from octowright.http.pairing import MCP_PAIR_CODE_TTL_SECONDS, PAIR_CODE_TTL_SECONDS
from octowright.server import _state, meta
from octowright.server.meta import octowright_dashboard_url

_ENV = "OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING"
_TOKEN = "test-cap-token"  # pragma: allowlist secret (synthetic fixture)
_BASE = "http://127.0.0.1:6286/"


@pytest.fixture(autouse=True)
def _runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """A bound sidecar with a token-carrying app, like a real daemon leader."""
    monkeypatch.delenv(_ENV, raising=False)
    monkeypatch.setattr(_http_state, "_RUNTIME_HOST", "127.0.0.1", raising=False)
    monkeypatch.setattr(_http_state, "_RUNTIME_PORT", 6286, raising=False)
    monkeypatch.setattr(_http_state, "_RUNTIME_ERROR", None, raising=False)
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    from octowright import defaults

    monkeypatch.setattr(defaults, "RECORDINGS_DIR", recordings)

    fake_pool = SimpleNamespace(active_count=lambda: 0, iter_sessions=lambda: [], list_sessions=lambda: [])
    fake_spool = SimpleNamespace(list_live=lambda: [])
    for module in (_state, meta):
        monkeypatch.setattr(module, "pool", fake_pool)
        monkeypatch.setattr(module, "scenario_pool", fake_spool)

    _http_app.build_app(mcp_token=_TOKEN)  # publishes the pairing store


def _call() -> dict[str, Any]:
    return octowright_dashboard_url()


def test_returns_a_pair_url_by_default() -> None:
    result = _call()
    assert result["pairing_required"] is True
    assert "/pair#" in result["url"]
    assert result["plain_url"] == _BASE


def test_the_minted_code_is_actually_redeemable() -> None:
    """A well-formed URL carrying a dead code would still be a broken link."""
    code = urlsplit(_call()["url"]).fragment
    assert code
    store = _http_state.dashboard_pairing_store()
    grant = store.redeem_code(code)
    assert grant is not None
    assert store.bearer_ok(grant.bearer)


def test_the_code_is_single_use() -> None:
    code = urlsplit(_call()["url"]).fragment
    store = _http_state.dashboard_pairing_store()
    assert store.redeem_code(code) is not None
    assert store.redeem_code(code) is None


def test_each_call_mints_a_distinct_code() -> None:
    first = urlsplit(_call()["url"]).fragment
    second = urlsplit(_call()["url"]).fragment
    assert first != second


def test_the_window_is_human_paced_not_the_cli_window() -> None:
    """A code that expires before the user reads the message is useless."""
    assert _call()["pairing_expires_in"] == int(MCP_PAIR_CODE_TTL_SECONDS)
    assert MCP_PAIR_CODE_TTL_SECONDS > PAIR_CODE_TTL_SECONDS


def test_the_code_rides_the_fragment_only() -> None:
    """A fragment is never sent to the server during navigation."""
    parts = urlsplit(_call()["url"])
    assert parts.query == ""
    assert parts.path == "/pair"
    assert parts.fragment


def test_opting_out_returns_the_plain_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "off")
    result = _call()
    assert result["pairing_required"] is False
    assert result["url"] == _BASE
    assert "/pair#" not in result["url"]


def test_tokenless_leader_returns_the_plain_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """An inline --no-singleton leader cannot pair, and its dashboard is open."""
    _http_app.build_app(mcp_token="")
    result = _call()
    assert result["pairing_required"] is False
    assert result["url"] == _BASE


def test_no_pairing_store_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Before any app is built the tool must still answer."""
    monkeypatch.setattr(_http_state, "_DASHBOARD_PAIRING", None, raising=False)
    result = _call()
    assert result["pairing_required"] is False
    assert result["url"] == _BASE


def test_sidecar_not_running_reports_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_http_state, "_RUNTIME_HOST", None, raising=False)
    monkeypatch.setattr(_http_state, "_RUNTIME_PORT", None, raising=False)
    result = _call()
    assert result["running"] is False
    assert "error" in result
    assert result["url"] is None


def test_a_mint_failure_degrades_to_a_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken store must not take the whole tool down."""

    class _Boom:
        token_configured = True

        def mint_code(self, **_kw: Any) -> str:
            raise RuntimeError("store is wedged")

    monkeypatch.setattr(_http_state, "_DASHBOARD_PAIRING", _Boom(), raising=False)
    result = _call()
    assert result["url"] == _BASE
    assert "octowright dashboard" in result["pairing_hint"]


def test_repeated_calls_keep_the_latest_link_working() -> None:
    """The tool mints per call, and the code store is a bounded LRU.

    ``MAX_PAIR_CODES`` (32) pending codes are kept, oldest evicted first, so a
    burst of calls costs earlier *unredeemed* codes -- including one a user
    minted with `octowright dashboard` and had not clicked yet. That is the
    accepted bound; what must always hold is that the link the user was just
    handed is the one that works.
    """
    from octowright.http.pairing import MAX_PAIR_CODES

    store = _http_state.dashboard_pairing_store()
    codes = [urlsplit(_call()["url"]).fragment for _ in range(MAX_PAIR_CODES + 4)]
    assert store.redeem_code(codes[-1]) is not None
    # The oldest ones fell out of the bounded store, as designed.
    assert store.redeem_code(codes[0]) is None


def test_status_and_tool_share_one_pairing_answer() -> None:
    """octowright_status also hands out a dashboard URL.

    It reports the *plain* address (status is polled often, and minting there
    would churn the bounded code store and could evict a code the user was
    handed), so it carries `dashboard_pairing_required` to tell the agent the
    address needs pairing and that `octowright_dashboard_url` mints one.

    Both answers come from `_dashboard_pairing_required`, exercised here
    directly: building a full `octowright_status()` needs a broad live-pool
    surface that is not what this is about.
    """
    from octowright.server.meta import _dashboard_pairing_required

    assert _dashboard_pairing_required() is True
    assert _call()["pairing_required"] is True


def test_status_pairing_flag_follows_the_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.server.meta import _dashboard_pairing_required

    monkeypatch.setenv(_ENV, "off")
    assert _dashboard_pairing_required() is False
    assert _call()["pairing_required"] is False


def test_status_exposes_the_flag_alongside_the_url() -> None:
    """The wiring itself: the key must be in the status payload, not just the helper."""
    import inspect

    from octowright.server import meta

    source = inspect.getsource(meta.octowright_status)
    assert '"dashboard_pairing_required": _dashboard_pairing_required()' in source
    assert '"dashboard_url"' in source
