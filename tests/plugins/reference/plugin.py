# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path
from typing import Any

from octowright.plugins.contract import PLUGIN_API_VERSION, FrontendAsset
from octowright.plugins.session_launch import PluginContext
from tests.plugins.reference.pool import KIND, ReferencePool, ReferenceSession

#: The reference plugin's dashboard renderer. `renderer.js` is the smallest
#: real consumer of `plugin-contract.d.ts` -- see its module docstring.
_ASSET_DIR = Path(__file__).resolve().parent / "assets"


class ReferenceScenarioAdapter:
    """The mandatory floor and nothing else.

    Deliberately partial: the interesting case for core is a kind that can JOIN
    a scenario but cannot run macros, sync, or take fixtures. A full adapter
    would exercise the same paths the browser adapter already covers, and would
    not prove that the capability narrowing actually narrows.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def resolve_participant(self, spec: Any, persona: Any) -> dict[str, Any]:
        # options pass through opaquely: core validated nothing inside them, and
        # the plugin is the only party that knows what its own settings mean.
        return {"label": spec.persona, "profile": spec.persona, **dict(spec.options)}


class ReferencePlugin:
    kind = KIND
    display_name = "Reference Kind"
    plugin_api_version = PLUGIN_API_VERSION
    tool_names = frozenset({"refkind_launch", "refkind_close"})
    tool_module = "tests.plugins.reference.tools"
    profile_name = "refkinds"
    frontend = FrontendAsset(
        renderer_api_version=1,
        asset_dir=_ASSET_DIR,
        module_path="renderer.js",
        layout="stream",
    )

    def create_pool(self, ctx: PluginContext) -> ReferencePool:
        return ReferencePool(ctx)

    def create_scenario_adapter(self, pool: Any) -> Any:
        return ReferenceScenarioAdapter(pool)

    def session_detail(self, session: ReferenceSession) -> dict[str, Any]:
        return {
            "id": session.instance_id,
            "kind": session.kind,
            "label": session.label,
            "log_path": str(session.log_path),
        }


plugin = ReferencePlugin()
