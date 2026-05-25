# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _replay_urls(bundle_id: str) -> list[str]:
    replay = Path("demo/bundles") / bundle_id / "artifacts" / "replay.jsonl"
    urls: list[str] = []
    for line in replay.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        url = event.get("url")
        if isinstance(url, str):
            urls.append(url)
    return urls


@pytest.mark.parametrize(
    "bundle_id",
    ["role-based-duo", "seven-mix-orchestration"],
)
def test_playground_hero_replays_use_local_playground_urls(bundle_id: str) -> None:
    urls = _replay_urls(bundle_id)
    assert urls, f"{bundle_id}: expected replay URLs"
    assert all(url.startswith("http://127.0.0.1:7900/") for url in urls), bundle_id
    assert all("octowright.com" not in url for url in urls), bundle_id
