# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""MCP tools for cached large payload analysis."""

from __future__ import annotations

import json
from typing import Any

from octowright import captures as _captures
from octowright.config_paths import user_config_dir
from octowright.defaults import CAPTURE_MAX_TOTAL_BYTES, CAPTURE_TTL_SECONDS, CAPTURES_DIR, RECORDINGS_DIR
from octowright.server._state import mcp, pool
from octowright.server.browser._operation import browser_operation
from octowright.server.profiles import annotate_next_actions_for_profile
from octowright.session._protocols import SessionLike


async def _capture_snapshot(session: SessionLike, _meta: dict[str, Any], _expression: str | None) -> str:
    # _target() so a snapshot capture descends into a switched frame, like browser_snapshot.
    async with session.operation("capture_create"):
        return await session._target().locator("body").aria_snapshot()


async def _capture_text(session: SessionLike, _meta: dict[str, Any], _expression: str | None) -> str:
    async with session.operation("capture_create"):
        return await session._target().locator("body").inner_text()


async def _capture_evaluate(session: SessionLike, meta: dict[str, Any], expression: str | None) -> str:
    if not expression:
        raise ValueError("expression is required when source='evaluate'")
    async with session.operation("capture_create"):
        value = await session.evaluate(expression)
    meta["expression"] = expression
    return value if isinstance(value, str) else json.dumps(value, default=str, indent=2)


async def _capture_console(session: SessionLike, _meta: dict[str, Any], _expression: str | None) -> str:
    return json.dumps(list(session.console), default=str, indent=2)


async def _capture_network(session: SessionLike, _meta: dict[str, Any], _expression: str | None) -> str:
    return json.dumps(session.get_network_requests(), default=str, indent=2)


async def _capture_markdown(session: SessionLike, meta: dict[str, Any], _expression: str | None) -> str:
    path = await session.capture_markdown()
    if path is None:
        raise RuntimeError("markdown capture did not produce a file")
    meta["path"] = str(path)
    return path.read_text(encoding="utf-8", errors="replace")


async def _capture_recording(session: SessionLike, meta: dict[str, Any], _expression: str | None) -> str:
    meta["path"] = str(session.log_path)
    return session.log_path.read_text(encoding="utf-8", errors="replace")


_CAPTURE_SOURCES = {
    "snapshot": _capture_snapshot,
    "text": _capture_text,
    "evaluate": _capture_evaluate,
    "console": _capture_console,
    "network": _capture_network,
    "markdown": _capture_markdown,
    "recording": _capture_recording,
}


def _capture_summary_next_actions(capture_id: str, summary_limit: int) -> list[dict[str, Any]]:
    return annotate_next_actions_for_profile(
        [
            {"tool": "capture_summary", "args": {"capture_id": capture_id, "limit": summary_limit}},
            {"tool": "capture_search", "args": {"capture_id": capture_id, "query": "<query>", "limit": 20}},
            {"tool": "capture_lines", "args": {"capture_id": capture_id, "start_line": 1, "limit": 80}},
            {
                "tool": "capture_get",
                "args": {"capture_id": capture_id, "offset": 0, "limit": _captures.DEFAULT_SLICE_CHARS},
            },
        ]
    )


def _annotate_capture_result_actions(result: dict[str, Any]) -> dict[str, Any]:
    if isinstance(result.get("next_actions"), list):
        result["next_actions"] = annotate_next_actions_for_profile(result["next_actions"])
    if isinstance(result.get("next_action"), dict):
        annotated = annotate_next_actions_for_profile([result["next_action"]])
        if annotated:
            result["next_action"] = annotated[0]

    for value in result.values():
        if isinstance(value, dict):
            _annotate_capture_result_actions(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _annotate_capture_result_actions(item)
    return result


async def _capture_content(session: SessionLike, source: str, expression: str | None) -> tuple[str, dict[str, Any]]:
    meta: dict[str, Any] = {"source": source}
    capture_source = _CAPTURE_SOURCES.get(source)
    if capture_source is None:
        raise ValueError(
            "unknown capture source; expected snapshot, text, evaluate, console, network, markdown, or recording"
        )
    content = await capture_source(session, meta, expression)
    return content, meta


@mcp.tool(
    structured_output=False,
    description=(
        "Create a cached full-fidelity payload for later analysis without dumping it all into context. "
        "source is one of: snapshot, text, evaluate, console, network, markdown, recording. "
        "Returns a compact preview plus capture_id. Pass response_mode='summary' to also return "
        "capture_summary inline; follow up with capture_search, capture_lines, or capture_get."
    ),
)
async def capture_create(
    instance_id: str,
    source: str = "snapshot",
    expression: str | None = None,
    preview_chars: int = _captures.CAPTURE_PREVIEW_CHARS,
    response_mode: str | None = None,
    summary_limit: int = 40,
) -> dict[str, Any]:
    async with browser_operation(pool, instance_id, "capture_create") as session:
        content, meta = await _capture_content(session, source, expression)
        result = _captures.save_capture(
            kind=source,
            content=content,
            # _target().url so the capture's url matches the frame the content came from.
            url=session._target().url,
            title=await session.page.title(),
            instance_id=instance_id,
            source=meta,
            preview_chars=preview_chars,
        )
        _annotate_capture_result_actions(result)
        if response_mode == "summary":
            capture_id = str(result["capture_id"])
            result["summary"] = _captures.summarize_capture(capture_id, limit=summary_limit)
            _annotate_capture_result_actions(result["summary"])
            result["actions"] = ["capture_summary", "capture_search", "capture_lines", "capture_get"]
            result["next_actions"] = _capture_summary_next_actions(capture_id, summary_limit)
        return result


@mcp.tool(
    structured_output=False,
    description="Read a bounded slice from a cached capture by capture_id. Use offset/limit for paging.",
)
def capture_get(capture_id: str, offset: int = 0, limit: int = _captures.DEFAULT_SLICE_CHARS) -> dict[str, Any]:
    return _annotate_capture_result_actions(_captures.get_capture_slice(capture_id, offset=offset, limit=limit))


@mcp.tool(
    structured_output=False,
    description=(
        "Read a bounded 1-based line range from a cached capture. "
        "Use after capture_summary when it gives useful line numbers."
    ),
)
def capture_lines(capture_id: str, start_line: int = 1, limit: int = 80) -> dict[str, Any]:
    return _annotate_capture_result_actions(_captures.get_capture_lines(capture_id, start_line=start_line, limit=limit))


@mcp.tool(
    structured_output=False,
    description=(
        "Search a cached capture without dumping the whole payload. Returns bounded match contexts. "
        "Use regex=True for regular expression search."
    ),
)
def capture_search(
    capture_id: str,
    query: str,
    regex: bool = False,
    context_chars: int = 500,
    limit: int = 20,
) -> dict[str, Any]:
    return _annotate_capture_result_actions(
        _captures.search_capture(
            capture_id,
            query,
            regex=regex,
            context_chars=context_chars,
            limit=limit,
        )
    )


@mcp.tool(
    structured_output=False,
    description=(
        "Return a compact structural outline for a cached capture without dumping the payload. "
        "Use before capture_get when you need to decide which section or line range to inspect."
    ),
)
def capture_summary(capture_id: str, limit: int = 40) -> dict[str, Any]:
    return _annotate_capture_result_actions(_captures.summarize_capture(capture_id, limit=limit))


@mcp.tool(
    structured_output=False,
    description="List cached captures, optionally filtered by instance_id or host.",
)
def capture_list(instance_id: str | None = None, host: str | None = None, limit: int = 50) -> dict[str, Any]:
    return _annotate_capture_result_actions(_captures.list_captures(instance_id=instance_id, host=host, limit=limit))


@mcp.tool(
    structured_output=False,
    description=(
        "Prune cached captures by age and total size. Dry-run by default; pass apply=True to delete. "
        "Defaults use OCTOWRIGHT_CAPTURE_TTL_SECONDS and OCTOWRIGHT_CAPTURE_MAX_TOTAL_BYTES."
    ),
)
def capture_cleanup(
    apply: bool = False,
    ttl_seconds: float = CAPTURE_TTL_SECONDS,
    max_total_bytes: int = CAPTURE_MAX_TOTAL_BYTES,
) -> dict[str, Any]:
    return _captures.cleanup_captures(apply=apply, ttl_seconds=ttl_seconds, max_total_bytes=max_total_bytes)


@mcp.tool(
    structured_output=False,
    description="Return the current Octowright file/directory storage breakdown for config, state, and cache paths.",
)
def octowright_storage_report() -> dict[str, Any]:
    return _captures.storage_report(
        recordings_dir=RECORDINGS_DIR,
        config_dir=user_config_dir(),
        captures_dir=CAPTURES_DIR,
    )
