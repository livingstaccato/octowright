# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The leaked-driver reaper must still find processes after SDK upgrades."""

from types import MethodType, SimpleNamespace
from typing import Any

import pytest

from tests.conftest import _driver_pid


@pytest.mark.parametrize("modern", [False, True], ids=["1.63", "1.65"])
def test_driver_pid_finds_the_transport_owner(modern: bool) -> None:
    transport = SimpleNamespace(_proc=SimpleNamespace(pid=12345))
    connection = SimpleNamespace()
    pw = SimpleNamespace(_impl_obj=SimpleNamespace(_connection=connection))
    if modern:
        manager = SimpleNamespace(_client=SimpleNamespace(_transport=transport))

        def stop(_self: Any) -> None:
            pass

        pw.stop = MethodType(stop, manager)
    else:
        connection._transport = transport
    assert _driver_pid(pw) == 12345


@pytest.mark.parametrize("pw", [None, SimpleNamespace(), SimpleNamespace(stop=lambda: None)])
def test_driver_pid_without_known_internals_is_unavailable(pw: Any) -> None:
    assert _driver_pid(pw) is None
