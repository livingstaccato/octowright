# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Registry dispatch for the HTTP session routes.

Lives beside the routes rather than inside ``sessions.py`` because "resolve a
session across every registered kind" is one responsibility with its own tests,
and ``sessions.py`` is already the largest module in this package.

Core keeps no parallel session table: a plugin's ``SessionPool`` is the single
registry for its kind, so every lookup here iterates the registered pools.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from octowright.http import state
from octowright.http.discovery import _live_summary


def iter_plugin_sessions() -> Iterator[Any]:
    """Yield every live session across every registered plugin pool.

    Guarded per pool: a pool whose ``iter_sessions()`` raises (either on the
    call itself or partway through iteration) must not stop later pools from
    being listed — the same isolation ``plugin_session_detail`` and
    ``_close_plugin_pools_on_shutdown`` already give a single misbehaving
    plugin. Without this, one bad pool 500s ``GET /api/sessions``, which the
    dashboard auto-polls, blanking the live list for browsers and terminals
    too.
    """
    for kind, pool in state.plugin_registry.pools().items():
        try:
            yield from pool.iter_sessions()
        except Exception as exc:  # a bad plugin must not 500 the dashboard
            state.log.warning(
                "octowright.http.plugin_pool_iteration_failed",
                kind=kind,
                error=repr(exc),
            )
            continue


def find_plugin_session(instance_id: str) -> tuple[str, Any] | None:
    """Resolve ``instance_id`` across registered pools.

    Returns ``(kind, session)`` or ``None``. Instance ids are unique across
    all pools — core enforces that at launch commit — so the first match is
    the only match.

    Guarded per pool for the same reason ``iter_plugin_sessions`` and
    ``plugin_session_detail`` are, and with more reach than either: this
    function runs *first* in both ``session_detail`` and ``session_close``,
    before the browser pool is consulted at all. A pool whose ``maybe_get``
    raises — mid-teardown, a half-rolled-back registration, a third-party bug
    — would therefore 500 the detail page and the close button for **browser**
    sessions too, not just its own. A raising pool is skipped: it cannot
    answer for the id, and treating "this pool is broken" as "this pool has no
    such session" is exactly the degradation the contract promises.
    """
    for kind, pool in state.plugin_registry.pools().items():
        try:
            session = pool.maybe_get(instance_id)
        except Exception as exc:  # a bad plugin must not 500 unrelated sessions
            state.log.warning(
                "octowright.http.plugin_pool_lookup_failed",
                kind=kind,
                instance_id=instance_id,
                error=repr(exc),
            )
            continue
        if session is not None:
            return kind, session
    return None


def plugin_session_detail(kind: str, session: Any) -> dict[str, Any]:
    """Build a plugin session's dashboard detail payload.

    Every plugin gets the same uniform base — ``_live_summary(session)``, the
    same started_at/live/protected/event/console/download/page-count fields a
    browser or terminal session reports — and supplies only its own extras on
    top. ``_live_summary`` is written generically (``getattr`` with fallbacks,
    ``operation_gate`` added only when the session actually supplies one), so
    it works on any conforming ``SessionRecord`` unchanged. The descriptor's
    own fields win on conflict.

    Both halves of the descriptor-specific work are guarded independently so a
    failure in either degrades to a partial payload rather than a 500: an
    enabled plugin shares the leader's process, but a bad detail builder — or
    a committed artifact file that vanishes between
    ``read_registered_artifacts``'s existence check and this function's own
    ``stat`` (a concurrent ``recordings_cleanup``, or a plugin rotating its
    own artifact) — must not take a dashboard page down with it.

    Artifacts are reported by id and mime type only. The absolute path stays
    server-side — the dashboard fetches through the artifact route, which
    re-validates containment on every request.
    """
    from octowright.plugins.artifacts import read_registered_artifacts

    # Guarded like the descriptor call below, and for the same reason: this is
    # not purely core code. `_live_summary` reads plugin-owned attributes and
    # CALLS the session's own `operation_snapshot()`, so a bad plugin can raise
    # from in here too. Unguarded it would 500 the dashboard -- exactly what
    # this function's contract says must not happen.
    try:
        base = _live_summary(session)
    except Exception as exc:
        state.log.warning(
            "octowright.http.plugin_session_summary_failed",
            kind=kind,
            instance_id=getattr(session, "instance_id", None),
            error=repr(exc),
        )
        base = {"id": getattr(session, "instance_id", None), "kind": kind, "summary_error": repr(exc)}
    try:
        plugin_detail = dict(state.plugin_registry.get_plugin(kind).descriptor.session_detail(session))
    except Exception as exc:  # a bad plugin must not 500 the dashboard
        state.log.warning(
            "octowright.http.plugin_session_detail_failed",
            kind=kind,
            instance_id=getattr(session, "instance_id", None),
            error=repr(exc),
        )
        plugin_detail = {"id": getattr(session, "instance_id", None), "kind": kind, "detail_error": repr(exc)}
    detail = {**base, **plugin_detail}

    reported_artifacts: list[dict[str, Any]] = []
    for artifact in read_registered_artifacts(Path(session.log_path), Path(state.RECORDINGS_DIR)):
        try:
            size = artifact.path.stat().st_size
        except OSError as exc:
            # read_registered_artifacts already confirmed the file existed;
            # a race between that check and this stat drops just this one
            # entry rather than failing the whole detail response.
            state.log.warning(
                "octowright.http.plugin_session_artifact_stat_failed",
                kind=kind,
                instance_id=getattr(session, "instance_id", None),
                artifact_id=artifact.artifact_id,
                error=repr(exc),
            )
            continue
        reported_artifacts.append({"artifact_id": artifact.artifact_id, "mime_type": artifact.mime_type, "bytes": size})
    detail["artifacts"] = reported_artifacts
    return detail


async def close_plugin_session(instance_id: str, *, force: bool) -> dict[str, Any] | None:
    """Close ``instance_id`` if it belongs to a registered plugin pool.

    Returns the pool's ``CloseResult`` as a plain dict, or ``None`` when the id
    is not a plugin session so the caller falls through to the browser path.
    ``ProtectedSessionCloseError`` propagates — the route maps it to 409,
    mirroring the browser and terminal paths.
    """
    found = find_plugin_session(instance_id)
    if found is None:
        return None
    kind, _session = found
    pool = state.plugin_registry.pools()[kind]
    return dict(await pool.close(instance_id, force=force))
