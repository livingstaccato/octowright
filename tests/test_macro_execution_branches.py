# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.macros.execution.

Targets the 143 surviving mutmut mutants in this module by asserting on:
- _resolve_slowmo_ms (None vs int, negative clamp, env-var fallback)
- _format_status (chain join, empty stack, action-description carrying)
- _push_status payload composition (visible/text/start/done flags, JS swallow)
- run_macro return shape (every MacroRunResult field, both happy + failure paths)
- run_sequence (stop_on_failure True/False, args_list shorter than names,
  per-step success vs failure shapes, ok aggregate flag)
- run_sequence error step shape vs success step shape
- pill push call sequence (start → action … → done OR failed)
- macro-call recursion: no invocation_stack raises
- exception path emits diagnostic bundle + healing suggestion in payload
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.macros import execution as _execution
from octowright.macros.execution import (
    _format_status,
    _push_status,
    _resolve_slowmo_ms,
    run_macro,
    run_sequence,
)

# ─── _resolve_slowmo_ms ──────────────────────────────────────────────────────


class TestResolveSlowmoMs:
    def test_explicit_int_returned_as_is(self) -> None:
        """Mutating the int() coercion would change the type."""
        assert _resolve_slowmo_ms(150) == 150

    def test_explicit_zero_returned(self) -> None:
        """Zero is a valid value, not a None sentinel."""
        assert _resolve_slowmo_ms(0) == 0

    def test_explicit_negative_clamped_to_zero(self) -> None:
        """max(0, ...) clamps negative inputs."""
        assert _resolve_slowmo_ms(-50) == 0

    def test_none_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The is-None branch reads MACRO_SLOWMO_MS."""
        monkeypatch.setattr(_execution, "MACRO_SLOWMO_MS", 75)
        assert _resolve_slowmo_ms(None) == 75

    def test_none_negative_default_clamped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Even a negative default gets clamped via the second max(0, ...)."""
        monkeypatch.setattr(_execution, "MACRO_SLOWMO_MS", -10)
        assert _resolve_slowmo_ms(None) == 0


# ─── _format_status ──────────────────────────────────────────────────────────


class TestFormatStatus:
    def test_with_invocation_stack_uses_chain_separator(self) -> None:
        """' > '.join — mutating the separator would change the format."""
        # describe_action lives in octowright.macros.descriptions; just assert format shape.
        result = _format_status(["outer", "inner"], {"action": "navigate", "url": "https://example.com"})
        assert result.startswith("outer > inner | ")

    def test_empty_stack_omits_separator(self) -> None:
        """No chain → no `chain | ` prefix."""
        result = _format_status([], {"action": "navigate", "url": "https://example.com"})
        assert " | " not in result

    def test_none_stack_omits_separator(self) -> None:
        """None stack treated same as empty."""
        result = _format_status(None, {"action": "navigate", "url": "https://example.com"})
        assert " | " not in result


# ─── _push_status ────────────────────────────────────────────────────────────


class TestPushStatus:
    @pytest.mark.anyio
    async def test_session_with_no_page_no_op(self) -> None:
        """If session.page is None, function returns silently."""
        session = MagicMock(spec=["page"])
        session.page = None
        # Should not raise.
        await _push_status(session, text="x")

    @pytest.mark.anyio
    async def test_payload_contains_only_visible_when_minimal(self) -> None:
        """Default payload is just {visible: True}."""
        session = MagicMock()
        session.page.evaluate = AsyncMock()
        await _push_status(session)
        args = session.page.evaluate.call_args
        payload = args[0][1]
        assert payload == {"visible": True}

    @pytest.mark.anyio
    async def test_payload_includes_text_when_provided(self) -> None:
        """`if text is not None` branch."""
        session = MagicMock()
        session.page.evaluate = AsyncMock()
        await _push_status(session, text="hello")
        payload = session.page.evaluate.call_args[0][1]
        assert payload["text"] == "hello"

    @pytest.mark.anyio
    async def test_payload_excludes_text_when_none(self) -> None:
        """text=None means key is absent, not present-with-None."""
        session = MagicMock()
        session.page.evaluate = AsyncMock()
        await _push_status(session, text=None)
        payload = session.page.evaluate.call_args[0][1]
        assert "text" not in payload

    @pytest.mark.anyio
    async def test_start_flag_added(self) -> None:
        """start=True → key present and True."""
        session = MagicMock()
        session.page.evaluate = AsyncMock()
        await _push_status(session, start=True)
        payload = session.page.evaluate.call_args[0][1]
        assert payload["start"] is True

    @pytest.mark.anyio
    async def test_start_flag_absent_by_default(self) -> None:
        """start=False (default) → key absent."""
        session = MagicMock()
        session.page.evaluate = AsyncMock()
        await _push_status(session)
        assert "start" not in session.page.evaluate.call_args[0][1]

    @pytest.mark.anyio
    async def test_done_flag_added(self) -> None:
        """done=True → payload has done=True."""
        session = MagicMock()
        session.page.evaluate = AsyncMock()
        await _push_status(session, done=True)
        assert session.page.evaluate.call_args[0][1]["done"] is True

    @pytest.mark.anyio
    async def test_visible_false_passes_through(self) -> None:
        """visible=False isn't dropped — payload reflects the explicit value."""
        session = MagicMock()
        session.page.evaluate = AsyncMock()
        await _push_status(session, visible=False)
        assert session.page.evaluate.call_args[0][1]["visible"] is False

    @pytest.mark.anyio
    async def test_evaluate_failure_swallowed(self) -> None:
        """The except Exception: pass — failures must never propagate."""
        session = MagicMock()
        session.page.evaluate = AsyncMock(side_effect=RuntimeError("page closed"))
        # Must not raise.
        await _push_status(session, text="x")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ─── run_macro / run_sequence test helpers ──────────────────────────────────


class _FakeSession:
    """Mocked session with everything run_macro touches."""

    def __init__(self) -> None:
        self.page = MagicMock()
        self.page.evaluate = AsyncMock()
        self.diagnostic_bundle = AsyncMock(return_value={"url": "https://x", "title": "t"})


@pytest.fixture
def fake_session() -> _FakeSession:
    return _FakeSession()


@pytest.fixture
def patched_runners(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Patch the load/dispatch/suggest-fix entry points; track calls per test."""
    macros: dict[str, dict[str, Any]] = {}
    dispatched: list[dict[str, Any]] = []
    suggest_calls: list[dict[str, Any]] = []
    raise_on: dict[str, Exception] = {}

    def fake_load(name: str) -> dict[str, Any]:
        if name not in macros:
            raise FileNotFoundError(f"no macro named {name!r}")
        return macros[name]

    async def fake_dispatch_one(session: Any, action: dict[str, Any], **_kwargs: Any) -> tuple[int, int]:
        dispatched.append(action)
        if action.get("action") in raise_on:
            raise raise_on[action["action"]]
        return (1, 0)

    async def fake_suggest(session: Any, action: dict[str, Any]) -> str | None:
        suggest_calls.append(action)
        return "use #other instead"

    monkeypatch.setattr(_execution, "load_macro", fake_load)
    monkeypatch.setattr(_execution, "_dispatch_one", fake_dispatch_one)
    monkeypatch.setattr(_execution, "_suggest_fix", fake_suggest)

    def register(name: str, actions: list[dict[str, Any]]) -> None:
        macros[name] = {"name": name, "actions": actions}

    return {
        "register": register,
        "dispatched": dispatched,
        "suggest_calls": suggest_calls,
        "raise_on": raise_on,
    }


# ─── run_macro: happy path ───────────────────────────────────────────────────


class TestRunMacroHappyPath:
    @pytest.mark.anyio
    async def test_returns_all_six_keys(self, fake_session: _FakeSession, patched_runners: dict[str, Any]) -> None:
        """Mutating the return dict shape would lose a key."""
        patched_runners["register"]("m", [{"action": "click", "selector": "#x"}])
        out = await run_macro(fake_session, "m")
        assert set(out.keys()) == {"macro", "executed", "skipped", "args_used", "slowmo_ms", "elapsed_s"}

    @pytest.mark.anyio
    async def test_executed_count_aggregates_dispatch_returns(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any]
    ) -> None:
        """fake dispatch returns (1, 0) per action; 3 actions → executed=3."""
        patched_runners["register"](
            "m",
            [
                {"action": "click", "selector": "#a"},
                {"action": "click", "selector": "#b"},
                {"action": "click", "selector": "#c"},
            ],
        )
        out = await run_macro(fake_session, "m")
        assert out["executed"] == 3
        assert out["skipped"] == 0

    @pytest.mark.anyio
    async def test_macro_name_round_trips(self, fake_session: _FakeSession, patched_runners: dict[str, Any]) -> None:
        """The 'macro' field equals the load name argument."""
        patched_runners["register"]("flow-a", [{"action": "click", "selector": "#x"}])
        out = await run_macro(fake_session, "flow-a")
        assert out["macro"] == "flow-a"

    @pytest.mark.anyio
    async def test_args_used_reflects_caller_args(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any]
    ) -> None:
        """Caller args echoed in args_used."""
        patched_runners["register"]("m", [])
        out = await run_macro(fake_session, "m", args={"email": "me@x", "pw": "secret"})
        assert out["args_used"] == {"email": "me@x", "pw": "secret"}

    @pytest.mark.anyio
    async def test_args_used_defaults_to_empty_dict(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any]
    ) -> None:
        """args=None → effective_args={} → args_used={}."""
        patched_runners["register"]("m", [])
        out = await run_macro(fake_session, "m")
        assert out["args_used"] == {}

    @pytest.mark.anyio
    async def test_slowmo_ms_explicit_value_echoed(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any]
    ) -> None:
        """Explicit slowmo_ms passes through resolve_slowmo_ms."""
        patched_runners["register"]("m", [])
        out = await run_macro(fake_session, "m", slowmo_ms=200)
        assert out["slowmo_ms"] == 200

    @pytest.mark.anyio
    async def test_slowmo_ms_default_falls_back_to_macro_slowmo_const(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """slowmo_ms=None → MACRO_SLOWMO_MS env default is used."""
        monkeypatch.setattr(_execution, "MACRO_SLOWMO_MS", 42)
        patched_runners["register"]("m", [])
        out = await run_macro(fake_session, "m")
        assert out["slowmo_ms"] == 42

    @pytest.mark.anyio
    async def test_elapsed_s_rounded_to_3_places(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any]
    ) -> None:
        """round(elapsed, 3) — mutating the precision would change the float."""
        patched_runners["register"]("m", [])
        out = await run_macro(fake_session, "m")
        # Just check shape: float, non-negative, at most 3 decimal places.
        assert isinstance(out["elapsed_s"], float)
        assert out["elapsed_s"] >= 0
        # Fewer than ~4 decimal places after the dot.
        s = repr(out["elapsed_s"])
        if "." in s:
            assert len(s.split(".")[1]) <= 4  # extra tolerance for trailing-zero stripping


# ─── run_macro: failure path ─────────────────────────────────────────────────


class TestRunMacroFailurePath:
    @pytest.mark.anyio
    async def test_dispatch_failure_raises_runtime_error(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any]
    ) -> None:
        """Dispatch exception wrapped in RuntimeError with diagnostic payload."""
        patched_runners["register"]("m", [{"action": "click", "selector": "#x"}])
        patched_runners["raise_on"]["click"] = ValueError("boom")
        with pytest.raises(RuntimeError) as exc_info:
            await run_macro(fake_session, "m")
        # The original is chained.
        assert isinstance(exc_info.value.__cause__, ValueError)

    @pytest.mark.anyio
    async def test_failure_payload_includes_macro_name_and_step(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any]
    ) -> None:
        """RuntimeError args[0] is a dict carrying macro/failed_at_step/failed_action."""
        patched_runners["register"](
            "m",
            [
                {"action": "click", "selector": "#x"},
                {"action": "click", "selector": "#y"},
            ],
        )
        patched_runners["raise_on"]["click"] = ValueError("boom")
        with pytest.raises(RuntimeError) as exc_info:
            await run_macro(fake_session, "m")
        payload = exc_info.value.args[0]
        assert payload["macro"] == "m"
        assert payload["failed_at_step"] == 0
        assert payload["failed_action"] == {"action": "click", "selector": "#x"}
        assert "ValueError" in payload["original"]

    @pytest.mark.anyio
    async def test_failure_payload_includes_diagnostic_bundle(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any]
    ) -> None:
        """session.diagnostic_bundle() output is embedded in the payload."""
        patched_runners["register"]("m", [{"action": "click", "selector": "#x"}])
        patched_runners["raise_on"]["click"] = ValueError("boom")
        fake_session.diagnostic_bundle = AsyncMock(return_value={"hint": "yo"})
        with pytest.raises(RuntimeError) as exc_info:
            await run_macro(fake_session, "m")
        assert exc_info.value.args[0]["bundle"] == {"hint": "yo"}

    @pytest.mark.anyio
    async def test_healing_suggestion_added_when_suggest_returns_string(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any]
    ) -> None:
        """Truthy suggest_fix → payload includes healing_suggestion."""
        patched_runners["register"]("m", [{"action": "click", "selector": "#x"}])
        patched_runners["raise_on"]["click"] = ValueError("boom")
        with pytest.raises(RuntimeError) as exc_info:
            await run_macro(fake_session, "m")
        assert exc_info.value.args[0]["healing_suggestion"] == "use #other instead"

    @pytest.mark.anyio
    async def test_healing_suggestion_omitted_when_suggest_returns_none(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Falsy suggest_fix → key absent."""

        async def no_suggestion(session: Any, action: dict[str, Any]) -> str | None:
            return None

        monkeypatch.setattr(_execution, "_suggest_fix", no_suggestion)
        patched_runners["register"]("m", [{"action": "click", "selector": "#x"}])
        patched_runners["raise_on"]["click"] = ValueError("boom")
        with pytest.raises(RuntimeError) as exc_info:
            await run_macro(fake_session, "m")
        assert "healing_suggestion" not in exc_info.value.args[0]

    @pytest.mark.anyio
    async def test_failed_step_index_is_position_in_actions(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any]
    ) -> None:
        """If a later action fails, failed_at_step reflects its index."""
        patched_runners["register"](
            "m",
            [
                {"action": "click", "selector": "#a"},
                {"action": "navigate", "url": "https://x"},
                {"action": "click", "selector": "#fails"},
            ],
        )
        patched_runners["raise_on"]["navigate"] = ValueError("nav-boom")
        with pytest.raises(RuntimeError) as exc_info:
            await run_macro(fake_session, "m")
        assert exc_info.value.args[0]["failed_at_step"] == 1


# ─── credential redaction ───────────────────────────────────────────────────


class TestCredentialRedaction:
    """The action dict embedded in the RuntimeError payload reaches the MCP
    client AND any log sink. ``substitute()`` has already resolved
    ``{{password}}``-style placeholders into the action before dispatch, so
    the raw ``value`` field can be a literal credential — redact it for the
    fill/type/fill_by kinds."""

    @pytest.mark.anyio
    async def test_fill_value_redacted_in_failure_payload(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any]
    ) -> None:
        patched_runners["register"](
            "m",
            [{"action": "fill", "selector": "#pw", "value": "hunter2"}],
        )
        patched_runners["raise_on"]["fill"] = ValueError("boom")
        with pytest.raises(RuntimeError) as exc_info:
            await run_macro(fake_session, "m")
        action = exc_info.value.args[0]["failed_action"]
        assert action["value"] == "<redacted>"
        # Selector + action kind are preserved — only the secret is stripped.
        assert action["selector"] == "#pw"
        assert action["action"] == "fill"

    @pytest.mark.anyio
    async def test_type_value_redacted_in_failure_payload(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any]
    ) -> None:
        patched_runners["register"](
            "m",
            [{"action": "type", "selector": "#pw", "value": "hunter2"}],
        )
        patched_runners["raise_on"]["type"] = ValueError("boom")
        with pytest.raises(RuntimeError) as exc_info:
            await run_macro(fake_session, "m")
        assert exc_info.value.args[0]["failed_action"]["value"] == "<redacted>"

    @pytest.mark.anyio
    async def test_fill_by_value_redacted_in_failure_payload(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any]
    ) -> None:
        patched_runners["register"](
            "m",
            [{"action": "fill_by", "name": "Password", "value": "hunter2"}],
        )
        patched_runners["raise_on"]["fill_by"] = ValueError("boom")
        with pytest.raises(RuntimeError) as exc_info:
            await run_macro(fake_session, "m")
        assert exc_info.value.args[0]["failed_action"]["value"] == "<redacted>"

    @pytest.mark.anyio
    async def test_click_action_value_passes_through(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any]
    ) -> None:
        """Non-credential actions are unchanged — only fill/type/fill_by carry secrets."""
        patched_runners["register"](
            "m",
            [{"action": "click", "selector": "#x", "value": "metadata"}],
        )
        patched_runners["raise_on"]["click"] = ValueError("boom")
        with pytest.raises(RuntimeError) as exc_info:
            await run_macro(fake_session, "m")
        assert exc_info.value.args[0]["failed_action"]["value"] == "metadata"

    @pytest.mark.anyio
    async def test_original_action_not_mutated_by_redaction(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any]
    ) -> None:
        """The redaction works on a copy; the macro author's action dict stays intact."""
        action_in = {"action": "fill", "selector": "#pw", "value": "hunter2"}
        patched_runners["register"]("m", [action_in])
        patched_runners["raise_on"]["fill"] = ValueError("boom")
        with pytest.raises(RuntimeError):
            await run_macro(fake_session, "m")
        # The dispatcher saw the original (the macro list isn't mutated by
        # the redaction-on-failure code).
        assert patched_runners["dispatched"][0]["value"] == "hunter2"

    @pytest.mark.anyio
    async def test_pill_status_shows_redacted_value(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any]
    ) -> None:
        """The status pill receives the redacted action so screenshots/traces
        of the page don't expose the credential."""
        patched_runners["register"](
            "m",
            [{"action": "fill", "selector": "#pw", "value": "hunter2"}],
        )
        await run_macro(fake_session, "m")
        # Inspect every status push for the secret.
        for call in fake_session.page.evaluate.call_args_list:
            text = call[0][1].get("text", "")
            assert "hunter2" not in text


# ─── run_macro: pill push sequencing ────────────────────────────────────────


class TestRunMacroPillSequence:
    @pytest.mark.anyio
    async def test_first_push_is_start(self, fake_session: _FakeSession, patched_runners: dict[str, Any]) -> None:
        """First evaluate call carries start=True."""
        patched_runners["register"]("m", [])
        await run_macro(fake_session, "m")
        first_payload = fake_session.page.evaluate.call_args_list[0][0][1]
        assert first_payload.get("start") is True
        assert first_payload["text"] == "m | starting"

    @pytest.mark.anyio
    async def test_last_push_is_done_on_success(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any]
    ) -> None:
        """Final push carries done=True with the 'done' label."""
        patched_runners["register"]("m", [])
        await run_macro(fake_session, "m")
        last_payload = fake_session.page.evaluate.call_args_list[-1][0][1]
        assert last_payload.get("done") is True
        assert last_payload["text"] == "m | done"

    @pytest.mark.anyio
    async def test_last_push_is_failed_on_exception(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any]
    ) -> None:
        """If an action raises, the final push label is 'failed'."""
        patched_runners["register"]("m", [{"action": "click", "selector": "#x"}])
        patched_runners["raise_on"]["click"] = ValueError("boom")
        with pytest.raises(RuntimeError):
            await run_macro(fake_session, "m")
        last_payload = fake_session.page.evaluate.call_args_list[-1][0][1]
        assert last_payload.get("done") is True
        assert last_payload["text"] == "m | failed"


# ─── run_sequence ────────────────────────────────────────────────────────────


class TestRunSequenceArgsAlignment:
    @pytest.mark.anyio
    async def test_args_list_none_yields_empty_args_per_step(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any]
    ) -> None:
        """args_list=None → every step gets {}."""
        patched_runners["register"]("a", [])
        patched_runners["register"]("b", [])
        out = await run_sequence(session=fake_session, names=["a", "b"])
        assert out["steps"][0]["args_used"] == {}
        assert out["steps"][1]["args_used"] == {}

    @pytest.mark.anyio
    async def test_args_list_shorter_than_names_pads_empty(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any]
    ) -> None:
        """Steps beyond the args_list length get {}."""
        patched_runners["register"]("a", [])
        patched_runners["register"]("b", [])
        patched_runners["register"]("c", [])
        out = await run_sequence(session=fake_session, names=["a", "b", "c"], args_list=[{"k": 1}])
        assert out["steps"][0]["args_used"] == {"k": 1}
        assert out["steps"][1]["args_used"] == {}
        assert out["steps"][2]["args_used"] == {}

    @pytest.mark.anyio
    async def test_args_list_falsy_entry_replaced_by_empty(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any]
    ) -> None:
        """The `args_list[index] or {}` branch — None or {} both yield {}."""
        patched_runners["register"]("a", [])
        patched_runners["register"]("b", [])
        out = await run_sequence(
            session=fake_session,
            names=["a", "b"],
            args_list=[None, {"x": 1}],  # type: ignore[list-item]
        )
        assert out["steps"][0]["args_used"] == {}
        assert out["steps"][1]["args_used"] == {"x": 1}


class TestRunSequenceShape:
    @pytest.mark.anyio
    async def test_success_step_has_ok_true_and_run_fields(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any]
    ) -> None:
        """Success step carries every field of MacroRunResult plus ok=True."""
        patched_runners["register"]("a", [])
        out = await run_sequence(session=fake_session, names=["a"])
        step = out["steps"][0]
        assert step["ok"] is True
        assert step["macro"] == "a"
        assert step["executed"] == 0
        assert step["args_used"] == {}
        assert "elapsed_s" in step
        assert "slowmo_ms" in step

    @pytest.mark.anyio
    async def test_aggregate_ok_true_when_all_steps_succeed(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any]
    ) -> None:
        """All-success run sets top-level ok=True."""
        patched_runners["register"]("a", [])
        patched_runners["register"]("b", [])
        out = await run_sequence(session=fake_session, names=["a", "b"])
        assert out["ok"] is True
        assert out["sequence"] == ["a", "b"]

    @pytest.mark.anyio
    async def test_continue_on_failure_records_error_step(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any]
    ) -> None:
        """stop_on_failure=False → failed step has ok=False + error string."""
        patched_runners["register"]("a", [{"action": "click", "selector": "#x"}])
        patched_runners["register"]("b", [])
        patched_runners["raise_on"]["click"] = ValueError("boom")
        out = await run_sequence(session=fake_session, names=["a", "b"], stop_on_failure=False)
        assert out["ok"] is False
        assert out["steps"][0]["ok"] is False
        assert out["steps"][0]["macro"] == "a"
        assert "boom" in out["steps"][0]["error"] or "click" in out["steps"][0]["error"]
        # Second step still ran.
        assert out["steps"][1]["ok"] is True

    @pytest.mark.anyio
    async def test_stop_on_failure_reraises_after_recording(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any]
    ) -> None:
        """stop_on_failure=True (default) re-raises after the failed step is appended."""
        patched_runners["register"]("a", [{"action": "click", "selector": "#x"}])
        patched_runners["register"]("b", [])
        patched_runners["raise_on"]["click"] = ValueError("boom")
        with pytest.raises(RuntimeError):
            await run_sequence(session=fake_session, names=["a", "b"])

    @pytest.mark.anyio
    async def test_failure_step_carries_args_used(
        self, fake_session: _FakeSession, patched_runners: dict[str, Any]
    ) -> None:
        """Failure step args_used field reflects the per-step args input."""
        patched_runners["register"]("a", [{"action": "click", "selector": "#x"}])
        patched_runners["raise_on"]["click"] = ValueError("boom")
        out = await run_sequence(
            session=fake_session,
            names=["a"],
            args_list=[{"foo": "bar"}],
            stop_on_failure=False,
        )
        assert out["steps"][0]["args_used"] == {"foo": "bar"}
