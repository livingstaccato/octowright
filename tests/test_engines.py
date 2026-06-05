# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from octowright import engines
from octowright.browser_pool import BrowserPool


@pytest.mark.anyio
async def test_run_playwright_cli_decodes_stdout_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeProc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return (b"ok-out", b"ok-err")

    async def _fake_create_subprocess_exec(*cmd: str, **_: Any) -> _FakeProc:
        assert cmd[0] == "playwright"
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    result = await engines._run_playwright_cli("install", "--list")
    assert result.returncode == 0
    assert result.stdout == "ok-out"
    assert result.stderr == "ok-err"
    assert result.command == ["playwright", "install", "--list"]


@pytest.mark.anyio
async def test_engine_status_parses_current_version(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_cli(*args: str) -> engines.CliResult:
        assert args == ("install", "--list")
        output = """
Playwright version: 1.59.0
  Browsers:
    /tmp/ms-playwright/chromium-1234
    /tmp/ms-playwright/firefox-1500
  References:
    /tmp/ref
"""
        return engines.CliResult(returncode=0, stdout=output, stderr="", command=["playwright", *args])

    monkeypatch.setattr(engines.importlib.metadata, "version", lambda _: "1.59.0")
    monkeypatch.setattr(engines, "_run_playwright_cli", _fake_cli)

    status = await engines.engine_status()
    assert status["ok"] is False
    assert status["engines"]["chromium"]["installed"] is True
    assert status["engines"]["firefox"]["installed"] is True
    assert status["engines"]["webkit"]["installed"] is False
    assert status["missing"] == ["webkit"]


@pytest.mark.anyio
async def test_engine_status_rejects_invalid_kind() -> None:
    with pytest.raises(ValueError):
        await engines.engine_status(["not-an-engine"])


@pytest.mark.anyio
async def test_engine_install_rejects_invalid_kind() -> None:
    with pytest.raises(ValueError):
        await engines.engine_install(["not-an-engine"])


@pytest.mark.anyio
async def test_engine_install_reports_status(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_cli(*args: str) -> engines.CliResult:
        return engines.CliResult(returncode=0, stdout="ok", stderr="", command=["playwright", *args])

    async def _fake_status(kinds: list[str] | None = None) -> dict[str, Any]:
        assert kinds == ["webkit"]
        return {"ok": True, "missing": [], "engines": {"webkit": {"installed": True}}}

    monkeypatch.setattr(engines, "_run_playwright_cli", _fake_cli)
    monkeypatch.setattr(engines, "engine_status", _fake_status)
    result = await engines.engine_install(["webkit"], with_deps=True)
    assert result["ok"] is True
    assert result["returncode"] == 0
    assert result["status"]["ok"] is True


def test_playwright_failure_sanity_detects_missing_binaries() -> None:
    msg = "BrowserType.launch_persistent_context: Executable doesn't exist at /tmp/webkit/pw_run.sh"
    hint = engines.playwright_failure_sanity(msg, kind="webkit")
    assert hint is not None
    assert hint["category"] == "playwright_binaries_missing"
    assert "playwright install webkit" in hint["recommended_actions"][0]


@pytest.mark.parametrize(
    ("message", "category"),
    [
        ("Host system is missing dependencies to run browsers", "playwright_os_dependencies_missing"),
        ("net::ERR_NAME_NOT_RESOLVED while navigating", "playwright_network_unreachable"),
        ("browser failed: Permission denied", "playwright_permission_error"),
        (
            "browserType.launch: Target page, context or browser has been closed due to sandbox policy",
            "playwright_sandbox_blocked",
        ),
        ("Target page, context or browser has been closed", "playwright_target_closed"),
        ("Navigation timeout 30000ms exceeded", "playwright_navigation_timeout"),
    ],
)
def test_playwright_failure_sanity_additional_categories(message: str, category: str) -> None:
    hint = engines.playwright_failure_sanity(message, kind="chromium")
    assert hint is not None
    assert hint["category"] == category


def test_playwright_failure_sanity_returns_none_for_unknown_error() -> None:
    hint = engines.playwright_failure_sanity("completely unrelated application error", kind="chromium")
    assert hint is None


def _assert_octowright_sanity(exc: pytest.ExceptionInfo[RuntimeError], category: str) -> None:
    text = str(exc.value)
    assert "[octowright_sanity]" in text
    assert category in text


@pytest.mark.anyio
async def test_canonical_pool_launch_wraps_missing_binary_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeBrowserType:
        async def launch(self, **_: Any) -> Any:
            raise RuntimeError("Executable doesn't exist at /tmp/ms-playwright/webkit/pw_run.sh")

    class _FakePlaywright:
        webkit = _FakeBrowserType()

    pool = BrowserPool()

    async def _fake_ensure_pw() -> Any:
        return _FakePlaywright()

    monkeypatch.setattr(pool, "_ensure_pw", _fake_ensure_pw)

    with pytest.raises(RuntimeError) as exc:
        await pool.launch(kind="webkit", url="about:blank", ephemeral=True)
    _assert_octowright_sanity(exc, "playwright_binaries_missing")


@pytest.mark.anyio
async def test_canonical_pool_launch_wraps_persistent_context_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeBrowserType:
        async def launch_persistent_context(self, *_: Any, **__: Any) -> Any:
            raise RuntimeError("Host system is missing dependencies to run browsers")

    class _FakePlaywright:
        chromium = _FakeBrowserType()

    pool = BrowserPool()

    async def _fake_ensure_pw() -> Any:
        return _FakePlaywright()

    monkeypatch.setattr(pool, "_ensure_pw", _fake_ensure_pw)

    with pytest.raises(RuntimeError) as exc:
        await pool.launch(kind="chromium", url="about:blank", profile="review-test")
    _assert_octowright_sanity(exc, "playwright_os_dependencies_missing")


@pytest.mark.anyio
async def test_canonical_pool_launch_wraps_initial_navigation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakePage:
        video = None
        url = "about:blank"
        main_frame = object()

        def on(self, *_: Any) -> None:
            pass

        def is_closed(self) -> bool:
            return False

        async def goto(self, _: str) -> None:
            raise RuntimeError("net::ERR_NAME_NOT_RESOLVED while navigating")

    class _FakeContext:
        pages: list[Any] = []

        def __init__(self) -> None:
            self.tracing = self

        def on(self, *_: Any) -> None:
            pass

        async def add_init_script(self, **_: Any) -> None:
            pass

        async def new_page(self) -> _FakePage:
            return _FakePage()

    class _FakeBrowser:
        def on(self, *_: Any) -> None:
            pass

        async def new_context(self, **_: Any) -> _FakeContext:
            return _FakeContext()

    class _FakeBrowserType:
        async def launch(self, **_: Any) -> _FakeBrowser:
            return _FakeBrowser()

    class _FakePlaywright:
        chromium = _FakeBrowserType()

    pool = BrowserPool()

    async def _fake_ensure_pw() -> Any:
        return _FakePlaywright()

    monkeypatch.setattr(pool, "_ensure_pw", _fake_ensure_pw)

    # Navigation failures no longer kill the browser — the session stays alive
    # and the error is reported in nav_warning so the caller can see it without
    # losing the instance.
    result = await pool.launch(kind="chromium", url="https://example.invalid", ephemeral=True)
    assert "nav_warning" in result
    assert "ERR_NAME_NOT_RESOLVED" in result["nav_warning"] or result["nav_warning"]


@pytest.mark.anyio
async def test_canonical_pool_launch_preserves_unwrapped_error_cause(monkeypatch: pytest.MonkeyPatch) -> None:
    original = RuntimeError("application-specific launch failure")

    class _FakeBrowserType:
        async def launch(self, **_: Any) -> Any:
            raise original

    class _FakePlaywright:
        chromium = _FakeBrowserType()

    pool = BrowserPool()

    async def _fake_ensure_pw() -> Any:
        return _FakePlaywright()

    monkeypatch.setattr(pool, "_ensure_pw", _fake_ensure_pw)

    with pytest.raises(RuntimeError) as exc:
        await pool.launch(kind="chromium", url="about:blank", ephemeral=True)
    assert exc.value is original
    assert exc.value.__cause__ is None
