# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

import anyio
from provide.telemetry import get_logger

from octowright._tracing import set_attrs, span
from octowright.browser_pool.errors import ProtectedBrowserCloseError
from octowright.browser_pool.events import SessionCloseReason
from octowright.browser_pool.lifecycle import CloseCoordinatorOutcome, reserve_close_browser
from octowright.browser_pool.limits import enforce_launch_limits, headed_launch_concurrency
from octowright.browser_pool.options import LaunchOptions

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


async def _await_reserved_or_return_error(entry: Any) -> Any:
    """Second-stage helper for ``close_all``: pass a first-stage reservation
    failure through unchanged, otherwise await that entry's own outcome."""
    if isinstance(entry, BaseException):
        return entry
    try:
        outcome = await entry.reservation.wait()
    except BaseException as exc:
        return exc
    return cast(CloseCoordinatorOutcome, outcome).response


def _close_all_candidate_ids(
    pool: BrowserPool,
    *,
    exclude_labels: list[str] | None,
    exclude_profiles: list[str] | None,
) -> list[str]:
    """Sessions eligible for ``close_all``, minus any excluded by label/profile."""
    label_set = set(exclude_labels) if exclude_labels else None
    profile_set = set(exclude_profiles) if exclude_profiles else None
    return [
        session.instance_id
        for session in pool.iter_sessions()
        if (label_set is None or session.label not in label_set)
        and (profile_set is None or session.profile not in profile_set)
    ]


async def close_all(
    pool: BrowserPool,
    *,
    force: bool = False,
    exclude_labels: list[str] | None = None,
    exclude_profiles: list[str] | None = None,
    _reason: SessionCloseReason = "agent_close",
) -> dict[str, Any]:
    # Two-stage: reserve every session's close cutoff FIRST (so every session
    # is admitted-or-refused up front, atomically with respect to each
    # other), THEN await every outcome. Never hold one session's lease while
    # reserving or awaiting another's -- a single hung browser's teardown
    # must not block the others from even being cut off.
    ids = _close_all_candidate_ids(pool, exclude_labels=exclude_labels, exclude_profiles=exclude_profiles)
    entries = await asyncio.gather(
        *(reserve_close_browser(pool, iid, force=force, reason=_reason) for iid in ids),
        return_exceptions=True,
    )
    results = await asyncio.gather(
        *(_await_reserved_or_return_error(entry) for entry in entries),
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


def roster_launch_kwargs(spec: dict[str, Any]) -> dict[str, Any]:
    """``pool.launch`` kwargs for one roster spec, through ``LaunchOptions``.

    A hand-kept key list here silently dropped every option it did not name
    -- the documented ``protected`` among them -- and silently ignored unknown
    keys such as ``headless`` (inverted sense). Going through ``from_mapping``
    refuses an unknown key with the same message every other launch path gives.
    An explicit null (a YAML ``key:``) means unset for EVERY key, so it is
    dropped and the dataclass default applies; for ``headed`` that default is
    ``None`` (auto), the same as the null. Internal-only options
    (``INTERNAL_ONLY_LAUNCH_FIELDS``) are refused, as at every external entry."""
    return LaunchOptions.from_external_mapping(
        {k: v for k, v in spec.items() if v is not None}, source="in a roster spec"
    ).to_pool_kwargs()


async def spawn_roster(pool: BrowserPool, specs: list[dict[str, Any]]) -> dict[str, Any]:
    # The single chokepoint both the browser_spawn_roster tool AND scenario_start
    # (pool.spawn_roster) route through. Enforce the cap + memory floor here so the
    # scenario path can't bypass a tool-only check and OOM the shared host.
    # All-or-nothing: refuse the whole batch before launching any browser.
    enforce_launch_limits(pool, adding=len(specs))

    # DEFENSIVE cap on simultaneous HEADED launches (not a proven crash fix — the
    # observed crash reproduces via sequential churn, not this concurrent path; see
    # limits.headed_launch_concurrency). Bounds window-server/GPU pressure from
    # firing a big roster's headed launches at the same instant; headless is immune
    # so it stays fully parallel. Per-call semaphore.
    headed_gate = asyncio.Semaphore(headed_launch_concurrency())

    async def _do_launch(spec: dict[str, Any]) -> dict[str, Any]:
        return await pool.launch(**roster_launch_kwargs(spec))

    async def _launch_one(spec: dict[str, Any]) -> dict[str, Any]:
        # Headless (headed is False) never storms — launch it unthrottled. Headed
        # or auto (headed None → resolves headed by default) goes through the gate.
        if spec.get("headed") is False:
            return await _do_launch(spec)
        async with headed_gate:
            return await _do_launch(spec)

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
