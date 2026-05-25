# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import pytest

from octowright import personas
from octowright.session.core_page_mixin import SessionPageMixin


class _FailingLocator:
    @property
    def first(self) -> _FailingLocator:
        return self

    async def evaluate(self, _script: str) -> str:
        raise RuntimeError("element detached")


class _FailingTarget:
    def locator(self, _selector: str) -> _FailingLocator:
        return _FailingLocator()


class _SessionWithFailingLookup(SessionPageMixin):
    def _target(self) -> _FailingTarget:
        return _FailingTarget()


@pytest.mark.anyio
async def test_password_lookup_failure_fails_closed() -> None:
    assert await _SessionWithFailingLookup()._is_password_input("#password") is True


def test_load_persona_rejects_invalid_yaml_shape(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path)
    persona_dir = tmp_path / "bad"
    persona_dir.mkdir()
    (persona_dir / "profile.yaml").write_text("- not-a-mapping\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid persona file"):
        personas.load_persona("bad")
