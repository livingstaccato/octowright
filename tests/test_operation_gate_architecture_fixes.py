# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Contract tests for the Task 11 review fix round: cached-handle root attrs,
assignment-target embedded reads, lambda-as-independent-scope, with-as taint
propagation, and the ambiguous-name (request/response/websocket) suppression.

Split out of ``test_operation_gate_architecture.py`` (kept under the
repository's LOC-per-file convention).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import scripts.check_operation_gate_architecture as _check_module
from scripts.check_operation_gate_architecture import main, scan_paths


def _violations(paths: list[Path], bypasses: dict[str, tuple[str, str]]) -> list[tuple[str, int]]:
    return [(item.function, item.line) for item in scan_paths(paths, bypasses=bypasses)]


def test_detects_private_cached_video_handle_dereference(tmp_path: Path) -> None:
    source = tmp_path / "video_leak.py"
    source.write_text(
        "async def leak(session):\n    await session._video.path()\n",
        encoding="utf-8",
    )
    assert _violations([source], {}) == [("leak", 2)]


def test_detects_other_private_cached_handle_shapes(tmp_path: Path) -> None:
    # General contract: every cached-handle root attr, not just _video.
    source = tmp_path / "handle_leak.py"
    source.write_text(
        "class Manager:\n"
        "    async def leak_browser(self):\n"
        "        await self._browser_for_close.close()\n"
        "    async def leak_bound_page(self):\n"
        "        await self._bound_page.screenshot(path='x')\n",
        encoding="utf-8",
    )
    violations = [(item.function, item.line) for item in scan_paths([source], bypasses={})]
    assert ("Manager.leak_browser", 3) in violations
    assert ("Manager.leak_bound_page", 5) in violations


def test_detects_read_embedded_in_subscript_assignment_target(tmp_path: Path) -> None:
    source = tmp_path / "subscript_target.py"
    source.write_text(
        "async def leak(session, cache):\n    cache[session.page.url] = 1\n",
        encoding="utf-8",
    )
    assert _violations([source], {}) == [("leak", 2)]


def test_detects_read_embedded_in_augassign_target(tmp_path: Path) -> None:
    source = tmp_path / "augassign_target.py"
    source.write_text(
        "async def leak(page, totals):\n    totals[page.url] += 1\n",
        encoding="utf-8",
    )
    assert _violations([source], {}) == [("leak", 2)]


def test_detects_read_embedded_in_attribute_assignment_target(tmp_path: Path) -> None:
    source = tmp_path / "attribute_target.py"
    source.write_text(
        "async def leak(session, record):\n    record.last_url = session.page.url\n",
        encoding="utf-8",
    )
    violations = [(item.function, item.line) for item in scan_paths([source], bypasses={})]
    assert ("leak", 2) in violations


def test_lambda_inside_gate_requires_its_own_classification(tmp_path: Path) -> None:
    source = tmp_path / "lambda_in_gate.py"
    source.write_text(
        "@gated_operation('browser_click')\n"
        "async def outer(session):\n"
        "    session.page.on('dialog', lambda: session.page.click('#ok'))\n",
        encoding="utf-8",
    )
    violations = [(item.function, item.line) for item in scan_paths([source], bypasses={})]
    assert ("outer.<lambda>", 3) in violations


def test_lambda_parameter_seeding_uses_conventional_names(tmp_path: Path) -> None:
    source = tmp_path / "lambda_param.py"
    source.write_text(
        "async def outer(session):\n    session.page.on('framenavigated', lambda frame: frame.url)\n",
        encoding="utf-8",
    )
    violations = [(item.function, item.line) for item in scan_paths([source], bypasses={})]
    assert ("outer.<lambda>", 2) in violations


def test_with_as_binding_propagates_taint_from_tainted_context_expr(tmp_path: Path) -> None:
    source = tmp_path / "with_as_popup.py"
    source.write_text(
        "async def leak(page):\n"
        "    async with page.expect_popup() as info:\n"
        "        p = await info.value\n"
        "    await p.goto('https://example.com')\n",
        encoding="utf-8",
    )
    violations = [(item.function, item.line) for item in scan_paths([source], bypasses={})]
    assert ("leak", 3) in violations
    assert ("leak", 4) in violations


def test_with_as_binding_does_not_taint_from_an_untainted_context_expr(tmp_path: Path) -> None:
    # Matches the real production shape (server/web.py): the with-as fix
    # (#4) must not resurrect the name-based false positive the ambiguous-
    # name suppression (#5) already closed -- proving the two fixes compose.
    source = tmp_path / "with_as_untainted.py"
    source.write_text(
        "import httpx\n\n"
        "async def handler(client):\n"
        "    async with client.stream('GET', 'https://example.com') as response:\n"
        "        return str(response.url)\n",
        encoding="utf-8",
    )
    assert scan_paths([source], bypasses={}) == []


def test_with_as_binding_still_flagged_by_name_without_ambiguous_import(tmp_path: Path) -> None:
    # The context-expr propagation this fix adds is additive, not a
    # replacement for the pre-existing name-based fallback: a with-as bound
    # to a conventional name (in a file with no httpx/starlette evidence)
    # must still be caught, exactly as a plain assignment already was.
    source = tmp_path / "with_as_named.py"
    source.write_text(
        "async def handler(client):\n"
        "    async with client.stream('GET', 'https://example.com') as response:\n"
        "        return str(response.url)\n",
        encoding="utf-8",
    )
    assert _violations([source], {}) == [("handler", 3)]


def test_annotated_local_does_not_suppress_a_genuinely_tainted_value(tmp_path: Path) -> None:
    # Regression for a bug this fix round's own AnnAssign override introduced:
    # an explicit non-Playwright annotation must only beat the conventional-
    # NAME heuristic, never a genuinely tainted RHS value. Otherwise a
    # misleading local annotation (page: MyPage = session.page) silently
    # blinds every later use of the name.
    source = tmp_path / "annotated_tainted_value.py"
    source.write_text(
        "from some.other.module import MyPage\n\n"
        "async def leak(session):\n"
        "    page: MyPage = session.page\n"
        "    await page.click('#x')\n",
        encoding="utf-8",
    )
    violations = [(item.function, item.line) for item in scan_paths([source], bypasses={})]
    assert ("leak", 4) in violations
    assert ("leak", 5) in violations


def test_with_as_binding_does_not_clobber_preexisting_taint_on_the_same_name(tmp_path: Path) -> None:
    # Regression for a bug this fix round's own with-as propagation
    # introduced: an untainted context expression is not proof the bound
    # name isn't Playwright-shaped (an arbitrary __enter__ return type is
    # invisible to this scanner), so it must never ERASE taint a prior
    # statement already established on the same name.
    source = tmp_path / "with_as_clobber.py"
    source.write_text(
        "import contextlib\n\n"
        "async def leak(session):\n"
        "    handle = session._target()\n"
        "    with contextlib.suppress(Exception) as handle:\n"
        "        pass\n"
        "    await handle.click('#x')\n",
        encoding="utf-8",
    )
    violations = [(item.function, item.line) for item in scan_paths([source], bypasses={})]
    assert ("leak", 4) in violations
    assert ("leak", 7) in violations


def test_annotated_local_with_explicit_non_playwright_type_is_not_flagged(tmp_path: Path) -> None:
    source = tmp_path / "annotated_local.py"
    source.write_text(
        "import httpx\n\n"
        "async def handler(client):\n"
        "    response: httpx.Response = await client.get('https://example.com')\n"
        "    return str(response.url)\n",
        encoding="utf-8",
    )
    assert scan_paths([source], bypasses={}) == []


def test_unannotated_local_named_response_in_httpx_file_is_not_flagged(tmp_path: Path) -> None:
    source = tmp_path / "unannotated_response.py"
    source.write_text(
        "import httpx\n\n"
        "async def handler(client):\n"
        "    response = await client.get('https://example.com')\n"
        "    return str(response.url)\n",
        encoding="utf-8",
    )
    assert scan_paths([source], bypasses={}) == []


def test_unannotated_websocket_param_in_starlette_file_is_not_flagged(tmp_path: Path) -> None:
    source = tmp_path / "starlette_ws.py"
    source.write_text(
        "import starlette\n\nasync def handler(websocket):\n    await websocket.accept()\n    return websocket.url\n",
        encoding="utf-8",
    )
    assert scan_paths([source], bypasses={}) == []


def test_response_param_still_flagged_without_ambiguous_import(tmp_path: Path) -> None:
    source = tmp_path / "playwright_response.py"
    source.write_text(
        "async def handle_response(response):\n    return response.status\n",
        encoding="utf-8",
    )
    assert _violations([source], {}) == [("handle_response", 2)]


def test_websocket_param_still_flagged_without_ambiguous_import(tmp_path: Path) -> None:
    source = tmp_path / "playwright_ws.py"
    source.write_text(
        "async def handle_ws(websocket):\n    websocket.on('close', lambda: None)\n",
        encoding="utf-8",
    )
    assert _violations([source], {}) == [("handle_ws", 2)]


def test_terminal_named_ancestor_directory_does_not_exclude_whole_scan(tmp_path: Path) -> None:
    root = tmp_path / "terminal" / "octowright_checkout"
    (root / "session").mkdir(parents=True)
    source = root / "session" / "leak.py"
    source.write_text(
        "async def leak(session):\n    await session.page.click('#x')\n",
        encoding="utf-8",
    )
    violations = [(item.function, item.line) for item in scan_paths([root], bypasses={})]
    assert ("leak", 2) in violations


def test_terminal_subdirectory_under_scan_root_is_still_excluded(tmp_path: Path) -> None:
    excluded = tmp_path / "terminal"
    excluded.mkdir()
    (excluded / "leak.py").write_text(
        "async def leak(session):\n    await session.page.click('#x')\n",
        encoding="utf-8",
    )
    assert scan_paths([tmp_path], bypasses={}) == []


def test_main_refuses_to_report_ok_on_a_near_empty_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    near_empty = tmp_path / "near_empty_src"
    near_empty.mkdir()
    (near_empty / "only_file.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(_check_module, "SRC", near_empty)
    exit_code = main()
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "OK" not in captured.out
    assert "only" in captured.err or "scanned" in captured.err
