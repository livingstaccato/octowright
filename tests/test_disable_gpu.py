# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The GPU escape hatch for the recurring headed-Chromium crash.

Characterised as a deterministic main-process CHECK abort reached through
native macOS UI plus the Metal GPU path on Chrome 148 / macOS 26. This knob is
NOT a confirmed fix -- it gives an operator whose browsers are crashing
something to try in one argument, instead of nothing.
"""

from __future__ import annotations

import pytest

from octowright.browser_pool.options import (
    DISABLE_GPU_ENV,
    GPU_DISABLE_ARGS,
    LaunchOptions,
    resolve_disable_gpu,
)
from octowright.browser_pool.pool import BrowserPool


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class TestResolution:
    def test_off_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(DISABLE_GPU_ENV, raising=False)

        assert resolve_disable_gpu(None) is False

    def test_env_opts_in(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(DISABLE_GPU_ENV, "1")

        assert resolve_disable_gpu(None) is True

    @pytest.mark.parametrize("token", ["", "0", "off", "false", "no", "never", "none", "disabled"])
    def test_falsey_tokens_keep_it_off(self, token: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Same token set every other OCTOWRIGHT_* switch honours."""
        monkeypatch.setenv(DISABLE_GPU_ENV, token)

        assert resolve_disable_gpu(None) is False

    def test_an_explicit_argument_outranks_the_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Including turning it back OFF on one launch while the env has it on."""
        monkeypatch.setenv(DISABLE_GPU_ENV, "1")

        assert resolve_disable_gpu(False) is False

    def test_it_is_read_at_call_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An operator mid-crash-loop should not have to restart the daemon."""
        monkeypatch.delenv(DISABLE_GPU_ENV, raising=False)
        assert resolve_disable_gpu(None) is False
        monkeypatch.setenv(DISABLE_GPU_ENV, "1")
        assert resolve_disable_gpu(None) is True


class TestLaunchArgs:
    @pytest.mark.anyio
    async def test_flags_are_added_for_chromium(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(DISABLE_GPU_ENV, raising=False)

        kwargs = await BrowserPool()._build_launch_kwargs(tile=False, kind="chromium", headless=True, disable_gpu=True)

        assert list(GPU_DISABLE_ARGS) == [a for a in kwargs["args"] if a in GPU_DISABLE_ARGS]

    @pytest.mark.anyio
    async def test_nothing_is_added_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Every pre-existing launch must be byte-identical to before."""
        monkeypatch.delenv(DISABLE_GPU_ENV, raising=False)

        kwargs = await BrowserPool()._build_launch_kwargs(tile=False, kind="chromium", headless=True)

        assert not any(arg in GPU_DISABLE_ARGS for arg in kwargs.get("args", []))

    @pytest.mark.anyio
    @pytest.mark.parametrize("kind", ["firefox", "webkit"])
    async def test_other_engines_never_see_chromium_flags(self, kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """These are Chromium flags; the others would be handed argv they do
        not understand."""
        monkeypatch.setenv(DISABLE_GPU_ENV, "1")

        kwargs = await BrowserPool()._build_launch_kwargs(tile=False, kind=kind, headless=False)

        assert not any(arg in GPU_DISABLE_ARGS for arg in kwargs.get("args", []))

    @pytest.mark.anyio
    async def test_a_caller_flag_can_still_override_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """launch_args is appended after ours, the ordering rule the builder
        documents, so an operator can re-enable the GPU for one launch."""
        monkeypatch.setenv(DISABLE_GPU_ENV, "1")

        kwargs = await BrowserPool()._build_launch_kwargs(
            tile=False, kind="chromium", headless=True, launch_args=["--enable-gpu"]
        )

        assert kwargs["args"].index("--disable-gpu") < kwargs["args"].index("--enable-gpu")


def test_it_needs_no_code_execution_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unlike launch_args -- arbitrary argv, gated behind
    OCTOWRIGHT_ALLOW_EXECUTABLE_PATH -- this is a boolean selecting a fixed flag
    set, so it grants no new power and must not require that door to be opened."""
    monkeypatch.delenv("OCTOWRIGHT_ALLOW_EXECUTABLE_PATH", raising=False)

    LaunchOptions(disable_gpu=True).validate()
