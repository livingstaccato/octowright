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


def iter_plugin_sessions() -> Iterator[Any]:
    """Yield every live session across every registered plugin pool."""
    for pool in state.plugin_registry.pools().values():
        yield from pool.iter_sessions()


def find_plugin_session(instance_id: str) -> tuple[str, Any] | None:
    """Resolve ``instance_id`` across registered pools.

    Returns ``(kind, session)`` or ``None``. Instance ids are unique across
    all pools — core enforces that at launch commit — so the first match is
    the only match.
    """
    for kind, pool in state.plugin_registry.pools().items():
        session = pool.maybe_get(instance_id)
        if session is not None:
            return kind, session
    return None


def plugin_session_detail(kind: str, session: Any) -> dict[str, Any]:
    """Build a plugin session's dashboard detail payload.

    A plugin raising here is caught and rendered as a degraded detail rather
    than a 500: an enabled plugin shares the leader's process, but a bad
    detail builder must not take a dashboard page down with it.

    Artifacts are reported by id and mime type only. The absolute path stays
    server-side — the dashboard fetches through the artifact route, which
    re-validates containment on every request.
    """
    from octowright.plugins.artifacts import read_registered_artifacts

    try:
        detail = dict(state.plugin_registry.get_plugin(kind).descriptor.session_detail(session))
    except Exception as exc:  # a bad plugin must not 500 the dashboard
        state.log.warning(
            "octowright.http.plugin_session_detail_failed",
            kind=kind,
            instance_id=getattr(session, "instance_id", None),
            error=repr(exc),
        )
        detail = {"id": getattr(session, "instance_id", None), "kind": kind, "detail_error": repr(exc)}

    artifacts = read_registered_artifacts(Path(session.log_path), Path(state.RECORDINGS_DIR))
    detail["artifacts"] = [
        {"artifact_id": a.artifact_id, "mime_type": a.mime_type, "bytes": a.path.stat().st_size} for a in artifacts
    ]
    return detail
