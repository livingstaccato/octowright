# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Load a `scripts/` or `ci/` file as a module, for the tests that check them.

Those files are not importable packages -- they are standalone executables with
no `__init__.py` above them -- so a test that wants to call one function out of
a gate script has to go through `importlib.util.spec_from_file_location`. Eight
test modules were each carrying their own copy of that six-line dance, and the
copies had already drifted in a way that matters: three registered the module in
`sys.modules` before executing it and five did not.

Registering is the correct half, which is why it is what this does. Between
`module_from_spec` and `exec_module` the module does not yet exist under its own
name, so anything the loaded file does that looks itself up by name fails or
silently sees nothing -- `typing.get_type_hints` on a module using
`from __future__ import annotations`, a dataclass resolving a forward reference,
`pickle`, and a module importing itself all land there. None of the five bit
anyone yet; the point is that whether they would is currently decided by which
copy a test happened to be written next to.

The name is prefixed with an underscore so a loaded script can never collide
with a real importable module of the same name -- the convention the two
registering copies already used.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_script_module(relative_path: str, *, name: str | None = None) -> ModuleType:
    """Execute the repo file at *relative_path* and return it as a module.

    `relative_path` is relative to the repository root (e.g.
    `"scripts/check_ty.py"`). `name` overrides the derived module name, for a
    caller that wants a specific one; it is otherwise the file's stem with an
    underscore prefix.
    """
    path = REPO_ROOT / relative_path
    module_name = name or f"_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path} as a module")
    module = importlib.util.module_from_spec(spec)
    # Before exec_module, so the file can find itself under its own name.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
