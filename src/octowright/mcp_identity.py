# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What Octowright tells an MCP client it is, without loading the server.

The leader's ``MCPServer`` (``server/_state.py``) is built from these values,
and a follower answers the ``initialize`` handshake from them while it is still
electing a leader -- so a client connects in milliseconds instead of waiting
out a daemon spawn. This module therefore lives outside ``octowright.server``
and imports only the MCP type models: importing the server would load
Playwright and the whole tool registry into every follower.

``local_initialize_result`` must answer exactly what the leader's low-level
server answers for the same params; ``tests/test_mcp_identity_parity.py``
compares the two across protocol versions, because a client that sees one
server at connect and another after it has no way to tell it happened.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

SERVER_NAME = "octowright"

# MCPServer does not set a version, so the leader advertises an empty one.
SERVER_VERSION = ""


@dataclass(frozen=True)
class ClientReconnect:
    """How ONE MCP client respawns its Octowright follower.

    Single source for the per-client reconnect matrix: rendered into
    ``INSTRUCTIONS`` (so the LLM can hand the user the exact step) and read by
    ``octowright doctor``'s followers check, which matches ``markers`` against
    the process tree of a stale/looping follower to name THAT client's step.
    Before this lived as prose inside the instructions string, the doctor
    surface had to restate it in its own words, and the two drifted.

    ``markers`` are lowercase substrings of a cmdline's executable basename
    (see doctor's matcher for the token rule); they identify the client's own
    process in a follower's ancestry, not the follower's. ``step`` empty =
    no in-session path: the client must be restarted. ``in_instructions=False``
    keeps a detection-only entry out of the server handshake (the VS Code
    extension host is undetectable as Cline-vs-Copilot from a process table).
    """

    name: str
    step: str
    markers: tuple[str, ...] = ()
    in_instructions: bool = True
    # Friendlier short label in doctor output; defaults to ``name``.
    detect_label: str | None = None


RECONNECT_STEPS: tuple[ClientReconnect, ...] = (
    ClientReconnect(
        "Claude Code",
        "/mcp -> select octowright -> Reconnect (if the first try silently fails, choose Reconnect a "
        "second time; the first attempt is a known no-op)",
        ("claude",),
    ),
    ClientReconnect("Cursor", "Settings -> Tools & MCP -> toggle octowright off then back on", ("cursor",)),
    ClientReconnect("Cline (VS Code)", "MCP Servers panel -> octowright -> Restart Server", ("cline",)),
    ClientReconnect("Copilot in VS Code", "Command Palette -> 'MCP: List Servers' -> octowright -> Restart"),
    ClientReconnect("Windsurf", "Cascade plugins (MCP) panel -> Refresh", ("windsurf",)),
    ClientReconnect(
        "Gemini CLI",
        "/mcp disable octowright then /mcp enable octowright (or /mcp refresh)",
        ("gemini",),
    ),
    ClientReconnect("GitHub Copilot CLI", "/mcp reload octowright", ("copilot",)),
    ClientReconnect(
        "Continue or Zed",
        "re-save the MCP config file (it hot-reloads the server)",
        ("continue", "zed"),
    ),
    ClientReconnect(
        "pi",
        "/mcp reconnect octowright (or /mcp reload)",
        ("pi",),
    ),
    ClientReconnect(
        "omp",
        "/mcp reconnect octowright (or /mcp reload)",
        ("omp",),
    ),
    ClientReconnect(
        "Codex CLI, OpenCode, and Amp",
        "",
        ("codex", "opencode", "amp"),
        detect_label="Codex CLI / OpenCode / Amp",
    ),
    # Detection-only: from a process table the Cline and Copilot extension
    # hosts are the same "code" binary, so name both steps together.
    ClientReconnect(
        "VS Code",
        "Cline: MCP Servers panel -> octowright -> Restart Server; Copilot: Command Palette -> "
        "'MCP: List Servers' -> octowright -> Restart",
        ("code",),
        in_instructions=False,
    ),
)


def reconnect_matrix_lines() -> list[str]:
    """The per-client reconnect sentences as they appear in ``INSTRUCTIONS``."""
    lines = [f"{entry.name} — {entry.step}." for entry in RECONNECT_STEPS if entry.in_instructions and entry.step]
    no_session = [entry.name for entry in RECONNECT_STEPS if entry.in_instructions and not entry.step]
    if no_session:
        lines.append(
            f"{', and '.join(no_session)} have NO in-session reconnect: the user must restart the "
            "client, which loses the current conversation."
        )
    lines.append(
        "Universal fallback for any other/unknown client: fully restart the client (recovers the server "
        "but loses the session). If you don't know the client, ask which one they're using."
    )
    return lines


def client_step(entry: ClientReconnect) -> str:
    """The reconnect sentence for one detected client, as doctor reports it."""
    if entry.step:
        return entry.step
    return "no in-session reconnect — the user must restart the client, which loses the current conversation"


INSTRUCTIONS = (
    "Launch and drive multiple headed Playwright browsers in parallel. "
    "Each browser has an instance_id; pass it to every per-browser tool. "
    "Every action is recorded to a JSONL log that can be exported as a Playwright script. "
    "Use the `profile` arg on browser_launch to persist cookies/localStorage/IndexedDB across runs. "
    "The visible tool surface may be slimmed by OCTOWRIGHT_PROFILE / `octowright serve --profile=...`; "
    "if a tool you expect is missing, the operator picked a narrower capability profile — call "
    "`octowright_status` to see the active profile. "
    "For low-token web browsing, prefer compact discovery before heavy snapshots: use `web_site_links`, "
    "`web_page_outline`, or `web_find_links` before launching a browser when public HTML is enough; "
    "after launch use `browser_page_outline` first for headings/landmarks/links/fields, or "
    "`browser_observe` when you need page outline plus compact console/network/download diagnostics, then "
    "`browser_find_link` or `browser_find_field` to choose a target. Use `browser_read_markdown` for "
    "article/docs text, or `browser_read_markdown(response_mode='summary')` for long pages where you "
    "need a capture_id plus outline before reading line ranges. Use `browser_console_summary` and "
    "`browser_network_summary` for diagnostics, and `browser_downloads_summary` for download checks; "
    "prefer structured next_actions and candidate action payloads for follow-up calls over guessing "
    "raw-tool filters or dumping all rows. "
    "`capture_create(response_mode='summary')` for full-fidelity snapshot/text/evaluate/console/network/"
    "recording payloads that need an inline structural outline without dumping the payload. "
    "pass `response_mode='outline'` on launch/navigate/wait/click/fill/key tools when you need the "
    "post-action page outline without an extra call. "
    "reserve `browser_snapshot`, full console dumps, screenshots, or raw recordings for cases where "
    "the compact tools are insufficient. "
    "Octowright PROACTIVELY pushes MCP notifications for exceptional situations (below); stdio clients "
    "receive them through the follower bridge even in the default detached-daemon deployment. Treat them "
    "as best-effort though — also confirm critical state with `octowright_status()` (health, crash.recent, "
    "crash.unresponsive_recent, pool.lost_sessions) after long operations, after any tool error, and "
    "whenever you suspect a crash, an unresponsive target, or "
    "driver loss (a direct HTTP-MCP client that bypasses the follower gets no push). React to a "
    "notification's `hint`: "
    "notifications/octowright/browser_crashed (a page crashed; if recovering=true Octowright is "
    "auto-replacing the page — WAIT for browser_recovered, do NOT relaunch yet; scope=unresponsive means "
    "the target merely stopped answering, not a crash — it is never auto-recovered, so wait/retry or "
    "relaunch as the hint says; scope=process means the whole browser process died, not a closed "
    "window — the session is gone: relaunch, or with auto-reopen on use pool.lost_sessions); "
    "notifications/octowright/browser_recovered (outcome=recovered → the page is usable again, just "
    "continue — unless recovered_elsewhere=true: it is not at its last URL, navigate again; "
    "outcome=failed|exhausted → relaunch with browser_launch); "
    "notifications/octowright/driver_died (the shared driver died and these sessions were lost — if "
    "auto-reopen is on, octowright_status().pool.lost_sessions has the old→new instance_id mapping; "
    "otherwise relaunch the browsers you need); "
    "notifications/octowright/session_closed (a session left the pool). "
    "Refused launches surface in-band as tool errors (browser cap reached / available memory below the "
    "floor) with actionable text — don't retry blindly; close browsers or tell the user. "
    "octowright_status() is the pull snapshot for the same signals: health, crash.recent, "
    "crash.unresponsive_recent, pool.lost_sessions. "
    "If Octowright tools stop responding or return 'Transport closed' and ONE retry still fails: "
    "STOP immediately. Do NOT run shell commands to restart the daemon (octowright restart, "
    "uv run octowright restart, etc.) — the binary is not on your shell PATH in most agent "
    "environments, and a restart cannot reopen a client connection that has already closed (it also "
    "closes every open browser). "
    "Do NOT run 'which octowright', search the filesystem, or probe /api/health — these waste tokens "
    "and cannot reconnect the MCP client. Do NOT write Playwright scripts or open URLs with shell "
    "commands as substitutes — they are not Octowright sessions. "
    "Instead: tell the user Octowright is disconnected and give them the reconnect step for THEIR "
    "MCP client, then wait for them to confirm before resuming. Reconnect by client (these keep the "
    "conversation unless noted): "
    # The per-client sentences come from RECONNECT_STEPS so the matrix a client
    # is told, doctor's remedy text and AGENTS.md's matrix cannot drift apart.
    + " ".join(reconnect_matrix_lines())
    + " Wait for the user to confirm reconnection before resuming."
)


def _mcp_sends_empty_experimental() -> bool:
    """Whether the installed mcp's server advertises ``experimental: {}``
    (up to 2.2) rather than omitting the field (2.3 on)."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        major, minor = (int(part) for part in version("mcp").split(".")[:2])
    except (PackageNotFoundError, ValueError):
        return False
    return (major, minor) < (2, 3)


def server_capabilities() -> Any:
    """The capabilities the leader advertises in its ``initialize`` result.

    MCPServer derives them from the request handlers it registers, which are
    the same whatever the tool profile: tools, prompts and resources, none of
    them announcing list changes. mcp 2.2 sends an empty ``experimental``
    object and 2.3 omits it; the field follows the installed mcp, because
    probing a real server for it would import starlette into the follower's
    early handshake.
    """
    from mcp.types import PromptsCapability, ResourcesCapability, ServerCapabilities, ToolsCapability

    return ServerCapabilities(
        experimental={} if _mcp_sends_empty_experimental() else None,
        prompts=PromptsCapability(list_changed=False),
        resources=ResourcesCapability(subscribe=False, list_changed=False),
        tools=ToolsCapability(list_changed=False),
        extensions={},
    )


def local_initialize_result(params: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The leader's ``initialize`` result for ``params``, as wire JSON.

    ``None`` when the params do not validate: the leader answers those with an
    error, and the follower lets such a request through to it rather than
    composing an error of its own.

    Negotiation is the SDK's: the client's version when the handshake era
    offers it, otherwise the newest handshake version. The result is shaped
    for the wire under the version a fresh connection starts with, as the
    leader's runner does before the handshake commits.
    """
    from mcp.types import Implementation, InitializeRequestParams, InitializeResult
    from mcp_types import methods
    from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS, LATEST_HANDSHAKE_VERSION
    from pydantic import ValidationError

    try:
        init = InitializeRequestParams.model_validate(params or {}, by_name=False)
    except ValidationError:
        return None
    requested = init.protocol_version
    negotiated = requested if requested in HANDSHAKE_PROTOCOL_VERSIONS else LATEST_HANDSHAKE_VERSION
    result = InitializeResult(
        protocol_version=negotiated,
        capabilities=server_capabilities(),
        server_info=Implementation(name=SERVER_NAME, version=SERVER_VERSION),
        instructions=INSTRUCTIONS,
    )
    dumped = result.model_dump(by_alias=True, mode="json", exclude_none=True)
    return methods.serialize_server_result("initialize", LATEST_HANDSHAKE_VERSION, dumped)


# The ``initialize`` result fields a client acts on for the whole session.
HANDSHAKE_FIELDS = ("capabilities", "instructions", "protocolVersion", "serverInfo")


def handshake_differences(told: Mapping[str, Any], leader: Mapping[str, Any]) -> list[str]:
    """The ``initialize`` result fields where what a client was told differs
    from what the leader answers, sorted; empty when they agree.

    A follower answers ``initialize`` from its own copy of this module while
    it elects a leader, so a follower older than the leader tells its client
    its own instructions and capabilities, and a client that kept one
    handshake across a leader restart holds the previous leader's. Neither can
    be corrected inside the session -- MCP has no way to re-send a handshake --
    so the difference is reported instead (the bridge snapshot's
    ``handshake_mismatch``).
    """
    return sorted(name for name in HANDSHAKE_FIELDS if told.get(name) != leader.get(name))
