# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The exported macro CLI renders live source, so under mutmut it must not copy the trampoline.

mutmut 3.x wraps every function it mutates in ``@_mutmut_mutated(<dict>)``,
and ``inspect.getsource`` returns that decorator with the function. The export
(``artifacts.script_export``) renders ``_serialized_variants`` and others that
way, so every exported script under mutmut opened with a decorator naming a
dict it does not define, and mutmut's clean run died on the first export test
(``NameError: name 'mutants_x__serialized_variants__mutmut' is not defined``)
before scoring a single mutant.
"""

from __future__ import annotations

import inspect

from tests import _mutmut_compat

MUTATED = """\
@_mutmut_mutated(mutants_x__serialized_variants__mutmut)
def _serialized_variants(value: str) -> tuple[str, ...]:
    return (value,)
"""

MUTATED_CLASS = """\
@_mutmut_mutated(mutants_x_Ledger__mutmut)
@dataclass
class Ledger:
    pending: int = 0
"""


def test_the_mutmut_trampoline_decorator_is_removed() -> None:
    assert _mutmut_compat.without_mutmut_trampoline(MUTATED) == (
        "def _serialized_variants(value: str) -> tuple[str, ...]:\n    return (value,)\n"
    )


def test_a_real_decorator_is_kept() -> None:
    assert _mutmut_compat.without_mutmut_trampoline(MUTATED_CLASS) == (
        "@dataclass\nclass Ledger:\n    pending: int = 0\n"
    )


def test_an_indented_trampoline_on_a_method_is_removed() -> None:
    source = "    @_mutmut_mutated(mutants_xǁLedgerǁadd__mutmut)\n    def add(self) -> None:\n        pass\n"
    assert _mutmut_compat.without_mutmut_trampoline(source) == "    def add(self) -> None:\n        pass\n"


def test_source_without_a_trampoline_is_unchanged() -> None:
    source = "@functools.cache\ndef f() -> int:\n    return 1\n"
    assert _mutmut_compat.without_mutmut_trampoline(source) == source


def test_getsource_is_left_alone_outside_mutmut() -> None:
    assert inspect.getsource is _mutmut_compat.ORIGINAL_GETSOURCE
