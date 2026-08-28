# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Direct coverage for the deterministic check evaluators.

`artifacts/verification.py` joined `[tool.mutmut] source_paths` on 2026-08-27,
immediately after a verification-idempotency bug lived here undetected: the
caller (`macros/artifacts.py`) was mutated while the module it delegates every
check evaluation to was not, so the defect sat in the one file mutation testing
could not see.

The first run over it scored 457 mutants and killed 151. Three of the five
check types the verifier supports -- `assertion_passed`, `log_contains` and
`evidence_exists` -- came back with **no covering test at all**: 118 mutants
that no assertion anywhere could observe. They were reachable only through a
full plan/run/verify cycle that no test drove with those check types, so each
one could be inverted to always-pass with the whole suite green.

An always-passing check is the worst failure mode this module has. It does not
error, it does not warn -- it silently reports that a claim was verified when
nothing verified it. Each evaluator is a pure function, so it is tested here
directly rather than through the artifact store.
"""

from __future__ import annotations

from typing import Any

import pytest

from octowright.artifacts.verification import (
    _eval_assertion_passed,
    _eval_evidence_exists,
    _eval_log_contains,
    _eval_result_status,
    _eval_screenshot_exists,
    _evaluate_check_inner,
    apply_verification_rollup,
)


def _evidence(**overrides: Any) -> dict[str, Any]:
    base = {"id": "ev_001", "type": "assertion", "label": "after", "status": "passed"}
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# evidence_exists -- matches on id OR label
# ---------------------------------------------------------------------------


def test_evidence_exists_matches_by_id() -> None:
    status, _msg, matched = _eval_evidence_exists({"id": "ev_001"}, [_evidence()])
    assert status == "passed"
    assert matched == ["ev_001"]


def test_evidence_exists_matches_by_label() -> None:
    status, _msg, matched = _eval_evidence_exists({"label": "after"}, [_evidence()])
    assert status == "passed"
    assert matched == ["ev_001"]


def test_evidence_exists_fails_when_nothing_matches() -> None:
    status, message, matched = _eval_evidence_exists({"id": "ev_999"}, [_evidence()])
    assert status == "failed"
    assert matched == []
    assert "ev_999" in message


def test_evidence_exists_fails_against_an_empty_record_set() -> None:
    """The always-pass mutant's most obvious shape: no evidence at all."""
    status, _msg, matched = _eval_evidence_exists({"id": "ev_001"}, [])
    assert status == "failed"
    assert matched == []


def test_evidence_exists_ignores_a_record_matching_neither_field() -> None:
    """Pins that both arms of the `or` are real tests, not a short circuit.

    A record whose id matches the check's *label* (and vice versa) must not
    count -- the fields are not interchangeable.
    """
    status, _msg, _matched = _eval_evidence_exists({"id": "after"}, [_evidence(id="ev_001", label="after")])
    assert status == "failed"


# ---------------------------------------------------------------------------
# assertion_passed -- three conditions, all required
# ---------------------------------------------------------------------------


def test_assertion_passed_accepts_a_passing_assertion_record() -> None:
    status, _msg, matched = _eval_assertion_passed({"id": "ev_001"}, [_evidence()])
    assert status == "passed"
    assert matched == ["ev_001"]


def test_assertion_passed_matches_by_label_too() -> None:
    status, _msg, matched = _eval_assertion_passed({"label": "after"}, [_evidence()])
    assert status == "passed"
    assert matched == ["ev_001"]


def test_assertion_passed_rejects_a_failing_assertion() -> None:
    """The whole point of this check type: a recorded failure is not a pass.

    Drop the `status == "passed"` clause and this check reports that an
    assertion held whenever an assertion merely *ran*.
    """
    status, _msg, matched = _eval_assertion_passed({"id": "ev_001"}, [_evidence(status="failed")])
    assert status == "failed"
    assert matched == []


def test_assertion_passed_rejects_a_non_assertion_record() -> None:
    """A passing screenshot is not a passing assertion."""
    status, _msg, _matched = _eval_assertion_passed({"id": "ev_001"}, [_evidence(type="screenshot")])
    assert status == "failed"


def test_assertion_passed_rejects_a_record_with_a_different_identity() -> None:
    status, _msg, _matched = _eval_assertion_passed({"id": "ev_002"}, [_evidence()])
    assert status == "failed"


# ---------------------------------------------------------------------------
# log_contains -- substring against a log_excerpt preview
# ---------------------------------------------------------------------------


def _log(preview: str) -> dict[str, Any]:
    return {"id": "ev_010", "type": "log_excerpt", "preview": preview}


def test_log_contains_finds_a_substring_of_the_preview() -> None:
    status, _msg, matched = _eval_log_contains({"text": "Timeout"}, [_log("... Timeout exceeded ...")])
    assert status == "passed"
    assert matched == ["ev_010"]


def test_log_contains_fails_when_the_text_is_absent() -> None:
    status, _msg, matched = _eval_log_contains({"text": "Timeout"}, [_log("all good")])
    assert status == "failed"
    assert matched == []


def test_log_contains_only_reads_log_excerpt_records() -> None:
    """A matching preview on the wrong record type must not count."""
    status, _msg, _matched = _eval_log_contains(
        {"text": "Timeout"}, [{"id": "ev_011", "type": "screenshot", "preview": "Timeout"}]
    )
    assert status == "failed"


def test_log_contains_is_case_sensitive() -> None:
    """Pins the comparison rather than leaving casing an accident."""
    status, _msg, _matched = _eval_log_contains({"text": "timeout"}, [_log("Timeout exceeded")])
    assert status == "failed"


def test_log_contains_with_no_text_matches_any_log_excerpt() -> None:
    """Documents a sharp edge rather than asserting it is desirable.

    `check.get("text", "")` defaults to the empty string, and every string
    contains it -- so a `log_contains` check that forgets its `text` field
    passes against any log excerpt at all. Worth knowing before writing one.
    """
    status, _msg, matched = _eval_log_contains({}, [_log("anything")])
    assert status == "passed"
    assert matched == ["ev_010"]


# ---------------------------------------------------------------------------
# The two evaluators that already had coverage, pinned at the boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expected", "actual", "want"),
    [("ok", "ok", "passed"), ("ok", "failed", "failed"), ("failed", "failed", "passed")],
)
def test_result_status_compares_expected_against_actual(expected: str, actual: str, want: str) -> None:
    status, _msg, _matched = _eval_result_status({"status": expected}, {"status": actual})
    assert status == want


def test_screenshot_exists_requires_both_the_type_and_the_label() -> None:
    shot = {"id": "ev_020", "type": "screenshot", "label": "after"}
    assert _eval_screenshot_exists({"label": "after"}, [shot])[0] == "passed"
    assert _eval_screenshot_exists({"label": "before"}, [shot])[0] == "failed"
    assert _eval_screenshot_exists({"label": "after"}, [{**shot, "type": "log_excerpt"}])[0] == "failed"


# ---------------------------------------------------------------------------
# apply_verification_rollup -- the idempotency fix's load-bearing half
# ---------------------------------------------------------------------------


def test_rollup_carries_only_the_verdict_and_the_run() -> None:
    declared = [{"id": "cp1", "description": "d", "checks": [{"type": "result_status", "status": "ok"}]}]
    verified = [{"id": "cp1", "status": "passed", "last_verified_run": "run_0001", "checks": [{"type": "x"}]}]

    rolled = apply_verification_rollup(declared, verified)

    assert rolled[0]["status"] == "passed"
    assert rolled[0]["last_verified_run"] == "run_0001"
    # The declaration survives verbatim -- this is the whole point.
    assert rolled[0]["checks"] == [{"type": "result_status", "status": "ok"}]
    assert rolled[0]["description"] == "d"


def test_rollup_does_not_mutate_the_declarations_it_is_given() -> None:
    declared = [{"id": "cp1", "status": "unknown", "checks": [{"type": "result_status", "status": "ok"}]}]
    apply_verification_rollup(declared, [{"id": "cp1", "status": "passed", "last_verified_run": "run_0001"}])
    assert declared[0]["status"] == "unknown"


def test_rollup_defaults_a_verdictless_entry_to_unknown() -> None:
    rolled = apply_verification_rollup([{"id": "cp1"}], [{"id": "cp1"}])
    assert rolled[0]["status"] == "unknown"
    assert rolled[0]["last_verified_run"] is None


def test_rollup_refuses_mismatched_lengths() -> None:
    """The 1:1 pairing is an invariant of the one call site, so state it.

    `zip(strict=True)` turns a future refactor that decouples the two lists
    into an immediate error rather than a silently truncated write-back that
    leaves later critical points holding a stale verdict.
    """
    with pytest.raises(ValueError):
        apply_verification_rollup([{"id": "cp1"}, {"id": "cp2"}], [{"id": "cp1", "status": "passed"}])


# ---------------------------------------------------------------------------
# Gaps the 2026-08-27 mutation run left open after the first pass
#
# The tests above raised verification.py from 151/457 killed to 261/457, but a
# specific set of survivors showed the coverage was shaped wrong rather than
# thin: every case tested happened to give the same verdict under the mutation
# as without it. These target exactly those.
# ---------------------------------------------------------------------------


def test_evidence_exists_rejects_a_label_that_does_not_match() -> None:
    """A *wrong* label must fail, not merely a missing one.

    `(elabel and e.get("label") == elabel)` flipped to `or` still passes every
    earlier test, because they either omit the label or omit the record. With
    `or`, any truthy label short-circuits the clause to True and the check
    matches whatever evidence happens to be present -- an always-pass check.
    """
    status, _msg, matched = _eval_evidence_exists({"label": "nope"}, [_evidence(label="after")])
    assert status == "failed"
    assert matched == []


def test_assertion_passed_rejects_a_label_that_does_not_match() -> None:
    """Same `and`/`or` short circuit, in the assertion evaluator."""
    status, _msg, matched = _eval_assertion_passed({"label": "nope"}, [_evidence(label="after")])
    assert status == "failed"
    assert matched == []


def test_screenshot_exists_returns_the_matching_evidence_id() -> None:
    """The id is the payload, not incidental.

    Asserting only the status let the returned id be replaced with `str(None)`
    -- so a passing check would cite evidence "None", and a caller resolving
    that id would find nothing.
    """
    shot = {"id": "ev_020", "type": "screenshot", "label": "after"}
    _status, _msg, matched = _eval_screenshot_exists({"label": "after"}, [shot])
    assert matched == ["ev_020"]


# --- dispatch: a check must reach ITS OWN evaluator -------------------------
#
# `_evaluate_check_inner` is a chain of `check_type == "..."` tests. Flipping
# any one to `!=` routes a check to the wrong evaluator, and in every case
# tested above the wrong evaluator happened to return the same verdict. Each
# test below is built so the two evaluators disagree.


def test_assertion_check_is_not_evaluated_as_evidence_exists() -> None:
    """A recorded FAILURE must fail, even though the record exists.

    `evidence_exists` asks only "is there a record with this id" and would say
    passed. `assertion_passed` additionally requires status == "passed". If the
    dispatch sends this to the wrong evaluator, a failed assertion is reported
    as a verified claim.
    """
    failed_assertion = _evidence(id="ev_001", type="assertion", status="failed")
    status, _msg, _matched = _evaluate_check_inner("assertion_passed", {"id": "ev_001"}, {}, [failed_assertion])
    assert status == "failed"


def test_log_check_is_not_evaluated_as_an_assertion() -> None:
    """A matching log excerpt passes; the assertion evaluator would reject it."""
    status, _msg, _matched = _evaluate_check_inner(
        "log_contains", {"text": "Timeout"}, {}, [_log("... Timeout exceeded ...")]
    )
    assert status == "passed"


def test_an_unknown_check_type_is_named_as_such() -> None:
    """Falling off the end of the chain must say *why* it failed.

    Both the unknown-type path and a failing `log_contains` return "failed", so
    only the message distinguishes them -- and an operator debugging a typo in
    `type:` needs to be told the type was unrecognised rather than that some
    check merely did not match.
    """
    status, message, _matched = _evaluate_check_inner("no_such_check", {}, {}, [])
    assert status == "failed"
    assert message == "unknown_check_type"
