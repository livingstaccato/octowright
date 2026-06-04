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
      /* --tint-h / --tint-s set by JS; base lightness per color scheme */
      background: hsl(var(--tint-h, 30), var(--tint-s, 50%), 98%);
      transition: background 90s linear;
      color: #1a1a1a;
      font-family: "JetBrains Mono", "SFMono-Regular", "Courier New", monospace;
      display: flex;
      align-items: center;
      justify-content: center;
      user-select: none;
    }
    @media (prefers-color-scheme: dark) {
      html, body {
        background: hsl(var(--tint-h, 240), var(--tint-s, 10%), 5%);
        color: #e8e8f0;
      }
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
    .wordmark strong {
      color: #be4b1f;
      font-weight: 600;
    }
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
    // Map hour (0-24) to a warm/cool hue and subtle saturation.
    // Dawn/dusk → warm orange-peach; noon → sky; midnight → cool blue.
    function timeTint() {
      var h = new Date().getHours() + new Date().getMinutes() / 60;
      var t = (h / 24) * Math.PI * 2; // 0..2π over 24h
      // Hue: warm(~30) at dawn(6) and dusk(18), cool(~210) at noon, deep(~240) at midnight
      var hue = 120 - 100 * Math.cos(t);               // 20..220, peaks midday cool
      // Saturation: higher at dawn/dusk, lower at noon and midnight
      var sat = 30 + 20 * Math.abs(Math.sin(t));        // 30..50%
      // In dark mode these are dampened further by the low lightness (5%)
      document.documentElement.style.setProperty('--tint-h', Math.round(hue));
      document.documentElement.style.setProperty('--tint-s', Math.round(sat) + '%');
    }
    timeTint();
    setInterval(timeTint, 60000); // recalculate each minute; CSS transition handles smoothing
  </script>
</body>
</html>
"""


async def new_tab(_: Request) -> HTMLResponse:
    return HTMLResponse(_HTML)


async def otto_svg(_: Request) -> FileResponse:
    return FileResponse(str(_OTTO_SVG), media_type="image/svg+xml")
