# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Macro tools: save / list / run / delete / run_sequence + run_test_suite."""

from __future__ import annotations

from typing import Any

from .. import macros as macro_mod
from ._state import mcp, pool


@mcp.tool(
    structured_output=False,
    description=(
        "Save the current recording of a live instance as a named, reusable macro. "
        "`parameters` is a dict mapping parameter NAME to its literal VALUE in this "
        "recording — those values get replaced by {{name}} placeholders in the saved "
        'macro. Example: parameters={"email":"me@example.com","password":"hunter2"}. '
        "Drops launch/close/snapshot entries by default. Returns the saved macro path."
    ),
)
def macro_save(
    instance_id: str,
    name: str,
    description: str | None = None,
    parameters: dict[str, str] | None = None,
    include_launch: bool = False,
) -> dict[str, Any]:
    session = pool.get(instance_id)
    path = macro_mod.save_macro(
        recording_path=session.log_path,
        name=name,
        description=description,
        parameters=parameters,
        include_launch=include_launch,
    )
    return {"saved": True, "name": name, "path": str(path)}


@mcp.tool(structured_output=False, description="List saved macros with their parameters and metadata.")
def macro_list() -> list[dict[str, Any]]:
    return macro_mod.list_macros()


@mcp.tool(
    structured_output=False,
    description=(
        "Replay a saved macro against a live browser instance. `args` supplies values "
        "for any {{placeholders}} the macro declares. Lifecycle actions (launch, close, "
        "snapshot) are skipped. Returns {macro, executed, skipped, args_used}."
    ),
)
async def macro_run(
    instance_id: str,
    name: str,
    args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    session = pool.get(instance_id)
    return await macro_mod.run_macro(session=session, name=name, args=args)


@mcp.tool(structured_output=False, description="Delete a saved macro by name. Raises if the macro does not exist.")
def macro_delete(name: str) -> dict[str, Any]:
    path = macro_mod.delete_macro(name)
    return {"deleted": True, "name": name, "path": str(path)}


@mcp.tool(
    structured_output=False,
    description=(
        "Replay several saved macros in order against one live instance. "
        "`names` is the list of macro names; `args_list[i]` supplies args for `names[i]`. "
        "By default a failing step aborts the chain (stop_on_failure=True); pass False "
        "to keep going and collect per-step outcomes."
    ),
)
async def macro_run_sequence(
    instance_id: str,
    names: list[str],
    args_list: list[dict[str, Any]] | None = None,
    stop_on_failure: bool = True,
) -> dict[str, Any]:
    session = pool.get(instance_id)
    return await macro_mod.run_sequence(
        session=session,
        names=names,
        args_list=args_list,
        stop_on_failure=stop_on_failure,
    )


@mcp.tool(
    structured_output=False,
    description=(
        "Static-analysis pass on a saved macro. Catches missing required fields, unknown "
        "action types, lifecycle actions that don't belong in macros, empty conditional "
        "branches, and string literals that look like credentials (email/password patterns) "
        "but aren't parameterized. Returns errors + warnings with per-action indices. Run "
        "this whenever you hand-edit a macro JSON file."
    ),
)
def macro_lint(name: str) -> dict[str, Any]:
    from .. import macro_lint as _lint

    macro = macro_mod.load_macro(name)  # raises FileNotFoundError if missing
    issues = _lint.lint_macro(macro)
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    return {
        "macro": name,
        "issues": [
            {"severity": i.severity, "code": i.code, "message": i.message, "action_index": i.action_index}
            for i in issues
        ],
        "summary": f"{len(issues)} issues: {len(errors)} errors, {len(warnings)} warnings",
        "ok": len(errors) == 0,
    }


@mcp.tool(
    structured_output=False,
    description=(
        "Run all test macros in a directory, producing a JUnit XML report. A macro is "
        "considered a test if its description starts with [test]. Spawns one ephemeral "
        "browser per test (kind defaults to 'webkit'). Returns {passed, failed, total, "
        "report_path, results: [per-test summary]}."
    ),
)
async def run_test_suite(
    macros_dir: str | None = None,
    kind: str = "webkit",
    tag: str | None = None,
    out_path: str | None = None,
) -> dict[str, Any]:
    from .. import runner

    return await runner.run_suite(
        macros_dir=macros_dir,
        kind=kind,
        tag=tag,
        out_path=out_path,
        pool=pool,
    )


@mcp.tool(
    structured_output=False,
    description=(
        "Find recording artefacts (JSONL logs, screenshots, videos, traces) older than "
        "`days` and optionally delete them. Defaults to dry_run=True so the first call "
        "is always safe. Pass dry_run=False to actually delete. Returns a per-kind "
        "breakdown so you can see what would be freed before committing."
    ),
)
def recordings_cleanup(days: float = 30.0, dry_run: bool = True) -> dict[str, Any]:
    from .. import recording_cleanup as _rc
    from ..defaults import RECORDINGS_DIR

    stale = _rc.find_stale_files(RECORDINGS_DIR, days)
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
def profile_cleanup(days: float = 30.0, dry_run: bool = True) -> dict[str, Any]:
    from .. import profile_cleanup as _pc
    from ..defaults import PROFILES_DIR

    in_use_dirs: list[Any] = []
    for session in pool._sessions.values():
        udd = getattr(session, "user_data_dir", None)
        if udd:
            from pathlib import Path as _Path

            in_use_dirs.append(_Path(udd))

    stale = _pc.find_stale_profiles(PROFILES_DIR, days, in_use=in_use_dirs)
    summary = _pc.cleanup_stale(stale, dry_run=dry_run)
    return {
        "profiles_dir": str(PROFILES_DIR),
        "days": days,
        "dry_run": dry_run,
        "found": len(stale),
        "removed": summary["removed_count"] if not dry_run else 0,
        "would_remove": len(stale) if dry_run else 0,
        "freed_bytes": summary["removed_bytes"] if not dry_run else sum(s.size_bytes for s in stale),
        "skipped_in_use": len(in_use_dirs),
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
    }
