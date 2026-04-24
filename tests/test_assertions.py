from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Helpers — reload macros module with patched env (same pattern as test_macros)
# ---------------------------------------------------------------------------


def _import_macros(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("OCTOWRIGHT_MACROS_DIR", str(tmp_path / "macros"))
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path / "profiles"))
    import octowright.macros as _m

    importlib.reload(_m)
    return _m


# ---------------------------------------------------------------------------
# Fake Page — in-process stub; no real browser launched.
# ---------------------------------------------------------------------------


class FakePage:
    """Minimal stub satisfying the async interface expected by the helpers."""

    def __init__(
        self,
        url: str = "https://example.com/path",
        evaluate_result: Any = True,
        inner_text_value: str = "hello world",
        query_selector_result: Any = None,
        wait_for_selector_result: Any = None,
        wait_for_selector_raises: Exception | None = None,
    ) -> None:
        self.url = url
        self._evaluate_result = evaluate_result
        self._inner_text_value = inner_text_value
        self._query_selector_result = query_selector_result
        self._wait_for_selector_result = wait_for_selector_result
        self._wait_for_selector_raises = wait_for_selector_raises

    async def evaluate(self, expression: str) -> Any:
        return self._evaluate_result

    async def wait_for_selector(self, selector: str, timeout: int = 5000) -> Any:
        if self._wait_for_selector_raises is not None:
            raise self._wait_for_selector_raises
        return self._wait_for_selector_result

    async def query_selector(self, selector: str) -> Any:
        return self._query_selector_result

    async def inner_text(self) -> str:
        return self._inner_text_value


class FakeElement:
    """Returned by wait_for_selector when an element should exist."""

    def __init__(self, text: str) -> None:
        self._text = text

    async def inner_text(self) -> str:
        return self._text


# ---------------------------------------------------------------------------
# _check_url tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_check_url_regex_match(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    page = FakePage(url="https://example.com/dashboard")
    actual = await m._check_url(page, r"example\.com/dash", "regex")
    assert actual == "https://example.com/dashboard"


@pytest.mark.anyio
async def test_check_url_regex_mismatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    page = FakePage(url="https://example.com/other")
    with pytest.raises(RuntimeError, match="regex"):
        await m._check_url(page, r"example\.com/dash", "regex")


@pytest.mark.anyio
async def test_check_url_equals_match(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    page = FakePage(url="https://example.com/exact")
    actual = await m._check_url(page, "https://example.com/exact", "equals")
    assert actual == "https://example.com/exact"


@pytest.mark.anyio
async def test_check_url_equals_mismatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    page = FakePage(url="https://example.com/other")
    with pytest.raises(RuntimeError, match="equals"):
        await m._check_url(page, "https://example.com/exact", "equals")


@pytest.mark.anyio
async def test_check_url_contains_match(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    page = FakePage(url="https://example.com/path?q=1")
    actual = await m._check_url(page, "/path", "contains")
    assert "/path" in actual


@pytest.mark.anyio
async def test_check_url_contains_mismatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    page = FakePage(url="https://example.com/other")
    with pytest.raises(RuntimeError, match="contains"):
        await m._check_url(page, "/dashboard", "contains")


@pytest.mark.anyio
async def test_check_url_bad_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    page = FakePage()
    with pytest.raises(ValueError, match="unknown mode"):
        await m._check_url(page, "x", "bogus")


# ---------------------------------------------------------------------------
# _check_text tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_check_text_contains_pass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    element = FakeElement("hello world")
    page = FakePage(wait_for_selector_result=element)
    actual = await m._check_text(page, "#x", "hello")
    assert "hello" in actual


@pytest.mark.anyio
async def test_check_text_contains_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    element = FakeElement("goodbye")
    page = FakePage(wait_for_selector_result=element)
    with pytest.raises(RuntimeError, match="#x"):
        await m._check_text(page, "#x", "hello")


@pytest.mark.anyio
async def test_check_text_equals_pass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    element = FakeElement("exact text")
    page = FakePage(wait_for_selector_result=element)
    actual = await m._check_text(page, "#x", "exact text", mode="equals")
    assert actual == "exact text"


@pytest.mark.anyio
async def test_check_text_equals_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    element = FakeElement("other text")
    page = FakePage(wait_for_selector_result=element)
    with pytest.raises(RuntimeError, match="equals"):
        await m._check_text(page, "#x", "exact text", mode="equals")


@pytest.mark.anyio
async def test_check_text_regex_pass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    element = FakeElement("Error code 42")
    page = FakePage(wait_for_selector_result=element)
    actual = await m._check_text(page, "#x", r"code \d+", mode="regex")
    assert "42" in actual


@pytest.mark.anyio
async def test_check_text_regex_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    element = FakeElement("no digits here")
    page = FakePage(wait_for_selector_result=element)
    with pytest.raises(RuntimeError, match="regex"):
        await m._check_text(page, "#x", r"code \d+", mode="regex")


@pytest.mark.anyio
async def test_check_text_timeout_raises_runtime_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    page = FakePage(wait_for_selector_raises=Exception("Timeout 500ms exceeded"))
    with pytest.raises(RuntimeError, match="never appeared"):
        await m._check_text(page, "#missing", "hi", timeout_ms=500)


# ---------------------------------------------------------------------------
# _check_selector tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_check_selector_present_pass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    element = FakeElement("content")
    page = FakePage(wait_for_selector_result=element)
    # Should not raise
    await m._check_selector(page, "#exists", present=True)


@pytest.mark.anyio
async def test_check_selector_present_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    page = FakePage(wait_for_selector_raises=Exception("Timeout"))
    with pytest.raises(RuntimeError, match="never appeared"):
        await m._check_selector(page, "#missing", present=True, timeout_ms=100)


@pytest.mark.anyio
async def test_check_selector_absent_pass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    # query_selector returns None → element absent → assertion passes
    page = FakePage(query_selector_result=None)
    await m._check_selector(page, "#gone", present=False)


@pytest.mark.anyio
async def test_check_selector_absent_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    # query_selector returns a non-None object → element still there
    page = FakePage(query_selector_result=FakeElement("oops"))
    with pytest.raises(RuntimeError, match="absent"):
        await m._check_selector(page, "#still-here", present=False)


# ---------------------------------------------------------------------------
# _check_js tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_check_js_truthy_pass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    page = FakePage(evaluate_result=True)
    result = await m._check_js(page, "1 === 1")
    assert result is True


@pytest.mark.anyio
async def test_check_js_truthy_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    page = FakePage(evaluate_result=False)
    with pytest.raises(RuntimeError, match="not truthy"):
        await m._check_js(page, "false")


@pytest.mark.anyio
async def test_check_js_equals_pass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    page = FakePage(evaluate_result=42)
    result = await m._check_js(page, "40 + 2", equals=42)
    assert result == 42


@pytest.mark.anyio
async def test_check_js_equals_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    page = FakePage(evaluate_result=99)
    with pytest.raises(RuntimeError, match="expected=42"):
        await m._check_js(page, "something", equals=42)


@pytest.mark.anyio
async def test_check_js_falsy_none_equals_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """evaluate returns 0 (falsy, no equals) → RuntimeError."""
    m = _import_macros(monkeypatch, tmp_path)
    page = FakePage(evaluate_result=0)
    with pytest.raises(RuntimeError, match="not truthy"):
        await m._check_js(page, "0")
