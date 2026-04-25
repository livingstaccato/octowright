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
