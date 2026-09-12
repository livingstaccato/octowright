# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""One session-scoped scrub set behind exactly one recorder wrapper.

#234: every macro run wrapped ``session.recorder`` again and nothing unwrapped
it, so an N-step sequence left N nested wrappers, each re-scrubbing every write.
#235: a nested ``macro_call``'s own arguments were never collected, so a
credential passed only to the nested call reached the recording unscrubbed.

The fix follows the macro parameter privacy design (r6-r8): the scrub set is
owned by the session and never uninstalled -- restoring it at the run boundary
would reopen cleartext for page-derived rows that outlive the run -- and
stacking is prevented by identity, not removal. Nested resolves append to the
ledger the one wrapper already holds.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.macros import execution, privacy

SECRET_A = "outer-credential-1"  # pragma: allowlist secret
SECRET_B = "nested-credential-2"  # pragma: allowlist secret


class _Recorder:
    def __init__(self) -> None:
        self.rows: list[tuple[str, dict[str, Any]]] = []

    def record(self, action: str, **fields: Any) -> None:
        self.rows.append((action, fields))

    def record_control(self, action: str, **fields: Any) -> None:
        self.rows.append((action, fields))


class _Session:
    def __init__(self) -> None:
        self.recorder: Any = _Recorder()


def test_repeated_installs_keep_exactly_one_wrapper() -> None:
    session = _Session()
    inner = session.recorder
    for secret in (SECRET_A, SECRET_B, SECRET_A):
        privacy.install_sensitive_recorder(session, (secret,))

    assert isinstance(session.recorder, privacy.SensitiveRecorder)
    assert session.recorder._recorder is inner, "a wrapper was installed around a wrapper"


def test_install_wraps_even_when_nothing_is_classified_yet() -> None:
    """A run whose own arguments classify nothing must still wrap: a nested
    credential appended later has to reach a ledger something reads."""
    session = _Session()
    inner = session.recorder
    privacy.install_sensitive_recorder(session, ())

    assert isinstance(session.recorder, privacy.SensitiveRecorder)
    session.recorder.record("note", text="plain")
    assert inner.rows == [("note", {"text": "plain"})]


def test_the_ledger_lives_on_the_session_deduplicated_and_longest_first() -> None:
    session = _Session()
    first = privacy.install_sensitive_recorder(session, (SECRET_A, SECRET_B))
    second = privacy.install_sensitive_recorder(session, (SECRET_A,))

    assert first is second
    assert first.values == (SECRET_B, SECRET_A)


def test_an_existing_wrapper_scrubs_values_appended_after_it_was_installed() -> None:
    """The wrapper holds a reference to the ledger, not a copy of its values."""
    session = _Session()
    inner = session.recorder
    privacy.install_sensitive_recorder(session, (SECRET_A,))
    wrapper = session.recorder
    privacy.install_sensitive_recorder(session, (SECRET_B,))

    wrapper.record("note", text=f"{SECRET_A} {SECRET_B}")
    (_action, fields) = inner.rows[0]
    assert SECRET_A not in fields["text"]
    assert SECRET_B not in fields["text"]


@pytest.mark.asyncio
async def test_nested_macro_call_arguments_join_the_session_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    """#235: a credential passed only to the nested call, under an outer run that
    classified nothing, is still scrubbed from the recording."""
    session = _Session()
    inner = session.recorder
    monkeypatch.setattr(execution, "load_macro", lambda _name: {"actions": []})
    monkeypatch.setattr(execution, "_push_status", AsyncMock())

    await execution._dispatch_one(
        session,
        {"action": "macro_call", "name": "child", "args": {"password": SECRET_B}},
        invocation_stack=["outer"],
    )

    assert isinstance(session.recorder, privacy.SensitiveRecorder)
    session.recorder.record("note", text=f"typed {SECRET_B}")
    assert SECRET_B not in inner.rows[0][1]["text"]


@pytest.mark.asyncio
async def test_a_nested_credential_is_scrubbed_from_the_failure_the_run_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """#235 at the run boundary: the outer run classifies nothing, the nested call
    carries the credential, and the child action fails with it in the message.

    The failure payload and the exception chain read what the RUN collected,
    nested calls included, not only the outer arguments."""
    session = MagicMock()
    session.instance_id = "instance-safe"
    session.kind = "chromium"
    session.diagnostic_bundle = AsyncMock(return_value={})
    macros = {
        "outer": {"actions": [{"action": "macro_call", "name": "child", "args": {"password": SECRET_B}}]},
        "child": {"actions": [{"action": "click", "selector": "#go"}]},
    }

    async def fail_dispatch(*_args: Any, **_kwargs: Any) -> tuple[int, int]:
        raise ValueError(f"click failed after typing {SECRET_B}")

    monkeypatch.setattr(execution, "load_macro", lambda name: macros[name])
    monkeypatch.setattr(execution, "_push_status", AsyncMock())
    monkeypatch.setattr(execution, "_suggest_fix", AsyncMock(return_value=None))
    monkeypatch.setattr(execution, "dispatch_plain_action", fail_dispatch)

    with pytest.raises(RuntimeError) as caught:
        await execution.run_macro(session, "outer", {})

    assert SECRET_B not in repr(caught.value.args)
    assert caught.value.__cause__ is None
