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
import sys
from typing import Any

ORIGINAL_GETSOURCE = inspect.getsource

_TRAMPOLINE = re.compile(r"^[ \t]*@_mutmut_mutated\([^\n]*\)[ \t]*\n", re.MULTILINE)


def without_mutmut_trampoline(source: str) -> str:
    return _TRAMPOLINE.sub("", source)


def _getsource(obj: Any) -> str:
    return without_mutmut_trampoline(ORIGINAL_GETSOURCE(obj))


def install() -> None:
    inspect.getsource = _getsource


def clear_function_caches() -> None:
    """Empty every ``functools`` cache on an ``octowright`` module's functions.

    mutmut forks each mutant from the process that ran the clean pass, so a
    cache filled there answers a mutant with the unmutated result and the
    mutant is scored as a survivor its tests would kill. Measured: the
    scrubber's ``_scrub_patterns`` hid word-boundary mutants in
    ``scrub_engine._identifier_bounded`` that fail its tests when applied.
    """
    for name, module in list(sys.modules.items()):
        if module is None or not (name == "octowright" or name.startswith("octowright.")):
            continue
        for value in list(vars(module).values()):
            clear = getattr(value, "cache_clear", None)
            if callable(clear):
                clear()
