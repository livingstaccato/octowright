# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for macros.runtime — pinning the action dispatcher.

Each test names the mutmut survival pattern it catches: argument shape,
default value, skip rule, fallback chain, or method-name mapping.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.macros.runtime import (
    _ACTION_MAP,
    _REPLAY_SKIP,
    _dispatch_click_or_fill,
    _dispatch_standard,
    dispatch_one,
    dispatch_simple,
)
from octowright.macros.substitution import SEMANTIC_LOCATOR_KEYS, action_kwargs, strip_non_aria_noise

# --------------------------------------------------------------------------
# Helpers / fixtures
# --------------------------------------------------------------------------


def _full_session() -> MagicMock:
    """Session double with every method dispatch_simple may invoke."""
    s = MagicMock()
    for method in (
        "navigate",
        "click",
        "type_text",
        "fill",
        "press_key",
        "screenshot",
        "evaluate",
        "wait_for",
        "expect_url",
        "expect_text",
        "expect_selector",
        "expect_js",
        "mock_route",
        "unmock_route",
        "set_dialog_policy",
        "set_input_files",
        "click_by",
        "fill_by",
    ):
        setattr(s, method, AsyncMock())
    return s


def _dispatch_via_simple(session: Any, action: dict[str, Any]) -> Any:
    """Invoke dispatch_simple with the project's real helper plumbing."""
    return dispatch_simple(
        session,
        action,
        semantic_keys=SEMANTIC_LOCATOR_KEYS,
        strip_non_aria_noise=strip_non_aria_noise,
        action_kwargs=action_kwargs,
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --------------------------------------------------------------------------
# Constant tables
# --------------------------------------------------------------------------


class TestConstantTables:
    def test_replay_skip_contains_exact_three_lifecycle_actions(self) -> None:
        """_REPLAY_SKIP must hold exactly launch/close/snapshot — no more, no less."""
        assert {"launch", "close", "snapshot"} == _REPLAY_SKIP

    @pytest.mark.parametrize(
        ("kind", "method"),
        [
            ("navigate", "navigate"),
            ("click", "click"),
            ("type", "type_text"),
            ("fill", "fill"),
            ("press_key", "press_key"),
            ("screenshot", "screenshot"),
            ("evaluate", "evaluate"),
            ("wait_for", "wait_for"),
            ("expect_url", "expect_url"),
            ("expect_text", "expect_text"),
            ("expect_selector", "expect_selector"),
            ("expect_js", "expect_js"),
            ("mock_route", "mock_route"),
            ("unmock_route", "unmock_route"),
            ("set_dialog_policy", "set_dialog_policy"),
            ("set_input_files", "set_input_files"),
            ("click_by", "click_by"),
            ("fill_by", "fill_by"),
            ("hover", "hover"),
            ("select_option", "select_option"),
            ("drag", "drag"),
            ("navigate_back", "navigate_back"),
            ("resize", "resize"),
            ("open_url", "open_url"),
            ("switch_page", "switch_page"),
            ("close_page", "close_page"),
            ("reset_frame", "reset_frame"),
            ("switch_frame", "switch_frame"),
            ("get_text_by", "get_text_by"),
        ],
    )
    def test_action_map_pins_every_action_to_session_method(self, kind: str, method: str) -> None:
        """_ACTION_MAP must keep its kind→method-name binding stable."""
        assert _ACTION_MAP[kind] == method

    def test_action_map_size_is_exactly_30(self) -> None:
        """Adding/removing keys to _ACTION_MAP is a contract change — fail loudly.

        Went 27 -> 29 when switch_frame and get_text_by became replayable. Both
        were already recorded and classified nowhere, so every recording that
        used them tallied an error instead of replaying the action. 29 -> 30
        with set_extra_http_headers (page-level headers a run learns partway
        through, e.g. a token obtained by logging in).
        """
        assert len(_ACTION_MAP) == 30

    def test_type_kind_maps_to_type_text_not_type(self) -> None:
        """Pin the rename from 'type' kind → session.type_text method (not session.type)."""
        assert _ACTION_MAP["type"] == "type_text"
        assert _ACTION_MAP["type"] != "type"


# --------------------------------------------------------------------------
# dispatch_simple skip / map gating
# --------------------------------------------------------------------------


class TestDispatchSimpleSkipPaths:
    @pytest.mark.parametrize("kind", ["launch", "close", "snapshot"])
    @pytest.mark.anyio
    async def test_lifecycle_actions_return_skip_tuple(self, kind: str) -> None:
        """Each member of _REPLAY_SKIP returns (0, 1) and never touches the session."""
        s = _full_session()
        executed, skipped = await _dispatch_via_simple(s, {"action": kind, "url": "x"})
        assert (executed, skipped) == (0, 1)
        for method in ("navigate", "click", "click_by", "fill"):
            getattr(s, method).assert_not_called()

    @pytest.mark.anyio
    async def test_empty_action_kind_returns_skip(self) -> None:
        """Missing 'action' → '' → not in _ACTION_MAP → (0, 1)."""
        s = _full_session()
        assert await _dispatch_via_simple(s, {}) == (0, 1)

    @pytest.mark.anyio
    async def test_unknown_action_kind_returns_skip(self) -> None:
        """Unknown kind → not in _ACTION_MAP → (0, 1) without raising."""
        s = _full_session()
        result = await _dispatch_via_simple(s, {"action": "unknown_xyz"})
        assert result == (0, 1)

    @pytest.mark.anyio
    async def test_session_missing_method_returns_skip(self) -> None:
        """If kind maps to a method the session lacks, dispatch_simple returns (0, 1)."""
        # spec=["instance_id"] satisfies the SessionLike attr access used to
        # tag the macro.action span; the dispatcher still finds no `navigate`
        # method via hasattr and short-circuits to the skip tuple.
        s = MagicMock(spec=["instance_id"])
        result = await _dispatch_via_simple(s, {"action": "navigate", "url": "x"})
        assert result == (0, 1)

    @pytest.mark.anyio
    async def test_macro_call_in_simple_dispatch_raises(self) -> None:
        """`macro_call` needs the full _dispatch_one (with invocation_stack)
        in macros/execution.py. If it reaches the simple dispatcher (e.g.
        via a conditional's plain-action path), previously the runtime
        returned (0, 1) — visually identical to a legitimate skip, so the
        misroute was silent. Raise instead so the bug is loud."""
        s = _full_session()
        with pytest.raises(RuntimeError, match="macro_call requires _dispatch_one"):
            await _dispatch_via_simple(s, {"action": "macro_call", "name": "inner"})


# --------------------------------------------------------------------------
# _dispatch_standard kwargs mutation
# --------------------------------------------------------------------------


class TestDispatchStandardKwargs:
    @pytest.mark.anyio
    async def test_navigate_passes_url_kwarg(self) -> None:
        """navigate forwards url= as a kwarg."""
        s = _full_session()
        await _dispatch_via_simple(s, {"action": "navigate", "url": "https://octowright.com"})
        s.navigate.assert_awaited_once_with(url="https://octowright.com")

    @pytest.mark.anyio
    async def test_press_key_passes_key_kwarg(self) -> None:
        """press_key forwards key= as a kwarg."""
        s = _full_session()
        await _dispatch_via_simple(s, {"action": "press_key", "key": "Enter"})
        s.press_key.assert_awaited_once_with(key="Enter")

    @pytest.mark.anyio
    async def test_evaluate_passes_expression_kwarg(self) -> None:
        """evaluate forwards expression= as a kwarg."""
        s = _full_session()
        await _dispatch_via_simple(s, {"action": "evaluate", "expression": "1+1"})
        s.evaluate.assert_awaited_once_with(expression="1+1")

    @pytest.mark.anyio
    async def test_expect_url_passes_pattern_kwarg(self) -> None:
        """expect_url forwards pattern= as a kwarg."""
        s = _full_session()
        await _dispatch_via_simple(s, {"action": "expect_url", "pattern": "^/home"})
        s.expect_url.assert_awaited_once_with(pattern="^/home")

    @pytest.mark.anyio
    async def test_expect_text_passes_selector_and_text(self) -> None:
        """expect_text forwards selector= and text= kwargs."""
        s = _full_session()
        await _dispatch_via_simple(
            s,
            {"action": "expect_text", "selector": "#status", "text": "Ready"},
        )
        s.expect_text.assert_awaited_once_with(selector="#status", text="Ready")

    @pytest.mark.anyio
    async def test_set_input_files_forwards_paths_list(self) -> None:
        """set_input_files preserves the list-of-paths argument shape."""
        s = _full_session()
        await _dispatch_via_simple(
            s,
            {"action": "set_input_files", "selector": "#file", "paths": ["/a.txt", "/b.txt"]},
        )
        s.set_input_files.assert_awaited_once_with(selector="#file", paths=["/a.txt", "/b.txt"])

    @pytest.mark.anyio
    async def test_recording_noise_keys_are_stripped(self) -> None:
        """ts/kind/profile/instance_id/action are not passed to the session method."""
        s = _full_session()
        await _dispatch_via_simple(
            s,
            {
                "action": "navigate",
                "ts": "2026-01-01T00:00:00Z",
                "kind": "chromium",
                "profile": "cosmo",
                "instance_id": "deadbeef",
                "url": "https://octowright.com",
            },
        )
        s.navigate.assert_awaited_once_with(url="https://octowright.com")


# --------------------------------------------------------------------------
# `type` action delay_ms default
# --------------------------------------------------------------------------


class TestTypeDelayDefault:
    @pytest.mark.anyio
    async def test_type_without_delay_ms_injects_zero(self) -> None:
        """type without delay_ms gets delay_ms=0 (not delay_ms=None and not absent)."""
        s = _full_session()
        await _dispatch_via_simple(s, {"action": "type", "selector": "#x", "text": "abc"})
        s.type_text.assert_awaited_once_with(selector="#x", text="abc", delay_ms=0)

    @pytest.mark.anyio
    async def test_type_with_explicit_delay_ms_preserves_value(self) -> None:
        """An explicit delay_ms passes through unchanged."""
        s = _full_session()
        await _dispatch_via_simple(
            s,
            {"action": "type", "selector": "#x", "text": "abc", "delay_ms": 50},
        )
        s.type_text.assert_awaited_once_with(selector="#x", text="abc", delay_ms=50)

    @pytest.mark.anyio
    async def test_type_with_explicit_delay_ms_zero_preserves_zero(self) -> None:
        """delay_ms=0 explicit must not be re-injected as a different value."""
        s = _full_session()
        await _dispatch_via_simple(
            s,
            {"action": "type", "selector": "#x", "text": "abc", "delay_ms": 0},
        )
        s.type_text.assert_awaited_once_with(selector="#x", text="abc", delay_ms=0)


# --------------------------------------------------------------------------
# wait_for default-fill behavior
# --------------------------------------------------------------------------


class TestWaitForDefaults:
    @pytest.mark.anyio
    async def test_wait_for_no_args_fills_three_none_defaults(self) -> None:
        """wait_for with empty action gets selector/text/timeout_ms = None."""
        s = _full_session()
        await _dispatch_via_simple(s, {"action": "wait_for"})
        s.wait_for.assert_awaited_once_with(selector=None, text=None, timeout_ms=None)

    @pytest.mark.anyio
    async def test_wait_for_partial_args_only_fills_missing_defaults(self) -> None:
        """A provided field is preserved; only absent ones get None."""
        s = _full_session()
        await _dispatch_via_simple(s, {"action": "wait_for", "selector": "#x"})
        s.wait_for.assert_awaited_once_with(selector="#x", text=None, timeout_ms=None)

    @pytest.mark.anyio
    async def test_wait_for_all_args_overrides_no_default(self) -> None:
        """Every wait_for field present → no overwrite."""
        s = _full_session()
        await _dispatch_via_simple(
            s,
            {"action": "wait_for", "selector": "#x", "text": "Ready", "timeout_ms": 5000},
        )
        s.wait_for.assert_awaited_once_with(selector="#x", text="Ready", timeout_ms=5000)


# --------------------------------------------------------------------------
# Screenshot path handling
# --------------------------------------------------------------------------


class TestScreenshotPathHandling:
    @pytest.mark.anyio
    async def test_screenshot_without_path_returns_skip(self) -> None:
        """No path → (0, 1) and session.screenshot NOT called."""
        s = _full_session()
        result = await _dispatch_via_simple(s, {"action": "screenshot"})
        assert result == (0, 1)
        s.screenshot.assert_not_called()

    @pytest.mark.anyio
    async def test_screenshot_empty_string_path_returns_skip(self) -> None:
        """Falsy path string ('') → (0, 1) — pin the truthiness check."""
        s = _full_session()
        result = await _dispatch_via_simple(s, {"action": "screenshot", "path": ""})
        assert result == (0, 1)
        s.screenshot.assert_not_called()

    @pytest.mark.anyio
    async def test_screenshot_path_is_passed_as_pathlib_positional(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """session.screenshot receives Path() positional, not the raw string."""
        from octowright import defaults

        recordings = tmp_path / "recordings"
        recordings.mkdir(parents=True, exist_ok=True)
        target = recordings / "x.png"
        monkeypatch.setattr(defaults, "RECORDINGS_DIR", recordings)

        s = _full_session()
        await _dispatch_via_simple(s, {"action": "screenshot", "path": str(target)})
        s.screenshot.assert_awaited_once_with(target)

    @pytest.mark.anyio
    async def test_screenshot_path_outside_recordings_is_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Replay screenshot paths must stay under RECORDINGS_DIR."""
        from octowright import defaults

        s = _full_session()
        monkeypatch.setattr(defaults, "RECORDINGS_DIR", tmp_path / "recordings")
        with pytest.raises(ValueError, match="screenshot path"):
            await _dispatch_via_simple(s, {"action": "screenshot", "path": str(tmp_path / "outside.png")})
        s.screenshot.assert_not_called()


# --------------------------------------------------------------------------
# _dispatch_click_or_fill semantic-vs-fallback chain
# --------------------------------------------------------------------------


class TestSemanticFirst:
    @pytest.mark.anyio
    async def test_click_with_semantic_keys_uses_click_by(self) -> None:
        """Click with role/role_name → click_by called, fallback NOT called."""
        s = _full_session()
        await _dispatch_via_simple(
            s,
            {"action": "click", "selector": "#fragile", "role": "button", "role_name": "Submit"},
        )
        s.click_by.assert_awaited_once_with(role="button", role_name="Submit")
        s.click.assert_not_called()

    @pytest.mark.anyio
    async def test_click_without_semantic_keys_falls_through_to_click(self) -> None:
        """No semantic kwargs → semantic call skipped, fallback click runs."""
        s = _full_session()
        await _dispatch_via_simple(s, {"action": "click", "selector": "#only"})
        s.click_by.assert_not_called()
        s.click.assert_awaited_once_with(selector="#only")

    @pytest.mark.anyio
    async def test_click_semantic_failure_falls_back_when_selector_present(self) -> None:
        """Semantic raises and selector exists → fallback click runs, no re-raise."""
        s = _full_session()
        s.click_by.side_effect = RuntimeError("not found")
        await _dispatch_via_simple(
            s,
            {"action": "click", "selector": "#fallback", "role": "button"},
        )
        s.click_by.assert_awaited_once()
        s.click.assert_awaited_once_with(selector="#fallback")

    @pytest.mark.anyio
    async def test_click_semantic_failure_reraises_when_no_selector(self) -> None:
        """Semantic raises and selector missing → exception re-raised."""
        s = _full_session()
        s.click_by.side_effect = RuntimeError("not found")
        with pytest.raises(RuntimeError, match="not found"):
            await _dispatch_via_simple(s, {"action": "click", "role": "button"})
        s.click.assert_not_called()


class TestSemanticFillBehavior:
    @pytest.mark.anyio
    async def test_fill_with_semantic_keys_uses_fill_by_with_value(self) -> None:
        """Fill with role + value → fill_by called with both; fallback NOT called."""
        s = _full_session()
        await _dispatch_via_simple(
            s,
            {
                "action": "fill",
                "selector": "#email",
                "label": "Email",
                "value": "me@octowright.test",
            },
        )
        s.fill_by.assert_awaited_once_with(label="Email", value="me@octowright.test")
        s.fill.assert_not_called()

    @pytest.mark.anyio
    async def test_fill_without_semantic_keys_still_attempts_fill_by_with_value(self) -> None:
        """is_fill always injects value into semantic_kwargs — fill_by is tried even
        without role/label. Fallback only runs if fill_by raises."""
        s = _full_session()
        await _dispatch_via_simple(
            s,
            {"action": "fill", "selector": "#x", "value": "v"},
        )
        s.fill_by.assert_awaited_once_with(value="v")
        s.fill.assert_not_called()

    @pytest.mark.anyio
    async def test_fill_without_semantic_keys_or_value_falls_through_to_fill(self) -> None:
        """With no value either, semantic_kwargs stays empty and fallback fill runs."""
        s = _full_session()
        await _dispatch_via_simple(
            s,
            {"action": "fill", "selector": "#x"},
        )
        s.fill_by.assert_not_called()
        s.fill.assert_awaited_once_with(selector="#x")

    @pytest.mark.anyio
    async def test_fill_by_uses_fill_by_directly(self) -> None:
        """The fill_by kind goes through the same semantic path."""
        s = _full_session()
        await _dispatch_via_simple(
            s,
            {"action": "fill_by", "label": "Email", "value": "me@octowright.test"},
        )
        s.fill_by.assert_awaited_once_with(label="Email", value="me@octowright.test")

    @pytest.mark.anyio
    async def test_click_by_uses_click_by_directly(self) -> None:
        """The click_by kind goes through the same semantic path."""
        s = _full_session()
        await _dispatch_via_simple(
            s,
            {"action": "click_by", "role": "button", "role_name": "Submit"},
        )
        s.click_by.assert_awaited_once_with(role="button", role_name="Submit")


class TestSemanticTimeoutPropagation:
    @pytest.mark.anyio
    async def test_timeout_ms_in_kwargs_added_to_semantic_kwargs(self) -> None:
        """timeout_ms always travels with the semantic call when present."""
        s = _full_session()
        await _dispatch_via_simple(
            s,
            {"action": "click", "role": "button", "timeout_ms": 1000},
        )
        s.click_by.assert_awaited_once_with(role="button", timeout_ms=1000)

    @pytest.mark.anyio
    async def test_fallback_kwargs_strip_timeout_ms_and_semantic_keys(self) -> None:
        """Fallback fill receives only selector/value — no role/label/timeout_ms."""
        s = _full_session()
        s.fill_by.side_effect = RuntimeError("nope")
        await _dispatch_via_simple(
            s,
            {
                "action": "fill",
                "selector": "#email",
                "label": "Email",
                "role": "textbox",
                "value": "x",
                "timeout_ms": 1000,
            },
        )
        s.fill.assert_awaited_once_with(selector="#email", value="x")


# --------------------------------------------------------------------------
# _dispatch_click_or_fill — direct unit tests
# --------------------------------------------------------------------------


class TestDispatchClickOrFillDirect:
    @pytest.mark.anyio
    async def test_no_semantic_method_falls_through_to_click_stripping_semantic_keys(self) -> None:
        """When session has no click_by attr, fallback runs and semantic_keys are stripped."""
        s = MagicMock(spec=["click", "fill"])
        s.click = AsyncMock()
        s.fill = AsyncMock()
        result = await _dispatch_click_or_fill(
            s,
            "click",
            {"selector": "#only", "role": "button"},
            SEMANTIC_LOCATOR_KEYS,
        )
        assert result == (1, 0)
        # role is in SEMANTIC_LOCATOR_KEYS so it gets stripped on fallback.
        s.click.assert_awaited_once_with(selector="#only")

    @pytest.mark.anyio
    async def test_no_semantic_method_for_fill_strips_semantic_keys(self) -> None:
        """Same fall-through + strip for fill when fill_by attr is missing."""
        s = MagicMock(spec=["fill", "click"])
        s.fill = AsyncMock()
        s.click = AsyncMock()
        result = await _dispatch_click_or_fill(
            s,
            "fill",
            {"selector": "#x", "value": "v", "label": "Foo"},
            SEMANTIC_LOCATOR_KEYS,
        )
        assert result == (1, 0)
        # 'label' is in SEMANTIC_LOCATOR_KEYS — stripped on fallback.
        s.fill.assert_awaited_once_with(selector="#x", value="v")

    @pytest.mark.anyio
    async def test_returns_one_zero_on_semantic_success(self) -> None:
        """Successful semantic dispatch returns (1, 0)."""
        s = _full_session()
        result = await _dispatch_click_or_fill(
            s,
            "click",
            {"role": "button"},
            SEMANTIC_LOCATOR_KEYS,
        )
        assert result == (1, 0)

    @pytest.mark.anyio
    async def test_returns_one_zero_on_fallback_success(self) -> None:
        """Successful fallback dispatch (no semantic kwargs) returns (1, 0)."""
        s = _full_session()
        result = await _dispatch_click_or_fill(
            s,
            "click",
            {"selector": "#x"},
            SEMANTIC_LOCATOR_KEYS,
        )
        assert result == (1, 0)


# --------------------------------------------------------------------------
# _dispatch_standard — direct unit tests
# --------------------------------------------------------------------------


class TestDispatchStandardDirect:
    @pytest.mark.anyio
    async def test_returns_one_zero_on_success(self) -> None:
        """Successful standard dispatch returns (1, 0)."""
        s = _full_session()
        result = await _dispatch_standard(s, "navigate", {"url": "x"}, "navigate")
        assert result == (1, 0)
        s.navigate.assert_awaited_once_with(url="x")

    @pytest.mark.anyio
    async def test_screenshot_skip_returns_zero_one(self) -> None:
        """No-path screenshot returns (0, 1) — pin the early-exit tuple shape."""
        s = _full_session()
        result = await _dispatch_standard(s, "screenshot", {}, "screenshot")
        assert result == (0, 1)


# --------------------------------------------------------------------------
# dispatch_one routing
# --------------------------------------------------------------------------


class TestDispatchOneRouting:
    @pytest.mark.parametrize("kind", ["if_selector", "try", "try_each"])
    @pytest.mark.anyio
    async def test_conditional_actions_route_to_dispatch_conditional(
        self, kind: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each CONDITIONAL_ACTIONS member is routed to dispatch_conditional, not dispatch_simple."""
        observed: dict[str, Any] = {}

        async def _fake_dispatch_conditional(session: Any, action: dict[str, Any], dispatch: Any) -> tuple[int, int]:
            observed["called"] = True
            observed["kind"] = action["action"]
            return (7, 8)

        import octowright.conditional as _cond

        monkeypatch.setattr(_cond, "dispatch_conditional", _fake_dispatch_conditional)
        s = _full_session()
        result = await dispatch_one(
            s,
            {"action": kind, "selector": "#x", "then": [], "else": [], "actions": [], "branches": []},
            semantic_keys=SEMANTIC_LOCATOR_KEYS,
            strip_non_aria_noise=strip_non_aria_noise,
            action_kwargs=action_kwargs,
        )
        assert result == (7, 8)
        assert observed["called"] is True
        assert observed["kind"] == kind

    @pytest.mark.anyio
    async def test_non_conditional_action_routes_to_dispatch_simple(self) -> None:
        """Plain action goes through dispatch_simple, calls session method directly."""
        s = _full_session()
        result = await dispatch_one(
            s,
            {"action": "navigate", "url": "x"},
            semantic_keys=SEMANTIC_LOCATOR_KEYS,
            strip_non_aria_noise=strip_non_aria_noise,
            action_kwargs=action_kwargs,
        )
        assert result == (1, 0)
        s.navigate.assert_awaited_once_with(url="x")


# --------------------------------------------------------------------------
# Newly-replayable actions (previously recorded but absent from _ACTION_MAP)
# --------------------------------------------------------------------------


class TestNewlyReplayableActions:
    @pytest.mark.anyio
    async def test_hover_dispatches_to_session_hover_with_selector(self) -> None:
        s = MagicMock()
        s.hover = AsyncMock()
        result = await _dispatch_via_simple(s, {"action": "hover", "selector": "#btn"})
        assert result == (1, 0)
        s.hover.assert_awaited_once_with(selector="#btn")

    @pytest.mark.anyio
    async def test_select_option_passes_through_kwargs(self) -> None:
        s = MagicMock()
        s.select_option = AsyncMock()
        result = await _dispatch_via_simple(
            s,
            {"action": "select_option", "selector": "#sel", "value": "v1"},
        )
        assert result == (1, 0)
        s.select_option.assert_awaited_once_with(selector="#sel", value="v1")

    @pytest.mark.anyio
    async def test_drag_renames_source_and_target_to_method_signature(self) -> None:
        """Recorder writes ``source``/``target``; method takes ``source_selector``/``target_selector``."""
        s = MagicMock()
        s.drag = AsyncMock()
        result = await _dispatch_via_simple(
            s,
            {"action": "drag", "source": "#a", "target": "#b"},
        )
        assert result == (1, 0)
        s.drag.assert_awaited_once_with(source_selector="#a", target_selector="#b")

    @pytest.mark.anyio
    async def test_mock_route_renames_pattern_to_url_pattern(self) -> None:
        """Recorder writes ``pattern`` (session/core_interaction_mixin.py); the
        session method's parameter is ``url_pattern`` — without the rename this
        replay raises TypeError: unexpected keyword argument 'pattern'."""
        s = MagicMock()
        s.mock_route = AsyncMock()
        result = await _dispatch_via_simple(
            s,
            {
                "action": "mock_route",
                "pattern": "**/api/checkout",
                "status": 200,
                "content_type": "application/json",
                "body": '{"ok": true}',
                "headers": {},
            },
        )
        assert result == (1, 0)
        s.mock_route.assert_awaited_once_with(
            url_pattern="**/api/checkout",
            status=200,
            content_type="application/json",
            body='{"ok": true}',
            headers={},
        )

    @pytest.mark.anyio
    async def test_unmock_route_renames_pattern_to_url_pattern(self) -> None:
        """Recorder writes ``pattern``; the session method takes ``url_pattern``."""
        s = MagicMock()
        s.unmock_route = AsyncMock()
        result = await _dispatch_via_simple(s, {"action": "unmock_route", "pattern": "**/api/checkout"})
        assert result == (1, 0)
        s.unmock_route.assert_awaited_once_with(url_pattern="**/api/checkout")

    @pytest.mark.anyio
    async def test_navigate_back_drops_recorded_url_field(self) -> None:
        """``navigate_back`` records the resulting URL but the method takes no args."""
        s = MagicMock()
        s.navigate_back = AsyncMock()
        result = await _dispatch_via_simple(
            s,
            {"action": "navigate_back", "url": "https://prev.example/"},
        )
        assert result == (1, 0)
        s.navigate_back.assert_awaited_once_with()

    @pytest.mark.anyio
    async def test_resize_dispatches_with_width_and_height(self) -> None:
        s = MagicMock()
        s.resize = AsyncMock()
        result = await _dispatch_via_simple(
            s,
            {"action": "resize", "width": 1280, "height": 720},
        )
        assert result == (1, 0)
        s.resize.assert_awaited_once_with(width=1280, height=720)

    @pytest.mark.anyio
    async def test_open_url_drops_recorded_page_index(self) -> None:
        """open_url records the resulting page_index alongside the inputs, but
        the method signature is ``open_url(url, target='tab', width=1024,
        height=768)`` — page_index must be stripped before dispatch or replay
        raises TypeError. Use a real async function (not AsyncMock) so wrong
        kwargs surface as TypeError rather than silently passing through.
        """
        captured: dict[str, Any] = {}

        async def real_open_url(url: str, target: str = "tab", width: int = 1024, height: int = 768) -> None:
            captured.update({"url": url, "target": target, "width": width, "height": height})

        s = MagicMock()
        s.open_url = real_open_url
        result = await _dispatch_via_simple(
            s,
            {"action": "open_url", "url": "https://x.example/", "target": "tab", "page_index": 1},
        )
        assert result == (1, 0)
        assert captured == {
            "url": "https://x.example/",
            "target": "tab",
            "width": 1024,
            "height": 768,
        }

    @pytest.mark.anyio
    async def test_switch_page_drops_recorded_url(self) -> None:
        s = MagicMock()
        s.switch_page = AsyncMock()
        result = await _dispatch_via_simple(
            s,
            {"action": "switch_page", "index": 2, "url": "https://second/"},
        )
        assert result == (1, 0)
        s.switch_page.assert_awaited_once_with(index=2)

    @pytest.mark.anyio
    async def test_close_page_drops_recorded_was_active(self) -> None:
        s = MagicMock()
        s.close_page = AsyncMock()
        result = await _dispatch_via_simple(
            s,
            {"action": "close_page", "index": 0, "was_active": True},
        )
        assert result == (1, 0)
        s.close_page.assert_awaited_once_with(index=0)

    @pytest.mark.anyio
    async def test_reset_frame_dispatches_with_no_kwargs(self) -> None:
        s = MagicMock()
        s.reset_frame = AsyncMock()
        result = await _dispatch_via_simple(s, {"action": "reset_frame"})
        assert result == (1, 0)
        s.reset_frame.assert_awaited_once_with()


class TestPassiveEventsAndUnknownActions:
    @pytest.mark.anyio
    async def test_passive_console_event_skips_without_counting_error(self) -> None:
        """Page-emitted events must be skipped silently (0, 0), not counted as errors."""
        s = MagicMock()
        result = await _dispatch_via_simple(
            s,
            {"action": "console", "text": "page log line"},
        )
        assert result == (0, 0)

    @pytest.mark.anyio
    async def test_unknown_action_kind_logs_warning_and_counts_error(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Truly unknown kinds (not skip, not passive, not in map) now log a warning."""
        s = MagicMock()
        with caplog.at_level("WARNING"):
            result = await _dispatch_via_simple(s, {"action": "totally_made_up"})
        assert result == (0, 1)
        assert any("unknown_action_kind" in r.message for r in caplog.records)
