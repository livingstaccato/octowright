# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Opt-in dashboard pairing: ticket/session store + the access decision.

The browser-facing dashboard surface is loopback-Host-gated only, so a
*different-user* or *sandboxed* loopback process can read live JSONL and drive
persona/scenario/macro writes. The capability token can't simply be embedded in
the served page — any loopback fetcher could scrape it exactly like the real
browser. Pairing routes the token to the HUMAN over a channel a hostile
process can't observe (the operator's tty):

1. ``octowright dashboard`` (same-user; reads the 0600 lockfile so it holds the
   capability token) POSTs ``/api/pair/mint`` with ``X-Octowright-Token`` and
   receives a single-use, short-TTL ticket.
2. The CLI prints ``http://127.0.0.1:PORT/pair#<ticket>`` — ticket in the URL
   *fragment*, so it is never sent on navigation, never logged, never in
   Referer. The human copies it into their browser.
3. The ``/pair`` page redeems the ticket for an HttpOnly, SameSite=Strict
   session cookie; every guarded route then accepts EITHER that cookie
   (browser) or the ``X-Octowright-Token`` header (follower/programmatic).

``OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING`` is **OFF by default** (back-compat:
the type-the-URL dashboard UX stays). Same-user processes are NOT defended —
they can read the lockfile and mint their own ticket; the lockfile remains the
same-user trust boundary, matching the /mcp token's honest limits.

All state is in-memory and per-leader: a restart invalidates every ticket and
session, which is the safe direction.
"""

from __future__ import annotations

import hmac
import os
import secrets
import time
from collections import OrderedDict
from collections.abc import Callable

from starlette.requests import HTTPConnection

PAIRING_REQUIRE_ENV = "OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING"
# Opt-IN knob (unlike the opt-out gates): only these tokens enable it.
_ENABLED_TOKENS = frozenset({"1", "on", "true", "yes"})

SESSION_COOKIE = "octowright_dash"
TICKET_TTL_SECONDS = 60.0
# Bounded LRU of live browser sessions; far above real use (a handful of tabs),
# far below memory concern. Oldest evicted first.
MAX_SESSIONS = 32
_TOKEN_HEADER = "x-octowright-token"  # nosec B105  # header NAME, not a credential


def pairing_required() -> bool:
    """Whether dashboard pairing is enforced. OFF by default; enable with
    ``OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING`` set to a truthy token."""
    return os.environ.get(PAIRING_REQUIRE_ENV, "").strip().lower() in _ENABLED_TOKENS


class PairingState:
    """In-memory single-use tickets + bounded LRU of session bearers.

    ``clock`` is injectable for TTL tests; everything else is deterministic.
    Lookups run constant-time compares over the (small, bounded) stores so a
    wrong ticket/bearer can't be probed by timing.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._tickets: OrderedDict[str, float] = OrderedDict()  # ticket -> expiry
        self._sessions: OrderedDict[str, float] = OrderedDict()  # bearer -> created
        self._expected_token = ""  # nosec B105  # empty sentinel, not a credential

    # -- capability token (the header alternative) ----------------------------

    def set_expected_token(self, token: str) -> None:
        self._expected_token = token

    @property
    def expected_token(self) -> str:
        return self._expected_token

    # -- tickets ---------------------------------------------------------------

    def mint_ticket(self) -> str:
        self._prune()
        ticket = secrets.token_urlsafe(16)
        self._tickets[ticket] = self._clock() + TICKET_TTL_SECONDS
        return ticket

    def redeem_ticket(self, ticket: str) -> str | None:
        """Consume ``ticket`` (single-use) and mint a session bearer, or None."""
        self._prune()
        match = next((known for known in self._tickets if hmac.compare_digest(known, ticket)), None)
        if match is None:
            return None
        del self._tickets[match]
        bearer = secrets.token_urlsafe(32)
        self._sessions[bearer] = self._clock()
        while len(self._sessions) > MAX_SESSIONS:
            self._sessions.popitem(last=False)
        return bearer

    def session_ok(self, bearer: str) -> bool:
        return any(hmac.compare_digest(known, bearer) for known in self._sessions)

    def reset(self) -> None:
        """Test hook: drop all tickets/sessions and the expected token."""
        self._tickets.clear()
        self._sessions.clear()
        self._expected_token = ""  # nosec B105  # empty sentinel, not a credential

    def _prune(self) -> None:
        now = self._clock()
        for ticket, expiry in list(self._tickets.items()):
            if expiry <= now:
                del self._tickets[ticket]


# Per-process singleton; build_app() stamps the leader's capability token on it.
PAIRING = PairingState()


def dashboard_access_ok(connection: HTTPConnection) -> bool:
    """The pairing access decision for a guarded HTTP request or WebSocket.

    True when pairing is disabled (default), when the connection carries a
    valid session cookie (paired browser), or when it presents the capability
    token header (follower / programmatic caller — unchanged behavior). Layered
    INSIDE the loopback/Host/cross-origin guards, never instead of them.
    """
    if not pairing_required():
        return True
    cookie = connection.cookies.get(SESSION_COOKIE)
    if cookie and PAIRING.session_ok(cookie):
        return True
    expected = PAIRING.expected_token
    header = connection.headers.get(_TOKEN_HEADER)
    return bool(expected and header is not None and hmac.compare_digest(header.encode(), expected.encode()))
