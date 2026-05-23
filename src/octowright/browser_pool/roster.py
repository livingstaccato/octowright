# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from provide.telemetry import get_logger

from octowright._tracing import set_attrs, span
from octowright.browser_pool.visuals import _BADGE_POSITION_DEFAULT

if TYPE_CHECKING:
    from octowright.browser_pool.pool import BrowserPool

log = get_logger(__name__)


async def close_all(pool: BrowserPool) -> dict[str, Any]:
    # Close every session concurrently. A single hung browser would otherwise
    # block daemon shutdown if we awaited them serially; gather + return_exceptions
    # lets the rest tear down even if one raises or stalls.
    ids = [session.instance_id for session in pool.iter_sessions()]
    results = await asyncio.gather(*(pool.close(iid) for iid in ids), return_exceptions=True)
    for iid, result in zip(ids, results, strict=True):
        if isinstance(result, BaseException):
            log.warning("octowright.browser.close_all_failed", instance_id=iid, error=repr(result))
    return {"closed": ids}


async def spawn_roster(pool: BrowserPool, specs: list[dict[str, Any]]) -> dict[str, Any]:
    async def _launch_one(spec: dict[str, Any]) -> dict[str, Any]:
        return await pool.launch(
            kind=spec.get("kind", "chromium"),
            url=spec.get("url"),
            headed=spec.get("headed"),
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

    with span("octowright.browser.spawn_roster", roster_size=len(specs)) as sp:
        results = await asyncio.gather(*[_launch_one(s) for s in specs], return_exceptions=True)
        launched: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for spec, result in zip(specs, results, strict=True):
            if isinstance(result, BaseException):
                errors.append({"spec": spec, "error": str(result)})
            else:
                launched.append(result)
        set_attrs(sp, launched=len(launched), failed=len(errors))
        return {"launched": launched, "errors": errors}
