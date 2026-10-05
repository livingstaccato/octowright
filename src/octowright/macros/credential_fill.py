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

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from provide.telemetry import get_logger

from octowright.credential_input import CredentialInputStopped
from octowright.credential_sinks import (
    CREDENTIAL_FILL_MARKER,
    credential_args_in,
    credential_fill_mode,
    credential_fill_refusal,
    credential_input_stopped,
    offsite_credential_origin,
    page_code_refusal,
    refuse_page_code,
)
from octowright.macros.privacy import PLACEHOLDER_RE
from octowright.macros.substitution import is_credential_arg, own_site_origins
from octowright.session.fill_origin import fill_origin_check

if TYPE_CHECKING:
    from octowright.session._protocols import SessionLike

log = get_logger(__name__)


@dataclass
class FillAudit:
    """Which top-level step is running, and the off-site fills warn mode let through.

    ``credential_args`` names the credential-tier args the run expands
    anywhere; while it is non-empty, every step that runs page code is
    refused (``credential_sinks.page_code_refusal``), a called macro's included.
    """

    step: int | None = None
    offsite: list[dict[str, Any]] = field(default_factory=list)
    credential_args: tuple[str, ...] = ()


_AUDIT: ContextVar[FillAudit | None] = ContextVar("octowright_credential_fill_audit", default=None)
#: The credential-tier args every step of the enclosing ``macro_run_sequence``
#: types (`sequence_credentials`). Each step is a run of its own, and a step's
#: page code can read back what an earlier step typed, or install the listener
#: a later step types into.
_SEQUENCE: ContextVar[frozenset[str]] = ContextVar("octowright_sequence_credentials", default=frozenset())


@contextmanager
def sequence_credentials(names: frozenset[str]) -> Iterator[None]:
    """Hold every run inside to the page-code refusal of *names*, the sequence's credential args."""
    token = _SEQUENCE.set(names)
    try:
        yield
    finally:
        _SEQUENCE.reset(token)


def written_credential_args(written: Any, credential_args: frozenset[str] = frozenset()) -> list[str]:
    """The credential-tier args *written* expands anywhere; see `credential_sinks.credential_args_in`."""
    return credential_args_in(
        written, is_credential=is_credential_arg, placeholder=PLACEHOLDER_RE, credential_args=credential_args
    )


def credential_run_args(
    written: list[dict[str, Any]], actions: list[dict[str, Any]], credential_args: frozenset[str] = frozenset()
) -> tuple[str, ...]:
    """The credential-tier args a run expands, refusing its page code up front.

    Judged on the macro as *written*, plus every other step's when the run is
    a ``macro_run_sequence`` step (`sequence_credentials`); the expanded
    *actions* are checked for page code at every depth before any step runs,
    so a refusal leaves nothing half done. A called macro's page code is
    refused as it is dispatched (`credential_fill_guard`).
    """
    names = sorted({*written_credential_args(written, credential_args), *_SEQUENCE.get()})
    refuse_page_code(actions, names)
    return tuple(names)


def begin_fill_audit(credential_args: tuple[str, ...] = ()) -> tuple[FillAudit, Token[FillAudit | None]]:
    audit = FillAudit(credential_args=credential_args)
    return audit, _AUDIT.set(audit)


def end_fill_audit(token: Token[FillAudit | None]) -> None:
    _AUDIT.reset(token)


def offsite_fields(audit: FillAudit) -> dict[str, Any]:
    """What warn mode let through, for a failure payload: a failed run still typed it off-site."""
    return {"credential_fill_offsite": list(audit.offsite)} if audit.offsite else {}


@asynccontextmanager
async def credential_fill_guard(session: SessionLike, action: dict[str, Any]) -> AsyncIterator[None]:
    """Refuse (or, in warn mode, record) a credential typed onto a foreign origin.

    Page code in a run that carries a credential is refused here too, so a
    called macro's ``evaluate`` is judged by the run it is part of.

    Checked twice, by one check. Before dispatch, on the URL read through the
    session's gate from the frame the fill would land in -- not from
    ``session.url``, which is the last URL an octowright navigate wrote and
    misses a redirect or a script-driven navigation -- so a refusal comes
    before the step does anything. Then, bound for the step's dispatch
    (``session.fill_origin``), on the document that actually receives the
    value, as it receives it (``octowright.credential_input``): a navigation
    during the fill's wait or partway through a type, or a selector that
    enters a frame, is what moved it. A type the page moved partway through
    on its own origin is stopped there too, and reported naming this step.
    """
    audit = _AUDIT.get()
    if audit is not None and (refusal := page_code_refusal(action, audit.credential_args)):
        raise refusal
    check = _OriginCheck(session, action) if action.get(CREDENTIAL_FILL_MARKER) else None
    if check is not None:
        check(await session.target_url())
    with fill_origin_check(check):
        try:
            yield
        except CredentialInputStopped as exc:
            raise credential_input_stopped(action, str(exc), started=exc.started) from exc


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
