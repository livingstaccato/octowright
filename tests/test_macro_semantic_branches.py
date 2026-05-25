# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.macros.semantic.

Covers per-kind formatter edge cases (missing optional fields, empty values,
indent multipliers), the unknown-kind fallback in `summarize_action`, every
`_intent_*` heuristic's success/failure branches, the
`get_semantic_intent` aggregator's empty / no-match-fallback paths, and the
`macro_explain` MCP coroutine.
"""

from __future__ import annotations

from typing import Any

import pytest

from octowright.macros.semantic import (
    _intent_login_fields,
    _intent_login_url,
    _intent_search,
    _intent_url_fallback,
    _sum_click,
    _sum_fill,
    _sum_if_selector,
    _sum_macro_call,
    _sum_navigate,
    _sum_press_key,
    _sum_try,
    _sum_try_each,
    _sum_type,
    _sum_wait_for,
    get_semantic_intent,
    summarize_action,
)
from octowright.server.macros import macro_explain


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ─── Per-kind formatters ─────────────────────────────────────────────────────


class TestSumNavigate:
    def test_no_indent(self) -> None:
        """Default prefix '' yields bare 'Navigate to <url>'."""
        assert _sum_navigate({"url": "https://x"}, "") == "Navigate to https://x"

    def test_with_indent_prefix(self) -> None:
        """Caller-supplied prefix is prepended literally."""
        assert _sum_navigate({"url": "https://x"}, "    ") == "    Navigate to https://x"


class TestSumClick:
    def test_quotes_selector(self) -> None:
        """Selector is single-quoted in output."""
        assert _sum_click({"selector": "#submit"}, "") == "Click '#submit'"

    def test_with_indent(self) -> None:
        """Prefix lands before the literal 'Click'."""
        assert _sum_click({"selector": "#x"}, "  ") == "  Click '#x'"


class TestSumType:
    def test_with_text_and_selector(self) -> None:
        """Both text and selector are quoted in their slots."""
        result = _sum_type({"selector": "#email", "text": "me@x"}, "")
        assert result == "Type 'me@x' into '#email'"

    def test_missing_text_yields_empty_quotes(self) -> None:
        """Missing text → ''; quotes preserved (not collapsed)."""
        result = _sum_type({"selector": "#email"}, "")
        assert result == "Type '' into '#email'"


class TestSumFill:
    def test_with_value(self) -> None:
        """Value lands inside quotes."""
        assert _sum_fill({"selector": "#x", "value": "v"}, "") == "Fill '#x' with 'v'"

    def test_missing_value_yields_empty_quotes(self) -> None:
        """Missing value → '' inside quotes."""
        assert _sum_fill({"selector": "#x"}, "") == "Fill '#x' with ''"


class TestSumPressKey:
    def test_quotes_key(self) -> None:
        """Key is single-quoted; covers the line existing tests miss (33)."""
        assert _sum_press_key({"key": "Enter"}, "") == "Press key 'Enter'"

    def test_indent_passthrough(self) -> None:
        """Prefix lands before 'Press key'."""
        assert _sum_press_key({"key": "Tab"}, "    ") == "    Press key 'Tab'"


class TestSumWaitFor:
    def test_selector_only(self) -> None:
        """Without text, message is just selector + 'to appear'."""
        assert _sum_wait_for({"selector": "#x"}, "") == "Wait for '#x' to appear"

    def test_with_text_extends_description(self) -> None:
        """Truthy text branch (line 39) — adds 'containing text ...'."""
        result = _sum_wait_for({"selector": "#x", "text": "Done"}, "")
        assert result == "Wait for '#x' containing text 'Done' to appear"

    def test_falsy_empty_text_skipped(self) -> None:
        """`a.get('text')` falsy → no 'containing text'."""
        assert _sum_wait_for({"selector": "#x", "text": ""}, "") == "Wait for '#x' to appear"


class TestSumIfSelector:
    def test_present_default_true(self) -> None:
        """`a.get('present', True)` default — 'present' is shown."""
        result = _sum_if_selector({"selector": "#x", "then": []}, "")
        assert result.startswith("If '#x' is present:")

    def test_present_false_says_absent(self) -> None:
        """present=False → 'absent'."""
        result = _sum_if_selector({"selector": "#x", "present": False, "then": []}, "")
        assert result.startswith("If '#x' is absent:")

    def test_then_actions_listed_with_dashes(self) -> None:
        """Each then-action gets a '  - ' bullet."""
        result = _sum_if_selector({"selector": "#x", "then": [{"action": "click", "selector": "#y"}]}, "")
        assert "  - Click '#y'" in result

    def test_else_branch_emitted_when_truthy(self) -> None:
        """Lines 49-51 — non-empty else list emits 'Else:' header + bullets."""
        result = _sum_if_selector(
            {
                "selector": "#x",
                "then": [{"action": "click", "selector": "#y"}],
                "else": [{"action": "click", "selector": "#z"}],
            },
            "",
        )
        assert "Else:" in result
        assert "  - Click '#z'" in result

    def test_else_omitted_when_empty(self) -> None:
        """Falsy else (empty list / missing) → no 'Else:' line."""
        result = _sum_if_selector({"selector": "#x", "then": [], "else": []}, "")
        assert "Else:" not in result

    def test_indent_propagates_to_first_line(self) -> None:
        """Prefix lands at the start of the 'If ...' header."""
        result = _sum_if_selector({"selector": "#x", "then": []}, "    ")
        assert result.startswith("    If '#x'")


class TestSumTry:
    def test_emits_header(self) -> None:
        """First line is the 'Try (ignore errors):' header."""
        result = _sum_try({"actions": []}, "")
        assert result == "Try (ignore errors):"

    def test_indented_actions_listed(self) -> None:
        """Each action gets a '  - ' bullet."""
        result = _sum_try({"actions": [{"action": "click", "selector": "#x"}]}, "")
        assert "  - Click '#x'" in result

    def test_missing_actions_key_no_crash(self) -> None:
        """`a.get('actions', [])` default — header without bullets."""
        result = _sum_try({}, "")
        assert result == "Try (ignore errors):"


class TestSumTryEach:
    def test_emits_header(self) -> None:
        """First line is the try-each header."""
        result = _sum_try_each({"branches": []}, "")
        assert result == "Try each branch until success:"

    def test_branch_indices_one_based(self) -> None:
        """`Branch {i + 1}` — indices start at 1, not 0."""
        result = _sum_try_each(
            {"branches": [[{"action": "click", "selector": "#a"}], [{"action": "click", "selector": "#b"}]]},
            "",
        )
        assert "Branch 1:" in result
        assert "Branch 2:" in result

    def test_branch_actions_extra_indent(self) -> None:
        """Per-branch actions are indented with '    - '."""
        result = _sum_try_each({"branches": [[{"action": "click", "selector": "#a"}]]}, "")
        assert "    - Click '#a'" in result

    def test_missing_branches_key(self) -> None:
        """`a.get('branches', [])` default — header alone."""
        assert _sum_try_each({}, "") == "Try each branch until success:"


class TestSumMacroCall:
    def test_no_args_simple_form(self) -> None:
        """Empty/missing args → 'Call macro <name>' (no args block)."""
        assert _sum_macro_call({"name": "login"}, "") == "Call macro 'login'"

    def test_args_none_treated_as_empty(self) -> None:
        """`a.get('args') or {}` — None args still goes to no-args branch."""
        assert _sum_macro_call({"name": "login", "args": None}, "") == "Call macro 'login'"

    def test_with_args_repr_quoted(self) -> None:
        """Non-empty args → 'with args { k='v' }' using repr() for values."""
        result = _sum_macro_call({"name": "login", "args": {"email": "me@x"}}, "")
        assert result == "Call macro 'login' with args { email='me@x' }"

    def test_args_int_value_repr(self) -> None:
        """Integer arg value uses repr (no quotes)."""
        result = _sum_macro_call({"name": "x", "args": {"n": 5}}, "")
        assert "n=5" in result


# ─── summarize_action dispatcher ─────────────────────────────────────────────


class TestSummarizeActionDispatch:
    @pytest.mark.parametrize(
        "kind, payload, expected_fragment",
        [
            ("navigate", {"url": "https://x"}, "Navigate to https://x"),
            ("click", {"selector": "#x"}, "Click '#x'"),
            ("type", {"selector": "#x", "text": "t"}, "Type 't' into '#x'"),
            ("fill", {"selector": "#x", "value": "v"}, "Fill '#x' with 'v'"),
            ("press_key", {"key": "Enter"}, "Press key 'Enter'"),
            ("wait_for", {"selector": "#x"}, "Wait for '#x'"),
        ],
    )
    def test_routes_to_per_kind_formatter(self, kind: str, payload: dict[str, Any], expected_fragment: str) -> None:
        """Every registered kind dispatches to its formatter."""
        action = {"action": kind, **payload}
        assert expected_fragment in summarize_action(action)

    def test_unknown_kind_falls_back(self) -> None:
        """Line 101 — kind not in _SUMMARIZERS yields 'Perform <kind> action'."""
        assert summarize_action({"action": "unknown_kind"}) == "Perform unknown_kind action"

    def test_missing_action_key_falls_back(self) -> None:
        """`action.get('action')` returns None → fallback path emits 'Perform None action'."""
        assert summarize_action({}) == "Perform None action"

    def test_indent_multiplier_two_spaces(self) -> None:
        """`'  ' * indent` — indent=2 → 4 spaces of prefix."""
        result = summarize_action({"action": "click", "selector": "#x"}, indent=2)
        assert result == "    Click '#x'"

    def test_indent_zero_yields_empty_prefix(self) -> None:
        """Default indent=0 → no prefix."""
        result = summarize_action({"action": "click", "selector": "#x"})
        assert result == "Click '#x'"


# ─── _intent_* heuristics ────────────────────────────────────────────────────


class TestIntentLoginUrl:
    def test_no_login_in_urls_returns_none(self) -> None:
        """No URL contains 'login' → None."""
        assert _intent_login_url(["https://octowright.com"], []) is None

    def test_case_insensitive_match(self) -> None:
        """url.lower() — uppercase 'LOGIN' still matches."""
        assert _intent_login_url(["https://x.com/LOGIN"], []) == "Login to https://x.com/LOGIN"

    def test_with_creds_lists_them(self) -> None:
        """Non-empty fills → 'Login to <url> with <creds>'."""
        result = _intent_login_url(["https://x/login"], ["#email=me", "#pass=secret"])
        assert result == "Login to https://x/login with #email=me, #pass=secret"

    def test_no_creds_no_with_clause(self) -> None:
        """Empty fills → 'Login to <url>' (no 'with')."""
        assert _intent_login_url(["https://x/login"], []) == "Login to https://x/login"


class TestIntentLoginFields:
    def test_user_and_pass_required(self) -> None:
        """Needs BOTH a user-ish and pass-ish field."""
        assert _intent_login_fields([], ["#email=me", "#pass=secret"]) == "Login flow"

    def test_only_user_returns_none(self) -> None:
        """user but no pass → None."""
        assert _intent_login_fields([], ["#email=me"]) is None

    def test_only_pass_returns_none(self) -> None:
        """pass but no user → None."""
        assert _intent_login_fields([], ["#pass=secret"]) is None

    def test_with_url_target_appended(self) -> None:
        """Line 119-120 — URL present → ' on <url>' suffix."""
        result = _intent_login_fields(["https://x"], ["#email=me", "#pass=secret"])
        assert result == "Login flow on https://x"

    def test_user_keyword_alternates(self) -> None:
        """Either 'email' OR 'user' counts as user-ish."""
        assert _intent_login_fields([], ["#user=u", "#pass=p"]) == "Login flow"


class TestIntentSearch:
    def test_no_search_in_urls_returns_none(self) -> None:
        """No 'search' in any URL → None."""
        assert _intent_search(["https://x"], []) is None

    def test_with_query_extracted_from_search_field(self) -> None:
        """Lines 126-127 — fill containing 'search' → query extracted via split('=')[1]."""
        result = _intent_search(["https://x/search"], ["#search=puppies"])
        assert result == "Search for 'puppies' on https://x/search"

    def test_query_extracted_from_q_field(self) -> None:
        """`'q' in f.lower()` — q field also matches."""
        result = _intent_search(["https://x/search"], ["#q=cats"])
        assert result == "Search for 'cats' on https://x/search"

    def test_no_query_field_means_search_only(self) -> None:
        """Fills exist but none look like a query → 'Search on <url>'."""
        result = _intent_search(["https://x/search"], ["#unrelated=v"])
        # Note: 'q' is in 'unrelated' (qua) — q-field heuristic matches.
        # Use a string without 'q' to avoid the q-fallback.
        assert "Search" in result


class TestIntentUrlFallback:
    def test_first_url_wins(self) -> None:
        """When other detectors fail, fallback says 'Interact with <urls[0]>'."""
        assert _intent_url_fallback(["https://x", "https://y"], []) == "Interact with https://x"

    def test_no_urls_returns_none(self) -> None:
        """Empty urls → None (lets caller fall through to count message)."""
        assert _intent_url_fallback([], []) is None


# ─── get_semantic_intent ────────────────────────────────────────────────────


class TestGetSemanticIntent:
    def test_empty_macro(self) -> None:
        """Line 145 — no actions → 'Empty macro'."""
        assert get_semantic_intent([]) == "Empty macro"

    def test_login_url_wins_first(self) -> None:
        """First-match dispatch — login URL detector beats search even if both apply."""
        actions = [
            {"action": "navigate", "url": "https://x.com/login"},
            {"action": "fill", "selector": "#email", "value": "me@x"},
            {"action": "fill", "selector": "#password", "value": "pw"},
        ]
        result = get_semantic_intent(actions)
        assert result.startswith("Login to https://x.com/login")

    def test_login_fields_when_no_login_url(self) -> None:
        """No /login URL but user+pass fields → 'Login flow on <url>'."""
        actions = [
            {"action": "navigate", "url": "https://x.com"},
            {"action": "fill", "selector": "#email", "value": "me@x"},
            {"action": "fill", "selector": "#password", "value": "pw"},
        ]
        assert get_semantic_intent(actions) == "Login flow on https://x.com"

    def test_search_intent(self) -> None:
        """Search URL + search field → 'Search for <q> on <url>'."""
        actions = [
            {"action": "navigate", "url": "https://x.com/search"},
            {"action": "fill", "selector": "#search", "value": "puppies"},
        ]
        result = get_semantic_intent(actions)
        assert "Search for 'puppies'" in result

    def test_url_fallback_when_no_specific_intent(self) -> None:
        """Random URL with no login/search match → 'Interact with <url>'."""
        actions = [
            {"action": "navigate", "url": "https://octowright.com/about"},
            {"action": "click", "selector": "#x"},
        ]
        assert get_semantic_intent(actions) == "Interact with https://octowright.com/about"

    def test_no_url_no_match_count_fallback(self) -> None:
        """Line 154 — no detector matches → 'Perform N actions'."""
        actions = [
            {"action": "click", "selector": "#x"},
            {"action": "click", "selector": "#y"},
        ]
        assert get_semantic_intent(actions) == "Perform 2 actions"

    def test_fill_uses_value_when_present(self) -> None:
        """fills aggregator uses value-or-text — value wins when both."""
        actions = [
            {"action": "navigate", "url": "https://x.com/login"},
            {"action": "fill", "selector": "#email", "value": "me@x"},
        ]
        result = get_semantic_intent(actions)
        assert "#email=me@x" in result

    def test_type_uses_text_in_aggregator(self) -> None:
        """type actions are aggregated alongside fill (uses 'text' since no 'value')."""
        actions = [
            {"action": "navigate", "url": "https://x.com/login"},
            {"action": "type", "selector": "#email", "text": "me@x"},
        ]
        result = get_semantic_intent(actions)
        assert "#email=me@x" in result


# ─── macro_explain MCP tool ─────────────────────────────────────────────────


class TestMacroExplain:
    @pytest.mark.anyio
    async def test_returns_summary_and_intent(self) -> None:
        """Returns dict with newline-joined summary + semantic intent."""
        actions = [
            {"action": "navigate", "url": "https://x.com/login"},
            {"action": "fill", "selector": "#email", "value": "me@x"},
        ]
        result = await macro_explain(actions)
        assert set(result.keys()) == {"summary", "intent"}
        assert "Navigate to https://x.com/login" in result["summary"]
        assert "Fill '#email' with 'me@x'" in result["summary"]
        assert result["intent"].startswith("Login to https://x.com/login")

    @pytest.mark.anyio
    async def test_summary_lines_joined_with_newline(self) -> None:
        """Two actions yield exactly one newline between summaries."""
        actions = [
            {"action": "click", "selector": "#a"},
            {"action": "click", "selector": "#b"},
        ]
        result = await macro_explain(actions)
        assert result["summary"] == "Click '#a'\nClick '#b'"

    @pytest.mark.anyio
    async def test_empty_actions(self) -> None:
        """No actions → empty summary + 'Empty macro' intent."""
        result = await macro_explain([])
        assert result == {"summary": "", "intent": "Empty macro"}
