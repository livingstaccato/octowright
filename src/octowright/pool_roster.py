# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from .pool_support import _BADGE_POSITION_DEFAULT

if TYPE_CHECKING:
    from .pool import BrowserPool


async def close_all(pool: BrowserPool) -> dict[str, Any]:
    ids = [session.instance_id for session in pool.iter_sessions()]
    for iid in ids:
        await pool.close(iid)
    return {"closed": ids}


async def spawn_roster(pool: BrowserPool, specs: list[dict[str, Any]]) -> dict[str, Any]:
    async def _launch_one(spec: dict[str, Any]) -> dict[str, Any]:
        return await pool.launch(
            kind=spec.get("kind", "chromium"),
            url=spec.get("url"),
            headed=spec.get("headed", True),
            label=spec.get("label"),
            viewport_w=spec.get("viewport_w"),
            viewport_h=spec.get("viewport_h"),
            profile=spec.get("profile"),
            record_video=spec.get("record_video", False),
            stabilize=spec.get("stabilize", False),
            trace=spec.get("trace", False),
            har=spec.get("har", False),
            har_path=spec.get("har_path"),
            har_mode=spec.get("har_mode", "minimal"),
            har_url_filter=spec.get("har_url_filter"),
            har_content=spec.get("har_content"),
            badge=spec.get("badge", True),
            badge_position=spec.get("badge_position", _BADGE_POSITION_DEFAULT),
            tile=spec.get("tile", False),
            ephemeral=spec.get("ephemeral", False),
            session=spec.get("session", False),
        )

    results = await asyncio.gather(*[_launch_one(s) for s in specs], return_exceptions=True)
    launched: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for spec, result in zip(specs, results, strict=True):
        if isinstance(result, BaseException):
            errors.append({"spec": spec, "error": str(result)})
        else:
            launched.append(result)
    return {"launched": launched, "errors": errors}
