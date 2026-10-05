# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Macro tools: save / list / run / delete / run_sequence + run_test_suite."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import Context

import octowright.macros as macro_mod
from octowright.dashboard_events import publish_dashboard_invalidation_nowait
from octowright.macros import listing as macro_listing
from octowright.mcp_types import (
    CleanupResult,
    MacroCompileResult,
    MacroDeleteResult,
    MacroLintIssue,
    MacroLintResult,
    MacroRepairApplyResult,
    MacroRepairPreviewResult,
    MacroRunResult,
    MacroSaveResult,
    MacroSequenceResult,
    TestSuiteResult,
)
from octowright.server._state import mcp, pool


@mcp.tool(
    structured_output=False,
    description=(
        "Save the current recording of a live instance as a named, reusable macro. "
        "`parameters` is a dict mapping parameter NAME to its literal VALUE in this "
        "recording — those values get replaced by {{name}} placeholders in the saved "
        'macro. Example: parameters={"email":"me@octowright.test","password":"hunter2"}. '
        "Drops launch/close/snapshot entries by default. Re-saving over an existing macro keeps "
        "its parameter_specs. Returns the saved macro path."
    ),
)
def macro_save(
    instance_id: str,
    name: str,
    description: str | None = None,
    parameters: dict[str, str] | None = None,
    include_launch: bool = False,
) -> MacroSaveResult:
    session = pool.get(instance_id)
    path = macro_mod.save_macro(
        recording_path=session.log_path,
        name=name,
        description=description,
        parameters=parameters,
        include_launch=include_launch,
    )
    publish_dashboard_invalidation_nowait("macros")
    return {"saved": True, "name": name, "path": str(path)}


@mcp.tool(
    structured_output=False,
    description=(
        "List saved macros, newest first, in bounded pages. Filter with prefix/contains, page with "
        "cursor. response_mode='families' rolls the flat namespace up by naming prefix and lists no "
        "macros at all -- ask for that first when you do not know what exists, then filter. "
        "'summary' (default) caps descriptions and omits paths; 'full' returns every field."
    ),
)
def macro_list(
    prefix: str | None = None,
    contains: str | None = None,
    limit: int | None = None,
    cursor: int = 0,
    response_mode: str = "summary",
) -> dict[str, Any]:
    # Unbounded, this returned every macro with every description: 402,942
    # characters on a real 337-macro machine, in a tool that ships in the
    # `macros` capability profile. See macros/listing.py.
    return macro_listing.select_macros(
        macro_mod.list_macros(),
        prefix=prefix,
        contains=contains,
        limit=limit,
        cursor=cursor,
        response_mode=response_mode,
        root=str(macro_mod.MACROS_DIR),
    )


@mcp.tool(structured_output=False, description="Plan/update a saved macro artifact manifest without running it.")
def macro_artifact_plan(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    from octowright.macros import artifacts as macro_artifacts

    return macro_artifacts.plan_macro_artifact(name=name, args=args)


@mcp.tool(structured_output=False, description="List saved macro artifact manifests, newest first.")
def macro_artifact_list(name: str | None = None, limit: int = 20) -> dict[str, Any]:
    from octowright.macros import artifacts as macro_artifacts

    return macro_artifacts.list_macro_artifacts(name=name, limit=limit)


@mcp.tool(structured_output=False, description="Replay a macro and write an artifact bundle with evidence files.")
async def macro_artifact_run(
    instance_id: str,
    name: str,
    args: dict[str, Any] | None = None,
    capture: bool = True,
    notes: str | None = None,
    slowmo_ms: int | None = None,
) -> dict[str, Any]:
    from octowright.macros import artifacts as macro_artifacts

    session = pool.get(instance_id)
    return await macro_artifacts.run_macro_artifact(
        session=session,
        name=name,
        args=args,
        capture=capture,
        notes=notes,
        slowmo_ms=slowmo_ms,
    )


@mcp.tool(structured_output=False, description="Return a redacted digest for a saved macro or recording JSONL path.")
def macro_digest(name: str | None = None, recording_path: str | None = None, max_chars: int = 4000) -> dict[str, Any]:
    from octowright.macros import artifacts as macro_artifacts

    return macro_artifacts.macro_digest(name=name, recording_path=recording_path, max_chars=max_chars)


@mcp.tool(
    structured_output=False,
    description=(
        "Export a saved macro as an import-safe Python argparse CLI script. The script enforces "
        "replay's guards: it types a credential only on an origin passed as --trusted-origin (or "
        "listed in the step's allowed_origins), uploads only from the upload roots, and runs "
        "expect_network_clean / expect_no_text, printing what each passing check saw."
    ),
)
def macro_export_cli(
    name: str,
    out_path: str | None = None,
    args: dict[str, Any] | None = None,
    include_evidence: bool = True,
) -> dict[str, Any]:
    from octowright.macros import artifacts as macro_artifacts

    return macro_artifacts.export_macro_cli(
        name=name,
        out_path=out_path,
        args=args,
        include_evidence=include_evidence,
    )


@mcp.tool(
    structured_output=False,
    description=(
        "Replay a saved macro against a live browser instance. `args` supplies values "
        "for any {{placeholders}} the macro declares. Lifecycle actions (launch, close, "
        "snapshot) are skipped. Pass `slowmo_ms` to insert a per-action delay (after the "
        "status pill updates, before the action dispatches) so a human can follow along; "
        "default comes from OCTOWRIGHT_MACRO_SLOWMO_MS. Returns {macro, executed, skipped, "
        "args_used, slowmo_ms}, plus `assertions` when the macro ran expect_network_clean or "
        "expect_no_text: what each saw, with a `warning` on a pass that judged less than asked "
        "(requests still in flight, a selector that matched nothing). A step can set "
        "`require_settled: true` (expect_network_clean) or `require_match: true` "
        "(expect_no_text) to fail on that caveat instead. A macro's parameter_specs "
        '({"name": {"sensitive": true|false}}) declare which parameters are sensitive; '
        "`warnings` names any declaration that was ignored (a credential-like name cannot be "
        "declared not sensitive)."
    ),
)
async def macro_run(
    instance_id: str,
    name: str,
    args: dict[str, Any] | None = None,
    slowmo_ms: int | None = None,
    ctx: Context | None = None,
) -> MacroRunResult:
    # ``ctx`` is injected by the MCP server (excluded from the client-facing schema). It
    # lets a long macro stream MCP progress per step, which the follower bridge
    # uses to keep the call alive past the flat request timeout.
    session = pool.get(instance_id)
    return await macro_mod.run_macro(session=session, name=name, args=args, slowmo_ms=slowmo_ms, ctx=ctx)


@mcp.tool(structured_output=False, description="Delete a saved macro by name. Raises if the macro does not exist.")
def macro_delete(name: str) -> MacroDeleteResult:
    path = macro_mod.delete_macro(name)
    publish_dashboard_invalidation_nowait("macros")
    return {"deleted": True, "name": name, "path": str(path)}


@mcp.tool(
    structured_output=False,
    description=(
        "Replay several saved macros in order against one live instance. "
        "`names` is the list of macro names; `args_list[i]` supplies args for `names[i]`. "
        "A failing step does not error the call: it returns {sequence, steps, ok, stopped_at}. "
        "By default (stop_on_failure=True) the chain stops after the first failing step; the "
        "result has ok: false, stopped_at: <that step's index>, and steps up to and including "
        "it (a failed step carries ok: false, error, and `failure` with the macro's structured "
        "failure details such as failed_at_step). Pass False to run every step and collect "
        "per-step outcomes; stopped_at is then null. A missing macro is a failed step. The call "
        "still errors, before any step acts, when it cannot run at all: unknown instance, "
        "malformed names/args_list, an args_list longer than names, a name no macro can have "
        "(such as '..'), or the session's operation gate refusing."
    ),
)
async def macro_run_sequence(
    instance_id: str,
    names: list[str],
    args_list: list[dict[str, Any]] | None = None,
    stop_on_failure: bool = True,
    slowmo_ms: int | None = None,
    ctx: Context | None = None,
) -> MacroSequenceResult:
    # ``ctx`` (server-injected, hidden from the client schema) streams per-step
    # progress through the inner macros so a long sequence keeps the bridge alive.
    session = pool.get(instance_id)
    return await macro_mod.run_sequence(
        session=session,
        names=names,
        args_list=args_list,
        stop_on_failure=stop_on_failure,
        slowmo_ms=slowmo_ms,
        ctx=ctx,
    )


@mcp.tool(
    structured_output=False,
    description=(
        "Static-analysis pass on a saved macro. Catches missing required fields, unknown "
        "action types, lifecycle actions that don't belong in macros, empty conditional "
        "branches, string literals that look like credentials (email/password patterns) "
        "but aren't parameterized, and parameter_specs that are malformed or ignored. Returns "
        "errors + warnings with per-action indices. Run "
        "this whenever you hand-edit a macro JSON file."
    ),
)
def macro_lint(name: str) -> MacroLintResult:
    from octowright.macros import lint as _lint

    macro = macro_mod.load_macro(name)  # raises FileNotFoundError if missing
    issues = _lint.lint_macro(macro)
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    issue_rows: list[MacroLintIssue] = [
        {"severity": i.severity, "code": i.code, "message": i.message, "action_index": i.action_index} for i in issues
    ]
    return {
        "macro": name,
        "issues": issue_rows,
        "summary": f"{len(issues)} issues: {len(errors)} errors, {len(warnings)} warnings",
        "ok": len(errors) == 0,
    }


@mcp.tool(
    structured_output=False,
    description=(
        "Preview non-mutating repair suggestions for a saved macro. Returns selector-based "
        "actions with stored semantic replacement candidates and manual review prompts; "
        "does not edit or replay the macro."
    ),
)
def macro_repair_preview(name: str) -> MacroRepairPreviewResult:
    return macro_mod.repair_preview(name)


@mcp.tool(
    structured_output=False,
    description=(
        "Apply a stored-heuristic repair to ONE action of a saved macro and save it in place: "
        "rewrite a brittle selector-based click/fill into its semantic click_by/fill_by form "
        "(using the role/label/text/test_id captured at record time), dropping the stale CSS "
        "selector. Call macro_repair_preview first to see which action_index values are "
        "repairable. Raises if the index is out of range or the action has no stored semantic "
        "locator — re-record or hand-edit those. Returns {macro, action_index, applied, "
        "original_action, replacement_action, path}."
    ),
)
def macro_repair_apply(name: str, action_index: int) -> MacroRepairApplyResult:
    result = macro_mod.repair_apply(name, action_index)
    publish_dashboard_invalidation_nowait("macros")
    return result


@mcp.tool(
    structured_output=False,
    description=(
        "Compile a friendly YAML macro DSL document into canonical macro JSON. "
        "By default this is a dry-run preview. Pass write=True to save the compiled "
        "macro to the normal macro JSON location; a write keeps the saved version's "
        "created_at, and its parameter_specs unless the YAML declares its own, and "
        "returns `warnings` when the write makes a parameter less sensitive. "
        "The runtime still uses JSON macros."
    ),
)
def macro_compile(
    yaml_text: str,
    name: str | None = None,
    write: bool = False,
    strict: bool = True,
) -> MacroCompileResult:
    from octowright.macros import dsl as macro_dsl

    compiled = macro_dsl.compile_macro_yaml(yaml_text, name=name, strict=strict)
    result: MacroCompileResult = {"compiled": compiled, "written": False}
    if write:
        path, findings = macro_mod.write_compiled_macro(name=compiled["name"], macro=compiled)
        publish_dashboard_invalidation_nowait("macros")
        result["written"] = True
        result["path"] = str(path)
        if findings:
            result["warnings"] = [message for _code, message in findings]
    return result


@mcp.tool(
    structured_output=False,
    description=(
        "Run all `[test]`-tagged macros against ephemeral browsers and emit a JUnit "
        "XML report. Discovery uses MACROS_DIR (override via OCTOWRIGHT_MACROS_DIR). "
        "Spawns one browser per test (kind defaults to 'webkit') with up to "
        "max_parallel running concurrently. out_path must sit under the recordings "
        "root (OCTOWRIGHT_RECORDINGS) and is checked before any browser launches; "
        "omitted, the report is a timestamped file there. Returns "
        "{passed, failed, total, report_path, results: [per-test summary]}."
    ),
)
async def run_test_suite(
    kind: str = "webkit",
    tag: str | None = None,
    out_path: str | None = None,
    max_parallel: int = 1,
) -> TestSuiteResult:
    import octowright.runner as runner

    return await runner.run_suite(
        kind=kind,
        tag=tag,
        out_path=out_path,
        pool=pool,
        max_parallel=max_parallel,
    )


@mcp.tool(
    structured_output=False,
    description=(
        "Delete a macro's artifact directory: its manifest and configured critical points, "
        "every run bundle, and any exported CLI. Irreversible. Nothing else removes these -- "
        "recordings_cleanup deliberately preserves artifacts because age does not make a "
        "curated artifact disposable, and macro_delete keeps them on purpose so the history "
        "of what a macro did outlives its definition. Returns the run count it removed."
    ),
)
def macro_artifact_delete(name: str) -> dict[str, Any]:
    from octowright.macros import artifacts as macro_artifacts

    return macro_artifacts.delete_macro_artifact(name)


def _live_plugin_sessions() -> list[Any]:
    """Every live session of every enabled plugin pool.

    Not guarded per pool, unlike the dashboard listing: a pool that cannot
    say which sessions it holds would let the sweep delete a live recording,
    so its error fails the cleanup instead.
    """
    from octowright.server import plugin_state

    return [
        session for plugin_pool in plugin_state.registry().pools().values() for session in plugin_pool.iter_sessions()
    ]


@mcp.tool(
    structured_output=False,
    description=(
        "Find recording artefacts (JSONL logs, screenshots, videos, traces) older than "
        "`days` and optionally delete them. Defaults to dry_run=True so the first call "
        "is always safe. Pass dry_run=False to actually delete. Returns a per-kind "
        "breakdown so you can see what would be freed before committing. Macro "
        "artifacts (manifests, critical points, run bundles, exports) live under the "
        "same root but are never swept -- they are curated, not incidental, so age "
        "does not make them disposable. Use macro_artifact_* tools to manage those. "
        "Files belonging to a live or closing browser, or a live plugin session such as "
        "a terminal, are skipped whatever their age."
    ),
)
def recordings_cleanup(days: float = 30.0, dry_run: bool = True) -> CleanupResult:
    import octowright.recording_cleanup as _rc
    from octowright.defaults import RECORDINGS_DIR

    # Files a live or closing browser -- or a live plugin session, such as a
    # terminal -- still writes are never swept, however old their mtime: an
    # idle session's recording stops changing.
    in_use = _rc.session_file_matcher([*pool.iter_sessions_including_closing(), *_live_plugin_sessions()])
    stale = [entry for entry in _rc.find_stale_files(RECORDINGS_DIR, days) if not in_use(entry.path)]
    summary = _rc.cleanup_stale(stale, dry_run=dry_run)
    return {
        "recordings_dir": str(RECORDINGS_DIR),
        "days": days,
        "dry_run": dry_run,
        "found": len(stale),
        "removed": summary["removed_count"] if not dry_run else 0,
        "would_remove": len(stale) if dry_run else 0,
        "freed_bytes": summary["removed_bytes"] if not dry_run else sum(s.size_bytes for s in stale),
        "by_kind": {
            kind: sum(1 for s in stale if s.kind == kind)
            for kind in ("recording", "screenshot", "video", "trace", "other")
        },
        "errors": summary["errors"],
    }


async def _delete_unused_profiles(stale: list[Any]) -> tuple[dict[str, Any], int]:
    """Delete each stale profile no browser holds; ``(summary, skipped)``.

    Decided again under the profile's lifecycle lock, as ``profile_delete``
    does: a launch still preparing holds that lock before its session is
    registered, and a browser may have opened the profile since the scan.
    """
    import asyncio

    import octowright.profile_cleanup as _pc
    from octowright.profile_lifecycle import profile_lifecycle_lock

    summary: dict[str, Any] = {"removed_count": 0, "removed_bytes": 0, "errors": []}
    skipped = 0
    for entry in stale:
        async with profile_lifecycle_lock(entry.engine, entry.persona):
            if pool.profile_users(entry.persona, kind=entry.engine):
                skipped += 1
                continue
            one = await asyncio.to_thread(_pc.cleanup_stale, [entry], dry_run=False)
        summary["removed_count"] += one["removed_count"]
        summary["removed_bytes"] += one["removed_bytes"]
        summary["errors"].extend(one["errors"])
    return summary, skipped


@mcp.tool(
    structured_output=False,
    description=(
        "Find persistent profile dirs older than `days` and not in use by any "
        "live browser session, and optionally delete them. Defaults to "
        "dry_run=True so the first call is always safe. Pass dry_run=False to "
        "actually delete. Now that browser_launch(label=X) auto-promotes to a "
        "persistent profile, casual one-off labels accumulate on disk — call "
        "this periodically to free space. Returns a per-persona breakdown with "
        "size + age info so you can see what would be freed before committing."
    ),
)
async def profile_cleanup(days: float = 30.0, dry_run: bool = True) -> CleanupResult:
    from pathlib import Path as _Path

    import octowright.profile_cleanup as _pc
    from octowright.defaults import PROFILES_DIR

    # Closing sessions count: one has left ``_sessions`` once its close ticket
    # owns the gate, but still holds its profile's database files open.
    in_use_dirs = [
        _Path(udd)
        for session in pool.iter_sessions_including_closing()
        if (udd := getattr(session, "user_data_dir", None))
    ]

    stale = _pc.find_stale_profiles(PROFILES_DIR, days, in_use=in_use_dirs)
    summary: dict[str, Any] = {"removed_count": 0, "removed_bytes": 0, "errors": []}
    skipped_at_delete = 0
    if not dry_run:
        summary, skipped_at_delete = await _delete_unused_profiles(stale)
    return {
        "profiles_dir": str(PROFILES_DIR),
        "days": days,
        "dry_run": dry_run,
        "found": len(stale),
        "removed": summary["removed_count"] if not dry_run else 0,
        "would_remove": len(stale) if dry_run else 0,
        "freed_bytes": summary["removed_bytes"] if not dry_run else sum(s.size_bytes for s in stale),
        "skipped_in_use": len(in_use_dirs) + skipped_at_delete,
        "details": [
            {
                "persona": s.persona,
                "engine": s.engine,
                "path": str(s.path),
                "size_bytes": s.size_bytes,
                "age_days": round(s.age_days, 1),
            }
            for s in stale
        ],
        "errors": summary["errors"],
    }


@mcp.tool(
    structured_output=False,
    description="Explain what a macro does in plain English and provide its semantic intent.",
)
async def macro_explain(actions: list[dict[str, Any]]) -> dict[str, str]:
    """Summarize a list of macro actions and return a one-line intent.

    Args:
        actions: List of macro actions (JSONL format).
    """
    from octowright.macros.semantic import explain_macro

    return explain_macro(actions)


@mcp.tool()
async def macro_artifact_critical_points_get(name: str) -> dict[str, Any]:
    from octowright.macros import artifacts as macro_artifacts

    return macro_artifacts.macro_artifact_critical_points_get(name)


@mcp.tool()
async def macro_artifact_critical_points_set(name: str, critical_points: list[dict[str, Any]]) -> dict[str, Any]:
    from octowright.macros import artifacts as macro_artifacts

    return macro_artifacts.macro_artifact_critical_points_set(name, critical_points)


@mcp.tool()
async def macro_artifact_verify(name: str, run_id: str | None = None) -> dict[str, Any]:
    from octowright.macros import artifacts as macro_artifacts

    return macro_artifacts.macro_artifact_verify(name, run_id)


@mcp.tool()
async def macro_artifact_status(name: str) -> dict[str, Any]:
    from octowright.macros import artifacts as macro_artifacts

    return macro_artifacts.macro_artifact_status(name)
