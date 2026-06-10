# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for the post-review-2 telemetry fixes:

1. macros.execution._run_macro_impl emits the failed-status counter +
   duration histogram on the exception path (previously the metric+log
   block sat outside the try/finally and silently never fired on failure).
2. macros.execution._macro_label() caps macro-name label cardinality at
   METRICS_MACRO_LABEL_CAP, collapsing overflow to "(overflow)".
3. macros.execution.run_sequence wraps its body in an
   ``octowright.macro.run_sequence`` parent span.
4. session.core_page_mixin.navigate sanitizes the URL passed to the span
   attribute (query string stripped) without changing the recorder/url
   value.
5. browser_pool.lifecycle.handoff_browser emits an
   ``octowright.browser.handoff`` span.
6. browser_pool.pool.relaunch_fluid emits an
   ``octowright.browser.relaunch_fluid`` span.
7. scenarios_pool.ScenarioPool.start emits an ``octowright.scenario.start``
   span; ScenarioPool.run_macro emits ``octowright.scenario.run_macro``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Shared OTel helpers
# ---------------------------------------------------------------------------


def _setup_metric_reader(monkeypatch: pytest.MonkeyPatch | None = None):
    """Install a fresh MeterProvider with an InMemoryMetricReader.

    Octowright's instruments are now provide.telemetry instruments, which
    resolve their OTel meter lazily via
    ``provide.telemetry.metrics.provider.get_meter``. Patch that seam to return
    a meter from a fresh isolated provider, then force the module-level macro
    instruments to re-resolve against it (they cache the resolved handle).
    """
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("octowright")

    if monkeypatch is not None:
        from provide.telemetry.metrics import provider as _pt_metrics_provider

        monkeypatch.setattr(_pt_metrics_provider, "get_meter", lambda *_a, **_k: meter)

        # provide.telemetry instruments cache their resolved OTel handle behind
        # ``_resolved``; clear it on the module-level macro instruments so the
        # next add()/record() rebinds to the freshly-injected meter.
        from octowright.macros import execution as _execution

        for proxy in (_execution._MACRO_RUN, _execution._MACRO_RUN_DURATION):
            if hasattr(proxy, "_resolved"):
                proxy._resolved = False  # type: ignore[attr-defined]
    return reader


def _setup_span_exporter(monkeypatch: pytest.MonkeyPatch | None = None):
    """Install a fresh TracerProvider with an InMemorySpanExporter.

    Octowright's spans now come from provide.telemetry's ``span()``/``@trace``,
    which resolve the tracer via ``provide.telemetry.tracing.provider``. Patch
    that seam (and mark a provider configured) so spans land in our in-memory
    exporter without touching the process-global OTel provider.
    """
    pytest.importorskip("opentelemetry.sdk")
    import opentelemetry.trace as _otel_trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("octowright")
    fake_api = SimpleNamespace(
        get_tracer=lambda *_a, **_k: tracer,
        get_current_span=_otel_trace.get_current_span,
    )

    if monkeypatch is not None:
        from provide.telemetry.tracing import provider as _pt_provider

        monkeypatch.setattr(_pt_provider, "_HAS_OTEL", True)
        monkeypatch.setattr(_pt_provider, "_provider_configured", True)
        monkeypatch.setattr(_pt_provider, "_load_otel_trace_api", lambda: fake_api)
    return exporter


def _collect_counter_points(reader, metric_name: str) -> list[tuple[dict[str, Any], float]]:
    """Return [(attributes, value), ...] for ``metric_name`` from ``reader``."""
    points: list[tuple[dict[str, Any], float]] = []
    data = reader.get_metrics_data()
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name != metric_name:
                    continue
                for point in metric.data.data_points:
                    points.append((dict(point.attributes), point.value))
    return points


def _collect_histogram_points(reader, metric_name: str) -> list[tuple[dict[str, Any], int]]:
    """Return [(attributes, count), ...] for ``metric_name`` from ``reader``."""
    points: list[tuple[dict[str, Any], int]] = []
    data = reader.get_metrics_data()
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name != metric_name:
                    continue
                for point in metric.data.data_points:
                    points.append((dict(point.attributes), point.count))
    return points


def _span_attrs(exporter, span_name: str) -> list[dict[str, Any]]:
    """Return per-span attribute dicts for spans matching ``span_name``."""
    return [dict(s.attributes) for s in exporter.get_finished_spans() if s.name == span_name]


# ---------------------------------------------------------------------------
# Fix 1: _MACRO_RUN / _MACRO_RUN_DURATION fire on both paths
# ---------------------------------------------------------------------------


class _FakeSession:
    def __init__(self) -> None:
        self.page = MagicMock()
        self.page.evaluate = AsyncMock()
        self.diagnostic_bundle = AsyncMock(return_value={"hint": "yo"})
        # SessionLike attrs for run_macro span tagging; previously masked by
        # getattr(..., None) defaults.
        self.instance_id = "fake-instance"
        self.kind = "chromium"


@pytest.fixture
def fake_session() -> _FakeSession:
    return _FakeSession()


@pytest.fixture
def patched_runners(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Wire load_macro / _dispatch_one / _suggest_fix to in-memory fakes."""
    from octowright.macros import execution as _execution

    macros: dict[str, dict[str, Any]] = {}
    raise_on: dict[str, Exception] = {}

    def fake_load(name: str) -> dict[str, Any]:
        if name not in macros:
            raise FileNotFoundError(name)
        return macros[name]

    async def fake_dispatch_one(session: Any, action: dict[str, Any], **_kwargs: Any) -> tuple[int, int]:
        if action.get("action") in raise_on:
            raise raise_on[action["action"]]
        return (1, 0)

    async def fake_suggest(session: Any, action: dict[str, Any]) -> str | None:
        return None

    monkeypatch.setattr(_execution, "load_macro", fake_load)
    monkeypatch.setattr(_execution, "_dispatch_one", fake_dispatch_one)
    monkeypatch.setattr(_execution, "_suggest_fix", fake_suggest)

    def register(name: str, actions: list[dict[str, Any]]) -> None:
        macros[name] = {"name": name, "actions": actions}

    return {"register": register, "raise_on": raise_on}


class TestMacroRunMetricsOnBothPaths:
    @pytest.mark.anyio
    async def test_ok_status_recorded_on_success(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from octowright.macros.execution import run_macro

        reader = _setup_metric_reader(monkeypatch)
        patched_runners["register"]("happy", [{"action": "click", "selector": "#x"}])
        await run_macro(fake_session, "happy")

        counter_points = _collect_counter_points(reader, "octowright_macro_run_total")
        # At least one point with status=ok for this macro.
        ok_points = [v for attrs, v in counter_points if attrs.get("status") == "ok" and attrs.get("macro") == "happy"]
        assert ok_points and sum(ok_points) >= 1

        hist_points = _collect_histogram_points(reader, "octowright_macro_run_duration_seconds")
        hist_for_macro = [count for attrs, count in hist_points if attrs.get("macro") == "happy"]
        assert hist_for_macro and sum(hist_for_macro) >= 1

    @pytest.mark.anyio
    async def test_failed_status_recorded_on_exception(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The critical regression: a raised RuntimeError must still emit the metric."""
        from octowright.macros.execution import run_macro

        reader = _setup_metric_reader(monkeypatch)
        patched_runners["register"]("sadpath", [{"action": "click", "selector": "#x"}])
        patched_runners["raise_on"]["click"] = ValueError("boom")
        with pytest.raises(RuntimeError):
            await run_macro(fake_session, "sadpath")

        counter_points = _collect_counter_points(reader, "octowright_macro_run_total")
        failed_points = [
            v for attrs, v in counter_points if attrs.get("status") == "failed" and attrs.get("macro") == "sadpath"
        ]
        assert failed_points, "failed-status datapoint was never emitted"
        assert sum(failed_points) >= 1

    @pytest.mark.anyio
    async def test_duration_recorded_on_failure(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The histogram must record on the failure path too, not just success."""
        from octowright.macros.execution import run_macro

        reader = _setup_metric_reader(monkeypatch)
        patched_runners["register"]("sadhist", [{"action": "click", "selector": "#x"}])
        patched_runners["raise_on"]["click"] = ValueError("boom")
        with pytest.raises(RuntimeError):
            await run_macro(fake_session, "sadhist")

        hist_points = _collect_histogram_points(reader, "octowright_macro_run_duration_seconds")
        hist_for_macro = [count for attrs, count in hist_points if attrs.get("macro") == "sadhist"]
        assert hist_for_macro and sum(hist_for_macro) >= 1


# ---------------------------------------------------------------------------
# Fix 2: _macro_label cardinality cap
# ---------------------------------------------------------------------------


class TestMacroLabelCardinalityCap:
    def test_distinct_names_within_cap_pass_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """First N (cap-sized) distinct names should be returned verbatim."""
        from octowright.macros import execution as _execution

        cap = 8
        monkeypatch.setattr(_execution, "METRICS_MACRO_LABEL_CAP", cap)
        monkeypatch.setattr(_execution, "_MACRO_LABEL_SEEN", set())

        for i in range(cap):
            assert _execution._macro_label(f"m{i}") == f"m{i}"

    def test_name_beyond_cap_collapses_to_overflow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The (cap+1)-th distinct name becomes the overflow bucket."""
        from octowright.macros import execution as _execution

        cap = 4
        monkeypatch.setattr(_execution, "METRICS_MACRO_LABEL_CAP", cap)
        monkeypatch.setattr(_execution, "_MACRO_LABEL_SEEN", set())

        for i in range(cap):
            _execution._macro_label(f"m{i}")
        # Cap is now full; any new name overflows.
        assert _execution._macro_label("never-seen") == _execution._MACRO_LABEL_OVERFLOW
        assert _execution._macro_label("also-new") == _execution._MACRO_LABEL_OVERFLOW

    def test_already_seen_name_returned_verbatim_after_overflow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A name admitted before the cap was hit must keep its identity forever."""
        from octowright.macros import execution as _execution

        cap = 4
        monkeypatch.setattr(_execution, "METRICS_MACRO_LABEL_CAP", cap)
        monkeypatch.setattr(_execution, "_MACRO_LABEL_SEEN", set())

        # Admit "early" as the very first label so it occupies a slot.
        assert _execution._macro_label("early") == "early"
        # Fill up the remaining cap-1 slots with throwaways.
        for i in range(cap - 1):
            _execution._macro_label(f"m{i}")
        # Now flood with many distinct names — every one overflows.
        for j in range(1000):
            assert _execution._macro_label(f"overflow-{j}") == _execution._MACRO_LABEL_OVERFLOW
        # "early" still passes through verbatim despite the flood.
        assert _execution._macro_label("early") == "early"

    @pytest.mark.anyio
    async def test_metrics_use_capped_label_after_overflow(
        self,
        fake_session: _FakeSession,
        patched_runners: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """End-to-end: run a macro with a name that overflows -> metric labels include "(overflow)"."""
        from octowright.macros import execution as _execution
        from octowright.macros.execution import run_macro

        # Force a tiny cap that's already saturated so any new name overflows.
        monkeypatch.setattr(_execution, "METRICS_MACRO_LABEL_CAP", 1)
        seen = {"already-in-set"}
        monkeypatch.setattr(_execution, "_MACRO_LABEL_SEEN", seen)

        reader = _setup_metric_reader(monkeypatch)
        patched_runners["register"]("brand-new-name", [{"action": "click", "selector": "#x"}])
        await run_macro(fake_session, "brand-new-name")

        counter_points = _collect_counter_points(reader, "octowright_macro_run_total")
        labels = {attrs.get("macro") for attrs, _v in counter_points}
        assert _execution._MACRO_LABEL_OVERFLOW in labels
        # And the new name itself was NOT admitted into the set.
        assert "brand-new-name" not in seen


class TestResetMacroLabelSeen:
    """Operator-visible reset helper for ``_MACRO_LABEL_SEEN``.

    Dynamic macro names (e.g. ``migrate-table-{uuid}``) can permanently fill
    the 256-slot cap with junk and force real macros into ``(overflow)``.
    ``reset_macro_label_seen()`` is the only escape hatch short of a daemon
    restart — exposed for tests and for an operator who has process access
    via the existing ``octowright_status`` MCP tool's reporting fields.
    """

    def test_reset_clears_seen_set_and_returns_prior_size(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from octowright.macros import execution as _execution

        seen: set[str] = set()
        monkeypatch.setattr(_execution, "_MACRO_LABEL_SEEN", seen)
        _execution._macro_label("alpha")
        _execution._macro_label("beta")
        _execution._macro_label("gamma")
        assert len(seen) == 3

        prior = _execution.reset_macro_label_seen()
        assert prior == 3
        assert len(seen) == 0

    def test_reset_on_empty_set_returns_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from octowright.macros import execution as _execution

        monkeypatch.setattr(_execution, "_MACRO_LABEL_SEEN", set())
        assert _execution.reset_macro_label_seen() == 0

    def test_reset_lets_overflowed_name_re_enter_after_clear(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """After reset, previously-overflowed names get fresh slots."""
        from octowright.macros import execution as _execution

        monkeypatch.setattr(_execution, "METRICS_MACRO_LABEL_CAP", 2)
        seen: set[str] = set()
        monkeypatch.setattr(_execution, "_MACRO_LABEL_SEEN", seen)

        _execution._macro_label("a")
        _execution._macro_label("b")
        # cap reached; "c" collapses.
        assert _execution._macro_label("c") == _execution._MACRO_LABEL_OVERFLOW

        prior = _execution.reset_macro_label_seen()
        assert prior == 2
        # After reset, "c" gets admitted (no longer overflows).
        assert _execution._macro_label("c") == "c"


# ---------------------------------------------------------------------------
# Fix 3: run_sequence span
# ---------------------------------------------------------------------------


class TestRunSequenceSpan:
    @pytest.mark.anyio
    async def test_emits_run_sequence_span(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from octowright.macros.execution import run_sequence

        exporter = _setup_span_exporter(monkeypatch)
        patched_runners["register"]("m1", [{"action": "click", "selector": "#a"}])
        patched_runners["register"]("m2", [{"action": "click", "selector": "#b"}])
        await run_sequence(session=fake_session, names=["m1", "m2"], stop_on_failure=True)

        all_attrs = _span_attrs(exporter, "octowright.macro.run_sequence")
        assert all_attrs, "octowright.macro.run_sequence span never fired"
        attrs = all_attrs[-1]
        assert attrs.get("names_count") == 2
        assert attrs.get("stop_on_failure") is True

    @pytest.mark.anyio
    async def test_per_macro_runs_nest_under_sequence(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The per-macro ``octowright.macro.run`` spans should have the sequence span as parent."""
        from octowright.macros.execution import run_sequence

        exporter = _setup_span_exporter(monkeypatch)
        patched_runners["register"]("m1", [{"action": "click", "selector": "#a"}])
        await run_sequence(session=fake_session, names=["m1"], stop_on_failure=True)

        finished = exporter.get_finished_spans()
        seq = next(s for s in finished if s.name == "octowright.macro.run_sequence")
        run = next(s for s in finished if s.name == "octowright.macro.run")
        assert run.parent is not None
        assert run.parent.span_id == seq.context.span_id

    @pytest.mark.anyio
    async def test_run_sequence_stop_on_failure_false_recorded(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from octowright.macros.execution import run_sequence

        exporter = _setup_span_exporter(monkeypatch)
        patched_runners["register"]("m1", [{"action": "click", "selector": "#a"}])
        patched_runners["raise_on"]["click"] = ValueError("boom")
        result = await run_sequence(session=fake_session, names=["m1"], stop_on_failure=False)
        # stop_on_failure=False means swallow + collect, but the span must still fire.
        assert result["ok"] is False

        all_attrs = _span_attrs(exporter, "octowright.macro.run_sequence")
        assert all_attrs
        assert all_attrs[-1].get("stop_on_failure") is False


# ---------------------------------------------------------------------------
# Fix 4: navigate URL sanitization for the span attribute
# ---------------------------------------------------------------------------


def _make_navigate_subject() -> Any:
    """Minimal SessionPageMixin subject — only what navigate() touches."""
    from octowright.session.core_page_mixin import SessionPageMixin

    subj = SessionPageMixin.__new__(SessionPageMixin)
    subj._last_mcp_navigation = None
    page = MagicMock()
    page.url = "https://octowright.com"
    page.goto = AsyncMock()
    page.title = AsyncMock(return_value="t")
    subj.page = page
    subj.pages = [page]
    subj.url = None
    subj.recorder = MagicMock()
    subj.recorder.record = MagicMock()
    subj._target = lambda: subj.page  # type: ignore[attr-defined]
    subj._schedule_markdown_capture = MagicMock()  # type: ignore[attr-defined]
    return subj


class TestNavigateUrlSanitization:
    def test_sanitize_strips_query(self) -> None:
        from octowright.session.core_page_mixin import _sanitize_url_for_span

        assert _sanitize_url_for_span("https://octowright.com/a?token=abc&id=1") == "https://octowright.com/a"

    def test_sanitize_keeps_fragment_and_path(self) -> None:
        from octowright.session.core_page_mixin import _sanitize_url_for_span

        # urlsplit preserves fragment; only query is stripped.
        out = _sanitize_url_for_span("https://octowright.com/x/y?secret=1#frag")
        assert out == "https://octowright.com/x/y#frag"

    def test_sanitize_passthrough_when_no_query(self) -> None:
        from octowright.session.core_page_mixin import _sanitize_url_for_span

        assert _sanitize_url_for_span("https://octowright.com/a") == "https://octowright.com/a"

    def test_sanitize_falls_back_on_parse_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If urlsplit raises, the helper returns the original URL untouched."""
        from octowright.session import core_page_mixin as _mixin

        def boom(_url: str) -> Any:
            raise RuntimeError("parse error")

        monkeypatch.setattr(_mixin, "urlsplit", boom)
        assert _mixin._sanitize_url_for_span("anything") == "anything"

    @pytest.mark.anyio
    async def test_navigate_span_url_attribute_strips_query(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """End-to-end: a navigate call's span attribute drops the query string."""
        exporter = _setup_span_exporter(monkeypatch)
        subj = _make_navigate_subject()
        await subj.navigate("https://target.com/path?token=SECRET")

        attrs = _span_attrs(exporter, "octowright.session.navigate")
        assert attrs, "navigate span never emitted"
        # Span URL is sanitized.
        assert attrs[-1].get("url") == "https://target.com/path"
        assert "SECRET" not in attrs[-1].get("url", "")

    @pytest.mark.anyio
    async def test_navigate_full_url_still_passed_to_recorder_and_self_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The recorder + self.url still carry the FULL URL — only the span is sanitized."""
        exporter = _setup_span_exporter(monkeypatch)  # noqa: F841 - just installs provider
        subj = _make_navigate_subject()
        await subj.navigate("https://target.com/path?token=SECRET")
        assert subj.url == "https://target.com/path?token=SECRET"
        subj.recorder.record.assert_any_call("navigate", url="https://target.com/path?token=SECRET")
        # page.goto receives the full URL too.
        subj.page.goto.assert_awaited_once()
        assert subj.page.goto.await_args.args[0] == "https://target.com/path?token=SECRET"


# ---------------------------------------------------------------------------
# Fix 5a: handoff span
# ---------------------------------------------------------------------------


class TestHandoffSpan:
    @pytest.mark.anyio
    async def test_handoff_emits_span_with_attrs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from octowright.browser_pool import BrowserPool

        exporter = _setup_span_exporter(monkeypatch)
        pool = BrowserPool()
        pool._sessions["old01"] = SimpleNamespace(
            instance_id="old01",
            kind="webkit",
            profile="dante",
            label="lab",
            url="https://octowright.com",
            user_data_dir="/tmp/profile-dir",
            har_path=None,
            stabilize=False,
            page=SimpleNamespace(url="https://octowright.com/live"),
        )

        async def _fake_close(instance_id: str) -> dict[str, Any]:
            pool._sessions.pop(instance_id, None)
            return {"closed": True}

        async def _fake_launch(**kwargs: Any) -> dict[str, Any]:
            return {"instance_id": "new01"}

        monkeypatch.setattr(pool, "close", _fake_close)
        monkeypatch.setattr(pool, "launch", _fake_launch)
        await pool.handoff("old01", headed=False)

        attrs_list = _span_attrs(exporter, "octowright.browser.handoff")
        assert attrs_list, "handoff span never emitted"
        attrs = attrs_list[-1]
        assert attrs.get("old_instance_id") == "old01"
        assert attrs.get("kind") == "webkit"
        assert attrs.get("headed") is False
        assert attrs.get("close_original") is True
        assert attrs.get("accept_stateless") is False

    @pytest.mark.anyio
    async def test_handoff_span_fires_even_on_validation_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the stateless guard rejects the call, the span still wraps the throw."""
        from octowright.browser_pool import BrowserPool

        exporter = _setup_span_exporter(monkeypatch)
        pool = BrowserPool()
        pool._sessions["old02"] = SimpleNamespace(
            instance_id="old02",
            kind="chromium",
            profile=None,
            label=None,
            url="https://octowright.com",
            user_data_dir=None,
            har_path=None,
            stabilize=False,
            page=SimpleNamespace(url="https://octowright.com"),
        )
        with pytest.raises(ValueError, match="accept_stateless=True"):
            await pool.handoff("old02", headed=True)
        # Span is still emitted (it wraps the raise).
        attrs_list = _span_attrs(exporter, "octowright.browser.handoff")
        assert attrs_list


# ---------------------------------------------------------------------------
# Fix 5b: relaunch_fluid span
# ---------------------------------------------------------------------------


class TestRelaunchFluidSpan:
    @pytest.mark.anyio
    async def test_relaunch_fluid_emits_span(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from octowright.browser_pool import BrowserPool

        exporter = _setup_span_exporter(monkeypatch)
        pool = BrowserPool()
        source = SimpleNamespace(
            instance_id="rid",
            kind="chromium",
            profile=None,
            label="lab",
            url="https://octowright.com/initial",
            user_data_dir=None,
            har_path=None,
            stabilize=False,
            trace=False,
            page=SimpleNamespace(url="https://octowright.com/live"),
        )
        pool._sessions["rid"] = source

        async def _fake_close(instance_id: str) -> dict[str, Any]:
            pool._sessions.pop(instance_id, None)
            return {"closed": True}

        async def _fake_launch(**_kw: Any) -> dict[str, Any]:
            return {"instance_id": "new-rid"}

        monkeypatch.setattr(pool, "close", _fake_close)
        monkeypatch.setattr(pool, "launch", _fake_launch)

        out = await pool.relaunch_fluid("rid")
        assert out["new_instance_id"] == "new-rid"

        attrs_list = _span_attrs(exporter, "octowright.browser.relaunch_fluid")
        assert attrs_list
        attrs = attrs_list[-1]
        assert attrs.get("instance_id") == "rid"
        assert attrs.get("kind") == "chromium"


# ---------------------------------------------------------------------------
# Fix 5c: scenario.start + scenario.run_macro spans
# ---------------------------------------------------------------------------


from dataclasses import dataclass, field  # noqa: E402


@dataclass
class _ParticipantSpec:
    persona: str
    role: str
    startup_macros: list[str] = field(default_factory=list)


@dataclass
class _Spec:
    name: str
    participants: list[_ParticipantSpec]
    fixtures: dict[str, Any] = field(default_factory=dict)
    teardown_macro: str | None = None


class _FakeSessionForScenario:
    def __init__(self, instance_id: str) -> None:
        self.instance_id = instance_id
        self.page = SimpleNamespace(url="https://x")


class _FakePoolForScenario:
    def __init__(self) -> None:
        self.sessions = {"a": _FakeSessionForScenario("a")}

    async def spawn_roster(self, _specs: Any) -> dict[str, Any]:
        return {
            "launched": [
                {"instance_id": "a", "log_path": "/tmp/a.log", "kind": "chromium"},
            ],
            "errors": [],
        }

    def get(self, instance_id: str) -> Any:
        return self.sessions[instance_id]

    async def close(self, _instance_id: str) -> None:
        return None


class TestScenarioSpans:
    @pytest.mark.anyio
    async def test_scenario_start_emits_span(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from octowright import scenarios_pool as _sp

        # Bypass scenarios.load_scenario / resolve_launch_kwargs / startup macros.
        spec = _Spec(name="demo", participants=[_ParticipantSpec(persona="cosmo", role="r1")])

        def _fake_resolve_launch_kwargs(_p: Any) -> dict[str, Any]:
            return {"kind": "chromium"}

        def _fake_load_scenario(_name: str) -> Any:
            return spec

        async def _no_op_apply_fixtures(*_args: Any, **_kw: Any) -> None:
            return None

        async def _no_op_startup_macros(*_args: Any, **_kw: Any) -> None:
            return None

        monkeypatch.setattr(_sp, "_apply_fixtures", _no_op_apply_fixtures)
        monkeypatch.setattr(_sp, "_run_startup_macros", _no_op_startup_macros)

        # Patch the inline imports inside ScenarioPool.start.
        import octowright.scenarios as _scenarios

        monkeypatch.setattr(_scenarios, "load_scenario", _fake_load_scenario, raising=False)
        monkeypatch.setattr(_scenarios, "resolve_launch_kwargs", _fake_resolve_launch_kwargs, raising=False)

        exporter = _setup_span_exporter(monkeypatch)
        sp = _sp.ScenarioPool()
        pool = _FakePoolForScenario()
        live = await sp.start(name="demo", browser_pool=pool, spec=spec)
        assert live.name == "demo"

        attrs_list = _span_attrs(exporter, "octowright.scenario.start")
        assert attrs_list
        attrs = attrs_list[-1]
        assert attrs.get("scenario_name") == "demo"
        assert attrs.get("participants") == 1
        assert isinstance(attrs.get("scenario_id"), str)

    @pytest.mark.anyio
    async def test_scenario_run_macro_emits_span(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from octowright import scenarios_pool as _sp
        from octowright.scenarios_pool import LiveScenario

        exporter = _setup_span_exporter(monkeypatch)
        sp = _sp.ScenarioPool()
        spec = _Spec(name="demo", participants=[_ParticipantSpec(persona="cosmo", role="r1")])
        live = LiveScenario(
            scenario_id="sid",
            name="demo",
            spec=spec,
            participants=[{"instance_id": "a", "persona": "cosmo", "role": "r1"}],
        )
        sp._live[live.scenario_id] = live

        pool = _FakePoolForScenario()

        async def _fake_run_macro(*, session: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True}

        # Patch the macros module so the per-participant run_macro is a no-op.
        import octowright.macros as _macros

        monkeypatch.setattr(_macros, "run_macro", _fake_run_macro, raising=False)

        out = await sp.run_macro(scenario_id="sid", macro="m", browser_pool=pool, role="r1")
        assert out["targeted"] == 1

        attrs_list = _span_attrs(exporter, "octowright.scenario.run_macro")
        assert attrs_list
        attrs = attrs_list[-1]
        assert attrs.get("scenario_id") == "sid"
        assert attrs.get("macro") == "m"
        assert attrs.get("role") == "r1"
        assert attrs.get("targeted") is True  # role filter applied

    @pytest.mark.anyio
    async def test_scenario_run_macro_targeted_false_when_no_role(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from octowright import scenarios_pool as _sp
        from octowright.scenarios_pool import LiveScenario

        exporter = _setup_span_exporter(monkeypatch)
        sp = _sp.ScenarioPool()
        spec = _Spec(name="demo", participants=[_ParticipantSpec(persona="cosmo", role="r1")])
        live = LiveScenario(
            scenario_id="sid2",
            name="demo",
            spec=spec,
            participants=[{"instance_id": "a", "persona": "cosmo", "role": "r1"}],
        )
        sp._live[live.scenario_id] = live
        pool = _FakePoolForScenario()

        async def _fake_run_macro(*, session: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True}

        import octowright.macros as _macros

        monkeypatch.setattr(_macros, "run_macro", _fake_run_macro, raising=False)

        await sp.run_macro(scenario_id="sid2", macro="m", browser_pool=pool, role=None)
        attrs = _span_attrs(exporter, "octowright.scenario.run_macro")[-1]
        assert attrs.get("targeted") is False
