# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Property-based tests for the project's user-facing parsers.

Surfaces covered:
  - load_yaml_scenario: round-trip from arbitrary participant lists
  - lint_macro: never raises, always returns a list of Issue objects
  - _credential_cmd_needs_shell: structural correctness (no false negatives)
"""

from __future__ import annotations

import yaml
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from octowright.defaults import SUPPORTED_KINDS
from octowright.macros.lint import lint_macro
from octowright.personas import _credential_cmd_needs_shell
from octowright.scenarios import load_yaml_scenario

# Persona names: simple slug-friendly tokens. Keep the strategy narrow so the
# test focuses on parser logic, not slug edge cases (those have their own tests).
_persona_text = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="-_"),
    min_size=1,
    max_size=20,
)


@st.composite
def _participant(draw: st.DrawFn) -> dict[str, object]:
    return {
        "persona": draw(_persona_text),
        "kind": draw(st.sampled_from(SUPPORTED_KINDS)),
        "role": draw(st.sampled_from(["player", "monitor", "spectator", "participant"])),
    }


@settings(suppress_health_check=[HealthCheck.too_slow], max_examples=80)
@given(participants=st.lists(_participant(), min_size=0, max_size=8))
def test_load_yaml_scenario_round_trips_unique_participant_pairs(participants: list[dict]) -> None:
    """A scenario YAML built from arbitrary participant lists should either
    parse cleanly or raise ValueError on a duplicate (persona, kind) pair.
    No other exception types are acceptable from this entry point."""
    doc = {"name": "hypo-scenario", "participants": participants}
    yaml_text = yaml.safe_dump(doc, sort_keys=False)

    seen: set[tuple[str, str]] = set()
    expected_dup = False
    for p in participants:
        key = (p["persona"], p["kind"])
        if key in seen:
            expected_dup = True
            break
        seen.add(key)

    try:
        scenario = load_yaml_scenario(yaml_text, "hypo-scenario")
    except ValueError as exc:
        # Only acceptable failure: duplicate participant detection.
        assert expected_dup, f"unexpected ValueError on unique participants: {exc}"
        return
    assert not expected_dup, "duplicate participants were not detected"
    assert len(scenario.participants) == len(participants)
    for declared, parsed in zip(participants, scenario.participants, strict=True):
        assert parsed.persona == declared["persona"]
        assert parsed.kind == declared["kind"]


# ---------------------------------------------------------------------------
# lint_macro must never raise — it's a static-analysis pass on user input.
# ---------------------------------------------------------------------------


_action_text = st.text(min_size=0, max_size=30)


@st.composite
def _macro_action(draw: st.DrawFn) -> dict[str, object]:
    """Generate a plausibly-shaped macro action; lint should handle missing
    fields, unknown action types, and nested junk without raising."""
    keys = draw(
        st.lists(st.sampled_from(["action", "selector", "text", "url", "value", "name"]), min_size=0, max_size=4)
    )
    return {key: draw(_action_text) for key in keys}


@settings(suppress_health_check=[HealthCheck.too_slow], max_examples=80)
@given(actions=st.lists(_macro_action(), min_size=0, max_size=10))
def test_lint_macro_never_raises_on_arbitrary_actions(actions: list[dict]) -> None:
    """lint_macro must be total: any dict-shaped input parses to a list of Issue."""
    macro = {"name": "hypo", "actions": actions}
    issues = lint_macro(macro)
    assert isinstance(issues, list)
    for issue in issues:
        assert hasattr(issue, "severity")
        assert hasattr(issue, "code")
        assert hasattr(issue, "message")


# ---------------------------------------------------------------------------
# _credential_cmd_needs_shell: structural invariants
# ---------------------------------------------------------------------------


@settings(suppress_health_check=[HealthCheck.too_slow], max_examples=120)
@given(cmd=st.text(min_size=0, max_size=80))
def test_credential_cmd_needs_shell_is_total(cmd: str) -> None:
    """The shell-classifier must always return (bool, list) — never raise."""
    needs_shell, parsed = _credential_cmd_needs_shell(cmd)
    assert isinstance(needs_shell, bool)
    assert isinstance(parsed, list)
    if not needs_shell:
        # Argv form must be non-empty and contain no shell operator tokens.
        assert all(isinstance(tok, str) for tok in parsed)
        assert not any(tok in {"|", "&", ";", ">", "<", "(", ")", "`"} for tok in parsed)


@given(cmd=st.from_regex(r"[a-zA-Z][a-zA-Z0-9_/-]*( [a-zA-Z0-9_/.:-]+)*", fullmatch=True))
@settings(suppress_health_check=[HealthCheck.too_slow], max_examples=80)
def test_credential_cmd_simple_invocations_are_argv_form(cmd: str) -> None:
    """Plain `binary arg arg` invocations must classify as argv-form (no shell)."""
    needs_shell, parsed = _credential_cmd_needs_shell(cmd)
    assert needs_shell is False, f"plain cmd flagged shell: {cmd!r} -> {parsed!r}"
    assert parsed[0] == cmd.split()[0]
