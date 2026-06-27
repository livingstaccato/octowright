# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import anyio
from provide.telemetry import get_logger

from octowright._tracing import set_attrs, span
from octowright.browser_pool.errors import ProtectedBrowserCloseError
from octowright.browser_pool.events import SessionCloseReason
from octowright.browser_pool.limits import enforce_launch_limits
from octowright.browser_pool.visuals import _BADGE_POSITION_DEFAULT

if TYPE_CHECKING:
    from octowright.browser_pool.pool import BrowserPool

log = get_logger(__name__)


async def shielded_rollback_close(
    pool: BrowserPool,
    instance_ids: list[str],
    *,
    logger: Any,
    event: str,
) -> None:
    """Close ``instance_ids`` best-effort while unwinding an error/cancellation.

    Wrapped in a shielded cancel scope so an incoming ``CancelledError`` can't
    abort the loop before every browser is closed, and each close is *awaited*
    (not deferred as a detached ``create_task``, which the event loop may never
    run during teardown). Close failures are logged, never raised — the caller
    re-raises the original exception once cleanup completes.
    """
    with anyio.CancelScope(shield=True):
        for instance_id in instance_ids:
            try:
                await pool.close(instance_id, force=True)
            except Exception as exc:
                logger.warning(event, instance_id=instance_id, error=repr(exc))


async def close_all(
    pool: BrowserPool,
    *,
    force: bool = False,
    _reason: SessionCloseReason = "agent_close",
) -> dict[str, Any]:
    # Close every session concurrently. A single hung browser would otherwise
    # block daemon shutdown if we awaited them serially; gather + return_exceptions
    # lets the rest tear down even if one raises or stalls.
    ids = [session.instance_id for session in pool.iter_sessions()]
    results = await asyncio.gather(
        *(pool.close(iid, force=force, _reason=_reason) for iid in ids),
        return_exceptions=True,
    )
    closed: list[str] = []
    skipped_protected: list[str] = []
    failed: list[dict[str, str]] = []
    for iid, result in zip(ids, results, strict=True):
        if isinstance(result, BaseException):
            if isinstance(result, ProtectedBrowserCloseError):
                skipped_protected.append(iid)
            else:
                log.warning("octowright.browser.close_all_failed", instance_id=iid, error=repr(result))
                failed.append({"instance_id": iid, "error": f"{type(result).__name__}: {result}"})
        else:
            closed.append(iid)
    body: dict[str, Any] = {"closed": closed}
    if failed:
        body["failed"] = failed
    if skipped_protected:
        body["skipped_protected"] = skipped_protected
        if closed:
            body["message"] = (
                f"Closed {len(closed)} unprotected browser(s); "
                f"skipped {len(skipped_protected)} protected browser(s). "
                "Pass force=True to also close protected browsers."
            )
        else:
            body["message"] = f"All {len(skipped_protected)} browser(s) are protected. Pass force=True to close them."
    return body


async def spawn_roster(pool: BrowserPool, specs: list[dict[str, Any]]) -> dict[str, Any]:
    # The single chokepoint both the browser_spawn_roster tool AND scenario_start
    # (pool.spawn_roster) route through. Enforce the cap + memory floor here so the
    # scenario path can't bypass a tool-only check and OOM the shared host.
    # All-or-nothing: refuse the whole batch before launching any browser.
    enforce_launch_limits(pool, adding=len(specs))

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
        cancelled: list[BaseException] = []
        for spec, result in zip(specs, results, strict=True):
            if isinstance(result, asyncio.CancelledError):
                # Cancellation during a user-initiated launch path is not a
                # "soft success" — propagate after collecting all results so
                # the caller (and any structured-concurrency parent) sees it.
                cancelled.append(result)
                errors.append({"spec": spec, "error": "cancelled"})
            elif isinstance(result, BaseException):
                errors.append({"spec": spec, "error": str(result)})
            else:
                launched.append(result)
        set_attrs(sp, launched=len(launched), failed=len(errors))
        if cancelled:
            # Cancellation during a user-initiated launch is not a "soft success" —
            # close the siblings that did launch (shielded + awaited so the
            # cleanup completes) before propagating the cancellation up.
            await shielded_rollback_close(
                pool,
                [info["instance_id"] for info in launched],
                logger=log,
                event="octowright.spawn_roster.rollback_close_failed",
            )
            raise cancelled[0]
        return {"launched": launched, "errors": errors}
