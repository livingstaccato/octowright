# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Generate a tiny unpacked MV3 extension that redirects Chromium new tabs.

A background service worker watches for new tabs landing on Chromium's NTP
(``chrome://newtab/`` / ``chrome://new-tab-page/``) and navigates them to the
daemon's ``/new-tab`` via ``chrome.tabs.update``.

Why a service worker and not ``chrome_url_overrides.newtab``: the override key
triggers Chrome's "An extension changed your new tab page — Keep it / Change it
back" protection dialog, which the user must dismiss per profile. The ``tabs``
API path carries no such prompt. The cost is a brief flash of the default NTP
before the redirect — acceptable in exchange for zero prompts.

Why a service worker and not a Playwright ``page.goto`` redirect: Chromium's
privileged NTP does a renderer-process swap on navigation that detaches
Playwright's page handle, so a post-open ``page.goto`` is unreliable.
``chrome.tabs.update`` runs at the browser level and is not affected.

Chromium-only: ``--load-extension`` has no Firefox/WebKit equivalent. Old
headless can't load extensions; callers skip this there. Firefox/WebKit new
tabs are handled by the page-event redirector in ``launch_pipeline.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

from octowright.config_paths import user_state_dir

# Stable per-user dir so the unpacked extension ID (derived from the path) stays
# constant across launches. Overwritten idempotently to pick up the bound port.
_EXTENSION_DIR = user_state_dir() / "newtab-extension"

_MANIFEST = {
    "manifest_version": 3,
    "name": "Octowright New Tab",
    "version": "1.0",
    "permissions": ["tabs"],
    "host_permissions": ["http://127.0.0.1/*", "http://localhost/*"],
    "background": {"service_worker": "sw.js"},
}

_SW_TEMPLATE = """\
const TARGET = __TARGET__;
function isNtp(u) {
  return (
    u === "chrome://newtab/" ||
    u.startsWith("chrome://new-tab-page") ||
    u.startsWith("chrome-search://local-ntp")
  );
}
function maybeRedirect(tabId, tab) {
  const u = (tab && (tab.pendingUrl || tab.url)) || "";
  if (isNtp(u)) chrome.tabs.update(tabId, { url: TARGET });
}
chrome.tabs.onCreated.addListener((tab) => maybeRedirect(tab.id, tab));
chrome.tabs.onUpdated.addListener((tabId, info, tab) => maybeRedirect(tabId, tab));
"""


def ensure_newtab_extension(url: str) -> Path:
    """Write (idempotently) the new-tab redirect extension; return its dir.

    The redirect ``url`` is baked into ``sw.js`` at write time, so callers pass
    the current ``get_default_url()`` to pick up the live bound port. Concurrent
    callers write identical bytes, so the unguarded writes are safe.
    """
    _EXTENSION_DIR.mkdir(parents=True, exist_ok=True)
    (_EXTENSION_DIR / "manifest.json").write_text(json.dumps(_MANIFEST, indent=2), encoding="utf-8")
    # json.dumps the URL so it lands as a safe JS string literal.
    sw = _SW_TEMPLATE.replace("__TARGET__", json.dumps(url))
    (_EXTENSION_DIR / "sw.js").write_text(sw, encoding="utf-8")
    return _EXTENSION_DIR
