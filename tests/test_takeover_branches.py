# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.takeover (competing-MCP detection
and reversible-disable rewrites of user config files).

The existing tests/test_takeover.py covers the happy paths; this file
covers the branches/error returns/internal helpers it didn't:
- Detection dataclass shape pin
- _load_json edge cases (OSError, non-dict roots, scalars/lists)
- _extract_mcp_servers / _extract_project_overrides on malformed input
- _command_string non-dict + url + missing-fields paths
- _match_reason returned string format (exact)
- _is_octowright + _is_already_disabled + disabled_key_for boundary cases
- _scan_servers non-string keys + already-disabled name skip
- detect_competing_servers projects-key mis-shape
- summarise single-scope multi-item "in" wording (1 vs many)
- apply_takeover every error-return branch + order preservation +
  nested-project rewrite + backup=False path
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from octowright.takeover import (
    COMPETING_COMMAND_PATTERNS,
    COMPETING_NAME_PATTERNS,
    DISABLED_PREFIX,
    DISABLED_SUFFIX,
    Detection,
    _command_string,
    _default_global_config,
    _default_project_config,
    _extract_mcp_servers,
    _extract_project_overrides,
    _is_already_disabled,
    _is_octowright,
    _load_json,
    _match_reason,
    _scan_servers,
    apply_takeover,
    detect_competing_servers,
    disabled_key_for,
    summarise,
)

# ─── Constants pin ───────────────────────────────────────────────────────────


class TestPatternConstants:
    def test_disabled_prefix_is_underscore(self) -> None:
        """Mutating to '' or '__' would break apply/disable round-trip."""
        assert DISABLED_PREFIX == "_"

    def test_disabled_suffix_exact(self) -> None:
        """Suffix is the magic marker tested against in _is_already_disabled."""
        assert DISABLED_SUFFIX == "_disabled_by_octowright"

    def test_name_patterns_present(self) -> None:
        """The three name patterns must be in the list. Mutating any drops a heuristic."""
        assert "playwright" in COMPETING_NAME_PATTERNS
        assert "chromium" in COMPETING_NAME_PATTERNS
        assert "browser-use" in COMPETING_NAME_PATTERNS

    def test_command_patterns_present(self) -> None:
        """The four command patterns matter for plugin-namespaced installs."""
        assert "@playwright/mcp" in COMPETING_COMMAND_PATTERNS
        assert "mcp-playwright" in COMPETING_COMMAND_PATTERNS
        assert "playwright/mcp" in COMPETING_COMMAND_PATTERNS
        assert r"plugin\.playwright" in COMPETING_COMMAND_PATTERNS


# ─── Detection dataclass ─────────────────────────────────────────────────────


class TestDetectionDataclass:
    def test_field_shape(self) -> None:
        """Five fields: scope, config_path, server_name, command, reason."""
        d = Detection(
            scope="project",
            config_path=Path("/tmp/.mcp.json"),
            server_name="playwright",
            command="npx @playwright/mcp",
            reason="name matches /playwright/",
        )
        assert d.scope == "project"
        assert d.config_path == Path("/tmp/.mcp.json")
        assert d.server_name == "playwright"
        assert d.command == "npx @playwright/mcp"
        assert d.reason == "name matches /playwright/"


# ─── _default_project_config / _default_global_config ───────────────────────


class TestDefaultPaths:
    def test_project_uses_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """`Path.cwd() / .mcp.json` — mutating to absolute would break per-repo isolation."""
        monkeypatch.chdir(tmp_path)
        assert _default_project_config() == tmp_path / ".mcp.json"

    def test_global_uses_home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """`Path.home() / .claude.json` — mutating to /etc/ would touch the wrong file."""
        monkeypatch.setenv("HOME", str(tmp_path))
        assert _default_global_config() == tmp_path / ".claude.json"

    def test_global_filename_is_dot_claude_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The filename is exactly `.claude.json`, not `claude.json` or `claude.config`."""
        monkeypatch.setenv("HOME", str(tmp_path))
        assert _default_global_config().name == ".claude.json"


# ─── _load_json ──────────────────────────────────────────────────────────────


class TestLoadJson:
    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        """No file → None (caller treats as 'no config to inspect')."""
        assert _load_json(tmp_path / "nope.json") is None

    def test_valid_dict_returned(self, tmp_path: Path) -> None:
        """Well-formed dict JSON → that dict."""
        p = tmp_path / "good.json"
        p.write_text(json.dumps({"a": 1, "b": [2, 3]}))
        assert _load_json(p) == {"a": 1, "b": [2, 3]}

    def test_malformed_json_returns_none(self, tmp_path: Path) -> None:
        """JSONDecodeError swallowed → None (don't crash on user typo)."""
        p = tmp_path / "bad.json"
        p.write_text("{ not json")
        assert _load_json(p) is None

    def test_list_root_returns_none(self, tmp_path: Path) -> None:
        """JSON list at root → None (we want a dict to look up mcpServers)."""
        p = tmp_path / "list.json"
        p.write_text(json.dumps([1, 2, 3]))
        assert _load_json(p) is None

    def test_scalar_root_returns_none(self, tmp_path: Path) -> None:
        """JSON scalar at root → None."""
        p = tmp_path / "scalar.json"
        p.write_text(json.dumps(42))
        assert _load_json(p) is None

    def test_string_root_returns_none(self, tmp_path: Path) -> None:
        """JSON string at root → None."""
        p = tmp_path / "str.json"
        p.write_text(json.dumps("hello"))
        assert _load_json(p) is None

    def test_oserror_swallowed_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """If read_text raises OSError → None (e.g. permission denied)."""
        p = tmp_path / "noread.json"
        p.write_text("{}")

        original_read_text = Path.read_text

        def _bad_read(self: Path, *args: Any, **kwargs: Any) -> str:
            if self == p:
                raise OSError("permission denied")
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", _bad_read)
        assert _load_json(p) is None


# ─── _extract_mcp_servers ────────────────────────────────────────────────────


class TestExtractMcpServers:
    def test_dict_returned(self) -> None:
        """Well-shaped mcpServers dict round-trips."""
        d = {"mcpServers": {"foo": {"command": "x"}}}
        assert _extract_mcp_servers(d) == {"foo": {"command": "x"}}

    def test_missing_key_returns_empty_dict(self) -> None:
        """No mcpServers key → {}, not None or KeyError."""
        assert _extract_mcp_servers({}) == {}

    def test_non_dict_value_returns_empty_dict(self) -> None:
        """mcpServers is a list/string/null → {} (defensive)."""
        assert _extract_mcp_servers({"mcpServers": [1, 2]}) == {}
        assert _extract_mcp_servers({"mcpServers": "stringy"}) == {}
        assert _extract_mcp_servers({"mcpServers": None}) == {}


# ─── _extract_project_overrides ──────────────────────────────────────────────


class TestExtractProjectOverrides:
    def test_well_formed_returns_per_project_servers(self) -> None:
        """`projects[<path>].mcpServers` mapping flattens to {path: servers}."""
        d = {
            "projects": {
                "/repo/a": {"mcpServers": {"playwright": {"command": "x"}}},
                "/repo/b": {"mcpServers": {"chromium": {"command": "y"}}},
            }
        }
        out = _extract_project_overrides(d)
        assert out == {
            "/repo/a": {"playwright": {"command": "x"}},
            "/repo/b": {"chromium": {"command": "y"}},
        }

    def test_missing_projects_key_empty(self) -> None:
        """No `projects` key → {}."""
        assert _extract_project_overrides({}) == {}

    def test_non_dict_projects_empty(self) -> None:
        """`projects` is a list → {}."""
        assert _extract_project_overrides({"projects": [1, 2]}) == {}

    def test_non_dict_per_project_skipped(self) -> None:
        """A per-project value that isn't a dict is silently skipped."""
        d = {
            "projects": {
                "/repo/a": "stringy",
                "/repo/b": {"mcpServers": {"foo": {}}},
            }
        }
        assert _extract_project_overrides(d) == {"/repo/b": {"foo": {}}}

    def test_non_dict_servers_skipped(self) -> None:
        """`mcpServers` not a dict → skipped."""
        d = {"projects": {"/repo": {"mcpServers": "stringy"}}}
        assert _extract_project_overrides(d) == {}

    def test_empty_servers_skipped(self) -> None:
        """Empty mcpServers dict is also skipped (no detections to surface)."""
        d = {"projects": {"/repo": {"mcpServers": {}}}}
        assert _extract_project_overrides(d) == {}

    def test_path_keys_coerced_to_str(self) -> None:
        """Non-string project keys are str()-coerced for the output."""
        d: dict[str, Any] = {"projects": {123: {"mcpServers": {"foo": {}}}}}
        assert _extract_project_overrides(d) == {"123": {"foo": {}}}


# ─── _command_string ─────────────────────────────────────────────────────────


class TestCommandString:
    def test_non_dict_entry_returns_empty(self) -> None:
        """Non-dict entry → '' (defensive against malformed configs)."""
        assert _command_string("oops") == ""
        assert _command_string(None) == ""
        assert _command_string([1, 2]) == ""

    def test_command_only(self) -> None:
        """Just a command field flattens to that string."""
        assert _command_string({"command": "npx"}) == "npx"

    def test_command_plus_args(self) -> None:
        """command + args → space-joined."""
        assert _command_string({"command": "npx", "args": ["-y", "@x/mcp"]}) == "npx -y @x/mcp"

    def test_non_string_args_skipped(self) -> None:
        """Args list filtered to strings."""
        result = _command_string({"command": "npx", "args": ["-y", 42, None, "a"]})
        assert result == "npx -y a"

    def test_args_not_list_ignored(self) -> None:
        """args=str/dict/None → not iterated."""
        assert _command_string({"command": "npx", "args": "stringy"}) == "npx"
        assert _command_string({"command": "npx", "args": None}) == "npx"

    def test_url_appended(self) -> None:
        """`url` field appended (HTTP-style MCP servers)."""
        assert _command_string({"url": "https://x/mcp"}) == "https://x/mcp"

    def test_non_string_command_dropped(self) -> None:
        """command=int → dropped, only args/url remain."""
        assert _command_string({"command": 42, "args": ["a"]}) == "a"

    def test_empty_dict_returns_empty(self) -> None:
        """No command/args/url at all → ''."""
        assert _command_string({}) == ""


# ─── _match_reason ───────────────────────────────────────────────────────────


class TestMatchReason:
    def test_name_match_returns_format_string(self) -> None:
        """Name match returns 'name matches /<pattern>/'."""
        assert _match_reason("playwright-server", "irrelevant") == "name matches /playwright/"

    def test_name_match_case_insensitive(self) -> None:
        """Case folded — 'PLAYWRIGHT' still matches."""
        assert _match_reason("PLAYWRIGHT", "") == "name matches /playwright/"

    def test_chromium_name_match(self) -> None:
        """The 'chromium' name pattern."""
        assert _match_reason("Chromium-MCP", "") == "name matches /chromium/"

    def test_browser_use_name_match(self) -> None:
        """The 'browser-use' name pattern."""
        assert _match_reason("browser-use-thing", "") == "name matches /browser-use/"

    def test_command_match_when_name_doesnt(self) -> None:
        """Bland name + competing command → command-match reason."""
        assert _match_reason("foo", "npx -y @playwright/mcp") == "command matches /@playwright/mcp/"

    def test_mcp_playwright_command_match(self) -> None:
        """`mcp-playwright` command pattern."""
        assert _match_reason("foo", "node mcp-playwright") == "command matches /mcp-playwright/"

    def test_plugin_dot_playwright_match(self) -> None:
        """`plugin.playwright` (escaped dot in pattern) matches literal."""
        result = _match_reason("foo", "plugin.playwright/server")
        assert result is not None
        assert "plugin" in result and "playwright" in result

    def test_no_match_returns_none(self) -> None:
        """Bland name + bland command → None."""
        assert _match_reason("foo", "node bar") is None

    def test_empty_inputs_no_match(self) -> None:
        """Both empty → None."""
        assert _match_reason("", "") is None


# ─── _is_octowright / _is_already_disabled / disabled_key_for ──────────────


class TestNameClassifiers:
    def test_is_octowright_lowercase(self) -> None:
        assert _is_octowright("octowright") is True

    def test_is_octowright_case_insensitive(self) -> None:
        """Case folded."""
        assert _is_octowright("Octowright") is True
        assert _is_octowright("OCTOWRIGHT") is True

    def test_is_octowright_substring_doesnt_match(self) -> None:
        """It's an exact match, not substring."""
        assert _is_octowright("octowright-extra") is False
        assert _is_octowright("my-octowright") is False

    def test_is_already_disabled_suffix_match(self) -> None:
        """Names ending in the suffix marker."""
        assert _is_already_disabled("_playwright_disabled_by_octowright") is True

    def test_is_already_disabled_negative(self) -> None:
        """Without the suffix → False."""
        assert _is_already_disabled("playwright") is False
        assert _is_already_disabled("_playwright") is False
        assert _is_already_disabled("octowright") is False

    def test_disabled_key_for_format(self) -> None:
        """`_<name>_disabled_by_octowright` shape."""
        assert disabled_key_for("playwright") == "_playwright_disabled_by_octowright"
        assert disabled_key_for("foo-bar") == "_foo-bar_disabled_by_octowright"


# ─── _scan_servers ───────────────────────────────────────────────────────────


class TestScanServers:
    def test_non_string_key_skipped(self) -> None:
        """A non-string key in the mcpServers dict is skipped (defensive).

        Both entries' commands match `mcp-playwright`, so without the type-skip
        the int-keyed entry would surface; with the skip we only see "foo".
        """
        servers: dict[Any, Any] = {
            42: {"command": "mcp-playwright"},
            "foo": {"command": "mcp-playwright"},
        }
        out = _scan_servers(servers, scope="project", config_path=Path("/tmp/x"))
        # 42-keyed entry must be skipped; only "foo" remains.
        assert len(out) == 1
        assert out[0].server_name == "foo"

    def test_octowright_excluded(self) -> None:
        """Octowright itself is never a competing detection."""
        servers = {"octowright": {"command": "playwright-thing"}}
        assert _scan_servers(servers, scope="project", config_path=Path("/tmp/x")) == []

    def test_already_disabled_excluded(self) -> None:
        """An entry whose name already ends in the disabled suffix is skipped
        (we already disabled it on a prior run)."""
        servers = {"_playwright_disabled_by_octowright": {"command": "old"}}
        assert _scan_servers(servers, scope="project", config_path=Path("/tmp/x")) == []

    def test_no_match_filtered(self) -> None:
        """Non-matching server returns empty list."""
        servers = {"my-other-mcp": {"command": "node /bin/server.js"}}
        assert _scan_servers(servers, scope="project", config_path=Path("/tmp/x")) == []


# ─── detect_competing_servers — extra branches ─────────────────────────────


class TestDetectExtra:
    def test_no_args_uses_defaults(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """No args → uses _default_project_config + _default_global_config."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        # Both configs missing → no detections, no crash.
        assert detect_competing_servers() == []

    def test_global_config_with_invalid_projects_section(self, tmp_path: Path) -> None:
        """Global config with `projects` field that isn't a dict → ignored, no crash."""
        global_cfg = tmp_path / ".claude.json"
        global_cfg.write_text(json.dumps({"mcpServers": {}, "projects": "not-a-dict"}))
        result = detect_competing_servers(
            project_config=tmp_path / "missing.mcp.json",
            global_config=global_cfg,
        )
        assert result == []

    def test_nested_project_override_reason_includes_projects_path(self, tmp_path: Path) -> None:
        """Detections from nested project overrides carry `(under projects[...])` in the reason."""
        global_cfg = tmp_path / ".claude.json"
        global_cfg.write_text(
            json.dumps(
                {
                    "mcpServers": {},
                    "projects": {"/work/repo": {"mcpServers": {"playwright": {"command": "npx @playwright/mcp"}}}},
                }
            )
        )
        result = detect_competing_servers(
            project_config=tmp_path / "missing.mcp.json",
            global_config=global_cfg,
        )
        assert len(result) == 1
        assert "(under projects[/work/repo])" in result[0].reason

    def test_project_override_with_octowright_excluded(self, tmp_path: Path) -> None:
        """Nested project entry named 'octowright' is excluded just like top-level."""
        global_cfg = tmp_path / ".claude.json"
        global_cfg.write_text(
            json.dumps(
                {
                    "mcpServers": {},
                    "projects": {"/repo": {"mcpServers": {"octowright": {"command": "x"}}}},
                }
            )
        )
        result = detect_competing_servers(
            project_config=tmp_path / "missing.mcp.json",
            global_config=global_cfg,
        )
        assert result == []


# ─── summarise — branches ────────────────────────────────────────────────────


class TestSummariseBranches:
    def _det(self, scope: str, fname: str, name: str) -> Detection:
        return Detection(
            scope=scope,
            config_path=Path(f"/tmp/{fname}"),
            server_name=name,
            command="x",
            reason="r",
        )

    def test_zero_returns_zero_string(self) -> None:
        """Empty list → '0 competing plugins'."""
        assert summarise([]) == "0 competing plugins"

    def test_single_uses_in_phrasing(self) -> None:
        """1 detection → '1 competing plugin in <chunk>'."""
        result = summarise([self._det("project", ".mcp.json", "playwright")])
        assert result == "1 competing plugin in project (.mcp.json: playwright)"

    def test_two_in_same_scope_no_in_phrasing(self) -> None:
        """2 in one scope → '2 competing plugins: <chunk>' (no 'in' phrasing)."""
        result = summarise(
            [
                self._det("project", ".mcp.json", "playwright"),
                self._det("project", ".mcp.json", "chromium"),
            ]
        )
        assert result == "2 competing plugins: project (.mcp.json: playwright, chromium)"

    def test_mixed_scopes_orders_project_then_global(self) -> None:
        """Order is project, then global — even if global appears first in input."""
        result = summarise(
            [
                self._det("global", ".claude.json", "chromium"),
                self._det("project", ".mcp.json", "playwright"),
            ]
        )
        assert result == ("2 competing plugins: project (.mcp.json: playwright); global (.claude.json: chromium)")

    def test_plural_word_for_two(self) -> None:
        """2 plugins uses 'plugins' (plural)."""
        result = summarise(
            [
                self._det("project", ".mcp.json", "a"),
                self._det("project", ".mcp.json", "b"),
            ]
        )
        assert "plugins" in result and "1 competing plugin" not in result

    def test_only_global_scope(self) -> None:
        """Single global detection still uses 'in' phrasing."""
        result = summarise([self._det("global", ".claude.json", "playwright")])
        assert result == "1 competing plugin in global (.claude.json: playwright)"


# ─── apply_takeover — error returns ─────────────────────────────────────────


class TestApplyTakeoverErrors:
    def _fresh_detection(self, path: Path, name: str = "playwright") -> Detection:
        return Detection(
            scope="project",
            config_path=path,
            server_name=name,
            command="x",
            reason="r",
        )

    def test_missing_config_returns_error(self, tmp_path: Path) -> None:
        """Config file doesn't exist → error dict, no crash."""
        det = self._fresh_detection(tmp_path / "missing.json")
        result = apply_takeover(det)
        assert result["disabled"] is False
        assert result["backup_path"] is None
        assert "config does not exist" in result["error"]

    def test_malformed_json_returns_error(self, tmp_path: Path) -> None:
        """Existing file with broken JSON → error dict."""
        cfg = tmp_path / ".mcp.json"
        cfg.write_text("{ not json")
        det = self._fresh_detection(cfg)
        result = apply_takeover(det)
        assert result["disabled"] is False
        assert "could not parse JSON" in result["error"]

    def test_non_dict_root_returns_error(self, tmp_path: Path) -> None:
        """JSON list at root → error dict (no mcpServers to find)."""
        cfg = tmp_path / ".mcp.json"
        cfg.write_text(json.dumps([1, 2]))
        det = self._fresh_detection(cfg)
        result = apply_takeover(det)
        assert result["disabled"] is False
        assert result["error"] == "config root is not a JSON object"

    def test_server_not_found_returns_error(self, tmp_path: Path) -> None:
        """Detection refers to a server name not present in config → error dict."""
        cfg = tmp_path / ".mcp.json"
        cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
        det = self._fresh_detection(cfg, name="playwright")
        result = apply_takeover(det)
        assert result["disabled"] is False
        assert "not found in" in result["error"]
        assert "'playwright'" in result["error"]

    def test_target_key_already_exists_refuses(self, tmp_path: Path) -> None:
        """If `_<name>_disabled_by_octowright` already exists, refuse to clobber."""
        cfg = tmp_path / ".mcp.json"
        cfg.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "playwright": {"command": "x"},
                        "_playwright_disabled_by_octowright": {"command": "old"},
                    }
                }
            )
        )
        det = self._fresh_detection(cfg)
        result = apply_takeover(det)
        assert result["disabled"] is False
        assert "already exists" in result["error"]
        assert "refusing to overwrite" in result["error"]

    def test_no_mcpservers_no_projects_returns_error(self, tmp_path: Path) -> None:
        """Config has neither mcpServers nor projects.<...>.mcpServers with the name."""
        cfg = tmp_path / ".mcp.json"
        cfg.write_text(json.dumps({"some_other_key": True}))
        det = self._fresh_detection(cfg, name="playwright")
        result = apply_takeover(det)
        assert result["disabled"] is False
        assert "not found in" in result["error"]


# ─── apply_takeover — successful + structural pins ─────────────────────────


class TestApplyTakeoverSuccess:
    def test_no_backup_when_backup_false(self, tmp_path: Path) -> None:
        """backup=False → backup_path is None on the success return."""
        cfg = tmp_path / ".mcp.json"
        cfg.write_text(json.dumps({"mcpServers": {"playwright": {"command": "x"}}}))
        det = Detection(
            scope="project",
            config_path=cfg,
            server_name="playwright",
            command="x",
            reason="r",
        )
        result = apply_takeover(det, backup=False)
        assert result["disabled"] is True
        assert result["backup_path"] is None
        # No bak.* file lying around.
        assert not list(tmp_path.glob("*.bak.*"))

    def test_backup_file_contains_original_text(self, tmp_path: Path) -> None:
        """Backup file matches the pre-rewrite content byte-for-byte."""
        cfg = tmp_path / ".mcp.json"
        original = json.dumps({"mcpServers": {"playwright": {"command": "x"}}})
        cfg.write_text(original)
        det = Detection(
            scope="project",
            config_path=cfg,
            server_name="playwright",
            command="x",
            reason="r",
        )
        result = apply_takeover(det)
        bp = Path(result["backup_path"])
        assert bp.exists()
        assert bp.read_text() == original

    def test_backup_filename_includes_timestamp(self, tmp_path: Path) -> None:
        """Backup filename: <orig>.bak.<YYYYMMDD-HHMMSS>."""
        cfg = tmp_path / ".mcp.json"
        cfg.write_text(json.dumps({"mcpServers": {"playwright": {"command": "x"}}}))
        det = Detection(
            scope="project",
            config_path=cfg,
            server_name="playwright",
            command="x",
            reason="r",
        )
        result = apply_takeover(det)
        bp = Path(result["backup_path"])
        # `<name>.json.bak.<YYYYMMDD-HHMMSS>`
        m = re.match(r"\.mcp\.json\.bak\.\d{8}-\d{6}$", bp.name)
        assert m is not None, f"unexpected backup name: {bp.name}"

    def test_order_of_other_servers_preserved(self, tmp_path: Path) -> None:
        """Renamed entry stays in its original position, other servers don't shuffle."""
        cfg = tmp_path / ".mcp.json"
        cfg.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "first": {"command": "a"},
                        "playwright": {"command": "b"},
                        "third": {"command": "c"},
                    }
                }
            )
        )
        det = Detection(
            scope="project",
            config_path=cfg,
            server_name="playwright",
            command="x",
            reason="r",
        )
        apply_takeover(det, backup=False)
        rewritten = json.loads(cfg.read_text())
        keys = list(rewritten["mcpServers"].keys())
        # The renamed key sits in the SAME slot the original was in.
        assert keys == ["first", "_playwright_disabled_by_octowright", "third"]

    def test_payload_preserved_under_renamed_key(self, tmp_path: Path) -> None:
        """The renamed entry carries the original config payload verbatim."""
        cfg = tmp_path / ".mcp.json"
        cfg.write_text(json.dumps({"mcpServers": {"playwright": {"command": "node", "args": ["x", "y"]}}}))
        det = Detection(
            scope="project",
            config_path=cfg,
            server_name="playwright",
            command="x",
            reason="r",
        )
        apply_takeover(det, backup=False)
        rewritten = json.loads(cfg.read_text())
        assert rewritten["mcpServers"]["_playwright_disabled_by_octowright"] == {
            "command": "node",
            "args": ["x", "y"],
        }

    def test_new_key_name_returned(self, tmp_path: Path) -> None:
        """`new_key_name` field on the success return is the disabled form."""
        cfg = tmp_path / ".mcp.json"
        cfg.write_text(json.dumps({"mcpServers": {"playwright": {"command": "x"}}}))
        det = Detection(
            scope="project",
            config_path=cfg,
            server_name="playwright",
            command="x",
            reason="r",
        )
        result = apply_takeover(det, backup=False)
        assert result["new_key_name"] == "_playwright_disabled_by_octowright"

    def test_idempotent_second_apply_refuses(self, tmp_path: Path) -> None:
        """Running apply_takeover twice on the same detection: 2nd run finds the
        renamed key, refuses (because the original `playwright` key is gone now)."""
        cfg = tmp_path / ".mcp.json"
        cfg.write_text(json.dumps({"mcpServers": {"playwright": {"command": "x"}}}))
        det = Detection(
            scope="project",
            config_path=cfg,
            server_name="playwright",
            command="x",
            reason="r",
        )
        first = apply_takeover(det, backup=False)
        assert first["disabled"] is True
        second = apply_takeover(det, backup=False)
        assert second["disabled"] is False
        # The original is gone now, so it's "not found".
        assert "not found in" in second["error"]


# ─── apply_takeover — nested project overrides ──────────────────────────────


class TestApplyTakeoverNested:
    def test_rewrites_nested_project_servers(self, tmp_path: Path) -> None:
        """When the detection points at projects[<path>].mcpServers entry, that
        nested mapping is rewritten in place."""
        cfg = tmp_path / ".claude.json"
        cfg.write_text(
            json.dumps(
                {
                    "mcpServers": {},
                    "projects": {"/work/repo": {"mcpServers": {"playwright": {"command": "x"}}}},
                }
            )
        )
        det = Detection(
            scope="global",
            config_path=cfg,
            server_name="playwright",
            command="x",
            reason="r (under projects[/work/repo])",
        )
        result = apply_takeover(det, backup=False)
        assert result["disabled"] is True
        rewritten = json.loads(cfg.read_text())
        nested = rewritten["projects"]["/work/repo"]["mcpServers"]
        assert "playwright" not in nested
        assert "_playwright_disabled_by_octowright" in nested

    def test_nested_skipped_when_proj_cfg_not_dict(self, tmp_path: Path) -> None:
        """If a `projects[<path>]` value isn't a dict, it's skipped during the
        nested fallback search and the not-found error still surfaces."""
        cfg = tmp_path / ".claude.json"
        cfg.write_text(
            json.dumps(
                {
                    "mcpServers": {},
                    "projects": {
                        "/repo/a": "not-a-dict",
                        "/repo/b": {"mcpServers": {"other": {"command": "y"}}},
                    },
                }
            )
        )
        det = Detection(
            scope="global",
            config_path=cfg,
            server_name="playwright",
            command="x",
            reason="r",
        )
        result = apply_takeover(det, backup=False)
        # `playwright` not found in any mcpServers (top-level or nested).
        assert result["disabled"] is False
        assert "not found in" in result["error"]
