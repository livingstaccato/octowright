# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.macros.repair.

Targets the 70 surviving mutmut mutants in this module by asserting on:
- exact field shapes of MacroRepairSuggestion (every key populated)
- selector/kind classification (which actions get a suggestion vs. don't)
- replacement_preview formatting precedence (role_name > label > text > test_id)
- prompt template text exactly (idx and selector both repr'd)
- source = "stored_heuristic" pin
- original_action is deep-copied (mutate-after-call doesn't bleed)
- empty / no-candidate macros yield empty suggestion lists.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.macros.repair import (
    repair_preview,
    replacement_preview,
    semantic_replacement,
    suggest_fix,
)

SEMANTIC_KEYS = ("role", "role_name", "role_exact", "label", "text", "test_id")


# ─── semantic_replacement ────────────────────────────────────────────────────


class TestSemanticReplacementKindGate:
    def test_returns_none_for_unsupported_kind(self) -> None:
        """Mutating the {'click', 'fill'} set would let other kinds through."""
        for kind in ("navigate", "press_key", "wait_for", "screenshot", "evaluate", "type"):
            assert (
                semantic_replacement({"action": kind, "selector": ".x", "label": "hi"}, semantic_keys=SEMANTIC_KEYS)
                is None
            )

    def test_returns_none_when_kind_missing(self) -> None:
        """No 'action' key falls into the disallowed branch."""
        assert semantic_replacement({"selector": ".x", "label": "hi"}, semantic_keys=SEMANTIC_KEYS) is None

    def test_returns_none_for_click_without_selector(self) -> None:
        """The `not action.get('selector')` guard."""
        assert semantic_replacement({"action": "click", "label": "hi"}, semantic_keys=SEMANTIC_KEYS) is None

    def test_returns_none_for_click_with_empty_selector(self) -> None:
        """Empty-string selector is falsy and must be rejected."""
        assert (
            semantic_replacement({"action": "click", "selector": "", "label": "hi"}, semantic_keys=SEMANTIC_KEYS)
            is None
        )


class TestSemanticReplacementSemanticGate:
    def test_returns_none_when_no_semantic_keys_present(self) -> None:
        """If the action has no semantic-locator data at all, no replacement."""
        assert semantic_replacement({"action": "click", "selector": "#x"}, semantic_keys=SEMANTIC_KEYS) is None

    def test_skips_semantic_key_when_value_is_none(self) -> None:
        """`action[k] is not None` filter: explicit None should not contribute."""
        assert (
            semantic_replacement(
                {"action": "click", "selector": "#x", "label": None, "role": None},
                semantic_keys=SEMANTIC_KEYS,
            )
            is None
        )

    def test_only_listed_semantic_keys_carried_over(self) -> None:
        """A key not in semantic_keys is dropped even if present on the action."""
        result = semantic_replacement(
            {"action": "click", "selector": "#x", "label": "OK", "extraneous": "drop me"},
            semantic_keys=SEMANTIC_KEYS,
        )
        assert result is not None
        assert "extraneous" not in result


class TestSemanticReplacementShape:
    def test_click_replacement_action_name_is_click_by(self) -> None:
        """Mutating the f"{kind}_by" suffix would rename the replacement."""
        result = semantic_replacement({"action": "click", "selector": "#x", "label": "OK"}, semantic_keys=SEMANTIC_KEYS)
        assert result is not None
        assert result["action"] == "click_by"

    def test_fill_replacement_action_name_is_fill_by(self) -> None:
        """Same f-string, fill side."""
        result = semantic_replacement(
            {"action": "fill", "selector": "#x", "label": "Email", "value": "v"}, semantic_keys=SEMANTIC_KEYS
        )
        assert result is not None
        assert result["action"] == "fill_by"

    def test_click_replacement_carries_each_semantic_key(self) -> None:
        """Each key in semantic_keys with a non-None value flows through."""
        action = {
            "action": "click",
            "selector": "#x",
            "role": "button",
            "role_name": "Save",
            "role_exact": True,
            "label": "L",
            "text": "T",
            "test_id": "tid",
        }
        result = semantic_replacement(action, semantic_keys=SEMANTIC_KEYS)
        assert result == {
            "action": "click_by",
            "role": "button",
            "role_name": "Save",
            "role_exact": True,
            "label": "L",
            "text": "T",
            "test_id": "tid",
        }

    def test_click_replacement_does_not_carry_value(self) -> None:
        """value is fill-only; click_by must not get it."""
        result = semantic_replacement(
            {"action": "click", "selector": "#x", "label": "L", "value": "must-not-carry"}, semantic_keys=SEMANTIC_KEYS
        )
        assert result is not None
        assert "value" not in result

    def test_fill_replacement_carries_value(self) -> None:
        """The `if kind == "fill" and "value" in action` branch."""
        result = semantic_replacement(
            {"action": "fill", "selector": "#x", "label": "L", "value": "secret"}, semantic_keys=SEMANTIC_KEYS
        )
        assert result is not None
        assert result["value"] == "secret"

    def test_fill_replacement_omits_value_when_action_has_none(self) -> None:
        """No 'value' key on the action → not added to replacement."""
        result = semantic_replacement({"action": "fill", "selector": "#x", "label": "L"}, semantic_keys=SEMANTIC_KEYS)
        assert result is not None
        assert "value" not in result

    def test_timeout_ms_carried_when_present(self) -> None:
        """The `if "timeout_ms" in action` branch."""
        result = semantic_replacement(
            {"action": "click", "selector": "#x", "label": "L", "timeout_ms": 250}, semantic_keys=SEMANTIC_KEYS
        )
        assert result is not None
        assert result["timeout_ms"] == 250

    def test_timeout_ms_not_added_when_absent(self) -> None:
        """No timeout_ms on action → not in replacement."""
        result = semantic_replacement({"action": "click", "selector": "#x", "label": "L"}, semantic_keys=SEMANTIC_KEYS)
        assert result is not None
        assert "timeout_ms" not in result

    def test_timeout_ms_zero_is_carried(self) -> None:
        """0 is falsy but `in` test still passes; mutating to truthy would skip 0."""
        result = semantic_replacement(
            {"action": "click", "selector": "#x", "label": "L", "timeout_ms": 0}, semantic_keys=SEMANTIC_KEYS
        )
        assert result is not None
        assert result["timeout_ms"] == 0

    def test_semantic_keys_subset_filters(self) -> None:
        """Only keys named in the tuple are carried."""
        result = semantic_replacement(
            {"action": "click", "selector": "#x", "label": "L", "test_id": "tid"},
            semantic_keys=("label",),
        )
        assert result == {"action": "click_by", "label": "L"}


# ─── replacement_preview ─────────────────────────────────────────────────────


class TestReplacementPreviewClickBy:
    @pytest.mark.parametrize(
        "fields, expected_target",
        [
            ({"role_name": "Save", "label": "L", "text": "T", "test_id": "tid"}, "'Save'"),
            ({"label": "L", "text": "T", "test_id": "tid"}, "'L'"),
            ({"text": "T", "test_id": "tid"}, "'T'"),
            ({"test_id": "tid"}, "'tid'"),
        ],
    )
    def test_target_precedence_role_name_over_label_over_text_over_test_id(
        self, fields: dict[str, Any], expected_target: str
    ) -> None:
        """Mutating any `or` chain order would change which field wins."""
        action = {"action": "click_by", **fields}
        assert replacement_preview(action) == f"Click by {expected_target}"

    def test_returns_locator_fallback_when_no_target(self) -> None:
        """All four target fields missing → fallback string."""
        assert replacement_preview({"action": "click_by"}) == "Click by semantic locator"

    def test_uses_repr_quoting_for_target(self) -> None:
        """!r quoting; mutating to str would drop quotes."""
        assert "'Save'" in replacement_preview({"action": "click_by", "role_name": "Save"})


class TestReplacementPreviewFillBy:
    def test_includes_target_and_value_when_target_present(self) -> None:
        """Both repr'd; mutating either would skip the quotes."""
        assert (
            replacement_preview({"action": "fill_by", "label": "Email", "value": "me@x"})
            == "Fill by 'Email' with 'me@x'"
        )

    def test_value_default_empty_string_when_missing(self) -> None:
        """value default '' triggers empty-quotes."""
        assert replacement_preview({"action": "fill_by", "label": "Email"}) == "Fill by 'Email' with ''"

    def test_target_precedence_role_name_first(self) -> None:
        """fill_by precedence chain mirrors click_by."""
        action = {"action": "fill_by", "role_name": "Save", "label": "L", "text": "T", "test_id": "tid", "value": "v"}
        assert replacement_preview(action) == "Fill by 'Save' with 'v'"

    def test_fallback_when_no_target_with_value(self) -> None:
        """No target field + a value → semantic-locator phrasing."""
        assert replacement_preview({"action": "fill_by", "value": "abc"}) == "Fill by semantic locator with 'abc'"

    def test_fallback_when_no_target_no_value(self) -> None:
        """No target, no value → 'Fill by semantic locator with '''."""
        assert replacement_preview({"action": "fill_by"}) == "Fill by semantic locator with ''"


class TestReplacementPreviewFallthrough:
    def test_other_kinds_use_summarize_action(self) -> None:
        """Kinds other than click_by/fill_by route to summarize_action."""
        assert (
            replacement_preview({"action": "navigate", "url": "https://example.com"})
            == "Navigate to https://example.com"
        )

    def test_unknown_kind_via_summarize_fallback(self) -> None:
        """summarize_action's own fallback handles unknown kinds."""
        assert replacement_preview({"action": "weird_thing"}) == "Perform weird_thing action"


# ─── suggest_fix ─────────────────────────────────────────────────────────────


class TestSuggestFix:
    @pytest.mark.anyio
    async def test_returns_none_without_selector(self) -> None:
        """First guard: no selector means no prompt to build."""
        session = MagicMock()
        result = await suggest_fix(session, {"action": "click"})
        assert result is None

    @pytest.mark.anyio
    async def test_returns_none_when_selector_empty_string(self) -> None:
        """Empty string is falsy."""
        session = MagicMock()
        result = await suggest_fix(session, {"action": "click", "selector": ""})
        assert result is None

    @pytest.mark.anyio
    async def test_returns_none_when_snapshot_raises(self) -> None:
        """The except Exception swallow path returns None, not the exception."""
        session = MagicMock()
        session.snapshot = AsyncMock(side_effect=RuntimeError("nope"))
        result = await suggest_fix(session, {"action": "click", "selector": "#x"})
        assert result is None

    @pytest.mark.anyio
    async def test_includes_summary_selector_and_aria_in_prompt(self) -> None:
        """The success path's exact f-string template."""
        session = MagicMock()
        session.snapshot = AsyncMock(return_value={"aria": "- button 'Save'"})
        result = await suggest_fix(session, {"action": "click", "selector": "#submit"})
        assert result is not None
        assert "Click '#submit'" in result  # summarize_action output
        assert "'#submit'" in result  # the failed-selector callout
        assert "- button 'Save'" in result  # aria embedded
        assert result.endswith("Based on the A11y tree, what should I use instead?")

    @pytest.mark.anyio
    async def test_uses_aria_field_not_full_snapshot(self) -> None:
        """If we mutated `snapshot["aria"]` to `snapshot["url"]`, the wrong field would land."""
        session = MagicMock()
        session.snapshot = AsyncMock(return_value={"aria": "ARIA-CONTENT", "url": "https://x"})
        result = await suggest_fix(session, {"action": "click", "selector": "#x"})
        assert result is not None
        assert "ARIA-CONTENT" in result
        assert "https://x" not in result


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ─── repair_preview ──────────────────────────────────────────────────────────


def _macro(name: str = "demo", actions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"name": name, "actions": actions or []}


class TestRepairPreviewEmptyAndFiltering:
    def test_empty_macro_returns_no_suggestions(self) -> None:
        """No actions → empty suggestions list."""
        result = repair_preview("demo", load_macro=lambda _: _macro(), semantic_keys=SEMANTIC_KEYS)
        assert result == {"macro": "demo", "suggestions": []}

    def test_actions_without_selector_skipped(self) -> None:
        """The `'selector' not in action` continue branch."""
        macro = _macro(
            actions=[
                {"action": "navigate", "url": "https://example.com"},
                {"action": "press_key", "key": "Enter"},
            ]
        )
        result = repair_preview("demo", load_macro=lambda _: macro, semantic_keys=SEMANTIC_KEYS)
        assert result["suggestions"] == []

    def test_non_dict_action_skipped(self) -> None:
        """The `not isinstance(action, dict)` continue branch — a stray string in the list."""
        macro = {"name": "demo", "actions": ["not-a-dict", {"action": "click", "selector": "#x"}]}
        result = repair_preview("demo", load_macro=lambda _: macro, semantic_keys=SEMANTIC_KEYS)
        # Only the dict action with a selector contributes.
        assert len(result["suggestions"]) == 1
        # The one suggestion is for the dict action — its action_index is 1, not 0.
        assert result["suggestions"][0]["action_index"] == 1


class TestRepairPreviewMacroName:
    def test_uses_name_from_macro_when_present(self) -> None:
        """macro.get('name') wins over the argument."""
        macro = _macro(name="from-yaml", actions=[{"action": "click", "selector": "#x"}])
        result = repair_preview("from-arg", load_macro=lambda _: macro, semantic_keys=SEMANTIC_KEYS)
        assert result["macro"] == "from-yaml"

    def test_falls_back_to_argument_when_macro_name_missing(self) -> None:
        """No 'name' field on the dict → use the loader argument."""
        macro = {"actions": [{"action": "click", "selector": "#x"}]}
        result = repair_preview("from-arg", load_macro=lambda _: macro, semantic_keys=SEMANTIC_KEYS)
        assert result["macro"] == "from-arg"

    def test_falls_back_to_argument_when_macro_name_empty_string(self) -> None:
        """Empty string is falsy; `or name` triggers."""
        macro = {"name": "", "actions": [{"action": "click", "selector": "#x"}]}
        result = repair_preview("from-arg", load_macro=lambda _: macro, semantic_keys=SEMANTIC_KEYS)
        assert result["macro"] == "from-arg"


class TestRepairPreviewSuggestionShape:
    def test_every_field_populated_on_suggestion(self) -> None:
        """All seven keys per the MacroRepairSuggestion TypedDict."""
        action = {"action": "click", "selector": "#submit", "label": "Save"}
        macro = _macro(actions=[action])
        result = repair_preview("demo", load_macro=lambda _: macro, semantic_keys=SEMANTIC_KEYS)
        s = result["suggestions"][0]
        assert s["macro"] == "demo"
        assert s["action_index"] == 0
        assert s["original_action"] == action
        assert s["source"] == "stored_heuristic"
        assert s["replacement_action"] == {"action": "click_by", "label": "Save"}
        assert s["action_preview"] == "Click by 'Save'"
        assert s["prompt"] == (
            "Review selector '#submit' for action 0. "
            "If it no longer matches, compare the stored semantic fields against the current page."
        )

    def test_prompt_includes_repr_quoted_selector_and_index(self) -> None:
        """Selector !r with single-quote, idx without quotes, both literal."""
        macro = _macro(actions=[{"action": "navigate", "url": "x"}, {"action": "click", "selector": "#second"}])
        result = repair_preview("demo", load_macro=lambda _: macro, semantic_keys=SEMANTIC_KEYS)
        s = result["suggestions"][0]
        assert s["action_index"] == 1
        assert "'#second'" in s["prompt"]
        assert "for action 1." in s["prompt"]

    def test_source_pinned_to_stored_heuristic(self) -> None:
        """If we mutated the literal, this would catch."""
        macro = _macro(actions=[{"action": "click", "selector": "#x"}])
        result = repair_preview("demo", load_macro=lambda _: macro, semantic_keys=SEMANTIC_KEYS)
        assert result["suggestions"][0]["source"] == "stored_heuristic"

    def test_action_index_increments_per_action_dict(self) -> None:
        """idx is the enumeration index, not a count of suggestions emitted."""
        macro = _macro(
            actions=[
                {"action": "navigate", "url": "x"},  # idx 0, no selector → skipped
                {"action": "click", "selector": "#a"},  # idx 1
                {"action": "navigate", "url": "y"},  # idx 2, skipped
                {"action": "click", "selector": "#b"},  # idx 3
            ]
        )
        result = repair_preview("demo", load_macro=lambda _: macro, semantic_keys=SEMANTIC_KEYS)
        indices = [s["action_index"] for s in result["suggestions"]]
        assert indices == [1, 3]

    def test_original_action_is_deep_copied(self) -> None:
        """Mutating the action AFTER repair_preview must not leak back."""
        action = {"action": "click", "selector": "#x", "nested": {"deep": "v"}}
        macro = _macro(actions=[action])
        result = repair_preview("demo", load_macro=lambda _: macro, semantic_keys=SEMANTIC_KEYS)
        action["nested"]["deep"] = "MUTATED"
        assert result["suggestions"][0]["original_action"]["nested"]["deep"] == "v"


class TestRepairPreviewReplacementBranch:
    def test_replacement_action_none_when_no_semantic_fields(self) -> None:
        """semantic_replacement returns None → suggestion still emitted but replacement_action is None."""
        macro = _macro(actions=[{"action": "click", "selector": "#x"}])
        result = repair_preview("demo", load_macro=lambda _: macro, semantic_keys=SEMANTIC_KEYS)
        s = result["suggestions"][0]
        assert s["replacement_action"] is None
        assert s["action_preview"] is None

    def test_replacement_action_none_for_non_click_fill_kind(self) -> None:
        """A wait_for with selector emits a suggestion but no replacement (kind not in {click,fill})."""
        macro = _macro(actions=[{"action": "wait_for", "selector": "#x", "label": "L"}])
        result = repair_preview("demo", load_macro=lambda _: macro, semantic_keys=SEMANTIC_KEYS)
        s = result["suggestions"][0]
        assert s["replacement_action"] is None
        assert s["action_preview"] is None
        # Prompt is still emitted for the bare-selector case.
        assert "'#x'" in s["prompt"]

    def test_action_preview_present_when_replacement_present(self) -> None:
        """The ternary `replacement_preview(replacement) if replacement else None` branch."""
        macro = _macro(actions=[{"action": "fill", "selector": "#x", "label": "Email", "value": "me@x"}])
        result = repair_preview("demo", load_macro=lambda _: macro, semantic_keys=SEMANTIC_KEYS)
        s = result["suggestions"][0]
        assert s["replacement_action"] == {"action": "fill_by", "label": "Email", "value": "me@x"}
        assert s["action_preview"] == "Fill by 'Email' with 'me@x'"


class TestRepairPreviewLoaderInteraction:
    def test_loader_called_with_macro_name(self) -> None:
        """The argument to load_macro is the name parameter, verbatim."""
        captured: list[str] = []

        def loader(n: str) -> dict[str, Any]:
            captured.append(n)
            return _macro()

        repair_preview("the-macro", load_macro=loader, semantic_keys=SEMANTIC_KEYS)
        assert captured == ["the-macro"]

    def test_macro_with_missing_actions_key_yields_empty(self) -> None:
        """`macro.get('actions', [])` default — key absent."""
        result = repair_preview("demo", load_macro=lambda _: {"name": "demo"}, semantic_keys=SEMANTIC_KEYS)
        assert result["suggestions"] == []
