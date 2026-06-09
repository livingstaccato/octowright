# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.server import macros as _macros


@pytest.fixture(autouse=True)
def _patch_deps(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    fake_pool = MagicMock()
    fake_macros = MagicMock()
    monkeypatch.setattr(_macros, "pool", fake_pool)
    monkeypatch.setattr(_macros, "macro_mod", fake_macros)
    return {"pool": fake_pool, "macros": fake_macros}


def test_macro_save_list_delete(_patch_deps: dict[str, MagicMock]) -> None:
    session = MagicMock()
    session.log_path = "/tmp/log.jsonl"
    _patch_deps["pool"].get.return_value = session
    _patch_deps["macros"].save_macro.return_value = "/tmp/m.json"
    _patch_deps["macros"].list_macros.return_value = [{"name": "m"}]
    _patch_deps["macros"].delete_macro.return_value = "/tmp/m.json"

    saved = _macros.macro_save("i-1", "m")
    listed = _macros.macro_list()
    deleted = _macros.macro_delete("m")

    assert saved["saved"] is True
    assert listed == [{"name": "m"}]
    assert deleted["deleted"] is True


@pytest.mark.anyio
async def test_macro_run_and_sequence(_patch_deps: dict[str, MagicMock]) -> None:
    session = MagicMock()
    _patch_deps["pool"].get.return_value = session
    _patch_deps["macros"].run_macro = AsyncMock(return_value={"executed": 2})
    _patch_deps["macros"].run_sequence = AsyncMock(return_value={"total": 2})

    ran = await _macros.macro_run("i-2", "m", args={"k": "v"})
    seq = await _macros.macro_run_sequence("i-2", ["a", "b"], args_list=[{}, {}], stop_on_failure=False)

    assert ran["executed"] == 2
    assert seq["total"] == 2


def test_macro_lint_formats_issues(monkeypatch: pytest.MonkeyPatch) -> None:
    import octowright.macros.lint as lint_module

    issue_err = MagicMock(severity="error", code="E1", message="bad", action_index=1)
    issue_warn = MagicMock(severity="warning", code="W1", message="warn", action_index=2)
    monkeypatch.setattr(lint_module, "lint_macro", MagicMock(return_value=[issue_err, issue_warn]))

    cast(Any, _macros.macro_mod).load_macro.return_value = {"name": "demo", "actions": []}
    out = _macros.macro_lint("demo")
    assert out["ok"] is False
    assert "errors" in out["summary"]
    assert len(out["issues"]) == 2


def test_macro_repair_preview_forwards_to_core(_patch_deps: dict[str, MagicMock]) -> None:
    fake_macros = _patch_deps["macros"]
    fake_macros.repair_preview.return_value = {"macro": "demo", "suggestions": []}

    out = _macros.macro_repair_preview("demo")

    assert out == {"macro": "demo", "suggestions": []}
    fake_macros.repair_preview.assert_called_once_with("demo")


def test_macro_repair_apply_forwards_to_core(_patch_deps: dict[str, MagicMock]) -> None:
    fake_macros = _patch_deps["macros"]
    fake_macros.repair_apply.return_value = {
        "macro": "demo",
        "action_index": 0,
        "applied": True,
        "original_action": {"action": "click", "selector": "#x", "label": "L"},
        "replacement_action": {"action": "click_by", "label": "L"},
        "path": "/tmp/demo.json",
    }

    out = _macros.macro_repair_apply("demo", 0)

    assert out["applied"] is True
    assert out["replacement_action"] == {"action": "click_by", "label": "L"}
    fake_macros.repair_apply.assert_called_once_with("demo", 0)


def test_macro_compile_returns_compiled_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    import octowright.macros.dsl as dsl_mod

    compile_mock = MagicMock(return_value={"name": "demo", "actions": [{"action": "press_key", "key": "Escape"}]})
    monkeypatch.setattr(dsl_mod, "compile_macro_yaml", compile_mock)

    out = _macros.macro_compile("name: demo\nactions:\n  - press_key: Escape\n", name="demo", write=False)

    assert out["compiled"]["name"] == "demo"
    assert out["written"] is False
    compile_mock.assert_called_once_with("name: demo\nactions:\n  - press_key: Escape\n", name="demo", strict=True)


def test_macro_compile_can_write_compiled_macro(
    _patch_deps: dict[str, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import octowright.macros.dsl as dsl_mod

    compiled = {"name": "demo", "actions": [{"action": "press_key", "key": "Escape"}]}
    monkeypatch.setattr(dsl_mod, "compile_macro_yaml", MagicMock(return_value=compiled))
    _patch_deps["macros"].write_macro.return_value = Path("/tmp/demo.json")

    out = _macros.macro_compile("name: demo\nactions: []\n", write=True)

    assert out["written"] is True
    assert out["path"] == str(Path("/tmp/demo.json"))
    _patch_deps["macros"].write_macro.assert_called_once_with(name="demo", macro=compiled)


@pytest.mark.anyio
async def test_run_test_suite_forwards(monkeypatch: pytest.MonkeyPatch) -> None:
    import octowright.runner as runner_mod

    run_suite_mock = AsyncMock(return_value={"passed": 1, "failed": 0, "total": 1})
    monkeypatch.setattr(runner_mod, "run_suite", run_suite_mock)
    out = await _macros.run_test_suite(
        kind="firefox",
        tag="smoke",
        out_path="/tmp/j.xml",
        max_parallel=3,
    )
    assert out["total"] == 1
    run_suite_mock.assert_awaited_once_with(
        kind="firefox",
        tag="smoke",
        out_path="/tmp/j.xml",
        pool=_macros.pool,
        max_parallel=3,
    )


def test_profile_cleanup_wraps_stale_and_in_use(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import octowright.defaults as defaults_mod
    import octowright.profile_cleanup as cleanup_mod

    stale = MagicMock(persona="cosmo", engine="webkit", path=tmp_path / "p", size_bytes=12, age_days=4.2)
    monkeypatch.setattr(cleanup_mod, "find_stale_profiles", MagicMock(return_value=[stale]))
    monkeypatch.setattr(
        cleanup_mod,
        "cleanup_stale",
        MagicMock(return_value={"removed_count": 1, "removed_bytes": 12, "errors": []}),
    )
    monkeypatch.setattr(defaults_mod, "PROFILES_DIR", tmp_path)

    in_use = MagicMock()
    in_use.user_data_dir = str(tmp_path / "live")
    pool_mock = cast(Any, _macros.pool)
    pool_mock._sessions = {"x": in_use}
    pool_mock.iter_sessions.return_value = (in_use,)
    out = _macros.profile_cleanup(days=1.0, dry_run=False)
    assert out["removed"] == 1
    assert out["skipped_in_use"] == 1
