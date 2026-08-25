# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The property step 5's core deletion was for: core references uterm nowhere.

Two checks, of different strength. The string scan is weaker -- it would miss
a dynamically-built import string -- but it is exhaustive over every source
file, including ones never imported by the smoke check below. The sys.modules
check is the stronger property (an actual import boundary, not text), but it
only proves the boundary holds for the modules it happens to import; it is not
a substitute for the string scan, it complements it.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys


def test_core_never_imports_uterm():
    """The whole point of the extraction. A core install has no uterm, so an
    import anywhere under src/octowright is an ImportError for every user who
    did not install the plugin."""
    hits = []
    for path in pathlib.Path("src/octowright").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "provide.uterm" in text or "provide_uterm" in text:
            hits.append(str(path))
    assert hits == [], f"core must not reference uterm: {hits}"


def test_importing_server_with_plugin_disabled_leaves_no_uterm_in_sys_modules():
    """Stronger than the string scan: actually import the MCP server surface
    (the widest import graph a core install reaches -- every @mcp.tool
    submodule) with no plugin enabled, and confirm the process never pulled in
    a single ``provide.uterm*`` module.

    Run in a subprocess rather than in-process: this test suite's own process
    already has ``octowright_terminal`` (and therefore ``provide.uterm``)
    imported by earlier tests / the plugin's own test collection, and
    ``sys.modules`` entries are never un-imported. A fresh interpreter with
    OCTOWRIGHT_PLUGINS unset is the only way to observe a clean import graph.
    """
    script = (
        "import sys\n"
        "import octowright.server\n"
        "hits = sorted(m for m in sys.modules if m == 'provide.uterm' or m.startswith('provide.uterm.'))\n"
        "assert hits == [], f'core import pulled in uterm modules: {hits}'\n"
        "print('OK')\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "OCTOWRIGHT_PLUGINS"}
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert result.stdout.strip() == "OK"
