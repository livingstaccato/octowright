# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from unittest.mock import AsyncMock

import pytest

from octowright.browser_pool.visuals import wire_init_scripts


@pytest.mark.anyio
async def test_viewport_pill_script_is_injected() -> None:
    context = type("Context", (), {"add_init_script": AsyncMock()})()

    await wire_init_scripts(
        context,
        profile=None,
        label="player",
        instance_id="abc123",
        kind="chromium",
        badge=False,
        badge_position="bottom-right",
        stabilize=False,
        viewport_mode="fixed",
        viewport_width=1280,
        viewport_height=800,
    )

    scripts = [call.kwargs["script"] for call in context.add_init_script.await_args_list]
    assert any("__octowright_viewport_status__" in script for script in scripts)
    assert any('"fixed"' in script and "1280" in script and "800" in script for script in scripts)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
