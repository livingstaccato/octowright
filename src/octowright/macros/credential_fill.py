# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live replay's half of the credential-fill origin check.

The rules are ``octowright.credential_sinks``'s, shared with the exported CLI;
this module reads the page's origin through the session and keeps the
``warn``-mode record for the run result.

The record rides a context variable rather than a parameter threaded through
``_dispatch_one``, its nested-call and conditional recursion and every test
fake of them: it is written only in the uncommon warn mode, and a run's
dispatch all happens in the run's own task.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from provide.telemetry import get_logger

from octowright.credential_sinks import (
    CREDENTIAL_FILL_MARKER,
    credential_fill_mode,
    credential_fill_refusal,
    offsite_credential_origin,
)
from octowright.macros.substitution import own_site_origins
from octowright.session.fill_origin import fill_origin_check

if TYPE_CHECKING:
    from octowright.session._protocols import SessionLike

log = get_logger(__name__)


@dataclass
class FillAudit:
    """Which top-level step is running, and the off-site fills warn mode let through."""

    step: int | None = None
    offsite: list[dict[str, Any]] = field(default_factory=list)


_AUDIT: ContextVar[FillAudit | None] = ContextVar("octowright_credential_fill_audit", default=None)


def begin_fill_audit() -> tuple[FillAudit, Token[FillAudit | None]]:
    audit = FillAudit()
    return audit, _AUDIT.set(audit)


def end_fill_audit(token: Token[FillAudit | None]) -> None:
    _AUDIT.reset(token)


@asynccontextmanager
async def credential_fill_guard(session: SessionLike, action: dict[str, Any]) -> AsyncIterator[None]:
    """Refuse (or, in warn mode, record) a credential typed onto a foreign origin.

    Checked twice, by one check. Before dispatch, on the URL read through the
    session's gate from the frame the fill would land in -- not from
    ``session.url``, which is the last URL an octowright navigate wrote and
    misses a redirect or a script-driven navigation -- so a refusal comes
    before the step does anything. Then, bound for the step's dispatch
    (``session.fill_origin``), on the document that actually receives the
    value, as it receives it (``octowright.credential_input``): a navigation
    during the fill's wait or partway through a type, or a selector that
    enters a frame, is what moved it.
    """
    check = _OriginCheck(session, action) if action.get(CREDENTIAL_FILL_MARKER) else None
    if check is not None:
        check(await session.target_url())
    with fill_origin_check(check):
        yield


class _OriginCheck:
    """One step's origin rule; warn mode records each foreign origin once, however often it is read."""

    def __init__(self, session: SessionLike, action: dict[str, Any]) -> None:
        self.session = session
        self.action = action
        self.trusted = own_site_origins(session)
        self.recorded: set[str] = set()

    def __call__(self, url: str) -> None:
        shown = offsite_credential_origin(self.action, url, self.trusted)
        if shown is None:
            return
        if credential_fill_mode() != "warn":
            raise credential_fill_refusal(self.action, shown)
        if shown in self.recorded:
            return
        self.recorded.add(shown)
        audit = _AUDIT.get()
        step = audit.step if audit is not None else None
        log.warning(
            "octowright.macro.credential_fill_offsite",
            instance_id=self.session.instance_id,
            action=self.action.get("action"),
            origin=shown,
            step=step,
        )
        if audit is not None:
            audit.offsite.append({"step": step, "action": self.action.get("action"), "origin": shown})
