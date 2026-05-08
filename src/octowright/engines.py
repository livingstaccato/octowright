# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import importlib.metadata
import platform
import re
from dataclasses import dataclass
from typing import Any

from octowright.defaults import SUPPORTED_KINDS
from octowright.types import PlaywrightFailureHint


@dataclass(frozen=True)
class CliResult:
    returncode: int
    stdout: str
    stderr: str
    command: list[str]


async def _run_playwright_cli(*args: str) -> CliResult:
    cmd = ["playwright", *args]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out_b, err_b = await proc.communicate()
    return CliResult(
        returncode=int(proc.returncode if proc.returncode is not None else 1),
        stdout=out_b.decode("utf-8", errors="replace"),
        stderr=err_b.decode("utf-8", errors="replace"),
        command=cmd,
    )


def _parse_list_output(list_output: str) -> dict[str, dict[str, list[str]]]:
    by_version: dict[str, dict[str, list[str]]] = {}
    current: str | None = None
    section: str | None = None
    for raw_line in list_output.splitlines():
        line = raw_line.rstrip()
        if line.startswith("Playwright version:"):
            current = line.split(":", 1)[1].strip()
            by_version[current] = {"browsers": [], "references": []}
            section = None
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped == "Browsers:":
            section = "browsers"
            continue
        if stripped == "References:":
            section = "references"
            continue
        if section and stripped.startswith("/"):
            by_version[current][section].append(stripped)
    return by_version


def _engine_present(kind: str, browser_paths: list[str]) -> bool:
    prefix = f"{kind}-"
    return any(f"/{prefix}" in p for p in browser_paths)


async def engine_status(kinds: list[str] | None = None) -> dict[str, Any]:
    wanted = kinds or list(SUPPORTED_KINDS)
    invalid = [k for k in wanted if k not in SUPPORTED_KINDS]
    if invalid:
        raise ValueError(f"unsupported engine kind(s): {invalid}; expected subset of {SUPPORTED_KINDS}")

    current_version = importlib.metadata.version("playwright")
    listed = await _run_playwright_cli("install", "--list")
    blocks = _parse_list_output(listed.stdout + "\n" + listed.stderr)
    current = blocks.get(current_version)
    browser_paths = current["browsers"] if current else []

    engines: dict[str, Any] = {}
    for kind in wanted:
        engines[kind] = {
            "installed": _engine_present(kind, browser_paths),
            "paths": [p for p in browser_paths if f"/{kind}-" in p],
        }
    missing = [k for k, v in engines.items() if not v["installed"]]
    return {
        "ok": len(missing) == 0,
        "playwright_version": current_version,
        "platform": platform.platform(),
        "engines": engines,
        "missing": missing,
        "install_command": "playwright install " + " ".join(missing or wanted),
        "raw_list_returncode": listed.returncode,
    }


async def engine_install(
    kinds: list[str] | None = None,
    *,
    with_deps: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    wanted = kinds or list(SUPPORTED_KINDS)
    invalid = [k for k in wanted if k not in SUPPORTED_KINDS]
    if invalid:
        raise ValueError(f"unsupported engine kind(s): {invalid}; expected subset of {SUPPORTED_KINDS}")
    args: list[str] = ["install"]
    if with_deps:
        args.append("--with-deps")
    if force:
        args.append("--force")
    args.extend(wanted)
    result = await _run_playwright_cli(*args)
    status = await engine_status(wanted)
    return {
        "ok": result.returncode == 0 and status["ok"],
        "returncode": result.returncode,
        "command": result.command,
        "stdout_tail": "\n".join(result.stdout.splitlines()[-40:]),
        "stderr_tail": "\n".join(result.stderr.splitlines()[-40:]),
        "status": status,
    }


def playwright_failure_sanity(error_text: str, kind: str | None = None) -> PlaywrightFailureHint | None:
    txt = error_text or ""
    target = kind if kind in SUPPORTED_KINDS else "<engine>"
    detectors = (
        _detect_binaries_missing,
        _detect_sandbox_blocked,
        _detect_target_closed,
        _detect_navigation_timeout,
        _detect_os_dependencies_missing,
        _detect_network_unreachable,
        _detect_permission_error,
    )
    for detector in detectors:
        hint = detector(txt, target)
        if hint is not None:
            return hint
    return None


def _detect_binaries_missing(txt: str, target: str) -> PlaywrightFailureHint | None:
    if "Executable doesn't exist" not in txt and "playwright install" not in txt:
        return None
    return {
        "category": "playwright_binaries_missing",
        "probable_cause": "Playwright package updated or binaries missing for this environment",
        "recommended_actions": [
            f"run `playwright install {target}`",
            "run `browser_engine_status` and confirm `installed=true` for required engines",
            "if still failing, run `browser_engine_reinstall` with force=true",
        ],
    }


def _detect_target_closed(txt: str, _target: str) -> PlaywrightFailureHint | None:
    if "Target page, context or browser has been closed" not in txt:
        return None
    return {
        "category": "playwright_target_closed",
        "probable_cause": "Page/context/browser was closed before action completed",
        "recommended_actions": [
            "call `browser_list` to verify a live instance_id",
            "relaunch using `browser_launch` or `browser_handoff`",
        ],
    }


def _detect_navigation_timeout(txt: str, _target: str) -> PlaywrightFailureHint | None:
    if not re.search(r"Navigation timeout .* exceeded", txt):
        return None
    return {
        "category": "playwright_navigation_timeout",
        "probable_cause": "Navigation did not complete before timeout",
        "recommended_actions": [
            "retry with longer timeout env vars (OCTOWRIGHT_NAV_TIMEOUT_MS)",
            "verify target URL is reachable",
        ],
    }


def _detect_os_dependencies_missing(txt: str, target: str) -> PlaywrightFailureHint | None:
    if not re.search(r"Host system is missing dependencies", txt, flags=re.IGNORECASE):
        return None
    return {
        "category": "playwright_os_dependencies_missing",
        "probable_cause": "System libraries required by Playwright browser runtime are not installed",
        "recommended_actions": [
            f"run `playwright install --with-deps {target}`",
            "on CI, ensure browser dependencies are installed before test execution",
        ],
    }


def _detect_sandbox_blocked(txt: str, _target: str) -> PlaywrightFailureHint | None:
    closed = "browserType.launch: Target page, context or browser has been closed" in txt
    if not closed or "sandbox" not in txt.lower():
        return None
    return {
        "category": "playwright_sandbox_blocked",
        "probable_cause": "Chromium sandbox restrictions in containerized/privileged environment",
        "recommended_actions": [
            "run in an environment that supports Chromium sandboxing",
            "if this is CI/container-only, use the project workflow's Playwright install/deps setup",
        ],
    }


def _detect_network_unreachable(txt: str, _target: str) -> PlaywrightFailureHint | None:
    if not re.search(r"(ECONNREFUSED|ERR_CONNECTION_REFUSED|net::ERR_|Name or service not known|ENOTFOUND)", txt):
        return None
    return {
        "category": "playwright_network_unreachable",
        "probable_cause": "Network/DNS/target endpoint is unreachable from current runtime",
        "recommended_actions": [
            "verify URL, DNS, proxy, and firewall settings for this environment",
            "re-run against a known reachable URL to isolate environment vs app issue",
        ],
    }


def _detect_permission_error(txt: str, _target: str) -> PlaywrightFailureHint | None:
    if not re.search(r"(Permission denied|EACCES|EPERM)", txt):
        return None
    return {
        "category": "playwright_permission_error",
        "probable_cause": "Filesystem or runtime permissions prevent browser startup or artifact writes",
        "recommended_actions": [
            "verify writable directories for profiles/recordings/traces/har output",
            "confirm the runtime user has permission to launch browser binaries",
        ],
    }
