# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Starlette app factory.

Assembles the API route list (from ``routes/``) plus the SPA frontend mount.
The frontend goes last so its catchall StaticFiles mount at ``/`` doesn't
shadow API routes.
"""

from __future__ import annotations

from typing import Any

from starlette.applications import Starlette

from .frontend import _frontend_routes
from .routes import all_routes


def build_app() -> Starlette:
    """Build the Starlette ASGI app. Stateless — safe to call from tests."""
    routes: list[Any] = list(all_routes())
    routes.extend(_frontend_routes())
    return Starlette(routes=routes)
