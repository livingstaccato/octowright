# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for BrowserSession telemetry context binding lifecycle.

Covers the three lifecycle points where the structlog contextvar binding
installed in ``BrowserSession.__post_init__`` must be torn down so the
per-session identifiers (instance_id, kind, profile, label) do not leak
into log lines emitted by unrelated tasks running on the same event loop:

1. ``__post_init__`` calls ``bind_context`` with all four keys.
2. ``unbind_telemetry_context`` calls ``unbind_context`` with the same
   four key names.
3. The external eviction path in ``browser_pool/listeners.py::_evict``
   invokes ``session.unbind_telemetry_context`` after the recorder
   cleanup so eviction leaves no residual binding.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# Import browser_pool first so the pool→listeners→session chain initializes
# before we touch session.core directly. Importing session.core ahead of
# browser_pool triggers a pre-existing circular import at collection time.
import octowright.browser_pool  # noqa: F401
from octowright.session.core import BrowserSession


def _make_session(
    monkeypatch: pytest.MonkeyPatch,
    *,
    bind_spy: Any,
    unbind_spy: Any,
    instance_id: str = "iid-1",
    kind: str = "chromium",
    profile: str | None = "alice",
    label: str | None = "L",
) -> BrowserSession:
    """Construct a BrowserSession with bind/unbind patched on the import site."""
    from octowright.session import core as _core

    monkeypatch.setattr(_core, "bind_context", bind_spy)
    monkeypatch.setattr(_core, "unbind_context", unbind_spy)

    context = MagicMock()
    context.close = AsyncMock(return_value=None)
    return BrowserSession(
        instance_id=instance_id,
        kind=kind,
        label=label,
        url="https://example.test",
        browser=None,
        context=context,
        page=MagicMock(),
        recorder=MagicMock(),
        log_path=Path("/tmp/octowright-telemetry-test.jsonl"),
        profile=profile,
    )


# ─── __post_init__ → bind_context ────────────────────────────────────────────


class TestPostInitBinding:
    def test_post_init_binds_session_context_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Constructing a BrowserSession must call bind_context with the four
        canonical octowright_* keys so subsequent log lines auto-carry the
        per-session identifiers."""
        bind_spy = MagicMock()
        unbind_spy = MagicMock()
        _make_session(
            monkeypatch,
            bind_spy=bind_spy,
            unbind_spy=unbind_spy,
            instance_id="iid-42",
            kind="webkit",
            profile="bob",
            label="player",
        )
        bind_spy.assert_called_once()
        kwargs = bind_spy.call_args.kwargs
        assert kwargs == {
            "octowright_instance_id": "iid-42",
            "octowright_kind": "webkit",
            "octowright_profile": "bob",
            "octowright_label": "player",
        }

    def test_post_init_bind_failure_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If telemetry binding raises (e.g. provide.telemetry not fully
        initialised in a worker thread), session creation must still succeed.
        Telemetry must never break session creation."""
        bind_spy = MagicMock(side_effect=RuntimeError("telemetry down"))
        unbind_spy = MagicMock()
        # Must not raise.
        session = _make_session(monkeypatch, bind_spy=bind_spy, unbind_spy=unbind_spy)
        assert session.instance_id == "iid-1"


# ─── unbind_telemetry_context → unbind_context ───────────────────────────────


class TestUnbindTelemetryContext:
    def test_unbind_uses_four_canonical_key_names(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """unbind_telemetry_context must remove the same four key names that
        __post_init__ binds — otherwise the binding survives session close."""
        bind_spy = MagicMock()
        unbind_spy = MagicMock()
        session = _make_session(monkeypatch, bind_spy=bind_spy, unbind_spy=unbind_spy)
        unbind_spy.reset_mock()  # ignore any incidental calls
        session.unbind_telemetry_context()
        unbind_spy.assert_called_once_with(
            "octowright_instance_id",
            "octowright_kind",
            "octowright_profile",
            "octowright_label",
        )

    def test_unbind_failure_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A failing unbind must not propagate — teardown paths are
        best-effort and a noisy telemetry layer should not break shutdown."""
        bind_spy = MagicMock()
        unbind_spy = MagicMock(side_effect=RuntimeError("telemetry down"))
        session = _make_session(monkeypatch, bind_spy=bind_spy, unbind_spy=unbind_spy)
        # Must not raise.
        session.unbind_telemetry_context()


# ─── _evict path → unbind_telemetry_context ──────────────────────────────────


class TestEvictUnbindsTelemetryContext:
    def test_evict_calls_unbind_telemetry_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The external eviction path (context.close / browser.disconnected /
        last-page-close) must tear down the structlog binding so the per-
        session identifiers don't leak into unrelated logs on the same
        loop after the session is gone."""
        from octowright.browser_pool import listeners as _listeners

        # Stand-in pool: only needs _evict_session_nowait to return a session.
        sentinel_session = MagicMock()
        sentinel_session.kind = "chromium"
        sentinel_session.profile = "alice"
        sentinel_session.log_path = Path("/tmp/x.jsonl")
        pool = MagicMock()
        pool._evict_session_nowait = MagicMock(return_value=sentinel_session)

        # Build a real session object so the wiring under test sees the
        # actual unbind_telemetry_context method and the on() registrations
        # work end-to-end.
        bind_spy = MagicMock()
        unbind_spy = MagicMock()
        session = _make_session(monkeypatch, bind_spy=bind_spy, unbind_spy=unbind_spy)

        # Spy on the helper method so the assertion is independent of the
        # internals of unbind_context.
        unbind_method_spy = MagicMock(wraps=session.unbind_telemetry_context)
        monkeypatch.setattr(session, "unbind_telemetry_context", unbind_method_spy)

        # Capture registered context-close handlers via a dict-on-the-fly stub.
        handlers: dict[str, list[Any]] = {}

        def _on(event: str, cb: Any) -> None:
            handlers.setdefault(event, []).append(cb)

        session.context.on = _on  # type: ignore[method-assign]

        # Avoid the session_manifest import path raising in the test env.
        import octowright.session_manifest as _manifest

        monkeypatch.setattr(_manifest, "remove_session", lambda *_a, **_kw: None)

        _listeners._wire_close_evictor(pool, session)
        close_handlers = handlers.get("close", [])
        assert close_handlers, "expected at least one context.on('close') handler"

        # Synthesize the external-close signal.
        for cb in close_handlers:
            cb()

        unbind_method_spy.assert_called()

    def test_evict_swallows_unbind_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If unbind_telemetry_context raises (e.g. a partially-initialised
        test subject), eviction must not blow up — wrap it in try/except and
        log at debug. Verified by replacing the helper with a raising stub
        and confirming the eviction sequence completes."""
        from octowright.browser_pool import listeners as _listeners

        sentinel_session = MagicMock()
        sentinel_session.kind = "chromium"
        sentinel_session.profile = None
        sentinel_session.log_path = Path("/tmp/x.jsonl")
        pool = MagicMock()
        pool._evict_session_nowait = MagicMock(return_value=sentinel_session)

        bind_spy = MagicMock()
        unbind_spy = MagicMock()
        session = _make_session(monkeypatch, bind_spy=bind_spy, unbind_spy=unbind_spy)

        # Replace the helper with a stub that raises so the try/except in
        # _evict is exercised.
        def _raising_unbind() -> None:
            raise RuntimeError("unbind boom")

        monkeypatch.setattr(session, "unbind_telemetry_context", _raising_unbind)

        handlers: dict[str, list[Any]] = {}

        def _on(event: str, cb: Any) -> None:
            handlers.setdefault(event, []).append(cb)

        session.context.on = _on  # type: ignore[method-assign]

        import octowright.session_manifest as _manifest

        monkeypatch.setattr(_manifest, "remove_session", lambda *_a, **_kw: None)

        # Capture debug log calls to confirm the failure is logged at debug.
        debug_events: list[tuple[str, dict[str, Any]]] = []

        class _LogStub:
            def info(self, *_a: Any, **_kw: Any) -> None:
                pass

            def warning(self, *_a: Any, **_kw: Any) -> None:
                pass

            def debug(self, event: str, **kw: Any) -> None:
                debug_events.append((event, kw))

        monkeypatch.setattr(_listeners, "log", _LogStub())

        _listeners._wire_close_evictor(pool, session)
        for cb in handlers.get("close", []):
            cb()

        # The debug log entry for the unbind failure must have fired.
        assert any("unbind_telemetry_failed" in name for name, _ in debug_events), debug_events

    def test_evict_handles_session_without_unbind_method(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Partially-initialised test subjects may not expose
        ``unbind_telemetry_context``; eviction must getattr-guard against
        AttributeError instead of blowing up."""
        # Build a minimal session-shaped object that intentionally LACKS
        # unbind_telemetry_context. SimpleNamespace works because the
        # _evict path uses getattr-with-default.
        from types import SimpleNamespace

        from octowright.browser_pool import listeners as _listeners

        stub_session = SimpleNamespace(
            instance_id="iid-stub",
            kind="chromium",
            profile=None,
            label=None,
            log_path=Path("/tmp/x.jsonl"),
            recorder=MagicMock(),
            context=MagicMock(),
            browser=None,
            pages=[],
        )
        # Explicitly delete (or just don't set) the attr.
        assert not hasattr(stub_session, "unbind_telemetry_context")

        sentinel_session = stub_session  # _evict_session_nowait returns this
        pool = MagicMock()
        pool._evict_session_nowait = MagicMock(return_value=sentinel_session)

        handlers: dict[str, list[Any]] = {}

        def _on(event: str, cb: Any) -> None:
            handlers.setdefault(event, []).append(cb)

        stub_session.context.on = _on  # type: ignore[attr-defined]

        import octowright.session_manifest as _manifest

        monkeypatch.setattr(_manifest, "remove_session", lambda *_a, **_kw: None)

        _listeners._wire_close_evictor(pool, stub_session)
        # Must not raise even though the helper method is missing.
        for cb in handlers.get("close", []):
            cb()
