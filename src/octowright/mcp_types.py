# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""TypedDict shapes for MCP-tool wire-facing returns.

Kept separate from ``octowright.types`` (which holds long-lived application
types like LaunchOptions and MacroAction) so this file can grow as new tools
are added without pushing the other one over the 500-LOC ceiling.
"""

from __future__ import annotations

from typing import Any, TypedDict

# ─── server/macros.py MCP returns ────────────────────────────────────────────


class MacroSaveResult(TypedDict):
    saved: bool
    name: str
    path: str


class MacroDeleteResult(TypedDict):
    deleted: bool
    name: str
    path: str


class MacroLintIssue(TypedDict):
    severity: str
    code: str
    message: str
    action_index: int | None


class MacroLintResult(TypedDict):
    macro: str
    issues: list[MacroLintIssue]
    summary: str
    ok: bool


class MacroCompileResult(TypedDict, total=False):
    compiled: dict[str, Any]
    written: bool
    path: str


class MacroListEntry(TypedDict):
    name: str
    description: str | None
    parameters: list[str]
    path: str
    created_at: str | None
    updated_at: str | None
    action_count: int


class MacroRunResult(TypedDict):
    macro: str
    executed: int
    skipped: int
    args_used: dict[str, Any]
    slowmo_ms: int
    elapsed_s: float


class MacroSequenceStep(TypedDict, total=False):
    macro: str
    executed: int
    skipped: int
    args_used: dict[str, Any]
    slowmo_ms: int
    elapsed_s: float
    ok: bool
    error: str  # only set on failed steps


class MacroSequenceResult(TypedDict):
    sequence: list[str]
    steps: list[MacroSequenceStep]
    ok: bool


class MacroRepairSuggestion(TypedDict):
    macro: str
    action_index: int
    original_action: dict[str, Any]
    source: str
    replacement_action: dict[str, Any] | None
    action_preview: str | None
    prompt: str


class MacroRepairPreviewResult(TypedDict):
    macro: str
    suggestions: list[MacroRepairSuggestion]


class TestSuiteCaseResult(TypedDict, total=False):
    name: str
    ok: bool
    error: str | None
    duration: float
    # Present only when the test passed but teardown (browser close) failed.
    # The test is still reported as ok=True; this carries the close-error repr
    # so callers can surface it as a soft warning.
    teardown_warning: str


class TestSuiteResult(TypedDict):
    total: int
    passed: int
    failed: int
    report_path: str
    results: list[TestSuiteCaseResult]


# ─── server/scenarios.py MCP returns ─────────────────────────────────────────


class ScenarioParticipant(TypedDict, total=False):
    """Per-participant snapshot. Total=False — not every code path populates
    every field (e.g. instance_id is only present after launch)."""

    persona: str
    kind: str
    role: str
    launch_kwargs: dict[str, Any]
    startup_macros: list[str]
    instance_id: str


class ScenarioPlanResult(TypedDict):
    name: str
    description: str | None
    summary: str
    participants: list[ScenarioParticipant]
    fixtures: dict[str, Any]
    teardown_macro: str | None
    verify: dict[str, str]
    would_launch: int


class ScenarioStartResult(TypedDict):
    scenario_id: str
    name: str
    participants: list[ScenarioParticipant]


class ScenarioStatusEntry(TypedDict):
    scenario_id: str
    name: str
    participants: list[ScenarioParticipant]


class ScenarioStatusResult(TypedDict):
    summary: str
    count: int
    scenarios: list[ScenarioStatusEntry]


class ScenarioStopResult(TypedDict):
    scenario_id: str
    teardown_errors: list[dict[str, str]]
    closed: list[str]


class ScenarioParticipantsResult(TypedDict):
    summary: str
    count: int
    participants: list[ScenarioParticipant]


class ScenarioRemapEntry(TypedDict):
    scenario_id: str
    role: str | None
    persona: str | None
    old_instance_id: str
    new_instance_id: str


class ScenarioRemapResult(TypedDict):
    scenario_id: str
    applied: list[ScenarioRemapEntry]
    count: int


class ScenarioParticipantOutcome(TypedDict, total=False):
    instance_id: str
    ok: bool
    error: str


class ScenarioRunMacroResult(TypedDict):
    scenario_id: str
    macro: str
    role: str | None
    targeted: int
    results: list[ScenarioParticipantOutcome]


class ScenarioWaitForSyncResult(TypedDict):
    scenario_id: str
    role: str | None
    selector: str | None
    text: str | None
    url: str | None
    targeted: int
    results: list[ScenarioParticipantOutcome]


class ScenarioTailEntry(TypedDict, total=False):
    instance_id: str
    persona: str
    role: str
    ts: str
    action: str


class ScenarioTailResult(TypedDict):
    scenario_id: str
    events: list[ScenarioTailEntry]
    cursors: dict[str, int]


class ScenarioRunAsTestResult(TypedDict):
    scenario_id: str
    name: str
    total: int
    passed: int
    failed: int
    report_path: str
    results: list[TestSuiteCaseResult]


# ─── server/browser/inspect.py MCP returns ───────────────────────────────────


class BrowserScreenshotResult(TypedDict):
    path: str


class BrowserSnapshotResult(TypedDict, total=False):
    url: str
    title: str
    aria: str
    truncated: bool
    aria_size: int
    cap: int


class BrowserEvaluateResult(TypedDict, total=False):
    result: Any
    truncated: bool
    result_size: int
    cap: int


class ConsoleMessage(TypedDict, total=False):
    """One entry in `BrowserSession.console`. `page_index` is only set when
    the message originated from a popup, not the primary page."""

    level: str
    text: str
    page_index: int


class BrowserConsoleMessagesResult(TypedDict):
    messages: list[ConsoleMessage]
    next_cursor: int
    total: int


class BrowserOkResult(TypedDict):
    """Generic ``{ok: True}`` shape used by wait_for / mock / unmock."""

    ok: bool


class BrowserPathResult(TypedDict):
    """Generic ``{path: str}`` shape used by recording_path / export_script."""

    path: str


class BrowserCaptureAndCloseResult(TypedDict, total=False):
    title: str
    url: str
    screenshot_path: str
    closed: bool
    aria: str


class BrowserExpectUrlResult(TypedDict):
    ok: bool
    url: str


class BrowserExpectTextResult(TypedDict):
    ok: bool
    text: str


class BrowserExpectSelectorResult(TypedDict):
    ok: bool
    selector: str
    present: bool


class BrowserExpectJsResult(TypedDict):
    ok: bool
    result: Any


class BrowserTailRecordingResult(TypedDict):
    events: list[dict[str, Any]]
    cursor: int
    total_bytes: int
    complete: bool


class BrowserReadMarkdownResult(TypedDict, total=False):
    url: str
    markdown: str
    truncated: bool
    markdown_size: int


class BrowserBriefResult(TypedDict):
    url: str
    title: str
    elements: str


# ─── cleanup tools (server/macros.py) ────────────────────────────────────────


class ProfileCleanupDetail(TypedDict):
    persona: str
    engine: str
    path: str
    size_bytes: int
    age_days: float


class CleanupResult(TypedDict, total=False):
    """Shared shape for recordings_cleanup + profile_cleanup MCP returns."""

    days: float
    dry_run: bool
    found: int
    removed: int
    would_remove: int
    freed_bytes: int
    errors: list[dict[str, str]]
    # recordings-specific
    recordings_dir: str
    by_kind: dict[str, int]
    # profiles-specific
    profiles_dir: str
    skipped_in_use: int
    details: list[ProfileCleanupDetail]
