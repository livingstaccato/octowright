# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Only the daemon's own new-tab page is excluded from the operator's own-site origins."""

from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import urlsplit

from octowright.defaults import new_tab_url
from octowright.macros.substitution import own_site_origins


def _origins(launch_url: str) -> set:
    return own_site_origins(SimpleNamespace(launch_url=launch_url, base_url=None))  # type: ignore[arg-type]


def test_the_daemons_new_tab_page_is_not_an_own_site() -> None:
    own = urlsplit(new_tab_url())

    assert _origins(f"http://localhost:{own.port}{own.path}/") == set()
    assert _origins(f"http://127.0.0.1:{own.port}{own.path}") == set()


def test_the_same_path_on_another_host_or_port_is_an_own_site() -> None:
    own = urlsplit(new_tab_url())

    assert _origins(f"http://app.example:{own.port}{own.path}") == {("http", "app.example", own.port)}
    assert _origins(f"http://127.0.0.1:{own.port + 1}{own.path}") == {("http", "127.0.0.1", own.port + 1)}


def test_a_neighbouring_path_on_the_daemons_port_is_an_own_site() -> None:
    own = urlsplit(new_tab_url())

    assert _origins(f"http://127.0.0.1:{own.port}{own.path}X") == {("http", "127.0.0.1", own.port)}
