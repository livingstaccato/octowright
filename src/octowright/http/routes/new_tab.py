# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""GET /new-tab — default landing page for browser_launch with no URL.
GET /otto.svg  — Otto the Octowright logo served locally.

Self-contained; no external network requests, no JS frameworks, no session
data. Background tint shifts slowly with the time of day.
"""

from __future__ import annotations

from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse

_OTTO_SVG = Path(__file__).resolve().parent.parent / "otto.svg"

_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>octowright</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    html, body {
      height: 100%;
      background: #f5f2ee; /* overwritten immediately by JS */
      transition: background 90s linear;
      color: #1a1a1a;
      font-family: "JetBrains Mono", "SFMono-Regular", "Courier New", monospace;
      display: flex;
      align-items: center;
      justify-content: center;
      user-select: none;
    }
    @media (prefers-color-scheme: dark) {
      html, body { background: #08100d; color: #e8e8f0; }
    }
    .card { text-align: center; }
    .otto {
      width: 96px;
      height: 96px;
      margin: 0 auto 1.25rem;
      display: block;
    }
    .wordmark {
      font-size: 1.25rem;
      font-weight: 600;
      letter-spacing: 0.04em;
    }
    .wordmark strong { color: #be4b1f; font-weight: 600; }
    @media (prefers-color-scheme: dark) {
      .wordmark strong { color: #e05a24; }
    }
  </style>
</head>
<body>
  <div class="card">
    <img src="/otto.svg" alt="Otto" class="otto" width="96" height="96">
    <div class="wordmark">octo<strong>wright</strong></div>
  </div>
  <script>
    function timeTint() {
      var h = new Date().getHours() + new Date().getMinutes() / 60;
      var t = (h / 24) * Math.PI * 2;
      // midnight→warm orange-red, noon→sky blue, dawn/dusk→golden
      var hue = Math.round(120 - 100 * Math.cos(t));          // 20..220
      var sat = Math.round(35 + 35 * Math.abs(Math.sin(t)));   // 35..70%
      var dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      // Light: 84-91% swing - clearly noticeable pastels without washing out
      // Dark:  6-14% swing - visible color shift on near-black
      var lit = dark
        ? Math.round(6 + 8 * Math.abs(Math.sin(t / 2 + 0.5)))
        : Math.round(84 + 7 * (Math.cos(t) * 0.5 + 0.5));
      var bg = 'hsl(' + hue + ',' + sat + '%,' + lit + '%)';
      document.documentElement.style.background = bg;
      document.body.style.background = bg;
    }
    // Paint instantly on load, then re-enable slow transition for minute-ticks.
    document.documentElement.style.transition = 'none';
    document.body.style.transition = 'none';
    timeTint();
    setTimeout(function() {
      document.documentElement.style.transition = '';
      document.body.style.transition = '';
      setInterval(timeTint, 60000);
    }, 50);
  </script>
</body>
</html>
"""


async def new_tab(_: Request) -> HTMLResponse:
    return HTMLResponse(_HTML)


async def otto_svg(_: Request) -> FileResponse:
    return FileResponse(str(_OTTO_SVG), media_type="image/svg+xml")
