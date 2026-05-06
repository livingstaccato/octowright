# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from octowright.browser_pool import BrowserPool
from octowright.scenarios import LiveScenario, Participant, Scenario, ScenarioPool


def _configure_runtime_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    rec = tmp_path / "recordings"
    prof = tmp_path / "profiles"
    rec.mkdir()
    prof.mkdir()
    monkeypatch.setenv("OCTOWRIGHT_HEADLESS", "1")
    monkeypatch.setenv("OCTOWRIGHT_RECORDINGS", str(rec))
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(prof))

    import octowright.browser_pool.pool as _pool
    from octowright import defaults as _defaults
    from octowright import personas as _personas
    from octowright import profiles as _profiles

    monkeypatch.setattr(_defaults, "RECORDINGS_DIR", rec)
    monkeypatch.setattr(_pool, "RECORDINGS_DIR", rec)
    monkeypatch.setattr(_defaults, "PROFILES_DIR", prof)
    monkeypatch.setattr(_personas, "PROFILES_DIR", prof)
    monkeypatch.setattr(_profiles, "PROFILES_DIR", prof)
    return rec, prof


def _make_persona(profiles_dir: Path, name: str) -> None:
    pdir = profiles_dir / name
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "profile.yaml").write_text(yaml.safe_dump({"name": name}), encoding="utf-8")


def _maybe_skip_live_engine(exc: Exception) -> None:
    msg = str(exc).lower()
    if any(
        snippet in msg
        for snippet in (
            "executable doesn't exist",
            "browser has been closed",
            "target page, context or browser has been closed",
            "missing x server",
            "headed browser",
            "no protocol specified",
            "cannot open display",
        )
    ):
        pytest.skip(f"playwright runtime unavailable for this engine in this environment: {exc!r}")
    raise exc


@pytest.mark.asyncio
@pytest.mark.live_browser
@pytest.mark.engine_matrix
@pytest.mark.parametrize("kind", ["chromium", "firefox", "webkit"])
async def test_live_har_capture_per_engine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str) -> None:
    pytest.importorskip("playwright")
    rec, prof = _configure_runtime_paths(monkeypatch, tmp_path)
    _make_persona(prof, "matrix")

    pool = BrowserPool()
    try:
        try:
            launched = await pool.launch(
                kind=kind,
                headed=False,
                profile="matrix",
                url="data:text/html,<h1>matrix-har</h1>",
                har=True,
            )
        except Exception as exc:
            _maybe_skip_live_engine(exc)

        assert launched["kind"] == kind
        assert isinstance(launched.get("har_path"), str)
        sid = launched["instance_id"]
        closed = await pool.close(sid)
        har_path = closed["har_path"]
        assert isinstance(har_path, str)
        hp = Path(har_path)
        assert hp.exists()
        assert hp.suffix == ".har"
        assert str(hp).startswith(str(rec))
    finally:
        await pool.shutdown()


@pytest.mark.asyncio
@pytest.mark.live_browser
@pytest.mark.engine_matrix
@pytest.mark.parametrize("kind", ["chromium", "firefox", "webkit"])
async def test_live_handoff_and_scenario_remap_per_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    pytest.importorskip("playwright")
    _rec, prof = _configure_runtime_paths(monkeypatch, tmp_path)
    _make_persona(prof, "matrix")

    pool = BrowserPool()
    spool = ScenarioPool()
    try:
        try:
            launched = await pool.launch(
                kind=kind,
                headed=False,
                profile="matrix",
                url="data:text/html,<h1>handoff-old</h1>",
                har=True,
            )
        except Exception as exc:
            _maybe_skip_live_engine(exc)

        old_id = launched["instance_id"]
        spool._live["scn-live"] = LiveScenario(
            scenario_id="scn-live",
            name="matrix",
            spec=Scenario(name="matrix", participants=[Participant(persona="matrix", kind=kind, role="player")]),
            participants=[{"role": "player", "persona": "matrix", "kind": kind, "instance_id": old_id}],
        )

        try:
            handed = await pool.handoff(old_id, headed=True)
        except Exception as exc:
            _maybe_skip_live_engine(exc)

        assert handed["old_instance_id"] == old_id
        assert handed["kind"] == kind
        assert handed["old_closed"] is True
        new_id = handed["new_instance_id"]
        assert new_id != old_id

        remap = spool.remap_participant(
            scenario_id="scn-live",
            old_instance_id=old_id,
            new_instance_id=new_id,
            role="player",
            browser_pool=pool,
        )
        assert remap["new_instance_id"] == new_id
        assert spool._live["scn-live"].participants[0]["instance_id"] == new_id

        closed = await pool.close(new_id)
        assert isinstance(closed.get("har_path"), str)
    finally:
        await pool.shutdown()
