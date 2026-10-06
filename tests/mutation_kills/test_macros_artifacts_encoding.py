# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The artifact store reads and writes UTF-8 whatever the host locale says.

A reader that leans on the locale works on a UTF-8 developer machine and breaks
on a host whose locale encoding is not UTF-8 (Windows' cp1252, a POSIX ``C``
locale), the moment a macro description or a recording holds a non-ASCII
character. The store is run once here and once in a child under an ASCII
locale, and both must report the same thing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests._macro_artifact_fixtures import _reload, restore_reloaded_defaults
from tests.mutation_kills._artifact_helpers import exercise_non_ascii

_CHILD = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "import octowright.macros.artifacts as macro_artifacts\n"
    "import octowright.macros.storage as storage\n"
    "from tests.mutation_kills._artifact_helpers import exercise_non_ascii\n"
    "print(json.dumps(exercise_non_ascii(storage, macro_artifacts, Path(sys.argv[1])), ensure_ascii=True))\n"
)


@pytest.fixture(autouse=True)
def _restore_defaults() -> Any:
    yield
    restore_reloaded_defaults()


@pytest.mark.skipif(sys.platform == "win32", reason="drives the POSIX C locale")
def test_the_store_round_trips_non_ascii_under_an_ascii_locale(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    here = tmp_path / "here"
    storage, macro_artifacts = _reload(monkeypatch, here)
    expected = exercise_non_ascii(storage, macro_artifacts, here)
    assert expected["verified"]["ok"] is True

    child = tmp_path / "child"
    env = {
        **os.environ,
        "LC_ALL": "C",
        "LANG": "C",
        "PYTHONCOERCECLOCALE": "0",
        "PYTHONUTF8": "0",
        "OCTOWRIGHT_RECORDINGS": str(child / "recordings"),
        "OCTOWRIGHT_MACROS_DIR": str(child / "macros"),
        "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
    }
    done = subprocess.run(
        [sys.executable, "-X", "utf8=0", "-c", _CHILD, str(child)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
        timeout=120,
    )

    assert done.returncode == 0, done.stderr[-2000:]
    assert json.loads(done.stdout.strip().splitlines()[-1]) == expected
