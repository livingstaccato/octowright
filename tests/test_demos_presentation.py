# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path

import pytest

from octowright.demos.catalog import load_demo_bundle
from octowright.demos.models import DemoPresentationConfig


def test_load_demo_bundle_parses_presentation_block(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "demo" / "bundles" / "alpha"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "demo.yaml").write_text(
        """
id: alpha
title: Alpha
presentation:
  mode: sync-multi
  primary_asset: hero_video
  overlay:
    style: subtle
    placement: bottom-right
    enabled: true
  timing:
    intro_ms: 500
    outro_ms: 1800
    minimum_ms: 6000
  sync_groups:
    - id: engines
      roles: [player, monitor]
""".strip(),
        encoding="utf-8",
    )

    bundle = load_demo_bundle(bundle_dir)

    assert bundle.presentation.mode == "sync-multi"
    assert bundle.presentation.primary_asset == "hero_video"
    assert bundle.presentation.overlay.enabled is True
    assert bundle.presentation.overlay.style == "subtle"
    assert bundle.presentation.overlay.placement == "bottom-right"
    assert bundle.presentation.timing.intro_ms == 500
    assert bundle.presentation.timing.outro_ms == 1800
    assert bundle.presentation.timing.minimum_ms == 6000
    assert len(bundle.presentation.sync_groups) == 1
    assert bundle.presentation.sync_groups[0].id == "engines"
    assert bundle.presentation.sync_groups[0].roles == ["player", "monitor"]


def test_load_demo_bundle_defaults_presentation_block(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "demo" / "bundles" / "defaults"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "demo.yaml").write_text("id: defaults\ntitle: Defaults\n", encoding="utf-8")

    bundle = load_demo_bundle(bundle_dir)

    assert bundle.presentation.mode == "single-clean"
    assert bundle.presentation.primary_asset == "hero_video"
    assert bundle.presentation.overlay.enabled is True
    assert bundle.presentation.overlay.style == "subtle"
    assert bundle.presentation.overlay.placement == "bottom-left"
    assert bundle.presentation.timing.intro_ms == 0
    assert bundle.presentation.timing.outro_ms == 1500
    assert bundle.presentation.timing.minimum_ms == 4000
    assert bundle.presentation.sync_groups == []


def test_load_demo_bundle_rejects_unknown_presentation_mode(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "demo" / "bundles" / "broken"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "demo.yaml").write_text("presentation:\n  mode: freestyle\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"presentation\.mode"):
        load_demo_bundle(bundle_dir)


def test_demo_presentation_config_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match=r"presentation\.mode"):
        DemoPresentationConfig(mode="freestyle")


@pytest.mark.parametrize("field_name", ["intro_ms", "outro_ms", "minimum_ms"])
def test_load_demo_bundle_rejects_negative_timing_values(tmp_path: Path, field_name: str) -> None:
    bundle_dir = tmp_path / "demo" / "bundles" / field_name
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "demo.yaml").write_text(
        f"presentation:\n  timing:\n    {field_name}: -1\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=rf"presentation\.timing\.{field_name}"):
        load_demo_bundle(bundle_dir)


def test_load_demo_bundle_rejects_malformed_overlay_payload(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "demo" / "bundles" / "bad-overlay"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "demo.yaml").write_text(
        "presentation:\n  overlay: []\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"presentation\.overlay"):
        load_demo_bundle(bundle_dir)


def test_load_demo_bundle_rejects_malformed_timing_payload(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "demo" / "bundles" / "bad-timing"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "demo.yaml").write_text(
        "presentation:\n  timing:\n    intro_ms: soon\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"presentation\.timing\.intro_ms"):
        load_demo_bundle(bundle_dir)


def test_load_demo_bundle_rejects_empty_sync_group_roles(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "demo" / "bundles" / "empty-roles"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "demo.yaml").write_text(
        """
presentation:
  sync_groups:
    - id: engines
      roles: []
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"presentation\.sync_groups\[0\]\.roles"):
        load_demo_bundle(bundle_dir)


def test_load_demo_bundle_rejects_malformed_sync_groups_payload(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "demo" / "bundles" / "bad-sync-groups"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "demo.yaml").write_text(
        "presentation:\n  sync_groups:\n    - id: engines\n      roles: player\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"presentation\.sync_groups\[0\]\.roles"):
        load_demo_bundle(bundle_dir)
