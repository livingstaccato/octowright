# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import Any

from octowright.plugins.contract import PLUGIN_API_VERSION
from octowright.plugins.session_launch import PluginContext
from tests.plugins.reference.pool import KIND, ReferencePool, ReferenceSession


class ReferencePlugin:
    kind = KIND
    display_name = "Reference Kind"
    plugin_api_version = PLUGIN_API_VERSION
    tool_names = frozenset({"refkind_launch", "refkind_close"})
    tool_module = "tests.plugins.reference.tools"
    profile_name = "refkinds"
    frontend = None

    def create_pool(self, ctx: PluginContext) -> ReferencePool:
        return ReferencePool(ctx)

    def create_scenario_adapter(self, pool: ReferencePool) -> None:
        # Scenario participation arrives in build step 3. Returning None here
        # is the honest state, not a placeholder: this kind cannot appear in a
        # scenario yet.
        return None

    def session_detail(self, session: ReferenceSession) -> dict[str, Any]:
        return {
            "id": session.instance_id,
            "kind": session.kind,
            "label": session.label,
            "log_path": str(session.log_path),
        }


plugin = ReferencePlugin()
