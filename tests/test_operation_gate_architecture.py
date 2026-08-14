# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_operation_gate_architecture import (
    APPROVED_BYPASS_CLASSES,
    OPERATION_NAME_FORWARDERS,
    BypassInventoryError,
    scan_paths,
)


def _violations(paths: list[Path], bypasses: dict[str, tuple[str, str]]) -> list[tuple[str, int]]:
    return [(item.function, item.line) for item in scan_paths(paths, bypasses=bypasses)]


def test_rejects_ungated_session_page_access(tmp_path: Path) -> None:
    source = tmp_path / "bad.py"
    source.write_text(
        "async def leak(session):\n    await session.page.locator('#secret').click()\n",
        encoding="utf-8",
    )
    violations = scan_paths([source], bypasses={})
    assert [(item.function, item.line) for item in violations] == [("leak", 2)]


def test_accepts_decorator_context_and_reasoned_bypass(tmp_path: Path) -> None:
    source = tmp_path / "good.py"
    source.write_text(
        "@gated_operation('browser_click')\n"
        "async def decorated(session):\n"
        "    await session.page.click('#x')\n"
        "async def contextual(session):\n"
        "    async with session.operation('browser_click'):\n"
        "        await session.page.click('#x')\n"
        "def cached(session):\n"
        "    return session.page_count\n",
        encoding="utf-8",
    )
    assert scan_paths([source], bypasses={}) == []


def test_detects_target_locator_chain_via_local_variables(tmp_path: Path) -> None:
    source = tmp_path / "mutation_target.py"
    source.write_text(
        "async def leak(session):\n"
        "    target = session._target()\n"
        "    locator = target.locator('#x')\n"
        "    await locator.click()\n",
        encoding="utf-8",
    )
    assert _violations([source], {}) == [("leak", 2), ("leak", 3), ("leak", 4)]


def test_detects_page_alias_assignment(tmp_path: Path) -> None:
    source = tmp_path / "mutation_page.py"
    source.write_text(
        "async def leak(session):\n    page = session.page\n    await page.title()\n",
        encoding="utf-8",
    )
    assert _violations([source], {}) == [("leak", 2), ("leak", 3)]


def test_detects_playwright_annotated_parameter(tmp_path: Path) -> None:
    source = tmp_path / "mutation_annotated.py"
    source.write_text(
        "from playwright.async_api import Page\n\nasync def leak(page: Page) -> None:\n    await page.title()\n",
        encoding="utf-8",
    )
    assert _violations([source], {}) == [("leak", 4)]


def test_does_not_confuse_starlette_annotations_for_playwright(tmp_path: Path) -> None:
    source = tmp_path / "starlette_like.py"
    source.write_text(
        "from starlette.requests import Request\n\n"
        "async def handler(request: Request) -> None:\n"
        "    await request.title()\n",
        encoding="utf-8",
    )
    assert _violations([source], {}) == []


def test_if_else_taint_uses_conservative_union_not_sequential_mutation(tmp_path: Path) -> None:
    """A name un-tainted inside the ``if`` branch (reassigned to a non-Playwright
    value) must not leak that discard into the ``else`` branch's walk, and must
    stay tainted after the if/else if EITHER branch would keep it that way at
    runtime -- only one branch ever actually executes. Sequential mutation of a
    single shared taint set (walking body then orelse on the SAME set) produces
    a false negative here: the body's reassignment discards the name before the
    (empty) orelse walk even starts, so the post-if access is wrongly cleared."""
    source = tmp_path / "if_else_union.py"
    source.write_text(
        "async def leak(session):\n"
        "    handle = session.page\n"
        "    if cond:\n"
        "        handle = 1\n"
        "    await handle.title()\n",
        encoding="utf-8",
    )
    assert _violations([source], {}) == [("leak", 2), ("leak", 5)]


def test_direct_access_before_valid_context_still_fails(tmp_path: Path) -> None:
    source = tmp_path / "mutation_before.py"
    source.write_text(
        "async def leak(session):\n"
        "    title = await session.page.title()\n"
        "    async with session.operation('browser_click'):\n"
        "        await session.page.click('#x')\n"
        "    return title\n",
        encoding="utf-8",
    )
    assert _violations([source], {}) == [("leak", 2)]


def test_dynamic_operation_name_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "mutation_dynamic.py"
    source.write_text(
        "async def leak(session, operation_name):\n"
        "    async with session.operation(operation_name):\n"
        "        await session.page.click('#x')\n",
        encoding="utf-8",
    )
    violations = scan_paths([source], bypasses={})
    assert ("leak", 2) in [(item.function, item.line) for item in violations]


def test_nested_callback_requires_its_own_classification(tmp_path: Path) -> None:
    source = tmp_path / "mutation_nested.py"
    source.write_text(
        "@gated_operation('browser_click')\n"
        "async def outer(session):\n"
        "    def _on_event() -> None:\n"
        "        session.page.title()\n"
        "    await session.page.click('#x')\n"
        "    return _on_event\n",
        encoding="utf-8",
    )
    violations = [(item.function, item.line) for item in scan_paths([source], bypasses={})]
    assert violations == [("outer._on_event", 4)]


def test_bypass_excuses_the_whole_function(tmp_path: Path) -> None:
    source = tmp_path / "bypassed.py"
    source.write_text(
        "async def teardown(session):\n    await session.page.close()\n",
        encoding="utf-8",
    )
    bypasses = {"bypassed.py:teardown": ("teardown-only", "runs only after close cutoff")}
    assert scan_paths([source], bypasses=bypasses) == []


@pytest.mark.parametrize(
    ("bypasses_factory", "match"),
    [
        (
            lambda: {"bad.py:leak": ("not-a-real-class", "some reason")},
            "class",
        ),
        (
            lambda: {"bad.py:leak": ("teardown-only", "")},
            "reason",
        ),
        (
            lambda: {"bad.py:missing_function": ("teardown-only", "some reason")},
            "not found",
        ),
    ],
)
def test_stale_bypass_entries_fail(tmp_path: Path, bypasses_factory, match: str) -> None:
    source = tmp_path / "bad.py"
    source.write_text(
        "async def leak(session):\n    await session.page.locator('#secret').click()\n",
        encoding="utf-8",
    )
    with pytest.raises(BypassInventoryError, match=match):
        scan_paths([source], bypasses=bypasses_factory())


def test_bypass_with_no_detected_access_fails_as_stale(tmp_path: Path) -> None:
    source = tmp_path / "clean.py"
    source.write_text(
        "async def cached(session):\n    return session.page_count\n",
        encoding="utf-8",
    )
    bypasses = {"clean.py:cached": ("cached-property-only", "no access here at all")}
    with pytest.raises(BypassInventoryError, match="no detected"):
        scan_paths([source], bypasses=bypasses)


def test_approved_bypass_classes_are_exactly_four() -> None:
    assert {
        "event-critical",
        "teardown-only",
        "cached-property-only",
        "launch-time-before-session-publication",
    } == APPROVED_BYPASS_CLASSES


def test_accepts_browser_operation_forwarder_boundary(tmp_path: Path) -> None:
    source = tmp_path / "server_helper.py"
    source.write_text(
        "async def browser_click(pool, instance_id):\n"
        "    async with browser_operation(pool, instance_id, 'browser_click') as session:\n"
        "        await session.page.click('#x')\n",
        encoding="utf-8",
    )
    assert scan_paths([source], bypasses={}) == []


def test_rejects_browser_operation_with_dynamic_third_argument(tmp_path: Path) -> None:
    source = tmp_path / "server_helper_dynamic.py"
    source.write_text(
        "async def browser_dispatch(pool, instance_id, operation_name):\n"
        "    async with browser_operation(pool, instance_id, operation_name) as session:\n"
        "        await session.page.click('#x')\n",
        encoding="utf-8",
    )
    violations = [(item.function, item.line) for item in scan_paths([source], bypasses={})]
    assert ("browser_dispatch", 2) in violations


def test_the_two_real_operation_name_forwarders_are_registered() -> None:
    assert set(OPERATION_NAME_FORWARDERS) == {
        "session/operation_gate.py:gated_operation._decorate._wrapped",
        "server/browser/_operation.py:browser_operation",
    }
    for reason in OPERATION_NAME_FORWARDERS.values():
        assert reason.strip()


def test_forwarder_allowlist_accepts_exactly_one_dynamic_forwarding_site(tmp_path: Path) -> None:
    source = tmp_path / "forwarder_good.py"
    source.write_text(
        "async def forward(session, operation_name):\n"
        "    async with session.operation(operation_name):\n"
        "        pass\n",
        encoding="utf-8",
    )
    forwarders = {"forwarder_good.py:forward": "forwards a validated literal name"}
    assert scan_paths([source], bypasses={}, forwarders=forwarders) == []


def test_forwarder_allowlist_rejects_a_third_undocumented_forwarder(tmp_path: Path) -> None:
    source = tmp_path / "forwarder_third.py"
    source.write_text(
        "async def blessed(session, operation_name):\n"
        "    async with session.operation(operation_name):\n"
        "        pass\n\n"
        "async def rogue(session, operation_name):\n"
        "    async with session.operation(operation_name):\n"
        "        pass\n",
        encoding="utf-8",
    )
    forwarders = {"forwarder_third.py:blessed": "forwards a validated literal name"}
    violations = [(item.function, item.line) for item in scan_paths([source], bypasses={}, forwarders=forwarders)]
    assert ("rogue", 6) in violations


def test_forwarder_entry_with_empty_reason_fails(tmp_path: Path) -> None:
    source = tmp_path / "forwarder_empty_reason.py"
    source.write_text(
        "async def forward(session, operation_name):\n"
        "    async with session.operation(operation_name):\n"
        "        pass\n",
        encoding="utf-8",
    )
    forwarders = {"forwarder_empty_reason.py:forward": ""}
    with pytest.raises(BypassInventoryError, match="reason"):
        scan_paths([source], bypasses={}, forwarders=forwarders)


def test_forwarder_entry_for_missing_function_fails(tmp_path: Path) -> None:
    source = tmp_path / "forwarder_missing.py"
    source.write_text("async def unrelated(session):\n    return None\n", encoding="utf-8")
    forwarders = {"forwarder_missing.py:forward": "forwards a validated literal name"}
    with pytest.raises(BypassInventoryError, match="not found"):
        scan_paths([source], bypasses={}, forwarders=forwarders)


def test_forwarder_entry_that_also_touches_playwright_fails(tmp_path: Path) -> None:
    source = tmp_path / "forwarder_dirty.py"
    source.write_text(
        "async def forward(session, operation_name):\n"
        "    async with session.operation(operation_name):\n"
        "        await session.page.click('#x')\n",
        encoding="utf-8",
    )
    forwarders = {"forwarder_dirty.py:forward": "forwards a validated literal name"}
    with pytest.raises(BypassInventoryError, match="Playwright access"):
        scan_paths([source], bypasses={}, forwarders=forwarders)


def test_close_operation_boundary_is_accepted(tmp_path: Path) -> None:
    source = tmp_path / "close_boundary.py"
    source.write_text(
        "async def teardown(session, gate, reservation):\n"
        "    async with gate.close_operation(reservation):\n"
        "        await session.page.close()\n",
        encoding="utf-8",
    )
    assert scan_paths([source], bypasses={}) == []


def test_ignores_type_checking_bodies_for_hit_scanning(tmp_path: Path) -> None:
    source = tmp_path / "type_checking.py"
    source.write_text(
        "from typing import TYPE_CHECKING\n\n"
        "async def leak(session):\n"
        "    if TYPE_CHECKING:\n"
        "        session.page.click('#x')\n",
        encoding="utf-8",
    )
    assert scan_paths([source], bypasses={}) == []


def test_ignores_protocol_method_declarations(tmp_path: Path) -> None:
    source = tmp_path / "protocol_like.py"
    source.write_text(
        "from typing import Protocol\n\n"
        "class SessionLike(Protocol):\n"
        "    async def click(self, session) -> None:\n"
        "        await session.page.click('#x')\n",
        encoding="utf-8",
    )
    assert scan_paths([source], bypasses={}) == []


def test_does_not_treat_arbitrary_async_context_managers_as_gates(tmp_path: Path) -> None:
    source = tmp_path / "arbitrary_cm.py"
    source.write_text(
        "async def leak(session):\n    async with session.some_lock():\n        await session.page.click('#x')\n",
        encoding="utf-8",
    )
    assert _violations([source], {}) == [("leak", 3)]


def test_scan_paths_is_deterministically_sorted(tmp_path: Path) -> None:
    first = tmp_path / "a_first.py"
    second = tmp_path / "z_second.py"
    first.write_text("async def leak(session):\n    await session.page.click('#x')\n", encoding="utf-8")
    second.write_text("async def leak(session):\n    await session.page.click('#x')\n", encoding="utf-8")
    violations = scan_paths([second, first], bypasses={})
    assert [v.path.name for v in violations] == ["a_first.py", "z_second.py"]
