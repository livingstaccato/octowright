# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations


def test_uterm_connector_factory_importable() -> None:
    from provide.uterm.server.connectors import (
        build_connector,
        register_connector,
        registered_types,
    )

    assert callable(build_connector)
    assert callable(register_connector)
    assert callable(registered_types)


def test_terminal_package_importable() -> None:
    import octowright.terminal  # noqa: F401


def test_terminal_reports_available_when_extra_installed() -> None:
    import octowright.terminal as terminal

    # The dev/test env has the [terminal] extra editable-installed, so True here.
    assert terminal.is_available() is True
