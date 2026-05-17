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

    # Fake checkout root that lives inside tmp_path — that way the sanitizer
    # actually has an absolute path to strip without coupling the test to any
    # one developer's home directory. The assertions verify that string never
    # appears in the sanitized output.
    fake_root = tmp_path / "fake-checkout" / "octowright"
    fake_home = tmp_path / "fake-home"
    bundle_seed_url = f"file://{fake_root}/demo/bundles/alpha-demo/seed/welcome.html?slot=0"

    replay_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "action": "launch",
                        "kind": "webkit",
                        "url": bundle_seed_url,
                        "user_data_dir": f"{fake_home}/.config/octowright/profiles/alpha/webkit",
                        "video_dir": f"{fake_root}/recordings/videos/abc123",
                    }
                ),
                json.dumps(
                    {
                        "action": "markdown_cached",
                        "path": f"{fake_root}/recordings/20260507T000000Z-webkit-alpha.markdown.md",
                    }
                ),
                json.dumps(
                    {
                        "action": "close",
                        "video_path": f"{fake_root}/recordings/videos/abc123/out.webm",
                        "markdown_path": f"{fake_root}/recordings/20260507T000000Z-webkit-alpha.markdown.md",
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
                        "log_path": f"{fake_root}/recordings/alpha.jsonl",
                        "url": bundle_seed_url,
                        "video_dir": f"{fake_root}/recordings/videos/abc123",
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
    roster_text = roster_path.read_text(encoding="utf-8")
    roster = json.loads(roster_text)

    # No trace of the fake-checkout or fake-home prefixes should survive.
    assert str(fake_root) not in replay_text
    assert str(fake_home) not in replay_text
    assert str(fake_root) not in roster_text
    assert "file://" not in replay_text
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
