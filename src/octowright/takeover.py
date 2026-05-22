# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Takeover: detect competing Playwright MCP plugins in MCP config files.

Two scopes are supported:

* ``project`` — ``<cwd>/.mcp.json`` (one repo).
* ``global``  — ``~/.claude.json`` (everywhere).

A third logical scope, ``session``, exists at the CLI level only — it changes
nothing on disk; it just acknowledges that the user wants to try octowright
once without rewriting their config.

The "disable" mechanism is a deliberately reversible key rename:

    "playwright" -> "_playwright_disabled_by_octowright"

MCP clients that read the ``mcpServers`` map skip unknown server names, so the
entry stays in the file (visible, intact) but is no longer registered. The user
can rename the key back at any time to re-enable. This avoids destroying the
original entry (safer than deletion) and avoids having to invent a new
"enabled: false" schema.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Anything case-insensitive-matching one of these in the server *name* counts.
# Word boundaries (\b) guard against false positives on unrelated server names.
# The negative lookahead on `chromium` excludes `chromium-extension*` style
# names (helpers that ship Chrome extensions, not headed-browser drivers) —
# `chromium-mcp` / `chromium-driver` still match.
COMPETING_NAME_PATTERNS: list[str] = [
    r"\bplaywright\b",
    r"\bchromium\b(?!-extension)",
    r"\bbrowser-use\b",
]

# Anything case-insensitive-matching one of these in the joined command/args
# string counts. Catches different package names + plugin-namespaced installs.
COMPETING_COMMAND_PATTERNS: list[str] = [
    r"@playwright/mcp",
    r"mcp-playwright",
    r"playwright/mcp",
    r"plugin\.playwright",
]

DISABLED_PREFIX = "_"
DISABLED_SUFFIX = "_disabled_by_octowright"


@dataclass
class Detection:
    scope: str  # "project" | "global"
    config_path: Path
    server_name: str  # the key in mcpServers (e.g. "playwright")
    command: str  # the registered command string (best-effort; for display)
    reason: str  # why this matched


def _default_project_config() -> Path:
    return Path.cwd() / ".mcp.json"


def _default_global_config() -> Path:
    return Path.home() / ".claude.json"


def _load_json(path: Path) -> dict[str, Any] | None:
    """Return parsed JSON dict or None if the file is missing/unreadable/invalid."""
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _extract_mcp_servers(data: dict[str, Any]) -> dict[str, Any]:
    """Return the ``mcpServers`` mapping if present, else an empty dict.

    Both .mcp.json (project) and ~/.claude.json (global) have a top-level
    ``mcpServers`` key. ~/.claude.json may *also* have per-project nested
    overrides under ``projects[<path>].mcpServers``; those are surfaced as
    additional detections too.
    """
    raw = data.get("mcpServers")
    if isinstance(raw, dict):
        return raw
    return {}


def _extract_project_overrides(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return ``{project_path: mcpServers_dict}`` for any per-project overrides
    nested under the global ``~/.claude.json`` ``projects`` key."""
    out: dict[str, dict[str, Any]] = {}
    projects = data.get("projects")
    if not isinstance(projects, dict):
        return out
    for proj_path, proj_cfg in projects.items():
        if not isinstance(proj_cfg, dict):
            continue
        servers = proj_cfg.get("mcpServers")
        if isinstance(servers, dict) and servers:
            out[str(proj_path)] = servers
    return out


def _command_string(entry: Any) -> str:
    """Best-effort flatten of an mcpServers entry to a 'command + args' string."""
    if not isinstance(entry, dict):
        return ""
    parts: list[str] = []
    cmd = entry.get("command")
    if isinstance(cmd, str):
        parts.append(cmd)
    args = entry.get("args")
    if isinstance(args, list):
        for a in args:
            if isinstance(a, str):
                parts.append(a)
    # Some configs use "url" for HTTP-style MCP servers.
    url = entry.get("url")
    if isinstance(url, str):
        parts.append(url)
    return " ".join(parts)


def _match_reason(server_name: str, command: str) -> str | None:
    """Return a short human reason if this server looks like a competing
    Playwright MCP plugin, else None."""
    name_lc = server_name.lower()
    for pat in COMPETING_NAME_PATTERNS:
        if re.search(pat, name_lc, flags=re.IGNORECASE):
            return f"name matches /{pat}/"
    cmd_lc = command.lower()
    for pat in COMPETING_COMMAND_PATTERNS:
        if re.search(pat, cmd_lc, flags=re.IGNORECASE):
            return f"command matches /{pat}/"
    return None


def _is_octowright(server_name: str) -> bool:
    return server_name.lower() == "octowright"


def _is_already_disabled(server_name: str) -> bool:
    return server_name.endswith(DISABLED_SUFFIX)


def _scan_servers(
    servers: dict[str, Any],
    *,
    scope: str,
    config_path: Path,
) -> list[Detection]:
    out: list[Detection] = []
    for name, entry in servers.items():
        if not isinstance(name, str):
            continue
        if _is_octowright(name) or _is_already_disabled(name):
            continue
        cmd = _command_string(entry)
        reason = _match_reason(name, cmd)
        if reason is None:
            continue
        out.append(
            Detection(
                scope=scope,
                config_path=config_path,
                server_name=name,
                command=cmd,
                reason=reason,
            )
        )
    return out


def detect_competing_servers(
    project_config: Path | None = None,
    global_config: Path | None = None,
) -> list[Detection]:
    """Walk the two config files (if they exist), inspect each entry under
    ``mcpServers``, return Detections for any whose name or command matches a
    competing pattern. Always EXCLUDES octowright itself."""
    project = project_config if project_config is not None else _default_project_config()
    global_ = global_config if global_config is not None else _default_global_config()

    detections: list[Detection] = []

    project_data = _load_json(project)
    if project_data is not None:
        detections.extend(_scan_servers(_extract_mcp_servers(project_data), scope="project", config_path=project))

    global_data = _load_json(global_)
    if global_data is not None:
        detections.extend(_scan_servers(_extract_mcp_servers(global_data), scope="global", config_path=global_))
        # Per-project nested overrides inside ~/.claude.json: report under the
        # global config_path (because that's where the rewrite would land) but
        # tag scope as "global" — they live in the global file. We carry the
        # nested project key in the reason string so the user can find it.
        for proj_path, servers in _extract_project_overrides(global_data).items():
            for d in _scan_servers(servers, scope="global", config_path=global_):
                detections.append(
                    Detection(
                        scope=d.scope,
                        config_path=d.config_path,
                        server_name=d.server_name,
                        command=d.command,
                        reason=f"{d.reason} (under projects[{proj_path}])",
                    )
                )

    return detections


def summarise(detections: list[Detection]) -> str:
    """Human-readable one-liner. Examples::

    "0 competing plugins"
    "1 competing plugin in project (.mcp.json: playwright)"
    "2 competing plugins: project (.mcp.json: playwright); global (.claude.json: chromium)"
    """
    if not detections:
        return "0 competing plugins"

    by_scope: dict[str, list[Detection]] = {}
    for d in detections:
        by_scope.setdefault(d.scope, []).append(d)

    chunks: list[str] = []
    for scope in ("project", "global"):
        items = by_scope.get(scope, [])
        if not items:
            continue
        fname = items[0].config_path.name
        names = ", ".join(d.server_name for d in items)
        chunks.append(f"{scope} ({fname}: {names})")

    n = len(detections)
    word = "plugin" if n == 1 else "plugins"
    if len(chunks) == 1 and n == 1:
        return f"1 competing {word} in {chunks[0]}"
    return f"{n} competing {word}: " + "; ".join(chunks)


def disabled_key_for(server_name: str) -> str:
    return f"{DISABLED_PREFIX}{server_name}{DISABLED_SUFFIX}"


def apply_takeover(
    detection: Detection,
    *,
    backup: bool = True,
) -> dict[str, Any]:
    """Modify the config file to disable the competing server.

    Strategy: rename the key from ``<name>`` to
    ``_<name>_disabled_by_octowright``. MCP clients skip unknown server names,
    so the entry effectively stops registering — but it stays in the
    file (intact, visible) so the user can rename it back to re-enable.

    Always writes a backup at ``<config>.bak.<timestamp>`` first when
    ``backup=True``.

    Returns ``{disabled, backup_path, config_path, new_key_name}``.
    """
    config_path = detection.config_path
    if not config_path.exists():
        return {
            "disabled": False,
            "backup_path": None,
            "config_path": str(config_path),
            "new_key_name": None,
            "error": f"config does not exist: {config_path}",
        }

    original_text = config_path.read_text(encoding="utf-8")
    try:
        data = json.loads(original_text)
    except (json.JSONDecodeError, ValueError) as e:
        return {
            "disabled": False,
            "backup_path": None,
            "config_path": str(config_path),
            "new_key_name": None,
            "error": f"could not parse JSON: {e}",
        }

    if not isinstance(data, dict):
        return {
            "disabled": False,
            "backup_path": None,
            "config_path": str(config_path),
            "new_key_name": None,
            "error": "config root is not a JSON object",
        }

    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or detection.server_name not in servers:
        # The detection might have come from a per-project override under
        # `projects[...].mcpServers` rather than the top-level mcpServers.
        # Try those too.
        rewrote_under: tuple[str, dict[str, Any]] | None = None
        projects = data.get("projects")
        if isinstance(projects, dict):
            for proj_path, proj_cfg in projects.items():
                if not isinstance(proj_cfg, dict):
                    continue
                pservers = proj_cfg.get("mcpServers")
                if isinstance(pservers, dict) and detection.server_name in pservers:
                    rewrote_under = (str(proj_path), pservers)
                    break
        if rewrote_under is None:
            return {
                "disabled": False,
                "backup_path": None,
                "config_path": str(config_path),
                "new_key_name": None,
                "error": f"server {detection.server_name!r} not found in {config_path}",
            }
        _, servers = rewrote_under

    new_key = disabled_key_for(detection.server_name)
    if new_key in servers:
        # Don't clobber an existing disabled twin; bail loudly.
        return {
            "disabled": False,
            "backup_path": None,
            "config_path": str(config_path),
            "new_key_name": new_key,
            "error": f"target key {new_key!r} already exists; refusing to overwrite",
        }

    backup_path: Path | None = None
    if backup:
        ts = time.strftime("%Y%m%d-%H%M%S")
        backup_path = config_path.with_suffix(config_path.suffix + f".bak.{ts}")
        backup_path.write_text(original_text, encoding="utf-8")

    # Preserve insertion order: rebuild the dict so the renamed entry sits
    # where the old one did, rather than getting appended at the end.
    new_servers: dict[str, Any] = {}
    for k, v in servers.items():
        if k == detection.server_name:
            new_servers[new_key] = v
        else:
            new_servers[k] = v
    servers.clear()
    servers.update(new_servers)

    config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    return {
        "disabled": True,
        "backup_path": str(backup_path) if backup_path else None,
        "config_path": str(config_path),
        "new_key_name": new_key,
    }
