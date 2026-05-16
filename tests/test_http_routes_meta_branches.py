# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.http.routes.meta.

Covers:
- list_personas / list_macros endpoint shape + field projection
- macro_repair_preview / detail / validate / update endpoints
- persona_sizes_endpoint (du-shell branch + missing dir + empty dir + parse failure)
- persona_detail_endpoint (yaml read + per-engine disk usage)
- persona_update_endpoint (404 + invalid YAML + ok-path)
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient

from octowright import http as _http
from octowright.http import state as _http_state
from octowright.server import _state


class _FakePool:
    def maybe_get(self, instance_id: str) -> Any:
        return None

    def has_session(self, instance_id: str) -> bool:
        return False

    def iter_sessions(self) -> tuple:
        return ()


class _FakeScenarioPool:
    def list_live(self) -> list[dict[str, Any]]:
        return []


@pytest.fixture
def isolated_profiles(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect PROFILES_DIR to a writable tmp dir."""
    p = tmp_path / "profiles"
    p.mkdir()
    from octowright.http.routes import meta as _meta_mod

    monkeypatch.setattr(_meta_mod, "PROFILES_DIR", p)
    return p


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    rec = tmp_path / "recordings"
    rec.mkdir()
    monkeypatch.setattr(_http_state, "RECORDINGS_DIR", rec)
    monkeypatch.setattr(_state, "pool", _FakePool())
    monkeypatch.setattr(_state, "scenario_pool", _FakeScenarioPool())
    return TestClient(_http.build_app())


# ─── list_personas / list_macros: projection ────────────────────────────────


class TestListPersonasShape:
    def test_projects_only_documented_fields(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """Only name/display_name/engines/last_used flow through to JSON output."""
        rows = [
            {
                "name": "cosmo",
                "display_name": "Crumpet Cosmo",
                "engines": ["chromium"],
                "last_used": "2026-01-01",
                "extraneous_field": "should-be-dropped",
            }
        ]
        monkeypatch.setattr(_http_state._personas, "list_personas", lambda: rows)
        result = client.get("/api/personas")
        assert result.status_code == 200
        body = result.json()
        assert body == [
            {
                "name": "cosmo",
                "display_name": "Crumpet Cosmo",
                "engines": ["chromium"],
                "last_used": "2026-01-01",
            }
        ]

    def test_defaults_for_missing_fields(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing display_name/engines/last_used → None / [] / None."""
        rows = [{"name": "ziggy"}]
        monkeypatch.setattr(_http_state._personas, "list_personas", lambda: rows)
        body = client.get("/api/personas").json()
        assert body == [{"name": "ziggy", "display_name": None, "engines": [], "last_used": None}]

    def test_empty_list(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """No personas → empty array."""
        monkeypatch.setattr(_http_state._personas, "list_personas", lambda: [])
        assert client.get("/api/personas").json() == []


class TestListMacrosShape:
    def test_projects_only_documented_fields(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """Only name/description/parameters/updated_at."""
        rows = [
            {
                "name": "login",
                "description": "do login",
                "parameters": ["email"],
                "updated_at": "2026-01-01",
                "extra": True,
            }
        ]
        monkeypatch.setattr(_http_state._macros, "list_macros", lambda: rows)
        body = client.get("/api/macros").json()
        assert body == [
            {"name": "login", "description": "do login", "parameters": ["email"], "updated_at": "2026-01-01"}
        ]

    def test_defaults(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing description/parameters/updated_at → None / [] / None."""
        monkeypatch.setattr(_http_state._macros, "list_macros", lambda: [{"name": "m"}])
        body = client.get("/api/macros").json()
        assert body == [{"name": "m", "description": None, "parameters": [], "updated_at": None}]


# ─── macro_repair_preview ───────────────────────────────────────────────────


class TestMacroRepairPreview:
    def test_returns_preview_json(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """Happy path: dispatched to _macros.repair_preview."""
        preview = {"macro": "login", "suggestions": []}
        monkeypatch.setattr(_http_state._macros, "repair_preview", lambda _name: preview)
        result = client.get("/api/macros/login/repair_preview")
        assert result.status_code == 200
        assert result.json() == preview

    def test_404_on_missing_macro(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """FileNotFoundError → 404 with shaped error body."""

        def boom(_name: str) -> Any:
            raise FileNotFoundError("nope")

        monkeypatch.setattr(_http_state._macros, "repair_preview", boom)
        result = client.get("/api/macros/missing/repair_preview")
        assert result.status_code == 404
        assert "missing" in result.json()["error"]


# ─── macro_detail ───────────────────────────────────────────────────────────


class TestMacroDetail:
    def test_returns_macro_json(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """Happy path returns the loaded macro dict."""
        macro = {"name": "login", "actions": [{"action": "click", "selector": "#btn"}]}
        monkeypatch.setattr(_http_state._macros, "load_macro", lambda _name: macro)
        result = client.get("/api/macros/login")
        assert result.status_code == 200
        assert result.json() == macro

    def test_404_on_missing(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing macro → 404 + error key."""

        def boom(_name: str) -> Any:
            raise FileNotFoundError("nope")

        monkeypatch.setattr(_http_state._macros, "load_macro", boom)
        result = client.get("/api/macros/missing")
        assert result.status_code == 404


# ─── macro_validate ─────────────────────────────────────────────────────────


class TestMacroValidate:
    def test_well_formed_macro_ok_true(self, client: TestClient) -> None:
        """A valid macro returns ok=True with no error issues."""
        body = {"macro": {"name": "x", "actions": [{"action": "navigate", "url": "https://x"}]}}
        r = client.post("/api/macros/x/validate", json=body)
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["error_count"] == 0
        assert "issues" in data
        assert "issue_count" in data

    def test_invalid_macro_ok_false(self, client: TestClient) -> None:
        """Macro with errors → ok=False + non-zero error_count."""
        body = {"macro": {"name": "x", "actions": [{"action": "click"}]}}  # missing required selector
        r = client.post("/api/macros/x/validate", json=body)
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is False
        assert data["error_count"] >= 1

    def test_payload_missing_macro_key_400(self, client: TestClient) -> None:
        """Body lacking 'macro' key → 400 with explanatory error."""
        r = client.post("/api/macros/x/validate", json={"other": True})
        assert r.status_code == 400
        assert "macro" in r.json()["error"]

    def test_macro_not_dict_400(self, client: TestClient) -> None:
        """`macro` not a dict → 400."""
        r = client.post("/api/macros/x/validate", json={"macro": "not-a-dict"})
        assert r.status_code == 400

    def test_invalid_json_body_400(self, client: TestClient) -> None:
        """Bad JSON body returns the canonical 400 from _read_json_body."""
        r = client.post("/api/macros/x/validate", content="{ not json", headers={"content-type": "application/json"})
        assert r.status_code == 400


# ─── macro_update ────────────────────────────────────────────────────────────


class TestMacroUpdate:
    def test_writes_macro_on_valid(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """Valid macro → write_macro called + ok=True body."""
        captured: dict[str, Any] = {}

        def fake_write(*, name: str, macro: dict[str, Any]) -> Path:
            captured["name"] = name
            captured["macro"] = macro
            return Path("/tmp/x.json")

        monkeypatch.setattr(_http_state._macros, "write_macro", fake_write)
        monkeypatch.setattr(_http_state._macros, "load_macro", lambda n: {"name": n, "actions": []})
        # Mock the publish_dashboard_invalidation to avoid cross-test SSE state.
        from octowright.http.routes import meta as _meta_mod

        monkeypatch.setattr(_meta_mod, "publish_dashboard_invalidation", AsyncMock(return_value=None))

        r = client.put(
            "/api/macros/login",
            json={"macro": {"name": "login", "actions": [{"action": "navigate", "url": "https://x"}]}},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert captured["name"] == "login"

    def test_rejects_macro_with_validation_errors(self, client: TestClient) -> None:
        """Macro with lint errors → 400 + 'macro validation failed'."""
        r = client.put("/api/macros/x", json={"macro": {"name": "x", "actions": [{"action": "click"}]}})
        assert r.status_code == 400
        assert "validation failed" in r.json()["error"]
        # Body still includes the issues list.
        assert "issues" in r.json()

    def test_404_when_payload_macro_not_dict(self, client: TestClient) -> None:
        """`macro` not a dict → 400."""
        r = client.put("/api/macros/x", json={"macro": [1, 2]})
        assert r.status_code == 400


# ─── persona_sizes_endpoint ─────────────────────────────────────────────────


class TestPersonaSizes:
    def test_missing_dir_returns_empty(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """PROFILES_DIR.exists() == False → empty dict."""
        from octowright.http.routes import meta as _meta_mod

        monkeypatch.setattr(_meta_mod, "PROFILES_DIR", tmp_path / "missing")
        assert client.get("/api/personas/sizes").json() == {}

    def test_empty_dir_returns_empty(self, client: TestClient, isolated_profiles: Path) -> None:
        """Existing dir with no entries → empty dict."""
        assert client.get("/api/personas/sizes").json() == {}

    def test_parses_du_output(
        self, client: TestClient, isolated_profiles: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tab-separated 'kb<TAB>path' lines → {persona: bytes}."""
        (isolated_profiles / "cosmo").mkdir()
        (isolated_profiles / "ziggy").mkdir()
        # Mock subprocess.run to return canned du output.
        result = SimpleNamespace(stdout=f"100\t{isolated_profiles / 'cosmo'}\n200\t{isolated_profiles / 'ziggy'}\n")
        monkeypatch.setattr(_http_state.subprocess, "run", MagicMock(return_value=result))
        body = client.get("/api/personas/sizes").json()
        assert body == {"cosmo": 100 * 1024, "ziggy": 200 * 1024}

    def test_skips_malformed_du_lines(
        self, client: TestClient, isolated_profiles: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Lines without TAB or with non-int size silently skipped."""
        (isolated_profiles / "cosmo").mkdir()
        result = SimpleNamespace(stdout=f"not-a-number\t{isolated_profiles / 'cosmo'}\nno-tab-line\n")
        monkeypatch.setattr(_http_state.subprocess, "run", MagicMock(return_value=result))
        assert client.get("/api/personas/sizes").json() == {}

    def test_subprocess_failure_returns_empty(
        self, client: TestClient, isolated_profiles: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exception (e.g. timeout) is logged + empty dict returned."""
        (isolated_profiles / "cosmo").mkdir()
        monkeypatch.setattr(
            _http_state.subprocess, "run", MagicMock(side_effect=subprocess.TimeoutExpired(cmd="du", timeout=15))
        )
        assert client.get("/api/personas/sizes").json() == {}

    def test_du_runs_in_worker_thread_not_event_loop(
        self,
        client: TestClient,
        isolated_profiles: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A slow ``du`` must not block the asyncio event loop. If the
        handler ever regresses to a synchronous ``subprocess.run(...)``,
        a fake that sleeps inside the call would freeze the loop and the
        request would only finish after that sleep — but more importantly,
        the call would happen on the loop thread instead of a worker.

        Capture the thread the fake ``run`` executes on and assert it
        differs from the test's own thread, which is the same thread the
        TestClient drives the loop on.
        """
        import threading

        (isolated_profiles / "dante-pemberton").mkdir()
        observed: dict[str, int] = {}

        def fake_run(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
            observed["thread_id"] = threading.get_ident()
            return SimpleNamespace(stdout=f"42\t{isolated_profiles / 'dante-pemberton'}\n")

        monkeypatch.setattr(_http_state.subprocess, "run", fake_run)

        r = client.get("/api/personas/sizes")
        assert r.status_code == 200
        assert r.json() == {"dante-pemberton": 42 * 1024}
        assert "thread_id" in observed
        assert observed["thread_id"] != threading.get_ident()


# ─── persona_detail_endpoint ────────────────────────────────────────────────


class TestPersonaDetail:
    def test_404_on_missing(self, client: TestClient, isolated_profiles: Path) -> None:
        """No yaml file → 404."""
        r = client.get("/api/personas/missing")
        assert r.status_code == 404

    def test_returns_yaml_and_disk_breakdown(self, client: TestClient, isolated_profiles: Path) -> None:
        """yaml + per-engine bytes + total."""
        p = isolated_profiles / "cosmo"
        p.mkdir()
        yaml_text = "name: cosmo\nemoji: 🦄\n"
        # write_bytes() avoids Windows' default '\n' -> '\r\n' translation
        # so disk_bytes (counted by stat()) matches the utf-8 byte length.
        (p / "profile.yaml").write_bytes(yaml_text.encode("utf-8"))
        # Add a chromium dir with a 50-byte file.
        chr_dir = p / "chromium"
        chr_dir.mkdir()
        (chr_dir / "Cookies").write_bytes(b"x" * 50)
        body = client.get("/api/personas/cosmo").json()
        assert body["name"] == "cosmo"
        assert body["yaml"] == yaml_text
        assert body["engine_bytes"]["chromium"] == 50
        assert body["disk_bytes"] == 50 + len(yaml_text.encode("utf-8"))

    def test_engines_without_dirs_omitted(self, client: TestClient, isolated_profiles: Path) -> None:
        """Engines whose dir doesn't exist are NOT in engine_bytes."""
        p = isolated_profiles / "cosmo"
        p.mkdir()
        (p / "profile.yaml").write_text("name: cosmo\n")
        # No engine dirs created.
        body = client.get("/api/personas/cosmo").json()
        assert body["engine_bytes"] == {}


# ─── persona_update_endpoint ────────────────────────────────────────────────


class TestPersonaUpdate:
    def test_404_on_missing_yaml(self, client: TestClient, isolated_profiles: Path) -> None:
        """Missing yaml file → 404."""
        r = client.put("/api/personas/missing", json={"yaml": "name: m\n"})
        assert r.status_code == 404

    def test_yaml_not_string_400(self, client: TestClient, isolated_profiles: Path) -> None:
        """yaml field isn't a str → 400."""
        p = isolated_profiles / "cosmo"
        p.mkdir()
        (p / "profile.yaml").write_text("name: cosmo\n")
        r = client.put("/api/personas/cosmo", json={"yaml": ["not", "a", "string"]})
        assert r.status_code == 400

    def test_invalid_yaml_400(self, client: TestClient, isolated_profiles: Path) -> None:
        """YAML parser raises → 400 with message."""
        p = isolated_profiles / "cosmo"
        p.mkdir()
        (p / "profile.yaml").write_text("name: cosmo\n")
        r = client.put("/api/personas/cosmo", json={"yaml": "key: : : :"})
        assert r.status_code == 400
        assert "invalid YAML" in r.json()["error"]

    def test_writes_and_publishes(
        self, client: TestClient, isolated_profiles: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Valid YAML → file rewritten + publish_dashboard_invalidation('personas')."""
        p = isolated_profiles / "cosmo"
        p.mkdir()
        yaml_path = p / "profile.yaml"
        yaml_path.write_text("name: cosmo\n")
        published: list[str] = []
        from octowright.http.routes import meta as _meta_mod

        async def fake_publish(scope: str) -> None:
            published.append(scope)

        monkeypatch.setattr(_meta_mod, "publish_dashboard_invalidation", fake_publish)
        new_yaml = "name: cosmo\ndefault_url: https://x\n"
        r = client.put("/api/personas/cosmo", json={"yaml": new_yaml})
        assert r.status_code == 200
        assert yaml_path.read_text() == new_yaml
        assert published == ["personas"]

    def test_invalid_json_body_400(self, client: TestClient, isolated_profiles: Path) -> None:
        """Body must be JSON; non-JSON → 400."""
        p = isolated_profiles / "cosmo"
        p.mkdir()
        (p / "profile.yaml").write_text("name: cosmo\n")
        r = client.put("/api/personas/cosmo", content="{ broken", headers={"content-type": "application/json"})
        assert r.status_code == 400
