# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from pathlib import Path

from octowright_demos.models import DemoBundle
from octowright_demos.public_artifacts import sanitize_public_artifacts


def test_sanitize_public_artifacts_strips_absolute_local_paths(tmp_path: Path) -> None:
    bundle_root = tmp_path / "demo" / "bundles" / "alpha-demo"
    artifacts_dir = bundle_root / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    replay_path = artifacts_dir / "replay.jsonl"
    roster_path = artifacts_dir / "participant-roster.json"
    replay_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "action": "launch",
                        "kind": "webkit",
                        "url": "file:///Users/tim/code/gh/provide-io/octowright/demo/bundles/alpha-demo/seed/welcome.html?slot=0",
                        "user_data_dir": "/Users/tim/.config/octowright/profiles/alpha/webkit",
                        "video_dir": "/Users/tim/code/gh/provide-io/octowright/recordings/videos/abc123",
                    }
                ),
                json.dumps(
                    {
                        "action": "markdown_cached",
                        "path": "/Users/tim/code/gh/provide-io/octowright/recordings/20260507T000000Z-webkit-alpha.markdown.md",
                    }
                ),
                json.dumps(
                    {
                        "action": "close",
                        "video_path": "/Users/tim/code/gh/provide-io/octowright/recordings/videos/abc123/out.webm",
                        "markdown_path": "/Users/tim/code/gh/provide-io/octowright/recordings/20260507T000000Z-webkit-alpha.markdown.md",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    roster_path.write_text(
        json.dumps(
            {
                "scenario_id": "demo-live",
                "participants": [
                    {
                        "instance_id": "iid-1",
                        "persona": "alpha",
                        "role": "player",
                        "kind": "webkit",
                        "log_path": "/Users/tim/code/gh/provide-io/octowright/recordings/alpha.jsonl",
                        "url": "file:///Users/tim/code/gh/provide-io/octowright/demo/bundles/alpha-demo/seed/welcome.html?slot=0",
                        "video_dir": "/Users/tim/code/gh/provide-io/octowright/recordings/videos/abc123",
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    bundle = DemoBundle(
        id="alpha-demo",
        title="Alpha Demo",
        replay_artifacts=["artifacts/replay.jsonl", "artifacts/participant-roster.json"],
        root=bundle_root,
    )

    sanitize_public_artifacts(bundle)

    replay_text = replay_path.read_text(encoding="utf-8")
    roster = json.loads(roster_path.read_text(encoding="utf-8"))
    assert "/Users/tim" not in replay_text
    assert "file:///Users/tim" not in replay_text
    assert "bundle://seed/welcome.html?slot=0" in replay_text
    launch_event = json.loads(replay_text.splitlines()[0])
    assert "user_data_dir" not in launch_event
    assert "video_dir" not in launch_event
    assert "path" not in json.loads(replay_text.splitlines()[1])
    assert "video_path" not in json.loads(replay_text.splitlines()[2])
    assert "markdown_path" not in json.loads(replay_text.splitlines()[2])
    participant = roster["participants"][0]
    assert participant["url"] == "bundle://seed/welcome.html?slot=0"
    assert "log_path" not in participant
    assert "video_dir" not in participant
