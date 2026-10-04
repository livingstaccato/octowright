# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import platform
import re

from octowright.defaults import SUPPORTED_KINDS
from octowright.types import PlaywrightFailureHint


def playwright_failure_sanity(error_text: str, kind: str | None = None) -> PlaywrightFailureHint | None:
    txt = error_text or ""
    target = kind if kind in SUPPORTED_KINDS else "<engine>"
    detectors = (
        _detect_binaries_missing,
        # First past the generic detectors on purpose. The real Server Core
        # text is `browserType.launch: Target page, context or browser has
        # been closed` followed by the WSALookupServiceBegin line in the
        # browser log, so _detect_target_closed (and _detect_sandbox_blocked,
        # when the log mentions sandboxing) would claim it and send the
        # reader off relaunching a browser that cannot start on this image.
        # The match is specific enough that ordering it first costs nothing.
        _detect_windows_media_stack_missing,
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
            "run `octowright doctor` to confirm the engine now launches",
            f"if still failing, run `playwright install --force {target}`",
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
            "relaunch using `browser_launch` (with the same `profile` to keep its state)",
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


def _running_on_windows() -> bool:
    """Whether this process launches browsers on Windows.

    Split out so it can be faked in tests; the browser that produced the error
    text ran in THIS process (see ``browser_pool.errors``), so the host
    platform is the right thing to gate on.
    """
    return platform.system() == "Windows"


def _detect_windows_media_stack_missing(txt: str, _target: str) -> PlaywrightFailureHint | None:
    """Windows images (notably Server Core) missing components Chromium needs.

    ``WSALookupServiceBegin failed with: 10091`` (WSASYSNOTREADY) and a failure
    to load ``mf.dll``/``mfplat.dll`` both mean the same thing in practice: the
    base image omits OS components Chromium initializes at startup. Raw, this
    reads as a transient network fault and sends the reader off chasing DNS and
    proxies; named, it points at the image.

    Gated on the host platform because this is ordered AHEAD of the generic
    detectors: an error text that merely happens to carry one of these tokens
    on Linux or macOS would otherwise be answered with "install the
    Server-Media-Foundation feature" and would suppress the correct
    sandbox/target-closed diagnosis -- the misdiagnosis this detector exists
    to prevent, pointed the other way.
    """
    if not _running_on_windows():
        return None
    if not re.search(r"(WSALookupServiceBegin|\bmfplat\.dll\b|\bmf\.dll\b)", txt, flags=re.IGNORECASE):
        return None
    return {
        "category": "windows_media_stack_missing",
        "probable_cause": (
            "This Windows image cannot initialize Chromium's network/media stack "
            "(missing OS components -- typical of Server Core and other minimal images)"
        ),
        "recommended_actions": [
            "use a Windows image with the desktop/media components (e.g. windows-2022 "
            "hosted runner, or a Server image with the Media Foundation feature installed)",
            "on Server Core, install the Server-Media-Foundation feature",
            "if only a Linux/macOS engine is needed, run this leg on that platform instead",
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
