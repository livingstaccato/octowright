# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Whole verification reports, and the spans and metrics a verification emits.

``tests/test_artifacts_verification.py`` drives each evaluator directly; nothing
compared what ``evaluate_checks`` returns as a whole, so the roll-up statuses
(``blocked`` for a critical point with no checks and for a run with no critical
points), the ``last_verified_run``/``verified_at`` keys, the evaluator messages
an operator reads in ``verification.json``, and the failure record written when
an evaluator raises could all change with the suite green.

The span and metric names and attributes are documented in
``docs/telemetry.md`` (``octowright.artifact.verify``,
``octowright.artifact.verify.check``, ``octowright_artifact_verify_total``,
``octowright_artifact_verify_check_total``), which makes them a contract with
whoever dashboards them. They are observed through provide.telemetry's own
provider seams with in-memory OTel exporters, as ``tests/test_telemetry_fixes.py``
does.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

import pytest

from octowright.artifacts.verification import evaluate_checks

_ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")

_RESULT = {"run_id": "r1", "macro": "checkout", "status": "ok"}
_EVIDENCE = [{"id": "ev_001", "type": "screenshot", "label": "after"}]


def _critical_points() -> list[dict[str, Any]]:
    return [
        {"id": "cp1", "description": "ran ok", "checks": [{"type": "result_status", "status": "ok"}]},
        {"id": "cp2", "checks": []},
        {
            "id": "cp3",
            "checks": [
                {"type": "evidence_exists", "label": "after"},
                {"type": "screenshot_exists", "label": "missing"},
            ],
        },
    ]


def _without_stamp(report: dict[str, Any]) -> dict[str, Any]:
    assert _ISO_Z.match(str(report["verified_at"])), report
    return {k: v for k, v in report.items() if k != "verified_at"}


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def test_a_verification_report_is_exactly_the_documented_record() -> None:
    report = evaluate_checks("macro", _critical_points(), _RESULT, _EVIDENCE)

    assert _without_stamp(report) == {
        "status": "failed",
        "critical_points": [
            {
                "id": "cp1",
                "description": "ran ok",
                "status": "passed",
                "last_verified_run": "r1",
                "checks": [
                    {"type": "result_status", "status": "passed", "message": "Result status matched ok", "evidence": []}
                ],
            },
            {"id": "cp2", "checks": [], "status": "blocked", "last_verified_run": "r1"},
            {
                "id": "cp3",
                "status": "failed",
                "last_verified_run": "r1",
                "checks": [
                    {
                        "type": "evidence_exists",
                        "status": "passed",
                        "message": "Found evidence for after",
                        "evidence": ["ev_001"],
                    },
                    {
                        "type": "screenshot_exists",
                        "status": "failed",
                        "message": "missing_evidence_file screenshot with label=missing",
                        "evidence": [],
                    },
                ],
            },
        ],
    }


def test_a_run_with_no_critical_points_is_blocked_not_passed() -> None:
    """Vacuous truth is not verification: nothing declared means nothing verified."""
    assert _without_stamp(evaluate_checks("macro", [], _RESULT, _EVIDENCE)) == {
        "status": "blocked",
        "critical_points": [],
    }


def _single_check(check: dict[str, Any], evidence: list[Any]) -> dict[str, Any]:
    report = evaluate_checks("macro", [{"id": "cp1", "checks": [check]}], _RESULT, evidence)
    return report["critical_points"][0]["checks"][0]


def test_an_evaluator_that_raises_is_recorded_as_a_failed_check_with_the_reason() -> None:
    """A malformed evidence row must fail the check, not the whole verification."""
    record = _single_check({"type": "evidence_exists", "id": "ev_001"}, ["not-a-record"])

    assert record == {
        "type": "evidence_exists",
        "status": "failed",
        "message": "'str' object has no attribute 'get'",
        "evidence": [],
    }


def test_a_check_without_a_type_is_reported_as_unknown() -> None:
    assert _single_check({"label": "after"}, _EVIDENCE) == {
        "type": "unknown",
        "status": "failed",
        "message": "unknown_check_type",
        "evidence": [],
    }


@pytest.mark.parametrize(
    ("check", "evidence", "expected"),
    [
        pytest.param(
            {"type": "assertion_passed", "id": "ev_009"},
            [{"id": "ev_009", "type": "assertion", "status": "passed"}],
            {"type": "assertion_passed", "status": "passed", "message": "Assertion passed", "evidence": ["ev_009"]},
            id="assertion-passed",
        ),
        pytest.param(
            {"type": "assertion_passed", "id": "ev_009"},
            [],
            {"type": "assertion_passed", "status": "failed", "message": "Check failed.", "evidence": []},
            id="assertion-failed",
        ),
        pytest.param(
            {"type": "log_contains"},
            [{"id": "ev_004", "type": "log_excerpt", "preview": "anything"}],
            {"type": "log_contains", "status": "passed", "message": "Found  in log", "evidence": ["ev_004"]},
            id="log-no-text",
        ),
        pytest.param(
            {"type": "log_contains", "text": "None"},
            [{"id": "ev_004", "type": "log_excerpt"}],
            {"type": "log_contains", "status": "failed", "message": "Check failed.", "evidence": []},
            id="log-no-preview-is-empty-not-None",
        ),
        pytest.param(
            {"type": "log_contains", "text": "XX"},
            [{"id": "ev_004", "type": "log_excerpt"}],
            {"type": "log_contains", "status": "failed", "message": "Check failed.", "evidence": []},
            id="log-no-preview-is-empty",
        ),
        pytest.param(
            {"type": "log_contains", "text": "paid"},
            [{"id": "ev_004", "type": "log_excerpt", "preview": "order paid"}],
            {"type": "log_contains", "status": "passed", "message": "Found paid in log", "evidence": ["ev_004"]},
            id="log-found",
        ),
    ],
)
def test_each_evaluator_reports_this_exact_record(
    check: dict[str, Any], evidence: list[dict[str, Any]], expected: dict[str, Any]
) -> None:
    assert _single_check(check, evidence) == expected


# ---------------------------------------------------------------------------
# Spans and metrics
# ---------------------------------------------------------------------------


@pytest.fixture
def telemetry(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    pytest.importorskip("opentelemetry.sdk")
    import opentelemetry.trace as otel_trace
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from provide.telemetry.metrics import provider as metrics_provider
    from provide.telemetry.tracing import provider as tracing_provider

    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader]).get_meter("octowright")
    monkeypatch.setattr(metrics_provider, "get_meter", lambda *_a, **_k: meter)

    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = tracer_provider.get_tracer("octowright")
    api = SimpleNamespace(get_tracer=lambda *_a, **_k: tracer, get_current_span=otel_trace.get_current_span)
    monkeypatch.setattr(tracing_provider, "_HAS_OTEL", True)
    monkeypatch.setattr(tracing_provider, "_provider_configured", True)
    monkeypatch.setattr(tracing_provider, "_load_otel_trace_api", lambda: api)

    def metrics() -> dict[str, list[tuple[dict[str, Any], Any]]]:
        out: dict[str, list[tuple[dict[str, Any], Any]]] = {}
        data: Any = reader.get_metrics_data()  # counters only: every point is a NumberDataPoint
        for rm in data.resource_metrics:
            for sm in rm.scope_metrics:
                for metric in sm.metrics:
                    points = [(dict(p.attributes), p.value) for p in metric.data.data_points]
                    out.setdefault(metric.name, []).extend(points)
        return out

    def spans() -> list[tuple[str, dict[str, Any]]]:
        return [(s.name, dict(s.attributes or {})) for s in exporter.get_finished_spans()]

    return SimpleNamespace(metrics=metrics, spans=spans)


def _sorted(points: list[tuple[dict[str, Any], Any]]) -> list[tuple[dict[str, Any], Any]]:
    return sorted(points, key=lambda p: sorted(p[0].items()))


def test_a_verification_emits_its_documented_spans_and_metrics(telemetry: SimpleNamespace) -> None:
    evaluate_checks("macro", _critical_points(), _RESULT, _EVIDENCE)

    assert telemetry.spans() == [
        (
            "octowright.artifact.verify.check",
            {"artifact_type": "macro", "check_type": "result_status", "status": "passed"},
        ),
        (
            "octowright.artifact.verify.check",
            {"artifact_type": "macro", "check_type": "evidence_exists", "status": "passed"},
        ),
        (
            "octowright.artifact.verify.check",
            {"artifact_type": "macro", "check_type": "screenshot_exists", "status": "failed"},
        ),
        (
            "octowright.artifact.verify",
            {"artifact_type": "macro", "name": "checkout", "critical_points": 3, "run_id": "r1"},
        ),
    ]
    metrics = telemetry.metrics()
    assert set(metrics) == {"octowright_artifact_verify_total", "octowright_artifact_verify_check_total"}
    assert metrics["octowright_artifact_verify_total"] == [({"artifact_type": "macro", "status": "failed"}, 1)]
    assert _sorted(metrics["octowright_artifact_verify_check_total"]) == _sorted(
        [
            ({"artifact_type": "macro", "check_type": "result_status", "status": "passed"}, 1),
            ({"artifact_type": "macro", "check_type": "evidence_exists", "status": "passed"}, 1),
            ({"artifact_type": "macro", "check_type": "screenshot_exists", "status": "failed"}, 1),
        ]
    )


def test_an_empty_run_is_tagged_with_its_fallback_name_and_run_id(telemetry: SimpleNamespace) -> None:
    evaluate_checks("macro", [], {}, [])

    assert telemetry.spans() == [
        (
            "octowright.artifact.verify",
            {"artifact_type": "macro", "name": "unknown", "critical_points": 0, "run_id": ""},
        )
    ]
    assert telemetry.metrics() == {
        "octowright_artifact_verify_total": [({"artifact_type": "macro", "status": "blocked"}, 1)]
    }


def test_a_check_with_no_type_is_counted_under_unknown(telemetry: SimpleNamespace) -> None:
    evaluate_checks("macro", [{"id": "cp1", "checks": [{}]}], _RESULT, [])

    assert telemetry.metrics()["octowright_artifact_verify_check_total"] == [
        ({"artifact_type": "macro", "check_type": "unknown", "status": "failed"}, 1)
    ]
