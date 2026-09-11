# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.

"""One macro credential must stay out of every returned diagnostic surface."""

from __future__ import annotations

import json
import sys
import types
import urllib.parse
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from octowright.artifacts.script_export import render_macro_cli
from octowright.macros import execution
from octowright.macros.privacy import (
    ARG_PRIVACY_CLASSIFIER_VERSION,
    is_sensitive_arg_key,
    redact_args,
    scrub_sensitive_values,
    sensitive_arg_values,
)

PASSWORD = "A4-RAW-PASSWORD-CANARY-9f51"  # pragma: allowlist secret
EMAIL = "a4-private-canary@example.test"
SOCIAL_ARGS = {
    "email": EMAIL,
    "password": PASSWORD,
    "peer_email": "a4-peer-private@example.test",
    "peer_password": "A4-PEER-PASSWORD-CANARY",
    "user": "a4-driver-subject",
    "peer": "a4-peer-subject",
}


def _assert_private(value: object) -> None:
    rendered = repr(value)
    assert PASSWORD not in rendered
    assert EMAIL not in rendered


def _session() -> MagicMock:
    session = MagicMock()
    session.instance_id = "instance-safe"
    session.kind = "chromium"
    session.diagnostic_bundle = AsyncMock(
        return_value={
            "console_tail": [{"type": "error", "text": f"failure {PASSWORD}"}],
            "url": f"https://app.test/?email={EMAIL}",
            "screenshot_error": f"capture failed for {PASSWORD}",
        }
    )
    session.get_network_requests = MagicMock(
        return_value={
            "requests": [
                {
                    "url": f"https://app.test/fail?secret={PASSWORD}",
                    "status": 500,
                    "body": json.dumps({"detail": PASSWORD, "email": EMAIL}),
                    "failure": f"network failure {PASSWORD}",
                }
            ]
        }
    )
    return session


@pytest.mark.asyncio
async def test_failure_scrubs_sensitive_arg_values_from_every_diagnostic_and_exception_chain() -> None:
    session = _session()

    async def fail_dispatch(*_args: Any, **_kwargs: Any) -> tuple[int, int]:
        raise ValueError(f"dispatch {PASSWORD} for {EMAIL}")

    with (
        patch.object(execution, "_dispatch_one", fail_dispatch),
        patch.object(
            execution,
            "load_macro",
            return_value={"actions": [{"action": "fill", "selector": "#password", "value": "{{password}}"}]},
        ),
        patch.object(execution, "_push_status", AsyncMock()),
        patch.object(
            execution,
            "_suggest_fix",
            AsyncMock(return_value=f"replace selector after seeing {PASSWORD}"),
        ),
        pytest.raises(RuntimeError) as caught,
    ):
        await execution.run_macro(
            session,
            "login",
            {"password": PASSWORD, "email": EMAIL},
        )

    _assert_private(caught.value)
    _assert_private(caught.value.args)
    _assert_private(caught.value.__dict__)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


@pytest.mark.asyncio
async def test_success_scrubs_args_used_logs_spans_and_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: list[object] = []

    @contextmanager
    def observed_span(*args: object, **kwargs: object):
        observed.append((args, kwargs))
        yield

    class Metric:
        def add(self, *args: object, **kwargs: object) -> None:
            observed.append((args, kwargs))

        def record(self, *args: object, **kwargs: object) -> None:
            observed.append((args, kwargs))

    monkeypatch.setattr(execution, "span", observed_span)
    monkeypatch.setattr(execution, "_MACRO_RUN", Metric())
    monkeypatch.setattr(execution, "_MACRO_RUN_DURATION", Metric())
    monkeypatch.setattr(
        execution,
        "log",
        types.SimpleNamespace(info=lambda *args, **kwargs: observed.append((args, kwargs))),
    )
    monkeypatch.setattr(execution, "load_macro", lambda _name: {"actions": []})
    monkeypatch.setattr(execution, "_push_status", AsyncMock())

    result = await execution.run_macro(
        _session(),
        "login",
        {"password": PASSWORD, "email": EMAIL, "flow": "ordinary"},
    )

    assert result["args_used"] == {
        "password": "<redacted>",
        "email": "<redacted>",
        "flow": "ordinary",
    }
    _assert_private(result)
    _assert_private(observed)


@pytest.mark.asyncio
async def test_runtime_scrubs_console_action_and_log_fields_before_recorder_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persisted: list[object] = []
    session = _session()
    session.recorder = types.SimpleNamespace(
        record=lambda action, **fields: persisted.append((action, fields)),
        record_control=lambda action, **fields: persisted.append((action, fields)),
    )

    async def dispatch(session: Any, *_args: Any, **_kwargs: Any) -> tuple[int, int]:
        session.recorder.record("console", text=f"failure {SOCIAL_ARGS['peer_email']}")
        session.recorder.record_control("action", value=SOCIAL_ARGS["peer_password"])
        return 1, 0

    monkeypatch.setattr(execution, "load_macro", lambda _name: {"actions": [{"action": "click"}]})
    monkeypatch.setattr(execution, "_dispatch_one", dispatch)
    monkeypatch.setattr(execution, "_push_status", AsyncMock())
    await execution._run_macro_impl(session, "social", SOCIAL_ARGS, slowmo_ms=0)
    rendered = repr(persisted)
    for value in SOCIAL_ARGS.values():
        if value != "ordinary message":
            assert value not in rendered


@pytest.mark.asyncio
async def test_exported_script_scrubs_runtime_error_and_persisted_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Page:
        async def fill(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError(f"browser rejected {PASSWORD} for {EMAIL}")

    class Browser:
        async def new_page(self) -> Page:
            return Page()

        async def close(self) -> None:
            return None

    class Chromium:
        async def launch(self, **_kwargs: Any) -> Browser:
            return Browser()

    class Playwright:
        chromium = Chromium()

    class Manager:
        async def __aenter__(self) -> Playwright:
            return Playwright()

        async def __aexit__(self, *_args: Any) -> None:
            return None

    async_api = types.ModuleType("playwright.async_api")
    async_api.async_playwright = lambda: Manager()  # type: ignore[attr-defined]
    package = types.ModuleType("playwright")
    package.async_api = async_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)

    source = render_macro_cli(
        name="private-export",
        macro={
            "parameters": ["password", "email"],
            "actions": [{"action": "fill", "selector": "#password", "value": "{{password}}"}],
        },
        args={"password": PASSWORD, "email": EMAIL},
    )
    assert PASSWORD not in source and EMAIL not in source
    module: dict[str, Any] = {"__name__": "private_export_test"}
    exec(compile(source, "<private-export>", "exec"), module)

    evidence = tmp_path / "evidence"
    with pytest.raises(RuntimeError) as caught:
        await module["run_private_export"](
            password=PASSWORD,
            email=EMAIL,
            evidence_dir=str(evidence),
        )

    _assert_private(caught.value)
    _assert_private(caught.value.__dict__)
    assert caught.value.__context__ is None
    persisted = "".join(path.read_text() for path in evidence.rglob("*") if path.is_file())
    _assert_private(persisted)


@pytest.mark.asyncio
async def test_exported_classified_macro_refuses_raw_screenshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "exported-raw.png"

    class Page:
        async def screenshot(self, *, path: str) -> None:
            Path(path).write_bytes(PASSWORD.encode())

    class Browser:
        async def new_page(self) -> Page:
            return Page()

        async def close(self) -> None:
            return None

    class Chromium:
        async def launch(self, **_kwargs: Any) -> Browser:
            return Browser()

    class Manager:
        async def __aenter__(self) -> object:
            return types.SimpleNamespace(chromium=Chromium())

        async def __aexit__(self, *_args: Any) -> None:
            return None

    async_api = types.ModuleType("playwright.async_api")
    async_api.async_playwright = lambda: Manager()  # type: ignore[attr-defined]
    package = types.ModuleType("playwright")
    package.async_api = async_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)

    source = render_macro_cli(
        name="private-screenshot-export",
        macro={
            "parameters": ["password"],
            "actions": [{"action": "screenshot", "path": str(target)}],
        },
    )
    module: dict[str, Any] = {"__name__": "private_screenshot_export"}
    exec(compile(source, "<private-screenshot-export>", "exec"), module)

    with pytest.raises(RuntimeError, match="classified macro screenshot"):
        await module["run_private_screenshot_export"](password=PASSWORD)

    assert not target.exists()


def test_versioned_classifier_covers_the_real_social_map_and_export_vocabulary() -> None:
    assert ARG_PRIVACY_CLASSIFIER_VERSION == 2
    for key in (*SOCIAL_ARGS, "auth", "credential", "api_key", "apikey", "access_key", "passphrase"):
        assert is_sensitive_arg_key(key), key
    assert not is_sensitive_arg_key("author")
    assert not is_sensitive_arg_key("peerage")
    assert execution._redact_args_for_response(SOCIAL_ARGS) == {key: "<redacted>" for key in SOCIAL_ARGS}


@pytest.mark.parametrize(
    "key",
    ["auth", "credential", "api_key", "apikey", "access_key", "passphrase", "peer_email"],
)
def test_export_never_embeds_any_runtime_sensitive_default(key: str) -> None:
    canary = f"A4-{key}-EXPORT-CANARY"
    source = render_macro_cli(
        name="private-export",
        macro={"parameters": [key], "actions": []},
        args={key: canary},
    )
    assert canary not in source


def test_recursive_scrub_covers_nested_leaves_keys_json_and_url_encodings() -> None:
    raw = 'p"a\\ss'
    nested = "A4-NESTED-CANARY"
    values = sensitive_arg_values({"credential": {"password": nested, "raw": raw}})
    assert nested in values and raw in values
    surfaces = {
        nested: json.dumps({"password": raw}),
        "url": urllib.parse.quote(raw, safe=""),
    }
    rendered = repr(scrub_sensitive_values(surfaces, values))
    assert nested not in rendered
    assert raw not in rendered
    assert json.dumps(raw)[1:-1] not in rendered
    assert urllib.parse.quote(raw, safe="") not in rendered
    assert scrub_sensitive_values("routine diagnostic", ("u",)) == "routine diagnostic"
    assert scrub_sensitive_values("subject=u", ("u",)) == "subject=<redacted>"


def test_sensitive_value_aliases_are_removed_from_success_args_and_export_defaults() -> None:
    raw = "A4-SENSITIVE-ALIAS-CANARY"
    args = {
        "password": raw,
        "display": raw,
        "payload": {"label": f"account={raw}", "ordinary": "public"},
    }

    redacted = execution._redact_args_for_response(args)
    source = render_macro_cli(
        name="private-alias-export",
        macro={"parameters": list(args), "actions": []},
        args=args,
    )

    assert raw not in repr(redacted)
    assert redacted["payload"]["ordinary"] == "public"
    assert raw not in source


def test_exported_runtime_args_redaction_removes_sensitive_value_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = types.ModuleType("playwright.async_api")
    async_api.async_playwright = lambda: None  # type: ignore[attr-defined]
    package = types.ModuleType("playwright")
    package.async_api = async_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)
    source = render_macro_cli(
        name="private-alias-runtime",
        macro={"parameters": ["password", "display"], "actions": []},
    )
    module: dict[str, Any] = {"__name__": "private_alias_runtime"}
    exec(compile(source, "<private-alias-runtime>", "exec"), module)
    raw = "A4-EXPORTED-RUNTIME-ALIAS-CANARY"

    redacted = module["_redact_args"]({"password": raw, "display": raw})

    assert raw not in repr(redacted)


def test_nested_sensitive_keys_are_found_below_ordinary_containers() -> None:
    secret = "A4-NESTED-ORDINARY-CONTAINER-CANARY"
    args = {"payload": {"profile": {"password": secret}, "display": "public"}}

    values = sensitive_arg_values(args)

    assert secret in values
    assert secret not in repr(scrub_sensitive_values({"detail": secret}, values))
    assert redact_args(args) == {"payload": {"profile": {"password": "<redacted>"}, "display": "public"}}


def test_scrub_normalizes_mixed_and_repeated_percent_encodings() -> None:
    raw = "xÿ/y"
    values = sensitive_arg_values({"password": raw})
    encoded = urllib.parse.quote(raw, safe="")
    mixed = encoded.replace("%C3", "%c3", 1)
    repeated = urllib.parse.quote(mixed, safe="")

    for rendered in (mixed, repeated):
        assert raw not in scrub_sensitive_values(rendered, values)
        assert rendered not in scrub_sensitive_values(rendered, values)


@pytest.mark.asyncio
@pytest.mark.parametrize("include_evidence", [False, True])
async def test_exported_success_closes_browser_exactly_once(
    include_evidence: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closes: list[str] = []

    class Page:
        pass

    class Browser:
        async def new_page(self) -> Page:
            return Page()

        async def close(self) -> None:
            closes.append("close")

    class Chromium:
        async def launch(self, **_kwargs: Any) -> Browser:
            return Browser()

    class Playwright:
        chromium = Chromium()

    class Manager:
        async def __aenter__(self) -> Playwright:
            return Playwright()

        async def __aexit__(self, *_args: Any) -> None:
            return None

    async_api = types.ModuleType("playwright.async_api")
    async_api.async_playwright = lambda: Manager()  # type: ignore[attr-defined]
    package = types.ModuleType("playwright")
    package.async_api = async_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)

    source = render_macro_cli(
        name="successful-export",
        macro={"actions": []},
        include_evidence=include_evidence,
    )
    module: dict[str, Any] = {"__name__": "successful_export_test"}
    exec(compile(source, "<successful-export>", "exec"), module)
    kwargs = {"evidence_dir": str(tmp_path / "evidence")} if include_evidence else {}

    assert await module["run_successful_export"](**kwargs) == {
        "executed": 0,
        "skipped": 0,
    }
    assert closes == ["close"]


@pytest.mark.asyncio
async def test_exported_new_page_failure_closes_browser_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closes: list[str] = []

    class Browser:
        async def new_page(self) -> object:
            raise RuntimeError("new page failed")

        async def close(self) -> None:
            closes.append("close")

    class Chromium:
        async def launch(self, **_kwargs: Any) -> Browser:
            return Browser()

    class Manager:
        async def __aenter__(self) -> object:
            return types.SimpleNamespace(chromium=Chromium())

        async def __aexit__(self, *_args: Any) -> None:
            return None

    async_api = types.ModuleType("playwright.async_api")
    async_api.async_playwright = lambda: Manager()  # type: ignore[attr-defined]
    package = types.ModuleType("playwright")
    package.async_api = async_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)
    source = render_macro_cli(name="new-page-failure", macro={"actions": []})
    module: dict[str, Any] = {"__name__": "new_page_failure"}
    exec(compile(source, "<new-page-failure>", "exec"), module)

    with pytest.raises(RuntimeError, match="new page failed"):
        await module["run_new_page_failure"]()
    assert closes == ["close"]


def test_exported_classifier_matches_nested_and_encoded_runtime_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = types.ModuleType("playwright.async_api")
    async_api.async_playwright = lambda: None  # type: ignore[attr-defined]
    package = types.ModuleType("playwright")
    package.async_api = async_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)
    source = render_macro_cli(name="privacy-parity", macro={"actions": []})
    module: dict[str, Any] = {"__name__": "privacy_parity"}
    exec(compile(source, "<privacy-parity>", "exec"), module)
    raw = "xÿ/y"
    args = {"payload": {"profile": {"password": raw}, "display": "public"}}
    mixed = urllib.parse.quote(raw, safe="").replace("%C3", "%c3", 1)
    repeated = urllib.parse.quote(mixed, safe="")

    assert module["_ARG_PRIVACY_CLASSIFIER_VERSION"] == ARG_PRIVACY_CLASSIFIER_VERSION
    assert module["_redact_args"](args) == redact_args(args)
    exported_values = tuple(module["_sensitive_arg_values"](args))
    assert exported_values == sensitive_arg_values(args)
    assert module["_redact_value"](repeated, list(exported_values)) == scrub_sensitive_values(repeated, exported_values)


@pytest.mark.asyncio
async def test_before_action_boundary_runs_after_slowmo_and_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    session = _session()
    session._octowright_before_macro_action = lambda **_kwargs: events.append("boundary")

    async def push(*_args: Any, **_kwargs: Any) -> None:
        events.append("status")

    async def sleep(_seconds: float) -> None:
        events.append("slowmo")

    async def dispatch(*_args: Any, **_kwargs: Any) -> tuple[int, int]:
        events.append("dispatch")
        return 1, 0

    monkeypatch.setattr(execution, "_push_status", push)
    monkeypatch.setattr(execution.asyncio, "sleep", sleep)
    monkeypatch.setattr(execution, "dispatch_plain_action", dispatch)

    assert await execution._dispatch_one(
        session,
        {"action": "click", "selector": "#submit"},
        invocation_stack=["social"],
        slowmo_ms=1,
    ) == (1, 0)
    assert events == ["status", "slowmo", "boundary", "dispatch"]


@pytest.mark.asyncio
async def test_classified_explicit_screenshot_requires_privacy_handler(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _session()
    session.screenshot = AsyncMock()

    async def unsafe_diagnostic(**_kwargs: Any) -> dict[str, Any]:
        leaked = tmp_path / "diagnostic.png"
        leaked.write_bytes(PASSWORD.encode())
        return {"screenshot": str(leaked)}

    session.diagnostic_bundle = AsyncMock(side_effect=unsafe_diagnostic)
    monkeypatch.setattr(
        execution,
        "load_macro",
        lambda _name: {"actions": [{"action": "screenshot", "path": "/tmp/raw.png"}]},
    )
    monkeypatch.setattr(execution, "_push_status", AsyncMock())

    with pytest.raises(RuntimeError) as caught:
        await execution._run_macro_impl(session, "private", {"password": PASSWORD}, slowmo_ms=0)

    session.screenshot.assert_not_awaited()
    session.diagnostic_bundle.assert_not_awaited()
    assert list(tmp_path.rglob("*.png")) == []
    assert "privacy handler" in str(caught.value)


@pytest.mark.asyncio
async def test_classified_explicit_screenshot_uses_only_privacy_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    session.screenshot = AsyncMock()
    handled: list[dict[str, Any]] = []

    async def privacy_handler(*, action: dict[str, Any], sensitive_values: tuple[str, ...]) -> tuple[int, int]:
        handled.append({"action": action, "sensitive_values": sensitive_values})
        return 1, 0

    session._octowright_sensitive_screenshot_handler = privacy_handler
    session._octowright_sensitive_screenshot_authority = True
    monkeypatch.setattr(
        execution,
        "load_macro",
        lambda _name: {"actions": [{"action": "screenshot", "path": "/tmp/raw.png"}]},
    )
    monkeypatch.setattr(execution, "_push_status", AsyncMock())

    result = await execution._run_macro_impl(session, "private", {"password": PASSWORD}, slowmo_ms=0)

    assert result["executed"] == 1
    assert handled == [
        {
            "action": {"action": "screenshot", "path": "/tmp/raw.png"},
            "sensitive_values": (PASSWORD,),
        }
    ]
    session.screenshot.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("container", ["conditional", "macro_call"])
async def test_nested_classified_screenshot_cannot_bypass_privacy_handler(
    monkeypatch: pytest.MonkeyPatch,
    container: str,
) -> None:
    session = _session()
    session.screenshot = AsyncMock()
    monkeypatch.setattr(execution, "_push_status", AsyncMock())

    if container == "conditional":
        async def dispatch_conditional(
            nested_session: Any, _action: dict[str, Any], recurse: Any
        ) -> tuple[int, int]:
            return await recurse(
                nested_session,
                {"action": "screenshot", "path": "/tmp/raw-nested.png"},
            )

        monkeypatch.setattr(execution.conditional, "dispatch_conditional", dispatch_conditional)
        action = {"action": "try"}
    else:
        monkeypatch.setattr(
            execution,
            "load_macro",
            lambda _name: {
                "actions": [{"action": "screenshot", "path": "/tmp/raw-nested.png"}]
            },
        )
        action = {"action": "macro_call", "name": "nested", "args": {}}

    with pytest.raises(RuntimeError, match="privacy handler"):
        await execution._dispatch_one(
            session,
            action,
            invocation_stack=["outer"],
            sensitive_values=(PASSWORD,),
        )

    session.screenshot.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("secondary", ["diagnostic", "healing", "failed_requests"])
async def test_secondary_diagnostic_failures_never_retain_raw_exception_context(secondary: str) -> None:
    session = _session()
    if secondary == "diagnostic":
        session.diagnostic_bundle = AsyncMock(side_effect=OSError("diagnostic failed"))
    if secondary == "failed_requests":
        session.get_network_requests = MagicMock(side_effect=OSError("requests failed"))

    async def fail_dispatch(*_args: Any, **_kwargs: Any) -> tuple[int, int]:
        raise ValueError(f"dispatch {PASSWORD}")

    suggestion = (
        AsyncMock(side_effect=OSError("healing failed")) if secondary == "healing" else AsyncMock(return_value=None)
    )
    with (
        patch.object(execution, "_dispatch_one", fail_dispatch),
        patch.object(execution, "load_macro", return_value={"actions": [{"action": "click"}]}),
        patch.object(execution, "_push_status", AsyncMock()),
        patch.object(execution, "_suggest_fix", suggestion),
        pytest.raises(RuntimeError) as caught,
    ):
        await execution._run_macro_impl(session, "private", {"password": PASSWORD, "email": EMAIL}, slowmo_ms=0)
    _assert_private(caught.value)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize("secondary", ["evidence", "close"])
async def test_export_cleanup_failures_never_retain_raw_browser_exception(
    secondary: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Page:
        async def fill(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError(f"browser rejected {PASSWORD}")

    class Browser:
        async def new_page(self) -> Page:
            return Page()

        async def close(self) -> None:
            if secondary == "close":
                raise OSError(f"close failed near {PASSWORD}")

    class Chromium:
        async def launch(self, **_kwargs: Any) -> Browser:
            return Browser()

    class Manager:
        async def __aenter__(self) -> object:
            return types.SimpleNamespace(chromium=Chromium())

        async def __aexit__(self, *_args: Any) -> None:
            return None

    async_api = types.ModuleType("playwright.async_api")
    async_api.async_playwright = lambda: Manager()  # type: ignore[attr-defined]
    package = types.ModuleType("playwright")
    package.async_api = async_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)

    source = render_macro_cli(
        name="cleanup-private",
        macro={
            "parameters": ["password"],
            "actions": [{"action": "fill", "selector": "#password", "value": "{{password}}"}],
        },
    )
    module: dict[str, Any] = {"__name__": "cleanup_private"}
    exec(compile(source, "<cleanup-private>", "exec"), module)
    if secondary == "evidence":
        module["_Evidence"].finish = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError(f"evidence failed near {PASSWORD}")
        )
    with pytest.raises(RuntimeError) as caught:
        await module["run_cleanup_private"](password=PASSWORD, evidence_dir=str(tmp_path / "evidence"))
    _assert_private(caught.value)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
