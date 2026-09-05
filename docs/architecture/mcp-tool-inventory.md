<!--
SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
SPDX-License-Identifier: Apache-2.0
SPDX-Comment: Part of octowright.
-->
# MCP tool inventory

**Authoritative source: `uv run octowright selftest`.** This file is still written by hand, but it is no longer trusted on its own: `scripts/check_tool_inventory_docs.py` runs under `make lint` and fails when any count or list here — or README.md's own total — disagrees with the live registry. It has to, because the disclaimer that used to sit in this spot ("may lag by one or two tools") turned out to be describing reality: the all-only section had lost `browser_a11y_dragdrop` and `macro_artifact_delete`, so it claimed 27 where the registry held 29, and README advertised the full surface as 129.

The guard measures a **core** install in a child process with its config dirs redirected, because an empty `OCTOWRIGHT_PLUGINS` falls through to the operator's `plugins.yaml` — so an in-process count reads 140 on a machine that enables the terminal plugin and 133 in CI. The `terminals` row below is therefore checked against the totals arithmetic rather than against registration.

Current totals: **97** profile-scoped + **7** always-on + **29** all-only = **133 total**.

With the `terminal` session-kind plugin enabled (`OCTOWRIGHT_PLUGINS=terminal` —
on PyPI as of core 0.19.2, see `packages/octowright-terminal/README.md`), the
`terminals` profile it declares adds 7 more tools for **140 total**.

## Profiles

A profile is selected with `OCTOWRIGHT_PROFILE=<name>[,<name>...]` (env var) or `octowright serve --profile=<name>[,<name>...]`. Multiple profiles compose. Unset (or `all`) registers every tool.

| Profile | Tools | Purpose |
|---|---:|---|
| `core` | 24 | Minimum browser-driving surface plus compact DOM and HTTP-first discovery. |
| `advanced` | 33 | Inspection, assertions, ARIA locators, summaries, viewport sync, capture cache, artifact manifest, export script, relaunch. |
| `macros` | 15 | Record → save → repair → replay pipeline + artifact bundles. |
| `scenarios` | 12 | Multi-browser orchestration. |
| `personas` | 8 | Identity + on-disk profile management. |
| `goldens` | 5 | Accessibility-tree snapshot baselines + diff. |
| `terminals` | 7 | Optional PTY/SSH/telnet terminal sessions; declared by the `terminal` session-kind plugin and only present when it is enabled via `OCTOWRIGHT_PLUGINS`. |
| _(always-on)_ | 7 | Registers under every profile (and under no profile). Status, dashboard, takeover detection, Advisor. |
| _(all-only)_ | 29 | Registers only when **no** `--profile` filter is active. Frames/tabs/popups, network mocking, request headers, dialog policy, traces, roster fan-out, macro-artifact extras, cleanup. |

## Per-profile tool list

### `core` (24)

`browser_brief`, `browser_click`, `browser_close`, `browser_close_all`, `browser_set_protected`, `browser_fields`, `browser_fill`, `browser_find_field`, `browser_find_link`, `browser_launch`, `browser_links`, `browser_list`, `browser_navigate`, `browser_page_outline`, `browser_press_key`, `browser_quick_launch`, `browser_read_markdown`, `browser_screenshot`, `browser_suggest_for_url`, `browser_type`, `browser_wait_for`, `web_find_links`, `web_page_outline`, `web_site_links`

### `advanced` (33)

`capture_cleanup`, `capture_create`, `capture_get`, `capture_lines`, `capture_list`, `capture_search`, `capture_summary`, `browser_artifact_manifest`, `browser_console_messages`, `browser_console_summary`, `browser_downloads`, `browser_downloads_summary`, `browser_each`, `browser_evaluate`, `browser_expect_js`, `browser_expect_selector`, `browser_expect_text`, `browser_expect_url`, `browser_export_script`, `browser_get_text_by`, `browser_network_requests`, `browser_network_summary`, `browser_websocket_messages`, `browser_websocket_summary`, `browser_observe`, `browser_relaunch_fluid`, `browser_resize`, `browser_recording_path`, `browser_snapshot`, `browser_tail_recording`, `browser_wait_for_download`, `browser_viewport_status`, `browser_viewport_sync`

### `macros` (15)

`macro_artifact_list`, `macro_artifact_plan`, `macro_artifact_run`, `macro_compile`, `macro_delete`, `macro_digest`, `macro_explain`, `macro_export_cli`, `macro_lint`, `macro_list`, `macro_repair_apply`, `macro_repair_preview`, `macro_run`, `macro_run_sequence`, `macro_save`

### `scenarios` (12)

`scenario_list`, `scenario_participants`, `scenario_plan`, `scenario_remap_participants`, `scenario_run_as_test`, `scenario_run_macro`, `scenario_spawn_template`, `scenario_start`, `scenario_status`, `scenario_stop`, `scenario_tail`, `scenario_wait_for_sync`

### `goldens` (5)

`golden_assert`, `golden_delete`, `golden_list`, `golden_save`, `golden_verify_loop`

### `personas` (8)

`persona_create`, `persona_credentials_check`, `persona_delete`, `persona_get`, `persona_list`, `profile_cleanup`, `profile_delete`, `profile_list`

### `terminals` (7, optional plugin)

`terminal_launch`, `terminal_send_input`, `terminal_snapshot`, `terminal_read`, `terminal_wait_for`, `terminal_close`, `terminal_list`

### Always-on meta + Advisor (7)

`octowright_advisor_record_macro_observation`, `octowright_advisor_set_preference`, `octowright_advisor_status`, `octowright_check_takeover`, `octowright_dashboard_url`, `octowright_status`, `octowright_storage_report`

### All-only (29) — register only when no `--profile` filter is active

`browser_a11y_dragdrop`, `browser_capture_and_close`, `browser_drag`, `browser_hover`, `browser_inject_headers`, `browser_list_frames`, `browser_mock_route`, `browser_navigate_back`, `browser_open_trace`, `browser_open_url`, `browser_reset_frame`, `browser_select_option`, `browser_set_dialog_policy`, `browser_set_extra_http_headers`, `browser_set_input_files`, `browser_spawn_roster`, `browser_switch_frame`, `browser_uninject_headers`, `browser_unmock_route`, `macro_artifact_critical_points_get`, `macro_artifact_critical_points_set`, `macro_artifact_delete`, `macro_artifact_status`, `macro_artifact_verify`, `page_close`, `page_list`, `page_switch`, `recordings_cleanup`, `run_test_suite`

Close-capable tools (`browser_close`, `browser_close_all`, `browser_capture_and_close`) honor protected sessions. They refuse protected browsers unless the caller passes `force=True`; `browser_capture_and_close` performs that check before screenshot/snapshot capture so refused calls do not create artifacts.
