# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Origin-scoped dashboard pairing credentials and access decisions.

Pairing is on by default. A lockfile-authenticated CLI (or, for an inline
``--no-singleton`` leader, the process itself) mints a one-time code, the
browser redeems it for a short-lived bearer, and the SPA keeps that bearer in
origin-scoped ``sessionStorage``. Raw codes and bearer values are never stored
server-side: one Starlette app owns one bounded, digest-only state machine.

The same-user lockfile boundary remains explicit. A process that can read the
leader token can mint its own code; pairing protects against other local users
and sandboxed loopback processes, not the daemon owner's processes.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import re
import secrets
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from provide.telemetry import get_logger
from starlette.requests import HTTPConnection

log = get_logger(__name__)

PAIRING_REQUIRE_ENV = "OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING"
_DISABLED_TOKENS = frozenset({"0", "off", "false", "no", "never", "none", "disabled"})

PAIR_CODE_TTL_SECONDS = 60.0
# A human reading an agent's message needs longer than an operator pasting
# from their own terminal. Still single-use and loopback-only.
MCP_PAIR_CODE_TTL_SECONDS = 600.0
# The IDLE window: a bearer dies this long after it was last used, not this
# long after it was minted. An open dashboard holds an SSE stream that
# revalidates its lease on every heartbeat (`routes/events.py`), so "the tab
# is open" already reaches the store roughly every 15 seconds and slides the
# deadline for free -- no renewal endpoint, no client bookkeeping. Before
# this was a sliding window it was an absolute one, and a dashboard someone
# had been watching all day died mid-use at the 8-hour mark with the terrible
# unpaired page as the only explanation.
DASHBOARD_SESSION_TTL_SECONDS = 8 * 60 * 60.0
# The HARD ceiling a slide can never push past. Sliding alone would let a
# bearer that keeps being poked live forever, which would make this the one
# credential in the tree with no bounded life. A week is far longer than any
# real dashboard sitting and short enough to stay a bound.
DASHBOARD_SESSION_MAX_LIFETIME_SECONDS = 7 * 24 * 60 * 60.0
MAX_PAIR_CODES = 32
MAX_DASHBOARD_SESSIONS = 32
DASHBOARD_STATE_ATTR = "dashboard_pairing"

CAPABILITY_TOKEN_HEADER = "x-octowright-token"  # nosec B105  # header name
DASHBOARD_WS_PROTOCOL = "octowright.dashboard"
DASHBOARD_WS_BEARER_PREFIX = f"{DASHBOARD_WS_PROTOCOL}.bearer."
DASHBOARD_STREAM_LEASE_ATTR = "dashboard_stream_lease"
DASHBOARD_STREAM_AUTH_CHECK_SECONDS = 1.0
DASHBOARD_AUTH_EXPIRED_REASON = "dashboard pairing expired"
_BASE64URL_TOKEN = re.compile(r"^[A-Za-z0-9_-]+$")


def pairing_required() -> bool:
    """Return whether the browser dashboard credential gate is on.

    **ON by default.** Loopback binding plus the Host/Origin guards stop a
    remote attacker and a malicious web page, but they are not authentication:
    any other local process that can open a socket to the port could otherwise
    enumerate live sessions, read recorded JSONL (typed input, URLs, console),
    fetch video, subscribe to the live screencast, and drive the browser. That
    made the on-by-default 0600 recording permission and 0700 profile
    permissions misleading -- the daemon handed the same bytes out over HTTP.

    Set ``OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING`` to a falsey token
    (``0``/``off``/``false``/``no``/``never``/``none``/``disabled``) for a
    single-user host that wants the type-the-URL flow back.

    Note this is the *policy*; the anchor it pairs against is always present
    on an app ``build_app`` made -- see ``pairing_anchor_available``.
    """
    return os.environ.get(PAIRING_REQUIRE_ENV, "on").strip().lower() not in _DISABLED_TOKENS


@dataclass(frozen=True)
class DashboardBearerGrant:
    """A bearer returned once to the browser; its repr deliberately redacts it."""

    bearer: str = field(repr=False)
    expires_at: int


@dataclass(frozen=True)
class _SessionWindow:
    """One bearer's sliding idle deadline and its immovable hard deadline.

    Both are monotonic-clock values. ``deadline`` is the one that decides
    validity, so a slide can extend the idle half without ever reaching past
    the ceiling set when the bearer was issued.
    """

    idle_deadline: float
    hard_deadline: float

    @property
    def deadline(self) -> float:
        return min(self.idle_deadline, self.hard_deadline)


_ExpiryT = TypeVar("_ExpiryT", float, _SessionWindow)


class DashboardPairingState:
    """One app's bounded one-time-code and dashboard-session stores."""

    def __init__(
        self,
        *,
        expected_token: str,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        code_ttl: float = PAIR_CODE_TTL_SECONDS,
        session_ttl: float = DASHBOARD_SESSION_TTL_SECONDS,
        session_max_lifetime: float = DASHBOARD_SESSION_MAX_LIFETIME_SECONDS,
        max_codes: int = MAX_PAIR_CODES,
        max_sessions: int = MAX_DASHBOARD_SESSIONS,
        http_mint: bool = True,
    ) -> None:
        # False for an inline leader's random in-memory anchor: nothing outside
        # the process knows it, so /api/pair/mint says so instead of a bare 403.
        self.http_mint = http_mint
        self._monotonic_clock = monotonic_clock
        self._wall_clock = wall_clock
        self._code_ttl = code_ttl
        self._session_ttl = session_ttl
        self._session_max_lifetime = session_max_lifetime
        self._max_codes = max(1, max_codes)
        self._max_sessions = max(1, max_sessions)
        self._codes: OrderedDict[bytes, float] = OrderedDict()
        self._sessions: OrderedDict[bytes, _SessionWindow] = OrderedDict()
        self._expected_token_digest = self._digest(expected_token) if expected_token else None

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(codes={len(self._codes)}, "
            f"sessions={len(self._sessions)}, token_configured={self._expected_token_digest is not None})"
        )

    @staticmethod
    def _digest(value: str) -> bytes:
        return hashlib.sha256(value.encode("utf-8")).digest()

    @staticmethod
    def _constant_time_key_match(store: OrderedDict[bytes, _ExpiryT], candidate: bytes) -> bytes | None:
        return next((known for known in store if hmac.compare_digest(known, candidate)), None)

    @property
    def token_configured(self) -> bool:
        return self._expected_token_digest is not None

    def capability_token_ok(self, candidate: str | None) -> bool:
        if not candidate or self._expected_token_digest is None:
            return False
        return hmac.compare_digest(self._digest(candidate), self._expected_token_digest)

    def mint_code(self, *, ttl: float | None = None) -> str:
        """Mint a single-use pairing code.

        *ttl* overrides the default window. The CLI keeps the short default
        because the operator is already at a terminal and pastes immediately;
        an agent surfacing the link through MCP is handing it to a human who
        may not look for a while, and a code that expires before it is clicked
        is worse than useless -- it reads as a broken dashboard. The code stays
        single-use and loopback-only either way.
        """
        self._prune_expired()
        while True:
            code = secrets.token_urlsafe(24)
            digest = self._digest(code)
            if digest not in self._codes:
                break
        self._codes[digest] = self._monotonic_clock() + (self._code_ttl if ttl is None else ttl)
        self._trim(self._codes, self._max_codes)
        return code

    def redeem_code(self, code: str) -> DashboardBearerGrant | None:
        self._prune_expired()
        if not code:
            return None
        match = self._constant_time_key_match(self._codes, self._digest(code))
        if match is None:
            return None
        del self._codes[match]

        while True:
            bearer = secrets.token_urlsafe(32)
            digest = self._digest(bearer)
            if digest not in self._sessions:
                break
        now = self._monotonic_clock()
        self._sessions[digest] = _SessionWindow(
            idle_deadline=now + self._session_ttl,
            hard_deadline=now + self._session_max_lifetime,
        )
        self._trim(self._sessions, self._max_sessions)
        return DashboardBearerGrant(
            bearer=bearer,
            expires_at=math.ceil(self._wall_clock() + self._session_ttl),
        )

    def bearer_ok(self, bearer: str) -> bool:
        return self._validated_bearer_digest(bearer) is not None

    def _validated_bearer_digest(self, bearer: str) -> bytes | None:
        self._prune_expired()
        if not bearer:
            return None
        match = self._constant_time_key_match(self._sessions, self._digest(bearer))
        if match is None:
            return None
        self._touch(match)
        return match

    def _bearer_digest_ok(self, digest: bytes) -> bool:
        self._prune_expired()
        match = self._constant_time_key_match(self._sessions, digest)
        if match is None:
            return False
        self._touch(match)
        return True

    def _touch(self, digest: bytes) -> None:
        """Slide a live bearer's idle deadline forward and refresh its LRU spot.

        Validating and sliding are deliberately the same operation: a check
        that succeeds is proof the credential is in use, and a check that fails
        never reaches here, so an expired bearer cannot be revived by being
        asked about. Re-assigning an existing key leaves its position alone, so
        ``move_to_end`` is still what keeps ``_trim`` evicting the least
        recently used session rather than the oldest one.
        """
        window = self._sessions[digest]
        self._sessions[digest] = _SessionWindow(
            idle_deadline=self._monotonic_clock() + self._session_ttl,
            hard_deadline=window.hard_deadline,
        )
        self._sessions.move_to_end(digest)

    @staticmethod
    def _trim(store: OrderedDict[bytes, _ExpiryT], limit: int) -> None:
        while len(store) > limit:
            store.popitem(last=False)

    def _prune_expired(self) -> None:
        now = self._monotonic_clock()
        for code_digest, code_expiry in list(self._codes.items()):
            if code_expiry <= now:
                del self._codes[code_digest]
        for session_digest, window in list(self._sessions.items()):
            if window.deadline <= now:
                del self._sessions[session_digest]


@dataclass(frozen=True)
class DashboardStreamLease:
    """Digest-only authorization captured when a dashboard stream is admitted."""

    _pairing_state: DashboardPairingState | None = field(default=None, repr=False)
    _bearer_digest: bytes | None = field(default=None, repr=False)
    _bypass: bool = field(default=False, repr=False)

    @classmethod
    def bypass(cls) -> DashboardStreamLease:
        return cls(_bypass=True)

    @classmethod
    def for_bearer(cls, pairing_state: DashboardPairingState, bearer_digest: bytes) -> DashboardStreamLease:
        return cls(_pairing_state=pairing_state, _bearer_digest=bearer_digest)

    @property
    def revalidatable(self) -> bool:
        return not self._bypass

    def valid(self) -> bool:
        if self._bypass:
            return True
        if self._pairing_state is None or self._bearer_digest is None:
            return False
        return self._pairing_state._bearer_digest_ok(self._bearer_digest)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(bypass={self._bypass}, digest_configured={self._bearer_digest is not None})"


def _attach_dashboard_stream_lease(connection: HTTPConnection, lease: DashboardStreamLease) -> None:
    try:
        setattr(connection.state, DASHBOARD_STREAM_LEASE_ATTR, lease)
    except (AttributeError, KeyError):
        # Small route-unit-test fakes may implement only the headers/app surface.
        # Real Starlette HTTPConnection instances always expose mutable state.
        return


def dashboard_stream_lease(connection: HTTPConnection) -> DashboardStreamLease | None:
    """Return the lease attached by admission, if this is a real connection."""
    try:
        lease = getattr(connection.state, DASHBOARD_STREAM_LEASE_ATTR, None)
    except (AttributeError, KeyError):
        return None
    return lease if isinstance(lease, DashboardStreamLease) else None


def dashboard_stream_lease_valid(lease: DashboardStreamLease | None) -> bool:
    """Revalidate a captured lease; direct unguarded test calls bypass only when pairing is off."""
    if lease is None:
        return not pairing_required()
    return lease.valid()


def dashboard_pairing_state(connection: HTTPConnection) -> DashboardPairingState | None:
    """Return the state attached to this connection's Starlette app."""
    try:
        return getattr(connection.app.state, DASHBOARD_STATE_ATTR, None)
    except (AttributeError, KeyError):
        return None


def authorization_bearer(connection: HTTPConnection) -> str | None:
    """Parse one unambiguous ``Authorization: Bearer <opaque>`` value."""
    values = connection.headers.getlist("authorization")
    if len(values) != 1:
        return None
    scheme, separator, bearer = values[0].partition(" ")
    if scheme.casefold() != "bearer" or separator != " " or not bearer:
        return None
    if any(char.isspace() for char in bearer):
        return None
    return bearer


def pairing_anchor_available(state: DashboardPairingState | None) -> bool:
    """Whether there is a credential to pair against on this app.

    ``build_app`` always attaches one: the leader's capability token, or -- for
    an inline ``--no-singleton`` leader, which has no lockfile to publish a
    token in -- a random in-memory anchor. So this is False only for an
    embedder mounting these routes on a Starlette app of its own, and the
    access checks treat that as a refusal, not as "cannot enforce".
    """
    return state is not None and state.token_configured


def dashboard_access_ok(connection: HTTPConnection) -> bool:
    """Authorize a guarded HTTP request using bearer or capability token."""
    if not pairing_required():
        _attach_dashboard_stream_lease(connection, DashboardStreamLease.bypass())
        return True
    state = dashboard_pairing_state(connection)
    if not pairing_anchor_available(state):
        # Fail closed. This used to wave the request through as "nothing to
        # pair against", which made an inline --no-singleton leader (then
        # tokenless) serve recordings and session control to any local user
        # under the default policy. Inline now gets a random anchor and can
        # mint codes in-process (octowright_dashboard_url, and the URL
        # `serve` prints at startup), so a missing anchor is only an
        # embedder that never attached state -- which must opt out
        # explicitly rather than be authorized by omission.
        return False
    assert state is not None  # narrowed by pairing_anchor_available  # nosec B101
    if state.capability_token_ok(connection.headers.get(CAPABILITY_TOKEN_HEADER)):
        _attach_dashboard_stream_lease(connection, DashboardStreamLease.bypass())
        return True
    bearer = authorization_bearer(connection)
    if bearer is None:
        return False
    digest = state._validated_bearer_digest(bearer)
    if digest is None:
        return False
    _attach_dashboard_stream_lease(connection, DashboardStreamLease.for_bearer(state, digest))
    return True


def _websocket_protocols(connection: HTTPConnection) -> list[str]:
    protocols: list[str] = []
    headers = getattr(connection, "headers", None)
    if headers is None:
        return protocols
    for value in headers.getlist("sec-websocket-protocol"):
        protocols.extend(part.strip() for part in value.split(",") if part.strip())
    return protocols


def _websocket_bearer(connection: HTTPConnection) -> str | None:
    protocols = _websocket_protocols(connection)
    if DASHBOARD_WS_PROTOCOL not in protocols:
        return None
    credentials = [value for value in protocols if value.startswith(DASHBOARD_WS_BEARER_PREFIX)]
    if len(credentials) != 1:
        return None
    bearer = credentials[0][len(DASHBOARD_WS_BEARER_PREFIX) :]
    if not bearer or _BASE64URL_TOKEN.fullmatch(bearer) is None:
        return None
    return bearer


def dashboard_websocket_auth(connection: HTTPConnection) -> tuple[bool, str | None]:
    """Authorize a dashboard WebSocket and choose a non-secret protocol.

    Browser clients propose both the stable public protocol and a private
    credential protocol. The secret-bearing value is validated but never
    selected or echoed in the handshake response.
    """
    protocols = _websocket_protocols(connection)
    public_protocol = DASHBOARD_WS_PROTOCOL if DASHBOARD_WS_PROTOCOL in protocols else None
    if not pairing_required():
        _attach_dashboard_stream_lease(connection, DashboardStreamLease.bypass())
        return True, public_protocol
    state = dashboard_pairing_state(connection)
    if not pairing_anchor_available(state):
        # Fail closed -- same reasoning as dashboard_access_ok.
        return False, public_protocol
    assert state is not None  # narrowed by pairing_anchor_available  # nosec B101
    if state.capability_token_ok(connection.headers.get(CAPABILITY_TOKEN_HEADER)):
        _attach_dashboard_stream_lease(connection, DashboardStreamLease.bypass())
        return True, public_protocol
    bearer = _websocket_bearer(connection)
    if bearer is None:
        return False, public_protocol
    digest = state._validated_bearer_digest(bearer)
    if digest is None:
        return False, public_protocol
    _attach_dashboard_stream_lease(connection, DashboardStreamLease.for_bearer(state, digest))
    return True, DASHBOARD_WS_PROTOCOL
