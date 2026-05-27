# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from typing import Any

from provide.telemetry import get_logger

from octowright._tracing import counter, set_attrs, span
from octowright.artifacts.models import now_iso

log = get_logger("octowright.artifacts.verification")


def evaluate_checks(
    artifact_type: str,
    critical_points: list[dict[str, Any]],
    result: dict[str, Any],
    evidence_records: list[dict[str, Any]],
) -> dict[str, Any]:
    with span("octowright.artifact.verify") as s:
        run_id = result.get("run_id", "")
        name = result.get("macro", "unknown")
        set_attrs(s, artifact_type=artifact_type, name=name, critical_points=len(critical_points), run_id=run_id)

        log.info("octowright.artifact.verify.start", artifact_type=artifact_type, name=name, run_id=run_id)

        verified_cps = []
        all_checks_passed = True

        for cp in critical_points:
            cp_result = _evaluate_cp(artifact_type, name, cp, result, evidence_records)
            verified_cps.append(cp_result)
            if cp_result["status"] != "passed":
                all_checks_passed = False

        status = "passed" if all_checks_passed and critical_points else "failed"
        if not critical_points:
            status = "blocked"

        counter("octowright_artifact_verify_total").add(
            1, attributes={"artifact_type": artifact_type, "status": status}
        )

        log.info(
            "octowright.artifact.verify.complete", artifact_type=artifact_type, name=name, run_id=run_id, status=status
        )

        return {
            "status": status,
            "verified_at": now_iso(),
            "critical_points": verified_cps,
        }


def _evaluate_cp(
    artifact_type: str, name: str, cp: dict[str, Any], result: dict[str, Any], evidence_records: list[dict[str, Any]]
) -> dict[str, Any]:
    checks = cp.get("checks", [])
    if not checks:
        return {**cp, "status": "blocked", "last_verified_run": result.get("run_id")}

    evaluated_checks = []
    all_passed = True
    for check in checks:
        c_res = _evaluate_check(artifact_type, name, cp.get("id"), check, result, evidence_records)
        evaluated_checks.append(c_res)
        if c_res["status"] != "passed":
            all_passed = False

    return {
        **cp,
        "status": "passed" if all_passed else "failed",
        "last_verified_run": result.get("run_id"),
        "checks": evaluated_checks,
    }


def _eval_result_status(check: dict[str, Any], result: dict[str, Any]) -> tuple[str, str, list[str]]:
    expected = check.get("status")
    if result.get("status") == expected:
        return "passed", f"Result status matched {expected}", []
    return "failed", f"Expected status {expected}, got {result.get('status')}", []


def _eval_evidence_exists(check: dict[str, Any], evidence_records: list[dict[str, Any]]) -> tuple[str, str, list[str]]:
    eid = check.get("id")
    elabel = check.get("label")
    for e in evidence_records:
        if (eid and e.get("id") == eid) or (elabel and e.get("label") == elabel):
            return "passed", f"Found evidence for {eid or elabel}", [str(e.get("id"))]
    return "failed", f"missing_evidence_file for {eid or elabel}", []


def _eval_screenshot_exists(
    check: dict[str, Any], evidence_records: list[dict[str, Any]]
) -> tuple[str, str, list[str]]:
    elabel = check.get("label")
    for e in evidence_records:
        if e.get("type") == "screenshot" and e.get("label") == elabel:
            return "passed", f"Found screenshot evidence label={elabel}", [str(e.get("id"))]
    return "failed", f"missing_evidence_file screenshot with label={elabel}", []


def _eval_assertion_passed(check: dict[str, Any], evidence_records: list[dict[str, Any]]) -> tuple[str, str, list[str]]:
    eid = check.get("id")
    elabel = check.get("label")
    for e in evidence_records:
        if (
            e.get("type") == "assertion"
            and e.get("status") == "passed"
            and ((eid and e.get("id") == eid) or (elabel and e.get("label") == elabel))
        ):
            return "passed", "Assertion passed", [str(e.get("id"))]
    return "failed", "Check failed.", []


def _eval_log_contains(check: dict[str, Any], evidence_records: list[dict[str, Any]]) -> tuple[str, str, list[str]]:
    for e in evidence_records:
        if e.get("type") == "log_excerpt" and str(check.get("text", "")) in str(e.get("preview", "")):
            return "passed", f"Found {check.get('text', '')} in log", [str(e.get("id"))]
    return "failed", "Check failed.", []


def _evaluate_check_inner(
    check_type: str, check: dict[str, Any], result: dict[str, Any], evidence_records: list[dict[str, Any]]
) -> tuple[str, str, list[str]]:
    if check_type == "result_status":
        return _eval_result_status(check, result)
    if check_type == "evidence_exists":
        return _eval_evidence_exists(check, evidence_records)
    if check_type == "screenshot_exists":
        return _eval_screenshot_exists(check, evidence_records)
    if check_type == "assertion_passed":
        return _eval_assertion_passed(check, evidence_records)
    if check_type == "log_contains":
        return _eval_log_contains(check, evidence_records)
    return "failed", "unknown_check_type", []


def _evaluate_check(
    artifact_type: str,
    name: str,
    cp_id: str | None,
    check: dict[str, Any],
    result: dict[str, Any],
    evidence_records: list[dict[str, Any]],
) -> dict[str, Any]:
    check_type = check.get("type", "unknown")
    with span("octowright.artifact.verify.check") as s:
        set_attrs(s, artifact_type=artifact_type, check_type=check_type)

        try:
            status, message, matching_evidence = _evaluate_check_inner(check_type, check, result, evidence_records)
        except Exception as exc:
            status = "failed"
            message = str(exc)
            matching_evidence = []

        set_attrs(s, status=status)
        counter("octowright_artifact_verify_check_total").add(
            1, attributes={"artifact_type": artifact_type, "check_type": check_type, "status": status}
        )

        if status == "failed":
            log.warning(
                "octowright.artifact.verify.check_failed",
                artifact_type=artifact_type,
                name=name,
                run_id=result.get("run_id"),
                critical_point_id=cp_id,
                check_type=check_type,
                status=status,
            )

        return {
            **check,
            "status": status,
            "message": message,
            "evidence": matching_evidence,
        }
