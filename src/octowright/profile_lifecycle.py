# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Process-local exclusion for persistent browser-profile lifecycle changes."""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field


@dataclass
class _LockEntry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


_registry_guard = threading.Lock()
_registry: dict[tuple[str, str], _LockEntry] = {}


@contextlib.asynccontextmanager
async def profile_lifecycle_lock(kind: str, name: str | None) -> AsyncIterator[None]:
    """Serialize launch/delete transitions for one engine profile.

    ``None`` is a deliberate no-op so callers can wrap ephemeral and persistent
    launches with one code path. Registry entries count holders and waiters and
    disappear once the last user leaves, preventing unbounded name retention.
    """
    if name is None:
        yield
        return

    # Profile paths use the persona slug, so aliases such as ``cosmo one`` and
    # ``cosmo-one`` must share a lock or they can still race on one directory.
    from octowright.personas import _slug

    key = (kind, _slug(name))
    with _registry_guard:
        entry = _registry.setdefault(key, _LockEntry())
        entry.users += 1

    acquired = False
    try:
        await entry.lock.acquire()
        acquired = True
        yield
    finally:
        if acquired:
            entry.lock.release()
        with _registry_guard:
            entry.users -= 1
            if entry.users == 0 and _registry.get(key) is entry:
                del _registry[key]


@contextlib.asynccontextmanager
async def profile_lifecycle_locks(keys: Iterable[tuple[str, str]]) -> AsyncIterator[None]:
    """Acquire a de-duplicated set of profile locks in stable order."""
    ordered = sorted(set(keys))
    async with contextlib.AsyncExitStack() as stack:
        for kind, name in ordered:
            await stack.enter_async_context(profile_lifecycle_lock(kind, name))
        yield
