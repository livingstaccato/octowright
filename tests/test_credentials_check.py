# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Exercise tests for persona credentials pre-flight.

Covers personas.check_credentials (pure) and the
persona_credentials_check MCP tool.

Every test monkey-patches the environment / subprocess surface so nothing
actually shells out to `op` or reads real secrets.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from octowright import personas as _personas
from octowright import server as _server

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _persona(creds: dict[str, str], name: str = "dante") -> _personas.Persona:
    return _personas.Persona(name=name, credentials=creds)


def _patch_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    *,
    returncode: int = 0,
    stdout: str = "secret",
    stderr: str = "",
) -> None:
    """Replace subprocess.run so credential *_cmd references don't shell out."""

    def fake_run(*args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr("subprocess.run", fake_run)


# ---------------------------------------------------------------------------
# credential-name derivation
# ---------------------------------------------------------------------------


class TestCredentialNameDerivation:
    def test_env_and_cmd_suffixes_both_recognised(self) -> None:
        names = _personas._credential_names(
            _persona(
                {
                    "email_env": "X_EMAIL",
                    "password_cmd": "op read op://x",
                }
            )
        )
        assert names == ["email", "password"]

    def test_both_forms_for_same_name_collapse(self) -> None:
        """If a persona has both *_env and *_cmd for the same field, it counts once."""
        names = _personas._credential_names(
            _persona(
                {
                    "password_env": "X_PW",
                    "password_cmd": "op read op://x",
                }
            )
        )
        assert names == ["password"]

    def test_unrecognised_suffixes_ignored(self) -> None:
        """Keys that don't end in _env or _cmd are metadata, not credentials."""
        names = _personas._credential_names(
            _persona(
                {
                    "email_env": "X",
                    "note": "not a credential",
                    "some_other_key": "x",
                }
            )
        )
        assert names == ["email"]

    def test_empty_credentials_returns_empty_list(self) -> None:
        assert _personas._credential_names(_persona({})) == []


# ---------------------------------------------------------------------------
# check_credentials
# ---------------------------------------------------------------------------


def test_check_credentials_all_resolve_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_EMAIL", "a@b.com")
    monkeypatch.setenv("X_TOKEN", "t1")
    persona = _persona({"email_env": "X_EMAIL", "token_env": "X_TOKEN"})

    report = _personas.check_credentials(persona)

    assert report["ok"] is True
    assert report["persona"] == "dante"
    assert report["summary"] == "2/2 credentials resolved"
    assert len(report["checked"]) == 2
    for c in report["checked"]:
        assert c["ok"] is True
        assert c["error"] is None
        assert c["source"] == "env"
        # Reference is the env var NAME, not the resolved value (no secret leak).
        assert c["reference"] in ("X_EMAIL", "X_TOKEN")


def test_check_credentials_missing_env_var_fails_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_VAR", raising=False)
    persona = _persona({"password_env": "MISSING_VAR"})

    report = _personas.check_credentials(persona)

    assert report["ok"] is False
    assert report["summary"].startswith("0/1 credentials resolved")
    assert "password" in report["summary"]
    c = report["checked"][0]
    assert c["ok"] is False
    assert "MISSING_VAR is unset" in c["error"]


def test_check_credentials_cmd_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_subprocess(monkeypatch, returncode=0, stdout="resolved-secret")
    persona = _persona({"password_cmd": "op read op://foo"})

    report = _personas.check_credentials(persona)

    assert report["ok"] is True
    c = report["checked"][0]
    assert c["source"] == "cmd"
    assert c["reference"] == "op read op://foo"
    # Secret value must NEVER leak into the report — only pass/fail.
    assert "resolved-secret" not in str(report)


def test_check_credentials_cmd_nonzero_exit_reports_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_subprocess(monkeypatch, returncode=1, stderr="not authorized")
    persona = _persona({"password_cmd": "op read op://foo"})

    report = _personas.check_credentials(persona)

    assert report["ok"] is False
    c = report["checked"][0]
    assert c["ok"] is False
    assert "cmd exited 1" in c["error"]
    assert "not authorized" in c["error"]


def test_check_credentials_cmd_wins_when_both_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """If both _env and _cmd exist, _cmd is used (matches resolve_credential)."""
    _patch_subprocess(monkeypatch, returncode=0, stdout="from-cmd")
    monkeypatch.setenv("X_PW", "from-env")
    persona = _persona({"password_env": "X_PW", "password_cmd": "some-cmd"})

    report = _personas.check_credentials(persona)

    assert len(report["checked"]) == 1
    c = report["checked"][0]
    assert c["source"] == "cmd"
    assert c["reference"] == "some-cmd"
    assert c["ok"] is True


def test_check_credentials_mixed_pass_and_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_EMAIL", "a@b.com")
    monkeypatch.delenv("MISSING", raising=False)
    _patch_subprocess(monkeypatch, returncode=0, stdout="ok")
    persona = _persona(
        {
            "email_env": "X_EMAIL",  # pass
            "token_env": "MISSING",  # fail
            "password_cmd": "ok-cmd",  # pass
        }
    )

    report = _personas.check_credentials(persona)

    assert report["ok"] is False
    assert "2/3 credentials resolved" in report["summary"]
    assert "token" in report["summary"]  # failing field named
    by_name = {c["name"]: c for c in report["checked"]}
    assert by_name["email"]["ok"] is True
    assert by_name["password"]["ok"] is True
    assert by_name["token"]["ok"] is False


def test_check_credentials_empty_persona_has_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    persona = _persona({})
    report = _personas.check_credentials(persona)
    assert report["checked"] == []
    # No creds declared → ok is False (nothing to confirm), but summary says so.
    assert report["ok"] is False
    assert "no credentials" in report["summary"]


# ---------------------------------------------------------------------------
# MCP tool surface
# ---------------------------------------------------------------------------


async def test_mcp_tool_loads_persona_and_runs_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    """The tool takes a persona NAME, loads it from disk, and returns the report."""
    import yaml

    pdir = tmp_path / "profiles"  # type: ignore[operator]
    pdir.mkdir()
    (pdir / "dante").mkdir()
    (pdir / "dante" / "profile.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "dante",
                "credentials": {"email_env": "X_EMAIL"},
            }
        )
    )
    monkeypatch.setattr(_personas, "PROFILES_DIR", pdir)
    monkeypatch.setenv("X_EMAIL", "resolved")

    report = await _server.persona_credentials_check(name="dante")

    assert report["persona"] == "dante"
    assert report["ok"] is True
    assert len(report["checked"]) == 1
