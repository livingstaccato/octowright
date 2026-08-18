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


class MacroRepairApplyResult(TypedDict):
    macro: str
    action_index: int
    applied: bool
    original_action: dict[str, Any]
    replacement_action: dict[str, Any]
    path: str


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
    connector_type: str  # terminal participants only (pty/ssh); absent for browsers
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
    # The participant's scenario role. NOT `role`: that is the ARIA locator key
    # on click/fill/click_by/fill_by, and stamping it here overwrote the
    # recorded value (see macros/substitution.RECORDING_NOISE_KEYS).
    scenario_role: str
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


class BrowserToolAction(TypedDict, total=False):
    tool: str
    args: dict[str, Any]
    available: bool
    requires_profile: str
    available_profiles: list[str]


class BrowserSnapshotResult(TypedDict, total=False):
    url: str
    title: str
    aria: str
    truncated: bool
    aria_size: int
    cap: int
    # Set when the aria snapshot exceeded SNAPSHOT_TIMEOUT_SECONDS on a heavy DOM;
    # the result then carries compact follow-up actions instead of hanging.
    snapshot_timed_out: bool
    timeout_s: float
    hint: str
    actions: list[BrowserToolAction]


class BrowserEvaluateResult(TypedDict, total=False):
    result: Any
    truncated: bool
    result_size: int
    cap: int
    next_actions: list[BrowserToolAction]


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


class BrowserOkResult(TypedDict, total=False):
    """Generic ``{ok: True}`` shape used by wait_for / mock / unmock."""

    ok: bool
    outline: BrowserPageOutlineResult


class BrowserPathResult(TypedDict):
    """Generic ``{path: str}`` shape used by recording_path / export_script."""

    path: str


class BrowserCaptureAndCloseResult(TypedDict, total=False):
    error: str
    title: str
    url: str
    screenshot_path: str
    closed: bool
    aria: str
    snapshot_timed_out: bool
    timeout_s: float
    hint: str
    actions: list[BrowserToolAction]


class BrowserExpectUrlResult(TypedDict):
    ok: bool
    url: str


class BrowserExpectTextResult(TypedDict, total=False):
    ok: bool
    text: str
    truncated: bool
    text_size: int
    cap: int
    next_actions: list[BrowserToolAction]


class BrowserExpectSelectorResult(TypedDict):
    ok: bool
    selector: str
    present: bool


class BrowserExpectJsResult(TypedDict, total=False):
    ok: bool
    result: Any
    truncated: bool
    result_size: int
    cap: int
    next_actions: list[BrowserToolAction]


class BrowserTailRecordingResult(TypedDict, total=False):
    events: list[dict[str, Any]]
    summary: dict[str, Any]
    cursor: int
    total_bytes: int
    complete: bool
    event_count: int
    returned_event_count: int
    truncated: bool
    next_actions: list[BrowserToolAction]


class BrowserReadMarkdownResult(TypedDict, total=False):
    url: str
    title: str | None
    markdown: str
    truncated: bool
    markdown_size: int
    capture_id: str
    kind: str
    size_chars: int
    summary: dict[str, Any]
    actions: list[str]
    next_actions: list[BrowserToolAction]


class BrowserBriefResult(TypedDict):
    url: str
    title: str
    elements: str


class BrowserActionSuggestion(TypedDict, total=False):
    tool: str
    args: dict[str, Any]
    fallback_args: dict[str, Any]
    requires_args: list[str]


class BrowserLinkCandidate(TypedDict, total=False):
    text: str
    href: str | None
    role: str | None
    label: str | None
    title: str | None
    selector: str | None
    visible: bool
    action: BrowserActionSuggestion
    rank: int
    score: float
    reason: str


class BrowserLinksResult(TypedDict):
    url: str
    title: str
    links: list[BrowserLinkCandidate]
    total: int
    truncated: bool
    next_actions: list[BrowserToolAction]


class BrowserFindLinkResult(BrowserLinksResult):
    query: str


class BrowserFieldCandidate(TypedDict, total=False):
    name: str
    type: str
    tag: str
    label: str | None
    placeholder: str | None
    value: str | None
    selector: str | None
    required: bool
    disabled: bool
    visible: bool
    action: BrowserActionSuggestion
    rank: int
    score: float
    reason: str


class BrowserFieldsResult(TypedDict):
    url: str
    title: str
    fields: list[BrowserFieldCandidate]
    total: int
    truncated: bool
    next_actions: list[BrowserToolAction]


class BrowserFindFieldResult(BrowserFieldsResult):
    query: str


class BrowserHeadingCandidate(TypedDict, total=False):
    level: int
    text: str
    selector: str | None
    visible: bool


class BrowserLandmarkCandidate(TypedDict, total=False):
    role: str
    text: str
    selector: str | None
    visible: bool


class BrowserPageOutlineResult(TypedDict):
    url: str
    title: str
    headings: list[BrowserHeadingCandidate]
    landmarks: list[BrowserLandmarkCandidate]
    links: list[BrowserLinkCandidate]
    fields: list[BrowserFieldCandidate]
    counts: dict[str, int]
    truncated: bool
    next_actions: list[BrowserToolAction]


class WebLinkCandidate(TypedDict, total=False):
    text: str
    href: str
    label: str
    title: str
    rel: str
    tag: str
    action: BrowserActionSuggestion
    actions: list[BrowserToolAction]
    score: float
    reason: str


class WebPageOutlineResult(TypedDict, total=False):
    url: str
    title: str
    canonical: str | None
    headings: list[str]
    links: list[WebLinkCandidate]
    total_links: int
    truncated: bool
    next_actions: list[BrowserToolAction]


class WebFindLinksResult(WebPageOutlineResult):
    query: str


class WebSiteLinksResult(WebFindLinksResult):
    sources: list[str]


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
