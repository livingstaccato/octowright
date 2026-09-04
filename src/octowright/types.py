# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict


class LaunchOptions(TypedDict, total=False):
    kind: str
    url: str | None
    headed: bool | None
    label: str | None
    viewport_w: int | None
    viewport_h: int | None
    profile: str | None
    stabilize: bool
    record_video: bool
    trace: bool
    har: bool
    har_path: str | None
    har_mode: str
    har_url_filter: str | None
    har_content: str | None
    badge: bool
    badge_position: str
    tile: bool
    ephemeral: bool
    session: bool


class MacroActionBase(TypedDict, total=False):
    ts: str
    kind: str
    profile: str | None
    instance_id: str


class NavigateAction(MacroActionBase):
    action: Literal["navigate"]
    url: str


class ClickAction(MacroActionBase):
    action: Literal["click"]
    selector: str
    role: NotRequired[str]
    role_name: NotRequired[str]
    role_exact: NotRequired[bool]
    label: NotRequired[str]
    label_exact: NotRequired[bool]
    text: NotRequired[str]
    text_exact: NotRequired[bool]
    test_id: NotRequired[str]
    timeout_ms: NotRequired[int | None]


class ClickByAction(MacroActionBase):
    action: Literal["click_by"]
    selector: NotRequired[str]
    role: NotRequired[str]
    role_name: NotRequired[str]
    role_exact: NotRequired[bool]
    label: NotRequired[str]
    label_exact: NotRequired[bool]
    text: NotRequired[str]
    text_exact: NotRequired[bool]
    test_id: NotRequired[str]
    timeout_ms: NotRequired[int | None]


class TypeAction(MacroActionBase):
    action: Literal["type"]
    selector: str
    text: str
    delay_ms: NotRequired[int | None]


class FillAction(MacroActionBase):
    action: Literal["fill"]
    selector: str
    value: str
    role: NotRequired[str]
    role_name: NotRequired[str]
    role_exact: NotRequired[bool]
    label: NotRequired[str]
    label_exact: NotRequired[bool]
    text: NotRequired[str]
    text_exact: NotRequired[bool]
    test_id: NotRequired[str]
    timeout_ms: NotRequired[int | None]


class FillByAction(MacroActionBase):
    action: Literal["fill_by"]
    value: str
    selector: NotRequired[str]
    role: NotRequired[str]
    role_name: NotRequired[str]
    role_exact: NotRequired[bool]
    label: NotRequired[str]
    label_exact: NotRequired[bool]
    text: NotRequired[str]
    text_exact: NotRequired[bool]
    test_id: NotRequired[str]
    timeout_ms: NotRequired[int | None]


class PressKeyAction(MacroActionBase):
    action: Literal["press_key"]
    key: str


class ScreenshotAction(MacroActionBase):
    action: Literal["screenshot"]
    path: NotRequired[str]


class EvaluateAction(MacroActionBase):
    action: Literal["evaluate"]
    expression: str


class WaitForAction(MacroActionBase):
    action: Literal["wait_for"]
    selector: NotRequired[str]
    text: NotRequired[str]
    timeout_ms: NotRequired[int | None]


class ExpectUrlAction(MacroActionBase):
    action: Literal["expect_url"]
    pattern: str
    mode: NotRequired[Literal["regex", "equals", "contains"]]


class ExpectTextAction(MacroActionBase):
    action: Literal["expect_text"]
    selector: str
    text: str
    mode: NotRequired[Literal["contains", "equals", "regex"]]
    timeout_ms: NotRequired[int | None]


class ExpectSelectorAction(MacroActionBase):
    action: Literal["expect_selector"]
    selector: str
    present: NotRequired[bool]
    timeout_ms: NotRequired[int | None]


class ExpectJsAction(MacroActionBase):
    action: Literal["expect_js"]
    expression: str
    equals: NotRequired[object]
    timeout_ms: NotRequired[int | None]


class MockRouteAction(MacroActionBase):
    action: Literal["mock_route"]
    pattern: str
    status: NotRequired[int]
    body: NotRequired[str | None]
    content_type: NotRequired[str]
    headers: NotRequired[dict[str, str]]


class UnmockRouteAction(MacroActionBase):
    action: Literal["unmock_route"]
    pattern: str


class SetDialogPolicyAction(MacroActionBase):
    action: Literal["set_dialog_policy"]
    policy: str
    prompt_text: NotRequired[str | None]


class SetInputFilesAction(MacroActionBase):
    action: Literal["set_input_files"]
    selector: str
    paths: list[str]


MacroAction = (
    NavigateAction
    | ClickAction
    | ClickByAction
    | TypeAction
    | FillAction
    | FillByAction
    | PressKeyAction
    | ScreenshotAction
    | EvaluateAction
    | WaitForAction
    | ExpectUrlAction
    | ExpectTextAction
    | ExpectSelectorAction
    | ExpectJsAction
    | MockRouteAction
    | UnmockRouteAction
    | SetDialogPolicyAction
    | SetInputFilesAction
)


class PlaywrightFailureHint(TypedDict):
    category: str
    probable_cause: str
    recommended_actions: list[str]


class PersonaListEntry(TypedDict):
    name: str
    display_name: str | None
    engines: list[str]
    path: str
    mtime: float
    last_used: str


class CredentialCheckEntry(TypedDict):
    name: str
    source: str
    reference: str
    ok: bool
    error: str | None


class CredentialCheckReport(TypedDict):
    persona: str
    checked: list[CredentialCheckEntry]
    ok: bool
    summary: str


class SessionManifestEntry(TypedDict, total=False):
    session_id: str
    kind: str
    label: str | None
    profile: str | None
    user_data_dir: str | None
    log_path: str
    launched_at: str
    updated_at: str
    state: str
    daemon_pid: int
    reason: str  # added by stale_entries() for orphan reporting


class SessionManifest(TypedDict):
    schema_version: int
    sessions: dict[str, SessionManifestEntry]


# MCP-tool wire-facing returns moved to octowright.mcp_types so this file
# stays under the 500-LOC ceiling. Import them from there directly.
