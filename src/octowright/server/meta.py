# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Meta tools — octowright-self introspection (takeover detection, dashboard URL).

Distinct from the gameplay tools (`browser`, `macros`, `scenarios`, …): these
tools talk *about* octowright (its UI, its MCP ecosystem) rather than driving
browsers.
"""

from __future__ import annotations

from typing import Any, cast

from provide.telemetry import get_logger

from octowright import advisor as _advisor
from octowright import takeover as _takeover
from octowright.defaults import HEADLESS_DEFAULT, IDLE_GRACE_SECONDS
from octowright.server._state import leader_mode_snapshot, mcp, pool, scenario_pool, upgrade_notice_snapshot
from octowright.server.registry import registered_tool_names

log = get_logger(__name__)

_STATUS_STALE_LIMIT = 20


def _memory_status(sysresources_mod: Any) -> dict[str, Any]:
    """Memory-governor view for status. The available-memory read is only paid
    when a floor is configured (the default OFF case stays a cheap two-None)."""
    floor = sysresources_mod.MIN_FREE_MEMORY_BYTES
    mb = 1024 * 1024
    if floor is None:
        return {"min_free_memory_mb": None, "available_memory_mb": None}
    available = sysresources_mod.available_memory_bytes()
    return {
        "min_free_memory_mb": floor // mb,
        "available_memory_mb": (available // mb) if available is not None else None,
    }


def _compute_health(health_mod: Any, incidents_mod: Any, crash_recovery_mod: Any) -> dict[str, Any]:
    """Roll the stability signals into one verdict and log loudly when degraded,
    so the operator doesn't have to be watching status to notice instability.
    Extracted from octowright_status to keep its complexity under the gate."""
    counts = incidents_mod.counts(category=incidents_mod.CATEGORY_RENDERER_CRASH)
    verdict: dict[str, Any] = health_mod.assess(
        driver_restarts=pool.driver_restart_count(),
        recovery_failures=crash_recovery_mod.recovery_stats()["recovery_failures"],
        recovery_exhausted=counts.get("exhausted", 0),
    )
    if verdict["status"] != "ok":
        log.warning("octowright.health.degraded", status=verdict["status"], reasons=verdict["reasons"])
    return verdict


@mcp.tool(
    structured_output=False,
    description=(
        "Detect competing Playwright MCP plugins registered in the user's MCP "
        "config files (project-level .mcp.json and Claude Code global ~/.claude.json). "
        "Returns the matching servers with scope, file path, server name, command, and "
        "the reason each one matched. WHEN TO INVOKE: call once per session if the "
        "user mentions Playwright tooling, OR when you notice you have access to both "
        "octowright tools (browser_*) AND another playwright-style toolset "
        "(e.g. mcp__playwright_*) — surface the conflict to the user, who can then run "
        "`octowright takeover` in a terminal to reversibly disable the competitors."
    ),
)
def octowright_check_takeover() -> dict[str, Any]:
    """Detect competing Playwright MCP plugins.

    Returns the Detection list structured for the MCP client to surface to the user.
    """
    detections = _takeover.detect_competing_servers()
    if detections:
        next_step = (
            "Run `octowright takeover` in a terminal to interactively disable them, "
            "or pass each detection's server_name to "
            "`octowright takeover --apply --scope=<scope> --name=<name>`."
        )
    else:
        next_step = "No competing playwright MCP plugins detected — octowright is already the one."

    return {
        "found": len(detections),
        "summary": _takeover.summarise(detections),
        "detections": [
            {
                "scope": d.scope,
                "config_path": str(d.config_path),
                "server_name": d.server_name,
                "command": d.command,
                "reason": d.reason,
            }
            for d in detections
        ],
        "next_step": next_step,
    }


@mcp.tool(
    structured_output=False,
    description=(
        "Returns the URL of octowright's web debugger dashboard. The URL is a "
        "localhost web app showing every live browser, every scenario, "
        "recordings/screenshots/videos/traces, plus a per-session debugger with "
        "action timeline + embedded video. Use this whenever the user asks "
        "'show me what's happening' or wants to debug a session — it's far "
        "better than calling browser_recording_path one at a time. "
        "Pass `session_id` to get a deep-link to a specific session's debugger page."
    ),
)
def octowright_dashboard_url(session_id: str | None = None) -> dict[str, Any]:
    """Return the dashboard URL (and optional session deep-link).

    Reports `running: false` with an `error` field when the HTTP sidecar
    failed to bind (e.g., port collision, sidecar not started).
    """
    from octowright import http as _http

    status = _http.runtime_status()
    base_url = _http.runtime_url()
    deep = _http.runtime_session_url(session_id) if session_id else None
    closed_count = 0
    live_count = pool.active_count()
    try:
        from octowright.defaults import RECORDINGS_DIR

        if RECORDINGS_DIR.exists():
            closed_count = sum(1 for _ in RECORDINGS_DIR.glob("*.jsonl"))
    except Exception:
        closed_count = 0

    result: dict[str, Any] = {
        "url": base_url,
        "session_url": deep,
        "live_sessions": live_count,
        "closed_sessions": closed_count,
        "running": status["running"],
        "live_scenarios": len(scenario_pool.list_live()),
    }
    if not status["running"] and status.get("error"):
        result["error"] = status["error"]
    elif not status["running"]:
        result["error"] = "HTTP debugger sidecar not running (call `octowright serve` to start it)"
    return result


@mcp.tool(
    structured_output=False,
    description=(
        "Return Octowright Advisor status: local preferences, recent usage summary, "
        "and current suggestions. Use this when deciding whether to ask the user "
        "about saving repeated workflows as macros or changing OCTOWRIGHT_PROFILE."
    ),
)
def octowright_advisor_status() -> dict[str, Any]:
    """Return the local Octowright Advisor status snapshot."""
    return _advisor.status()


@mcp.tool(
    structured_output=False,
    description=(
        "Set an Octowright Advisor preference. suggestion_type must be "
        "`macro_candidate` or `profile_change`; preference must be `yes`, `no`, "
        "or `automatic`. `automatic` may automate profile-change suggestions, "
        "but macro candidates still require user approval before saving."
    ),
)
def octowright_advisor_set_preference(suggestion_type: str, preference: str) -> dict[str, Any]:
    """Persist an Advisor preference and return updated status."""
    _advisor.set_preference(
        cast(_advisor.SuggestionType, suggestion_type),
        cast(_advisor.Preference, preference),
    )
    return {
        "ok": True,
        "advisor": _advisor.status(),
    }


@mcp.tool(
    structured_output=False,
    description=(
        "Record an Advisor macro observation when a repeated workflow is noticed. "
        "Call this with source=`llm` when you, the agent, notice repeated steps; "
        "server-side detections may use source=`server`. Repeated matching "
        "signatures produce a macro_candidate suggestion, but never auto-save a macro."
    ),
)
def octowright_advisor_record_macro_observation(source: str, signature: str, summary: str) -> dict[str, Any]:
    """Record a repeated-workflow observation and return updated Advisor status."""
    if not signature.strip():
        raise ValueError("signature must not be empty")
    _advisor.record_macro_observation(
        source=source.strip() or "unknown",
        signature=signature.strip(),
        summary=summary.strip(),
    )
    return {
        "ok": True,
        "advisor": _advisor.status(),
    }


@mcp.tool(
    structured_output=False,
    description=(
        "First-touch status snapshot for octowright. WHEN TO INVOKE: call this "
        "ONCE per MCP client session, the first time octowright comes up — before "
        "the first browser_launch — and present a brief banner to the user with "
        "the daemon's identity, current persistence default (persistent vs ephemeral), "
        "live browser/scenario counts, available personas, and the dashboard URL. "
        "Returns daemon PID + uptime, defaults block (ephemeral_default, headed_default, "
        "idle_grace_seconds, badge_position), pool counts (live_browsers, protected_browsers), "
        "persona names, and dashboard URL, plus an optional `upgrade` block on the first "
        "run after a version change (present its highlights to the user as a what's-new note). "
        "Lets the user confirm what mode they're in without surprise."
    ),
)
def octowright_status() -> dict[str, Any]:
    """Return a one-shot session-startup status snapshot."""
    import os
    import time

    from octowright import bridge_state, defaults
    from octowright import http as _http
    from octowright import personas as _personas
    from octowright import session_manifest as _session_manifest
    from octowright import singleton as _singleton
    from octowright import sysresources as _sysresources
    from octowright.browser_pool import crash_recovery as _crash_recovery
    from octowright.browser_pool import crash_reports as _crash_reports
    from octowright.browser_pool import driver_relaunch as _driver_relaunch
    from octowright.browser_pool import health as _health
    from octowright.browser_pool import incidents as _incidents
    from octowright.macros import execution as _macro_execution
    from octowright.server.profiles import PROFILES, active_filter

    lock = _singleton.read_lock()
    daemon_pid: int | None = None
    daemon_uptime: float | None = None
    if lock is not None:
        daemon_pid = lock.pid
        # When the lockfile belongs to THIS process, derive uptime from a
        # monotonic clock so it's immune to wall-clock skew (NTP, DST, manual
        # set). When the lockfile is foreign (a leader running in a different
        # process), monotonic isn't comparable across processes so we fall
        # back to the wall-clock difference, clamped to >= 0.
        local_start = _singleton.local_leader_started_monotonic()
        if lock.pid == os.getpid() and local_start is not None:
            daemon_uptime = max(0.0, time.monotonic() - local_start)
        else:
            daemon_uptime = max(0.0, time.time() - lock.started_at)

    persona_list = _personas.list_personas()
    persona_names = [p["name"] for p in persona_list]

    http_status = _http.runtime_status()
    stale_sessions = _session_manifest.stale_entries(
        live_session_ids={session.instance_id for session in pool.iter_sessions()}
    )
    stale_count = len(stale_sessions)
    stale_preview = stale_sessions[:_STATUS_STALE_LIMIT]

    raw_profile = defaults.active_profile_raw()
    profile_filter = active_filter()
    if profile_filter is None:
        profile_block: dict[str, Any] = {
            "active": raw_profile or None,  # 'all', 'ALL' → echo; unset → None
            "filter_active": False,
            "tool_count": len(registered_tool_names()),
            "available_profiles": sorted(PROFILES.keys()),
        }
    else:
        profile_block = {
            "active": raw_profile,
            "filter_active": True,
            "tool_count": len(registered_tool_names()),
            "available_profiles": sorted(PROFILES.keys()),
        }

    bridge_snapshot = bridge_state.read_state(defaults.BRIDGE_STATE_PATH)
    leader = leader_mode_snapshot()

    health_verdict = _compute_health(_health, _incidents, _crash_recovery)

    return {
        # Rolled-up stability verdict: "ok" | "degraded" | "critical" + reasons.
        # Surface this to the user when it isn't "ok" — it means browsers/driver
        # are unstable right now.
        "health": health_verdict,
        "daemon": {
            "pid": daemon_pid,
            "this_pid": os.getpid(),
            "is_daemon_self": daemon_pid == os.getpid(),
            "uptime_seconds": round(daemon_uptime, 1) if daemon_uptime is not None else None,
            "lockfile": str(_singleton.LOCK_PATH),
            # "daemon" = detached daemon leader (resilient); "inline" = leader runs
            # inside this process (fragile — see inline_reason); "unknown" before
            # the leader is wired. inline_reason: "no_singleton" | "daemon_spawn_failed".
            "mode": leader["mode"],
            "inline_reason": leader["inline_reason"],
        },
        "defaults": {
            # Persistent profiles are the default for named launches; ephemeral
            # is the explicit opt-out via ephemeral=True. Phrased this way so a
            # user who reads the banner can see exactly what mode they're in.
            "ephemeral_default": False,
            "headless_default": HEADLESS_DEFAULT,
            "idle_grace_seconds": IDLE_GRACE_SECONDS,
            "badge_default": True,
            "badge_position_default": "bottom-right",
        },
        "pool": {
            "live_browsers": pool.active_count(),
            "protected_browsers": pool.protected_count(),
            # Pool-wide concurrent-browser cap (shared across all MCP clients).
            # null when disabled (OCTOWRIGHT_MAX_BROWSERS=off). When live_browsers
            # nears browser_cap, user-facing launches will start refusing.
            "browser_cap": defaults.MAX_BROWSERS,
            # Times the shared Playwright driver died and was rebuilt mid-run. A
            # non-zero, climbing value means the driver (and thus every browser at
            # once) is unstable — the deepest mass-failure signal.
            "driver_restarts": pool.driver_restart_count(),
            # Recent driver-restart records (ts, reason, restart_count) for postmortem.
            "driver_restart_recent": _incidents.recent(category=_incidents.CATEGORY_DRIVER_RESTART, limit=5),
            # Sessions lost when the shared driver died (H4a): each {instance_id,
            # kind, url, profile, reason, relaunched_to}. relaunched_to is null
            # unless OCTOWRIGHT_DRIVER_RELAUNCH reopened it. Empty in the common
            # (no driver death) case.
            "driver_relaunch_mode": _driver_relaunch.DRIVER_RELAUNCH_MODE,
            "lost_sessions": _driver_relaunch.recent_lost(limit=10),
            # Memory-pressure governor (OCTOWRIGHT_MIN_FREE_MEMORY_MB). Both null
            # when the guard is off (the default); when set, available_memory_mb
            # nearing min_free_memory_mb means launches will start refusing.
            **_memory_status(_sysresources),
            "live_scenarios": len(scenario_pool.list_live()),
            "stale_manifest_sessions": stale_preview,
            "stale_manifest_count": stale_count,
            "stale_manifest_list_truncated": stale_count > len(stale_preview),
        },
        # Renderer-crash + auto-recovery tallies (process-lifetime). Lets the
        # operator/LLM see that "random crashes" are happening and whether they
        # self-healed, instead of guessing. recovery_failures climbing means the
        # reload isn't sticking (likely the browser process itself died).
        "crash": {
            "recovery_enabled": defaults.CRASH_RECOVERY_ENABLED,
            "recovery_max": defaults.CRASH_RECOVERY_MAX,
            **_crash_recovery.recovery_stats(),
            # Recent per-crash records (instance_id, url, ts, outcome, attempts) so
            # "what happened" is answerable from this call, not a log grep. On
            # macOS each record is enriched at read time with the correlated
            # `.ips` SIGSEGV signature (the OS writes it a beat after the crash,
            # so it can't be attached when the incident is first recorded).
            "recent": _crash_reports.enrich(_incidents.recent(category=_incidents.CATEGORY_RENDERER_CRASH, limit=10)),
        },
        "personas": {
            "count": len(persona_names),
            "names": persona_names,
        },
        "profile": profile_block,
        "advisor": _advisor.status(),
        "bridge": {
            "state_path": str(defaults.BRIDGE_STATE_PATH),
            # summary reflects the FULL state (true follower_count); bounded_view caps
            # the raw followers/events dump so a stale-follower leak can't blow the payload.
            "summary": bridge_state.summarize_state(bridge_snapshot),
            **bridge_state.bounded_view(bridge_snapshot),
        },
        "metrics": {
            # ``macro_labels_seen`` / ``macro_label_overflow_count`` let an
            # operator notice when dynamic macro names have filled the
            # per-macro metric cardinality cap. Recovery (short of a daemon
            # restart) is ``macros.execution.reset_macro_label_seen()`` —
            # in-process only, not exposed as an MCP tool by design.
            "macro_labels_seen": len(_macro_execution._MACRO_LABEL_SEEN),
            "macro_label_overflow_count": _macro_execution._MACRO_LABEL_OVERFLOW_COUNT,
            "macro_label_cap": _macro_execution.METRICS_MACRO_LABEL_CAP,
        },
        "dashboard_url": _http.runtime_url() if http_status["running"] else None,
        # Present only on the first run after an update (version changed since
        # last seen) — {kind, previous_version, current_version, highlights}.
        # Surface these highlights to the user as a "what's new" banner.
        "upgrade": upgrade_notice_snapshot(),
    }
