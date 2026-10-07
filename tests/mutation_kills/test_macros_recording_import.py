# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A recording is read as UTF-8 whatever the host locale says."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from octowright.macros.recording_import import load_macro_from_recording

_READ = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "from octowright.macros.recording_import import load_macro_from_recording\n"
    "print(json.dumps(load_macro_from_recording(Path(sys.argv[1])), ensure_ascii=True))\n"
)


@pytest.mark.skipif(sys.platform == "win32", reason="drives the POSIX C locale")
def test_a_non_ascii_recording_reads_under_an_ascii_locale(tmp_path: Path) -> None:
    recording = tmp_path / "r.jsonl"
    entry = {"action": "fill", "selector": "#name", "value": "Zoë Ångström"}
    recording.write_bytes((json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8"))

    # In-process too: mutmut maps a test to the code it runs in its own process.
    assert load_macro_from_recording(recording) == [entry]

    # The C locale with coercion and UTF-8 mode both off makes the locale
    # encoding ASCII, which is what a reader that leans on the locale would use.
    env = {**os.environ, "LC_ALL": "C", "LANG": "C", "PYTHONCOERCECLOCALE": "0", "PYTHONUTF8": "0"}
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    done = subprocess.run(
        [sys.executable, "-X", "utf8=0", "-c", _READ, str(recording)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
        timeout=60,
    )

    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == [entry]
