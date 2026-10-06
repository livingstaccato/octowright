# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What ``macro_run_sequence`` reports around its steps.

A failed step's record is what the caller acts on: ``error`` is one line
naming the macro, step, action and cause; ``failure`` is the structured payload
only when the step raised one; ``args_used`` is redacted as that macro's own
view classifies them -- its ``parameter_specs`` included, and every value when
the macro cannot be classified at all. The sequence's input errors are raised
before any step runs and are the message the caller sees.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any

import pytest

from octowright.macros import sequence_steps as ss

R = "<redacted>"
PW = "pw"  # pragma: allowlist secret (synthetic fixture)


def _loader(macros: dict[str, Any]) -> Any:
    def load(name: str) -> Any:
        if name not in macros:
            raise FileNotFoundError(name)
        return macros[name]

    return load


DECLARED = {
    "name": "declared",
    "parameters": ["display", "page"],
    "parameter_specs": {"display": {"sensitive": True}},
    "actions": [{"action": "fill", "selector": "#d", "value": "{{display}}"}],
}


# --- inputs ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("names", "args_list", "message"),
    [
        ("a", None, "names must be a list of macro names"),
        (["a", 1], None, "names must be a list of macro names"),
        (["a"], [1], "args_list must be a list of argument objects (or null), one per name"),
        (["a"], {"x": 1}, "args_list must be a list of argument objects (or null), one per name"),
        (
            ["a"],
            [{}, {}],
            "args_list has 2 entries but names has 1; each args_list entry supplies the macro at the same index, "
            "so the extra ones would never run",
        ),
    ],
)
def test_malformed_sequence_inputs_are_refused(names: Any, args_list: Any, message: str) -> None:
    with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
        ss.resolve_sequence_args(names, args_list)


def test_short_args_list_pads_with_empty_objects() -> None:
    assert ss.resolve_sequence_args(["a", "b", "c"], [{"x": 1}, None]) == [{"x": 1}, {}, {}]
    assert ss.resolve_sequence_args(["a", "b"], None) == [{}, {}]


def test_a_sequence_counts_declared_sensitive_parameters_as_credentials() -> None:
    macros = _loader({"declared": DECLARED, "plain": {"actions": [{"action": "fill", "value": "{{password}}"}]}})
    assert ss.sequence_credential_args(macros, ["declared", "plain", "missing", "declared"]) == frozenset(
        {"display", "password"}
    )


# --- a failed step -----------------------------------------------------------


def _payload(**extra: Any) -> dict[str, Any]:
    return {"macro": "m", "failed_at_step": 2, **extra}


def test_only_a_runtime_error_carries_a_failure_payload() -> None:
    assert ss.macro_failure_details(RuntimeError(_payload())) == _payload()
    assert ss.macro_failure_details(ValueError(_payload())) is None
    assert ss.macro_failure_details(RuntimeError()) is None
    assert ss.macro_failure_details(KeyError()) is None


@pytest.mark.parametrize(
    ("payload", "line"),
    [
        (
            _payload(failed_action={"action": "click"}, original="boom\nsecond"),
            "macro m failed at step 2 (click): boom",
        ),
        (_payload(failed_action={}, original=None), "macro m failed at step 2 (?)"),
        (_payload(failed_action=None, original="  "), "macro m failed at step 2 (?)"),
        (_payload(), "macro m failed at step 2 (?)"),
    ],
)
def test_failure_line(payload: dict[str, Any], line: str) -> None:
    assert ss.failure_line(payload) == line


def test_failed_step_with_a_payload() -> None:
    payload = _payload(failed_action={"action": "fill"}, original="nope", warnings=[1, "careful"])
    assert ss.failed_step("m", RuntimeError(payload), {"a": 1}) == {
        "macro": "m",
        "ok": False,
        "error": "macro m failed at step 2 (fill): nope",
        "args_used": {"a": 1},
        "failure": payload,
        "warnings": ["1", "careful"],
    }


@pytest.mark.parametrize("warnings", ["careful", [], None])
def test_failed_step_reports_only_a_non_empty_warning_list(warnings: Any) -> None:
    payload = _payload(warnings=warnings)
    assert ss.failed_step("m", RuntimeError(payload), {}) == {
        "macro": "m",
        "ok": False,
        "error": "macro m failed at step 2 (?)",
        "args_used": {},
        "failure": payload,
    }


def test_failed_step_without_a_payload() -> None:
    assert ss.failed_step("m", ValueError("bad"), {}) == {"macro": "m", "ok": False, "error": "bad", "args_used": {}}


# --- args_used ---------------------------------------------------------------


def test_args_used_follow_the_macros_declarations() -> None:
    args = {"display": "Ada", "page": "2", "password": PW}
    assert ss.step_args_used(_loader({"declared": DECLARED}), "declared", args) == {
        "display": R,
        "page": "2",
        "password": R,
    }


def test_args_used_of_a_macro_that_never_loaded_are_redacted_by_name() -> None:
    assert ss.step_args_used(_loader({}), "missing", {"display": "Ada", "password": PW}) == {
        "display": "Ada",
        "password": R,
    }


class _Unreadable(Mapping[str, Any]):
    """A loaded macro that cannot be read back, so it cannot be classified."""

    def __getitem__(self, key: str) -> Any:
        raise RuntimeError("unreadable")

    def __iter__(self) -> Iterator[str]:
        return iter(["actions"])

    def __len__(self) -> int:
        return 1


def test_args_used_of_an_unclassifiable_macro_are_all_redacted() -> None:
    assert ss.step_args_used(_loader({"odd": _Unreadable()}), "odd", {"display": "Ada", "page": "2"}) == {
        "display": R,
        "page": R,
    }


# --- the span ----------------------------------------------------------------


class _Span:
    def __init__(self, *, fail: bool = False) -> None:
        self.attributes: dict[str, Any] = {}
        self.statuses: list[Any] = []
        self.fail = fail

    def is_recording(self) -> bool:
        return True

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_attributes(self, attrs: dict[str, Any]) -> None:
        self.attributes.update(attrs)

    def set_status(self, status: Any) -> None:
        if self.fail:
            raise RuntimeError("exporter gone")
        self.statuses.append(status)


def test_a_failed_sequence_marks_its_span_with_fixed_text() -> None:
    from opentelemetry.trace import StatusCode

    span = _Span()
    ss.mark_sequence_span(span, ok=False, stopped_at=1, failed_steps=1)
    [status] = span.statuses
    assert (status.status_code, status.description) == (StatusCode.ERROR, "macro sequence step failed")


def test_a_passing_sequence_sets_no_status() -> None:
    span = _Span()
    ss.mark_sequence_span(span, ok=True, stopped_at=None, failed_steps=0)
    assert span.statuses == []


def test_a_span_that_refuses_a_status_does_not_fail_the_sequence() -> None:
    ss.mark_sequence_span(_Span(fail=True), ok=False, stopped_at=0, failed_steps=1)
