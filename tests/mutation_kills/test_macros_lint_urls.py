# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A path context spelled with separators still announces a token."""

from __future__ import annotations

import pytest

from octowright.macros.lint_urls import url_carries_credential

TOKEN = "q7Lm2Xv9Rt4Kp8Wz3Nb6Hc1Fd5Gs0Ja"  # pragma: allowlist secret


@pytest.mark.parametrize("context", ["reset-password", "Magic_Link", "verify.email"])
def test_a_separated_credential_context_flags_the_token_after_it(context: str) -> None:
    assert url_carries_credential(f"https://app.example/{context}/{TOKEN}") is True


def test_the_same_token_after_a_resource_name_is_not_flagged() -> None:
    assert url_carries_credential(f"https://app.example/reports/{TOKEN}") is False
