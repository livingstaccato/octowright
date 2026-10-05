# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``parameter_specs`` survive a re-save, and every macro write is serialised (#248, Part B).

``save_macro`` composed a fresh dict from the recording, so re-saving a macro
dropped the sensitivity its author had declared -- silently loosening it. It
now reads the version on disk and keeps its specs, under a write lock that
``write_macro`` and ``delete_macro`` take too, with a bounded acquire and a
named timeout. ``macro_lint`` warns when a new version makes fewer parameters
sensitive than the one on disk.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests._macro_artifact_fixtures import _reload, restore_reloaded_defaults

SPECS = {"display": {"sensitive": True}, "username": {"sensitive": False}}


@pytest.fixture(autouse=True)
def _restore() -> Any:
    yield
    restore_reloaded_defaults()


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    module, _artifacts = _reload(monkeypatch, tmp_path)
    return module


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    from starlette.testclient import TestClient

    from octowright import http as http_app
    from octowright.http import state as http_state
    from octowright.server import _state
    from tests.test_http_routes_meta_branches import _FakePool, _FakeScenarioPool

    monkeypatch.setattr(http_state, "RECORDINGS_DIR", tmp_path / "recordings")
    monkeypatch.setattr(_state, "pool", _FakePool())
    monkeypatch.setattr(_state, "scenario_pool", _FakeScenarioPool())
    return TestClient(http_app.build_app())


def _recording(tmp_path: Path) -> Path:
    path = tmp_path / "recording.jsonl"
    rows = [
        {"ts": "2026-10-04T10:00:00Z", "action": "fill", "selector": "#user", "value": "alice-header"},
        {"ts": "2026-10-04T10:00:01Z", "action": "fill", "selector": "#display", "value": "Natl-Id-77"},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    return path


def _saved(storage: Any, name: str) -> dict[str, Any]:
    return json.loads(storage.macro_path(name).read_text(encoding="utf-8"))


def _save(storage: Any, tmp_path: Path) -> Path:
    return storage.save_macro(
        recording_path=_recording(tmp_path),
        name="login",
        parameters={"username": "alice-header", "display": "Natl-Id-77"},
    )


def test_a_resave_keeps_the_declared_specs(storage: Any, tmp_path: Path) -> None:
    _save(storage, tmp_path)
    macro = _saved(storage, "login")
    storage.write_macro(name="login", macro={**macro, "parameter_specs": SPECS})

    _save(storage, tmp_path)

    resaved = _saved(storage, "login")
    assert resaved["parameter_specs"] == SPECS
    assert resaved["actions"] == macro["actions"]


def test_a_first_save_has_no_specs(storage: Any, tmp_path: Path) -> None:
    _save(storage, tmp_path)
    assert "parameter_specs" not in _saved(storage, "login")


@pytest.fixture
def held(storage: Any) -> Iterator[None]:
    """The write lock held by another thread for the length of the test."""
    acquired, release = threading.Event(), threading.Event()

    def hold() -> None:
        with storage.macro_write_lock():
            acquired.set()
            release.wait(10)

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    assert acquired.wait(5)
    try:
        yield
    finally:
        release.set()
        thread.join(5)


def test_a_save_waits_a_bounded_time_and_names_the_timeout(
    storage: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, held: None
) -> None:
    monkeypatch.setattr(storage, "MACRO_WRITE_LOCK_TIMEOUT_SECONDS", 0.05)
    with pytest.raises(storage.MacroWriteLockTimeout, match="another macro"):
        _save(storage, tmp_path)
    assert issubclass(storage.MacroWriteLockTimeout, TimeoutError)
    assert not storage.macro_path("login").exists()


def test_delete_takes_the_same_lock(storage: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _save(storage, tmp_path)
    monkeypatch.setattr(storage, "MACRO_WRITE_LOCK_TIMEOUT_SECONDS", 0.05)
    acquired, release = threading.Event(), threading.Event()

    def hold() -> None:
        with storage.macro_write_lock():
            acquired.set()
            release.wait(10)

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    assert acquired.wait(5)
    try:
        with pytest.raises(storage.MacroWriteLockTimeout):
            storage.delete_macro("login")
        with pytest.raises(storage.MacroWriteLockTimeout):
            storage.write_macro(name="login", macro={"actions": []})
        assert storage.macro_path("login").exists()
    finally:
        release.set()
        thread.join(5)


def test_the_lock_is_reentrant_for_one_writer(storage: Any) -> None:
    """A read-modify-write that ends in write_macro (repair_apply) must not deadlock on itself."""
    with storage.macro_write_lock():
        storage.write_macro(name="m", macro={"actions": []})
    assert storage.macro_path("m").exists()


def test_the_dashboard_update_writes_off_the_event_loop(
    storage: Any, monkeypatch: pytest.MonkeyPatch, client: Any
) -> None:
    from octowright.http import state as http_state

    on_loop: list[bool] = []
    real = storage.write_macro

    def spy(**kwargs: Any) -> Path:
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real(**kwargs)

    monkeypatch.setattr(http_state._macros, "write_macro", spy)
    monkeypatch.setattr(http_state._macros, "load_macro", storage.load_macro)
    response = client.put("/api/macros/m", json={"macro": {"name": "m", "actions": []}})

    assert response.status_code == 200, response.text
    assert on_loop == [False]


# -- the shrink warning ----------------------------------------------------------


def _codes(macro: dict[str, Any], previous: dict[str, Any] | None) -> list[str]:
    from octowright.macros.lint import lint_macro

    return [issue.code for issue in lint_macro(macro, previous=previous)]


def _macro(specs: dict[str, Any] | None = None, parameters: list[str] | None = None) -> dict[str, Any]:
    macro: dict[str, Any] = {
        "name": "m",
        "parameters": parameters if parameters is not None else ["username", "display"],
        "actions": [{"action": "click", "selector": "#go"}],
    }
    if specs is not None:
        macro["parameter_specs"] = specs
    return macro


def test_lint_warns_when_a_declared_sensitive_parameter_is_dropped() -> None:
    from octowright.macros.lint import lint_macro

    issues = lint_macro(_macro({}), previous=_macro({"display": {"sensitive": True}}))
    (shrank,) = [issue for issue in issues if issue.code == "sensitive_parameters_shrank"]
    assert shrank.severity == "warning"
    assert "'display'" in shrank.message


def test_lint_warns_when_a_name_classified_parameter_is_declared_public() -> None:
    assert "sensitive_parameters_shrank" in _codes(_macro({"username": {"sensitive": False}}), _macro())


def test_lint_is_quiet_when_nothing_shrinks_or_there_is_no_previous_version() -> None:
    assert "sensitive_parameters_shrank" not in _codes(_macro({"display": {"sensitive": True}}), _macro())
    assert "sensitive_parameters_shrank" not in _codes(_macro({}), None)
    # A parameter removed from the macro receives no value, so it shrinks nothing.
    assert "sensitive_parameters_shrank" not in _codes(_macro({}, ["display"]), _macro({}, ["username", "display"]))


def test_the_dashboard_validation_compares_against_the_version_on_disk(
    storage: Any, monkeypatch: pytest.MonkeyPatch, client: Any
) -> None:
    from octowright.http import state as http_state

    storage.write_macro(name="m", macro=_macro({"display": {"sensitive": True}}))
    monkeypatch.setattr(http_state._macros, "load_macro", storage.load_macro)

    response = client.post("/api/macros/m/validate", json={"macro": _macro({})})

    assert response.status_code == 200
    assert "sensitive_parameters_shrank" in [issue["code"] for issue in response.json()["issues"]]


def test_the_macro_detail_carries_the_specs(storage: Any, monkeypatch: pytest.MonkeyPatch, client: Any) -> None:
    from octowright.http import state as http_state

    storage.write_macro(name="m", macro=_macro(SPECS))
    monkeypatch.setattr(http_state._macros, "load_macro", storage.load_macro)

    assert client.get("/api/macros/m").json()["parameter_specs"] == SPECS
