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


async def guard_credential_fill(session: SessionLike, action: dict[str, Any]) -> None:
    """Refuse (or, in warn mode, record) a credential typed onto a foreign origin.

    The URL is read through the session's gate, from the frame the fill will
    land in, immediately before dispatch -- not from ``session.url``, which is
    the last URL an octowright navigate wrote and misses a redirect or a
    script-driven navigation.
    """
    if not action.get(CREDENTIAL_FILL_MARKER):
        return
    shown = offsite_credential_origin(action, await session.target_url(), own_site_origins(session))
    if shown is None:
        return
    if credential_fill_mode() != "warn":
        raise credential_fill_refusal(action, shown)
    audit = _AUDIT.get()
    step = audit.step if audit is not None else None
    log.warning(
        "octowright.macro.credential_fill_offsite",
        instance_id=session.instance_id,
        action=action.get("action"),
        origin=shown,
        step=step,
    )
    if audit is not None:
        audit.offsite.append({"step": step, "action": action.get("action"), "origin": shown})
