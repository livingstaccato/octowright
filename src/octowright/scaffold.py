# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""First-run scaffolding: dirs + sample persona/scenario/macro + MCP registration block.

Pure logic — no click, no telemetry. The CLI command in `octowright init` is a
thin wrapper around `scaffold_all`.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

SAMPLE_PERSONA_NAME = "default"
SAMPLE_SCENARIO_NAME = "sample-solo"
SAMPLE_MACRO_NAME = "sample-page-ready"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _ensure_dir(path: Path) -> bool:
    """mkdir -p; return True if newly created, False if it already existed."""
    if path.exists():
        return False
    path.mkdir(parents=True, exist_ok=True)
    return True


def write_sample_persona(profiles_dir: Path, *, force: bool = False) -> tuple[Path, str]:
    """Write a stub persona at <profiles_dir>/sample/profile.yaml.
    Returns (path, status) where status is 'created' | 'exists' | 'overwritten'."""
    pdir = profiles_dir / SAMPLE_PERSONA_NAME
    pdir.mkdir(parents=True, exist_ok=True)
    target = pdir / "profile.yaml"
    if target.exists() and not force:
        return target, "exists"
    doc: dict[str, Any] = {
        "name": SAMPLE_PERSONA_NAME,
        "display_name": "Default Persona",
        "default_url": "https://octowright.com/",
        "default_macros": [],
        "credentials": {},
        "app": {"hosts": ["octowright.com"]},
    }
    target.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return target, "overwritten" if force else "created"


def write_sample_scenario(scenarios_dir: Path, *, force: bool = False) -> tuple[Path, str]:
    target = scenarios_dir / f"{SAMPLE_SCENARIO_NAME}.yaml"
    if target.exists() and not force:
        return target, "exists"
    doc: dict[str, Any] = {
        "name": SAMPLE_SCENARIO_NAME,
        "description": "One webkit browser visiting octowright.com — the smallest possible scenario.",
        "participants": [
            {
                "persona": SAMPLE_PERSONA_NAME,
                "kind": "webkit",
                "role": "visitor",
            }
        ],
    }
    target.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return target, "overwritten" if force else "created"


def write_sample_macro(macros_dir: Path, *, force: bool = False) -> tuple[Path, str]:
    """A trivial macro that asserts the page rendered. Doesn't depend on a recording."""
    target = macros_dir / f"{SAMPLE_MACRO_NAME}.json"
    if target.exists() and not force:
        return target, "exists"
    doc: dict[str, Any] = {
        "name": SAMPLE_MACRO_NAME,
        "description": "[test:smoke] Sample macro — assert window/document/body exist on the active page.",
        "parameters": [],
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "actions": [
            {"action": "expect_js", "expression": "typeof window === 'object' && typeof document === 'object'"},
            {"action": "expect_js", "expression": "typeof document.body === 'object'"},
        ],
    }
    target.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return target, "overwritten" if force else "created"


def write_project_config(target_dir: Path, *, force: bool = False) -> tuple[Path, str]:
    """Write a starter .octowright/config.yaml in target_dir.

    Returns (path, status) where status is 'created' | 'exists' | 'overwritten'.
    """
    cfg_dir = target_dir / ".octowright"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    target = cfg_dir / "config.yaml"
    if target.exists() and not force:
        return target, "exists"
    import getpass
    import subprocess

    try:
        r = subprocess.run(  # nosec B603 B607 - fixed git argv, no user input
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=2
        )
        repo_name = Path(r.stdout.strip()).name if r.returncode == 0 else ""
    except Exception:
        repo_name = ""
    try:
        username = getpass.getuser()
    except Exception:
        username = "user"
    label = repo_name or username
    doc = f"""\
# octowright project config
# Picked up automatically by browser_launch when this file is present.
# Override any field or delete to fall back to octowright's auto-detection.
#
# Note: the daemon caches this file at startup.
# Changes take effect after `octowright restart`.

# Human-readable label for browsers launched from this project.
label: {label}

# Persona to adopt (must match a profile.yaml in your profiles dir).
# persona: {label}

# Override the persistent profile name (defaults to label).
# profile: {label}
"""
    target.write_text(doc, encoding="utf-8")
    return target, "overwritten" if force else "created"


def mcp_registration_block(install_dir: Path | None = None) -> str:
    """Render the JSON snippet a user pastes into .mcp.json or ~/.claude.json.

    `install_dir` defaults to the repo containing this module (so the snippet
    works for the developer running `octowright init` from a checkout).
    """
    if install_dir is None:
        install_dir = Path(__file__).resolve().parents[2]
    block = {
        "mcpServers": {
            "octowright": {
                "command": "uv",
                "args": [
                    "--directory",
                    str(install_dir),
                    "run",
                    "octowright",
                    "serve",
                ],
            }
        }
    }
    return json.dumps(block, indent=2)


def scaffold_all(
    profiles_dir: Path,
    macros_dir: Path,
    scenarios_dir: Path,
    *,
    target_dir: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Run every scaffolding step and return a structured report.

    Report shape:
        {
          "dirs": {profiles: {path, created}, macros: ..., scenarios: ...},
          "files": {persona: {path, status}, scenario: ..., macro: ..., project_config: ...},
          "mcp_block": "<json snippet>",
        }
    """
    dirs = {
        "profiles": {"path": str(profiles_dir), "created": _ensure_dir(profiles_dir)},
        "macros": {"path": str(macros_dir), "created": _ensure_dir(macros_dir)},
        "scenarios": {"path": str(scenarios_dir), "created": _ensure_dir(scenarios_dir)},
    }
    persona_path, persona_status = write_sample_persona(profiles_dir, force=force)
    scenario_path, scenario_status = write_sample_scenario(scenarios_dir, force=force)
    macro_path, macro_status = write_sample_macro(macros_dir, force=force)
    cfg_dir = target_dir if target_dir is not None else Path.cwd()
    cfg_path, cfg_status = write_project_config(cfg_dir, force=force)
    return {
        "dirs": dirs,
        "files": {
            "persona": {"path": str(persona_path), "status": persona_status},
            "scenario": {"path": str(scenario_path), "status": scenario_status},
            "macro": {"path": str(macro_path), "status": macro_status},
            "project_config": {"path": str(cfg_path), "status": cfg_status},
        },
        "mcp_block": mcp_registration_block(),
    }


def render_report(report: dict[str, Any], stream: Any = None) -> None:
    """Pretty-print a scaffold_all report. Defaults to sys.stdout."""
    out = stream or sys.stdout
    print("octowright init — scaffolding complete\n", file=out)
    print("directories:", file=out)
    for name, info in report["dirs"].items():
        marker = "+" if info["created"] else "·"
        print(f"  {marker} {name:10s} {info['path']}", file=out)
    print("\nfiles:", file=out)
    for name, info in report["files"].items():
        marker = {"created": "+", "overwritten": "*", "exists": "·"}[info["status"]]
        print(f"  {marker} {name:10s} {info['path']}  ({info['status']})", file=out)
    print(
        "\nNext step — register octowright with your MCP client by adding this "
        "to the `mcpServers` block of `.mcp.json` (project-scoped) or, for "
        "Claude Code, `~/.claude.json` (globally):\n",
        file=out,
    )
    print(report["mcp_block"], file=out)
    print(
        "\nThen reload your MCP client. Verify with `octowright selftest` "
        "(lists every registered MCP tool) or by asking the client "
        "'what octowright tools do you have?'.",
        file=out,
    )
