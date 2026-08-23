# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The browser kind's scenario adapter.

Core used to inline this behind ``if p.get("kind") == "terminal": return ...``
checks. The problem with that shape was never the check -- it was the code
*after* it, which reached into a browser session (``pool.get(...)``,
``session.page``, ``session.wait_for``). Swapping the check for a capability
flag would have left that body intact, so a plugin declaring ``macros`` would
still have been looked up in the browser pool.

So each kind supplies an adapter instead, and the adapter resolves the instance
id against its own pool. That is what lets core stop knowing which pool a
participant lives in.

Capabilities are not declared here. ``contract.capabilities_of`` derives them by
checking which Protocols this class satisfies, so implementing ``run_macro`` IS
the claim to ``macros``; there is no second place to keep in sync.
"""

from __future__ import annotations

import re
from typing import Any

#: Matches ``_apply_fixtures``'s historical defaults exactly. Changing one is a
#: behaviour change to every existing scenario, not a tidy-up.
_MOCK_ROUTE_DEFAULT_STATUS = 200
_MOCK_ROUTE_DEFAULT_CONTENT_TYPE = "application/json"

#: ``wait_for_sync``'s url branch used this when the caller passed no timeout.
_URL_WAIT_DEFAULT_TIMEOUT_MS = 30000


class BrowserScenarioAdapter:
    """Scenario participation for a browser session.

    Implements all four capability Protocols, which is what makes it the
    reference shape: a plugin adapter is measured against how much of this it
    chooses to provide.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def resolve_participant(self, spec: Any, persona: Any) -> dict[str, Any]:  # noqa: ARG002 - floor param, see docstring
        """Turn a ``Participant`` into launch kwargs for the browser roster.

        Delegates to ``scenarios.resolve_launch_kwargs`` rather than rebuilding
        the mapping. That function already owns the participant-override ->
        persona-default -> fallback resolution order, including the
        ``default_url`` fallback and the ``False`` defaults for
        ``stabilize``/``record_video``/``trace``; a second copy here would be
        verbatim duplication that silently diverges the moment either side
        gains a field.

        ``persona`` is accepted and unused: ``resolve_launch_kwargs`` loads the
        persona itself. The parameter stays because it is part of the
        ``ScenarioAdapter`` floor -- a terminal adapter needs it to read
        ``app.ssh`` defaults -- and a floor method that varies by kind is not a
        floor.
        """
        from octowright.scenarios import resolve_launch_kwargs

        return resolve_launch_kwargs(spec)

    async def run_macro(self, instance_id: str, *, name: str, args: dict[str, Any]) -> None:
        from octowright import macros as _macros

        session = self._pool.get(instance_id)
        await _macros.run_macro(session=session, name=name, args=args)

    async def wait_for_sync(
        self,
        instance_id: str,
        *,
        selector: str | None,
        text: str | None,
        url: str | None,
        timeout_ms: int | None,
    ) -> None:
        session = self._pool.get(instance_id)
        if selector or text:
            await session.wait_for(selector=selector, text=text, timeout_ms=timeout_ms)
        elif url:
            async with session.operation("scenario_wait_for_sync"):
                # Already-there is a pass, not a wait: re.search against the
                # live url first, exactly as the inline version did.
                if not re.search(url, session.page.url):
                    await session.page.wait_for_url(url, timeout=timeout_ms or _URL_WAIT_DEFAULT_TIMEOUT_MS)
        else:
            await session.wait_for(selector=None, text=None, timeout_ms=timeout_ms)

    async def set_dialog_policy(self, instance_id: str, policy: str) -> None:
        session = self._pool.get(instance_id)
        await session.set_dialog_policy(policy)

    async def install_mock_routes(self, instance_id: str, routes: list[dict[str, Any]]) -> None:
        session = self._pool.get(instance_id)
        for mr in routes:
            await session.mock_route(
                mr["pattern"],
                status=mr.get("status", _MOCK_ROUTE_DEFAULT_STATUS),
                body=mr.get("body"),
                content_type=mr.get("content_type", _MOCK_ROUTE_DEFAULT_CONTENT_TYPE),
                headers=mr.get("headers"),
            )


def browser_scenario_adapter(pool: Any) -> BrowserScenarioAdapter:
    """Factory mirroring a plugin's ``create_scenario_adapter(pool)``.

    Core's own kind goes through the same shape as a plugin's so the dispatch
    layer has exactly one way to obtain an adapter.
    """
    return BrowserScenarioAdapter(pool)
