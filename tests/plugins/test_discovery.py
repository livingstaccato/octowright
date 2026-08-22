# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import sys
from importlib.metadata import EntryPoint

import pytest

from octowright.plugins.discovery import discover, enabled_names
from octowright.plugins.errors import DuplicatePluginNameError


def _ep(name: str, value: str = "tests.plugins._import_probe:MARKER") -> EntryPoint:
    return EntryPoint(name=name, value=value, group="octowright.session_kinds")


def test_discovery_reports_metadata_without_importing(monkeypatch):
    sys.modules.pop("tests.plugins._import_probe", None)
    found = discover(entry_points=[_ep("refkind")])

    assert [p.name for p in found] == ["refkind"]
    assert found[0].entry_point == "tests.plugins._import_probe:MARKER"
    # The whole trust boundary: discovery must not execute plugin code.
    assert "tests.plugins._import_probe" not in sys.modules


def test_duplicate_entry_point_names_are_refused():
    with pytest.raises(DuplicatePluginNameError, match="refkind"):
        discover(entry_points=[_ep("refkind"), _ep("refkind")])


def test_invalid_entry_point_name_is_skipped_not_fatal():
    # One malformed package must not take out discovery for every other one.
    found = discover(entry_points=[_ep("Bad Name"), _ep("refkind")])
    assert [p.name for p in found] == ["refkind"]


def test_env_var_wins_over_config(tmp_path):
    cfg = tmp_path / "plugins.yaml"
    cfg.write_text("plugins:\n  - fromfile\n")
    assert enabled_names(env={"OCTOWRIGHT_PLUGINS": "fromenv"}, config_path=cfg) == ["fromenv"]


def test_config_file_used_when_env_unset(tmp_path):
    cfg = tmp_path / "plugins.yaml"
    cfg.write_text("plugins:\n  - fromfile\n  - second\n")
    assert enabled_names(env={}, config_path=cfg) == ["fromfile", "second"]


def test_nothing_enabled_by_default(tmp_path):
    assert enabled_names(env={}, config_path=tmp_path / "absent.yaml") == []


def test_malformed_config_is_not_fatal(tmp_path):
    cfg = tmp_path / "plugins.yaml"
    cfg.write_text("plugins: not-a-list\n")
    assert enabled_names(env={}, config_path=cfg) == []


def test_project_config_does_not_enable_plugins(tmp_path, monkeypatch):
    # Enable is daemon-scoped. `.octowright/config.yaml` is found by walking up
    # from CWD, so honouring it here would make the MCP tool surface depend on
    # which directory the daemon was spawned in.
    project = tmp_path / "proj" / ".octowright"
    project.mkdir(parents=True)
    (project / "config.yaml").write_text("plugins:\n  - refkind\n")
    monkeypatch.chdir(tmp_path / "proj")

    assert enabled_names(env={}, config_path=tmp_path / "absent.yaml") == []
