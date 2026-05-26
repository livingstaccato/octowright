# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Containment tests for ``_build_har_kwargs``.

Reachable via ``browser_launch(har_path=...)``: an LLM-supplied absolute
path used to pass straight through to Playwright's ``record_har_path``,
letting a HAR get written anywhere on disk. The fix sandboxes both the
relative-resolved and absolute branches under RECORDINGS_DIR.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from octowright.browser_pool import launch_helpers


def test_build_har_kwargs_relative_path_lands_under_recordings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Sanity: a relative har_path is rooted at RECORDINGS_DIR (existing behaviour)."""
    monkeypatch.setattr(launch_helpers, "RECORDINGS_DIR", tmp_path)
    log_path = tmp_path / "session.jsonl"
    har_path, kwargs = launch_helpers._build_har_kwargs(
        har=True,
        har_path_opt="capture.har",
        har_mode="minimal",
        har_url_filter=None,
        har_content=None,
        log_path=log_path,
    )
    assert har_path is not None
    assert har_path.is_relative_to(tmp_path)
    assert kwargs["record_har_path"].endswith("capture.har")


def test_build_har_kwargs_absolute_path_under_recordings_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An absolute path that already lives under RECORDINGS_DIR is OK."""
    monkeypatch.setattr(launch_helpers, "RECORDINGS_DIR", tmp_path)
    log_path = tmp_path / "session.jsonl"
    target = tmp_path / "inner" / "capture.har"
    har_path, _kwargs = launch_helpers._build_har_kwargs(
        har=True,
        har_path_opt=str(target),
        har_mode="minimal",
        har_url_filter=None,
        har_content=None,
        log_path=log_path,
    )
    assert har_path == target


def test_build_har_kwargs_rejects_absolute_path_outside_recordings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression: absolute har_path outside RECORDINGS_DIR must raise.

    Previously the sandboxing branch only ran for relative paths, so an
    LLM passing ``har_path="/etc/passwd"`` (or similar) reached
    ``record_har_path`` directly. The fix rejects with a ValueError."""
    monkeypatch.setattr(launch_helpers, "RECORDINGS_DIR", tmp_path)
    log_path = tmp_path / "session.jsonl"
    outside = tmp_path.parent / "escape" / "evil.har"
    with pytest.raises(ValueError, match="resolves outside"):
        launch_helpers._build_har_kwargs(
            har=True,
            har_path_opt=str(outside),
            har_mode="minimal",
            har_url_filter=None,
            har_content=None,
            log_path=log_path,
        )


def test_build_har_kwargs_optional_url_filter_and_content_when_har_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When neither ``har`` nor ``har_path_opt`` is set, nothing happens."""
    monkeypatch.setattr(launch_helpers, "RECORDINGS_DIR", tmp_path)
    log_path = tmp_path / "session.jsonl"
    har_path, kwargs = launch_helpers._build_har_kwargs(
        har=False,
        har_path_opt=None,
        har_mode="minimal",
        har_url_filter=None,
        har_content=None,
        log_path=log_path,
    )
    assert har_path is None
    assert kwargs == {}


def test_build_har_kwargs_passes_through_url_filter_and_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Optional filter/content kwargs are forwarded as record_har_* options."""
    monkeypatch.setattr(launch_helpers, "RECORDINGS_DIR", tmp_path)
    log_path = tmp_path / "session.jsonl"
    _path, kwargs = launch_helpers._build_har_kwargs(
        har=True,
        har_path_opt="capture.har",
        har_mode="full",
        har_url_filter="https://octowright.com/**",
        har_content="embed",
        log_path=log_path,
    )
    assert kwargs["record_har_url_filter"] == "https://octowright.com/**"
    assert kwargs["record_har_content"] == "embed"
    assert kwargs["record_har_mode"] == "full"
