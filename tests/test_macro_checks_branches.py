# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.macros.checks.

Targets the 27 surviving mutmut mutants by asserting on:
- exact RuntimeError / ValueError messages (mode-suffix labels, "(equals)",
  "(contains)", "(regex)", "(not truthy)" + repr-quoted values).
- the `timeout_ms or 10000` default fallback for both check_text and
  check_selector — pin both branches (None falls back, explicit value
  passes through, explicit 0 still falls back since 0 is falsy).
- the `equals is not None` branch in check_js — the falsy-but-not-None
  case (equals=0, equals="") routes to the equals path, not truthy path.
- return values from each happy-path call.
- wait_for_selector argument values (selector + timeout).
- the absent-selector RuntimeError when a present element is found.
"""

from __future__ import annotations

from typing import Any

import pytest

from octowright.macros import checks as macro_checks
from tests._operation_gate_fakes import OperationAwareFake


class _Elem:
    def __init__(self, text: str = "") -> None:
        self._text = text

    async def inner_text(self) -> str:
        return self._text


class _Page:
    """Minimal page double; records every wait_for_selector / query_selector / evaluate call."""

    def __init__(self, url: str = "https://example.test/path") -> None:
        self.url = url
        self.selector_map: dict[str, _Elem | None] = {}
        self.eval_result: Any = True
        self.wait_calls: list[tuple[str, int]] = []
        self.query_calls: list[str] = []
        self.eval_calls: list[str] = []

    async def wait_for_selector(self, selector: str, timeout: int = 0) -> _Elem | None:
        self.wait_calls.append((selector, timeout))
        return self.selector_map.get(selector)

    async def query_selector(self, selector: str) -> _Elem | None:
        self.query_calls.append(selector)
        return self.selector_map.get(selector)

    async def evaluate(self, expression: str) -> Any:
        self.eval_calls.append(expression)
        return self.eval_result


class _Session(OperationAwareFake):
    """Wraps a bare ``_Page`` double behind the ``SessionLike`` surface
    ``macros.checks`` now requires: ``.page`` for ``_check_url`` and
    ``._target()`` for the element/JS checks."""

    def __init__(self, page: _Page) -> None:
        super().__init__()
        self.page = page

    def _target(self) -> _Page:
        return self.page


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ─── _check_url: error message format ────────────────────────────────────────


class TestCheckUrlEqualsBranch:
    @pytest.mark.anyio
    async def test_equals_match_returns_actual_url(self) -> None:
        """Happy path: returns the page URL verbatim."""
        page = _Page(url="https://example.test/x")
        session = _Session(page)
        assert (
            await macro_checks._check_url(session, "https://example.test/x", mode="equals") == "https://example.test/x"
        )

    @pytest.mark.anyio
    async def test_equals_mismatch_message_includes_equals_label_and_both_urls(self) -> None:
        """Mutating the f-string would lose any of: '(equals)', expected, actual."""
        page = _Page(url="https://actual")
        session = _Session(page)
        with pytest.raises(RuntimeError) as exc_info:
            await macro_checks._check_url(session, "https://expected", mode="equals")
        msg = str(exc_info.value)
        assert "URL mismatch" in msg
        assert '"https://expected"' in msg
        assert '"https://actual"' in msg
        assert "(equals)" in msg


class TestCheckUrlContainsBranch:
    @pytest.mark.anyio
    async def test_contains_match_returns_actual_url(self) -> None:
        """Substring match returns actual URL."""
        page = _Page(url="https://example.test/path?q=1")
        session = _Session(page)
        assert (
            await macro_checks._check_url(session, "example.test", mode="contains") == "https://example.test/path?q=1"
        )

    @pytest.mark.anyio
    async def test_contains_mismatch_message_uses_substring_label(self) -> None:
        """Message has 'substring' wording and '(contains)' label."""
        page = _Page(url="https://actual")
        session = _Session(page)
        with pytest.raises(RuntimeError) as exc_info:
            await macro_checks._check_url(session, "missing-piece", mode="contains")
        msg = str(exc_info.value)
        assert "substring" in msg
        assert "(contains)" in msg
        assert '"missing-piece"' in msg
        assert '"https://actual"' in msg


class TestCheckUrlRegexBranch:
    @pytest.mark.anyio
    async def test_default_mode_is_regex(self) -> None:
        """Mode default is 'regex' — calling without mode should regex-match."""
        page = _Page(url="https://example.test/path")
        session = _Session(page)
        assert await macro_checks._check_url(session, r"example\.test") == "https://example.test/path"

    @pytest.mark.anyio
    async def test_regex_mismatch_message_uses_pattern_label(self) -> None:
        """Message has 'pattern' wording and '(regex)' label."""
        page = _Page(url="https://actual")
        session = _Session(page)
        with pytest.raises(RuntimeError) as exc_info:
            await macro_checks._check_url(session, r"^nomatch$", mode="regex")
        msg = str(exc_info.value)
        assert "pattern" in msg
        assert "(regex)" in msg
        assert '"^nomatch$"' in msg
        assert '"https://actual"' in msg

    @pytest.mark.anyio
    async def test_regex_uses_re_search_not_match(self) -> None:
        """Pattern matches anywhere in URL (re.search), not anchored to start (re.match)."""
        page = _Page(url="https://example.test/middle/path")
        session = _Session(page)
        # If implementation used re.match, this would fail — re.search succeeds.
        assert (
            await macro_checks._check_url(session, r"middle/path", mode="regex") == "https://example.test/middle/path"
        )


class TestCheckUrlUnknownMode:
    @pytest.mark.anyio
    async def test_unknown_mode_raises_value_error_with_mode_repr(self) -> None:
        """ValueError message must include the bad mode repr-quoted and list valid modes."""
        page = _Page()
        session = _Session(page)
        with pytest.raises(ValueError) as exc_info:
            await macro_checks._check_url(session, "x", mode="bogus")
        msg = str(exc_info.value)
        assert "unknown mode" in msg
        assert "'bogus'" in msg  # repr quoting
        assert "regex" in msg
        assert "equals" in msg
        assert "contains" in msg


# ─── _check_text: branches + defaults ───────────────────────────────────────


class TestCheckTextDefaults:
    @pytest.mark.anyio
    async def test_default_mode_is_contains(self) -> None:
        """Mode default is 'contains'."""
        page = _Page()
        session = _Session(page)
        page.selector_map["#x"] = _Elem("hello world")
        # No mode kwarg.
        assert await macro_checks._check_text(session, "#x", "hello") == "hello world"

    @pytest.mark.anyio
    async def test_timeout_ms_none_falls_back_to_10000(self) -> None:
        """The `timeout_ms or 10000` fallback when timeout_ms is None."""
        page = _Page()
        session = _Session(page)
        page.selector_map["#x"] = _Elem("ok")
        await macro_checks._check_text(session, "#x", "ok")
        assert page.wait_calls == [("#x", 10000)]

    @pytest.mark.anyio
    async def test_timeout_ms_zero_falls_back_to_10000(self) -> None:
        """0 is falsy; `or 10000` triggers the fallback."""
        page = _Page()
        session = _Session(page)
        page.selector_map["#x"] = _Elem("ok")
        await macro_checks._check_text(session, "#x", "ok", timeout_ms=0)
        assert page.wait_calls == [("#x", 10000)]

    @pytest.mark.anyio
    async def test_timeout_ms_explicit_value_passes_through(self) -> None:
        """Truthy timeout passes through verbatim."""
        page = _Page()
        session = _Session(page)
        page.selector_map["#x"] = _Elem("ok")
        await macro_checks._check_text(session, "#x", "ok", timeout_ms=2500)
        assert page.wait_calls == [("#x", 2500)]


class TestCheckTextElementMissing:
    @pytest.mark.anyio
    async def test_missing_element_message_includes_selector(self) -> None:
        """Mutating the f-string would lose the selector name."""
        page = _Page()
        session = _Session(page)
        # selector_map empty → wait_for_selector returns None.
        with pytest.raises(RuntimeError) as exc_info:
            await macro_checks._check_text(session, "#nope", "irrelevant")
        msg = str(exc_info.value)
        assert "never appeared" in msg
        assert '"#nope"' in msg


class TestCheckTextContainsBranch:
    @pytest.mark.anyio
    async def test_contains_mismatch_message_format(self) -> None:
        """Message: text mismatch on "<selector>": expected to contain "<text>", got "<actual>"."""
        page = _Page()
        session = _Session(page)
        page.selector_map["#x"] = _Elem("hello world")
        with pytest.raises(RuntimeError) as exc_info:
            await macro_checks._check_text(session, "#x", "missing", mode="contains")
        msg = str(exc_info.value)
        assert '"#x"' in msg
        assert "expected to contain" in msg
        assert '"missing"' in msg
        assert '"hello world"' in msg


class TestCheckTextEqualsBranch:
    @pytest.mark.anyio
    async def test_equals_match_returns_actual(self) -> None:
        page = _Page()
        session = _Session(page)
        page.selector_map["#x"] = _Elem("exact")
        assert await macro_checks._check_text(session, "#x", "exact", mode="equals") == "exact"

    @pytest.mark.anyio
    async def test_equals_mismatch_message_includes_equals_label(self) -> None:
        """'(equals)' label and both expected/actual in repr-quotes."""
        page = _Page()
        session = _Session(page)
        page.selector_map["#x"] = _Elem("got-this")
        with pytest.raises(RuntimeError) as exc_info:
            await macro_checks._check_text(session, "#x", "want-that", mode="equals")
        msg = str(exc_info.value)
        assert "(equals)" in msg
        assert '"want-that"' in msg
        assert '"got-this"' in msg


class TestCheckTextRegexBranch:
    @pytest.mark.anyio
    async def test_regex_match_returns_actual(self) -> None:
        page = _Page()
        session = _Session(page)
        page.selector_map["#x"] = _Elem("hello world 123")
        assert await macro_checks._check_text(session, "#x", r"world \d+", mode="regex") == "hello world 123"

    @pytest.mark.anyio
    async def test_regex_mismatch_message_includes_pattern_label(self) -> None:
        """Message uses 'pattern' wording and '(regex)' label."""
        page = _Page()
        session = _Session(page)
        page.selector_map["#x"] = _Elem("hello")
        with pytest.raises(RuntimeError) as exc_info:
            await macro_checks._check_text(session, "#x", r"^zzz$", mode="regex")
        msg = str(exc_info.value)
        assert "pattern" in msg
        assert "(regex)" in msg


class TestCheckTextUnknownMode:
    @pytest.mark.anyio
    async def test_unknown_mode_raises_with_repr_and_valid_list(self) -> None:
        """ValueError message includes repr'd mode and the three valid mode names."""
        page = _Page()
        session = _Session(page)
        page.selector_map["#x"] = _Elem("ok")
        with pytest.raises(ValueError) as exc_info:
            await macro_checks._check_text(session, "#x", "ok", mode="weird")
        msg = str(exc_info.value)
        assert "'weird'" in msg
        assert "contains" in msg
        assert "equals" in msg
        assert "regex" in msg


# ─── _check_selector ────────────────────────────────────────────────────────


class TestCheckSelectorPresentBranch:
    @pytest.mark.anyio
    async def test_present_default_true(self) -> None:
        """Default present=True calls wait_for_selector."""
        page = _Page()
        session = _Session(page)
        page.selector_map["#x"] = _Elem("x")
        await macro_checks._check_selector(session, "#x")
        assert page.wait_calls == [("#x", 10000)]
        assert page.query_calls == []

    @pytest.mark.anyio
    async def test_present_returns_none(self) -> None:
        """Function returns None on success."""
        page = _Page()
        session = _Session(page)
        page.selector_map["#x"] = _Elem("x")
        result = await macro_checks._check_selector(session, "#x", present=True)
        assert result is None

    @pytest.mark.anyio
    async def test_present_timeout_ms_none_falls_back_to_10000(self) -> None:
        """`timeout_ms or 10000` for the present-branch wait_for_selector call."""
        page = _Page()
        session = _Session(page)
        page.selector_map["#x"] = _Elem("x")
        await macro_checks._check_selector(session, "#x", present=True, timeout_ms=None)
        assert page.wait_calls == [("#x", 10000)]

    @pytest.mark.anyio
    async def test_present_timeout_ms_zero_falls_back_to_10000(self) -> None:
        """0 is falsy; the `or 10000` fallback triggers."""
        page = _Page()
        session = _Session(page)
        page.selector_map["#x"] = _Elem("x")
        await macro_checks._check_selector(session, "#x", present=True, timeout_ms=0)
        assert page.wait_calls == [("#x", 10000)]

    @pytest.mark.anyio
    async def test_present_timeout_ms_explicit_passes_through(self) -> None:
        page = _Page()
        session = _Session(page)
        page.selector_map["#x"] = _Elem("x")
        await macro_checks._check_selector(session, "#x", present=True, timeout_ms=750)
        assert page.wait_calls == [("#x", 750)]


class TestCheckSelectorAbsentBranch:
    @pytest.mark.anyio
    async def test_absent_uses_query_selector_not_wait(self) -> None:
        """The absent branch uses query_selector so it doesn't block."""
        page = _Page()
        session = _Session(page)
        # Absent: not in selector_map.
        await macro_checks._check_selector(session, "#missing", present=False)
        assert page.query_calls == ["#missing"]
        assert page.wait_calls == []

    @pytest.mark.anyio
    async def test_absent_succeeds_when_element_truly_absent(self) -> None:
        """No element → no error."""
        page = _Page()
        session = _Session(page)
        result = await macro_checks._check_selector(session, "#missing", present=False)
        assert result is None

    @pytest.mark.anyio
    async def test_absent_raises_when_element_found_with_selector_in_message(self) -> None:
        """RuntimeError message must include the selector name."""
        page = _Page()
        session = _Session(page)
        page.selector_map["#here"] = _Elem("oops")
        with pytest.raises(RuntimeError) as exc_info:
            await macro_checks._check_selector(session, "#here", present=False)
        msg = str(exc_info.value)
        assert "should be absent" in msg
        assert '"#here"' in msg

    @pytest.mark.anyio
    async def test_absent_does_not_consult_timeout_for_query(self) -> None:
        """The absent branch ignores timeout_ms (passes nothing to query_selector)."""
        page = _Page()
        session = _Session(page)
        # Even with a non-default timeout, the absent path doesn't touch wait_for_selector.
        await macro_checks._check_selector(session, "#missing", present=False, timeout_ms=99999)
        assert page.wait_calls == []


# ─── _check_js ───────────────────────────────────────────────────────────────


class TestCheckJsEqualsBranch:
    @pytest.mark.anyio
    async def test_equals_match_returns_result(self) -> None:
        page = _Page()
        session = _Session(page)
        page.eval_result = 42
        assert await macro_checks._check_js(session, "x", equals=42) == 42

    @pytest.mark.anyio
    async def test_equals_mismatch_message_format(self) -> None:
        """Message: 'JS assertion failed' + repr-quoted expression/expected/got."""
        page = _Page()
        session = _Session(page)
        page.eval_result = 99
        with pytest.raises(RuntimeError) as exc_info:
            await macro_checks._check_js(session, "x.length", equals=42)
        msg = str(exc_info.value)
        assert "JS assertion failed" in msg
        assert "expression='x.length'" in msg
        assert "expected=42" in msg
        assert "got=99" in msg

    @pytest.mark.anyio
    async def test_equals_zero_routes_through_equals_branch(self) -> None:
        """`equals is not None` — 0 should NOT fall through to truthy check."""
        page = _Page()
        session = _Session(page)
        page.eval_result = 0
        # Both equals=0 and result=0 → equality holds → no raise, returns 0.
        assert await macro_checks._check_js(session, "x", equals=0) == 0

    @pytest.mark.anyio
    async def test_equals_empty_string_routes_through_equals_branch(self) -> None:
        """`equals is not None` — '' should NOT fall through to truthy check."""
        page = _Page()
        session = _Session(page)
        page.eval_result = ""
        # equals='' and result='' match → returns ''.
        assert await macro_checks._check_js(session, "x", equals="") == ""


class TestCheckJsTruthyBranch:
    @pytest.mark.anyio
    async def test_truthy_returns_result(self) -> None:
        page = _Page()
        session = _Session(page)
        page.eval_result = {"any": "truthy"}
        assert await macro_checks._check_js(session, "x") == {"any": "truthy"}

    @pytest.mark.anyio
    async def test_falsy_no_equals_raises_with_not_truthy_label(self) -> None:
        """Truthy-branch message includes '(not truthy)'."""
        page = _Page()
        session = _Session(page)
        page.eval_result = 0
        with pytest.raises(RuntimeError) as exc_info:
            await macro_checks._check_js(session, "x")
        msg = str(exc_info.value)
        assert "(not truthy)" in msg
        assert "expression='x'" in msg
        assert "got=0" in msg

    @pytest.mark.anyio
    async def test_none_result_with_no_equals_raises(self) -> None:
        """None is falsy; truthy branch raises."""
        page = _Page()
        session = _Session(page)
        page.eval_result = None
        with pytest.raises(RuntimeError):
            await macro_checks._check_js(session, "x")

    @pytest.mark.anyio
    async def test_empty_list_with_no_equals_raises(self) -> None:
        """Empty list is falsy."""
        page = _Page()
        session = _Session(page)
        page.eval_result = []
        with pytest.raises(RuntimeError):
            await macro_checks._check_js(session, "x")

    @pytest.mark.anyio
    async def test_evaluate_called_with_expression_verbatim(self) -> None:
        """Mutating the expression argument would change what page.evaluate sees."""
        page = _Page()
        session = _Session(page)
        page.eval_result = True
        await macro_checks._check_js(session, "window.foo === 'bar'")
        assert page.eval_calls == ["window.foo === 'bar'"]
