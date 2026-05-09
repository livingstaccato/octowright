# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.macros.substitution.

Targets the 16 surviving mutmut mutants in this module by asserting on:
- normalise_parameters: list/dict/None inputs, indexed param[N] keys
- substitute_in_action: each placeholder substitution + non-string passthrough
- action_kwargs: which keys belong to RECORDING_NOISE_KEYS (stripped)
- strip_non_aria_noise: kind-gate on click/fill/click_by/fill_by; NON_ARIA_NOISE_KEYS removed for others
- substitute / _substitute_value: regex anchor at {{...}}, single-brace ignored,
  KeyError on missing arg, str() coercion of non-string args, recursion into
  dict/list, original input not mutated
- SEMANTIC_LOCATOR_KEYS / NON_ARIA_NOISE_KEYS / RECORDING_NOISE_KEYS membership
"""

from __future__ import annotations

from typing import Any

import pytest

from octowright.macros.substitution import (
    NON_ARIA_NOISE_KEYS,
    RECORDING_NOISE_KEYS,
    SEMANTIC_LOCATOR_KEYS,
    action_kwargs,
    normalise_parameters,
    strip_non_aria_noise,
    substitute,
    substitute_in_action,
)

# ─── constants ────────────────────────────────────────────────────────────────


class TestSemanticLocatorKeys:
    def test_exact_membership(self) -> None:
        """Mutating any tuple element would break the locator-key gate."""
        assert SEMANTIC_LOCATOR_KEYS == ("role", "role_name", "label", "text", "test_id", "role_exact")

    @pytest.mark.parametrize("key", ["role", "role_name", "label", "text", "test_id", "role_exact"])
    def test_each_key_present(self, key: str) -> None:
        """Each individual member must remain in the tuple."""
        assert key in SEMANTIC_LOCATOR_KEYS

    def test_no_extra_keys(self) -> None:
        """Tuple length is exactly 6 — adding/removing would shift behaviour."""
        assert len(SEMANTIC_LOCATOR_KEYS) == 6


class TestNonAriaNoiseKeys:
    def test_exact_membership(self) -> None:
        """The set stripped from non-click/fill kinds is fixed."""
        assert NON_ARIA_NOISE_KEYS == ("role", "role_name", "test_id", "role_exact")

    @pytest.mark.parametrize("key", ["role", "role_name", "test_id", "role_exact"])
    def test_each_key_present(self, key: str) -> None:
        """Every member must be stripped for non-locator kinds."""
        assert key in NON_ARIA_NOISE_KEYS

    def test_label_and_text_not_in_set(self) -> None:
        """label/text are SEMANTIC keys but NOT noise — they survive stripping."""
        assert "label" not in NON_ARIA_NOISE_KEYS
        assert "text" not in NON_ARIA_NOISE_KEYS


class TestRecordingNoiseKeys:
    def test_exact_membership(self) -> None:
        """The recording-noise set is the canonical action-bookkeeping fields."""
        assert RECORDING_NOISE_KEYS == ("action", "ts", "kind", "profile", "instance_id")

    @pytest.mark.parametrize("key", ["action", "ts", "kind", "profile", "instance_id"])
    def test_each_key_present(self, key: str) -> None:
        """Each entry is dropped by action_kwargs."""
        assert key in RECORDING_NOISE_KEYS

    def test_selector_and_value_not_recording_noise(self) -> None:
        """selector/value are domain payload, not recording bookkeeping."""
        assert "selector" not in RECORDING_NOISE_KEYS
        assert "value" not in RECORDING_NOISE_KEYS


# ─── normalise_parameters ─────────────────────────────────────────────────────


class TestNormaliseParameters:
    def test_none_returns_empty_dict(self) -> None:
        """None input → {} (not None, not [])."""
        assert normalise_parameters(None) == {}

    def test_dict_returned_as_is(self) -> None:
        """Dict pass-through preserves identity (not just equality)."""
        d = {"email": "a@x", "pw": "s"}
        assert normalise_parameters(d) is d

    def test_empty_list_returns_empty_dict(self) -> None:
        """Empty list → {} via the dict-comprehension."""
        assert normalise_parameters([]) == {}

    def test_list_indexed_keys(self) -> None:
        """List entries map to params[N] in order."""
        assert normalise_parameters(["a", "b", "c"]) == {
            "params[0]": "a",
            "params[1]": "b",
            "params[2]": "c",
        }

    def test_list_index_starts_at_zero(self) -> None:
        """First key is params[0], not params[1] — mutating start would shift."""
        result = normalise_parameters(["only"])
        assert "params[0]" in result
        assert "params[1]" not in result

    def test_empty_dict_passthrough(self) -> None:
        """Empty dict input returns empty dict (same instance, not None)."""
        d: dict[str, str] = {}
        assert normalise_parameters(d) is d


# ─── substitute_in_action ────────────────────────────────────────────────────


class TestSubstituteInAction:
    def test_string_value_in_map_replaced_with_placeholder(self) -> None:
        """String value matching value_to_name → {{name}}."""
        action = {"action": "fill", "value": "secret"}
        result = substitute_in_action(action, {"secret": "password"})
        assert result == {"action": "fill", "value": "{{password}}"}

    def test_string_value_not_in_map_preserved(self) -> None:
        """Non-matching string passes through unchanged."""
        action = {"action": "click", "selector": "#btn"}
        result = substitute_in_action(action, {"other": "name"})
        assert result == action

    def test_non_string_value_preserved(self) -> None:
        """Numeric/bool/None values bypass the in-check entirely."""
        action = {"action": "click", "timeout_ms": 500, "exact": True, "x": None}
        result = substitute_in_action(action, {"500": "should-not-match"})
        assert result == action

    def test_list_value_preserved_unchanged(self) -> None:
        """Lists aren't recursed into here — the function is shallow."""
        action = {"action": "set_input_files", "paths": ["/tmp/a", "/tmp/b"]}
        result = substitute_in_action(action, {"/tmp/a": "name"})
        assert result == action

    def test_returns_new_dict_not_input(self) -> None:
        """Caller dict not mutated; result is a fresh dict."""
        action = {"action": "fill", "value": "v"}
        result = substitute_in_action(action, {"v": "n"})
        assert result is not action
        assert action == {"action": "fill", "value": "v"}

    def test_placeholder_uses_curly_braces(self) -> None:
        """Output is exactly '{{name}}', not '{name}' or '{{{name}}}'."""
        action = {"value": "x"}
        result = substitute_in_action(action, {"x": "param"})
        assert result["value"] == "{{param}}"

    def test_each_matching_key_substituted(self) -> None:
        """Every matching value in the dict gets a placeholder; others stay."""
        action = {"a": "v1", "b": "v2", "c": "stay"}
        result = substitute_in_action(action, {"v1": "n1", "v2": "n2"})
        assert result == {"a": "{{n1}}", "b": "{{n2}}", "c": "stay"}

    def test_empty_value_to_name_passthrough(self) -> None:
        """Empty mapping → identity-like result (new dict, same content)."""
        action = {"action": "click", "selector": "#x"}
        result = substitute_in_action(action, {})
        assert result == action
        assert result is not action

    def test_empty_string_value_in_map_substituted(self) -> None:
        """Empty string can be a real param value; the in-check matches it."""
        action = {"value": ""}
        result = substitute_in_action(action, {"": "blank"})
        assert result["value"] == "{{blank}}"


# ─── action_kwargs ───────────────────────────────────────────────────────────


class TestActionKwargs:
    def test_strips_each_recording_noise_key(self) -> None:
        """action/ts/kind/profile/instance_id all dropped."""
        action = {
            "action": "click",
            "ts": "2026-01-01T00:00:00Z",
            "kind": "chromium",
            "profile": "alice",
            "instance_id": "abc",
            "selector": "#btn",
            "timeout_ms": 1000,
        }
        result = action_kwargs(action)
        assert result == {"selector": "#btn", "timeout_ms": 1000}

    def test_preserves_domain_payload(self) -> None:
        """Selectors, values, semantic locator keys all survive."""
        action = {
            "action": "fill_by",
            "selector": "#x",
            "value": "v",
            "label": "Email",
            "role": "textbox",
            "role_name": "Email",
            "test_id": "tid",
            "role_exact": True,
            "text": "fragment",
            "timeout_ms": 500,
        }
        result = action_kwargs(action)
        # Only 'action' should be stripped from this set.
        assert "action" not in result
        for k in ("selector", "value", "label", "role", "role_name", "test_id", "role_exact", "text", "timeout_ms"):
            assert k in result

    def test_returns_new_dict(self) -> None:
        """Caller dict isn't mutated."""
        action = {"action": "click", "selector": "#x"}
        result = action_kwargs(action)
        assert result is not action

    def test_empty_input_yields_empty(self) -> None:
        """Empty action → empty kwargs."""
        assert action_kwargs({}) == {}

    def test_only_noise_keys_yields_empty(self) -> None:
        """Action containing only noise keys → empty result."""
        result = action_kwargs({"action": "click", "ts": "x", "kind": "k", "profile": "p", "instance_id": "i"})
        assert result == {}


# ─── strip_non_aria_noise ────────────────────────────────────────────────────


class TestStripNonAriaNoise:
    @pytest.mark.parametrize("kind", ["click", "fill", "click_by", "fill_by"])
    def test_locator_kinds_keep_all_keys(self, kind: str) -> None:
        """The {click, fill, click_by, fill_by} branch keeps the full kwargs."""
        kwargs = {"selector": "#x", "role": "button", "role_name": "OK", "test_id": "tid", "role_exact": True}
        result = strip_non_aria_noise(kind, kwargs)
        assert result == kwargs

    @pytest.mark.parametrize("kind", ["click", "fill", "click_by", "fill_by"])
    def test_locator_kinds_return_copy(self, kind: str) -> None:
        """Result is a shallow copy, not the original kwargs dict."""
        kwargs = {"selector": "#x"}
        result = strip_non_aria_noise(kind, kwargs)
        assert result is not kwargs

    def test_non_locator_kind_strips_each_noise_key(self) -> None:
        """For non-locator kinds, every NON_ARIA_NOISE_KEYS entry is removed."""
        kwargs = {"role": "x", "role_name": "y", "test_id": "z", "role_exact": True, "selector": "#s"}
        result = strip_non_aria_noise("navigate", kwargs)
        assert result == {"selector": "#s"}

    def test_non_locator_kind_preserves_label_and_text(self) -> None:
        """label/text are NOT in NON_ARIA_NOISE_KEYS — survive stripping."""
        kwargs = {"role": "x", "label": "L", "text": "T"}
        result = strip_non_aria_noise("expect_text", kwargs)
        assert "label" in result
        assert "text" in result
        assert "role" not in result

    def test_non_locator_kind_returns_copy_not_input(self) -> None:
        """Caller's kwargs dict isn't mutated."""
        kwargs = {"role": "x", "selector": "#s"}
        original = dict(kwargs)
        result = strip_non_aria_noise("navigate", kwargs)
        assert kwargs == original
        assert result is not kwargs

    def test_non_locator_kind_with_no_noise_keys(self) -> None:
        """No noise keys present → kwargs returned unchanged."""
        kwargs = {"selector": "#x", "value": "v"}
        result = strip_non_aria_noise("type", kwargs)
        assert result == kwargs

    def test_unknown_kind_still_strips_noise(self) -> None:
        """Any kind not in the locator set goes through the strip path."""
        kwargs = {"role": "x", "role_name": "y", "selector": "#s"}
        result = strip_non_aria_noise("totally_unknown_kind", kwargs)
        assert "role" not in result
        assert "role_name" not in result
        assert result["selector"] == "#s"


# ─── substitute (and _substitute_value) ──────────────────────────────────────


class TestSubstitute:
    def test_empty_actions_returns_empty(self) -> None:
        """Empty input list → empty result."""
        assert substitute([], {"any": "arg"}) == []

    def test_empty_args_with_no_placeholders(self) -> None:
        """No placeholders + no args → identity-like behaviour."""
        actions: list[dict[str, Any]] = [{"action": "click", "selector": "#x"}]
        result = substitute(actions, {})
        assert result == actions

    def test_double_brace_placeholder_substituted(self) -> None:
        """{{name}} matches and is replaced."""
        actions: list[dict[str, Any]] = [{"action": "fill", "value": "{{email}}"}]
        result = substitute(actions, {"email": "me@x"})
        assert result[0]["value"] == "me@x"

    def test_single_brace_placeholder_not_matched(self) -> None:
        """{name} (single brace) is NOT a placeholder; stays literal."""
        actions: list[dict[str, Any]] = [{"action": "fill", "value": "{email}"}]
        result = substitute(actions, {"email": "me@x"})
        assert result[0]["value"] == "{email}"

    def test_triple_brace_treats_inner_lbrace_as_part_of_name(self) -> None:
        """The regex `[^}]+` is greedy and lets `{` into the captured name —
        `{{{email}}}` captures `{email`, which fails the args lookup."""
        actions: list[dict[str, Any]] = [{"action": "fill", "value": "{{{email}}}"}]
        with pytest.raises(KeyError) as exc_info:
            substitute(actions, {"email": "me@x"})
        assert "{email" in str(exc_info.value)

    def test_missing_placeholder_raises_keyerror(self) -> None:
        """A {{x}} with no matching arg raises KeyError including the name and available list."""
        actions: list[dict[str, Any]] = [{"action": "fill", "value": "{{missing}}"}]
        with pytest.raises(KeyError) as exc_info:
            substitute(actions, {"other": "v"})
        msg = str(exc_info.value)
        assert "missing" in msg
        assert "other" in msg

    def test_int_arg_coerced_to_string(self) -> None:
        """Non-string args go through str()."""
        actions: list[dict[str, Any]] = [{"action": "fill", "value": "{{count}}"}]
        result = substitute(actions, {"count": 42})
        assert result[0]["value"] == "42"

    def test_none_arg_coerced_to_none_literal(self) -> None:
        """None becomes literal 'None' (str(None))."""
        actions: list[dict[str, Any]] = [{"action": "fill", "value": "{{x}}"}]
        result = substitute(actions, {"x": None})
        assert result[0]["value"] == "None"

    def test_list_arg_coerced_via_str(self) -> None:
        """Lists become their repr (str([...]))."""
        actions: list[dict[str, Any]] = [{"action": "fill", "value": "{{xs}}"}]
        result = substitute(actions, {"xs": [1, 2, 3]})
        assert result[0]["value"] == "[1, 2, 3]"

    def test_multiple_placeholders_in_one_value(self) -> None:
        """Two placeholders in the same string both substitute."""
        actions: list[dict[str, Any]] = [{"action": "fill", "value": "{{a}}-{{b}}"}]
        result = substitute(actions, {"a": "A", "b": "B"})
        assert result[0]["value"] == "A-B"

    def test_recurses_into_nested_dict(self) -> None:
        """Dict values get recursed: nested {{x}} substituted."""
        actions: list[dict[str, Any]] = [{"action": "x", "headers": {"Authorization": "Bearer {{token}}"}}]
        result = substitute(actions, {"token": "abc"})
        assert result[0]["headers"]["Authorization"] == "Bearer abc"

    def test_recurses_into_nested_list(self) -> None:
        """List values get recursed element-wise."""
        actions: list[dict[str, Any]] = [{"action": "set_input_files", "paths": ["/{{dir}}/a", "/{{dir}}/b"]}]
        result = substitute(actions, {"dir": "tmp"})
        assert result[0]["paths"] == ["/tmp/a", "/tmp/b"]

    def test_non_string_non_container_passthrough(self) -> None:
        """Bools, ints, None at the leaf level pass through unchanged."""
        actions: list[dict[str, Any]] = [{"action": "click", "exact": True, "timeout_ms": 250, "marker": None}]
        result = substitute(actions, {})
        assert result[0]["exact"] is True
        assert result[0]["timeout_ms"] == 250
        assert result[0]["marker"] is None

    def test_input_actions_not_mutated(self) -> None:
        """Deep-copy preserves the caller's list of actions."""
        actions: list[dict[str, Any]] = [{"action": "fill", "value": "{{x}}"}]
        snapshot = [dict(a) for a in actions]
        substitute(actions, {"x": "v"})
        assert actions == snapshot

    def test_input_nested_dict_not_mutated(self) -> None:
        """Deep-copy reaches nested dicts — caller's nested data is intact."""
        nested = {"Authorization": "Bearer {{token}}"}
        actions: list[dict[str, Any]] = [{"action": "x", "headers": nested}]
        substitute(actions, {"token": "abc"})
        assert nested == {"Authorization": "Bearer {{token}}"}

    def test_keyerror_message_includes_available_args_list(self) -> None:
        """KeyError message includes 'available:' and the list of args."""
        actions: list[dict[str, Any]] = [{"action": "fill", "value": "{{x}}"}]
        with pytest.raises(KeyError) as exc_info:
            substitute(actions, {"y": "v", "z": "w"})
        msg = str(exc_info.value)
        assert "available" in msg
        assert "y" in msg
        assert "z" in msg

    def test_placeholder_with_special_chars_in_name_works(self) -> None:
        """The regex matches anything-but-} inside the braces."""
        actions: list[dict[str, Any]] = [{"action": "fill", "value": "{{params[0]}}"}]
        result = substitute(actions, {"params[0]": "indexed"})
        assert result[0]["value"] == "indexed"

    def test_returns_new_outer_list(self) -> None:
        """Outer list is freshly constructed per call."""
        actions: list[dict[str, Any]] = []
        result = substitute(actions, {})
        assert result is not actions
