# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A value is scrubbed in its Python-repr spelling too (#248, found by running an export).

An error message built with ``{actual!r}``, and every failure payload's
``original`` (``repr(exc)``), escape a string the way Python's repr does: a
``'`` becomes ``\\'`` when the text also holds a ``"``, and a control
character becomes ``\\xNN``. Neither is a JSON spelling, so a password holding
both quotes, or a control character, reached the exported script's
``result.json`` -- and a live failure payload -- in the clear.
"""

from __future__ import annotations

import pytest

from octowright.macros.privacy import scrub_sensitive_values, sensitive_value_variants

VALUES = [
    "Pw\"q'x-7Kd",  # pragma: allowlist secret
    "Pw'only-quote",  # pragma: allowlist secret
    "ctl\x01char-pw",  # pragma: allowlist secret
    "back\\slash'\"pw",  # pragma: allowlist secret
]


@pytest.mark.parametrize("value", VALUES)
def test_a_repr_quoted_value_is_scrubbed(value: str) -> None:
    for message in (f"got {value!r}", f'got "{value}"', f'got {value!r} and {"x"!r} "q"'):
        text = repr(RuntimeError(message))
        assert scrub_sensitive_values(text, (value,)).count("<redacted>") >= 1, text
        assert value not in scrub_sensitive_values(text, (value,))
        for spelling in (repr(value + "'\"")[1:-4], repr(value + "'\"")[1:-4].replace("\\'", "'")):
            assert spelling not in scrub_sensitive_values(text, (value,))


def test_a_plain_value_gains_no_variants() -> None:
    """A value with no quote, backslash or control character spells the same in repr."""
    plain = "Plain-Value-42"  # pragma: allowlist secret
    assert all("\\" not in variant for variant in sensitive_value_variants([plain]))
