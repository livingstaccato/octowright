# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""GET /new-tab — default landing page for browser_launch with no URL.

Self-contained; no external network requests, no JS frameworks, no session
data. Identifies the browser as octowright-managed and shows a ready state.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import HTMLResponse

_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Octowright — ready</title>
  <style>
    :root {
      --bg: #0c0c0e;
      --fg: #e8e8f0;
      --fg-2: #9090a0;
      --accent: #e87028;
    }
    @media (prefers-color-scheme: light) {
      :root {
        --bg: #f4f4f6;
        --fg: #18181e;
        --fg-2: #48485a;
        --accent: #e87028;
      }
    }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    html, body {
      height: 100%;
      background: var(--bg);
      color: var(--fg);
      font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
      display: flex;
      align-items: center;
      justify-content: center;
      user-select: none;
    }
    .card { text-align: center; }
    .otto { font-size: 3rem; line-height: 1; margin-bottom: 1rem; }
    .name {
      font-size: 1.125rem;
      font-weight: 600;
      letter-spacing: 0.03em;
    }
    .name em { color: var(--accent); font-style: normal; }
    .status {
      margin-top: 0.5rem;
      font-size: 0.8125rem;
      color: var(--fg-2);
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 0.4em;
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
      50%       { opacity: 0.35; }
    }
  </style>
</head>
<body>
  <div class="card">
    <div class="otto">&#x1F419;</div>
    <div class="name">Otto the <em>Octowright</em></div>
    <div class="status"><span class="dot"></span>browser ready</div>
  </div>
</body>
</html>
"""


async def new_tab(_: Request) -> HTMLResponse:
    return HTMLResponse(_HTML)
