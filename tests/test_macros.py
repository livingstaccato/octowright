# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import ast
import importlib
import json
import time
from pathlib import Path
from typing import Any

import pytest

from tests._operation_gate_fakes import OperationAwareFake

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Sentinel value used wherever a "password" substitution arg is exercised.
# Renamed away from realistic strings ("s3cr3t" / "pass123") because the
# detect-secrets pre-commit hook fires on the "password" keyword regardless
# of value; the obviously-synthetic name plus a single module-level constant
# keeps the test diff free of allowlist-secret pragmas at every call site.
PW_FIXTURE = "fixture-not-a-real-secret"  # pragma: allowlist secret


SAMPLE_RECORDING = [
    {"ts": "2026-04-24T10:00:00.000Z", "action": "launch", "url": "https://octowright.com"},
    {"ts": "2026-04-24T10:00:01.000Z", "action": "navigate", "url": "https://discord.com/login"},
    {
        "ts": "2026-04-24T10:00:02.000Z",
        "action": "fill",
        "selector": "input[name=email]",
        "value": "me@octowright.test",
    },
    {"ts": "2026-04-24T10:00:03.000Z", "action": "fill", "selector": "input[name=password]", "value": "hunter2"},
    {"ts": "2026-04-24T10:00:04.000Z", "action": "click", "selector": "button[type=submit]"},
    {"ts": "2026-04-24T10:00:05.000Z", "action": "snapshot"},
    {"ts": "2026-04-24T10:00:06.000Z", "action": "close"},
]


def _write_recording(tmp_path: Path, lines: list[dict[str, Any]] | None = None) -> Path:
    p = tmp_path / "recording.jsonl"
    p.write_text(
        "\n".join(json.dumps(line) for line in (lines or SAMPLE_RECORDING)),
        encoding="utf-8",
    )
    return p


def _import_macros(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Set env vars BEFORE importing so module-level MACROS_DIR resolves correctly."""
    monkeypatch.setenv("OCTOWRIGHT_MACROS_DIR", str(tmp_path / "macros"))
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path / "profiles"))
    # Force re-import so module-level constants pick up patched env vars.
    # MACROS_DIR is now defined in octowright.defaults (and re-exported by
    # macros.storage), so reload defaults first.
    from octowright import defaults

    importlib.reload(defaults)
    import octowright.macros.storage as _storage

    importlib.reload(_storage)
    import octowright.macros as _m

    importlib.reload(_m)
    return _m


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_save_macro_writes_expected_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    rec = _write_recording(tmp_path)

    path = m.save_macro(
        recording_path=rec,
        name="discord-login",
        description="Log in to Discord",
        parameters={"email": "me@octowright.test", "password": "hunter2"},
    )

    assert path.exists()
    data = json.loads(path.read_text())

    assert data["name"] == "discord-login"
    assert data["description"] == "Log in to Discord"
    assert set(data["parameters"]) == {"email", "password"}
    assert data["created_at"]
    assert data["updated_at"]

    # Parameters were substituted
    actions = data["actions"]
    fill_actions = [a for a in actions if a["action"] == "fill"]
    assert any("{{email}}" in a.get("value", "") for a in fill_actions)
    assert any("{{password}}" in a.get("value", "") for a in fill_actions)


def test_macros_init_is_export_surface_only() -> None:
    init_path = Path("src/octowright/macros/__init__.py")
    tree = ast.parse(init_path.read_text(encoding="utf-8"))
    allowed = (
        ast.Assign,
        ast.Expr,
        ast.ImportFrom,
    )
    disallowed = [
        node
        for node in tree.body
        if not isinstance(node, allowed)
        or isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.If | ast.For | ast.Try | ast.With
        )
    ]
    assert disallowed == []


def test_package_init_files_are_export_surfaces_only() -> None:
    disallowed_nodes = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.If, ast.For, ast.Try, ast.With)
    offenders: list[str] = []
    for init_path in sorted(Path("src/octowright").rglob("__init__.py")):
        tree = ast.parse(init_path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, disallowed_nodes):
                offenders.append(f"{init_path}:{node.lineno}:{type(node).__name__}")
    assert offenders == []


def test_save_macro_strips_lifecycle_actions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    rec = _write_recording(tmp_path)

    # Default: launch stripped, close and snapshot stripped
    path = m.save_macro(recording_path=rec, name="test-strip")
    data = json.loads(path.read_text())
    action_types = [a["action"] for a in data["actions"]]
    assert "launch" not in action_types
    assert "close" not in action_types
    assert "snapshot" not in action_types
    assert "navigate" in action_types

    # include_launch=True: launch kept, close/snapshot still stripped
    path2 = m.save_macro(recording_path=rec, name="test-keep-launch", include_launch=True)
    data2 = json.loads(path2.read_text())
    action_types2 = [a["action"] for a in data2["actions"]]
    assert "launch" in action_types2
    assert "close" not in action_types2
    assert "snapshot" not in action_types2


def test_save_macro_preserves_semantic_metadata_on_css_actions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    rec = _write_recording(
        tmp_path,
        [
            {
                "action": "click",
                "selector": "#login",
                "role": "button",
                "role_name": "Log in",
            },
            {
                "action": "fill",
                "selector": "#email",
                "value": "me@octowright.test",
                "label": "Email",
            },
        ],
    )

    path = m.save_macro(recording_path=rec, name="semantic-css")
    actions = json.loads(path.read_text())["actions"]

    assert actions == [
        {"action": "click", "selector": "#login", "role": "button", "role_name": "Log in"},
        {"action": "fill", "selector": "#email", "value": "me@octowright.test", "label": "Email"},
    ]


def test_save_macro_preserves_created_at_on_overwrite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    rec = _write_recording(tmp_path)

    path = m.save_macro(recording_path=rec, name="overwrite-me")
    first = json.loads(path.read_text())
    original_created_at = first["created_at"]

    # Small delay so updated_at would differ if it changes
    time.sleep(0.01)

    path2 = m.save_macro(recording_path=rec, name="overwrite-me")
    second = json.loads(path2.read_text())

    assert second["created_at"] == original_created_at
    # updated_at may be equal (same second) but should never be earlier
    assert second["updated_at"] >= first["updated_at"]


def test_list_macros_sorted_desc_and_has_action_count(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    rec = _write_recording(tmp_path)

    m.save_macro(recording_path=rec, name="alpha")
    time.sleep(0.01)
    m.save_macro(recording_path=rec, name="beta")

    result = m.list_macros()
    assert len(result) == 2
    assert result[0]["name"] == "beta"
    assert result[1]["name"] == "alpha"
    # action_count present and is an int
    for item in result:
        assert isinstance(item["action_count"], int)
        assert item["action_count"] >= 0


def test_load_macro_returns_full_dict(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    rec = _write_recording(tmp_path)

    m.save_macro(recording_path=rec, name="loadable", description="hello")
    data = m.load_macro("loadable")

    assert data["name"] == "loadable"
    assert data["description"] == "hello"
    assert "actions" in data


def test_load_macro_raises_on_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)

    with pytest.raises(FileNotFoundError):
        m.load_macro("does-not-exist")


def test_delete_macro_removes_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    rec = _write_recording(tmp_path)

    m.save_macro(recording_path=rec, name="to-delete")
    deleted_path = m.delete_macro("to-delete")

    assert not deleted_path.exists()


def test_delete_macro_raises_on_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)

    with pytest.raises(FileNotFoundError):
        m.delete_macro("ghost-macro")


def test_substitute_replaces_placeholders(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)

    actions = [
        {"action": "fill", "selector": "input[name=email]", "value": "{{email}}"},
        {"action": "fill", "selector": "input[name=pw]", "value": "{{password}}"},
        {"action": "click", "selector": "button"},
    ]
    result = m.substitute(actions, {"email": "cosmo@octowright.test", "password": PW_FIXTURE})

    assert result[0]["value"] == "cosmo@octowright.test"
    assert result[1]["value"] == PW_FIXTURE
    assert result[2]["selector"] == "button"


def test_substitute_raises_on_missing_placeholder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)

    actions = [{"action": "fill", "selector": "x", "value": "{{missing_key}}"}]
    with pytest.raises(KeyError) as exc_info:
        m.substitute(actions, {})
    assert "missing_key" in str(exc_info.value)


def test_substitute_noop_for_extra_args(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)

    actions = [{"action": "click", "selector": "button"}]
    result = m.substitute(actions, {"unused_key": "some_value", "another": "val"})
    assert result == actions


@pytest.mark.anyio
async def test_run_macro_calls_session_in_order(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    rec = _write_recording(tmp_path)

    # include_launch=True keeps the launch action in the saved file so that
    # run_macro's replay-time skip logic is exercised.
    saved_path = m.save_macro(
        recording_path=rec,
        name="replay-test",
        parameters={"email": "me@octowright.test", "password": "hunter2"},
        include_launch=True,
    )

    # Also inject a close and a snapshot directly into the saved JSON so that
    # all three lifecycle/inspection action types appear in the replay list.
    saved_data = json.loads(saved_path.read_text())
    saved_data["actions"].append({"action": "close"})
    saved_data["actions"].append({"action": "snapshot"})
    saved_path.write_text(json.dumps(saved_data), encoding="utf-8")

    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    class FakeSession(OperationAwareFake):
        # SessionLike attrs for run_macro span tagging.
        instance_id = "fake-instance"
        kind = "chromium"
        page = None  # `_push_status` reads `.page`; None short-circuits.

        async def diagnostic_bundle(self) -> dict[str, Any]:
            return {}

        async def navigate(self, url: str) -> dict[str, Any]:
            calls.append(("navigate", (url,), {}))
            return {"url": url, "title": ""}

        async def click(self, selector: str) -> None:
            calls.append(("click", (selector,), {}))

        async def type_text(self, selector: str, text: str, delay_ms: int | None) -> None:
            calls.append(("type_text", (selector, text, delay_ms), {}))

        async def fill(self, selector: str, value: str) -> None:
            calls.append(("fill", (selector, value), {}))

        async def press_key(self, key: str) -> None:
            calls.append(("press_key", (key,), {}))

        async def screenshot(self, path: Path) -> Path:
            calls.append(("screenshot", (path,), {}))
            return path

        async def evaluate(self, expression: str) -> Any:
            calls.append(("evaluate", (expression,), {}))
            return None

        async def wait_for(self, selector: str | None, text: str | None, timeout_ms: int | None) -> None:
            calls.append(("wait_for", (selector, text, timeout_ms), {}))

    fake = FakeSession()
    result = await m.run_macro(
        fake,  # type: ignore[arg-type]
        "replay-test",
        args={"email": "cosmo@octowright.test", "password": PW_FIXTURE},
    )

    assert result["macro"] == "replay-test"
    assert result["executed"] > 0
    # launch, close, snapshot are all in the saved action list (we injected
    # them above) — run_macro must skip all three.
    assert result["skipped"] >= 3

    # navigate was first non-lifecycle action
    assert calls[0][0] == "navigate"
    assert calls[0][1][0] == "https://discord.com/login"

    # fill calls used substituted values
    fill_calls = [(name, args) for name, args, _ in calls if name == "fill"]
    fill_values = [args[1] for _, args in fill_calls]
    assert "cosmo@octowright.test" in fill_values
    assert PW_FIXTURE in fill_values

    # click was called (button[type=submit])
    click_calls = [name for name, _, _ in calls if name == "click"]
    assert click_calls  # at least one click


@pytest.mark.anyio
async def test_run_macro_macro_call_dispatches_nested_actions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)

    _save_macro_file(
        m,
        tmp_path,
        "signup-child",
        [
            {"action": "fill", "selector": "#email", "value": "{{email}}"},
            {"action": "click", "selector": "#submit"},
        ],
    )
    _save_macro_file(
        m,
        tmp_path,
        "signup-parent",
        [
            {"action": "navigate", "url": "https://octowright.com"},
            {"action": "macro_call", "name": "signup-child", "args": {"email": "{{email}}"}},
        ],
    )

    session = _CallAwareFakeSession()
    result = await m.run_macro(session, "signup-parent", args={"email": "person@octowright.test"})

    assert result["macro"] == "signup-parent"
    # 1 navigate + 1 macro_call wrapper + 2 child actions = 4 executed.
    assert result["executed"] == 4
    assert session.calls[1] == ("fill", ("#email", "person@octowright.test"), None)
    assert session.calls[2] == ("click", ("#submit",), None)


@pytest.mark.anyio
async def test_run_macro_macro_call_shape_error_hard_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    _save_macro_file(
        m,
        tmp_path,
        "bad-macro-call",
        [{"action": "macro_call", "name": 123}],
    )
    session = _CallAwareFakeSession()

    with pytest.raises(RuntimeError) as exc_info:
        await m.run_macro(session, "bad-macro-call")

    payload = exc_info.value.args[0]
    assert payload["macro"] == "bad-macro-call"
    assert "macro_call action 'name' must be a non-empty string" in payload["original"]


@pytest.mark.anyio
async def test_run_macro_macro_call_detects_direct_recursion(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    _save_macro_file(m, tmp_path, "loop", [{"action": "macro_call", "name": "loop"}])
    session = _CallAwareFakeSession()

    with pytest.raises(RuntimeError) as exc_info:
        await m.run_macro(session, "loop")

    assert "macro_call recursion detected: loop -> loop" in str(exc_info.value.args[0]["original"])


@pytest.mark.anyio
async def test_run_macro_macro_call_detects_mutual_recursion(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    _save_macro_file(m, tmp_path, "macro-a", [{"action": "macro_call", "name": "macro-b"}])
    _save_macro_file(m, tmp_path, "macro-b", [{"action": "macro_call", "name": "macro-a"}])
    session = _CallAwareFakeSession()

    with pytest.raises(RuntimeError) as exc_info:
        await m.run_macro(session, "macro-a")

    assert "macro_call recursion detected: macro-a -> macro-b -> macro-a" in str(exc_info.value.args[0]["original"])


@pytest.mark.anyio
async def test_run_macro_macro_call_enforces_depth_limit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    import octowright.macros.execution as macro_execution

    monkeypatch.setattr(macro_execution, "MAX_MACRO_CALL_DEPTH", 2, raising=False)
    _save_macro_file(m, tmp_path, "root", [{"action": "macro_call", "name": "second"}])
    _save_macro_file(m, tmp_path, "second", [{"action": "macro_call", "name": "third"}])
    _save_macro_file(m, tmp_path, "third", [{"action": "navigate", "url": "https://octowright.com"}])
    session = _CallAwareFakeSession()

    with pytest.raises(RuntimeError) as exc_info:
        await m.run_macro(session, "root")

    assert "macro_call recursion depth exceeded (2) at root -> second -> third" in str(
        exc_info.value.args[0]["original"]
    )


# ---------------------------------------------------------------------------
# run_sequence tests
# ---------------------------------------------------------------------------


class _FakeSessionForSequence(OperationAwareFake):
    """Minimal fake BrowserSession for run_sequence tests."""

    instance_id = "fake-instance"
    kind = "chromium"

    def __init__(self, *, raises_on: str | None = None) -> None:
        super().__init__()
        self._raises_on = raises_on
        self.calls: list[str] = []
        # `_push_status` reads `.page`; None short-circuits the JS push.
        self.page = None

    async def navigate(self, url: str) -> dict[str, Any]:
        self.calls.append("navigate")
        return {"url": url, "title": ""}

    async def click(self, selector: str) -> None:
        self.calls.append("click")

    async def type_text(self, selector: str, text: str, delay_ms: int | None) -> None:
        self.calls.append("type_text")

    async def fill(self, selector: str, value: str) -> None:
        self.calls.append("fill")
        if self._raises_on and selector == self._raises_on:
            raise RuntimeError(f"intentional failure on selector {selector!r}")

    async def press_key(self, key: str) -> None:
        self.calls.append("press_key")

    async def screenshot(self, path: Path) -> Path:
        self.calls.append("screenshot")
        return path

    async def evaluate(self, expression: str) -> Any:
        self.calls.append("evaluate")
        return None

    async def wait_for(self, selector: str | None, text: str | None, timeout_ms: int | None) -> None:
        self.calls.append("wait_for")

    async def diagnostic_bundle(
        self, *, screenshot_dir: Any = None, console_tail: int = 25, html_full: bool = False
    ) -> dict[str, Any]:
        return {"screenshot": None, "console": [], "url": "about:blank"}


def _save_macro_file(m: Any, tmp_path: Path, name: str, actions: list[dict[str, Any]]) -> None:
    (tmp_path / "macros").mkdir(parents=True, exist_ok=True)
    macro = {
        "name": name,
        "description": None,
        "parameters": [],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "actions": actions,
    }
    (tmp_path / "macros" / f"{name}.json").write_text(json.dumps(macro), encoding="utf-8")


class _CallAwareFakeSession(OperationAwareFake):
    # SessionLike attrs accessed by run_macro / dispatch_simple for span +
    # log tagging; previously masked by getattr(..., None) defaults.
    instance_id = "fake-instance"
    kind = "chromium"

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any] | None]] = []
        # `_push_status` reads `.page`; None short-circuits the JS push, which
        # is what the test wants (no Playwright in-process).
        self.page = None

    async def navigate(self, url: str) -> dict[str, Any]:
        self.calls.append(("navigate", (url,), None))
        return {"url": url, "title": ""}

    async def click(self, selector: str) -> None:
        self.calls.append(("click", (selector,), None))

    async def type_text(self, selector: str, text: str, delay_ms: int | None) -> None:
        self.calls.append(("type_text", (selector, text, delay_ms), None))

    async def fill(self, selector: str, value: str) -> None:
        self.calls.append(("fill", (selector, value), None))

    async def press_key(self, key: str) -> None:
        self.calls.append(("press_key", (key,), None))

    async def screenshot(self, path: Path) -> Path:
        self.calls.append(("screenshot", (str(path),), None))
        return path

    async def evaluate(self, expression: str) -> Any:
        self.calls.append(("evaluate", (expression,), None))
        return None

    async def wait_for(self, selector: str | None, text: str | None, timeout_ms: int | None) -> None:
        self.calls.append(("wait_for", (selector, text, timeout_ms), None))

    async def diagnostic_bundle(
        self, *, screenshot_dir: Any = None, console_tail: int = 25, html_full: bool = False
    ) -> dict[str, Any]:
        return {"screenshot": None, "console": [], "url": "about:blank"}


def _save_minimal_macro(m: Any, tmp_path: Path, name: str) -> None:
    """Write a single-navigate macro JSON directly (no recording needed)."""
    import json

    (tmp_path / "macros").mkdir(parents=True, exist_ok=True)
    macro = {
        "name": name,
        "description": None,
        "parameters": [],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "actions": [{"action": "navigate", "url": "https://octowright.com"}],
    }
    (tmp_path / "macros" / f"{name}.json").write_text(json.dumps(macro), encoding="utf-8")


@pytest.mark.anyio
async def test_run_sequence_all_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)
    _save_minimal_macro(m, tmp_path, "seq-a")
    _save_minimal_macro(m, tmp_path, "seq-b")

    session = _FakeSessionForSequence()
    result = await m.run_sequence(session=session, names=["seq-a", "seq-b"])  # type: ignore[arg-type]

    assert result["ok"] is True
    assert result["sequence"] == ["seq-a", "seq-b"]
    assert len(result["steps"]) == 2
    assert all(s["ok"] for s in result["steps"])


@pytest.mark.anyio
async def test_run_sequence_stop_on_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Middle macro raises → chain stops, third macro not called."""
    m = _import_macros(monkeypatch, tmp_path)
    _save_minimal_macro(m, tmp_path, "pre")

    # middle macro: single fill action that triggers failure
    import json

    (tmp_path / "macros").mkdir(parents=True, exist_ok=True)
    bad_macro = {
        "name": "bad",
        "description": None,
        "parameters": [],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "actions": [{"action": "fill", "selector": "__fail__", "value": "x"}],
    }
    (tmp_path / "macros" / "bad.json").write_text(json.dumps(bad_macro), encoding="utf-8")
    _save_minimal_macro(m, tmp_path, "post")

    session = _FakeSessionForSequence(raises_on="__fail__")
    with pytest.raises(RuntimeError):
        await m.run_sequence(session=session, names=["pre", "bad", "post"])  # type: ignore[arg-type]

    # "post" macro should not have been called — navigate only runs for pre
    assert session.calls.count("navigate") == 1


@pytest.mark.anyio
async def test_run_sequence_collect_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """stop_on_failure=False — all three run, per-step ok flags reflect outcomes."""
    m = _import_macros(monkeypatch, tmp_path)
    _save_minimal_macro(m, tmp_path, "s1")

    import json

    (tmp_path / "macros").mkdir(parents=True, exist_ok=True)
    bad_macro = {
        "name": "s2-bad",
        "description": None,
        "parameters": [],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "actions": [{"action": "fill", "selector": "__fail__", "value": "x"}],
    }
    (tmp_path / "macros" / "s2-bad.json").write_text(json.dumps(bad_macro), encoding="utf-8")
    _save_minimal_macro(m, tmp_path, "s3")

    session = _FakeSessionForSequence(raises_on="__fail__")
    result = await m.run_sequence(
        session=session,  # type: ignore[arg-type]
        names=["s1", "s2-bad", "s3"],
        stop_on_failure=False,
    )

    assert result["ok"] is False
    assert len(result["steps"]) == 3
    assert result["steps"][0]["ok"] is True
    assert result["steps"][1]["ok"] is False
    assert result["steps"][1]["error"]  # non-empty error string
    assert result["steps"][2]["ok"] is True


@pytest.mark.anyio
async def test_run_macro_dispatches_expect_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Macro JSONL with expect_text action dispatches to _check_text via session.page."""
    m = _import_macros(monkeypatch, tmp_path)

    import json

    (tmp_path / "macros").mkdir(parents=True, exist_ok=True)
    macro = {
        "name": "assert-macro",
        "description": None,
        "parameters": [],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "actions": [{"action": "expect_text", "selector": "#x", "text": "hi"}],
    }
    (tmp_path / "macros" / "assert-macro.json").write_text(json.dumps(macro), encoding="utf-8")

    class FakeElement:
        async def inner_text(self) -> str:
            return "hi there"

    class FakePage:
        async def wait_for_selector(self, selector: str, timeout: int = 15000) -> FakeElement:
            return FakeElement()

    class FakeSessionWithPage(OperationAwareFake):
        page = FakePage()
        # SessionLike attrs for span / log tagging in run_macro.
        instance_id = "fake-instance"
        kind = "chromium"

        async def diagnostic_bundle(self) -> dict[str, Any]:
            return {}

        async def expect_text(
            self, selector: str, text: str, mode: str = "contains", timeout_ms: int | None = None
        ) -> str:
            return "hi"

    result = await m.run_macro(FakeSessionWithPage(), "assert-macro")  # type: ignore[arg-type]
    assert result["executed"] == 1
    assert result["skipped"] == 0


# ---------------------------------------------------------------------------
# Auto-capture on failure tests
# ---------------------------------------------------------------------------


class _FakeSessionWithDiagnostic(OperationAwareFake):
    """Fake session whose click always raises, and provides diagnostic_bundle."""

    # SessionLike attrs for run_macro span tagging.
    instance_id = "fake-instance"
    kind = "chromium"
    page = None  # `_push_status` reads `.page`; None short-circuits.

    async def navigate(self, url: str) -> dict[str, Any]:
        return {"url": url, "title": ""}

    async def click(self, selector: str) -> None:
        raise ValueError(f"fake click error on {selector!r}")

    async def type_text(self, selector: str, text: str, delay_ms: int | None) -> None:
        pass

    async def fill(self, selector: str, value: str) -> None:
        pass

    async def press_key(self, key: str) -> None:
        pass

    async def screenshot(self, path: Path) -> Path:
        return path

    async def evaluate(self, expression: str) -> Any:
        return None

    async def wait_for(self, selector: str | None, text: str | None, timeout_ms: int | None) -> None:
        pass

    async def diagnostic_bundle(
        self, *, screenshot_dir: Any = None, console_tail: int = 25, html_full: bool = False
    ) -> dict[str, Any]:
        bundle: dict[str, Any] = {
            "console_tail": [],
            "url": "fake://page",
            "title": "fake",
            "html_path": "/tmp/fake-fail.html",
            "html_size": 8,
            "html_sha256": "abc123",
            "html_preview": "<html/>",
            "screenshot": None,
        }
        if html_full:
            bundle["html"] = "<html/>"
        return bundle


@pytest.mark.anyio
async def test_run_macro_captures_diagnostic_bundle_on_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When an action raises, run_macro wraps the error with a diagnostic bundle."""
    m = _import_macros(monkeypatch, tmp_path)

    import json

    (tmp_path / "macros").mkdir(parents=True, exist_ok=True)
    macro = {
        "name": "fail-macro",
        "description": None,
        "parameters": [],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "actions": [
            {"action": "navigate", "url": "https://octowright.com"},
            {"action": "click", "selector": "#does-not-exist"},
        ],
    }
    (tmp_path / "macros" / "fail-macro.json").write_text(json.dumps(macro), encoding="utf-8")

    session = _FakeSessionWithDiagnostic()

    with pytest.raises(RuntimeError) as exc_info:
        await m.run_macro(session, "fail-macro")  # type: ignore[arg-type]

    payload = exc_info.value.args[0]
    assert isinstance(payload, dict)
    assert payload["macro"] == "fail-macro"
    assert payload["failed_at_step"] == 1  # second action (index 1)
    assert payload["failed_action"]["action"] == "click"
    assert "original" in payload
    assert "bundle" in payload

    bundle = payload["bundle"]
    assert "console_tail" in bundle
    assert "url" in bundle
    assert "html_path" in bundle
    assert "html_size" in bundle
    assert "html_sha256" in bundle
    assert "html_preview" in bundle
    assert "screenshot" in bundle


@pytest.mark.anyio
async def test_run_macro_bundle_has_expected_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The bundle keys match the documented shape even when screenshot is None."""
    m = _import_macros(monkeypatch, tmp_path)

    import json

    (tmp_path / "macros").mkdir(parents=True, exist_ok=True)
    macro = {
        "name": "fail-click",
        "description": None,
        "parameters": [],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "actions": [{"action": "click", "selector": "#boom"}],
    }
    (tmp_path / "macros" / "fail-click.json").write_text(json.dumps(macro), encoding="utf-8")

    session = _FakeSessionWithDiagnostic()

    with pytest.raises(RuntimeError) as exc_info:
        await m.run_macro(session, "fail-click")  # type: ignore[arg-type]

    payload = exc_info.value.args[0]
    bundle = payload["bundle"]

    for key in ("console_tail", "url", "html_path", "html_size", "html_sha256", "html_preview", "screenshot"):
        assert key in bundle, f"missing key {key!r} in bundle"

    assert bundle["screenshot"] is None


@pytest.mark.anyio
async def test_run_macro_chained_cause(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The RuntimeError must chain from the original exception via __cause__."""
    m = _import_macros(monkeypatch, tmp_path)

    import json

    (tmp_path / "macros").mkdir(parents=True, exist_ok=True)
    macro = {
        "name": "cause-test",
        "description": None,
        "parameters": [],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "actions": [{"action": "click", "selector": "#x"}],
    }
    (tmp_path / "macros" / "cause-test.json").write_text(json.dumps(macro), encoding="utf-8")

    session = _FakeSessionWithDiagnostic()

    with pytest.raises(RuntimeError) as exc_info:
        await m.run_macro(session, "cause-test")  # type: ignore[arg-type]

    assert exc_info.value.__cause__ is not None
    assert isinstance(exc_info.value.__cause__, ValueError)
