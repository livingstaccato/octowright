# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Generate a tiny unpacked MV3 extension that overrides Chromium's new-tab page.

Chromium's privileged NTP (``chrome://new-tab-page/``) does a renderer-process
swap when navigated, which detaches Playwright's page handle — so a post-open
``page.goto()`` redirect is unreliable. Overriding the NTP via an extension
sidesteps that entirely: the new tab opens directly on our
``chrome-extension://…/newtab.html``, which immediately navigates to ``/new-tab``
(a top-level navigation Chromium permits from an extension page).

Chromium-only: ``--load-extension`` has no Firefox/WebKit equivalent. Old
headless can't load extensions; callers skip this there. Firefox/WebKit new
tabs are handled by the page-event redirector in ``launch_pipeline.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

from octowright.config_paths import user_state_dir

# Stable per-user dir so the extension survives across launches. Overwritten
# (idempotently) on each call to pick up the current bound port.
_EXTENSION_DIR = user_state_dir() / "newtab-extension"

_MANIFEST = {
    "manifest_version": 3,
    "name": "Octowright New Tab",
    "version": "1.0",
    "chrome_url_overrides": {"newtab": "newtab.html"},
}


def ensure_newtab_extension(url: str) -> Path:
    """Write (idempotently) the new-tab override extension; return its dir.

    The redirect ``url`` is baked into ``newtab.js`` at write time, so callers
    pass the current ``get_default_url()`` to pick up the live bound port.
    Concurrent callers write identical bytes, so the unguarded writes are safe.

    The redirect lives in an external ``newtab.js`` (not an inline <script>)
    because MV3's default page CSP (``script-src 'self'``) blocks inline
    scripts — an inline redirect silently no-ops and leaves a blank tab.
    """
    _EXTENSION_DIR.mkdir(parents=True, exist_ok=True)
    (_EXTENSION_DIR / "manifest.json").write_text(json.dumps(_MANIFEST, indent=2), encoding="utf-8")
    html = '<!doctype html><meta charset="utf-8"><title>octowright</title><script src="newtab.js"></script>'
    (_EXTENSION_DIR / "newtab.html").write_text(html, encoding="utf-8")
    # json.dumps the URL so it lands as a safe JS string literal.
    (_EXTENSION_DIR / "newtab.js").write_text(f"location.replace({json.dumps(url)});\n", encoding="utf-8")
    return _EXTENSION_DIR
