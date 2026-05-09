# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.cli.persona."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from octowright.cli._root import cli

# ─── persona list ────────────────────────────────────────────────────────────


class TestPersonaList:
    def test_lists_personas_with_engines(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Each row is rendered with name, engines, display_name."""
        from octowright import personas as _p

        rows = [
            {"name": "alice", "engines": ["chromium", "firefox"], "display_name": "Alice"},
            {"name": "bob", "engines": ["webkit"], "display_name": None},
        ]
        monkeypatch.setattr(_p, "list_personas", lambda: rows)
        result = CliRunner().invoke(cli, ["persona", "list"])
        assert result.exit_code == 0
        assert "alice" in result.output
        assert "chromium,firefox" in result.output
        assert "Alice" in result.output
        assert "bob" in result.output

    def test_empty_engines_shows_dash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty engines list → '-' placeholder."""
        from octowright import personas as _p

        monkeypatch.setattr(_p, "list_personas", lambda: [{"name": "a", "engines": [], "display_name": "A"}])
        result = CliRunner().invoke(cli, ["persona", "list"])
        assert result.exit_code == 0
        assert "engines=-" in result.output  # the dash for empty engines

    def test_missing_display_name_shows_blank(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """display_name missing → blank string used."""
        from octowright import personas as _p

        monkeypatch.setattr(_p, "list_personas", lambda: [{"name": "a", "engines": ["chromium"]}])
        result = CliRunner().invoke(cli, ["persona", "list"])
        assert result.exit_code == 0
        # Just ensure it runs and includes name + engine.
        assert "a" in result.output
        assert "chromium" in result.output

    def test_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No personas → exits 0 with no row output."""
        from octowright import personas as _p

        monkeypatch.setattr(_p, "list_personas", lambda: [])
        result = CliRunner().invoke(cli, ["persona", "list"])
        assert result.exit_code == 0


# ─── persona show ────────────────────────────────────────────────────────────


class TestPersonaShow:
    def test_renders_each_persona_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Show prints name/display_name/default_url/default_macros/credentials/app."""
        from octowright import personas as _p

        persona = SimpleNamespace(
            name="alice",
            display_name="Alice",
            default_url="https://x",
            default_macros=["login"],
            credentials={"email": {"env": "EMAIL"}},
            app={"foo": "bar"},
        )
        monkeypatch.setattr(_p, "load_persona", lambda _name: persona)
        result = CliRunner().invoke(cli, ["persona", "show", "alice"])
        assert result.exit_code == 0
        assert "alice" in result.output
        assert "Alice" in result.output
        assert "https://x" in result.output
        assert "['login']" in result.output
        assert "['email']" in result.output
        assert "{'foo': 'bar'}" in result.output

    def test_propagates_load_persona_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If load_persona raises, the click runner catches it."""
        from octowright import personas as _p

        def boom(_name: str) -> Any:
            raise FileNotFoundError("nope")

        monkeypatch.setattr(_p, "load_persona", boom)
        result = CliRunner().invoke(cli, ["persona", "show", "missing"])
        assert result.exit_code != 0


# ─── persona create ──────────────────────────────────────────────────────────


class TestPersonaCreate:
    def test_creates_persona_with_defaults(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        """Default --display and --url unset → kwargs are None."""
        from octowright import personas as _p

        captured: dict[str, Any] = {}

        def fake_create(name: str, *, display_name: str | None, default_url: str | None) -> Any:
            captured["name"] = name
            captured["display_name"] = display_name
            captured["default_url"] = default_url
            return tmp_path / "alice"

        monkeypatch.setattr(_p, "create_persona", fake_create)
        result = CliRunner().invoke(cli, ["persona", "create", "alice"])
        assert result.exit_code == 0
        assert captured == {"name": "alice", "display_name": None, "default_url": None}
        assert "created" in result.output

    def test_creates_persona_with_options(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        """--display and --url are wired through verbatim."""
        from octowright import personas as _p

        captured: dict[str, Any] = {}

        def fake_create(name: str, *, display_name: str | None, default_url: str | None) -> Any:
            captured["display_name"] = display_name
            captured["default_url"] = default_url
            return tmp_path / "alice"

        monkeypatch.setattr(_p, "create_persona", fake_create)
        result = CliRunner().invoke(cli, ["persona", "create", "alice", "--display", "Alice A.", "--url", "https://x"])
        assert result.exit_code == 0
        assert captured == {"display_name": "Alice A.", "default_url": "https://x"}

    def test_exists_error_returns_exit_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FileExistsError → SystemExit(1) with message on stderr."""
        from octowright import personas as _p

        def boom(*_args: Any, **_kw: Any) -> Any:
            raise FileExistsError("persona 'alice' already exists")

        monkeypatch.setattr(_p, "create_persona", boom)
        result = CliRunner().invoke(cli, ["persona", "create", "alice"])
        assert result.exit_code == 1
        # Click captures stderr into result.output by default.
        assert "already exists" in result.output


# ─── persona delete ──────────────────────────────────────────────────────────


class TestPersonaDelete:
    def test_calls_delete_persona(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        """delete_persona path printed back to user."""
        from octowright import profiles as _profiles

        target = tmp_path / "alice"

        def fake_delete(name: str) -> Any:
            return target

        monkeypatch.setattr(_profiles, "delete_persona", fake_delete)
        result = CliRunner().invoke(cli, ["persona", "delete", "alice"])
        assert result.exit_code == 0
        assert "deleted" in result.output
        assert str(target) in result.output

    def test_propagates_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If delete_persona raises, the runner surfaces non-zero exit."""
        from octowright import profiles as _profiles

        def boom(_name: str) -> Any:
            raise FileNotFoundError("nope")

        monkeypatch.setattr(_profiles, "delete_persona", boom)
        result = CliRunner().invoke(cli, ["persona", "delete", "missing"])
        assert result.exit_code != 0
