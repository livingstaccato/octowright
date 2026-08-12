# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio

import pytest

from octowright.profile_lifecycle import profile_lifecycle_lock, profile_lifecycle_locks


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_same_profile_operations_serialize() -> None:
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()

    async def first() -> None:
        async with profile_lifecycle_lock("chromium", "cosmo"):
            first_entered.set()
            await release_first.wait()

    async def second() -> None:
        await first_entered.wait()
        async with profile_lifecycle_lock("chromium", "cosmo"):
            second_entered.set()

    first_task = asyncio.create_task(first())
    second_task = asyncio.create_task(second())
    await first_entered.wait()
    await asyncio.sleep(0)
    assert not second_entered.is_set()

    release_first.set()
    await asyncio.gather(first_task, second_task)
    assert second_entered.is_set()


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_names_that_resolve_to_same_profile_directory_serialize() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    alias_entered = asyncio.Event()

    async def hold_original() -> None:
        async with profile_lifecycle_lock("chromium", "cosmo one"):
            entered.set()
            await release.wait()

    async def take_alias() -> None:
        await entered.wait()
        async with profile_lifecycle_lock("chromium", "cosmo-one"):
            alias_entered.set()

    holder = asyncio.create_task(hold_original())
    waiter = asyncio.create_task(take_alias())
    await entered.wait()
    await asyncio.sleep(0)
    assert not alias_entered.is_set()
    release.set()
    await asyncio.gather(holder, waiter)


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_different_profile_operations_can_overlap() -> None:
    first_entered = asyncio.Event()
    second_entered = asyncio.Event()

    async def hold_first() -> None:
        async with profile_lifecycle_lock("chromium", "cosmo"):
            first_entered.set()
            await second_entered.wait()

    async def enter_second() -> None:
        await first_entered.wait()
        async with profile_lifecycle_lock("firefox", "cosmo"):
            second_entered.set()

    await asyncio.wait_for(asyncio.gather(hold_first(), enter_second()), timeout=1.0)


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_cancelled_waiter_does_not_retain_profile_lock() -> None:
    async with profile_lifecycle_lock("webkit", "dante"):
        waiter = asyncio.create_task(_take_profile_lock("webkit", "dante"))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

    await asyncio.wait_for(_take_profile_lock("webkit", "dante"), timeout=1.0)


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_multiple_profile_locks_use_stable_order() -> None:
    acquired = asyncio.Event()
    release = asyncio.Event()

    async def hold_chromium() -> None:
        async with profile_lifecycle_lock("chromium", "ziggy"):
            acquired.set()
            await release.wait()

    holder = asyncio.create_task(hold_chromium())
    await acquired.wait()
    all_acquired = asyncio.Event()

    async def take_all() -> None:
        async with profile_lifecycle_locks((("webkit", "ziggy"), ("chromium", "ziggy"), ("firefox", "ziggy"))):
            all_acquired.set()

    waiter = asyncio.create_task(take_all())
    await asyncio.sleep(0)
    assert not all_acquired.is_set()
    release.set()
    await asyncio.gather(holder, waiter)
    assert all_acquired.is_set()


async def _take_profile_lock(kind: str, name: str) -> None:
    async with profile_lifecycle_lock(kind, name):
        return
