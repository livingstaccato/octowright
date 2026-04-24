# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class Recorder:
    """Append-only JSONL action log for one browser instance.

    Each `record()` call writes a single line:
        {"ts": "<iso8601>", "action": "<name>", ...fields}
    """

    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.log_path.open("a", encoding="utf-8")

    def record(self, action: str, **fields: Any) -> None:
        entry = {"ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "action": action, **fields}
        self._fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()


def new_log_path(base_dir: Path, instance_id: str, label: str | None, kind: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"-{label}" if label else ""
    return base_dir / f"{stamp}-{kind}-{instance_id}{suffix}.jsonl"
