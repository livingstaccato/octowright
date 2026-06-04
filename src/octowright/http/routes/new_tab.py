# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""GET /new-tab — default landing page for browser_launch with no URL.
GET /otto.svg  — Otto the Octowright logo served locally.

Self-contained; no external network requests, no JS frameworks, no session
data. Identifies the browser as octowright-managed and shows a ready state.
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
  <title>Octowright — ready</title>
  <style>
    :root {
      --bg:    #fdf8f1;
      --fg:    #1a1a1a;
      --brand: #be4b1f;
      --muted: #6b6b6b;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg:    #0c0c0e;
        --fg:    #e8e8f0;
        --brand: #e05a24;
        --muted: #9090a0;
      }
    }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    html, body {
      height: 100%;
      background: var(--bg);
      color: var(--fg);
      font-family: "JetBrains Mono", "SFMono-Regular", "Courier New", monospace;
      display: flex;
      align-items: center;
      justify-content: center;
      user-select: none;
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
      color: var(--brand);
      font-weight: 600;
    }
    .status {
      margin-top: 0.5rem;
      font-size: 0.75rem;
      color: var(--muted);
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 0.4em;
      letter-spacing: 0.06em;
      text-transform: lowercase;
    }
    .dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: #4caf50;
      flex-shrink: 0;
      animation: pulse 2.4s ease-in-out infinite;
    }
    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50%       { opacity: 0.3; }
    }
  </style>
</head>
<body>
  <div class="card">
    <img src="/otto.svg" alt="Otto the Octowright" class="otto" width="96" height="96">
    <div class="wordmark">Octo<strong>wright</strong></div>
    <div class="status"><span class="dot"></span>browser ready</div>
  </div>
</body>
</html>
"""


async def new_tab(_: Request) -> HTMLResponse:
    return HTMLResponse(_HTML)


async def otto_svg(_: Request) -> FileResponse:
    return FileResponse(str(_OTTO_SVG), media_type="image/svg+xml")
