# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tool-chosen write paths may replace only a file of their own kind.

``browser_screenshot``, ``browser_capture_and_close`` and ``macro_export_cli``
checked containment under the recordings root and nothing else, and each write
is an atomic replace: a contained path could overwrite another session's
``.jsonl`` recording or a macro's ``artifact.json`` with PNG bytes or generated
Python. ``browser_export_script`` checked suffix and directory, but compared
the directory case-sensitively, so ``Artifacts/`` passed on a case-insensitive
filesystem where it IS ``artifacts/``.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from octowright._paths import checked_write_target
from octowright.request_errors import InvalidRequestError
from octowright.server.browser import inspect as _inspect
from octowright.server.browser import inspect_capture as _inspect_capture
from octowright.write_targets import checked_macro_export_target, checked_screenshot_target

#: Destinations no screenshot may replace, with why.
REFUSED_FOR_SCREENSHOT = [
    ("20260101T000000Z-chromium-other1.jsonl", "another session's recording"),
    ("20260101T000000Z-chromium-other1.har", "another session's HAR"),
    ("artifacts/macros/login/artifact.json", "a macro artifact manifest"),
    ("artifacts/macros/login/shot.png", "anything under artifacts/"),
    ("Artifacts/macros/login/shot.PNG", "artifacts/ in another case"),
    ("session-artifacts/abc123/x.png", "a plugin's committed session artifact"),
    ("SESSION-ARTIFACTS/abc123/x.png", "session-artifacts/ in another case"),
]


@pytest.fixture
def recordings(tmp_path: Path) -> Path:
    root = tmp_path / "recordings"
    root.mkdir()
    return root


def _plant(root: Path, relative: str) -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("KEEP")
    return target


def test_helper_compares_suffix_and_directory_casefolded(recordings: Path) -> None:
    ok = checked_write_target(
        recordings / "shots" / "a.PNG", recordings, label="x", allowed_suffixes=(".png",), forbidden_subdirs=("artifacts",)
    )
    assert ok == (recordings / "shots" / "a.PNG").resolve()
    with pytest.raises(InvalidRequestError, match="must end in"):
        checked_write_target(recordings / "a.jsonl", recordings, label="x", allowed_suffixes=(".png",))
    with pytest.raises(InvalidRequestError, match="which this tool never writes"):
        checked_write_target(
            recordings / "ARTIFACTS" / "a.png",
            recordings,
            label="x",
            allowed_suffixes=(".png",),
            forbidden_subdirs=("artifacts",),
        )
    # A FILE named like a forbidden directory at the root is not inside it.
    assert checked_write_target(
        recordings / "artifacts.png", recordings, label="x", allowed_suffixes=(".png",), forbidden_subdirs=("artifacts",)
    )


@pytest.mark.parametrize(("relative", "why"), REFUSED_FOR_SCREENSHOT)
def test_screenshot_target_refuses_what_is_not_a_screenshot(recordings: Path, relative: str, why: str) -> None:
    with pytest.raises(InvalidRequestError, match="screenshot path"):
        checked_screenshot_target(recordings / relative, recordings)


@pytest.mark.parametrize("name", ["s.png", "s.jpg", "s.JPEG", "shots/deep/s.png"])
def test_screenshot_target_accepts_an_image_path(recordings: Path, name: str) -> None:
    assert checked_screenshot_target(recordings / name, recordings) == (recordings / name).resolve()


@pytest.fixture
def screenshot_tools(monkeypatch: pytest.MonkeyPatch, recordings: Path) -> SimpleNamespace:
    log = recordings / "20260101T000000Z-chromium-abc123.jsonl"
    log.write_text(json.dumps({"action": "launch"}) + "\n")
    written: list[Path] = []

    async def _screenshot(target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\x89PNG fake")
        written.append(target)
        return target

    session = SimpleNamespace(log_path=log, screenshot=_screenshot)

    @asynccontextmanager
    async def _operation(_pool, _instance_id, _name):
        yield session

    pool = MagicMock()
    pool.get.return_value = session
    monkeypatch.setattr(_inspect, "browser_operation", _operation)
    monkeypatch.setattr(_inspect, "RECORDINGS_DIR", recordings)
    monkeypatch.setattr(_inspect_capture, "pool", pool)
    monkeypatch.setattr(_inspect_capture, "RECORDINGS_DIR", recordings)
    return SimpleNamespace(written=written, log=log)


@pytest.mark.anyio
@pytest.mark.parametrize(("relative", "why"), REFUSED_FOR_SCREENSHOT)
async def test_browser_screenshot_never_replaces_a_recording_or_artifact(
    screenshot_tools: SimpleNamespace, recordings: Path, relative: str, why: str
) -> None:
    target = _plant(recordings, relative)
    with pytest.raises(InvalidRequestError, match="screenshot path"):
        await _inspect.browser_screenshot("abc123", path=str(target))
    assert target.read_text() == "KEEP", why
    assert screenshot_tools.written == []


@pytest.mark.anyio
async def test_browser_screenshot_still_writes_its_default_and_a_chosen_png(
    screenshot_tools: SimpleNamespace, recordings: Path
) -> None:
    default = await _inspect.browser_screenshot("abc123")
    chosen = await _inspect.browser_screenshot("abc123", path=str(recordings / "shots" / "x.png"))
    assert Path(default["path"]) == screenshot_tools.log.with_suffix(".png").resolve()
    assert Path(chosen["path"]).read_bytes().startswith(b"\x89PNG")


@pytest.mark.anyio
@pytest.mark.parametrize(("relative", "why"), REFUSED_FOR_SCREENSHOT)
async def test_capture_and_close_never_replaces_a_recording_or_artifact(
    screenshot_tools: SimpleNamespace, recordings: Path, relative: str, why: str
) -> None:
    target = _plant(recordings, relative)
    with pytest.raises(InvalidRequestError, match="screenshot path"):
        await _inspect_capture.browser_capture_and_close("abc123", screenshot_path=str(target))
    assert target.read_text() == "KEEP", why


@pytest.mark.parametrize(
    ("relative", "why"),
    [
        ("20260101T000000Z-chromium-other1.jsonl", "a recording"),
        ("artifacts/macros/login/artifact.json", "a macro artifact manifest"),
        ("artifacts/macros/login/Artifact.JSON", "a manifest in another case"),
        ("session-artifacts/abc123/x.py", "a plugin's committed session artifact"),
        ("Session-Artifacts/abc123/x.py", "the same in another case"),
        ("out.txt", "not a Python script"),
    ],
)
def test_macro_export_target_refuses_what_is_not_a_macro_script(recordings: Path, relative: str, why: str) -> None:
    with pytest.raises(InvalidRequestError, match="macro export path"):
        checked_macro_export_target(recordings / relative, recordings)


def test_macro_export_cli_keeps_its_default_and_relative_destinations(recordings: Path) -> None:
    from octowright.artifacts.paths import ArtifactStore

    store = ArtifactStore(recordings_dir=recordings)
    default = store.resolve_macro_export_path("login", None)
    relative = store.resolve_macro_export_path("login", "mine/login.py")
    absolute = store.resolve_macro_export_path("login", str(recordings / "artifacts/macros/login/exports/x.py"))

    assert default == recordings / "artifacts" / "macros" / "login" / "exports" / "login.py"
    assert relative == (recordings / "artifacts" / "mine" / "login.py").resolve()
    assert absolute.name == "x.py"
    with pytest.raises(InvalidRequestError, match="macro export path"):
        store.resolve_macro_export_path("login", "macros/login/artifact.json")
    with pytest.raises(InvalidRequestError, match="macro export path"):
        store.resolve_macro_export_path("login", str(recordings / "20260101T000000Z-chromium-x.jsonl"))
