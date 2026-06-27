# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""GET /new-tab — default landing page for browser_launch with no URL.
GET /otto.svg  — Otto the Octowright logo served locally.

Self-contained; no external network requests, no JS frameworks. Version and
commit hash are baked server-side at request time; uptime and browser count
are refreshed client-side every 10 s via fetch(/api/sessions).
"""

from __future__ import annotations

import importlib.metadata
import subprocess
from functools import lru_cache
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse

_OTTO_SVG = Path(__file__).resolve().parent.parent / "otto.svg"


@lru_cache(maxsize=1)
def _version() -> str:
    try:
        return importlib.metadata.version("octowright")
    except Exception:
        return "?"


@lru_cache(maxsize=1)
def _commit() -> str:
    try:
        r = subprocess.run(  # nosec B603 B607 - fixed git argv, no user input
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return r.stdout.strip() or "?"
    except Exception:
        return "?"


def _started_at() -> float:
    try:
        from octowright.singleton import read_lock

        info = read_lock()
        return info.started_at if info else 0.0
    except Exception:
        return 0.0


_HTML_TMPL = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>octowright</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    html, body {{
      height: 100%;
      background: #f5f2ee;
      transition: background 90s linear;
      color: #1a1a1a;
      font-family: "JetBrains Mono", "SFMono-Regular", "Courier New", monospace;
      display: flex;
      align-items: center;
      justify-content: center;
      user-select: none;
    }}
    @media (prefers-color-scheme: dark) {{
      html, body {{ background: #08100d; color: #e8e8f0; }}
    }}
    .card {{ text-align: center; }}
    .otto {{
      width: 192px; height: 192px;
      margin: 0 auto 1.25rem;
      display: block;
    }}
    .wordmark {{
      font-size: 1.25rem;
      font-weight: 600;
      letter-spacing: 0.04em;
    }}
    .wordmark strong {{ color: #be4b1f; font-weight: 600; }}
    @media (prefers-color-scheme: dark) {{
      .wordmark strong {{ color: #e05a24; }}
    }}
    .meta {{
      margin-top: 1.4rem;
      font-size: 0.7rem;
      letter-spacing: 0.06em;
      opacity: 0.45;
      line-height: 1.9;
    }}
    .meta a {{
      color: inherit;
      text-decoration: none;
      border-bottom: 1px dotted currentColor;
    }}
    .meta a:hover {{ opacity: 0.8; }}
    .dot {{ margin: 0 0.35em; opacity: 0.5; }}
    .browsers {{ display: inline-block; }}
    .browsers::before {{ content: '\\25cf\\0020'; font-size: 0.55rem; vertical-align: middle; }}
  </style>
</head>
<body>
  <div class="card">
    <img src="/otto.svg" alt="Otto" class="otto" width="192" height="192">
    <div class="wordmark">octo<strong>wright</strong></div>
    <div class="meta">
      <div>
        v{version}<span class="dot">·</span>{commit}<span class="dot">·</span><span id="uptime">--</span>
      </div>
      <div>
        <span class="browsers"><span id="browser-count">?</span> browser<span id="browser-s">s</span></span>
        <span class="dot">·</span>
        <a href="/" id="dash-link">dashboard</a>
      </div>
    </div>
  </div>
  <script>
    var STARTED_AT = {started_at};

    // --- time tint ---
    function timeTint() {{
      var h = new Date().getHours() + new Date().getMinutes() / 60;
      var t = (h / 24) * Math.PI * 2;
      var hue = Math.round(120 - 100 * Math.cos(t));
      var sat = Math.round(35 + 35 * Math.abs(Math.sin(t)));
      var dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      var lit = dark
        ? Math.round(6 + 8 * Math.abs(Math.sin(t / 2 + 0.5)))
        : Math.round(84 + 7 * (Math.cos(t) * 0.5 + 0.5));
      var bg = 'hsl(' + hue + ',' + sat + '%,' + lit + '%)';
      document.documentElement.style.background = bg;
      document.body.style.background = bg;
    }}
    document.documentElement.style.transition = 'none';
    document.body.style.transition = 'none';
    timeTint();
    setTimeout(function() {{
      document.documentElement.style.transition = '';
      document.body.style.transition = '';
      setInterval(timeTint, 60000);
    }}, 50);

    // --- uptime counter ---
    function fmtUptime(secs) {{
      secs = Math.max(0, Math.round(secs));
      var d = Math.floor(secs / 86400);
      var h = Math.floor((secs % 86400) / 3600);
      var m = Math.floor((secs % 3600) / 60);
      var s = secs % 60;
      if (d > 0) return d + 'd ' + h + 'h';
      if (h > 0) return h + 'h ' + (m < 10 ? '0' : '') + m + 'm';
      if (m > 0) return m + 'm ' + (s < 10 ? '0' : '') + s + 's';
      return s + 's';
    }}
    function tickUptime() {{
      if (!STARTED_AT) return;
      document.getElementById('uptime').textContent = 'up ' + fmtUptime(Date.now() / 1000 - STARTED_AT);
    }}
    tickUptime();
    setInterval(tickUptime, 1000);

    // --- browser count ---
    function refreshBrowsers() {{
      fetch('/api/sessions')
        .then(function(r) {{ return r.json(); }})
        .then(function(d) {{
          var n = (d.live || []).length;
          document.getElementById('browser-count').textContent = n;
          document.getElementById('browser-s').textContent = n === 1 ? '' : 's';
        }})
        .catch(function() {{}});
    }}
    // Delay first fetch so the session is registered before we count it.
    setTimeout(function() {{
      refreshBrowsers();
      setInterval(refreshBrowsers, 3000);
    }}, 2000);
  </script>
</body>
</html>
"""


async def new_tab(_: Request) -> HTMLResponse:
    html = _HTML_TMPL.format(
        version=_version(),
        commit=_commit(),
        started_at=_started_at(),
    )
    return HTMLResponse(html)


async def otto_svg(_: Request) -> FileResponse:
    return FileResponse(str(_OTTO_SVG), media_type="image/svg+xml")
