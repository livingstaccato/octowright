# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

import importlib
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import octowright.goldens


@pytest.fixture
def temp_goldens(monkeypatch, tmp_path):
    monkeypatch.setenv("OCTOWRIGHT_GOLDENS_DIR", str(tmp_path / "goldens"))
    # GOLDENS_DIR is owned by defaults; reload it first.
    from octowright import defaults

    importlib.reload(defaults)
    importlib.reload(octowright.goldens)
    return octowright.goldens.GOLDENS_DIR


@pytest.fixture
def mock_pool(monkeypatch):
    from octowright.server import _state, goldens

    mock_session = MagicMock()
    mock_session.snapshot = AsyncMock(return_value={"role": "Root"})
    mock_session.page.url = "https://example.com"

    mpool = MagicMock()
    mpool.get.return_value = mock_session
    monkeypatch.setattr(_state, "pool", mpool)
    monkeypatch.setattr(goldens, "pool", mpool)
    return mpool


@pytest.mark.anyio
async def test_golden_save_creates_file(mock_pool, temp_goldens):
    from octowright.server.goldens import golden_save

    result = await golden_save("inst-1", name="manual-save", description="test save")

    assert result["saved"] is True
    assert (temp_goldens / "manual-save.json").exists()

    data = json.loads((temp_goldens / "manual-save.json").read_text())
    assert data["name"] == "manual-save"
    assert data["tree"] == {"role": "Root"}
    assert data["url"] == "https://example.com"
    assert data["description"] == "test save"


@pytest.mark.anyio
async def test_golden_verify_loop_reports_diffs(mock_pool, temp_goldens):
    from octowright.server.goldens import golden_save, golden_verify_loop

    # 1. Save a golden with "Root"
    await golden_save("inst-1", name="auto-login")

    # 2. Change the session's snapshot to something different
    mock_session = mock_pool.get.return_value
    mock_session.snapshot.return_value = {"role": "Root", "children": [{"role": "button", "name": "Login"}]}

    # 3. Verify loop should report diffs
    result = await golden_verify_loop("inst-1", name="auto-login")

    assert result["ok"] is False
    assert result["diff_count"] > 0
    assert len(result["diffs"]) > 0


@pytest.mark.anyio
async def test_golden_verify_loop_ok_on_match(mock_pool, temp_goldens):
    from octowright.server.goldens import golden_save, golden_verify_loop

    # 1. Save a golden with "Root"
    await golden_save("inst-1", name="auto-login")

    # 2. Keep the snapshot the same
    # 3. Verify loop should report ok
    result = await golden_verify_loop("inst-1", name="auto-login")

    assert result["ok"] is True
    assert result["diffs"] == 0


@pytest.mark.anyio
async def test_golden_assert_and_list_delete(mock_pool, temp_goldens):
    from octowright.server.goldens import golden_assert, golden_delete, golden_list, golden_save

    await golden_save("inst-1", name="to-delete")
    ok = await golden_assert("inst-1", "to-delete")
    assert ok["ok"] is True
    listed = golden_list()
    assert any(item["name"] == "to-delete" for item in listed)
    deleted = golden_delete("to-delete")
    assert deleted["deleted"] is True


@pytest.mark.anyio
async def test_golden_assert_raises_on_diff(mock_pool, temp_goldens):
    from octowright.server.goldens import golden_assert, golden_save

    await golden_save("inst-1", name="mismatch")
    mock_session = mock_pool.get.return_value
    mock_session.snapshot.return_value = {"role": "Root", "children": [{"role": "button"}]}
    with pytest.raises(RuntimeError):
        await golden_assert("inst-1", "mismatch")
