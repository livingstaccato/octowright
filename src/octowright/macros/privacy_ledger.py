# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The scrub sets: a run's, the session's, and the recorder wrapper that reads the session's.

Split out of ``macros.privacy``, which re-exports every name here, so the
classifier and the ledgers each fit a module.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from octowright.macros.scrub_engine import _scrub_tree, filtered_text_scrubber

if TYPE_CHECKING:
    from octowright.macros.privacy import ScrubAdmission

#: The session attribute that owns its scrub set, in the private namespace
#: composition roots already use (``_octowright_before_macro_action``).
SESSION_PRIVACY_LEDGER_ATTR = "_octowright_privacy_ledger"


class PrivacyLedger:
    """An append-only, de-duplicated set of values to scrub, longest first.

    Longest first because a replacing scrub must not let a shorter value consume
    the characters a longer one needed. De-duplicated because the scrub cost is
    per value per write, so it has to track distinct credentials, not runs.
    """

    def __init__(self, values: Iterable[str] = ()) -> None:
        self._members: set[str] = set()
        self._values: tuple[str, ...] = ()
        self._bounded: set[str] = set()
        self._anywhere: set[str] = set()
        self._word_bounded: frozenset[str] = frozenset()
        # Bumped by every add that changes what a scrub does; `_text_scrubber`
        # rebuilds its cache when this moves.
        self._version = 0
        self._cached: tuple[int, Callable[[str], str]] | None = None
        self.add(values)

    def add(self, values: Iterable[str], *, word_bounded: bool = False) -> None:
        """Append *values*; ``word_bounded`` ones are scrubbed only as whole identifiers.

        A value admitted both ways is scrubbed anywhere: the stronger claim wins,
        whichever order the two admissions arrived in.
        """
        admitted = {value for value in values if isinstance(value, str) and value}
        claim = self._bounded if word_bounded else self._anywhere
        if admitted <= claim:
            return
        claim.update(admitted)
        self._members |= admitted
        self._refresh()

    def _scoped(self) -> frozenset[str]:
        """Values scrubbed anywhere beside the ledger's own; see `SessionPrivacyLedger`."""
        return frozenset()

    def _refresh(self) -> None:
        scoped = self._scoped()
        self._values = tuple(sorted(self._members | scoped, key=lambda value: (-len(value), value)))
        self._word_bounded = frozenset(self._bounded - self._anywhere - scoped)
        self._version += 1

    @property
    def values(self) -> tuple[str, ...]:
        return self._values

    @property
    def word_bounded(self) -> frozenset[str]:
        """Values scrubbed only where not embedded in a longer identifier.

        The passwords `admit_redacted_input` adds. They are whatever someone
        typed into a password field -- very often a test value such as
        ``admin`` -- and replacing one anywhere rewrote ``administrator`` and
        ``#admin-menu`` in every later row, so a macro saved from the recording
        replayed broken selectors. Bounding keeps every echo that stands as its
        own token (``pw=admin``, ``"admin"``, a URL parameter) scrubbed; what
        it gives up is an echo glued to other identifier characters.
        """
        return self._word_bounded

    def scrub(self, value: Any) -> Any:
        """*value* scrubbed of this ledger's values, or *value* itself when it is empty.

        Byte-identical to `scrub_sensitive_values` over the same state, which
        the live buffers (`input_redaction.live_scrubbed`) call on every
        console message, network row and socket URL: see `_text_scrubber`.
        """
        return _scrub_tree(value, self._text_scrubber()) if self._values else value

    def _text_scrubber(self) -> Callable[[str], str]:
        """The per-string scrub for the current state, built once per state.

        Almost every string a page produces holds no ledger value; see
        `scrub_engine.filtered_text_scrubber`.
        """
        cached = self._cached
        if cached is not None and cached[0] == self._version:
            return cached[1]
        scrub_text = filtered_text_scrubber(self._values, self._word_bounded)
        self._cached = (self._version, scrub_text)
        return scrub_text


class SessionPrivacyLedger(PrivacyLedger):
    """A session's scrub set: appended to by every run and nested call, never cleared.

    Deliberately not restored at the run boundary. Recorder rows are driven by
    page events that outlive the run, and a credential typed in one sequence step
    keeps appearing in the next step's page-derived rows, so restoring would
    reopen cleartext rather than prevent stacking. Stacking is prevented by
    identity instead: exactly one ``SensitiveRecorder`` reads this ledger.

    Run scopes are the one exception, and they never hold a credential: an
    identity/contextual macro value (admitted only under
    ``OCTOWRIGHT_MACRO_BLIND_SCRUB_POLICY=all``) is scrubbed while the run that
    supplied it is open and dropped when it closes (#247), unless
    ``OCTOWRIGHT_MACRO_SCRUB_RUN_SCOPED`` is off. Such a value is not a secret,
    and scrubbing it for the session's lifetime rewrote every later row that
    happened to contain it. While open, a scope's values are part of `values`,
    so every reader -- the recorder, the durable text scrub, the screenshot
    boundary -- treats them as the ledger's own.
    """

    def __init__(self, values: Iterable[str] = ()) -> None:
        self._scopes: dict[object, frozenset[str]] = {}
        super().__init__(values)

    def _scoped(self) -> frozenset[str]:
        return frozenset().union(*self._scopes.values())

    def open_run_scope(self) -> object:
        """A token for a new, empty run scope."""
        token = object()
        self._scopes[token] = frozenset()
        return token

    def add_run_scoped(self, token: object, values: Iterable[str]) -> None:
        """Scrub *values* (matched anywhere) until *token*'s scope closes.

        Adding to a closed scope adds to the session ledger instead: a value
        nobody will remove must at least be scrubbed.
        """
        admitted = frozenset(value for value in values if isinstance(value, str) and value)
        if token not in self._scopes:
            self.add(admitted)
            return
        if admitted <= self._scopes[token]:
            return
        self._scopes[token] = self._scopes[token] | admitted
        self._refresh()

    def close_run_scope(self, token: object) -> None:
        if self._scopes.pop(token, None) is not None:
            self._refresh()


class SensitiveRecorder:
    """Scrub macro values at the recorder boundary before any durable write.

    Holds a reference to the session ledger, not a copy of its values, so a
    value appended by a later run or a nested call is scrubbed from the very next
    write without installing anything.
    """

    def __init__(self, recorder: Any, ledger: PrivacyLedger) -> None:
        self._recorder = recorder
        self.ledger = ledger

    def _scrubbed(self, fields: dict[str, Any]) -> dict[str, Any]:
        # Nothing to scrub is the common case for a session that never admitted a
        # macro value, and scrubbing an empty set still copies every field --
        # ``PrivacyLedger.scrub`` returns *fields* untouched then.
        scrubbed: dict[str, Any] = self.ledger.scrub(fields)
        return scrubbed

    def record(self, action: str, **fields: Any) -> None:
        self._recorder.record(action, **self._scrubbed(fields))

    def record_control(self, action: str, **fields: Any) -> None:
        self._recorder.record_control(action, **self._scrubbed(fields))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._recorder, name)


def session_privacy_ledger(session: Any) -> SessionPrivacyLedger:
    """The session's ledger, created on first use."""
    ledger = getattr(session, SESSION_PRIVACY_LEDGER_ATTR, None)
    # isinstance rather than ``is None``: a mock session answers every getattr.
    if not isinstance(ledger, SessionPrivacyLedger):
        ledger = SessionPrivacyLedger()
        setattr(session, SESSION_PRIVACY_LEDGER_ATTR, ledger)
    return ledger


def with_session_values(session: Any, values: Iterable[str]) -> tuple[str, ...]:
    """*values* and every value the session ledger holds, as one flat tuple, longest first.

    For a caller that must redact everything the session holds, not only its
    own run's values: the ledger also carries what was admitted outside any
    macro -- a password the input classification hid from a direct
    ``browser_fill`` -- and the page may still render it. Reads the ledger
    without creating one.

    Flat on purpose: the ledger's word bounds (`PrivacyLedger.word_bounded`)
    belong to the recording, whose own scrub reads the session ledger
    directly. Every caller here matches each value anywhere -- a failure
    payload's page-derived text goes back to the MCP client, and a screenshot
    is pixels -- and a tuple cannot carry the bounds, so none can be applied
    by mistake.
    """
    merged = PrivacyLedger(values)
    ledger = getattr(session, SESSION_PRIVACY_LEDGER_ATTR, None)
    if isinstance(ledger, SessionPrivacyLedger):
        merged.add(ledger.values)
    return merged.values


def install_sensitive_recorder(session: Any, sensitive_values: Iterable[str] = ()) -> SessionPrivacyLedger:
    """Add values to the session's scrub set and make sure exactly one wrapper reads it.

    Idempotent and never uninstalled: a session that is already wrapped gets its
    ledger appended to, never a second wrapper. It wraps even when there is
    nothing to scrub yet, because a nested call or a later run may append a
    credential, and that append has to reach a ledger something reads.
    """
    ledger = session_privacy_ledger(session)
    ledger.add(sensitive_values)
    recorder = getattr(session, "recorder", None)
    if recorder is not None and not isinstance(recorder, SensitiveRecorder):
        session.recorder = SensitiveRecorder(recorder, ledger)
    # The markdown cache is the other durable write of page content; a page
    # that renders the password would otherwise put it on disk in cleartext.
    if session.durable_text_scrubber is None:
        session.durable_text_scrubber = DurableTextScrubber(ledger)
    return ledger


class DurableTextScrubber:
    """The session's ``durable_text_scrubber``: scrubs page text against its ledger.

    ``active`` lets a caller with work to do BEFORE scrubbing skip it: the
    websocket sidecar base64-decodes every binary frame to look for a value,
    and a session that once ran a macro keeps this installed with a ledger that
    may well be empty.
    """

    def __init__(self, ledger: PrivacyLedger) -> None:
        self.ledger = ledger

    @property
    def active(self) -> bool:
        return bool(self.ledger.values)

    def __call__(self, text: str) -> str:
        scrubbed: str = self.ledger.scrub(text)
        return scrubbed

    def scrub_value(self, value: Any) -> Any:
        """A structure (a console entry, a network row) scrubbed like text."""
        return self.ledger.scrub(value)


def admit_redacted_input(session: Any, value: str) -> None:
    """A value the recorder's input classification hid, now kept out of every other durable write.

    ``OCTOWRIGHT_REDACT_INPUTS`` replaces a password field's typed value in the
    ``fill``/``type`` row, but the page is free to echo it -- a
    ``console.log``, a request body, a websocket frame, the rendered page --
    and each of those rows used to persist it in cleartext. Appending to the
    session ledger (never replacing it) keeps it scrubbed across every later
    macro run as well. Admitted WORD-BOUNDED (`PrivacyLedger.word_bounded`),
    unlike a macro's classified values: only the field type says this is a
    secret, and a typed password is often an ordinary word.
    """
    install_sensitive_recorder(session).add([value], word_bounded=True)


class RunPrivacyLedger(PrivacyLedger):
    """What one macro run admitted, nested calls included, and the session scope it lives in.

    Its own values are what the run's failure payloads and screenshot boundary
    scrub. `admit` also routes each value to the session: credentials to the
    session-wide ledger, identity/contextual values to this run's scope, which
    `close` drops. Nothing touches the session before the first `admit`, so a
    policy refusal raised while computing the admission has no side effect.
    """

    def __init__(self, session: Any) -> None:
        super().__init__()
        self._session = session
        self._session_ledger: SessionPrivacyLedger | None = None
        self._scope: object | None = None
        self._exempt: list[dict[str, str]] = []

    def admit(self, macro: str, admission: ScrubAdmission) -> None:
        if self._session_ledger is None:
            self._session_ledger = install_sensitive_recorder(self._session)
            self._scope = self._session_ledger.open_run_scope()
        self.add(admission.values)
        self._session_ledger.add(admission.persistent)
        self._session_ledger.add_run_scoped(self._scope, admission.run_scoped)
        # A call repeated in a loop reports its exemption once.
        self._exempt.extend(
            row for row in (item.as_dict(macro) for item in admission.exempt) if row not in self._exempt
        )

    def close(self) -> None:
        if self._session_ledger is not None and self._scope is not None:
            self._session_ledger.close_run_scope(self._scope)
            self._scope = None

    @property
    def exempt_args(self) -> list[dict[str, str]]:
        """``scrub_exempt_args``: the arguments the floor or list left visible, by path, never by value."""
        return list(self._exempt)

    def exempt_fields(self) -> dict[str, list[dict[str, str]]]:
        """`exempt_args` for a failure payload: absent when nothing was exempt."""
        return {"scrub_exempt_args": self.exempt_args} if self._exempt else {}


@contextmanager
def run_privacy_ledger(session: Any) -> Iterator[RunPrivacyLedger]:
    """A run's ledger, its session scope closed however the run ends."""
    ledger = RunPrivacyLedger(session)
    try:
        yield ledger
    finally:
        ledger.close()


def admit_call_privacy(session: Any, run_ledger: PrivacyLedger, macro: str, admission: ScrubAdmission) -> None:
    """A nested ``macro_call``'s admission, scoped to the run it executes in.

    With no run to scope to -- a call dispatched outside `run_privacy_ledger`
    -- every admitted value joins the session ledger, the side that scrubs more.
    """
    if isinstance(run_ledger, RunPrivacyLedger):
        run_ledger.admit(macro, admission)
        return
    run_ledger.add(admission.values)
    install_sensitive_recorder(session, admission.values)
