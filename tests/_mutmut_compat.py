# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Keep mutmut's trampoline out of the source the exported macro CLI renders.

The export (``artifacts.script_export``) builds its script from
``inspect.getsource`` of live functions, so the script and replay cannot drift.
mutmut 3.x decorates every function it mutates with
``@_mutmut_mutated(<per-function dict>)``, and ``getsource`` returns that line
too: every exported script then opened with a decorator naming a dict it does
not define, and mutmut's clean run failed on the first export test before
scoring anything.

Only that decorator is removed, and only under mutmut: the root
``conftest.py`` calls :func:`install` when it is running as
``mutants/conftest.py``. Any other decorator is kept. The rendered body is the
function's original one, so an exported script runs unmutated code; a mutant
in a rendered function is still exercised by the live path the export tests
compare against.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

ORIGINAL_GETSOURCE = inspect.getsource

_TRAMPOLINE = re.compile(r"^[ \t]*@_mutmut_mutated\([^\n]*\)[ \t]*\n", re.MULTILINE)


def without_mutmut_trampoline(source: str) -> str:
    return _TRAMPOLINE.sub("", source)


def _getsource(obj: Any) -> str:
    return without_mutmut_trampoline(ORIGINAL_GETSOURCE(obj))


def install() -> None:
    inspect.getsource = _getsource
