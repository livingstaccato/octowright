# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""`octowright test` launches at the configured default viewport.

``OCTOWRIGHT_VIEWPORT_W`` / ``OCTOWRIGHT_VIEWPORT_H`` (``defaults.DEFAULT_VIEWPORT_W/H``)
now reach both runner entry points; they used to be ignored in favour of a
hard-coded 1280x800. Driven with a fake pool asserting the exact launch
kwargs, because "unset, nothing changes" is a claim about exactly those
kwargs. Also pins that the runner leaves ``slowmo_ms`` unset, which is what
lets ``OCTOWRIGHT_MACRO_SLOWMO_MS`` apply to every macro it runs.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from octowright import defaults, runner
from octowright.macros import execution

# The launch kwargs a run made before the defaults were honoured. Asserted whole, so an
# extra key sneaking into the default path fails here.
_SUITE_DEFAULT = {
    "kind": "chromium",
    "url": "about:blank",
    "headed": False,
    "label": "test-t1",
    "viewport_w": 1280,
    "viewport_h": 800,
    "profile": None,
}
_SEQUENCE_DEFAULT = {**_SUITE_DEFAULT, "label": "sequence-sequence"}


@pytest.fixture
def recordings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    rec = tmp_path / "recordings"
    rec.mkdir()
    monkeypatch.setattr(defaults, "RECORDINGS_DIR", rec)
    return rec


def _pool() -> MagicMock:
    pool = MagicMock()
    pool.launch = AsyncMock(return_value={"instance_id": "abc123"})
    pool.get = MagicMock(return_value=MagicMock())
    pool.close = AsyncMock(return_value={"closed": True})
    return pool


def _suite(recordings: Path, **kwargs: Any) -> tuple[MagicMock, AsyncMock]:
    pool = _pool()
    run_macro = AsyncMock()
    with (
        patch("octowright.runner.macro_mod.list_macros", return_value=[{"name": "t1"}]),
        patch("octowright.runner.macro_mod.load_macro", return_value={"name": "t1", "description": "[test] one"}),
        patch("octowright.runner.macro_mod.run_macro", run_macro),
    ):
        asyncio.run(runner.run_suite(kind="chromium", pool=pool, out_path=str(recordings / "s.xml"), **kwargs))
    return pool, run_macro


def _sequence(recordings: Path, tmp_path: Path, **kwargs: Any) -> tuple[MagicMock, AsyncMock]:
    pool = _pool()
    run_macro = AsyncMock()
    sequence = tmp_path / "sequence.json"
    sequence.write_text(json.dumps([{"macro": "m1"}]))
    with patch("octowright.runner.macro_mod.run_macro", run_macro):
        asyncio.run(
            runner.run_sequence_file(
                sequence=sequence,
                kind="chromium",
                persona=None,
                artifacts=None,
                redact_errors=False,
                out_path=str(recordings / "s.xml"),
                pool=pool,
                **kwargs,
            )
        )
    return pool, run_macro


@pytest.fixture
def stock(monkeypatch: pytest.MonkeyPatch) -> None:
    """The defaults as shipped, whatever this shell's OCTOWRIGHT_VIEWPORT_W/H say."""
    monkeypatch.setattr(defaults, "DEFAULT_VIEWPORT_W", 1280)
    monkeypatch.setattr(defaults, "DEFAULT_VIEWPORT_H", 800)


@pytest.fixture
def wide(monkeypatch: pytest.MonkeyPatch) -> None:
    """What OCTOWRIGHT_VIEWPORT_W=1920 OCTOWRIGHT_VIEWPORT_H=1080 sets at import."""
    monkeypatch.setattr(defaults, "DEFAULT_VIEWPORT_W", 1920)
    monkeypatch.setattr(defaults, "DEFAULT_VIEWPORT_H", 1080)


class TestRunnerViewport:
    @pytest.mark.usefixtures("stock")
    def test_suite_default_launch_is_unchanged(self, recordings: Path) -> None:
        pool, _ = _suite(recordings)
        assert pool.launch.await_args.kwargs == _SUITE_DEFAULT

    @pytest.mark.usefixtures("stock")
    def test_sequence_default_launch_is_unchanged(self, recordings: Path, tmp_path: Path) -> None:
        pool, _ = _sequence(recordings, tmp_path)
        assert pool.launch.await_args.kwargs == _SEQUENCE_DEFAULT

    def test_the_default_default_is_still_1280x800(self) -> None:
        # With the two above, this is the claim "unset, nothing changes": the
        # shipped defaults are today's hard-coded size. (Skipped if this shell sets them.)
        import os

        if os.environ.get("OCTOWRIGHT_VIEWPORT_W") or os.environ.get("OCTOWRIGHT_VIEWPORT_H"):
            pytest.skip("OCTOWRIGHT_VIEWPORT_W/H set in this environment")
        assert (defaults.DEFAULT_VIEWPORT_W, defaults.DEFAULT_VIEWPORT_H) == (1280, 800)

    @pytest.mark.usefixtures("wide")
    def test_suite_launches_at_the_configured_viewport(self, recordings: Path) -> None:
        pool, _ = _suite(recordings)
        assert pool.launch.await_args.kwargs == {**_SUITE_DEFAULT, "viewport_w": 1920, "viewport_h": 1080}

    @pytest.mark.usefixtures("wide")
    def test_sequence_launches_at_the_configured_viewport(self, recordings: Path, tmp_path: Path) -> None:
        pool, _ = _sequence(recordings, tmp_path)
        assert pool.launch.await_args.kwargs == {**_SEQUENCE_DEFAULT, "viewport_w": 1920, "viewport_h": 1080}


class TestRunnerSlowMo:
    """The runner never passes ``slowmo_ms``, so ``run_macro`` resolves it from
    ``OCTOWRIGHT_MACRO_SLOWMO_MS`` -- the live test measures the delay itself."""

    def test_suite_leaves_slowmo_to_the_env_default(self, recordings: Path) -> None:
        _, run_macro = _suite(recordings)
        assert "slowmo_ms" not in run_macro.await_args.kwargs

    def test_sequence_leaves_slowmo_to_the_env_default(self, recordings: Path, tmp_path: Path) -> None:
        _, run_macro = _sequence(recordings, tmp_path)
        assert "slowmo_ms" not in run_macro.await_args.kwargs

    def test_an_unset_slowmo_resolves_to_the_env_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(execution, "MACRO_SLOWMO_MS", 400)
        assert execution._resolve_slowmo_ms(None) == 400
